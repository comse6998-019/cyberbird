"""Unit tests for the pure normalization layer.

These run against small literal records, never against a live scanner, so the
contract is pinned independently of the fixture or the scanner versions.
"""
import pytest

from cyberbird.plan_and_validation import scan as plan_and_validation_scan
from cyberbird.reactive import scan as reactive_scan


# Each agent owns a copy of the scanner; both must honour the same contract.
@pytest.fixture(params=[reactive_scan, plan_and_validation_scan],
                ids=["reactive", "plan_and_validation"])
def n(request):
    return request.param


# rule ids
def test_bandit_rule_id_is_scanner_prefixed(n):
    assert n.canonical_rule_id("bandit", "B608") == "bandit:B608"


# severity
@pytest.mark.parametrize("raw,want", [("HIGH", "high"), ("MEDIUM", "medium"), ("LOW", "low")])
def test_bandit_severity(n, raw, want):
    assert n.canonical_severity(raw) == want


# identity
def test_alert_id_is_deterministic(n):
    a = n.alert_id("bandit:B608", "testcode/BenchmarkTest00283.py", 46, 8)
    b = n.alert_id("bandit:B608", "testcode/BenchmarkTest00283.py", 46, 8)
    assert a == b and len(a) == 8


def test_alert_id_distinguishes_lines(n):
    a = n.alert_id("bandit:B608", "f.py", 46, 8)
    b = n.alert_id("bandit:B608", "f.py", 47, 8)
    assert a != b


# categories
def test_cwe_maps_to_fixture_category(n):
    assert n.categorize(89, "bandit:B608") == "sqli"
    assert n.categorize(643, "bandit:whatever") == "xpathi"


def test_generic_cwe20_xml_cluster_resolves_to_xxe(n):
    # Bandit files every XML parser test under CWE-20 (Improper Input
    # Validation), which is too generic to categorize on. The rule id decides.
    for rule in ("B405", "B314", "B408", "B406", "B317", "B318"):
        assert n.categorize(20, f"bandit:{rule}") == "xxe"


def test_overloaded_cwe78_splits_by_rule(n):
    # Bandit uses CWE-78 for both subprocess use and eval/exec, which are
    # different weaknesses in the fixture's vocabulary.
    assert n.categorize(78, "bandit:B602") == "cmdi"
    assert n.categorize(78, "bandit:B102") == "codeinj"


def test_unmapped_cwe_falls_through_to_other(n):
    assert n.categorize(93, "bandit:crlf") == "other"
    assert n.categorize(None, "bandit:nocwe") == "other"


# record shape
def test_normalize_bandit_record_has_exactly_the_contract_fields(n):
    doc = {"results": [{
        "test_id": "B608",
        "filename": "./testcode/BenchmarkTest00283.py",
        "line_number": 46,
        "col_offset": 8,
        "issue_severity": "MEDIUM",
        "issue_text": "Possible SQL injection vector through\n  string-based query construction.",
        "issue_cwe": {"id": 89},
    }]}
    (alert,) = n.normalize_bandit(doc)
    assert set(alert) == {"alert_id", "rule", "file", "line", "message", "severity", "category"}
    assert alert["rule"] == "bandit:B608"
    assert alert["file"] == "testcode/BenchmarkTest00283.py"   # leading ./ stripped
    assert alert["line"] == 46
    assert alert["severity"] == "medium"
    assert alert["category"] == "sqli"
    assert "\n" not in alert["message"]                        # collapsed to one line
    assert alert["message"].endswith("string-based query construction.")


def test_weaknesses_outside_the_fixture_vocabulary_stay_in_other(n):
    assert n.categorize(259, "bandit:B106") == "other"
