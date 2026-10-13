"""Local ledger: the exact source of truth for numbers.

HydraDB retrieves connected passages; it does not add up seats. The ledger keeps every
record as one JSON line so pass lines are computed exactly and every number can be traced
to record ids. The same records are also written to HydraDB for graph recall.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def home() -> Path:
    return Path(os.environ.get("WOODY_HOME", ".woody"))


class Ledger:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else home()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.jsonl"
        self.charter_path = self.root / "charter.json"

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def all(self, experiment_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if experiment_id:
            rows = [r for r in rows if r["experiment_id"] == experiment_id]
        return rows

    def get(self, record_id: str) -> dict[str, Any] | None:
        return next((r for r in self.all() if r["id"] == record_id), None)

    def save_charter(self, charter: dict[str, Any]) -> None:
        self.charter_path.write_text(json.dumps(charter, indent=2), encoding="utf-8")

    def load_charter(self) -> dict[str, Any]:
        if not self.charter_path.exists():
            raise SystemExit("No charter yet. Run: python -m woody init --charter examples/woody_exp1.json")
        return json.loads(self.charter_path.read_text(encoding="utf-8"))
