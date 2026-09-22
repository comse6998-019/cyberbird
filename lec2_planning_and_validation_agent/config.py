"""One place for every path, limit and default the run depends on.

    from lec2_planning_and_validation_agent.config import CONFIG, Config

    CONFIG.fixture          # demo/app
    CONFIG.search_max_hits  # 50

Frozen, so nothing mutates it mid-run and two components cannot disagree about
what the budget is. Variants come from `dataclasses.replace`, which keeps the
original intact:

    slow = replace(CONFIG, model="ollama:llama3.1", budget=10)

Paths are properties derived from `root` rather than stored, so overriding
`root` in a test moves every path with it and none can drift out of step.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

# demo/ — this file lives at demo/lec2_planning_and_validation_agent/config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    """Everything tunable about a run."""

    # where things live
    root: Path = _PROJECT_ROOT

    # the model
    model: str = "ollama:qwen3.8"
    seed: int = 0
    temperature: float = 0.0
    stream: bool = True      # show the response forming instead of a silent gap
    reasoning: bool = True   # ask a reasoning model to emit its thinking

    # limits the runtime enforces
    budget: int = 40            # model calls per run: the failsafe, not a target
    max_attempts: int = 2       # V4's outer loop
    no_progress_steps: int = 3  # consecutive steps without a state change

    # tool limits, to protect the context window rather than the disk
    read_max_bytes: int = 200_000
    search_max_hits: int = 50
    default_glob: str = "**/*.py"

    # OWASP Benchmark ships each weakness as a vulnerable case AND a safe
    # twin, so the correct patch for any case sits in a sibling file. Leaving
    # them in the workspace lets an agent grep for the answer instead of
    # reasoning about the code. True prunes every case but the flagged one.
    isolate_case: bool = True

    # workspace and scanning
    workspace_prefix: str = "mini-swe-agent-"
    scan_timeout: int = 120

    # the lecture's worked example
    worked_example: str = "bc284c6b"

    # derived paths
    @property
    def fixture(self) -> Path:
        """The pinned fixture. Never written to."""
        return self.root / "app"

    @property
    def rules_dir(self) -> Path:
        return self.root / "intake" / "rules"

    @property
    def findings(self) -> Path:
        return self.root / "findings.json"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def ground_truth(self) -> Path:
        """Named so it can be asserted unreachable, never so it can be read."""
        return self.root / "ground-truth"

    # variants
    def with_(self, **overrides) -> "Config":
        """A copy with fields replaced. The original is untouched."""
        return replace(self, **overrides)

    @classmethod
    def from_args(cls, args, base: "Config | None" = None) -> "Config":
        """Overlay argparse values, ignoring any the user did not supply."""
        base = base or CONFIG
        supplied = {name: getattr(args, name)
                    for name in ("model", "seed", "budget")
                    if getattr(args, name, None) is not None}
        return base.with_(**supplied)


# The singleton. Import this; pass it where a component needs to be configurable.
CONFIG = Config()
