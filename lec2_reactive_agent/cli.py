"""The command line shared by every version.

Each of v0.py to v4.py builds its parser from `build_parser`, so the five take
identical arguments and a trace from one is comparable with a trace from
another.

Run this module directly to inspect the alert queue:

    python -m agent.cli --list
    python -m agent.cli --category sqli
    python -m agent.cli --alert-id bc284c6b
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from lec2_reactive_agent.config import CONFIG, Config
from lec2_reactive_agent.exceptions import AlertNotFound




def load_findings(path: Path | None = None) -> dict:
    path = path or CONFIG.findings
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m intake.build_findings` first")
    return json.loads(path.read_text())


def iter_alerts(findings: dict):
    """Every alert across every category, in the document's order."""
    for block in findings["categories"].values():
        yield from block["alerts"]


def select_alert(alert_id: str | None = None,
                 location: str | None = None,
                 path: Path | None = None) -> dict:
    """Resolve a selector to exactly one alert.

    `location` is FILE:LINE. If it matches more than one alert — two scanners
    can flag the same line — that is an error naming the candidates, not a
    silent pick.
    """
    findings = load_findings(path)

    if alert_id:
        for alert in iter_alerts(findings):
            if alert["alert_id"] == alert_id:
                return alert
        raise AlertNotFound(f"no alert with id {alert_id!r}")

    if location:
        file, sep, line = location.rpartition(":")
        if not sep or not line.isdigit():
            raise AlertNotFound(f"--location must be FILE:LINE, got {location!r}")
        matches = [a for a in iter_alerts(findings)
                   if a["file"] == file and a["line"] == int(line)]
        if not matches:
            raise AlertNotFound(f"no alert at {location}")
        if len(matches) > 1:
            ids = ", ".join(f"{m['alert_id']} ({m['rule']})" for m in matches)
            raise AlertNotFound(
                f"{len(matches)} alerts at {location}; select one by id: {ids}")
        return matches[0]

    raise AlertNotFound("pass --alert-id or --location FILE:LINE")


def build_parser(description: str) -> argparse.ArgumentParser:
    """The argument surface every version shares."""
    p = argparse.ArgumentParser(description=description)

    sel = p.add_argument_group("alert selection")
    sel.add_argument("--alert-id", metavar="ID",
                     help=f"alert id from findings.json (worked example: {CONFIG.worked_example})")
    sel.add_argument("--location", metavar="FILE:LINE",
                     help="select by source location instead of id")

    run = p.add_argument_group("run")
    run.add_argument("--model", default=CONFIG.model,
                     help=f"provider:model string (default: {CONFIG.model})")
    run.add_argument("--seed", type=int, default=CONFIG.seed,
                     help="sampling seed; fixed seed + temperature 0 replays identically")
    run.add_argument("--budget", type=int, default=CONFIG.budget,
                     help=f"model-call ceiling, checked before each call (default: {CONFIG.budget})")
    run.add_argument("--trace", type=Path, default=None,
                     help="where to write the JSONL trace (default: runs/<version>-<alert>.jsonl)")
    return p


def resolve_alert(args) -> dict:
    """Selector -> alert, for a parser built by `build_parser`."""
    return select_alert(alert_id=args.alert_id, location=args.location)


def default_trace_path(version: str, alert: dict, config: Config = CONFIG) -> Path:
    return config.runs_dir / f"{version}-{alert['alert_id']}.jsonl"


def _describe(alert: dict) -> str:
    return (f"{alert['alert_id']}  {alert['severity']:<6} {alert['category']:<16}"
            f"{alert['file']}:{alert['line']}\n"
            f"          {alert['rule']}\n"
            f"          {alert['message']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Inspect the alert queue.")
    p.add_argument("--alert-id", metavar="ID")
    p.add_argument("--location", metavar="FILE:LINE")
    p.add_argument("--category", metavar="NAME", help="list one category's alerts")
    p.add_argument("--list", action="store_true", help="summarise the categories")
    args = p.parse_args(argv)

    findings = load_findings()

    if args.list:
        totals = findings["totals"]
        print(f"{totals['alerts']} alerts   {totals['by_scanner']}")
        print(f"severity  {totals['by_severity']}")
        for name, count in totals["by_category"].items():
            print(f"  {name:<16} {count:>4}")
        if findings["coverage_gaps"]:
            print(f"\nno alerts at all: {', '.join(findings['coverage_gaps'])}")
        return 0

    if args.category:
        block = findings["categories"].get(args.category)
        if block is None:
            print(f"no such category: {args.category}", file=sys.stderr)
            return 1
        for alert in block["alerts"]:
            print(_describe(alert), "\n")
        return 0

    try:
        print(_describe(select_alert(args.alert_id, args.location)))
    except (AlertNotFound, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
