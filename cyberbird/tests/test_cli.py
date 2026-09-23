"""The `cyberbird` command: lectures are groups, versions forward to argparse."""
import json
import pathlib
import subprocess
import sys

from click.testing import CliRunner

from cyberbird.cli import cli

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
EXAMPLE_TRACE = ROOT / "lectures" / "lec02" / "runs" / "v1-bc284c6b.jsonl"
# The installed console script, next to this interpreter.
SCRIPT = pathlib.Path(sys.executable).parent / "cyberbird"


def run(*args):
    return CliRunner().invoke(cli, list(args))


def test_top_level_help_lists_commands():
    result = run("--help")
    assert result.exit_code == 0
    for name in ("lec02", "alerts", "trace", "intake"):
        assert name in result.output


def test_lecture_help_lists_its_agents():
    result = run("lec02", "--help")
    assert result.exit_code == 0
    assert "reactive" in result.output
    assert "plan-and-validation" in result.output


def test_version_help_is_the_versions_own_argparse_help():
    # Through the real script: argparse names itself from how Python was
    # launched, which pytest's own process would get wrong.
    result = subprocess.run([SCRIPT, "lec02", "reactive", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.startswith("usage: cyberbird lec02 reactive")
    assert "--alert-id" in result.stdout


def test_alerts_list_reports_the_queue():
    result = run("alerts", "--list")
    assert result.exit_code == 0
    assert result.output.startswith("720 alerts")


def test_alerts_work_from_another_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = run("alerts", "--list")
    assert result.exit_code == 0
    assert result.output.startswith("720 alerts")


def test_trace_renders_the_lecture_example():
    result = run("trace", str(EXAMPLE_TRACE), "--format", "summary")
    assert result.exit_code == 0
    assert "model calls" in result.output


def test_intake_honours_its_output_flag(tmp_path):
    out = tmp_path / "findings.json"
    result = run("intake", "-o", str(out))
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["totals"]["alerts"] == 720


def test_bad_flag_exits_non_zero():
    assert run("lec02", "reactive", "--no-such-flag").exit_code == 2


def test_unknown_lecture_exits_non_zero():
    assert run("lec99").exit_code == 2
