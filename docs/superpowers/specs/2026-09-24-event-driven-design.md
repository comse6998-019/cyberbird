# Event-driven patching agent: design

Status: in progress. Sections are added as they are agreed.

## Goal

A standalone lecture on event-driven agent orchestration, built on LlamaIndex
Workflows. The agent scans the fixture and turns each alert into an event. A
gate step lets one alert through at a time. Each alert is planned, worked,
checked and judged by up to three validators that run concurrently.

This lecture's design stands on its own. It borrows no behaviour from the
graph agents in `reactive/` and `plan_and_validation/`.

What the lecture shows:

- **Routing by type.** A step accepts the event types in its signature and
  emits the ones in its return annotation. No step names another.
- **Fan-out and fan-in.** `ctx.send_event` sends N events at once and
  `ctx.collect_events` waits for all N. The validators use this.
- **A scheduler step.** `gate` holds the alert queue and releases one alert
  at a time.
- **Correlation IDs.** Every event carries `alert_id`, and state is keyed by
  it, because one workflow run handles many alerts.
- **Resource lifetime across steps.** A `with` block cannot span steps, so the
  runner owns an `ExitStack` as the safety net.

## Decisions

| Question | Decision |
|---|---|
| Framework | LlamaIndex Workflows (`llama-index-workflows`, imported as `workflows`) |
| Package | `cyberbird/event_driven/`, run as `cyberbird event-driven`. It imports nothing from the other agents. |
| Build | From an empty package, one file at a time |
| Author | The instructor writes all code. The TA writes this doc and reviews. |
| Model | OpenRouter through `llm.py`, the only module that names a provider. Key: `OPENROUTER_API_KEY` in the repo-root `.env`. |
| Tools | A `Tool` ABC in `tools/base.py`. `__call__` is a template method: it validates `Args` (Pydantic), runs `run`, and turns any exception into a failed `Observation`. `render` decides what a model sees. `schema()` feeds `bind_tools`. There is no dispatcher and no global registry: each role gets an explicit tuple of tools. |
| Scanning | One `scan` tool: Bandit plus normalization to alerts. Only the runtime calls it: once at the start, and as the rescan in `submit`. |
| Alerts | `scan` emits one `AlertFound` per alert. `gate` queues them and releases one at a time. `--limit`, `--rule` and `--alert-id` filter the queue. |
| Workspaces | One per alert, opened by `gate` and closed when the alert finishes. The runner's `ExitStack` closes any the run leaves open. |
| Validators | Three lenses, all must agree: **correctness**, **behaviour**, **scope**. `--validators 1..3` sets N. They run concurrently. |

## Setup

- Dependencies: `llama-index-workflows`, `langchain-openrouter`, `python-dotenv`.
- `max_tokens` is set in config. Without it, OpenRouter reserves credit for
  the model's whole output window, and a key with a monthly cap refuses the
  call.
- `:free` models are rate-limited (429) and ignore `seed`.

### Workflow defaults that cause problems

| Default | Problem | Do this |
|---|---|---|
| `Workflow(timeout=45.0)` | Kills a run after 45 s | Pass `timeout=None` |
| `@step(num_workers=4)` | Up to four copies of a step run at once | Set it on every step whose concurrency matters |
| `workflow.run()` is not a coroutine | It must be called inside a running loop | `async def __call__` on the agent |
| Blocking calls in an `async` step | They freeze every other step | `await asyncio.to_thread(...)` |

## Flow

```
RunRequested ─▶ start ─▶ ScanRequested ─▶ scan ─▶ N × AlertFound ─▶ gate
gate ─▶ AlertStarted ─▶ plan ─▶ PlanCreated ─▶ controller ⇄ tools
controller ─▶ Submitted ─▶ submit ─▶ ChecksFailed ─▶ controller
                                  └▶ V × ValidateRequested ─▶ validate ─▶ Verdict
V × Verdict ─▶ judge ─▶ AlertDone ─▶ gate ─▶ next AlertStarted, or StopEvent
```

Every event after `AlertFound` carries `alert_id`.

## To design

- The `RunState` keyed by alert, and the `gate` contract
- The stall and replan rule, and the budget per alert
- Trace and console: `handler.stream_events()`
- Budget and failures during the validator fan-out
