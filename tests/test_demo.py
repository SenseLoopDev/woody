"""The demo page runs a JavaScript port of the analysis engine. These tests run the port and the
Python original on the same records, for every day of both sample experiments, and require identical
numbers, flags, decisions, report text, documents and search ranking."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import unittest
from datetime import date, timedelta
from pathlib import Path

from woody.analysis import conclusion_report, decide, evaluate, weekly_review
from woody.records import to_document

DEMO = Path(__file__).resolve().parent.parent / "demo"
QUESTIONS = ["what did kids think of the pulley toy", "which channel brought families", "was there an injury",
             "can we share photos", "what did buyers say about the price", "Instagram or Google", "zzz nothing here"]


def rank(records, question):
    import re
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
    scored = []
    for rec in records:
        doc = to_document(rec).lower()
        hits = sum(1 for w in words if w in doc)
        if hits:
            scored.append((hits, rec["id"]))
    scored.sort(key=lambda t: -t[0])
    return [list(t) for t in scored[:10]]


def same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class DemoParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((DEMO / "data.json").read_text())
        run = subprocess.run(["node", str(DEMO / "parity.js")], capture_output=True, text=True, cwd=DEMO, check=True)
        cls.js = json.loads(run.stdout)

    def test_every_day_matches(self):
        checked = 0
        for exp in self.data["experiments"]:
            js_days = self.js[exp["id"]]["days"]
            d, i = date.fromisoformat(exp["start"]), 0
            while d <= date.fromisoformat(exp["end"]):
                day = d.isoformat()
                jd = js_days[i]
                self.assertEqual(jd["day"], day)
                recs = [r for r in exp["records"] if r["ts"][:10] <= day]
                results = evaluate(exp["charter"], recs, d)
                decision = decide(exp["charter"], results, d)
                with self.subTest(exp=exp["id"], day=day):
                    self.assertEqual(len(results), len(jd["results"]))
                    for py, js in zip(results, jd["results"]):
                        self.assertEqual(py["id"], js["id"])
                        self.assertEqual(py["status"], js["status"])
                        self.assertEqual(py["flags"], js["flags"])
                        self.assertEqual(py["evidence"], js["evidence"])
                        if "groups" in py:
                            self.assertEqual([[g, ids] for g, (_, ids) in py["groups"].items()], [[g[0], g[2]] for g in js["groups"]])
                            for (g, (v, _)), jg in zip(py["groups"].items(), js["groups"]):
                                self.assertTrue(v == jg[1] if v is None else same(v, jg[1]))
                        else:
                            v = py["value"]
                            self.assertTrue(js["value"] is None if v is None else same(v, js["value"]))
                    self.assertEqual(decision["outcome"], jd["decision"]["outcome"])
                    self.assertEqual(decision["final"], jd["decision"]["final"])
                    self.assertEqual(decision["because"], jd["decision"]["because"])
                    self.assertEqual(weekly_review(exp["charter"], recs, d), jd["review"])
                    report, _, evidence = conclusion_report(exp["charter"], recs, d)
                    self.assertEqual(report, jd["report"])
                    self.assertEqual(evidence, jd["evidence"])
                checked += 1
                d += timedelta(days=1)
                i += 1
        self.assertGreater(checked, 60)

    def test_documents_and_search_match(self):
        for exp in self.data["experiments"]:
            js = self.js[exp["id"]]
            self.assertEqual([to_document(r) for r in exp["records"]], js["documents"])
            for q, got in zip(QUESTIONS, js["search"]):
                with self.subTest(exp=exp["id"], q=q):
                    self.assertEqual(rank(exp["records"], q), got)


class DemoStory(unittest.TestCase):
    """The sample data tells the story the page claims."""

    @classmethod
    def setUpClass(cls):
        cls.data = {e["id"]: e for e in json.loads((DEMO / "data.json").read_text())["experiments"]}

    def decision_on(self, exp, day):
        e = self.data[exp]
        d = date.fromisoformat(day)
        recs = [r for r in e["records"] if r["ts"][:10] <= day]
        return decide(e["charter"], evaluate(e["charter"], recs, d), d)

    def test_woodworking_arc(self):
        self.assertEqual(self.decision_on("woody-exp1", "2026-10-15"), {**self.decision_on("woody-exp1", "2026-10-15"), "outcome": "stop", "final": False})
        self.assertEqual(self.decision_on("woody-exp1", "2026-11-03")["outcome"], "pivot")
        final = self.decision_on("woody-exp1", "2026-11-10")
        self.assertEqual((final["outcome"], final["final"]), ("go", True))

    def test_pricing_final_is_go(self):
        final = self.decision_on("pricing-test", "2026-12-02")
        self.assertEqual((final["outcome"], final["final"]), ("go", True))

    def test_thin_evidence_flag_appears_after_session_one(self):
        e = self.data["woody-exp1"]
        recs = [r for r in e["records"] if r["ts"][:10] <= "2026-11-07"]
        flags = [f for r in evaluate(e["charter"], recs, date(2026, 11, 7)) for f in r["flags"]]
        self.assertIn("thin evidence: only 4 responses", flags)


if __name__ == "__main__":
    unittest.main()
