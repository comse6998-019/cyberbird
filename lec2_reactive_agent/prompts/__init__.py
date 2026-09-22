"""What each role is told.

The prompts themselves are plain text files in this directory, one per role:

    controller_system.md    the controller's standing instructions
    controller_task.md      the alert, handed over as the task
    rejected_feedback.md    what the runtime says when checks fail

Kept as files rather than string literals so the wording can be edited, diffed
and reviewed on its own. A prompt change is a change to the agent's behaviour
and should show up in a diff as one, not buried among control flow.

They are read once, at import. A missing file fails immediately rather than
halfway through a run.
"""
from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
SUFFIX = ".md"


def available() -> list[str]:
    """Every prompt in this directory, by stem."""
    return sorted(p.stem for p in PROMPTS_DIR.glob(f"*{SUFFIX}"))


def load(name: str) -> str:
    """Read one prompt by stem. Raises if it is missing."""
    path = PROMPTS_DIR / f"{name}{SUFFIX}"
    if not path.is_file():
        raise FileNotFoundError(
            f"no prompt {name!r} in {PROMPTS_DIR}; have: {', '.join(available())}")
    return path.read_text(encoding="utf-8")


CONTROLLER_SYSTEM = load("controller_system")
CONTROLLER_TASK = load("controller_task")
REJECTED_FEEDBACK = load("rejected_feedback")


def controller_task(alert: dict) -> str:
    """The alert, rendered as the opening task."""
    scanner, _, rule = alert["rule"].partition(":")
    return CONTROLLER_TASK.format(scanner=scanner, rule=rule, **{
        k: alert[k] for k in ("file", "line", "severity", "message")})


def rejected_feedback(reasons: list[str]) -> str:
    """The runtime's failed checks, rendered for the controller to act on."""
    return REJECTED_FEEDBACK.format(
        reasons="\n".join(f"  - {r}" for r in reasons))
