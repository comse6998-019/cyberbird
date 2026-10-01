"""The event-driven agent: steps, and the events that connect them.

A step accepts the event types in its signature and emits the ones in its return
annotation. No step names another; this table is the whole wiring.

    |--------------------------------------|------------|--------------------------------------------|------------|
    | Input events                         | Step       | Output events                              | Decided by |
    |======================================|============|============================================|============|
    | RunRequested                         | start      | ScanRequested                              | runtime    |
    | ScanRequested                        | scan       | N × AlertFound, StopEvent                  | runtime    |
    | AlertFound, AlertDone                | gate       | AlertStarted (one at a time), StopEvent    | runtime    |
    | AlertStarted, Stalled                | plan       | PlanCreated, AlertDone                     | model      |
    | PlanCreated, Observed, ChecksFailed  | controller | ToolsRequested, Submitted, AlertDone       | model      |
    | ToolsRequested                       | tools      | Observed, Stalled, AlertDone               | runtime    |
    | Submitted                            | submit     | ChecksFailed, V × ValidateRequested        | runtime    |
    | ValidateRequested                    | validate   | Verdict                                    | model      |
    | V × Verdict                          | judge      | AlertDone                                  | runtime    |
    |--------------------------------------|------------|--------------------------------------------|------------|

An alert's life:

    scan --> AlertFound --> gate --> AlertStarted --> plan --> PlanCreated --> controller
    controller --> ToolsRequested --> tools --> Observed --> controller         (the loop)
                                           |--> Stalled  --> plan               (replan once)
    controller --> Submitted --> submit --> V × ValidateRequested --> validate  (fan-out)
    V × Verdict --> judge --> AlertDone --> gate --> the next alert, or StopEvent

How an alert ends (`AlertDone.status`):

    accepted          every validator accepted the patch                   (judge)
    rejected          at least one validator refused it                    (judge)
    no_progress       stalled again after its one replan                   (tools)
    budget_exhausted  `config.budget` model calls spent                    (plan, controller)

Four rules the code below keeps:

1. Every event after `AlertFound` carries `alert_id`, the correlation key. Data
   that builds up over an alert lives in `RunState.alerts[alert_id]`.
2. All writes go through `ctx.store.edit_state()`. It locks the whole store, so
   slow work (model calls, copying trees, Bandit) happens outside it.
3. Write state first, then emit the event that lets other steps read it.
4. Nothing on `self` may share a name with a step: `self.tools` would hide the
   `tools` step and the workflow would refuse to start.
"""
from __future__ import annotations

import asyncio
import random
from enum import StrEnum

from pydantic import BaseModel, Field
from workflows import Context, Workflow, step
from workflows.events import StopEvent

from cyberbird.event_driven.config import CONFIG, Config
from cyberbird.event_driven.llm import build_model, is_rate_limited, json_schema_format
from cyberbird.event_driven.events import (
    AlertDone, AlertFound, AlertStarted, ChecksFailed, ModelCalled, Observed,
    PlanCreated, RunRequested, ScanRequested, Stalled, Submitted, Thinking,
    ToolsRequested, ValidateRequested, Verdict,
)
from cyberbird.event_driven.state import AlertState, RunState
from cyberbird.event_driven.tools.scan import Scan
from cyberbird.event_driven.workspace import Workspaces

from langchain_core.messages import (
    HumanMessage, SystemMessage, ToolMessage, message_chunk_to_message,
)
from pydantic import ValidationError
from cyberbird.event_driven.prompts import (
    CONTROLLER_SYSTEM, PLANNER_SYSTEM, alert_text, plan_message, planner_task, replan_task,
)
from cyberbird.event_driven.tools.base import Observation
from cyberbird.event_driven.tools.files import Edit, ReadFile, Search

# The controller's tools. Classes, not instances: `schema()` needs no workspace,
# and the `tools` step binds instances to each alert's workspace.
WORKSPACE_TOOLS = (ReadFile, Search, Edit)


class PlanStep(BaseModel):
    goal: str = Field(description="what to do, specific enough to act on")
    evidence: str = Field(description="the observation that shows the step is done")


class Plan(BaseModel):
    """A short plan for fixing one alert."""
    steps: list[PlanStep]


class Lens(StrEnum):
    """Different lenses for validation."""
    CORRECTNESS = "correctness"
    BEHAVIOUR = "behaviour"
    SCOPE = "scope"


