"""Build the self-contained demo page: demo/woody_demo.html (and demo/data.json).

The sample data is synthetic. Bookings and surveys come from the CSV files in examples/, with
dates added here; posts, sessions, notes, photos, costs, incidents and learnings are written
out below. Records are made with the same functions the real tool uses.

    PYTHONPATH=. python3 demo/build_demo.py
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from woody.core import auto_links, parse_value, validate_charter
from woody.records import make_record

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
OUT = Path(__file__).resolve().parent


def read_csv(name: str) -> list[dict]:
    with open(EX / name, newline="", encoding="utf-8") as f:
        return [{k.strip(): parse_value(v) for k, v in row.items() if k and v not in (None, "")}
                for row in csv.DictReader(f)]


class Builder:
    def __init__(self, exp: str):
        self.exp = exp
        self.items: list[tuple[str, int, dict]] = []
        self.counts: dict[str, int] = {}

    def add(self, kind: str, fields: dict, ts: str, rid: str | None = None, extra_links=None) -> str:
        from woody.records import KIND_PREFIX
        if rid is None:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            rid = f"{KIND_PREFIX[kind]}-{self.counts[kind]:03d}"
        links = auto_links(fields) + list(extra_links or [])
        rec = make_record(kind, self.exp, fields, links, record_id=rid, ts=ts)
        self.items.append((ts, len(self.items), rec))
        return rid

    def records(self) -> list[dict]:
        return [r for _, _, r in sorted(self.items, key=lambda t: (t[0], t[1]))]


def woodworking() -> dict:
    charter = json.loads((EX / "woody_exp1.json").read_text())
    validate_charter(charter)
    b = Builder(charter["experiment_id"])
    posts = {
        "nextdoor": b.add("post", {"channel": "nextdoor", "text": "Backyard woodworking for kids, Nov 7 and 8. Six seats an hour."}, "2026-10-15T09:00:00"),
        "facebook": b.add("post", {"channel": "facebook", "text": "Neighborhood parents group: kids build a pulley toy, parents join free."}, "2026-10-15T19:30:00"),
        "homeschool_group": b.add("post", {"channel": "homeschool_group", "text": "Homeschool group: hands-on weekend class, ages 7 and up."}, "2026-10-16T08:15:00"),
    }
    b.add("session", {"name": "Session 1", "date": "2026-11-07", "seats_booked": 6}, "2026-10-15T09:00:00", rid="S1")
    b.add("session", {"name": "Session 2", "date": "2026-11-08", "seats_booked": 5}, "2026-10-15T09:00:00", rid="S2")

    book_dates = {"F01": "10-15", "F02": "10-15", "F03": "10-17", "F04": "10-20", "F05": "10-22", "F06": "10-22",
                  "F07": "10-26", "F08": "10-29", "F09": "11-01", "F10": "11-02", "F11": "11-03", "F12": "10-28"}
    for row in read_csv("sample_bookings.csv"):
        row["post"] = posts[row["channel"]]
        d = book_dates[row["family_code"]]
        b.add("booking", row, f"2026-{d}T{12 + int(row['family_code'][1:]) % 6:02d}:10:00")

    survey_dates = {"F06": "2026-11-08T09:30:00"}  # a late reply, the day after the session
    for row in read_csv("sample_surveys.csv"):
        ts = survey_dates.get(row["family_code"]) or ("2026-11-07T14:00:00" if row["session"] == "S1" else "2026-11-08T14:00:00")
        b.add("survey", row, ts)

    b.add("observation", {"text": "Nextdoor post drew the most replies. Three parents asked what ages it suits.", "about": "H5"}, "2026-10-16T21:00:00")
    b.add("observation", {"text": "Kids loved the pulley toy. The 11+ group finished 15 minutes early and asked for a harder build.", "session": "S1", "about": "H3"}, "2026-11-07T13:00:00")
    b.add("observation", {"text": "Two parents asked whether there is a four-week series.", "session": "S2", "about": "H6"}, "2026-11-08T13:20:00")
    b.add("photo", {"file": "s1_pulley_build.jpg", "caption": "Pulley toy build, safety glasses on", "consent": "B", "shareable": True, "session": "S1", "family_code": "F02"}, "2026-11-07T13:30:00")
    b.add("photo", {"file": "s1_sanding.jpg", "caption": "Sanding station", "consent": "A", "shareable": False, "session": "S1", "family_code": "F04"}, "2026-11-07T13:40:00")
    b.add("photo", {"file": "s2_finished_toys.jpg", "caption": "Finished pulley toys lined up", "consent": "B", "shareable": True, "session": "S2", "family_code": "F07"}, "2026-11-08T13:30:00")
    b.add("cost", {"item": "Starter kit: hand tools, safety glasses, clamps", "amount": 283, "category": "tools"}, "2026-10-26T16:00:00")
    b.add("cost", {"item": "Lumber, pulleys and rope for both sessions", "amount": 96, "category": "materials"}, "2026-11-05T10:00:00")
    b.add("incident", {"text": "Small splinter during sanding. First aid kit used, parent informed, no further action.", "severity": "minor", "session": "S1"}, "2026-11-07T13:15:00")
    b.add("learning", {"text": "Seats filled in 12 days from three local channels and parents rated value 4 or 5. Next test: a four-week series.", "supports": "H1"}, "2026-11-09T18:00:00")

    return {
        "id": charter["experiment_id"], "label": "Backyard woodworking pilot", "charter": charter,
        "records": b.records(), "start": "2026-10-12", "end": "2026-11-13", "default_asof": "2026-11-10",
        "milestones": [
            {"date": "2026-10-15", "label": "Posts go live"}, {"date": "2026-11-03", "label": "Bookings close"},
            {"date": "2026-11-07", "label": "Session 1"}, {"date": "2026-11-08", "label": "Session 2"},
            {"date": "2026-11-09", "label": "Final call"}],
        "presets": [
            {"date": "2026-10-15", "label": "Launch day"}, {"date": "2026-10-28", "label": "Mid-run"},
            {"date": "2026-11-03", "label": "Bookings close"}, {"date": "2026-11-07", "label": "After session 1"},
            {"date": "2026-11-10", "label": "Final call"}],
        "chart_default": "H1",
        "ask_examples": ["what did kids think of the pulley toy", "which channel brought families", "was there an injury", "can we share photos"],
        "new_booking": {"kind": "woodworking"},
    }


def pricing() -> dict:
    charter = json.loads((EX / "pricing_test.json").read_text())
    validate_charter(charter)
    b = Builder(charter["experiment_id"])
    posts = {
        "instagram_ads": b.add("post", {"channel": "instagram_ads", "text": "Instagram ad set: monthly craft box, two price tests."}, "2026-11-03T08:00:00"),
        "google_ads": b.add("post", {"channel": "google_ads", "text": "Google search ad: monthly craft box for kids."}, "2026-11-03T08:30:00"),
    }
    b.add("session", {"name": "Launch week pre-order page", "date": "2026-11-03"}, "2026-11-03T08:00:00", rid="launch")

    rng = random.Random(7)
    paid = [r for r in read_csv("pricing_bookings.csv") if r["paid"] is True]
    unpaid = [r for r in read_csv("pricing_bookings.csv") if r["paid"] is False]
    rng.shuffle(paid)  # two prices run side by side, so arrivals interleave
    offsets = sorted(rng.randrange(0, 26) for _ in paid)  # Nov 3 .. Nov 28
    from datetime import date, timedelta
    for row, off in zip(paid, offsets):
        row["post"] = posts[row["channel"]]
        d = date(2026, 11, 3) + timedelta(days=off)
        b.add("booking", row, f"{d.isoformat()}T{9 + (off * 7 + len(b.items)) % 11:02d}:20:00")
    for row, d in zip(unpaid, ["2026-11-10", "2026-11-17", "2026-11-24"]):
        row["post"] = posts[row["channel"]]
        b.add("booking", row, f"{d}T15:00:00")
    for i, row in enumerate(read_csv("pricing_surveys.csv")):
        b.add("survey", row, f"2026-11-{29 + i % 2}T{10 + i}:00:00")

    b.add("cost", {"item": "Ad spend, first two weeks", "amount": 300, "category": "ads"}, "2026-11-03T09:00:00")
    b.add("cost", {"item": "Ad spend, second two weeks", "amount": 250, "category": "ads"}, "2026-11-17T09:00:00")
    b.add("observation", {"text": "Two comments on the $39 ad asked about shipping cost.", "about": "H2"}, "2026-11-12T11:00:00")
    b.add("observation", {"text": "Instagram brings more pre-orders than Google at both prices.", "about": "H4"}, "2026-11-20T11:00:00")
    b.add("learning", {"text": "More families chose $29, but $39 buyers still lifted revenue past the pass line. Next test: a $34 price.", "supports": "H3"}, "2026-11-30T17:00:00")

    return {
        "id": charter["experiment_id"], "label": "Subscription box price test", "charter": charter,
        "records": b.records(), "start": "2026-11-02", "end": "2026-12-04", "default_asof": "2026-12-02",
        "milestones": [{"date": "2026-11-03", "label": "Ads go live"}, {"date": "2026-12-01", "label": "Final call"}],
        "presets": [
            {"date": "2026-11-03", "label": "Ads live"}, {"date": "2026-11-12", "label": "Week 2"},
            {"date": "2026-11-20", "label": "Week 3"}, {"date": "2026-11-30", "label": "Last day"},
            {"date": "2026-12-02", "label": "Final call"}],
        "chart_default": "H1",
        "ask_examples": ["what did buyers say about the price", "which ad channel works", "did anyone ask about shipping"],
        "new_booking": {"kind": "pricing"},
    }


def main() -> None:
    data = {"experiments": [woodworking(), pricing()]}
    (OUT / "data.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    html = (OUT / "template.html").read_text(encoding="utf-8")
    engine = (OUT / "engine.js").read_text(encoding="utf-8")
    page = (html.replace("/*__ENGINE__*/", engine)
                .replace("/*__DATA__*/", "window.WOODY_DATA = " + json.dumps(data, separators=(",", ":")).replace("</", "<\\/") + ";"))
    (OUT / "woody_demo.html").write_text(page, encoding="utf-8")
    for e in data["experiments"]:
        print(f"{e['id']}: {len(e['records'])} records")
    print(f"wrote {OUT / 'woody_demo.html'} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
