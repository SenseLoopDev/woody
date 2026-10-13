"""The memory layer: HydraDB (cloud) or a local stand-in for offline demos.

HydraBrain follows the documented HydraDB v2 flow: create a database, wait until it is
ready for ingestion, ingest knowledge and memories, wait until they are indexed, then
query. It takes any client with the same shape, so tests can pass a fake.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
from typing import Any

from .records import MEMORY_KINDS, to_document, to_memory_text


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    """Read nested values from SDK objects or plain dicts without assuming either."""
    for key in path:
        if obj is None:
            return default
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    return default if obj is None else obj


class HydraBrain:
    def __init__(self, client: Any, database: str, experiment_id: str, send_metadata: bool | None = None):
        self.client = client
        self.database = database
        self.experiment_id = experiment_id
        self.knowledge_collection = f"{experiment_id}_records"
        self.memory_collection = f"{experiment_id}_memory"
        if send_metadata is None:
            send_metadata = os.environ.get("WOODY_SEND_METADATA") == "1"
        self.send_metadata = send_metadata

    @classmethod
    def from_env(cls, database: str, experiment_id: str) -> "HydraBrain":
        key = os.environ.get("HYDRA_DB_API_KEY")
        if not key:
            raise SystemExit(
                "HYDRA_DB_API_KEY is not set. Create a key at dashboard.hydradb.com, "
                "export it in your shell, or run with --offline."
            )
        from hydra_db import HydraDB  # pip install "hydradb-sdk>=2,<3"

        return cls(HydraDB(token=key), database, experiment_id)

    # -- setup ---------------------------------------------------------------
    def ensure_database(self, timeout: float = 300, poll: float = 5) -> None:
        try:
            self.client.databases.create(database=self.database)
        except Exception as exc:  # already exists is fine; anything else shows up in status
            print(f"note: databases.create said: {exc}", file=sys.stderr)  # stdout is reserved for MCP
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.client.databases.status(database=self.database)
            if _get(status, "data", "infra", "ready_for_ingestion", default=False):
                return
            time.sleep(poll)
        raise TimeoutError(f"database {self.database!r} was not ready for ingestion in {timeout}s")

    # -- writing -------------------------------------------------------------
    def remember(self, record: dict[str, Any], wait: bool = False) -> str:
        """Write one record to HydraDB; decisions, learnings and observations become memories."""
        rid = record["id"]
        if record["kind"] in MEMORY_KINDS:
            self.client.context.ingest(
                type="memory",
                database=self.database,
                collection=self.memory_collection,
                memories=json.dumps([{"text": to_memory_text(record)}]),
            )
            collection = self.memory_collection
        else:
            meta: dict[str, Any] = {"id": rid}
            if self.send_metadata:
                meta["additional_metadata"] = {"kind": record["kind"], "experiment_id": record["experiment_id"]}
            body = io.BytesIO(to_document(record).encode("utf-8"))
            self.client.context.ingest(
                type="knowledge",
                database=self.database,
                collection=self.knowledge_collection,
                documents=[(f"{rid}.md", body, "text/markdown")],
                document_metadata=json.dumps([meta]),
            )
            collection = self.knowledge_collection
        if wait and record["kind"] not in MEMORY_KINDS:
            self.wait_indexed([rid], collection)
        return rid

    def wait_indexed(self, ids: list[str], collection: str, timeout: float = 300, poll: float = 3) -> bool:
        """Poll until the items are searchable (graph_creation) or completed."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self.client.context.status(database=self.database, collection=collection, ids=ids)
            statuses = _get(resp, "data", "statuses", default=[]) or []
            states = [str(_get(s, "indexing_status", default="")) for s in statuses]
            if any(s in ("errored", "failed") for s in states):
                raise RuntimeError(f"indexing failed for {ids}: {states}")
            if states and all(s in ("graph_creation", "completed") for s in states):
                return True
            time.sleep(poll)
        return False

    # -- reading -------------------------------------------------------------
    def ask(self, question: str, max_results: int = 10) -> str:
        """Retrieve connected passages for a question. HydraDB returns context, not an answer."""
        results = self.client.query(
            database=self.database,
            collections=[self.knowledge_collection, self.memory_collection],
            type="all",
            query=question,
            max_results=max_results,
        )
        text = _format(results)
        request_id = _get(results, "meta", "request_id")
        if request_id:
            self._feedback(request_id)
        return text

    def _feedback(self, request_id: str) -> None:
        """Fire and forget, as HydraDB's agent guide asks; never surface failures."""
        try:
            self.client.feedback.submit(request_id=request_id, source="agent")
        except Exception:
            pass


def _format(results: Any) -> str:
    try:
        from hydra_db.helpers import build_string

        return build_string(results)
    except Exception:
        pass
    passages = _get(results, "passages")
    if passages:
        return "\n\n".join(str(p) for p in passages)
    return str(results)


class LocalBrain:
    """Offline stand-in with the same interface: keyword search over the local ledger.

    It lets the whole loop run with no key and no network, which is how the demo and the
    tests run. It has no graph; only HydraBrain follows relationships.
    """

    def __init__(self, ledger: Any, experiment_id: str):
        self.ledger = ledger
        self.experiment_id = experiment_id

    def ensure_database(self, **_: Any) -> None:
        return None

    def remember(self, record: dict[str, Any], wait: bool = False) -> str:
        return record["id"]  # already in the ledger

    def ask(self, question: str, max_results: int = 10) -> str:
        words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
        scored = []
        for rec in self.ledger.all(self.experiment_id):
            doc = to_document(rec)
            hits = sum(1 for w in words if w in doc.lower())
            if hits:
                scored.append((hits, doc))
        scored.sort(key=lambda t: -t[0])
        return "\n\n".join(doc for _, doc in scored[:max_results]) or "No matching records."
