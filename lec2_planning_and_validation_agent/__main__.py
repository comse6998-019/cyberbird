"""Run V2/V3 on one alert.

    python -m lec2_planning_and_validation_agent --alert-id bc284c6b
    python -m lec2_planning_and_validation_agent --location testcode/BenchmarkTest00283.py:46 --budget 10
"""
from __future__ import annotations

import sys

from lec2_planning_and_validation_agent.cli import build_parser, default_trace_path, resolve_alert
from lec2_planning_and_validation_agent.console import Console
from lec2_planning_and_validation_agent.config import CONFIG, Config
from lec2_planning_and_validation_agent.exceptions import AlertNotFound
from lec2_planning_and_validation_agent.graph import build_graph
from lec2_planning_and_validation_agent.reactive_agent import ReactiveAgent
from lec2_planning_and_validation_agent.trace import Trace

VERSION = "v23"


def draw(kind: str, config: Config) -> int:
    """Draw the state graph without running it.

    Conditional edges render dotted and unconditional ones solid, and in this
    version only two edges are solid: `START -> plan` and `validate -> END`. The
    entry and the exit are fixed; every edge between them is chosen at run time
    by a predicate over state. In V1 `tools -> controller` was solid too, and
    seeing it become dotted here is the clearest picture of what V2 changed.
    """
    import tempfile
    from pathlib import Path as _Path

    placeholder = {"alert_id": "-", "rule": "bandit:B608", "file": "-",
                   "line": 0, "severity": "-", "message": "-"}
    scratch = _Path(tempfile.mkdtemp()) / "graph.jsonl"
    with Trace("graph", scratch) as trace:
        graph = build_graph(trace, config).get_graph()
        trace.terminal("error")

    if kind == "mermaid":
        print(graph.draw_mermaid())
    elif kind == "ascii":
        print(graph.draw_ascii())
    else:
        out = config.runs_dir / f"{VERSION}-graph.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(graph.draw_mermaid_png())
        print(f"wrote {out}")
    return 0


def main(argv=None) -> int:
    parser = build_parser(f"{VERSION}: the planning and validation agent")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress the live commentary; print only the report")
    parser.add_argument("--graph", choices=("mermaid", "ascii", "png"),
                        help="draw the state graph and exit without running")
    parser.add_argument("--diagram", choices=("mermaid", "text", "none"),
                        default="mermaid",
                        help="sequence diagram of the run, drawn as the trace closes")
    args = parser.parse_args(argv)
    config = Config.from_args(args)

    if args.graph:
        return draw(args.graph, config)

    try:
        alert = resolve_alert(args)
    except (AlertNotFound, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1

    trace_path = args.trace or default_trace_path(VERSION, alert, config)
    run_id = f"{VERSION}-{alert['alert_id']}"

    console = Console(enabled=not args.quiet)
    console.header(run_id, alert, config.model, config.budget)

    with ReactiveAgent(alert, config, trace_path, console, VERSION,
                       diagram=args.diagram) as agent:
        final = agent.execute()

    console.report(final, config.budget, trace_path)
    return 0 if final["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
