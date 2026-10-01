"""What the run builds up across steps. Everything else travels on events.

One run, many alerts: the run's own bookkeeping (queue, results) sits at the
top, and everything about one alert sits in its own `AlertState`, keyed by id.
"""
from __future__ import annotations
from pathlib import Path

from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field


class AlertState(BaseModel):
    """One alert's run. Nothing here is shared with any other alert."""
    alert: dict
    workspace: str | Path 
    plan: list[dict] = Field(default_factory=list)
    replanned_at: int = 0
    messages: list[AnyMessage] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)
    model_calls: int = 0
    usage: dict = Field(default_factory=lambda: {"input": 0, "output": 0})


class RunState(BaseModel):
    filters: dict = Field(default_factory=dict)
    expected: int = 0                                      # alerts the scan emitted
    queue: list[dict] = Field(default_factory=list)        # found, not yet started
    current: str | None = None                             # the one alert in progress
    alerts: dict[str, AlertState] = Field(default_factory=dict)
    results: dict[str, str] = Field(default_factory=dict)  # alert_id -> status
