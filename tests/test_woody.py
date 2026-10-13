import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from woody import analysis
from woody.cli import main
from woody.hydra import HydraBrain
from woody.records import make_record, to_document
from woody.testing import FakeHydraDB

ROOT = Path(__file__).resolve().parent.parent
WOODY = json.loads((ROOT / "examples" / "woody_exp1.json").read_text())
PRICING = json.loads((ROOT / "examples" / "pricing_test.json").read_text())


def bookings(n, exp="woody-exp1"):
    out = []
    for i in range(n):
        band = "7-10" if i % 2 == 0 else "11+"
        channel = "nextdoor" if i % 3 else "facebook"
        out.append(make_record("booking", exp, {"band": band, "paid": True, "channel": channel, "price": 75 if band == "7-10" else 50}))
    return out


def surveys(n, worth=5, rebook=True, exp="woody-exp1"):
    return [make_record("survey", exp, {"worth_price": worth, "rebook": rebook}) for _ in range(n)]


class RecordTests(unittest.TestCase):
    def test_document_spells_out_links(self):
        rec = make_record("booking", "e1", {"band": "7-10"}, [{"rel": "for_session", "to": "SES-1"}], record_id="BKG-1")
        doc = to_document(rec)
        self.assertIn("Booking BKG-1 is for session SES-1.", doc)
        self.assertIn("- band: 7-10", doc)

    def test_unknown_link_rejected(self):
        with self.assertRaises(ValueError):
            make_record("booking", "e1", {}, [{"rel": "loves", "to": "x"}])


class AnalysisTests(unittest.TestCase):
    today = date(2026, 11, 10)

    def outcome(self, n_book, **survey_kw):
        recs = bookings(n_book) + surveys(6, **survey_kw)
        results = analysis.evaluate(WOODY, recs, self.today)
        return analysis.decide(WOODY, results, self.today)["outcome"], results

    def test_go(self):
        self.assertEqual(self.outcome(11)[0], "go")

    def test_stop(self):
        self.assertEqual(self.outcome(3)[0], "stop")

    def test_pivot_on_middle_seats(self):
        self.assertEqual(self.outcome(7)[0], "pivot")

    def test_pivot_when_value_rating_misses(self):
        self.assertEqual(self.outcome(12, worth=2)[0], "pivot")

    def test_group_breakdowns_and_evidence_ids(self):
        _, results = self.outcome(10)
        h2 = next(r for r in results if r["id"] == "H2")
        self.assertEqual(set(h2["groups"]), {"7-10", "11+"})
        h1 = next(r for r in results if r["id"] == "H1")
        self.assertEqual(h1["value"], 10)
        self.assertEqual(len(h1["evidence"]), 10)

    def test_thin_evidence_and_deadline_flags(self):
        recs = bookings(4) + surveys(2)
        results = analysis.evaluate(WOODY, recs, self.today)
        h3 = next(r for r in results if r["id"] == "H3")
        self.assertTrue(any("thin evidence" in f for f in h3["flags"]))
        h1 = next(r for r in results if r["id"] == "H1")
        self.assertTrue(any("deadline" in f for f in h1["flags"]))

    def test_no_data_is_never_a_pass(self):
        results = analysis.evaluate(WOODY, [], self.today)
        by_id = {r["id"]: r for r in results}
        self.assertEqual(by_id["H1"]["status"], "miss")  # zero seats is a real result
        self.assertEqual(by_id["H3"]["status"], "no_data")  # no surveys is missing evidence
        self.assertEqual(by_id["H2"]["status"], "no_data")

    def test_day_one_reading_is_provisional(self):
        day_one = date(2026, 10, 12)
        results = analysis.evaluate(WOODY, [], day_one)
        decision = analysis.decide(WOODY, results, day_one)
        self.assertFalse(decision["final"])
        review = analysis.weekly_review(WOODY, [], day_one)
        self.assertIn("Provisional reading", review)
        report, d, _ = analysis.conclusion_report(WOODY, [], day_one)
        self.assertIn("Provisional only", report)

    def test_final_after_decide_date(self):
        results = analysis.evaluate(WOODY, [], self.today)
        self.assertTrue(analysis.decide(WOODY, results, self.today)["final"])
        self.assertEqual(analysis.decide(WOODY, results, self.today)["outcome"], "stop")

    def test_same_engine_runs_a_different_experiment(self):
        recs = bookings(22, exp="pricing-test") + [
            make_record("survey", "pricing-test", {"recommend_score": 9}) for _ in range(6)
        ]
        day = date(2026, 12, 2)
        results = analysis.evaluate(PRICING, recs, day)
        decision = analysis.decide(PRICING, results, day)
        self.assertEqual(decision["outcome"], "go")
        self.assertTrue(decision["final"])

    def test_report_lists_evidence(self):
        recs = bookings(10) + surveys(6)
        report, decision, evidence = analysis.conclusion_report(WOODY, recs, self.today)
        self.assertIn("Decision: GO", report)
        self.assertEqual(decision["outcome"], "go")
        self.assertTrue(all(i in report for i in evidence[:5]))


