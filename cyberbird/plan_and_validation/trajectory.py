"""Turn a recorded run into a sequence diagram.

The state graph shows what the agent *could* do. A trajectory shows what one run
actually did, in order, with who decided each step.

    python -m cyberbird.plan_and_validation.trajectory runs/v23-bc284c6b.jsonl
    python -m cyberbird.plan_and_validation.trajectory runs/v23-bc284c6b.jsonl --format mermaid

Five participants, matching the lecture's vocabulary:

    Planner      the model, proposing subgoals and revising them.
    Controller   the model, choosing one action at a time.
    Runtime      the dispatcher and Submit. Executes, refuses, checks, routes.
    Workspace    the pinned fixture this attempt may edit.
    Validator    the model, judging a patch it did not write.

Three of the five are the same model under different instructions, and keeping
them apart on the diagram is the point: a student should be able to see that the
thing which proposed the patch is not the thing which accepted it.

An arrow leaving Controller is a proposal. An arrow returning to it is an
observation the runtime produced. Nothing reaches Controller that the runtime did
not put there, which is the property the dispatcher exists to guarantee.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cyberbird.plan_and_validation.trace import read_events

# Which participant a model call belongs to. A role the diagram does not know
# lands on the controller, because an unattributed decision is still a decision.
ACTOR = {"planner": "P", "controller": "C", "validator": "V"}


def _args(args: dict, width: int = 40) -> str:
    text = ", ".join(f"{k}={v}" for k, v in args.items())
    text = text.replace("\n", " ").replace('"', "'")
    return text if len(text) <= width else text[:width - 1] + "…"


def _note(text: str, width: int = 60) -> str:
    """One line of Mermaid note text. Newlines and semicolons break the parser."""
    text = " ".join(str(text).split()).replace(";", ",").replace('"', "'")
    return text if len(text) <= width else text[:width - 1] + "…"


def to_mermaid(events: list[dict]) -> str:
    """A Mermaid sequenceDiagram of one run."""
    out = [
        "sequenceDiagram",
        "    autonumber",
        "    participant P as Planner<br/>(model plans)",
        "    participant C as Controller<br/>(model decides)",
        "    participant R as Runtime<br/>(executes, refuses, checks)",
        "    participant W as Workspace<br/>(pinned fixture)",
        "    participant V as Validator<br/>(model judges)",
        "",
    ]
    start = events[0]["ts"] if events else 0

    for event in events:
        kind = event["kind"]
        at = f"{event['ts'] - start:.0f}s"

        if kind == "model_call":
            usage = event.get("usage") or {}
            who = ACTOR.get(event.get("role", "controller"), "C")
            out.append(f"    Note over {who}: {at} · in={usage.get('input', 0)} "
                       f"out={usage.get('output', 0)}")
            if who == "C" and not (event.get("tool_calls") or []):
                out.append("    C->>R: no tool call — finished")

        elif kind == "state_change" and event.get("node") == "plan":
            steps = event.get("steps") or []
            revised = event.get("revised")
            # A dashed arrow for the revision, so the second plan cannot be
            # mistaken for the first one drawn twice.
            out.append(f"    {'P-->>C' if revised else 'P->>C'}: "
                       f"{'revised plan' if revised else 'plan'} — {len(steps)} steps")

        elif kind == "tool_request":
            out.append(f"    C->>R: {event['tool']}({_args(event.get('args', {}))})")
            out.append(f"    R->>W: {event['tool']}")

        elif kind == "tool_result":
            if event.get("ok"):
                out.append(f"    W-->>R: {event.get('chars', 0)} chars")
                out.append("    R-->>C: observation")
            else:
                out.append(f"    R-->>C: refused — {_note(event.get('reason', ''), 46)}")

        elif kind == "state_change" and event.get("checks"):
            checks = event["checks"]
            marks = " ".join(f"{name}={'✓' if checks[name] else '✗'}"
                             for name in ("applies", "touches_line", "rescan_clean"))
            out.append("    R->>W: rescan")
            out.append(f"    Note over R: {marks}")

        elif kind == "routing" and event["decision"] in ("accepted", "rejected"):
            out.append(f"    V-->>R: {event['decision']} — decided by {event.get('by')}")
            for reason in event.get("reasons") or []:
                out.append(f"    Note over V: {_note(reason)}")

        elif kind == "routing" and event.get("by") == "runtime":
            if event["decision"] == "plan":
                out.append(f"    R->>P: replan — {_note(event.get('reason', ''), 46)}")
            elif event["decision"] == "validate":
                out.append("    R->>V: candidate patch + the three checks")
            else:
                out.append(f"    Note over R: routed → {event['decision']} (runtime)")

        elif kind == "terminal":
            out.append(f"    Note over P,V: {event['status'].upper()}")

    return "\n".join(out)


def to_text(events: list[dict]) -> str:
    """The same trajectory as plain text, for reading in a terminal."""
    lines, start = [], (events[0]["ts"] if events else 0)
    for event in events:
        at = f"{event['ts'] - start:6.1f}s"
        kind = event["kind"]
        if kind == "model_call":
            usage = event.get("usage") or {}
            asked = ", ".join(event.get("tool_calls") or []) or "no tool call — finished"
            role = event.get("role", "controller")
            lines.append(f"{at}  MODEL     {role} decides: {asked}"
                         f"   (in={usage.get('input',0)} out={usage.get('output',0)})")
        elif kind == "routing" and event["decision"] in ("accepted", "rejected"):
            lines.append(f"{at}  VERDICT   {event['decision']}"
                         f"   decided by {event.get('by')}")
            for reason in event.get("reasons") or []:
                lines.append(f"{at}            - {reason}")
        elif kind == "routing":
            note = event.get("reason") or f"decided by {event.get('by')}"
            lines.append(f"{at}  ROUTE     → {event['decision']}   {note}")
        elif kind == "state_change" and event.get("node") == "plan":
            steps = event.get("steps") or []
            label = "PLAN ↻   " if event.get("revised") else "PLAN     "
            lines.append(f"{at}  {label} {len(steps)} steps"
                         + ("  (revised)" if event.get("revised") else ""))
            for step in steps:
                lines.append(f"{at}            {step['id']}. {step['goal']}")
                if step.get("evidence"):
                    lines.append(f"{at}               evidence: {step['evidence']}")
        elif kind == "tool_request":
            lines.append(f"{at}  RUNTIME   {event['tool']}({_args(event.get('args', {}), 56)})")
        elif kind == "tool_result":
            verdict = f"ok, {event.get('chars',0)} chars" if event.get("ok") \
                else f"REFUSED — {event.get('reason','')[:56]}"
            lines.append(f"{at}            {verdict}")
        elif kind == "state_change" and event.get("checks"):
            checks = event["checks"]
            lines.append(f"{at}  CHECK     " + "  ".join(
                f"{n}={checks[n]}" for n in ("applies", "touches_line", "rescan_clean")))
        elif kind == "terminal":
            lines.append(f"{at}  END       {event['status'].upper()}")
    return "\n".join(lines)


def summarise(events: list[dict]) -> str:
    calls = [e for e in events if e["kind"] == "model_call"]
    tools = [e for e in events if e["kind"] == "tool_result"]
    usage = {k: sum((e.get("usage") or {}).get(k, 0) for e in calls)
             for k in ("input", "output")}
    span = (events[-1]["ts"] - events[0]["ts"]) if events else 0
    decided_by = [e.get("by") for e in events if e["kind"] == "routing"]

    roles: dict[str, int] = {}
    for call in calls:
        role = call.get("role", "controller")
        roles[role] = roles.get(role, 0) + 1
    replans = sum(1 for e in events
                  if e["kind"] == "routing" and e["decision"] == "plan")
    verdicts = [e for e in events if e["kind"] == "routing"
                and e["decision"] in ("accepted", "rejected")]

    return (f"{len(calls)} model calls, {len(tools)} tool results, {span:.0f}s\n"
            f"by role: " + ", ".join(f"{r}={n}" for r, n in sorted(roles.items())) + "\n"
            f"tokens input={usage['input']} output={usage['output']}\n"
            f"edges chosen by model: {decided_by.count('model')}, "
            f"by runtime: {decided_by.count('runtime')}\n"
            f"plan revisions: {replans}\n"
            f"verdict: " + (f"{verdicts[-1]['decision']} "
                            f"(decided by {verdicts[-1].get('by')})"
                            if verdicts else "none — the run never reached the validator"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--format", choices=("text", "mermaid", "summary"),
                        default="text")
    args = parser.parse_args(argv)

    if not args.trace.exists():
        print(f"no such trace: {args.trace}", file=sys.stderr)
        return 1

    events = read_events(args.trace)
    print({"text": to_text, "mermaid": to_mermaid,
           "summary": summarise}[args.format](events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
