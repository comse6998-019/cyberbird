"""What each role is told.

One Markdown file per prompt, so a change to what a model is told shows up in
a diff on its own, apart from any control flow:

    controller_system.md     the controller's standing instructions and its tools
    planner_system.md        the planner's standing instructions
    alert.md                 one alert, as every task shows it
    planner_task.md          the alert, as something to plan
    replan_task.md           the stalled plan, why it stalled, and a call to revise
    plan_message.md          the plan, handed to the controller
    revised_plan_message.md  the same, after a revision

System prompts are used verbatim. The others are `str.format` templates, so
they must not contain literal braces other than their placeholders.

Every file is read at import: a missing one fails at once, not mid-run.
"""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load(name: str) -> str:
    path = _DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt {name!r} in {_DIR}")
    return path.read_text(encoding="utf-8")


CONTROLLER_SYSTEM = load("controller_system")
PLANNER_SYSTEM = load("planner_system")
_ALERT = load("alert")
_PLANNER_TASK = load("planner_task")
_REPLAN_TASK = load("replan_task")
_PLAN_MESSAGE = load("plan_message")
_REVISED_PLAN_MESSAGE = load("revised_plan_message")


def alert_text(alert: dict) -> str:
    return _ALERT.format(**alert)


def planner_task(alert: dict) -> str:
    return _PLANNER_TASK.format(alert=alert_text(alert))


def render_steps(steps: list[dict]) -> str:
    """A plan, numbered, each step with what shows it is done."""
    return "\n".join(f"{i}. {s['goal']}\n   Done when: {s['evidence']}"
                     for i, s in enumerate(steps, 1)) or "(no steps)"


def replan_task(alert: dict, plan: list[dict], reason: str, last_result: str) -> str:
    return _REPLAN_TASK.format(alert=alert_text(alert), plan=render_steps(plan),
                               reason=reason, last_result=last_result)


def plan_message(steps: list[dict], revised: bool) -> str:
    """The plan as the controller receives it: input from someone else."""
    template = _REVISED_PLAN_MESSAGE if revised else _PLAN_MESSAGE
    return template.format(steps=render_steps(steps))
