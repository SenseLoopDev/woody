"""Record types for the experiment graph and how they become documents.

Every fact is a record with an id, a kind, free-form fields and typed links to other
records. The document text spells the links out as plain sentences, so HydraDB's graph
extraction can connect records, and so a person can read them too.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

KIND_PREFIX = {
    "experiment": "EXP",
    "hypothesis": "HYP",
    "metric": "MET",
    "post": "POST",
    "booking": "BKG",
    "session": "SES",
    "observation": "OBS",
    "photo": "PHO",
    "survey": "SUR",
    "incident": "INC",
    "cost": "CST",
    "decision": "DEC",
    "learning": "LRN",
}

# Facts that carry across conversations go to HydraDB memory; the rest is knowledge.
MEMORY_KINDS = {"observation", "decision", "learning"}

REL_PHRASES = {
    "supports": "supports",
    "contradicts": "contradicts",
    "measured_by": "is measured by",
    "for_session": "is for session",
    "by_family": "is by family",
    "produced_by": "was produced by",
    "based_on": "is based on",
    "about": "is about",
    "part_of": "is part of",
}


def new_id(kind: str) -> str:
    return f"{KIND_PREFIX[kind]}-{uuid.uuid4().hex[:6]}"


def make_record(
    kind: str,
    experiment_id: str,
    fields: dict[str, Any] | None = None,
    links: list[dict[str, str]] | None = None,
    record_id: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    if kind not in KIND_PREFIX:
        raise ValueError(f"unknown kind {kind!r}; choose from {sorted(KIND_PREFIX)}")
    for link in links or []:
        if link["rel"] not in REL_PHRASES:
            raise ValueError(f"unknown link type {link['rel']!r}; choose from {sorted(REL_PHRASES)}")
    return {
        "id": record_id or new_id(kind),
        "kind": kind,
        "experiment_id": experiment_id,
        "ts": ts or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fields": dict(fields or {}),
        "links": list(links or []),
    }


def to_document(record: dict[str, Any]) -> str:
    """Render a record as markdown that both people and the graph extractor can read."""
    kind = record["kind"]
    rid = record["id"]
    lines = [
        f"# {kind.capitalize()} {rid}",
        f"Experiment: {record['experiment_id']}",
        f"Recorded: {record['ts']}",
        "",
    ]
    for key, value in record["fields"].items():
        lines.append(f"- {key}: {value}")
    if record["links"]:
        lines += ["", "Relationships:"]
        for link in record["links"]:
            lines.append(f"- {kind.capitalize()} {rid} {REL_PHRASES[link['rel']]} {link['to']}.")
    return "\n".join(lines) + "\n"


def to_memory_text(record: dict[str, Any]) -> str:
    """One self-contained sentence-style memory for decisions, learnings and observations."""
    text = record["fields"].get("text") or record["fields"].get("summary") or ""
    links = "; ".join(f"{REL_PHRASES[l['rel']]} {l['to']}" for l in record["links"])
    head = f"[{record['kind']} {record['id']} in {record['experiment_id']}]"
    return f"{head} {text}" + (f" ({links})" if links else "")
