"""The scan and normalize nodes: every run starts by scanning the fixture.

`normalize` is exercised on the report from one shared scan (the `scanned`
fixture), so these tests call no model.
"""
import importlib

import pytest

AGENTS = ["reactive", "plan_and_validation"]


def _mod(agent, name):
    return importlib.import_module(f"cyberbird.{agent}.{name}")


def _dag(agent, tmp_path, opened):
    trace = _mod(agent, "trace").Trace("test", tmp_path / "t.jsonl")
    config = _mod(agent, "config").CONFIG
    dag = _mod(agent, "graph").AgentDAG(trace, config,
                                        open_workspace=lambda a: opened.append(a) or tmp_path)
    return dag, config, trace


@pytest.mark.parametrize("agent", AGENTS)
def test_every_run_starts_scan_then_normalize(agent, tmp_path):
    trace = _mod(agent, "trace").Trace("test", tmp_path / "t.jsonl")
    edges = {(e.source, e.target) for e in _mod(agent, "graph").build_graph(trace).get_graph().edges}
    assert ("__start__", "scan") in edges
    assert ("scan", "normalize") in edges


@pytest.mark.parametrize("agent", AGENTS)
def test_normalize_picks_the_alert_and_asks_for_a_workspace(agent, tmp_path, monkeypatch, scanned):
    opened = []
    dag, config, trace = _dag(agent, tmp_path, opened)
    # findings.json is rewritten by the node; keep the real one untouched.
    monkeypatch.setattr(type(config), "findings", property(lambda self: tmp_path / "findings.json"))
    state = {"selector": {"alert_id": "bc284c6b"}, "scan_report": str(config.scan_report)}

    update = dag.normalize(state)
    trace.terminal("error")

    assert update["alert"]["rule"] == "bandit:B608"
    assert update["workspace"] == str(tmp_path)
    assert [a["alert_id"] for a in opened] == ["bc284c6b"]
    assert update["messages"], "the opening conversation arrives with the alert"
    assert (tmp_path / "findings.json").is_file()


@pytest.mark.parametrize("agent", AGENTS)
def test_unknown_alert_ends_the_run_before_any_workspace(agent, tmp_path, monkeypatch, scanned):
    opened = []
    dag, config, trace = _dag(agent, tmp_path, opened)
    monkeypatch.setattr(type(config), "findings", property(lambda self: tmp_path / "findings.json"))
    state = {"selector": {"alert_id": "nope0000"}, "scan_report": str(config.scan_report)}

    update = dag.normalize(state)
    trace.terminal("error")

    assert update == {"status": "error"}
    assert opened == []


@pytest.mark.parametrize("agent", AGENTS)
def test_runs_are_named_from_the_selector(agent):
    run_name = _mod(agent, "cli").run_name
    assert run_name("a", {"alert_id": "bc284c6b"}) == "a-bc284c6b"
    assert run_name("a", {"location": "testcode/BenchmarkTest00283.py:46"}) == "a-BenchmarkTest00283-46"