class EventDrivenAgent(Workflow):

    def __init__(self, workspaces: Workspaces, config: Config = CONFIG, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.validators = config.validators
        self.workspaces = workspaces 
        # The runtime's tools. No model is ever bound to these. Not `self.tools`:
        # that would shadow the `tools` step, and the workflow would lose it.
        self.runtime_tools = {t.name: t for t in (Scan(),)}

        # One model, two roles. Not `self.plan` / `self.controller`: those names
        # are steps, and an attribute would shadow them just as `self.tools` would.
        model = build_model(config)
        # The planner replies with JSON matching Plan. Bound as a response format,
        # not wrapped in a parser, so its reply streams like the controller's.
        self.planner_model = model.bind(response_format=json_schema_format(Plan))
        self.controller_model = model.bind_tools([t.schema() for t in WORKSPACE_TOOLS])

    async def __call__(self, **start) -> dict:
        return await self.run(**start)

    async def _admit(self, ctx: Context[RunState], alert_id: str) -> bool:
        """Reserve one model call for this alert, or refuse.

        Checked BEFORE the call; checked afterwards it would only report that
        the budget was already exceeded. Check and reservation happen under one
        lock, so steps admitted at the same moment (the validators) cannot both
        take the last call.
        """
        async with ctx.store.edit_state() as s:
            a = s.alerts[alert_id]
            if a.model_calls >= self.config.budget:
                return False
            a.model_calls += 1
            return True

    @staticmethod
    def _counts(reply) -> dict:
        """One reply's token counts, as {"input": n, "output": n}."""
        counts = reply.usage_metadata or {}
        return {"input": counts.get("input_tokens", 0),
                "output": counts.get("output_tokens", 0)}

    @classmethod
    def _add_usage(cls, a: AlertState, reply) -> None:
        """Add one reply's token counts to the alert's totals. Call inside edit_state."""
        for key, n in cls._counts(reply).items():
            a.usage[key] += n

    RETRIES = 5

    async def _think(self, ctx: Context[RunState], alert_id: str, role: str,
                     model, messages: list):
        """One model call, streamed. Every model call in this agent goes through here.

        Each piece of reasoning is published as a `Thinking` event the moment it
        arrives, so the console can show the model thinking. The reply is returned
        whole, and one `ModelCalled` (usage, tool calls, the full reasoning) goes
        to the trace. Neither event is routed: no step receives them.

        A rate-limited call (429) starts again from the beginning, after a pause
        that doubles each time. A retry is not a new decision, so `_admit` is not
        asked again and the budget is not charged twice.
        """
        for attempt in range(self.RETRIES):
            reply, thinking = None, []
            try:
                async for chunk in model.astream(messages):
                    reply = chunk if reply is None else reply + chunk
                    thought = (chunk.additional_kwargs or {}).get("reasoning_content")
                    if thought:
                        thinking.append(thought)
                        ctx.write_event_to_stream(
                            Thinking(alert_id=alert_id, role=role, text=thought))
                break
            except Exception as exc:
                if not is_rate_limited(exc) or attempt == self.RETRIES - 1:
                    raise
                await asyncio.sleep(2 ** attempt + random.random())
        if reply is None:
            raise RuntimeError(f"{role} returned an empty stream")

        # The summed chunks, as the plain message the conversation expects.
        reply = message_chunk_to_message(reply)
        ctx.write_event_to_stream(ModelCalled(
            alert_id=alert_id, role=role, usage=self._counts(reply),
            tool_calls=[c["name"] for c in reply.tool_calls], thinking="".join(thinking)))
        return reply

    @step
    async def start(self, ctx: Context[RunState], ev: RunRequested) -> ScanRequested:
        """This represents the start of the workflow.
        
        It takes as input a `RunRequested` event and produces a `ScanRequested` event as output.
        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | RunRequested                         | start      | ScanRequested                              |
        |--------------------------------------|------------|--------------------------------------------|
        
        `ScanRequested` is the output event produced by this step. And that's because of how we defined it. 
        """
        async with ctx.store.edit_state() as s:
            s.filters = {"rule": ev.rule, "alert_id": ev.alert_id, "limit": ev.limit}
        # The runtime decides to scan; no model is asked.
        return ScanRequested(root=str(self.config.fixture))

    @step
    async def scan(self, ctx: Context[RunState], ev: ScanRequested
                   ) -> AlertFound | StopEvent | None:
        """The scan step performs the actual scanning using the runtime tools.
        
        It takes as input a `ScanRequested` event and produces an `AlertFound`, `StopEvent`, or `None` as output.
        
        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | ScanRequested                        | scan       | AlertFound, StopEvent, None                |
        |--------------------------------------|------------|--------------------------------------------|
        
        Output events produced by this step are `AlertFound`, `StopEvent`, or `None`. This depends on the result of the scan operation.
        Only one of the output events will be produced for each invocation of this step and particularly the `AlertFound` event will be 
        produced for each alert detected. You'll notice that this is trigger the event-driven flow of the workflow, i.e., gate @step
        """
        
        # Bandit blocks for ~2 s; in a thread, the event loop keeps running.
        obs = await asyncio.to_thread(self.runtime_tools["scan"], {"root": ev.root})
        if not obs.ok:
            return StopEvent(result={"error": obs.result})
        state = await ctx.store.get_state()
        alerts = self.select(obs.value, **state.filters)
        if not alerts:
            return StopEvent(result={})       # nothing will ever reach the gate
        async with ctx.store.edit_state() as s:
            s.expected = len(alerts)          # set BEFORE the first event is sent
        for alert in alerts:
            ctx.send_event(AlertFound(alert=alert))
        return None

    @step(num_workers=1)
    async def gate(self, ctx: Context[RunState], ev: AlertFound | AlertDone
                   ) -> AlertStarted | StopEvent | None:
        """The gate step controls the flow of alerts based on num_workers. It releases 
        one alert at a time to the next step and keeps track of the current alert being 
        processed. It also handles the completion of alerts and stops the workflow when 
        all expected alerts have been processed.
        
        It takes as input an `AlertFound` or `AlertDone` event and gates the flow of alerts based on the 
        current state and number of workers.
        
        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | AlertFound, AlertDone                | gate       | AlertStarted, StopEvent, None              |
        |--------------------------------------|------------|--------------------------------------------|  
        
        It produces an `AlertStarted`, `StopEvent`, or `None` as output. None indicates that the step is either busy 
        or waiting for more alerts.
        """
        
        # If the Alerts is done processing, we may need to stop the workflow. If the alert is done, close its workspace. 
        # If it is not accepted, keep the workspace for inspection.
        if isinstance(ev, AlertDone):
            await asyncio.to_thread(self.workspaces.close, ev.alert_id, keep=ev.status != "accepted")
            
        async with ctx.store.edit_state() as s:
            if isinstance(ev, AlertFound):
                s.queue.append(ev.alert) # New work found, enqueue.
            else:
                s.results[ev.alert_id] = ev.status # done: record the result of the completed alert
                s.current = None                   # free the current alert slot
            if len(s.results) == s.expected: # all expected alerts have been processed
                return StopEvent(result=dict(s.results)) # signal to stop the workflow as all expected alerts are processed
            if s.current is not None or not s.queue:
                return None                   # busy, or waiting for more alerts
            
            # Dequeue the next alert and mark it as the current alert being processed.
            alert = s.queue.pop(0)
            s.current = alert["alert_id"]
        
        # Save the workspace path for the alert and create its workspace. Save it in the state so the 
        # next event can access it and use it's AlertState for processing.
        root = await asyncio.to_thread(self.workspaces.open, alert)
        async with ctx.store.edit_state() as s:
            s.alerts[alert["alert_id"]] = AlertState(alert=alert, workspace=str(root))
        
        # Return an event indicating that the alert has started processing.
        return AlertStarted(alert_id=alert["alert_id"], alert=alert)

    @step
    async def plan(self, ctx: Context[RunState], ev: AlertStarted | Stalled
                   ) -> PlanCreated | AlertDone:
        """The plan step asks a model for a plan to fix the alert, or for a new one when the work stalls.

        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | AlertStarted                         | plan       | PlanCreated, AlertDone                     |
        | Stalled                              | plan       | PlanCreated (revised=True), AlertDone      |
        |--------------------------------------|------------|--------------------------------------------|

        Two triggers, one step. `AlertStarted` plans from the alert alone, and also starts the
        controller's conversation: system prompt, alert, plan. `Stalled` sends the plan that
        stopped working, why it stopped, and the last result, and asks for a different plan.
        It also moves `replanned_at`, so the stall check only looks at work done under the new plan.

        The plan reaches the controller as a HumanMessage: input from someone else, not
        something the controller said. A planner that returns no plan does not end the alert;
        the controller works without one.

        AlertDone(status="budget_exhausted") when the alert has no model calls left.
        """
        if not await self._admit(ctx, ev.alert_id):
            return AlertDone(alert_id=ev.alert_id, status="budget_exhausted")

        state = await ctx.store.get_state()
        current = state.alerts[ev.alert_id]
        revised = isinstance(ev, Stalled)
        
        task = (replan_task(current.alert, current.plan, ev.reason, ev.last_result)
                if revised else planner_task(current.alert))

        # Call the planner model with the task to generate a structured plan that can be decomposed into plan
        # steps as in PlanStep and Plan.
        reply = await self._think(ctx, ev.alert_id, "planner", self.planner_model,
                                  [SystemMessage(PLANNER_SYSTEM), HumanMessage(task)])
        # A planner that fails to plan must not end the alert: work without a plan.
        try:
            steps = [s.model_dump() for s in Plan.model_validate_json(reply.content).steps]
        except ValidationError:
            steps = []

        async with ctx.store.edit_state() as s:
            a = s.alerts[ev.alert_id]
            a.plan = steps
            if revised:
                # Move the stall window to start here. Otherwise the three identical
                # results that caused this revision are still the latest three, and
                # the alert gives up on the very next turn.
                a.replanned_at = len(a.observations)
            else:
                a.messages = [SystemMessage(CONTROLLER_SYSTEM),
                              HumanMessage(alert_text(a.alert))]
            # The plan reaches the controller as input from someone else,
            # not as something it said itself.
            a.messages.append(HumanMessage(plan_message(steps, revised)))
            self._add_usage(a, reply)
        return PlanCreated(alert_id=ev.alert_id, steps=steps, revised=revised)

    @step(num_workers=1)
    async def controller(self, ctx: Context[RunState],
                         ev: PlanCreated | Observed | ChecksFailed
                         ) -> ToolsRequested | Submitted | AlertDone:
        """The controller step asks a model what to do next for the alert, given everything so far.

        It may request tool calls, submit its patch, or end the alert when the budget is spent.
        `num_workers=1`: one alert's turns are strictly one after another.
        
        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | PlanCreated                          | controller | ToolsRequested, Submitted, AlertDone       |
        | Observed                             | controller | ToolsRequested, Submitted, AlertDone       |
        | ChecksFailed                         | controller | ToolsRequested, Submitted, AlertDone       |
        |--------------------------------------|------------|--------------------------------------------|
        
        a.messages holds the conversation so far: the system prompt, the alert, the plan,
        and every reply and tool result since. One model call per turn reads all of it.

        The model decides the routing: a reply with tool calls becomes `ToolsRequested`;
        a reply without any is the model saying it is done, and becomes `Submitted`.

        AlertDone(status="budget_exhausted") when the alert has no model calls left.
        """
        if not await self._admit(ctx, ev.alert_id):
            return AlertDone(alert_id=ev.alert_id, status="budget_exhausted")

        state = await ctx.store.get_state()
        reply = await self._think(ctx, ev.alert_id, "controller", self.controller_model,
                                  state.alerts[ev.alert_id].messages)

        # Stored before ToolsRequested goes out: the tools step answers each of
        # its call ids, and the next model call needs the reply they answer.
        async with ctx.store.edit_state() as s:
            a = s.alerts[ev.alert_id]
            a.messages.append(reply)
            self._add_usage(a, reply)

        if reply.tool_calls:
            return ToolsRequested(alert_id=ev.alert_id, calls=reply.tool_calls)
        return Submitted(alert_id=ev.alert_id)

    @step
    async def tools(self, ctx: Context[RunState], ev: ToolsRequested
                    ) -> Observed | Stalled | AlertDone:
        """Tools step: Executes the requested tool calls from the controller, then judges whether the work is still moving.

        One of the possible outcomes of the controller is to request tool calls, which this step executes.

        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | ToolsRequested                       | tools      | Observed, Stalled, AlertDone               |
        |--------------------------------------|------------|--------------------------------------------|

        Each call runs in the alert's own workspace, through a tool bound to that workspace. A failed
        or unknown call is an observation, not a crash, and every call gets a ToolMessage reply: leave
        one out and the model's next call is malformed.

        One of three possible outcomes will occur after executing the tool calls: Observed, Stalled, or AlertDone.

        Observed:  the work is moving. Back to the controller for its next turn.
        Stalled:   the last `no_progress_steps` results were identical. The plan is judged wrong,
                   not the alert hopeless: back to `plan` for a different plan. Happens once.
        AlertDone: stalled again after that replan, status "no_progress". The alert ends.
        """
        
        state = await ctx.store.get_state()
        root = state.alerts[ev.alert_id].workspace
        # The same tuple the controller's schemas come from, so they cannot disagree.
        toolbox = {cls.name: cls(root) for cls in WORKSPACE_TOOLS}

        observations, replies = [], []
        for call in ev.calls:
            tool = toolbox.get(call["name"])
            obs = (await asyncio.to_thread(tool, call["args"]) if tool else
                   Observation(call["name"], call["args"], ok=False,
                               result=f"unknown tool {call['name']!r}"))
            observations.append(obs.as_dict())
            # Every call gets a reply, a failed one included. Leave one out
            # and the model's next call is malformed.
            replies.append(ToolMessage(content=obs.result, tool_call_id=call["id"],
                                       name=call["name"]))

        async with ctx.store.edit_state() as s:
            a = s.alerts[ev.alert_id]
            a.observations += observations
            a.messages += replies
            window = a.observations[a.replanned_at:]
            revised = a.replanned_at > 0

        limit = self.config.no_progress_steps
        if self.stalled(window, limit):
            if revised:
                return AlertDone(alert_id=ev.alert_id, status="no_progress",
                                 reason=f"stalled again after replanning ({limit} identical results)")
            return Stalled(alert_id=ev.alert_id, repeated=limit,
                           reason=f"{limit} consecutive tool calls returned the same result",
                           last_result=window[-1]["result"])
        return Observed(alert_id=ev.alert_id, observations=observations)

    @step
    async def submit(self, ctx: Context[RunState], ev: Submitted
                     ) -> ChecksFailed | ValidateRequested | None:
        """The submit step checks the patch, then fans it out to the validators. (Stub: no checks yet.)

        |--------------------------------------|------------|--------------------------------------------|
        | Input events                         | Step       | Output events                              |
        |======================================|============|============================================|
        | Submitted                            | submit     | ChecksFailed, V × ValidateRequested, None  |
        |--------------------------------------|------------|--------------------------------------------|

        The fan-out: one `ValidateRequested` per lens, sent with `ctx.send_event` rather than
        returned, because a step can return only one event. It still lists `ValidateRequested`
        in its return annotation: the workflow checks at startup that every event it emits has
        a consumer. It returns None; the events it sent carry the run forward.

        ChecksFailed (once the checks are real): back to the controller with the reasons.
        """
        for lens in list(Lens)[:self.validators]:
            ctx.send_event(ValidateRequested(alert_id=ev.alert_id, lens=lens,
                                             patch="", checks={"passed": True}))
        return None

    # @step(num_workers=1)          #  No parallelism for validation -- should take 6 seconds (2 simulated seconds per lens, 3 lenses)
    @step(num_workers=len(Lens))    #  Maximum parallelism for validation
    async def validate(self, ev: ValidateRequested) -> Verdict:
        await asyncio.sleep(2)  # Simulate async work (stub. will be replaced)
        return Verdict(alert_id=ev.alert_id, lens=ev.lens, accepted=True,
                       reasons=["stub"], decided_by="stub")

    @step
    async def judge(self, ctx: Context[RunState], ev: Verdict) -> AlertDone | None:
        # Not keyed by alert: safe only because the gate lets one alert through
        # at a time. Run alerts concurrently and verdicts from two alerts mix.
        verdicts = ctx.collect_events(ev, [Verdict] * self.validators)
        if verdicts is None:
            return None
        accepted = all(v.accepted for v in verdicts)
        return AlertDone(alert_id=ev.alert_id,
                         status="accepted" if accepted else "rejected")
    
    # --- Static Helpers --- #
    @staticmethod
    def select(alerts: list[dict], rule=None, alert_id=None, limit=None) -> list[dict]:
        """The queue's filters. Runtime policy, not scanning."""
        picked = [a for a in alerts
                if (rule is None or a["rule"] == rule)
                and (alert_id is None or a["alert_id"] == alert_id)]
        return picked[:limit] if limit else picked

    @staticmethod
    def stalled(window: list[dict], limit: int) -> bool:
        """True when the last `limit` observations all returned the same result.
        Compares what came back, not what was asked, so varying the pattern while
        getting "no matches" every time is still a stall. A successful edit is
        progress by definition, even though two edits to one file render alike.
        """
        if len(window) < limit:
            return False
        recent = window[-limit:]
        if any(o["ok"] and o["tool"] == "edit" for o in recent):
            return False
        return len({o["result"] for o in recent}) == 1
    
