"""V2 and V3: planning, and validation.

    START -> plan -> controller -> tools -+-> controller
                        |                 |
                        |                 +-> plan      (nothing new came back)
                        v
                     submit -+-> validate -> END
                             |
                             +-> controller             (checks failed)

Five nodes. Three of them — `plan`, `controller`, `validate` — are places where a
model decides something. `tools` and `submit` are runtime work.

Two things changed from V1. Everything else in this file is plumbing that serves
them.

**`tools -> controller` is no longer unconditional.** In V1 that single edge is
what makes the agent reactive, and it always went back to the controller. Here it
chooses, and its second destination is the planner: when the last few
observations have said nothing new, the runtime decides the *plan* was wrong
rather than that the *run* is over. V1 ended such a run with `no_progress`.

**`submit` can no longer accept.** It runs the same three mechanical checks and
then routes on; only `validate` can end a run `accepted`. The claim that the
entity which proposes is not the entity which judges is a property of the wiring
here, rather than something a prompt is asked to arrange.

The graph definition is still fixed. Nothing here is rewritten between runs; the
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

from lec2_planning_and_validation_agent.config import CONFIG, Config
from lec2_planning_and_validation_agent.dispatch import Dispatcher
from lec2_planning_and_validation_agent.llm import build_model
from lec2_planning_and_validation_agent.prompts import (
    CONTROLLER_SYSTEM,
    PLANNER_SYSTEM,
    VALIDATOR_SYSTEM,
    controller_task,
    plan_message,
    planner_task,
    rejected_feedback,
    replan_task,
    validator_task,
)
from lec2_planning_and_validation_agent.state import RUNNING, AgentState
from lec2_planning_and_validation_agent.submit import Submission
from lec2_planning_and_validation_agent.tools import ToolBundle
from lec2_planning_and_validation_agent.trace import EventKind, TerminalStatus, Trace


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

        A successful `edit` anywhere in the window means no stall, whatever the
        results say. An edit changes the working tree, so it is progress by
        definition — but the dispatcher renders it as "path=..., replaced=1",
        which is byte-identical for any two successful edits to the same file.
        Three edits to one file would otherwise be indistinguishable from three
        repetitions of one useless call, and a fix that needs edits in several
        places is exactly when that arises. Observed on the path-traversal alert,
        where two consecutive edits to the same file rendered identically.

        This is ReAct's own reported failure mode (§3.3, Table 2): a model
        repeating an action that is getting it nowhere.
        """
        if len(observations) < limit:
            return False
        recent = observations[-limit:]
        if any(o.get("ok") and o.get("tool") == "edit" for o in recent):
            return False
        results = [o.get("result") for o in recent]
        return all(result == results[0] for result in results)

    def _admit(self, state: AgentState) -> dict | None:
        """The budget check every model node makes before it calls.

        Checked BEFORE the call is issued; checked afterwards it would be a
        post-mortem rather than a control. Returns the terminal update when the
        budget is spent, or None to proceed.

        Three nodes now spend model calls, so this cannot live in one of them.
        """
        if state["model_calls"] >= self.config.budget:
            self.trace.event(EventKind.ROUTING, decision="budget_exhausted", by="runtime",
                        model_calls=state["model_calls"], budget=self.config.budget)
            return {"status": TerminalStatus.BUDGET_EXHAUSTED}
        return None

    def _call(self, model, messages: list, role: str):
        """Issue one model call, narrate it, and record it under its role.

        Shared by all three model nodes, so the token counters and the
        `model_call` event cannot drift apart between them. `role` is what tells
        a trace reader which of the three actors spoke.
        """
        if self.console:
            self.console.calling(len(messages), role=role)
        reply = (self._stream(model, messages) if self.config.stream
                 else model.invoke(messages))
        usage = self._usage(reply)
        self.trace.event(EventKind.MODEL_CALL, role=role, model=self.config.model,
                    tool_calls=[c["name"] for c in (reply.tool_calls or [])],
                    thinking=(reply.additional_kwargs or {}).get("reasoning_content", ""),
                    usage=usage)
        return reply

    @staticmethod
    def _usage(reply) -> dict:
        """Two counters taken; the provider's total_tokens deliberately dropped."""
        counts = reply.usage_metadata or {}
        return {"input": counts.get("input_tokens", 0),
                "output": counts.get("output_tokens", 0)}

    def planning_window(self, state: AgentState) -> list:
        """The observations made under the plan currently in force.

        Everything before `replanned_at` was answered by a plan that has since
        been replaced, and judging a new plan by the old plan's results is how a
        run convicts itself of a failure it has already corrected.

        The window moves rather than the history shrinking, because
        `observations` accumulates with `operator.add` and so cannot be cleared.
        Without it, the three identical observations that triggered a revision
        are still the most recent three immediately after it, and the run would
        revise and then declare no progress on the same turn.
        """
        return state["observations"][state.get("replanned_at", 0):]

    def plan(self, state: AgentState) -> dict:
        """Propose subgoals, or revise them when the work stops moving.

        Two triggers, and they have to be named apart. At START it plans from the
        alert alone. Reached again from `tools`, it revises: the last few
        observations said nothing new, so the runtime has decided that the *plan*
        is wrong rather than that the *run* is over.

        The plan reaches the executor as a HumanMessage, not as the planner's own
        reply. The planner is a different actor and its output arrives to the
        executor as input; splicing the planner's AIMessage into the controller's
        conversation would make the executor look like it planned its own work.
        """
        spent = self._admit(state)
        if spent:
            return spent

        failed = state.get("failed_step")
        task = (replan_task(state["alert"], state.get("plan") or [], failed) if failed
                else planner_task(state["alert"]))
        model = build_model(self.config).bind_tools(ToolBundle.planner_tools())
        reply = self._call(model, [SystemMessage(PLANNER_SYSTEM), HumanMessage(task)],
                           role="planner")
        steps = self._steps(reply)

        # The full steps go into the event, not just their goals: the console
        # renders the plan from this record, so a recorded run can be narrated
        # again without the objects that produced it.
        self.trace.event(EventKind.STATE_CHANGE, node="plan", revised=bool(failed),
                    steps=steps)

        update = {"plan": steps,
                  "failed_step": None,
                  "messages": [HumanMessage(plan_message(steps, revised=bool(failed)))],
                  "model_calls": 1,
                  "usage": self._usage(reply)}
        if failed:
            # The planning window closes here. Every observation so far belongs
            # to the plan being replaced, and the stall that caused this revision
            # must not go on to condemn the revision. At START this would be 0
            # anyway, which is how `replanned_at > 0` doubles as "has revised".
            update["replanned_at"] = len(state["observations"])
        return update

    @staticmethod
    def _steps(reply) -> list[dict]:
        """The planner's tool call, unpacked into numbered steps.

        `propose_plan` is the only tool bound, so a well-behaved reply carries one
        call whose `steps` is a list of "goal :: evidence" strings. A flat list of
        strings is asked for rather than a list of objects because small local
        models produce it far more reliably; the split happens here instead.

        A reply with no tool call yields no steps, and that is not fatal — the
        controller then works exactly as V1 does, without a plan. A planner that
        fails to plan must not be able to end a run.
        """
        calls = reply.tool_calls or []
        if not calls:
            return []
        steps = []
        for number, raw in enumerate(calls[0]["args"].get("steps") or [], start=1):
            goal, _, evidence = str(raw).partition("::")
            steps.append({"id": number, "goal": goal.strip(),
                          "evidence": evidence.strip()})
        return steps

    def controller(self, state: AgentState) -> dict:
        """The node where a model decides what to do next."""
        spent = self._admit(state)
        if spent:
            return spent

        # Stalling is no longer fatal on its own: `after_tools` gets first
        # refusal and sends the run back to the planner. Reaching here stalled
        # means that already happened and the revised plan fared no better.
        if self.stalled(self.planning_window(state), self.config.no_progress_steps):
            self.trace.event(EventKind.ROUTING, decision="no_progress", by="runtime",
                        repeated=self.config.no_progress_steps,
                        after_replan=state.get("replanned_at", 0) > 0)
            return {"status": TerminalStatus.NO_PROGRESS}

        model = build_model(self.config).bind_tools(ToolBundle.get_all_tools())
        reply = self._call(model, state["messages"], role="controller")
        return {"messages": [reply], "model_calls": 1, "usage": self._usage(reply)}

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

        This node also decides whether the plan is still working. A conditional
        edge can only read state, and the node that saw the observations is the
        one able to judge them — so the judgement is made here and written to
        `failed_step`, and the predicate downstream reads a single field.
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

        update = {"observations": observations, "messages": replies}

        # The reducer has not run yet, so `state["observations"]` still holds only
        # what earlier turns appended. This turn's are added by hand to ask the
        # question about the history that will exist a moment from now.
        window = (state["observations"] + observations)[state.get("replanned_at", 0):]
        if (not state.get("replanned_at")
                and self.stalled(window, self.config.no_progress_steps)):
            update["failed_step"] = {
                "reason": (f"{self.config.no_progress_steps} consecutive tool calls "
                           "returned the same observation."),
                "repeated": self.config.no_progress_steps,
                "last_result": window[-1].get("result", "")}
        return update

    def submit(self, state: AgentState) -> dict:
        """Runtime work: the three mechanical checks, and nothing beyond them.

        This node cannot accept. In V1 it could — passing the checks ended the
        run — and moving that power to `validate` is the structural claim of V3:
        the thing that proposes a patch is not the thing that judges it.

        What it still owns is the independent signal. The checks are mechanical,
        so nothing can argue them out of a verdict, and the validator is handed
        their results rather than asked to re-derive them.
        """
        submission = Submission(Path(state["workspace"]), state["alert"], self.config)
        checks = submission.check()
        patch = submission.diff()
        self.trace.event(EventKind.STATE_CHANGE, node="submit", checks=checks.as_dict())

        if checks.passed:
            # Not "accepted". This is a routing decision towards the judge, and
            # naming it accepted here would put the runtime's name on a verdict
            # it no longer makes.
            self.trace.event(EventKind.ROUTING, decision="validate", by="runtime")
            return {"candidate_patch": patch, "checks": checks.as_dict()}
    
        # Also not "rejected", which from V3 means the validator refused the
        # patch and ends a run. Failing the mechanical checks sends the work
        # back for another turn and ends nothing.
        self.trace.event(EventKind.ROUTING, decision="controller", by="runtime",
                    reason="mechanical checks failed", reasons=checks.reasons)
        return {"candidate_patch": patch,
                "checks": checks.as_dict(),
                "messages": [HumanMessage(rejected_feedback(checks.reasons))]}

    def validate(self, state: AgentState) -> dict:
        """The only node that can end a run accepted.

        `submit` proposes and this judges. It is shown the three mechanical
        checks, and by the time it runs they have all passed — a patch that fails
        them goes back to the controller instead. Being handed a clean rescan and
        asked whether that establishes a correct fix is the entire point.

        It reads `observations`, never `messages`. The trajectory is what the
        runtime saw; the conversation is what the model said about it, and a judge
        who takes the defendant's account of the evidence is not a judge.
        """
        spent = self._admit(state)
        if spent:
            return spent

        model = build_model(self.config).bind_tools(ToolBundle.validator_tools())
        task = validator_task(state["alert"], state.get("candidate_patch"),
                              state.get("checks"), state["observations"])
        reply = self._call(model, [SystemMessage(VALIDATOR_SYSTEM), HumanMessage(task)],
                           role="validator")
        verdict = self._verdict(reply)

        status = (TerminalStatus.ACCEPTED if verdict["accepted"]
                  else TerminalStatus.REJECTED)
        self.trace.event(EventKind.ROUTING, decision=str(status),
                    by=verdict["decided_by"], reasons=verdict["reasons"])
        return {"validation": verdict, "status": status,
                "model_calls": 1, "usage": self._usage(reply)}

    @staticmethod
    def _verdict(reply) -> dict:
        """The validator's tool call, unpacked — or its absence, recorded as such.

        A model that returns no `decide` call has not refused the patch; it has
        failed to answer. Recording that as the model's judgement would put words
        in its mouth, and would leave a machine failure indistinguishable in the
        trace from a considered refusal. `decided_by` is what keeps them apart.
        """
        calls = reply.tool_calls or []
        if not calls:
            return {"accepted": False,
                    "reasons": ["the validator returned no decision"],
                    "decided_by": "runtime"}
        args = calls[0]["args"]
        return {"accepted": bool(args.get("accepted")),
                "reasons": [str(reason) for reason in (args.get("reasons") or [])],
                "decided_by": "model"}

    def after_controller(self, state: AgentState) -> str:
        """Continue, or submit. The model picks by calling a tool or not."""
        if state["status"] != RUNNING:
            return END
        if state["messages"][-1].tool_calls:
            self.trace.event(EventKind.ROUTING, decision="tools", by="model")
            return "tools"
        self.trace.event(EventKind.ROUTING, decision="submit", by="model")
        return "submit"

    def after_plan(self, state: AgentState) -> str:
        """Straight to the executor, unless the budget ran out first."""
        return END if state["status"] != RUNNING else "controller"

    def after_tools(self, state: AgentState) -> str:
        """Continue, or replan. In V1 this edge was unconditional.

        V1's `tools -> controller` is the line its own docstring calls the one
        that makes the agent reactive, and it had exactly one destination. Giving
        it a second is the whole of V2's structural diff — and the new
        destination is the planner rather than an exit, so a run that has stopped
        learning gets its plan replaced before it gets abandoned.
        """
        if state.get("failed_step"):
            self.trace.event(EventKind.ROUTING, decision="plan", by="runtime",
                        reason=state["failed_step"]["reason"])
            return "plan"
        return "controller"

    def after_submit(self, state: AgentState) -> str:
        """To the judge when the checks passed, back to work when they did not."""
        if state["status"] != RUNNING:
            return END
        return "validate" if (state.get("checks") or {}).get("passed") else "controller"

    def compile(self):
        """Wire and compile the graph."""
        graph = StateGraph(AgentState)
        graph.add_node("plan", self.plan)
        graph.add_node("controller", self.controller)
        graph.add_node("tools", self.tools)
        graph.add_node("submit", self.submit)
        graph.add_node("validate", self.validate)

        # A run now begins by planning, not by acting.
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", self.after_plan,
                                    {"controller": "controller", END: END})
        graph.add_conditional_edges("controller", self.after_controller,
                                    {"tools": "tools", "submit": "submit", END: END})
        # V1 wired this one unconditionally and called it the line that makes the
        # agent reactive. Here it chooses, and its second choice is the planner.
        graph.add_conditional_edges("tools", self.after_tools,
                                    {"controller": "controller", "plan": "plan"})
        graph.add_conditional_edges("submit", self.after_submit,
                                    {"controller": "controller",
                                     "validate": "validate", END: END})
        # The only node that can accept, and the only edge to the exit that is
        # not a failure of some kind.
        graph.add_edge("validate", END)
        return graph.compile()


def build_graph(trace: Trace, config: Config = CONFIG, console=None):
    """Compile the graph. Kept as a function so callers need not know the class."""
    return AgentDAG(trace, config, console).compile()
