"""V1: the reactive agent.

    START -> controller -> tools -> controller -> ... -> submit -> END
                  |                                        |
                  +--------------- rejected ---------------+

Three nodes. `controller` is the only one where a model decides; `tools` and
`submit` are runtime work. The model chooses which edge is taken by emitting a
tool call or not — that choice, made at run time by a non-deterministic
component, is what makes this version reactive.

The graph definition is fixed. Nothing here is rewritten between runs; the
different paths a run takes are predicates over state.

Nodes are instance methods. LangGraph calls a node with the state alone, so the
collaborators have to come from somewhere; holding them on the instance keeps
each node callable on its own:

    AgentDAG(trace, config).controller(state)

which is how a node is exercised without building a graph at all. Binding them
with `functools.partial` instead would work, but LangGraph inspects a node's
signature and reserves the parameter name `config` for its own `RunnableConfig`
— a static method taking `config` collides with it and warns.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from lec2_reactive_agent.config import CONFIG, Config
from lec2_reactive_agent.dispatch import Dispatcher
from lec2_reactive_agent.llm import build_model
from lec2_reactive_agent.prompts import CONTROLLER_SYSTEM, controller_task, rejected_feedback
from lec2_reactive_agent.state import RUNNING, AgentState
from lec2_reactive_agent.submit import Submission
from lec2_reactive_agent.tools import ToolBundle
from lec2_reactive_agent.trace import EventKind, TerminalStatus, Trace


def opening_messages(alert: dict) -> list:
    """The conversation a run starts from."""
    return [SystemMessage(CONTROLLER_SYSTEM), HumanMessage(controller_task(alert))]


class AgentDAG:
    """The graph itself: nodes, predicates and edges.

    A DAG in the sense that matters here — a fixed structure, declared once and
    never rewritten. It is not acyclic: `tools -> controller` is the cycle that
    makes the agent reactive. What is fixed is the definition; which path a run
    takes through it is decided at run time by predicates over state.

    The agent that *runs* this graph is `ReactiveAgent` in reactive_agent.py.
    """

    def __init__(self, trace: Trace, config: Config = CONFIG, console=None):
        self.trace = trace
        self.config = config
        # Optional. Tokens arrive here as the model generates them; a node is
        # the only place that sees them. The trace records the finished call,
        # not the keystrokes.
        self.console = console

    @staticmethod
    def stalled(observations: list, limit: int) -> bool:
        """True when the last `limit` observations said nothing new.

        Compares what came back, not what was asked. The obvious rule — the
        same call repeated — looks equivalent and is not: a model that varies
        its pattern slightly on each attempt evades it completely while making
        no progress at all. Observed on the isolated fixture, where ten
        consecutive searches with six distinct patterns all returned "no
        matches" and the rule never fired; only the budget stopped the run.

        Comparing results subsumes the identical-call case, because identical
        calls to deterministic tools return identical results.

        This is ReAct's own reported failure mode (§3.3, Table 2): a model
        repeating an action that is getting it nowhere.
        """
        if len(observations) < limit:
            return False
        recent = [o.get("result") for o in observations[-limit:]]
        return all(result == recent[0] for result in recent)

    def controller(self, state: AgentState) -> dict:
        """The one node where a model decides what happens next."""
        # Budget is checked BEFORE the call is issued. Checked afterwards it
        # would be a post-mortem, not a control.

        # Check if the budget has been exhausted before issuing a new model call.
        if state["model_calls"] >= self.config.budget:
            self.trace.event(EventKind.ROUTING, decision="budget_exhausted", by="runtime",
                        model_calls=state["model_calls"], budget=self.config.budget)
            return {"status": TerminalStatus.BUDGET_EXHAUSTED}

        # If we repeat with no progress, stop
        if self.stalled(state["observations"], self.config.no_progress_steps):
            self.trace.event(EventKind.ROUTING, decision="no_progress", by="runtime",
                        repeated=self.config.no_progress_steps)
            return {"status": TerminalStatus.NO_PROGRESS}

        # Otherwise, issue a new model call
        model = build_model(self.config).bind_tools(ToolBundle.get_all_tools())
        if self.console:
            self.console.calling(len(state["messages"]))
        reply = (self._stream(model, state["messages"]) if self.config.stream
                 else model.invoke(state["messages"]))

        counts = reply.usage_metadata or {}
        # Two counters taken; the provider's total_tokens deliberately dropped.
        usage = {"input": counts.get("input_tokens", 0),
                 "output": counts.get("output_tokens", 0)}
        thinking = (reply.additional_kwargs or {}).get("reasoning_content", "")
        self.trace.event(EventKind.MODEL_CALL, role="controller", model=self.config.model,
                    tool_calls=[c["name"] for c in (reply.tool_calls or [])],
                    thinking=thinking, usage=usage)
        return {"messages": [reply], "model_calls": 1, "usage": usage}

    def _stream(self, model, messages):
        """Consume the response as it is generated, narrating as it goes.

        A model call takes 10 to 30 seconds. Logged only on completion it is a
        silent gap; streamed, it is the part of the run worth watching. Chunks
        are summed back into one message, so everything downstream — tool calls,
        usage counters — is identical to a plain invoke.
        """
        reply = None
        for chunk in model.stream(messages):
            reply = chunk if reply is None else reply + chunk
            if self.console:
                thought = (chunk.additional_kwargs or {}).get("reasoning_content")
                if thought:
                    self.console.thinking(thought)
        if self.console:
            self.console.thinking_done()
        return reply

    def tools(self, state: AgentState) -> dict:
        """Runtime work: execute what the model asked for, observe the result.

        Every tool call gets a ToolMessage back, including a failed one. Leave
        one out and the next model call is malformed.
        """
        dispatcher = Dispatcher(Path(state["workspace"]), self.trace,
                                config=self.config)
        observations, replies = [], []
        for call in state["messages"][-1].tool_calls:
            observation = dispatcher({"name": call["name"], "args": call["args"]})
            observations.append(observation.as_dict())
            replies.append(ToolMessage(content=observation.result,
                                       tool_call_id=call["id"],
                                       name=call["name"]))
        return {"observations": observations, "messages": replies}

    def submit(self, state: AgentState) -> dict:
        """Runtime work: the three mechanical checks.

        No model judges acceptance here. That is V3's job, and keeping this
        node mechanical is what makes V3's validator worth adding.
        """
        submission = Submission(Path(state["workspace"]), state["alert"], self.config)
        checks = submission.check()
        patch = submission.diff()
        self.trace.event(EventKind.STATE_CHANGE, node="submit", checks=checks.as_dict())

        if checks.passed:
            self.trace.event(EventKind.ROUTING, decision="accepted", by="runtime")
            return {"candidate_patch": patch, "status": TerminalStatus.ACCEPTED}
    
        self.trace.event(EventKind.ROUTING, decision="rejected", by="runtime",
                    reasons=checks.reasons)
        return {"candidate_patch": patch,
                "messages": [HumanMessage(rejected_feedback(checks.reasons))]}

    def after_controller(self, state: AgentState) -> str:
        """Continue, or submit. The model picks by calling a tool or not."""
        if state["status"] != RUNNING:
            return END
        if state["messages"][-1].tool_calls:
            self.trace.event(EventKind.ROUTING, decision="tools", by="model")
            return "tools"
        self.trace.event(EventKind.ROUTING, decision="submit", by="model")
        return "submit"

    def after_submit(self, state: AgentState) -> str:
        return END if state["status"] != RUNNING else "controller"

    def compile(self):
        """Wire and compile the graph."""
        graph = StateGraph(AgentState)
        graph.add_node("controller", self.controller)
        graph.add_node("tools", self.tools)
        graph.add_node("submit", self.submit)

        graph.add_edge(START, "controller")
        graph.add_conditional_edges("controller", self.after_controller,
                                    {"tools": "tools", "submit": "submit", END: END})
        # The edge back. This one line is what makes V1 reactive.
        graph.add_edge("tools", "controller")
        graph.add_conditional_edges("submit", self.after_submit,
                                    {"controller": "controller", END: END})
        return graph.compile()


def build_graph(trace: Trace, config: Config = CONFIG, console=None):
    """Compile V1's graph. Kept as a function so callers need not know the class."""
    return AgentDAG(trace, config, console).compile()
