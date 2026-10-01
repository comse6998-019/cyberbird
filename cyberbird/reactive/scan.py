"""The scanner tool: run Bandit over the fixture and turn its report into findings.json.

The graph's first two nodes are thin wrappers over this module:

    scan        run_bandit()                          -> runs/scans/bandit.json
    normalize   build_findings() + write_findings()   -> runs/findings.json

Neither node calls a model. Everything below `run_bandit` is pure: parsed JSON
in, plain dicts out, so the alert contract is testable without Bandit installed.

Run it on its own to refresh the alert queue without starting an agent:

    python -m cyberbird.reactive.scan

The alert contract is five fields, plus two derived values:

    rule      scanner-prefixed rule id, e.g. "bandit:B608"
    file      path relative to the fixture root
    line      1-based line number
    message   single-line description
    severity  high | medium | low

    alert_id  stable 8-hex identity over (rule, file, line, col)
    category  derived from the CWE and the rule id

Bandit reports nothing in seven of the fixture's categories (ldapi, pathtraver,
redirect, securecookie, trustbound, xpathi, xss). findings.json lists them under
`coverage_gaps` rather than leaving them silently absent.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cyberbird.reactive.config import CONFIG, Config

REPO = "https://github.com/OWASP-Benchmark/BenchmarkPython"
COMMIT = "f1291485808b66e20ddb6b01b10dc71b3df8c8ba"
PINNED_FILE_COUNT = 2540

# Moved to ground-truth/ beside src/, so the agent cannot read the answers out of the fixture.
STRIPPED = [
    "expectedresults-0.1.csv",
    "results/BenchmarkPython-Bandit.sarif",
    "results/Benchmark-Bearer-v1.51.1.json",
]

_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}

# The fixture's own vocabulary, read from expectedresults-0.1.csv, so a
# category here is directly comparable to the ground-truth label.
CATEGORY_BY_CWE = {
    22: "pathtraver",
    78: "cmdi",
    79: "xss",
    89: "sqli",
    90: "ldapi",
    94: "codeinj",
    327: "hash",            # broken or risky algorithm
    328: "hash",            # reversible one-way hash (the fixture's own label)
    330: "weakrand",
    501: "trustbound",
    502: "deserialization",
    601: "redirect",
    611: "xxe",
    614: "securecookie",
    643: "xpathi",
}

# Two cases where Bandit's CWE is too coarse to categorize on, so the rule id
# decides instead:
#   CWE-20 (Improper Input Validation) is where Bandit files its entire XML
#          parser cluster, plus yaml.load.
#   CWE-78 (OS Command Injection) covers subprocess use *and* eval/exec, which
#          the fixture separates into cmdi and codeinj.
CATEGORY_BY_RULE = {
    "bandit:B405": "xxe",              # import xml.etree
    "bandit:B314": "xxe",              # xml.etree parse
    "bandit:B406": "xxe",              # import xml.sax
    "bandit:B408": "xxe",              # import xml.dom.minidom
    "bandit:B317": "xxe",              # xml.sax.make_parser
    "bandit:B318": "xxe",              # xml.dom.minidom.parse
    "bandit:B506": "deserialization",  # yaml.load
    "bandit:B307": "codeinj",          # eval
    "bandit:B102": "codeinj",          # exec
    "bandit:B404": "cmdi",             # import subprocess
    "bandit:B602": "cmdi",             # subprocess, shell=True
    "bandit:B603": "cmdi",             # subprocess, no shell
}

# Deliberately NOT mapped, and therefore reported as "other":
#   CWE-259 hardcoded password  (bandit B106)
# It has no counterpart in the fixture's 14 categories. Inventing a nearest fit
# would misreport it, so it surfaces in "other" for a human.

UNCATEGORIZED = "other"

# The fixture's full vocabulary. Categories with no alerts are reported as
# coverage gaps rather than silently omitted.
FIXTURE_CATEGORIES = sorted(set(CATEGORY_BY_CWE.values()))

CWE_BY_CATEGORY = collections.defaultdict(list)
for _cwe, _cat in sorted(CATEGORY_BY_CWE.items()):
    CWE_BY_CATEGORY[_cat].append(_cwe)


# ---------------------------------------------------------------- the scanner

# Run from inside the fixture, so every path in the report is relative to it
# ("./testcode/...") and the saved report is the same on every machine.
BANDIT_ARGV = ["bandit", "-r", ".", "-f", "json", "-q"]


def run_bandit(config: Config = CONFIG) -> dict:
    """Scan the pinned fixture, save the raw report, and return it parsed."""
    proc = subprocess.run(BANDIT_ARGV, cwd=config.fixture, capture_output=True,
                          text=True, timeout=config.scan_timeout)
    # bandit exits 1 when it finds something; only a crash has no JSON.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"bandit produced no report: {proc.stderr.strip()}")
    config.scan_report.parent.mkdir(parents=True, exist_ok=True)
    config.scan_report.write_text(json.dumps(report, indent=2) + "\n")
    return report


# ---------------------------------------------------------------- normalize

def canonical_rule_id(scanner: str, raw_id: str) -> str:
    return f"{scanner}:{raw_id}"


def canonical_severity(raw_severity: str) -> str:
    return _SEVERITY.get(str(raw_severity).upper(), "low")


def alert_id(rule: str, file: str, line: int, col: int) -> str:
    """Stable identity, deterministic across runs and machines."""
    return hashlib.sha256(f"{rule}|{file}|{line}|{col}".encode()).hexdigest()[:8]


def categorize(cwe, rule: str) -> str:
    """Map a finding onto the fixture's category vocabulary.

    The rule id wins over the CWE, because the overrides above exist precisely
    for the cases where the CWE is not specific enough.
    """
    if rule in CATEGORY_BY_RULE:
        return CATEGORY_BY_RULE[rule]
    if cwe is not None and int(cwe) in CATEGORY_BY_CWE:
        return CATEGORY_BY_CWE[int(cwe)]
    return UNCATEGORIZED


def _one_line(text) -> str:
    return " ".join(str(text).split())


def _relative(path: str) -> str:
    return path[2:] if path.startswith("./") else path


def normalize_bandit(doc: dict) -> list[dict]:
    alerts = []
    for r in doc.get("results", []):
        rule = canonical_rule_id("bandit", r["test_id"])
        file = _relative(r["filename"])
        alerts.append({
            "alert_id": alert_id(rule, file, r["line_number"], r.get("col_offset", 0)),
            "rule": rule,
            "file": file,
            "line": int(r["line_number"]),
            "message": _one_line(r["issue_text"]),
            "severity": canonical_severity(r["issue_severity"]),
            "category": categorize((r.get("issue_cwe") or {}).get("id"), rule),
        })
    return alerts


def _tool_version(argv: list[str]) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return (out.stdout + out.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"


def build_findings(report: dict, config: Config = CONFIG) -> dict:
    """The raw Bandit report as findings.json: normalized, grouped, with provenance."""
    alerts = normalize_bandit(report)
    alerts.sort(key=lambda a: (a["file"], a["line"], a["rule"]))

    ids = collections.Counter(a["alert_id"] for a in alerts)
    collisions = sorted(i for i, c in ids.items() if c > 1)

    grouped = collections.defaultdict(list)
    for a in alerts:
        grouped[a["category"]].append(a)

    by_category = {c: len(v) for c, v in grouped.items()}
    ordered = sorted(grouped, key=lambda c: (-by_category[c], c))

    return {
        "schema_version": "1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                 .replace(microsecond=0).isoformat(),
        "fixture": {
            "repo": REPO,
            "commit": COMMIT,
            "files_at_pinned_commit": PINNED_FILE_COUNT,
            "stripped_to_ground_truth": STRIPPED,
        },
        "scanners": [
            {
                "name": "bandit",
                "version": _tool_version(["bandit", "--version"]),
                "invocation": f"{' '.join(BANDIT_ARGV)}  (from {config.fixture.relative_to(config.root)})",
            },
        ],
        "totals": {
            "alerts": len(alerts),
            "by_scanner": dict(collections.Counter(
                a["rule"].split(":", 1)[0] for a in alerts)),
            "by_severity": {
                s: sum(1 for a in alerts if a["severity"] == s)
                for s in ("high", "medium", "low")
            },
            "by_category": {c: by_category[c] for c in ordered},
            "alert_id_collisions": collisions,
        },
        # Fixture categories the scanner produced no alert for. These are the
        # blind spots, and they are worth saying out loud.
        "coverage_gaps": [c for c in FIXTURE_CATEGORIES if c not in grouped],
        "categories": {
            c: {
                "cwe": CWE_BY_CATEGORY.get(c, []),
                "count": by_category[c],
                "alerts": grouped[c],
            }
            for c in ordered
        },
    }


def write_findings(findings: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(findings, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan the fixture with Bandit and write findings.json.")
    ap.add_argument("-o", "--out", type=Path, default=CONFIG.findings)
    args = ap.parse_args(argv)

    findings = build_findings(run_bandit(CONFIG))
    write_findings(findings, args.out)

    t = findings["totals"]
    print(f"wrote {args.out}")
    print(f"  {t['alerts']} alerts  {t['by_scanner']}")
    print(f"  severity: {t['by_severity']}")
    print(f"  categories: {t['by_category']}")
    if findings["coverage_gaps"]:
        print(f"  coverage gaps (no alerts): {', '.join(findings['coverage_gaps'])}")
    if t["alert_id_collisions"]:
        print(f"  WARNING: alert_id collisions: {t['alert_id_collisions']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
