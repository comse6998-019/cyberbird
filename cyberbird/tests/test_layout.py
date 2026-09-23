"""Both versions resolve the same repo layout, and the answers stay out of reach."""
import pathlib
import subprocess

import pytest

from cyberbird.plan_and_validation.config import CONFIG as PLAN_AND_VALIDATION
from cyberbird.reactive.config import CONFIG as REACTIVE


@pytest.mark.parametrize("config", [REACTIVE, PLAN_AND_VALIDATION],
                         ids=["reactive", "plan_and_validation"])
def test_paths_resolve(config):
    assert (config.fixture / "testcode").is_dir()
    assert config.findings.is_file()
    assert config.ground_truth.is_dir()
    assert config.rules_dir.is_dir()


@pytest.mark.parametrize("config", [REACTIVE, PLAN_AND_VALIDATION],
                         ids=["reactive", "plan_and_validation"])
def test_ground_truth_is_outside_the_fixture(config):
    # The agent's workspace is a copy of the fixture; answers inside it leak.
    assert not config.ground_truth.resolve().is_relative_to(config.fixture.resolve())


def _ignored(path):
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    return subprocess.run(["git", "-C", root, "check-ignore", "-q", path]).returncode == 0


def test_new_runs_are_ignored_but_lecture_examples_are_not():
    assert _ignored("runs/v1-new.jsonl")
    assert not _ignored("lectures/lec03/runs/v4-example.jsonl")
