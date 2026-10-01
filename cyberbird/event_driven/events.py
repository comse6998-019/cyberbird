"""These are the events used in the event-driven workflow of the Cyberbird agent.

A step accepts one (or more) event types as input and emits one (or more) event types as output.

One run handles many alerts, so every event about one alert subclasses
`AlertEvent` and carries its `alert_id`: the correlation key.
"""

from __future__ import annotations

from workflows.events import Event, StartEvent


class RunRequested(StartEvent):
    """The run's input: filters on the alert queue."""
    rule: str | None = None
    alert_id: str | None = None
    limit: int | None = None

class ScanRequested(Event):
    """Event indicating that a scan has been requested."""
    root: str

class AlertFound(Event):
    """Event indicating that the scan found an alert. It waits at the gate."""
    alert: dict


class AlertEvent(Event):
    """Every event about one alert carries its id: the correlation key."""
    alert_id: str


class ModelCalled(AlertEvent):
    """Published, not routed: one model call, for the trace. No step accepts it.

    Steps publish it with `ctx.write_event_to_stream`. Every other event reaches the
    trace because a step emitted it; a model call is not an event in the flow, so
    the step that made it says so.
    """
    role: str                   # planner | controller | validator
    usage: dict                 # {"input": n, "output": n}
    tool_calls: list[str] = []  # names only; the calls themselves ride on ToolsRequested
    thinking: str = ""          # the model's whole reasoning, once the call is over


class Thinking(AlertEvent):
    """Published, not routed: one piece of a model's reasoning, as it streams.

    For the console only. The trace does not write these one by one; it keeps
    the whole reasoning once, on the ModelCalled that ends the call.
    """
    role: str
    text: str

# --- Everthing below is alert-related events, so they subclass `AlertEvent` --- #
class AlertStarted(AlertEvent):
    """Event indicating that the gate let an alert through."""
    alert: dict

class PlanCreated(AlertEvent):
    """Event indicating that a plan has been created."""
    steps: list[dict]
    revised: bool  # Indicates whether the plan has been revised.

class ToolsRequested(AlertEvent):
    """Event indicating that tool(s) has(have) been requested."""
    calls: list[dict]

class Observed(AlertEvent):
    """Event indicating that an observation has been made."""
    observations: list[dict]

class Stalled(AlertEvent):
    """Event indicating that a process has stalled."""
    reason: str
    repeated: int
    last_result: str

class Submitted(AlertEvent):
    """Event indicating that a submission has been made."""
    pass

class ChecksFailed(AlertEvent):
    """Event indicating that checks have failed."""
    reasons: list[str]

class ValidateRequested(AlertEvent):
    """Event indicating that validation has been requested."""
    lens: str
    patch: str
    checks: dict

class Verdict(AlertEvent):
    """Event indicating the verdict of a validation."""
    lens: str
    accepted: bool
    reasons: list[str]
    decided_by: str

class AlertDone(AlertEvent):
    """Event indicating that an alert reached a terminal status. Back to the gate."""
    status: str
    reason: str = ""
