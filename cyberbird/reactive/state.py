"""The state record every version shares, and its reducers.

This is the schema handed to `StateGraph(AgentState)`.

A node never mutates state. It returns a dict of proposed changes, and LangGraph
merges each key into the running state. How a key merges is decided by its
reducer: a field with no reducer is replaced, a field annotated with one is
combined by calling it. Reducers are LangGraph's to call, never yours.

Fields fall into two lifetimes here:

    identity    set once, never changed     alert, repo, commit, workspace
    run-local   built up as the run goes    messages, observations, candidate_patch,
                                            usage, model_calls, status

Each later version adds the fields it actually populates: V2 brings `plan` with
the planner that writes it, V3 brings `validation` with the validator, and V4
brings `reflections` and `attempt` along with the machinery to clear run-local
fields between attempts. A field arrives with the node that fills it, so the
diff between two versions shows one change, not a field that was always there
waiting.
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
    alert: dict
    repo: str
    commit: str
    workspace: str

    # built up as the run goes
    messages: Annotated[list, add_messages]
    observations: Annotated[list, operator.add]
    candidate_patch: str | None
    usage: Annotated[Usage, add_usage]
    model_calls: Annotated[int, operator.add]
    status: str


def initial_state(alert: dict, workspace: str,
                  config: Config = CONFIG) -> AgentState:
    """A fresh run on one alert.

    Called before the graph starts, so no reducer is involved: this dict becomes
    the starting state directly.

    `workspace` is required, not defaulted. Creating one here would produce a
    tree nobody owns and nobody cleans up — and, worse, one built without
    `case=`, so every sibling benchmark case would be present and the agent
    could grep the answer out of a safe twin. The caller that makes the
    workspace is the caller that must remove it.
    """
    return AgentState(
        alert=alert,
        repo=config.fixture.name,
        commit=PINNED_COMMIT,
        workspace=workspace,
        messages=[],
        observations=[],
        candidate_patch=None,
        model_calls=0,
        usage=Usage(),
        status=RUNNING,
    )
