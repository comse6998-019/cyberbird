"""Run the reactive agent on one alert.

    python -m cyberbird.reactive --alert-id bc284c6b
    python -m cyberbird.reactive --location testcode/BenchmarkTest00283.py:46 --budget 10
"""
from __future__ import annotations

import sys

from cyberbird.reactive.cli import build_parser, default_trace_path, run_name, selector_from_args
from cyberbird.reactive.console import Console
from cyberbird.reactive.config import CONFIG, Config
from cyberbird.reactive.exceptions import AlertNotFound
from cyberbird.reactive.graph import build_graph
from cyberbird.reactive.reactive_agent import ReactiveAgent
from cyberbird.reactive.trace import Trace

AGENT = "reactive"


def draw(kind: str, config: Config) -> int:
    """Draw the state graph without running it.

    Conditional edges render dotted and unconditional ones solid, so the single
    solid `tools -> controller` edge — the one line that makes this agent
    reactive — is visually distinct from every edge the model chooses.
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
        out = config.runs_dir / f"{AGENT}-graph.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(graph.draw_mermaid_png())
        print(f"wrote {out}")
    return 0


def main(argv=None) -> int:
    parser = build_parser("the reactive agent")
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
        selector = selector_from_args(args)
    except AlertNotFound as exc:
        print(exc, file=sys.stderr)
        return 1

    run_id = run_name(AGENT, selector)
    trace_path = args.trace or default_trace_path(run_id, config)

    console = Console(enabled=not args.quiet)
    console.header(run_id, config.model, config.budget)

    with ReactiveAgent(selector, config, trace_path, console, AGENT,
                       diagram=args.diagram) as agent:
        final = agent.execute()

    console.report(final, config.budget, trace_path)
    return 0 if final["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
