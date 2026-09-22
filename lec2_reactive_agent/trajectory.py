"""Turn a recorded run into a sequence diagram.

The state graph shows what the agent *could* do. A trajectory shows what one run
actually did, in order, with who decided each step.

    python -m lec2_reactive_agent.trajectory runs/v1-bc284c6b.jsonl
    python -m lec2_reactive_agent.trajectory runs/v1-bc284c6b.jsonl --format mermaid

Three participants, matching the lecture's vocabulary:

    Controller   the model. The only participant that decides.
    Runtime      the dispatcher and Submit. Executes, refuses, checks.
    Workspace    the pinned fixture this attempt may edit.

An arrow leaving Controller is a proposal. An arrow returning to it is an
observation the runtime produced. Nothing reaches Controller that the runtime
did not put there, which is the property the dispatcher exists to guarantee.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lec2_reactive_agent.trace import read_events


def _args(args: dict, width: int = 40) -> str:
    text = ", ".join(f"{k}={v}" for k, v in args.items())
    text = text.replace("\n", " ").replace('"', "'")
    return text if len(text) <= width else text[:width - 1] + "…"


def to_mermaid(events: list[dict]) -> str:
    """A Mermaid sequenceDiagram of one run."""
    out = [
        "sequenceDiagram",
        "    autonumber",
        "    participant C as Controller<br/>(model decides)",
        "    participant R as Runtime<br/>(executes, refuses, checks)",
        "    participant W as Workspace<br/>(pinned fixture)",
        "",
    ]
    start = events[0]["ts"] if events else 0

    for event in events:
        kind = event["kind"]
        at = f"{event['ts'] - start:.0f}s"

        if kind == "model_call":
            usage = event.get("usage") or {}
            asked = ", ".join(event.get("tool_calls") or [])
            out.append(f"    Note over C: {at} · in={usage.get('input', 0)} "
                       f"out={usage.get('output', 0)}")
            if not asked:
                out.append("    C->>R: no tool call — finished")

        elif kind == "tool_request":
            out.append(f"    C->>R: {event['tool']}({_args(event.get('args', {}))})")
            out.append(f"    R->>W: {event['tool']}")

        elif kind == "tool_result":
            if event.get("ok"):
                out.append(f"    W-->>R: {event.get('chars', 0)} chars")
                out.append(f"    R-->>C: observation")
            else:
                out.append(f"    R-->>C: refused — {_args({'':event.get('reason','')}, 46)[1:]}")

        elif kind == "state_change" and event.get("checks"):
            checks = event["checks"]
            marks = " ".join(f"{n}={'✓' if checks[n] else '✗'}"
                             for n in ("applies", "touches_line", "rescan_clean"))
            out.append(f"    R->>W: rescan")
            out.append(f"    Note over R: {marks}")

        elif kind == "routing" and event.get("by") == "runtime":
            out.append(f"    Note over R: routed → {event['decision']} (runtime)")

        elif kind == "terminal":
            out.append(f"    Note over C,W: {event['status'].upper()}")

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
            lines.append(f"{at}  MODEL     decides: {asked}"
                         f"   (in={usage.get('input',0)} out={usage.get('output',0)})")
        elif kind == "routing":
            lines.append(f"{at}  ROUTE     → {event['decision']}   decided by {event.get('by')}")
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
    return (f"{len(calls)} model calls, {len(tools)} tool results, {span:.0f}s\n"
            f"tokens input={usage['input']} output={usage['output']}\n"
            f"edges chosen by model: {decided_by.count('model')}, "
            f"by runtime: {decided_by.count('runtime')}")


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