class HydraBrainTests(unittest.TestCase):
    def make(self):
        client = FakeHydraDB()
        return client, HydraBrain(client, "woody", "woody-exp1")

    def test_database_wait_polls_until_ready(self):
        client, brain = self.make()
        brain.ensure_database(timeout=5, poll=0)
        self.assertEqual(client.databases.created, ["woody"])
        self.assertGreaterEqual(client.databases.polls, 2)

    def test_knowledge_and_memory_routing(self):
        client, brain = self.make()
        brain.remember(make_record("booking", "woody-exp1", {"band": "7-10"}, record_id="BKG-1"), wait=True)
        brain.remember(make_record("decision", "woody-exp1", {"text": "GO on seats"}, record_id="DEC-1"))
        kinds = [c["type"] for c in client.context.calls]
        self.assertEqual(kinds, ["knowledge", "memory"])
        self.assertEqual(client.context.calls[0]["collection"], "woody-exp1_records")
        self.assertEqual(client.context.calls[1]["collection"], "woody-exp1_memory")
        self.assertEqual(json.loads(client.context.calls[0]["document_metadata"]), [{"id": "BKG-1"}])

    def test_ask_returns_connected_passages(self):
        client, brain = self.make()
        brain.remember(make_record("booking", "woody-exp1", {"channel": "nextdoor"}, record_id="BKG-9"))
        brain.remember(make_record("decision", "woody-exp1", {"text": "Pivot on nextdoor price"}, record_id="DEC-2"))
        text = brain.ask("what happened on nextdoor")
        self.assertIn("BKG-9", text)
        self.assertIn("DEC-2", text)

    def test_metadata_is_opt_in(self):
        client = FakeHydraDB()
        brain = HydraBrain(client, "woody", "woody-exp1", send_metadata=True)
        brain.remember(make_record("booking", "woody-exp1", {}, record_id="BKG-3"))
        meta = json.loads(client.context.calls[0]["document_metadata"])[0]
        self.assertEqual(meta["additional_metadata"]["kind"], "booking")


class CliEndToEnd(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["--offline", *argv])
        return buf.getvalue()

    def test_full_loop_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["WOODY_HOME"] = tmp
            try:
                self.run_cli("init", "--charter", str(ROOT / "examples" / "woody_exp1.json"))
                # a second init must not duplicate records
                out = self.run_cli("init", "--charter", str(ROOT / "examples" / "woody_exp1.json"))
                self.assertIn("already set up", out)

                csvp = Path(tmp) / "bookings.csv"
                lines = ["family_code,band,price,paid,channel,session"]
                for i in range(11):
                    lines.append(f"F{i:02d},{'7-10' if i % 2 == 0 else '11+'},{75 if i % 2 == 0 else 50},true,nextdoor,S1")
                csvp.write_text("\n".join(lines))
                self.assertIn("Imported 11 booking", self.run_cli("import-bookings", str(csvp)))

                svp = Path(tmp) / "surveys.csv"
                svp.write_text("worth_price,rebook,series_interest\n" + "\n".join("5,yes,yes" for _ in range(6)))
                self.assertIn("Imported 6 survey", self.run_cli("import-surveys", str(svp)))

                self.run_cli("note", "Kids loved the pulley toy but hammering took long", "--session", "S1", "--about", "H3")
                self.run_cli("photo", "IMG_1.jpg", "--caption", "birdhouse", "--consent", "B", "--session", "S1")
                with self.assertRaises(SystemExit):
                    self.run_cli("photo", "IMG_2.jpg", "--consent", "X")

                review = self.run_cli("review", "--today", "2026-11-10")
                self.assertIn("H1 Demand: 11", review)
                self.assertIn("Current reading: GO", review)

                # before the final-call date the decision cannot be saved without --force
                with self.assertRaises(SystemExit):
                    self.run_cli("conclude", "--today", "2026-11-05", "--save")

                concl = self.run_cli("conclude", "--today", "2026-11-10", "--save")
                self.assertIn("Decision: GO", concl)
                self.assertTrue((Path(tmp) / "conclusion.md").exists())

                answer = self.run_cli("ask", "pulley toy hammering")
                self.assertIn("pulley", answer)
                self.assertIn("Saved decision", concl)

                status = self.run_cli("status")
                self.assertIn("11 booking", status)
                self.assertIn("1 decision", status)
            finally:
                os.environ.pop("WOODY_HOME", None)


class CoreReliability(unittest.TestCase):
    def test_both_example_charters_validate(self):
        from woody.core import validate_charter
        validate_charter(WOODY)
        validate_charter(PRICING)

    def test_validation_lists_every_problem(self):
        from woody.core import validate_charter
        bad = json.loads(json.dumps(WOODY))
        bad["experiment_id"] = "Bad Id"
        bad["hypotheses"][0]["op"] = "~"
        bad["hypotheses"][1]["op"] = ">="
        bad["hypotheses"][1]["value"] = 3
        bad["decide_after"] = "soon"
        with self.assertRaises(ValueError) as ctx:
            validate_charter(bad)
        msg = str(ctx.exception)
        for part in ("experiment_id", "H1: pass line", "report-only", "decide_after"):
            self.assertIn(part, msg)

    def test_record_survives_a_hydradb_outage_and_syncs_later(self):
        from woody.core import Woody

        class Flaky:
            def __init__(self):
                self.up, self.got = False, []

            def ensure_database(self, **_):
                return None

            def remember(self, rec, wait=False):
                if not self.up:
                    raise ConnectionError("HydraDB unreachable")
                self.got.append(rec["id"])

        with tempfile.TemporaryDirectory() as tmp:
            w = Woody(offline=True, root=Path(tmp))
            w.ledger.save_charter(WOODY)
            flaky = Flaky()
            w._brain = flaky
            rec = w.add("booking", {"paid": True, "band": "7-10"})
            self.assertEqual(len(w.ledger.all()), 1)  # kept locally
            self.assertIn("not yet in HydraDB", w.warnings[0])
            self.assertIn("1 not yet in HydraDB", Woody(root=Path(tmp), offline=False).status())
            flaky.up = True
            self.assertEqual(w.sync(), "Synced 1 of 1 pending records.")
            self.assertEqual(flaky.got, [rec["id"]])
            self.assertEqual(w.sync(), "Synced 0 of 0 pending records.")


if __name__ == "__main__":
    unittest.main()
