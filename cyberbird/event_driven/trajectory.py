"""Turn a recorded run into a sequence diagram.

The workflow's table shows what the agent *could* do. A trajectory shows what one
run actually did, in order, with who decided each step.

    python -m cyberbird.event_driven.trajectory runs/event-driven-bc284c6b-….jsonl
    python -m cyberbird.event_driven.trajectory runs/event-driven-bc284c6b-….jsonl --format mermaid
    python -m cyberbird.event_driven.trajectory runs/event-driven-bc284c6b-….jsonl --format summary

Five participants:

    Planner      the model, proposing a plan and revising it once
    Controller   the model, choosing one action at a time
    Runtime      scan, gate, tools, submit, judge. Executes, refuses, checks, routes
    Workspace    the alert's own copy of the fixture
    Validators   the model, one call per lens, judging a patch it did not write

Three of the five are the same model under different instructions. Keeping them
apart on the diagram is the point: the one that proposed the patch is not the one
that accepted it.

Each arrow is one event from the trace. The diagram is built from events alone,
so it can only show what the workflow actually carried.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cyberbird.event_driven.trace import read_events

ACTOR = {"planner": "P", "controller": "C", "validator": "V"}


def _clip(text, width: int) -> str:
    """One line of Mermaid-safe text: newlines, semicolons and quotes break it."""
    text = " ".join(str(text).split()).replace(";", ",").replace('"', "'").replace("#", "")
    return text if len(text) <= width else text[:width - 1] + "…"


def _args(args: dict, width: int = 40) -> str:
    return _clip(", ".join(f"{k}={v}" for k, v in args.items()), width)


def to_mermaid(lines: list[dict]) -> str:
    """A Mermaid sequenceDiagram of one run."""
    out = [
        "sequenceDiagram",
        "    autonumber",
        "    participant P as Planner<br/>(model plans)",
        "    participant C as Controller<br/>(model decides)",
        "    participant R as Runtime<br/>(executes, refuses, checks)",
        "    participant W as Workspace<br/>(alert's own copy)",
        "    participant V as Validators<br/>(model judges)",
        "",
    ]
    start = lines[0]["ts"] if lines else 0
    found = 0

    for line in lines:
        at = f"{line['ts'] - start:.0f}s"
        kind = line["kind"]

        if kind == "model_call":
            usage = line.get("usage") or {}
            who = ACTOR.get(line.get("role"), "C")
            out.append(f"    Note over {who}: {at} · in={usage.get('input', 0)} "
                       f"out={usage.get('output', 0)}")
            continue
        if kind == "terminal":
            out.append(f"    Note over P,V: RUN {str(line['status']).upper()}")
            continue
        if kind != "event":
            continue

        kind, data = line["type"], line.get("data") or {}

        if kind == "AlertFound":
            found += 1
        elif kind == "AlertStarted":
            if found:
                out.append(f"    Note over R: scan found {found}, gate releases one at a time")
                found = 0
            alert = data.get("alert") or {}
            out.append(f"    Note over P,V: alert {line['alert_id']} · {alert.get('rule')} "
                       f"at {alert.get('file')}:{alert.get('line')}")
            out.append("    R->>P: AlertStarted")
        elif kind == "PlanCreated":
            # Dashed for the revision, so the second plan is not the first drawn twice.
            revised = data.get("revised")
            out.append(f"    {'P-->>C' if revised else 'P->>C'}: "
                       f"{'revised plan' if revised else 'plan'} · {len(data.get('steps') or [])} steps")
        elif kind == "ToolsRequested":
            for call in data.get("calls") or []:
                out.append(f"    C->>R: {call['name']}({_args(call.get('args') or {})})")
                out.append(f"    R->>W: {call['name']}")
        elif kind == "Observed":
            for obs in data.get("observations") or []:
                if obs.get("ok"):
                    out.append(f"    W-->>R: {_clip(obs.get('result', ''), 40)}")
                    out.append("    R-->>C: observation")
                else:
                    out.append(f"    R-->>C: refused · {_clip(obs.get('result', ''), 46)}")
        elif kind == "Stalled":
            out.append(f"    R->>P: replan · {_clip(data.get('reason', ''), 46)}")
        elif kind == "Submitted":
            out.append("    C->>R: no tool call · submit")
        elif kind == "ChecksFailed":
            out.append(f"    R-->>C: checks failed · {_clip('; '.join(data.get('reasons') or []), 46)}")
        elif kind == "ValidateRequested":
            out.append(f"    R->>V: judge through lens {data.get('lens')}")
        elif kind == "Verdict":
            verdict = "accepted" if data.get("accepted") else "rejected"
            out.append(f"    V-->>R: {data.get('lens')} · {verdict} (decided by {data.get('decided_by')})")
            for reason in data.get("reasons") or []:
                out.append(f"    Note over V: {_clip(reason, 60)}")
        elif kind == "AlertDone":
            reason = f" · {_clip(data['reason'], 40)}" if data.get("reason") else ""
            out.append(f"    Note over P,V: {line['alert_id']} {str(data.get('status')).upper()}{reason}")

    return "\n".join(out)


def to_text(lines: list[dict]) -> str:
    """The same trajectory as plain text, for reading in a terminal."""
    out, start = [], (lines[0]["ts"] if lines else 0)
    for line in lines:
        at = f"{line['ts'] - start:6.1f}s"
        kind = line["kind"]
        if kind == "model_call":
            usage = line.get("usage") or {}
            asked = (", ".join(line.get("tool_calls") or []) or "(no tool call)"
                     if line.get("role") == "controller" else "decides")
            out.append(f"{at}  MODEL     {line.get('role')}: {asked}"
                       f"   in={usage.get('input', 0)} out={usage.get('output', 0)}")
        elif kind == "step" and line.get("name") == "validate":
            out.append(f"{at}  STEP      validate {line['state']} on worker {line.get('worker')}")
        elif kind == "terminal":
            out.append(f"{at}  END       {str(line['status']).upper()}   {line.get('results', '')}")
        elif kind == "event":
            data = line.get("data") or {}
            who = f"[{line['alert_id']}] " if line.get("alert_id") else ""
            detail = {
                "PlanCreated": lambda: f"{len(data.get('steps') or [])} steps"
                                       + ("  (revised)" if data.get("revised") else ""),
                "ToolsRequested": lambda: ", ".join(f"{c['name']}({_args(c.get('args') or {}, 50)})"
                                                    for c in data.get("calls") or []),
                "Observed": lambda: ", ".join(("ok " if o.get("ok") else "REFUSED ")
                                              + _clip(o.get("result", ""), 50)
                                              for o in data.get("observations") or []),
                "Stalled": lambda: data.get("reason", ""),
                "Verdict": lambda: f"{data.get('lens')}: "
                                   + ("accepted" if data.get("accepted") else "rejected"),
                "AlertDone": lambda: f"{data.get('status')}  {data.get('reason', '')}",
                "ValidateRequested": lambda: str(data.get("lens")),
            }.get(line["type"], lambda: "")()
            out.append(f"{at}  {line['type']:<18}{who}{detail}")
    return "\n".join(out)


def summarise(lines: list[dict]) -> str:
    calls = [l for l in lines if l["kind"] == "model_call"]
    events = [l for l in lines if l["kind"] == "event"]
    usage = {k: sum((c.get("usage") or {}).get(k, 0) for c in calls) for k in ("input", "output")}
    span = (lines[-1]["ts"] - lines[0]["ts"]) if lines else 0
    roles: dict[str, int] = {}
    for call in calls:
        roles[call.get("role")] = roles.get(call.get("role"), 0) + 1
    done = {l["alert_id"]: (l.get("data") or {}).get("status")
            for l in events if l["type"] == "AlertDone"}
    replans = sum(1 for l in events if l["type"] == "Stalled")
    terminal = next((l for l in lines if l["kind"] == "terminal"), None)
    return (f"{len(events)} events, {len(calls)} model calls, {span:.0f}s\n"
            f"by role: " + (", ".join(f"{r}={n}" for r, n in sorted(roles.items())) or "none") + "\n"
            f"tokens: input={usage['input']} output={usage['output']}\n"
            f"plan revisions: {replans}\n"
            f"alerts: " + (", ".join(f"{a}={s}" for a, s in done.items()) or "none finished") + "\n"
            f"run: {terminal['status'] if terminal else 'no terminal line: the trace was cut short'}")


def write_mermaid(trace_path: Path, out: Path | None = None) -> Path:
    """Save the run's diagram, by default beside its trace as <trace>.mmd."""
    out = Path(out) if out else Path(trace_path).with_suffix(".mmd")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_mermaid(read_events(trace_path)) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--format", choices=("text", "mermaid", "summary"), default="text")
    args = parser.parse_args(argv)
    if not args.trace.exists():
        print(f"no such trace: {args.trace}", file=sys.stderr)
        return 1
    lines = read_events(args.trace)
    print({"text": to_text, "mermaid": to_mermaid, "summary": summarise}[args.format](lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
