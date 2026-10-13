"""Shared core: every command and every MCP tool calls these functions.

Rules baked in here so no front end can skip them: charters are validated before use,
readings stay provisional until the charter's final-call date, and a record is kept in the
local ledger even if HydraDB is unreachable (run sync to catch HydraDB up).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from .analysis import OPS, conclusion_report, weekly_review
from .hydra import HydraBrain, LocalBrain
from .records import KIND_PREFIX, REL_PHRASES, make_record
from .store import Ledger

# A field with one of these names also becomes a typed link in the graph.
AUTO_LINKS = {
    "session": "for_session",
    "family_code": "by_family",
    "post": "produced_by",
    "about": "about",
    "supports": "supports",
    "contradicts": "contradicts",
}
AGGS = {"count", "sum", "mean", "share"}
OUTCOMES = {"go", "pivot", "stop"}
SCALARS = (str, int, float, bool)


def parse_value(text: str) -> Any:
    low = text.strip().lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text.strip()


def auto_links(fields: dict[str, Any]) -> list[dict[str, str]]:
    return [{"rel": rel, "to": str(fields[name])} for name, rel in AUTO_LINKS.items() if fields.get(name) not in (None, "")]


def _is_date(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def validate_charter(c: dict[str, Any]) -> None:
    """Raise ValueError listing every problem, so an agent can fix a draft in one pass."""
    errors: list[str] = []
    for key in ("experiment_id", "name", "question", "hypotheses", "decision_rules"):
        if key not in c:
            errors.append(f"missing {key!r}")
    if errors:
        raise ValueError("; ".join(errors))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(c["experiment_id"])):
        errors.append("experiment_id must be lowercase letters, digits and hyphens")
    ids: set[str] = set()
    for h in c["hypotheses"]:
        hid = h.get("id", "?")
        if hid in ids or hid == "?":
            errors.append(f"hypothesis id {hid!r} is missing or repeated")
        ids.add(hid)
        if "title" not in h:
            errors.append(f"{hid}: missing title")
        m = h.get("metric")
        if not isinstance(m, dict):
            errors.append(f"{hid}: missing metric")
            continue
        if m.get("kind") not in KIND_PREFIX:
            errors.append(f"{hid}: metric kind must be one of {sorted(KIND_PREFIX)}")
        if m.get("agg") not in AGGS:
            errors.append(f"{hid}: metric agg must be one of {sorted(AGGS)}")
        elif m["agg"] != "count" and "field" not in m:
            errors.append(f"{hid}: {m['agg']} needs a field")
        if m.get("agg") == "share":
            p = m.get("predicate")
            if not (isinstance(p, dict) and p.get("op") in OPS and "value" in p):
                errors.append(f"{hid}: share needs predicate {{op, value}}")
        if "op" in h and (h["op"] not in OPS or "value" not in h):
            errors.append(f"{hid}: pass line needs op in {sorted(OPS)} and a value")
        if "group_by" in m and "op" in h:
            errors.append(f"{hid}: a group_by breakdown is report-only; remove op and value")
        if "by" in h and not _is_date(h["by"]):
            errors.append(f"{hid}: 'by' must be YYYY-MM-DD")
    for i, rule in enumerate(c["decision_rules"]):
        if rule.get("outcome") not in OUTCOMES:
            errors.append(f"rule {i}: outcome must be one of {sorted(OUTCOMES)}")
        for cond in rule.get("all", []):
            if cond.get("h") not in ids:
                errors.append(f"rule {i}: unknown hypothesis {cond.get('h')!r}")
            if cond.get("op") not in OPS or not isinstance(cond.get("v"), (int, float)):
                errors.append(f"rule {i}: condition needs op and a numeric v")
        if not rule.get("all"):
            errors.append(f"rule {i}: needs a non-empty 'all' list")
    if "decide_after" in c and not _is_date(c["decide_after"]):
        errors.append("decide_after must be YYYY-MM-DD")
    if c.get("default_outcome", "pivot") not in OUTCOMES:
        errors.append("default_outcome must be go, pivot or stop")
    if errors:
        raise ValueError("; ".join(errors))


class Woody:
    def __init__(self, offline: bool | None = None, root: Path | None = None):
        self.offline = bool(offline) or os.environ.get("WOODY_OFFLINE") == "1"
        self.ledger = Ledger(root)
        self.synced_path = self.ledger.root / "synced.txt"
        self.warnings: list[str] = []
        self._brain: Any = None

    # -- plumbing ------------------------------------------------------------
    def experiment_id(self) -> str:
        return self.ledger.load_charter()["experiment_id"]

    def brain(self, exp: str | None = None) -> Any:
        exp = exp or self.experiment_id()
        if self._brain is None:
            if self.offline:
                self._brain = LocalBrain(self.ledger, exp)
            else:
                self._brain = HydraBrain.from_env(os.environ.get("WOODY_DATABASE", "woody"), exp)
        return self._brain

    def _synced(self) -> set[str]:
        return set(self.synced_path.read_text().split()) if self.synced_path.exists() else set()

    def _remember(self, rec: dict[str, Any]) -> None:
        try:
            self.brain(rec["experiment_id"]).remember(rec)
        except Exception as exc:  # keep the record; sync later
            self.warnings.append(f"{rec['id']} is saved locally but not yet in HydraDB ({exc}). Run sync later.")
            return
        with self.synced_path.open("a") as f:
            f.write(rec["id"] + "\n")

    def _record(self, kind: str, exp: str, fields: dict[str, Any], links: list[dict[str, str]] | None = None,
                record_id: str | None = None) -> dict[str, Any]:
        bad = [k for k, v in fields.items() if not isinstance(v, SCALARS)]
        if bad:
            raise ValueError(f"field values must be text, numbers or true/false; fix: {bad}")
        rec = make_record(kind, exp, fields, (links or []) + auto_links(fields), record_id=record_id)
        self.ledger.append(rec)
        self._remember(rec)
        return rec

    # -- Framer --------------------------------------------------------------
    def init(self, charter: dict[str, Any]) -> str:
        validate_charter(charter)
        exp = charter["experiment_id"]
        existing = self.ledger.charter_path.exists() and self.ledger.load_charter()["experiment_id"]
        if existing and existing != exp and self.ledger.all():
            raise ValueError(f"This folder already holds experiment {existing!r}. Use a new WOODY_HOME for {exp!r}.")
        self.ledger.save_charter(charter)
        self._brain = None
        self.brain(exp).ensure_database()
        if any(r["kind"] == "experiment" for r in self.ledger.all(exp)):
            return f"{exp} is already set up; charter refreshed."
        self._record("experiment", exp, {"name": charter["name"], "question": charter["question"]}, record_id=f"EXP-{exp}")
        for h in charter["hypotheses"]:
            mid = f"MET-{exp}-{h['id']}"
            self._record("metric", exp, {"definition": json.dumps(h["metric"])}, record_id=mid)
            fields = {"hypothesis_id": h["id"], "title": h["title"],
                      "pass_line": f"{h.get('op', 'report only')} {h.get('value', '')}".strip()}
            if h.get("by"):
                fields["deadline"] = h["by"]
            self._record("hypothesis", exp, fields,
                         [{"rel": "measured_by", "to": mid}, {"rel": "part_of", "to": f"EXP-{exp}"}],
                         record_id=f"HYP-{exp}-{h['id']}")
        return f"Set up {exp}: {len(charter['hypotheses'])} hypotheses written."

    # -- Collector -----------------------------------------------------------
    def add(self, kind: str, fields: dict[str, Any], links: list[dict[str, str]] | None = None,
            record_id: str | None = None) -> dict[str, Any]:
        return self._record(kind, self.experiment_id(), fields, links, record_id)

    def add_many(self, kind: str, rows: list[dict[str, Any]]) -> int:
        exp = self.experiment_id()
        for row in rows:
            self._record(kind, exp, row)
        return len(rows)

    def import_csv(self, kind: str, path: str) -> int:
        if not str(path).lower().endswith(".csv"):
            raise ValueError("only .csv files can be imported")
        with open(path, newline="", encoding="utf-8") as f:
            rows = [{k.strip(): parse_value(v) for k, v in row.items() if k and v not in (None, "")}
                    for row in csv.DictReader(f)]
        return self.add_many(kind, rows)

    def note(self, text: str, session: str | None = None, about: str | None = None) -> str:
        fields: dict[str, Any] = {"text": text}
        if session:
            fields["session"] = session
        if about:
            fields["about"] = about
        return self._record("observation", self.experiment_id(), fields)["id"]

    def photo(self, file: str, consent: str, caption: str = "", session: str | None = None,
              family_code: str | None = None) -> str:
        consent = consent.upper()
        if consent not in ("A", "B", "C"):
            raise ValueError("consent must be A (records only), B (records and sharing) or C (no photos of the child)")
        fields: dict[str, Any] = {"file": file, "caption": caption, "consent": consent, "shareable": consent == "B"}
        if session:
            fields["session"] = session
        if family_code:
            fields["family_code"] = family_code
        return self._record("photo", self.experiment_id(), fields)["id"]

    # -- Analyst / Concluder / Memory keeper -----------------------------------
    def review(self, today: date | None = None) -> str:
        charter = self.ledger.load_charter()
        return weekly_review(charter, self.ledger.all(charter["experiment_id"]), today)

    def conclude(self, today: date | None = None, save: bool = False, force: bool = False) -> str:
        charter = self.ledger.load_charter()
        exp = charter["experiment_id"]
        report, decision, evidence = conclusion_report(charter, self.ledger.all(exp), today)
        if not save:
            return report
        if not decision["final"] and not force:
            raise ValueError(f"The reading is provisional until {charter['decide_after']}. Not saved; use force only if the user asks.")
        text = f"{decision['outcome'].upper()}: {decision['because']} Evidence: {', '.join(evidence)}"
        links = [{"rel": "based_on", "to": e} for e in evidence[:25]]
        rec = self._record("decision", exp, {"text": text}, links)
        (self.ledger.root / "conclusion.md").write_text(report, encoding="utf-8")
        return f"{report}\nSaved decision {rec['id']} and {self.ledger.root / 'conclusion.md'}"

    def ask(self, question: str) -> str:
        return self.brain().ask(question)

    def sync(self) -> str:
        done = self._synced()
        pending = [r for r in self.ledger.all(self.experiment_id()) if r["id"] not in done]
        self.brain().ensure_database()
        sent = 0
        for rec in pending:
            before = len(self.warnings)
            self._remember(rec)
            sent += len(self.warnings) == before
        return f"Synced {sent} of {len(pending)} pending records."

    def status(self) -> str:
        exp = self.experiment_id()
        counts = Counter(r["kind"] for r in self.ledger.all(exp))
        pending = len([r for r in self.ledger.all(exp) if r["id"] not in self._synced()]) if not self.offline else 0
        line = f"{exp}: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
        return line + (f"; {pending} not yet in HydraDB" if pending else "")
