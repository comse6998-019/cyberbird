"""The run's event log: what happened, in order, and how it ended.

Every module writes here. The dispatcher records tool requests and results, each
graph records model calls and routing decisions, and every run ends by stating a
terminal status. Build this before anything that generates events — a trace
added afterwards is always missing the event you needed.

The trace is what makes the lecture's claims checkable. "V2 retried without a
model call" is an assertion about this file, not about the code.

    with Trace("v1-bc284c6b", path) as t:
        t.event("model_call", role="controller", usage={"input": 900, "output": 40})
        t.event("routing", decision="tools", by="model")
        t.terminal("accepted")

Two design decisions are recorded here rather than left implicit:

1. `usage` on a Trace is accumulated *from the events written*, so it cannot
   drift from the file. It is a mirror for reporting. `AgentState["usage"]` is
   authoritative for the budget predicate, because that is what routing reads
   and what V4's attempt reset will have to retain deliberately.

2. Every event carries both a monotonic `step` and a wall-clock `ts`. `ts` is
   volatile, so comparing two runs strips it — see `compare`. Keep the clock for
   debugging a slow run at ~10.5s per model call; ignore it when asserting that
   a run replays identically.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class TerminalStatus(str, Enum):
    """The mutually exclusive ways a run can end."""

    # Print the value, not "TerminalStatus.MEMBER". These are written into
    # traces and reports, where the repr would be noise.
    __str__ = str.__str__

    ACCEPTED = "accepted"                  # the goal test passed
    UNRESOLVED = "unresolved"              # attempts exhausted (V4)
    BUDGET_EXHAUSTED = "budget_exhausted"  # the failsafe fired
    NO_PROGRESS = "no_progress"            # no state change over N steps
    ERROR = "error"                        # unrecoverable

    @classmethod
    def values(cls) -> frozenset[str]:
        """Valid string values, preserving string-based callers on Python 3.10+."""
        return frozenset(status.value for status in cls)


class EventKind(str, Enum):
    """The complete vocabulary of trace events."""

    # Print the value, not "EventKind.MEMBER". These are written into
    # traces and reports, where the repr would be noise.
    __str__ = str.__str__

    MODEL_CALL = "model_call"      # a model node's four token counters
    TOOL_REQUEST = "tool_request"  # the dispatcher, never a graph node
    TOOL_RESULT = "tool_result"    # the dispatcher
    STATE_CHANGE = "state_change"  # attempt resets and non-obvious updates
    ROUTING = "routing"            # a conditional edge: who decided
    TERMINAL = "terminal"          # once, at the end, always

    @classmethod
    def values(cls) -> frozenset[str]:
        """Valid string values, preserving string-based callers on Python 3.10+."""
        return frozenset(kind.value for kind in cls)


@dataclass
class Usage:
    """Four token counters, kept apart.

    There is deliberately no `total`. The four are not interchangeable: a cache
    read costs a fraction of fresh input, and output is the expensive one. A
    single number cannot answer "did context management help?" — that question
    is whether `cache_read` rose while `input` fell, which summing erases.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, **counts: int) -> None:
        for name, value in counts.items():
            if not hasattr(self, name):
                raise ValueError(f"unknown token counter: {name!r}")
            setattr(self, name, getattr(self, name) + int(value))

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


class NoTerminalStatus(Exception):
    """A run was closed without stating how it ended."""


class Trace:
    """An append-only JSONL event log for one run."""

    def __init__(self, run_id: str, path: Path, on_event=None, on_close=None):
        self.run_id = run_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._step = 0
        self._terminal: str | None = None
        self.usage = Usage()
        # An optional listener, notified after the event is on disk.
        # A console is a spectator: it can never change what was recorded.
        self.on_event = on_event
        # Fired once, just before the file closes, with the completed Trace.
        # The same spectator arrangement as on_event: a listener may read a
        # finished run, never alter what it recorded.
        self.on_close = on_close

    def event(self, kind: str, **fields) -> dict:
        """Append one event. Flushed immediately.

        Nothing is buffered: a run that crashes must leave behind the trace that
        explains the crash.
        """
        if kind not in EventKind.values():
            raise ValueError(f"unknown event kind {kind!r}; add it to EventKind deliberately")
        if self._terminal is not None:
            raise RuntimeError(f"{self.run_id} already ended with {self._terminal!r}")

        # Accumulating here, and only here, is what keeps self.usage honest:
        # it can never disagree with the events actually on disk.
        if "usage" in fields and fields["usage"]:
            self.usage.add(**fields["usage"])

        self._step += 1
        record = {"step": self._step, "ts": time.time(), "run_id": self.run_id,
                  "kind": kind, **fields}
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()
        if self.on_event is not None:
            self.on_event(record)
        return record

    def terminal(self, status: str) -> dict:
        """State how the run ended. Exactly once, and always."""
        if status not in TerminalStatus.values():
            raise ValueError(f"unknown terminal status {status!r}; one of {sorted(TerminalStatus.values())}")
        record = self.event("terminal", status=status, usage_total=self.usage.as_dict())
        self._terminal = status
        return record

    def close(self) -> None:
        """Close the file, refusing a run that never stated a status."""
        if self.on_close is not None:
            try:
                self.on_close(self)
            except Exception:  # a listener must never break a run
                pass
        self._fh.close()
        if self._terminal is None:
            raise NoTerminalStatus(
                f"{self.run_id} closed without a terminal status — see {self.path}")

    def __enter__(self) -> "Trace":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        # An unhandled exception *is* a terminal status. Recording it here means
        # the invariant "every run states how it ended" holds even when a run
        # dies, rather than the close() below masking the original traceback.
        if exc_type is not None and self._terminal is None:
            self.terminal("error")
            self._fh.write(json.dumps(
                {"step": self._step + 1, "ts": time.time(), "run_id": self.run_id,
                 "kind": "state_change", "exception": exc_type.__name__,
                 "message": str(exc_value)}, default=str) + "\n")
            self._fh.flush()
        self.close()


def read_events(path: Path) -> list[dict]:
    """Every event in a trace file, in order."""
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def stable(events: list[dict]) -> list[dict]:
    """Events with volatile fields removed, for comparing two runs."""
    # Wall-clock timestamps are real but not reproducible.
    return [{k: v for k, v in e.items() if k != "ts"} for e in events]


def kinds(events: list[dict]) -> list[str]:
    """Just the ordered event kinds — the shape most assertions care about."""
    return [e["kind"] for e in events]


def compare(a: Path, b: Path) -> list[str]:
    """Differences between two runs, ignoring volatile fields.

    An empty list means the runs replayed identically, which is what the
    scripted-fault demo has to be able to claim.
    """
    left, right = stable(read_events(a)), stable(read_events(b))
    diffs = []
    for i in range(max(len(left), len(right))):
        x = left[i] if i < len(left) else None
        y = right[i] if i < len(right) else None
        if x != y:
            diffs.append(f"step {i + 1}: {x} != {y}")
    return diffs


def total_usage(events: list[dict]) -> Usage:
    """Recompute usage from a trace, independent of whoever wrote it."""
    u = Usage()
    for e in events:
        if e["kind"] == "model_call" and e.get("usage"):
            u.add(**e["usage"])
    return u
