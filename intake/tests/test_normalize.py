"""Unit tests for the pure normalization layer.

These run against small literal records, never against a live scanner, so the
contract is pinned independently of the fixture or the scanner versions.
"""
import pytest
from intake import normalize as n


# rule ids
def test_bandit_rule_id_is_scanner_prefixed():
    assert n.canonical_rule_id("bandit", "B608") == "bandit:B608"


def test_semgrep_rule_id_strips_local_config_path():
    # Scanning against vendored YAML makes semgrep prefix check_id with the
    # file path it loaded the rule from; the canonical id is what the registry
    # would have emitted.
    raw = "intake.rules.python.lang.security.use-defused-xml-parse.use-defused-xml-parse"
    assert n.canonical_rule_id("semgrep", raw) == (
        "semgrep:python.lang.security.use-defused-xml-parse.use-defused-xml-parse"
    )


def test_semgrep_rule_id_strips_prefix_for_any_namespace():
    # Vendored rule files carry registry ids that already begin with their own
    # namespace (python.*, generic.*), so the only added prefix is the config
    # directory. Verified against the real scan output.
    raw = "intake.rules.generic.nginx.security.header-injection.header-injection"
    assert n.canonical_rule_id("semgrep", raw) == (
        "semgrep:generic.nginx.security.header-injection.header-injection"
    )


# severity
@pytest.mark.parametrize("raw,want", [("HIGH", "high"), ("MEDIUM", "medium"), ("LOW", "low")])
def test_bandit_severity(raw, want):
    assert n.canonical_severity("bandit", raw) == want


@pytest.mark.parametrize("raw,want", [("ERROR", "high"), ("WARNING", "medium"), ("INFO", "low")])
def test_semgrep_severity(raw, want):
    assert n.canonical_severity("semgrep", raw) == want


# identity
def test_alert_id_is_deterministic():
    a = n.alert_id("bandit:B608", "testcode/BenchmarkTest00283.py", 46, 8)
    b = n.alert_id("bandit:B608", "testcode/BenchmarkTest00283.py", 46, 8)
    assert a == b and len(a) == 8


def test_alert_id_distinguishes_lines():
    a = n.alert_id("bandit:B608", "f.py", 46, 8)
    b = n.alert_id("bandit:B608", "f.py", 47, 8)
    assert a != b


# categories
def test_cwe_maps_to_fixture_category():
    assert n.categorize(89, "bandit:B608") == "sqli"
    assert n.categorize(643, "semgrep:whatever") == "xpathi"


def test_generic_cwe20_xml_cluster_resolves_to_xxe():
    # Bandit files every XML parser test under CWE-20 (Improper Input
    # Validation), which is too generic to categorize on. The rule id decides.
    for rule in ("B405", "B314", "B408", "B406", "B317", "B318"):
        assert n.categorize(20, f"bandit:{rule}") == "xxe"


def test_overloaded_cwe78_splits_by_rule():
    # Bandit uses CWE-78 for both subprocess use and eval/exec, which are
    # different weaknesses in the fixture's vocabulary.
    assert n.categorize(78, "bandit:B602") == "cmdi"
    assert n.categorize(78, "bandit:B102") == "codeinj"


def test_unmapped_cwe_falls_through_to_other():
    assert n.categorize(93, "semgrep:crlf") == "other"
    assert n.categorize(None, "semgrep:nocwe") == "other"


# record shape
def test_normalize_bandit_record_has_exactly_the_contract_fields():
    doc = {"results": [{
        "test_id": "B608",
        "filename": "fixture/testcode/BenchmarkTest00283.py",
        "line_number": 46,
        "col_offset": 8,
        "issue_severity": "MEDIUM",
        "issue_text": "Possible SQL injection vector through\n  string-based query construction.",
        "issue_cwe": {"id": 89},
    }]}
    (alert,) = n.normalize_bandit(doc)
    assert set(alert) == {"alert_id", "rule", "file", "line", "message", "severity", "category"}
    assert alert["rule"] == "bandit:B608"
    assert alert["file"] == "testcode/BenchmarkTest00283.py"   # fixture/ prefix stripped
    assert alert["line"] == 46
    assert alert["severity"] == "medium"
    assert alert["category"] == "sqli"
    assert "\n" not in alert["message"]                        # collapsed to one line
    assert alert["message"].endswith("string-based query construction.")


def test_normalize_semgrep_record():
    doc = {"results": [{
        "check_id": "intake.rules.python.lang.security.audit.dangerous-system-call",
        "path": "fixture/testcode/BenchmarkTest00001.py",
        "start": {"line": 55, "col": 11},
        "extra": {
            "severity": "ERROR",
            "message": "Found dynamic   content in os.system",
            "metadata": {"cwe": ["CWE-78: Improper Neutralization of Special Elements"]},
        },
    }]}
    (alert,) = n.normalize_semgrep(doc)
    assert alert["rule"] == "semgrep:python.lang.security.audit.dangerous-system-call"
    assert alert["file"] == "testcode/BenchmarkTest00001.py"
    assert alert["severity"] == "high"
    assert alert["category"] == "cmdi"
    assert alert["message"] == "Found dynamic content in os.system"


def test_upstream_mislabelled_sql_rule_is_corrected():
    # Published as CWE-704 (Incorrect Type Conversion) but it is a SQLi rule.
    rule = "semgrep:python.flask.security.injection.tainted-sql-string.tainted-sql-string"
    assert n.categorize(704, rule) == "sqli"


def test_weaknesses_outside_the_fixture_vocabulary_stay_in_other():
    assert n.categorize(93, "semgrep:python.django.security.injection."
                            "request-data-write.request-data-write") == "other"
    assert n.categorize(259, "bandit:B106") == "other"
