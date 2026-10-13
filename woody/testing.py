"""A fake HydraDB client with the same call shapes, used by the tests."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace as NS
from typing import Any


class _Databases:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.polls = 0

    def create(self, database: str) -> None:
        self.created.append(database)

    def status(self, database: str) -> Any:
        self.polls += 1
        return NS(data=NS(infra=NS(ready_for_ingestion=self.polls >= 2)))


class _Context:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], str] = {}
        self.memories: list[tuple[str, str]] = []
        self.calls: list[dict[str, Any]] = []

    def ingest(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if kw["type"] == "knowledge":
            meta = json.loads(kw["document_metadata"])
            for (name, fileobj, _mime), m in zip(kw["documents"], meta):
                self.docs[(kw["collection"], m["id"])] = fileobj.read().decode("utf-8")
        else:
            for m in json.loads(kw["memories"]):
                self.memories.append((kw["collection"], m["text"]))
        return NS(data=NS(success_count=1))

    def status(self, database: str, collection: str, ids: list[str]) -> Any:
        return NS(data=NS(statuses=[NS(id=i, indexing_status="completed") for i in ids]))


class FakeHydraDB:
    def __init__(self) -> None:
        self.databases = _Databases()
        self.context = _Context()
        self.feedback = NS(submit=lambda **kw: None)

    def query(self, database: str, collections: list[str], type: str, query: str, **kw: Any) -> Any:
        words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
        passages = []
        for (coll, _id), text in self.context.docs.items():
            if coll in collections and any(w in text.lower() for w in words):
                passages.append(text)
        for coll, text in self.context.memories:
            if coll in collections and any(w in text.lower() for w in words):
                passages.append(text)
        return NS(passages=passages, meta=NS(request_id="req-fake"))
