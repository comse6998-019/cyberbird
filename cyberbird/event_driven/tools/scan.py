"""The scan tool: Bandit over a tree, normalized to alerts, in one call.

An alert is six fields: alert_id, rule, file, line, message, severity.
`alert_id` hashes (rule, file, line, col), so it is the same on every machine.
"""
from __future__ import annotations

import hashlib
import json
import subprocess

from pydantic import BaseModel

from cyberbird.event_driven.tools.base import Tool

# Run from inside the tree, so every path is relative to it ("./testcode/...").
BANDIT_ARGV = ["bandit", "-r", ".", "-f", "json", "-q"]


def alert_id(rule: str, file: str, line: int, col: int) -> str:
    return hashlib.sha256(f"{rule}|{file}|{line}|{col}".encode()).hexdigest()[:8]


class Scan(Tool):
    """Run Bandit over a tree and return its alerts."""
    name = "scan"

    class Args(BaseModel):
        root: str

    def run(self, args: Args) -> list[dict]:
        """Run Bandit and normalize its report; exit 1 means findings, not failure."""
        proc = subprocess.run(BANDIT_ARGV, cwd=args.root, capture_output=True,
                              text=True, timeout=120)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"bandit produced no report: {proc.stderr.strip()}")

        alerts = []
        for r in report.get("results", []):
            rule = f"bandit:{r['test_id']}"
            file = r["filename"].removeprefix("./")
            line = int(r["line_number"])
            alerts.append({
                "alert_id": alert_id(rule, file, line, r.get("col_offset", 0)),
                "rule": rule, "file": file, "line": line,
                "message": " ".join(r["issue_text"].split()),
                "severity": r["issue_severity"].lower(),
            })
        return sorted(alerts, key=lambda a: (a["file"], a["line"], a["rule"]))

    def render(self, value: list[dict]) -> str:
        return f"{len(value)} alerts"
