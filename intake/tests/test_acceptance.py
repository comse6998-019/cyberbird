"""Acceptance checks against the real, built findings.json.

Unlike test_normalize.py these depend on the fixture and the scan actually
having run. They pin the worked example the lecture notes use throughout.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
FINDINGS = ROOT / "data" / "findings.json"

pytestmark = pytest.mark.skipif(
    not FINDINGS.exists(), reason="run `python -m intake.build_findings` first")


@pytest.fixture(scope="module")
def doc():
    return json.loads(FINDINGS.read_text())


def test_worked_example_is_present(doc):
    """Bandit B608 at testcode/BenchmarkTest00283.py:46, under sqli.

    The notes warn that the fixture's bundled Bandit report does not contain
    the selected case IDs, so this proves the fresh scan really covers it.
    """
    sqli = doc["categories"]["sqli"]["alerts"]
    hit = [a for a in sqli
           if a["rule"] == "bandit:B608"
           and a["file"] == "testcode/BenchmarkTest00283.py"
           and a["line"] == 46]
    assert len(hit) == 1, "the lecture's worked example is missing from findings.json"
    assert hit[0]["severity"] == "medium"


def test_fixture_is_pinned(doc):
    assert doc["fixture"]["commit"] == "f1291485808b66e20ddb6b01b10dc71b3df8c8ba"


def test_answer_key_is_not_reachable_from_the_fixture():
    for name in ("expectedresults-0.1.csv",
                 "results/BenchmarkPython-Bandit.sarif",
                 "results/Benchmark-Bearer-v1.51.1.json"):
        assert not (ROOT / "fixture" / name).exists(), f"{name} still readable in fixture/"
        assert (ROOT / "data" / "ground-truth" / pathlib.Path(name).name).exists()


def test_every_alert_carries_the_full_contract(doc):
    fields = {"alert_id", "rule", "file", "line", "message", "severity", "category"}
    for cat in doc["categories"].values():
        for a in cat["alerts"]:
            assert set(a) == fields
            assert a["severity"] in ("high", "medium", "low")
            assert isinstance(a["line"], int) and a["line"] > 0
            assert "\n" not in a["message"]


def test_alert_ids_are_unique(doc):
    assert doc["totals"]["alert_id_collisions"] == []


def test_counts_are_internally_consistent(doc):
    total = sum(c["count"] for c in doc["categories"].values())
    assert total == doc["totals"]["alerts"]
    for name, cat in doc["categories"].items():
        assert cat["count"] == len(cat["alerts"])


def test_builder_writes_where_it_is_told(tmp_path):
    """`main` takes its own argv, so a wrapper can forward arguments to it."""
    from intake import build_findings

    out = tmp_path / "findings.json"
    assert build_findings.main(["-o", str(out)]) == 0
    assert json.loads(out.read_text())["totals"]["alerts"] == 720
