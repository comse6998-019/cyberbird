"""What each role is told.

The prompts themselves are plain text files in this directory, one per role:

    controller_system.md     the controller's standing instructions
    controller_task.md       the alert, handed over as the task
    rejected_feedback.md     what the runtime says when checks fail
    planner_system.md        the planner's standing instructions
    planner_task.md          the alert, handed over as something to plan
    replan_task.md           the plan, the stall, and the call to revise
    plan_message.md          the plan, handed to the controller as its input
    revised_plan_message.md  the same, after a revision
    validator_system.md      the validator's standing instructions
    validator_task.md        the patch, the checks and the trajectory to judge

Three roles, three system prompts. Each is bound only to its own tools, so the
controller cannot decide acceptance and the validator cannot edit the tree.

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
PLANNER_SYSTEM = load("planner_system")
PLANNER_TASK = load("planner_task")
REPLAN_TASK = load("replan_task")
PLAN_MESSAGE = load("plan_message")
REVISED_PLAN_MESSAGE = load("revised_plan_message")
VALIDATOR_SYSTEM = load("validator_system")
VALIDATOR_TASK = load("validator_task")


def _alert_fields(alert: dict) -> dict:
    """The alert, flattened into the names every task template uses.

    A template that wants fewer of them simply omits them; str.format ignores
    keyword arguments it was not asked for.
    """
    scanner, _, rule = alert["rule"].partition(":")
    return {"scanner": scanner, "rule": rule,
            **{k: alert[k] for k in ("file", "line", "severity", "message")}}


def controller_task(alert: dict) -> str:
    """The alert, rendered as the opening task."""
    return CONTROLLER_TASK.format(**_alert_fields(alert))


def rejected_feedback(reasons: list[str]) -> str:
    """The runtime's failed checks, rendered for the controller to act on."""
    return REJECTED_FEEDBACK.format(
        reasons="\n".join(f"  - {r}" for r in reasons))


def planner_task(alert: dict) -> str:
    """The alert, rendered as something to plan."""
    return PLANNER_TASK.format(**_alert_fields(alert))


def render_steps(steps: list[dict]) -> str:
    """A plan, numbered, one step per line with its completion condition."""
    lines = []
    for step in steps or []:
        lines.append(f"  {step['id']}. {step['goal']}")
        if step.get("evidence"):
            lines.append(f"     evidence: {step['evidence']}")
    return "\n".join(lines) if lines else "  (no steps)"


def replan_task(alert: dict, plan: list[dict], failed_step: dict) -> str:
    """The plan, why it stopped working, and the call to revise it."""
    failed_step = failed_step or {}
    return REPLAN_TASK.format(
        plan=render_steps(plan),
        reason=failed_step.get("reason", "The work stopped making progress."),
        last_result=_indent(failed_step.get("last_result", "(not recorded)")),
        **_alert_fields(alert))


def plan_message(steps: list[dict], revised: bool = False) -> str:
    """The plan as the controller receives it.

    A HumanMessage, not the planner's own reply: the planner is a different
    actor, and its output arrives to the executor as input.
    """
    template = REVISED_PLAN_MESSAGE if revised else PLAN_MESSAGE
    return template.format(steps=render_steps(steps))


def render_trajectory(observations: list[dict], width: int = 110) -> str:
    """What the runtime actually did, for the validator to read.

    The observations, never the conversation. A model's account of its own work
    is exactly what the validator must not be asked to trust.
    """
    lines = []
    for n, obs in enumerate(observations or [], start=1):
        args = ", ".join(f"{k}={_clip(repr(v), 48)}"
                         for k, v in (obs.get("args") or {}).items())
        # The result already says what went wrong — every refusal message is
        # self-describing — so labelling it "refused" as well produced
        # "refused: refused: ...". The marker goes in front of the call
        # instead, where a reader scanning for failures will find it.
        marker = "  " if obs.get("ok") else "! "
        result = _clip(" ".join((obs.get("result") or "").split()), width)
        lines.append(f"  {n}. {marker}{obs.get('tool')}({args}) -> {result}")
    return "\n".join(lines) if lines else "  (no tool calls)"


def validator_task(alert: dict, patch: str | None, checks: dict | None,
                   observations: list[dict]) -> str:
    """The candidate patch, the mechanical checks, and the trajectory."""
    checks = checks or {}
    rendered = "\n".join(f"  {name}: {value}" for name, value in checks.items()
                          if name != "reasons") or "  (none run)"
    return VALIDATOR_TASK.format(
        patch=patch or "  (no patch produced)",
        checks=rendered,
        trajectory=render_trajectory(observations),
        **_alert_fields(alert))


def _clip(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    clipped = text[:width - 1] + "\u2026"
    # Clipping a repr drops its closing quote, which leaves the argument list
    # looking unbalanced to whatever reads it next. Put the quote back.
    if text[0] in "'\"":
        clipped += text[0]
    return clipped


def _indent(text: str) -> str:
    return "\n".join(f"  {line}" for line in str(text).splitlines()) or "  (empty)"
