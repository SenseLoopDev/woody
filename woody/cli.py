"""Command line: a thin front end over woody.core, for people and scripts.

Roles map to commands: Framer = init, Collector = add / import-bookings / import-surveys /
note / photo, Analyst = review, Concluder = conclude, Memory keeper = ask.
For Claude to call the same functions as tools, run the MCP server: python -m woody.mcp_server
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .core import Woody, parse_value
from .records import KIND_PREFIX


def _today(args: argparse.Namespace) -> date | None:
    return date.fromisoformat(args.today) if getattr(args, "today", None) else None


def _show(w: Woody) -> None:
    for line in w.warnings:
        print(f"warning: {line}", file=sys.stderr)


def run(args: argparse.Namespace) -> None:
    w = Woody(offline=args.offline)
    try:
        out = dispatch(w, args)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    if out:
        print(out)
    _show(w)


def dispatch(w: Woody, args: argparse.Namespace) -> str:
    cmd = args.cmd
    if cmd == "init":
        return w.init(json.loads(Path(args.charter).read_text(encoding="utf-8")))
    if cmd == "add":
        fields = {}
        for pair in args.fields:
            if "=" not in pair:
                raise ValueError(f"expected key=value, got {pair!r}")
            key, value = pair.split("=", 1)
            fields[key] = parse_value(value)
        links = [dict(zip(("rel", "to"), p.split("=", 1))) for p in args.link or []]
        return w.add(args.kind, fields, links, args.id)["id"]
    if cmd == "import-bookings":
        return f"Imported {w.import_csv('booking', args.csv)} booking records."
    if cmd == "import-surveys":
        return f"Imported {w.import_csv('survey', args.csv)} survey records."
    if cmd == "note":
        return w.note(args.text, args.session, args.about)
    if cmd == "photo":
        return w.photo(args.file, args.consent, args.caption, args.session, args.family_code)
    if cmd == "review":
        return w.review(_today(args))
    if cmd == "conclude":
        return w.conclude(_today(args), save=args.save, force=args.force)
    if cmd == "ask":
        return w.ask(args.question)
    if cmd == "sync":
        return w.sync()
    if cmd == "status":
        return w.status()
    raise ValueError(f"unknown command {cmd!r}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="woody", description="Woody Experiment Brain")
    p.add_argument("--offline", action="store_true", help="use the local stand-in instead of HydraDB")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="Framer: load a charter and write it to the brain")
    s.add_argument("--charter", required=True)

    s = sub.add_parser("add", help="Collector: add one record, e.g. add booking band=7-10 paid=true")
    s.add_argument("kind", choices=sorted(KIND_PREFIX))
    s.add_argument("fields", nargs="*", help="key=value pairs")
    s.add_argument("--link", action="append", help="rel=target, e.g. supports=H3")
    s.add_argument("--id")

    for name, what in (("import-bookings", "bookings"), ("import-surveys", "surveys")):
        s = sub.add_parser(name, help=f"Collector: import a {what} CSV")
        s.add_argument("csv")

    s = sub.add_parser("note", help="Collector: log an observation (text of a voice or text note)")
    s.add_argument("text")
    s.add_argument("--session")
    s.add_argument("--about", help="e.g. a hypothesis id such as H3")

    s = sub.add_parser("photo", help="Collector: register a photo with its consent choice")
    s.add_argument("file")
    s.add_argument("--caption", default="")
    s.add_argument("--consent", required=True, help="A, B or C from the consent form")
    s.add_argument("--session")
    s.add_argument("--family-code")

    s = sub.add_parser("review", help="Analyst: weekly review against the pass lines")
    s.add_argument("--today", help="YYYY-MM-DD, for testing")

    s = sub.add_parser("conclude", help="Concluder: go, pivot or stop report with evidence")
    s.add_argument("--today")
    s.add_argument("--save", action="store_true", help="also record the decision in the brain")
    s.add_argument("--force", action="store_true", help="save even if the reading is still provisional")

    s = sub.add_parser("ask", help="Memory keeper: retrieve connected context for a question")
    s.add_argument("question")

    sub.add_parser("sync", help="Send records saved locally but missing from HydraDB")
    sub.add_parser("status", help="Count records by kind")
    return p


def main(argv: list[str] | None = None) -> None:
    run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main(sys.argv[1:])
