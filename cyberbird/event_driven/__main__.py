"""Run the event-driven variant of the agent.

    cyberbird event-driven
    cyberbird event-driven --alert-id bc284c6b
    python -m cyberbird.event_driven --rule bandit:B608 --limit 3

Every run writes three files:

    runs/<subject>-<time>.jsonl          the trace: every event, step and model call, in order
    runs/<subject>-<time>.mmd            the same run as a Mermaid sequence diagram
    lectures/lec03/runs/<subject>.mmd    the diagram again, for the lecture: the latest
                                         run per subject, replaced each time

<subject> is event-driven-<alert id>, event-driven-<rule>, or event-driven-all.
To keep a trace for the lecture as well, copy it there by hand: a trace is a
deliberate choice, a diagram is always wanted.
"""
from __future__ import annotations

import click
import asyncio
from datetime import datetime

from cyberbird.event_driven.config import CONFIG
from cyberbird.event_driven.console import Console
from cyberbird.event_driven.trace import Trace, Tracer
from cyberbird.event_driven.trajectory import write_mermaid
from cyberbird.event_driven.workflow import EventDrivenAgent
from cyberbird.event_driven.workspace import Workspaces


def run_subject(rule: str | None, alert_id: str | None) -> str:
    """event-driven-<what>: what the run was about, without when."""
    return f"event-driven-{alert_id or (rule or 'all').replace(':', '-')}"


@click.command(help="The event-driven agent.")
@click.option("--rule", help="Only alerts for this rule, e.g. bandit:B608.")
@click.option("--alert-id", help="Only this alert (bc284c6b is the worked example).")
@click.option("--limit", type=int, default=1, show_default=True, help="At most this many alerts.")
@click.option("--validators", type=click.IntRange(min=1, max=3), default=3, help="Number of validators to use. We have 3 available. So max is 3.")
@click.option("--budget", type=int, default=CONFIG.budget, show_default=True,
              help="Model calls allowed per alert.")
@click.option("--quiet", is_flag=True, help="No live commentary; print only the report.")
@click.option("--verbose", is_flag=True, help="Also print the framework's raw step and event log.")
def main(rule: str | None, alert_id: str | None, limit: int, validators: int,
         budget: int, quiet: bool, verbose: bool) -> None:
    # timeout=None: the default is 45 s, shorter than one agent run.
    config = CONFIG.with_(validators=validators, budget=budget)
    subject = run_subject(rule, alert_id)
    # runs/ keeps every run, timestamped; the lecture keeps the latest per subject.
    run_id = f"{subject}-{datetime.now():%Y%m%d-%H%M%S}"
    trace_path = config.runs_dir / f"{run_id}.jsonl"
    lecture_diagram = config.lecture_runs_dir / f"{subject}.mmd"
    console = Console(enabled=not quiet)
    console.header(run_id, config.model, config.budget, config.validators)

    results = None
    try:
        # Trace outermost: if the run dies, the workspaces close first and the
        # trace still records "error" as the terminal line.
        with Trace(run_id, trace_path, on_event=console.event) as trace, \
                Workspaces(config) as workspaces:
            agent = EventDrivenAgent(workspaces, config, timeout=None, verbose=verbose,
                                     runtime=Tracer(trace))
            results = asyncio.run(agent(rule=rule, alert_id=alert_id, limit=limit))
            trace.terminal("completed", results=results)
    finally:
        # Drawn from the trace, so a run that crashed still gets its diagram.
        console.report(results, config.budget, trace_path,
                       [write_mermaid(trace_path), write_mermaid(trace_path, lecture_diagram)])


if __name__ == "__main__":
    main()
