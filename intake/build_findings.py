"""Assemble findings.json from the raw scanner output.

Reads data/scans/bandit.json and data/scans/semgrep.json, normalizes both
into the one alert contract, groups by the fixture's category vocabulary, and
writes a single document with enough provenance to reproduce the run.

    python -m intake.build_findings

data/findings.json is derived and should never be hand-edited; re-run this
instead.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

from intake import normalize

ROOT = pathlib.Path(__file__).resolve().parent.parent

REPO = "https://github.com/OWASP-Benchmark/BenchmarkPython"
COMMIT = "f1291485808b66e20ddb6b01b10dc71b3df8c8ba"
PINNED_FILE_COUNT = 2540

# Moved to data/ground-truth/ so the agent cannot read the answers out of the fixture.
STRIPPED = [
    "expectedresults-0.1.csv",
    "results/BenchmarkPython-Bandit.sarif",
    "results/Benchmark-Bearer-v1.51.1.json",
]

RULESETS = [
    ("python.yml", "https://semgrep.dev/c/p/python"),
    ("owasp-top-ten.yml", "https://semgrep.dev/c/p/owasp-top-ten"),
]

# The fixture's full vocabulary. Categories with no alerts are reported as
# coverage gaps rather than silently omitted.
FIXTURE_CATEGORIES = sorted(set(normalize.CATEGORY_BY_CWE.values()))

CWE_BY_CATEGORY = collections.defaultdict(list)
for _cwe, _cat in sorted(normalize.CATEGORY_BY_CWE.items()):
    CWE_BY_CATEGORY[_cat].append(_cwe)


def _tool_version(argv: list[str]) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return (out.stdout + out.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    bandit_raw = json.loads((ROOT / "data/scans/bandit.json").read_text())
    semgrep_raw = json.loads((ROOT / "data/scans/semgrep.json").read_text())

    alerts = normalize.normalize_bandit(bandit_raw) + normalize.normalize_semgrep(semgrep_raw)
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
                "invocation": "bandit -r fixture/ -f json -o data/scans/bandit.json",
            },
            {
                "name": "semgrep",
                "version": _tool_version(["semgrep", "--version"]),
                "invocation": ("semgrep scan --config intake/rules/python.yml "
                               "--config intake/rules/owasp-top-ten.yml --json "
                               "--metrics=off fixture/"),
                "rulesets": [
                    {"file": f, "source": url,
                     "sha256": _sha256(ROOT / "intake/rules" / f)}
                    for f, url in RULESETS
                ],
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
        # Fixture categories that neither scanner produced a single alert for.
        # These are the blind spots, and they are worth saying out loud.
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(ROOT / "data" / "findings.json"))
    args = ap.parse_args(argv)

    doc = build()
    pathlib.Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")

    t = doc["totals"]
    print(f"wrote {args.out}")
    print(f"  {t['alerts']} alerts  {t['by_scanner']}")
    print(f"  severity: {t['by_severity']}")
    print(f"  categories: {t['by_category']}")
    if doc["coverage_gaps"]:
        print(f"  coverage gaps (no alerts): {', '.join(doc['coverage_gaps'])}")
    if t["alert_id_collisions"]:
        print(f"  WARNING: alert_id collisions: {t['alert_id_collisions']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
