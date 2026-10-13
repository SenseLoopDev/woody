"""Run both example experiments offline with synthetic data: python examples/demo.py

Shows that one engine handles two different experiments: the woodworking demand test and a
$29 vs $39 subscription-box pricing test. No key and no network are needed. The data in
examples/*.csv is made up for the demo.
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from woody.core import Woody  # noqa: E402


def banner(text: str) -> None:
    print("\n" + "=" * 70 + f"\n{text}\n" + "=" * 70)


def run(charter_file: str, bookings: str, surveys: str, today: date, notes: list[tuple[str, str]], question: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        w = Woody(offline=True, root=Path(tmp))
        charter = json.loads((HERE / charter_file).read_text())
        banner(f"Framer: {charter['name']}")
        print(w.init(charter))
        print(f"Question: {charter['question']}")
        banner("Collector: importing bookings, surveys and notes")
        print(f"bookings: {w.import_csv('booking', str(HERE / bookings))}")
        print(f"surveys:  {w.import_csv('survey', str(HERE / surveys))}")
        for text, about in notes:
            print("note:", w.note(text, session="S1", about=about))
        print(w.status())
        banner("Analyst: review")
        print(w.review(today))
        banner("Concluder: conclusion")
        print(w.conclude(today, save=True).split("Saved decision")[0])
        banner("Memory keeper: ask")
        print(f"Q: {question}")
        print(w.ask(question)[:400])


run("woody_exp1.json", "sample_bookings.csv", "sample_surveys.csv", date(2026, 11, 10),
    [("Kids loved the pulley toy but hammering took too long", "H3")],
    "what did kids think of the pulley toy and hammering")
run("pricing_test.json", "pricing_bookings.csv", "pricing_surveys.csv", date(2026, 12, 2),
    [("Visitors on mobile hesitated at the $39 tier", "H2")],
    "why did mobile visitors hesitate at the higher tier")
