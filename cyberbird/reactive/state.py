"""The state record every agent shares, and its reducers.

This is the schema handed to `StateGraph(AgentState)`.

A node never mutates state. It returns a dict of proposed changes, and LangGraph
merges each key into the running state. How a key merges is decided by its
reducer: a field with no reducer is replaced, a field annotated with one is
combined by calling it. Reducers are LangGraph's to call, never yours.

Fields fall into two lifetimes here:

    identity    set once, never changed     selector, repo, commit
                set once, by `normalize`    scan_report, alert, workspace
    run-local   built up as the run goes    messages, observations, candidate_patch,
                                            usage, model_calls, status

`alert` and `workspace` start empty. The run begins from a *selector* (an alert
id or a FILE:LINE), and only the normalize node, having scanned the fixture,
can turn that into an alert. The workspace waits for the alert because it
withholds every benchmark case except the alert's own.

A field exists only when a node here fills it, so nothing sits in the record
waiting for a node this agent does not have.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from cyberbird.reactive.config import CONFIG, Config
from cyberbird.reactive.trace import Usage

RUNNING = "running"

PINNED_COMMIT = "f1291485808b66e20ddb6b01b10dc71b3df8c8ba"


def add_usage(existing: Usage | None, update: Usage | dict | None) -> Usage:
    """Sum the four token counters, returning a new Usage.

    A node reports only its own call's delta; this keeps the running total. No
    node ever holds the total, so no node can clobber it.

    Never produces a sum across the four. Each counter accumulates with its own
    kind and nothing else.
    """
    total = Usage(**(existing.as_dict() if existing else {}))
    if update:
        total.add(**(update.as_dict() if isinstance(update, Usage) else update))
    return total


class AgentState(TypedDict, total=False):
    # identity, set once
    selector: dict
    repo: str
    commit: str

    # set once, by the scan and normalize nodes
    scan_report: str | None
    alert: dict | None
    workspace: str | None

    # built up as the run goes
    messages: Annotated[list, add_messages]
    observations: Annotated[list, operator.add]
    candidate_patch: str | None
    usage: Annotated[Usage, add_usage]
    model_calls: Annotated[int, operator.add]
    status: str


def initial_state(selector: dict, config: Config = CONFIG) -> AgentState:
    """A fresh run, before anything has been scanned.

    Called before the graph starts, so no reducer is involved: this dict becomes
    the starting state directly.

    There is no workspace here, and there must not be. Creating one would
    produce a tree nobody owns and nobody cleans up — and, worse, one built
    without `case=`, so every sibling benchmark case would be present and the
    agent could grep the answer out of a safe twin. `normalize` asks the run
    for one once it knows the alert; the run that makes it is the run that
    removes it.
    """
    return AgentState(
        selector=selector,
        # fixture is fixtures/<app>/src, so the app's name is its parent's
        repo=config.fixture.parent.name,
        commit=PINNED_COMMIT,
        scan_report=None,
        alert=None,
        workspace=None,
        messages=[],
        observations=[],
        candidate_patch=None,
        model_calls=0,
        usage=Usage(),
        status=RUNNING,
    )
