"""The reactive agent: one run of one alert, start to terminal status.

    with ReactiveAgent({"alert_id": "bc284c6b"}, config, trace_path, console) as agent:
        final = agent.execute()

A run is the thing with a lifetime, which is why this is the context manager and
the graph is not. It owns two resources:

    the workspace   a ~11 MB copy of the fixture, made when `normalize` asks
    the trace       a file handle, opened on entry and closed on exit

The workspace cannot be made on entry: it withholds every benchmark case but the
alert's own, and the alert is not known until the graph has scanned the fixture.

Both end together. Before this existed, every run left its workspace behind:
eighteen of them, 198 MB, in one afternoon.

A workspace is kept when the run did not end in `accepted`, and its path is
printed. A run that failed is the one worth opening; a run that succeeded is
11 MB nobody will read again. The validator depends on this, because a deliberately
rejected attempt has to survive for inspection.
"""
from __future__ import annotations

from pathlib import Path

from cyberbird.reactive.cli import run_name
from cyberbird.reactive.config import CONFIG, Config
from cyberbird.reactive.graph import build_graph
from cyberbird.reactive.state import initial_state
from cyberbird.reactive.trace import TerminalStatus, Trace, read_events
from cyberbird.reactive.trajectory import to_mermaid
from contextlib import ExitStack

from cyberbird.reactive.workspace import AgentWorkspace


class ReactiveAgent:
    """Owns one run's workspace and trace, and drives its graph.

    The graph structure lives in `AgentDAG`; this is the thing that runs it.
    """

    def __init__(self, selector: dict, config: Config = CONFIG,
                 trace_path: Path | None = None, console=None,
                 agent: str = "reactive", keep_unless_accepted: bool = True,
                 diagram: str = "mermaid"):
        self.selector = selector
        self.alert: dict | None = None
        self.config = config
        self.agent = agent
        self.run_id = run_name(agent, selector)
        self.trace_path = Path(trace_path) if trace_path else (
            config.runs_dir / f"{self.run_id}.jsonl")
        self.console = console
        self.keep_unless_accepted = keep_unless_accepted
        self.diagram = diagram

        self._stack = ExitStack()
        self._ws: AgentWorkspace | None = None
        self.workspace: Path | None = None
        self.trace: Trace | None = None
        self.state: dict | None = None
        self.diagram_path: Path | None = None

    def __enter__(self) -> "ReactiveAgent":
        self.trace = Trace(self.run_id, self.trace_path,
                           on_event=self.console.event if self.console else None,
                           on_close=self._emit_sequence_diagram)
        return self

    def _open_workspace(self, alert: dict) -> Path:
        """Called by `normalize` once the alert is known. Owned here, not there.

        The answers are withheld while the agent works and put back on exit.
        """
        if self._ws is not None:
            raise RuntimeError("this run already has a workspace")
        self.alert = alert
        self._ws = AgentWorkspace(self.config, case=Path(alert["file"]).stem)
        self.workspace = self._stack.enter_context(self._ws)
        return self.workspace

    def _emit_sequence_diagram(self, trace: Trace) -> None:
        """Called by Trace just before it closes: save and draw what the run did.

        The diagram is written whatever `--diagram` says. That flag governs what
        appears on screen; the file is an artifact of the run, like the trace,
        and a run that scrolled past is still one you can read afterwards.

        Saved as Mermaid, because the file's job is to be pasted into the notes
        beside the other graphs.
        """
        events = read_events(trace.path)
        if not events:
            return

        self.diagram_path = self.config.runs_dir / f"{self.run_id}.mmd"
        self.diagram_path.parent.mkdir(parents=True, exist_ok=True)
        self.diagram_path.write_text(to_mermaid(events) + "\n", encoding="utf-8")

        if self.console is not None:
            self.console.trajectory(events, self.diagram, saved=self.diagram_path)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # Trace's own __exit__ records a terminal status for a run that died,
        # so the invariant "every run states how it ended" survives a crash.
        if self.trace is not None:
            self.trace.__exit__(exc_type, exc_value, traceback)

        # The workspace decides for itself whether to survive; tell it the
        # outcome first, then let its own __exit__ restore and clean up.
        if self._ws is not None:
            accepted = (self.state or {}).get("status") == TerminalStatus.ACCEPTED
            self._ws.keep = self.keep_unless_accepted and not accepted
        self._stack.close()
        self.workspace = None

    def execute(self) -> dict:
        """Run the graph to a terminal status."""
        if self.trace is None:
            raise RuntimeError("use ReactiveAgent as a context manager")

        state = initial_state(self.selector, self.config)
        graph = build_graph(self.trace, self.config, self.console,
                            open_workspace=self._open_workspace)

        # Each model call costs several supersteps, so the recursion limit
        # tracks the budget rather than being a second number to keep in step.
        self.state = graph.invoke(
            state, {"recursion_limit": max(4, self.config.budget * 4)})
        self.trace.terminal(self.state["status"])
        return self.state
