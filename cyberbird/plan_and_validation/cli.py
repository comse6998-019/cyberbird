"""The command line shared by every agent.

Every agent builds its parser from `build_parser`, so they all take identical
arguments and a trace from one is comparable with a trace from another.

Run this module directly to inspect the alert queue the last scan wrote:

    python -m cyberbird.plan_and_validation.cli --list
    python -m cyberbird.plan_and_validation.cli --category sqli
    python -m cyberbird.plan_and_validation.cli --alert-id bc284c6b
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from cyberbird.plan_and_validation.config import CONFIG, Config
from cyberbird.plan_and_validation.exceptions import AlertNotFound




def load_findings(path: Path | None = None) -> dict:
    path = path or CONFIG.findings
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python -m cyberbird.plan_and_validation.scan` first")
    return json.loads(path.read_text())


def iter_alerts(findings: dict):
    """Every alert across every category, in the document's order."""
    for block in findings["categories"].values():
        yield from block["alerts"]


def select_alert(alert_id: str | None = None,
                 location: str | None = None,
                 path: Path | None = None) -> dict:
    """Resolve a selector against the findings.json on disk."""
    return pick_alert(load_findings(path), alert_id, location)


def pick_alert(findings: dict, alert_id: str | None = None,
               location: str | None = None) -> dict:
    """Resolve a selector to exactly one alert in `findings`.

    `location` is FILE:LINE. If it matches more than one alert — Bandit can
    flag the same line under two rules — that is an error naming the
    candidates, not a silent pick.
    """
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
    """The argument surface every agent shares."""
    p = argparse.ArgumentParser(description=description)

    sel = p.add_argument_group("alert selection")
    sel.add_argument("--alert-id", metavar="ID",
                     help=f"alert id from the scan (worked example: {CONFIG.worked_example})")
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
                     help="where to write the JSONL trace (default: runs/<run>.jsonl)")
    return p


def selector_from_args(args) -> dict:
    """The alert selector a run starts from; the normalize node resolves it."""
    if not (args.alert_id or args.location):
        raise AlertNotFound("pass --alert-id or --location FILE:LINE")
    return {"alert_id": args.alert_id, "location": args.location}


def run_name(agent: str, selector: dict) -> str:
    """Named before the scan, so from the selector rather than the alert.

    An alert id names the run directly (`reactive-bc284c6b`). A location has no
    id until the scan resolves it, so the file stem and line stand in
    (`reactive-BenchmarkTest00283-46`).
    """
    if selector.get("alert_id"):
        return f"{agent}-{selector['alert_id']}"
    file, _, line = selector["location"].rpartition(":")
    return f"{agent}-{Path(file).stem}-{line}"


def default_trace_path(run: str, config: Config = CONFIG) -> Path:
    return config.runs_dir / f"{run}.jsonl"


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
