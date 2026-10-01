"""Every path, limit and default the run depends on. Frozen; vary with `with_`."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

# the repo root: this file is <root>/cyberbird/event_driven/config.py
_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    root: Path = _ROOT

    # the model: same family as the graph agent's ollama:qwen3.8, hosted
    model: str = "openrouter:qwen/qwen3.8-flash"
    seed: int = 0
    temperature: float = 0.0
    reasoning: dict | None = None   # e.g. {"effort": "low"}; None leaves it to the model
    # OpenRouter reserves credit for the model's whole output window (131k here)
    # unless told otherwise; a capped key then refuses the call outright.
    max_tokens: int = 4096

    # limits the runtime enforces
    budget: int = 40
    no_progress_steps: int = 3
    validators: int = 3

    @property
    def fixture(self) -> Path:
        return self.root / "fixtures" / "owasp-benchmark-python" / "src"

    # the lecture this agent belongs to: every run's diagram is also saved there
    lecture: str = "lec03"

    @property
    def runs_dir(self) -> Path:
        """Every run's trace and diagram, timestamped. Git-ignored."""
        return self.root / "runs"

    @property
    def lecture_runs_dir(self) -> Path:
        """The lecture's copy of the latest diagram per alert. Tracked in git."""
        return self.root / "lectures" / self.lecture / "runs"

    def with_(self, **overrides) -> "Config":
        return replace(self, **overrides)


CONFIG = Config()
