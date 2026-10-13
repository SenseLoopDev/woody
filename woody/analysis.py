"""The Analyst and Concluder: exact numbers from the ledger, judged against the charter.

A charter is plain JSON, so the same code runs any experiment: hypotheses name a metric
(a count, sum, mean or share over records of one kind), a comparison and a pass line.
Decision rules turn hypothesis results into go, pivot or stop. Every number carries the
ids of the records behind it.
"""
from __future__ import annotations

import operator
from collections import defaultdict
from datetime import date
from typing import Any

OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "==": operator.eq, "!=": operator.ne}
THIN_SAMPLE = 5  # rates and means over fewer answers than this are flagged as weak evidence


def _rows(metric: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    where = metric.get("where", {})
    return [
        r for r in records
        if r["kind"] == metric["kind"] and all(r["fields"].get(k) == v for k, v in where.items())
    ]


def measure(metric: dict[str, Any], records: list[dict[str, Any]]) -> tuple[float | None, list[str]]:
    """Return (value, evidence record ids); value is None when there is no data."""
    rows = _rows(metric, records)
    agg = metric["agg"]
    if agg == "count":
        return float(len(rows)), [r["id"] for r in rows]
    field = metric["field"]
    used = [r for r in rows if field in r["fields"]]
    if not used:
        return None, []
    values = [r["fields"][field] for r in used]
    ids = [r["id"] for r in used]
    if agg == "sum":
        return float(sum(values)), ids
    if agg == "mean":
        return sum(values) / len(values), ids
    if agg == "share":
        pred = metric["predicate"]
        hit = sum(1 for v in values if OPS[pred["op"]](v, pred["value"]))
        return hit / len(values), ids
    raise ValueError(f"unknown aggregation {agg!r}")


def breakdown(metric: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, tuple[float | None, list[str]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in _rows(metric, records):
        groups[str(r["fields"].get(metric["group_by"], "unknown"))].append(r)
    return {g: measure(metric, rs) for g, rs in sorted(groups.items())}


def evaluate(charter: dict[str, Any], records: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    results = []
    for h in charter["hypotheses"]:
        metric = h["metric"]
        res: dict[str, Any] = {"id": h["id"], "title": h["title"], "flags": []}
        if "group_by" in metric:
            res["status"] = "report"
            res["groups"] = breakdown(metric, records)
            res["evidence"] = sorted({i for _, ids in res["groups"].values() for i in ids})
            if not res["groups"]:
                res["status"] = "no_data"
            results.append(res)
            continue
        value, ids = measure(metric, records)
        res.update(value=value, evidence=ids, n=len(ids), target=h.get("value"), op=h.get("op"))
        if value is None:
            res["status"] = "no_data"
        elif "op" in h:
            res["status"] = "pass" if OPS[h["op"]](value, h["value"]) else "miss"
        else:
            res["status"] = "report"
        if value is not None and metric["agg"] in ("share", "mean") and len(ids) < THIN_SAMPLE:
            res["flags"].append(f"thin evidence: only {len(ids)} responses")
        by = h.get("by")
        if by and res["status"] != "pass" and today > date.fromisoformat(by):
            res["flags"].append(f"deadline {by} has passed")
        results.append(res)
    return results


def decide(charter: dict[str, Any], results: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    """Apply the charter's ordered rules; the first rule whose conditions all hold wins.

    The reading is provisional until the charter's decide_after date, so an empty ledger
    on day one never reads as "stop".
    """
    today = today or date.today()
    after = charter.get("decide_after")
    final = after is None or today >= date.fromisoformat(after)
    values = {r["id"]: r.get("value") for r in results}
    for rule in charter["decision_rules"]:
        conds = rule["all"]
        ok = True
        for c in conds:
            v = values.get(c["h"])
            if v is None or not OPS[c["op"]](v, c["v"]):
                ok = False
                break
        if ok:
            return {"outcome": rule["outcome"], "because": rule.get("because", ""), "rule": conds, "final": final}
    return {"outcome": charter.get("default_outcome", "pivot"), "because": "No go or stop rule matched.", "rule": [], "final": final}


def _fmt(v: float | None, metric: dict[str, Any]) -> str:
    if v is None:
        return "no data"
    return f"{v:.0%}" if metric.get("agg") == "share" else f"{v:g}"


def weekly_review(charter: dict[str, Any], records: list[dict[str, Any]], today: date | None = None) -> str:
    results = evaluate(charter, records, today)
    lines = [f"# Weekly review: {charter['name']}", ""]
    for r, h in zip(results, charter["hypotheses"]):
        line = f"- {r['id']} {r['title']}: "
        if r["status"] == "report" and "groups" in r:
            parts = [f"{g} {_fmt(v, h['metric'])} (n={len(ids)})" for g, (v, ids) in r["groups"].items()]
            line += "; ".join(parts) or "no data"
        else:
            line += f"{_fmt(r.get('value'), h['metric'])}"
            if "op" in h:
                line += f" against {h['op']} {_fmt(h['value'], h['metric'])} -> {r['status'].upper()}"
        if r["flags"]:
            line += "  [" + "; ".join(r["flags"]) + "]"
        lines.append(line)
    d = decide(charter, results, today)
    label = "Current reading" if d["final"] else f"Provisional reading (final call after {charter['decide_after']})"
    lines += ["", f"{label}: {d['outcome'].upper()}" + (f" ({d['because']})" if d["because"] else "")]
    weak = [r for r in results if r["status"] == "no_data" or r["flags"]]
    lines.append("")
    if weak:
        lines.append("Decision needed from you: " + "; ".join(f"{r['id']} {r['title']} has weak or missing evidence" for r in weak) + ".")
    else:
        lines.append("Decision needed from you: none; evidence is in for every hypothesis.")
    return "\n".join(lines) + "\n"


def conclusion_report(charter: dict[str, Any], records: list[dict[str, Any]], today: date | None = None) -> tuple[str, dict[str, Any], list[str]]:
    """Return (markdown report, decision, all evidence ids). Each claim lists its records."""
    results = evaluate(charter, records, today)
    decision = decide(charter, results, today)
    evidence: list[str] = []
    lines = [f"# Conclusion: {charter['name']}", "", f"**Question.** {charter['question']}", ""]
    if not decision["final"]:
        lines += [f"**Provisional only.** The charter asks for the final call after {charter['decide_after']}.", ""]
    lines.append(f"**Decision: {decision['outcome'].upper()}.** {decision['because']}".rstrip())
    lines += ["", "## Findings", ""]
    for r, h in zip(results, charter["hypotheses"]):
        ids = r["evidence"]
        evidence += ids
        if "groups" in r:
            body = "; ".join(f"{g}: {_fmt(v, h['metric'])} (n={len(i)})" for g, (v, i) in r["groups"].items()) or "no data"
        else:
            body = _fmt(r.get("value"), h["metric"])
            if "op" in h:
                body += f" vs pass line {h['op']} {_fmt(h['value'], h['metric'])}: {r['status']}"
        flag = f" Caution: {'; '.join(r['flags'])}." if r["flags"] else ""
        lines.append(f"- **{r['id']} {r['title']}**: {body}.{flag} Evidence: {', '.join(ids) if ids else 'none'}.")
    return "\n".join(lines) + "\n", decision, sorted(set(evidence))
