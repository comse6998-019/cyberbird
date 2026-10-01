"""The run's event log: every event, in order, and how the run ended.

In the graph agents, every node called `trace.event(...)`. Here no step does. The
trace is a subscriber: `Tracer` wraps the workflow's runtime, the way `verbose=True`
does, and sees every event the moment it enters the workflow, with its full
contents, plus every step starting and finishing. A step cannot forget to trace,
because it never traces at all.

    with Trace(run_id, path) as trace:
        agent = EventDrivenAgent(workspaces, config, runtime=Tracer(trace))
        result = await agent(alert_id="bc284c6b")
        trace.terminal("completed", results=result)

One JSON object per line, each with `step` (1, 2, 3, ...), `ts` (wall clock),
`run_id` and `kind`:

    event       an event entered the workflow: its type, alert_id, and fields
    step        a step started or finished, on which worker, from which event
    model_call  one model call: role, tokens, tool calls and its whole reasoning
                (published by the step, never routed)

A model's reasoning also streams to the console piece by piece as it arrives
(`Thinking` events), but only the finished whole is written to the file.
    terminal    once, at the end, always; "error" if the run died

`ts` is real but not reproducible; compare two runs on everything else.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from workflows.events import Event, StepState, StepStateChanged, StopEvent
from workflows.plugins._context import get_current_runtime
from workflows.runtime.runtime_decorators import (
    BaseInternalRunAdapterDecorator,
    BaseRuntimeDecorator,
)
from workflows.runtime.types.results import StepWorkerResult
from workflows.runtime.types.ticks import TickAddEvent, TickStepResult

from cyberbird.event_driven.events import ModelCalled, Thinking

KINDS = frozenset({"event", "step", "model_call", "terminal"})


class Trace:
    """An append-only JSONL log for one run. Flushed on every line."""

    def __init__(self, run_id: str, path: Path, on_event=None):
        self.run_id = run_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._step = 0
        self._terminal: str | None = None
        # An optional listener (the console), told after each line is on disk.
        # A spectator: it can never change what was recorded.
        self.on_event = on_event

    def record(self, kind: str, **fields) -> dict:
        """Append one line. Nothing is buffered: a run that crashes must leave
        behind the trace that explains the crash."""
        if kind not in KINDS:
            raise ValueError(f"unknown trace kind {kind!r}; one of {sorted(KINDS)}")
        if self._terminal is not None:
            raise RuntimeError(f"{self.run_id} already ended with {self._terminal!r}")
        self._step += 1
        line = {"step": self._step, "ts": time.time(), "run_id": self.run_id,
                "kind": kind, **fields}
        self._fh.write(json.dumps(line, default=str) + "\n")
        self._fh.flush()
        if self.on_event is not None:
            self.on_event(line)
        return line

    def live(self, kind: str, **fields) -> None:
        """Tell the listener, write nothing. For what is only worth watching as it
        happens: a model's reasoning, one piece at a time. The file keeps the whole
        reasoning once, on the model_call line."""
        if self.on_event is not None:
            self.on_event({"kind": kind, "run_id": self.run_id, **fields})

    def event(self, ev: Event) -> dict:
        """One workflow event, whole. `alert_id` is lifted out so a reader can
        filter one alert's story from a run that handled several."""
        data = ev.model_dump(mode="json")
        return self.record("event", type=type(ev).__name__,
                           alert_id=data.pop("alert_id", None), data=data)

    def terminal(self, status: str, **fields) -> dict:
        """How the run ended. Exactly once."""
        line = self.record("terminal", status=status, **fields)
        self._terminal = status
        return line

    def __enter__(self) -> "Trace":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # An unhandled exception is a terminal status too: every run states
        # how it ended, including the ones that died.
        if self._terminal is None:
            self.terminal("error", exception=exc_type.__name__ if exc_type else None,
                          message=str(exc_value) if exc_value else "closed without a status")
        self._fh.close()


def _name(raw: str | None) -> str | None:
    """"<class 'pkg.mod.Cls'>" -> "Cls"; the framework reports types as strings."""
    if not raw:
        return None
    if raw.startswith("<class '") and raw.endswith("'>"):
        raw = raw[8:-2].rsplit(".", 1)[-1]
    return None if raw == "NoneType" else raw


class _Recorder(BaseInternalRunAdapterDecorator):
    """Sits between the workflow and its runtime, and writes down what passes."""

    def __init__(self, decorated, trace: Trace):
        super().__init__(decorated)
        self._trace = trace

    async def on_tick(self, tick) -> None:
        # Every event entering the workflow is a TickAddEvent: the start event,
        # a step's return value, and each ctx.send_event of a fan-out.
        if isinstance(tick, TickAddEvent):
            self._trace.event(tick.event)
        # The StopEvent is not added to any queue; it arrives as a step result.
        elif isinstance(tick, TickStepResult):
            for result in tick.result:
                if isinstance(result, StepWorkerResult) and isinstance(result.result, StopEvent):
                    self._trace.event(result.result)
        await super().on_tick(tick)

    async def write_to_event_stream(self, event: Event) -> None:
        if isinstance(event, StepStateChanged) and event.step_state != StepState.PREPARING:
            running = event.step_state == StepState.RUNNING
            self._trace.record("step", name=event.name,
                               state="started" if running else "finished",
                               worker=event.worker_id,
                               input=_name(event.input_event_name),
                               output=None if running else _name(event.output_event_name))
        elif isinstance(event, Thinking):
            self._trace.live("thinking", alert_id=event.alert_id, role=event.role,
                             text=event.text)
        elif isinstance(event, ModelCalled):
            self._trace.record("model_call", alert_id=event.alert_id, role=event.role,
                               usage=event.usage, tool_calls=event.tool_calls,
                               thinking=event.thinking)
        await super().write_to_event_stream(event)


class Tracer(BaseRuntimeDecorator):
    """A runtime that records the run into `trace`, then does what it always did.

    Passed as `runtime=` to the workflow. Composes with `verbose=True`, which wraps
    whatever runtime it is given in exactly the same way.
    """

    def __init__(self, trace: Trace, decorated=None):
        super().__init__(decorated or get_current_runtime())
        self._trace = trace

    def get_internal_adapter(self, workflow):
        return _Recorder(self._decorated.get_internal_adapter(workflow), self._trace)


def read_events(path: Path) -> list[dict]:
    """Every line of a trace, in order."""
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
