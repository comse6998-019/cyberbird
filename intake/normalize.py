"""Normalize Bandit and Semgrep output into one alert contract.

Every function here is pure: parsed JSON in, plain dicts out. Nothing shells
out and nothing touches disk, so the contract is testable without a scanner
installed and stays pinned across scanner versions.

The contract is five fields, plus two derived values:

    rule      scanner-prefixed rule id, e.g. "bandit:B608"
    file      path relative to the fixture root
    line      1-based line number
    message   single-line description
    severity  high | medium | low

    alert_id  stable 8-hex identity over (rule, file, line, col)
    category  derived from the CWE and the rule id
"""
from __future__ import annotations

import hashlib
import re

# Semgrep prefixes each rule id with the config path it loaded the rule from.
# We scan against vendored YAML under intake/rules/, so registry ids arrive
# carrying this prefix; stripping it recovers the published id.
LOCAL_CONFIG_PREFIX = "intake.rules."

FIXTURE_ROOT = "app/"

_SEVERITY = {
    "bandit":  {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"},
    "semgrep": {"ERROR": "high", "WARNING": "medium", "INFO": "low"},
}

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

# Two cases where the scanner's CWE is too coarse to categorize on, so the
# rule id decides instead. Both are Bandit:
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

    # Upstream registry metadata is simply wrong here: this rule detects SQL
    # built from tainted input (its own name and OWASP tag say Injection) but
    # is published as CWE-704, Incorrect Type Conversion. Verified by reading
    # the flagged line: it is the same f-string SQL pattern as B608.
    "semgrep:python.flask.security.injection.tainted-sql-string.tainted-sql-string": "sqli",
}

# Deliberately NOT mapped, and therefore reported as "other":
#   CWE-93  CRLF injection      (semgrep request-data-write)
#   CWE-259 hardcoded password  (bandit B106)
# Neither weakness has a counterpart in the fixture's 14 categories. Inventing
# a nearest fit would misreport them, so they surface in "other" for a human.

UNCATEGORIZED = "other"


def canonical_rule_id(scanner: str, raw_id: str) -> str:
    rule = raw_id
    if scanner == "semgrep" and rule.startswith(LOCAL_CONFIG_PREFIX):
        rule = rule[len(LOCAL_CONFIG_PREFIX):]
    return f"{scanner}:{rule}"


def canonical_severity(scanner: str, raw_severity: str) -> str:
    return _SEVERITY[scanner].get(str(raw_severity).upper(), "low")


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
    return path[len(FIXTURE_ROOT):] if path.startswith(FIXTURE_ROOT) else path


def _record(scanner, raw_rule, path, line, col, severity, message, cwe) -> dict:
    rule = canonical_rule_id(scanner, raw_rule)
    file = _relative(path)
    return {
        "alert_id": alert_id(rule, file, line, col),
        "rule": rule,
        "file": file,
        "line": int(line),
        "message": _one_line(message),
        "severity": canonical_severity(scanner, severity),
        "category": categorize(cwe, rule),
    }


def normalize_bandit(doc: dict) -> list[dict]:
    return [
        _record("bandit", r["test_id"], r["filename"], r["line_number"],
                r.get("col_offset", 0), r["issue_severity"], r["issue_text"],
                (r.get("issue_cwe") or {}).get("id"))
        for r in doc.get("results", [])
    ]


_CWE_NUM = re.compile(r"CWE-(\d+)")


def _semgrep_cwe(extra: dict):
    for entry in extra.get("metadata", {}).get("cwe", []) or []:
        match = _CWE_NUM.search(entry)
        if match:
            return int(match.group(1))
    return None


def normalize_semgrep(doc: dict) -> list[dict]:
    out = []
    for r in doc.get("results", []):
        extra = r.get("extra", {})
        out.append(_record("semgrep", r["check_id"], r["path"],
                           r["start"]["line"], r["start"].get("col", 0),
                           extra.get("severity", "INFO"),
                           extra.get("message", ""), _semgrep_cwe(extra)))
    return out
