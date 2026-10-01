"""The `cyberbird` command: each agent is a subcommand that forwards to argparse."""
import json
import pathlib
import subprocess
import sys

from click.testing import CliRunner

from cyberbird.cli import cli
from cyberbird.plan_and_validation import cli as alerts
from cyberbird.plan_and_validation import trajectory
from cyberbird.reactive import scan

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
EXAMPLE_TRACE = ROOT / "lectures" / "lec02" / "runs" / "reactive-bc284c6b.jsonl"
# The installed console script, next to this interpreter.
SCRIPT = pathlib.Path(sys.executable).parent / "cyberbird"


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_top_level_help_lists_the_agents():
    result = run("--help")
    assert result.exit_code == 0
    for name in ("reactive", "plan-and-validation"):
        assert name in result.output


def test_agent_help_is_the_agents_own_argparse_help():
    # Through the real script: argparse names itself from how Python was
    # launched, which pytest's own process would get wrong.
    result = subprocess.run([SCRIPT, "reactive", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.startswith("usage: cyberbird reactive")
    assert "--alert-id" in result.stdout


def test_bad_flag_exits_non_zero():
    assert run("reactive", "--no-such-flag").exit_code == 2


def test_unknown_agent_exits_non_zero():
    assert run("no-such-agent").exit_code == 2


# The tools below left the `cyberbird` menu; each still runs as `python -m`.
def test_alerts_list_reports_the_queue(capsys, scanned):
    assert alerts.main(["--list"]) == 0
    assert capsys.readouterr().out.startswith("536 alerts")


def test_alerts_work_from_another_directory(tmp_path, monkeypatch, capsys, scanned):
    monkeypatch.chdir(tmp_path)
    assert alerts.main(["--list"]) == 0
    assert capsys.readouterr().out.startswith("536 alerts")


def test_trace_renders_the_lecture_example(capsys):
    assert trajectory.main([str(EXAMPLE_TRACE), "--format", "summary"]) == 0
    assert "model calls" in capsys.readouterr().out


def test_scan_honours_its_output_flag(tmp_path):
    out = tmp_path / "findings.json"
    assert scan.main(["-o", str(out)]) == 0
    assert json.loads(out.read_text())["totals"]["alerts"] == 536
