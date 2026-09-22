# The planning and validation agent

Run it:

```sh
cd demo
.venv/bin/python -m lec2_planning_and_validation_agent --alert-id bc284c6b
```

That is the lecture's worked example: Bandit `B608`, SQL injection at
`testcode/BenchmarkTest00283.py:46`. It prints each turn as it happens.

This package holds **two lecture segments in one graph**. V2 adds the planner
and in-run revision; V3 adds the validator. Both are always compiled, so one run
shows both.

## Before you run anything in front of a room

Three things will bite, in order of likelihood.

1. **Run one agent at a time.** Ollama serialises requests. A second run does
   not fail — it sits there looking hung, and so does the first.

2. **Never `Ctrl-C` a run mid-generation.** It wedges the model runner, and
   every later run hangs with no error. If that happens, kill the runner process
   so `ollama serve` respawns it:

   ```sh
   pgrep -f 'ollama runner' | xargs kill
   ```

3. **Tell a wedged Ollama from a slow one with one command.** This returns in
   well under a second when healthy. If it hangs, Ollama is wedged, not thinking.

   ```sh
   curl -s -m 5 localhost:11434/api/tags | head -c 60
   ```

## Check the machine is ready

1. Confirm the model is pulled. The run needs `qwen3.8`.

   ```sh
   ollama list
   ```

2. Confirm the alert queue exists. It should report 720 alerts.

   ```sh
   .venv/bin/python -m lec2_planning_and_validation_agent.cli --list
   ```

If step 2 fails, rebuild it with `.venv/bin/python -m intake.build_findings`.

## Read the output

Colour answers the question every version of the agent is built around: **who
decided this?**

| Colour | Meaning |
|---|---|
| Cyan | A model decided |
| Yellow | The model's own reasoning, streamed |
| White | The runtime acted |
| Green | It worked |
| Red | It was refused, or it failed |

Each line carries elapsed time. A `MODEL` line costs about 10 seconds; a `TOOL`
line is near-instant. The gap is the point.

```
   3.1s MODEL    planner → propose_plan      in=410 out=88
   3.1s PLAN     3 steps
                   1. Read the flagged line
                      evidence: the f-string that builds the query
  14.2s MODEL    controller → search         in=980 out=41
  14.2s ROUTE    → tools                     decided by model
  14.2s TOOL →   search(pattern='cur.execute\(sql,')
  14.4s TOOL ←   search ok                   10 chars
```

Three roles now appear on `MODEL` lines — `planner`, `controller`, `validator` —
and they are the same model under different instructions, bound to different
tools.

A run ends with one of six terminal statuses: `accepted`, `rejected`,
`unresolved`, `budget_exhausted`, `no_progress`, `error`.

## Watch for the two moments the segments are about

**The replan.** When three consecutive tool calls return the same observation,
the runtime decides the *plan* is wrong rather than that the *run* is over:

```
  91.7s ROUTE    → plan   3 consecutive tool calls returned the same observation.
  99.2s MODEL    planner → propose_plan
  99.2s PLAN ↻   3 steps — revised
```

In V1 that same condition ended the run with `no_progress`. A run gets exactly
one revision; stall again afterwards and it terminates as V1 did.

**The verdict.** `submit` can no longer accept. It runs the three mechanical
checks and hands them to the validator, which is the only node that can end a
run `accepted`:

```
 106.8s CHECK    applies=True touches_line=True rescan_clean=True
 106.8s ROUTE    → validate                   decided by runtime
 118.0s VERDICT  rejected                     decided by model
                   - the placeholder is bound but bar is still interpolated
```

A clean rescan reaching a `rejected` verdict is the segment's whole argument.

## Choose what to run

Select one alert by id, or by location:

```sh
.venv/bin/python -m lec2_planning_and_validation_agent --alert-id be043aa4
.venv/bin/python -m lec2_planning_and_validation_agent --location testcode/BenchmarkTest00283.py:46
```

Browse the queue first if you want a different weakness:

```sh
.venv/bin/python -m lec2_planning_and_validation_agent.cli --category weakrand
```

Three that worked for V1, for a live demo:

| Alert | Weakness | V1 calls |
|---|---|---|
| `bc284c6b` | SQL injection — the worked example | 5 |
| `be043aa4` | Weak random — needs two edits in different places | 4 |
| `465a5dfe` | Weak MD5 hash | — |

Budget one planner call and one validator call on top of those.

## Flags

| Flag | Does |
|---|---|
| `--alert-id ID` | Pick the alert by id |
| `--location FILE:LINE` | Pick it by source location instead |
| `--budget N` | Model-call ceiling, checked before each call. Default 40 |
| `--model NAME` | Any provider, e.g. `anthropic:claude-opus-5` |
| `--seed N` | Sampling seed. Default 0 |
| `--trace PATH` | Where to write the trace |
| `--quiet` | Print only the final report |

## Show the failsafe

Set the budget to zero. The run ends before it issues a single model call, which
is what "checked before the call, not after" means. All three model nodes make
that check, so the planner is stopped too.

```sh
.venv/bin/python -m lec2_planning_and_validation_agent --alert-id bc284c6b --budget 0
```

## Read the trace afterwards

The console is a summary. The trace is the record, one JSON event per line, at
`runs/v23-<alert_id>.jsonl`.

```sh
.venv/bin/python -m lec2_planning_and_validation_agent.trajectory \
    runs/v23-bc284c6b.jsonl --format summary
```

That reports model calls by role, how many plan revisions happened, and who
decided the verdict. For the sequence diagram:

```sh
.venv/bin/python -m lec2_planning_and_validation_agent.trajectory \
    runs/v23-bc284c6b.jsonl --format mermaid
```

To show that a run reproduces, run it twice to different files and compare.
`compare` ignores timestamps and returns an empty list when the runs match.

```sh
.venv/bin/python -c "
from lec2_planning_and_validation_agent.trace import compare
print(compare('/tmp/a.jsonl', '/tmp/b.jsonl'))"
```

## What the graph does

Five nodes. `plan`, `controller` and `validate` are where a model decides;
`tools` and `submit` are runtime work.

```
START → plan → controller → tools ─┬→ controller
                  │                └→ plan       (nothing new came back)
                  ↓
               submit ─┬→ validate → END
                       └→ controller             (checks failed)
```

Two lines differ from V1, and everything else serves them.

**`tools → controller` is no longer unconditional.** In V1 that single edge is
what makes the agent reactive, and it had one destination. Here it chooses, and
the second destination is the planner.

**`submit` can no longer accept.** The claim that the entity which proposes is
not the entity which judges is a property of the wiring here, not a request made
of a prompt.

The graph definition is still fixed. Every path a run takes is a predicate over
state — dynamic paths need not change the graph definition.

## Fix a failed run

**If the model narrates a tool call instead of emitting one**, the loop spins
until the budget stops it and the status is `budget_exhausted`. Check the model
supports tool calling.

**If a run stops with `no_progress`**, it stalled, was given a revised plan, and
stalled again. The trace records `after_replan` on that event, which is how you
tell that case from a V1-style stall.

**If the plan is empty**, the planner returned no `propose_plan` call. That is
not fatal — the controller works without a plan, exactly as V1 does — but it
means the V2 segment has nothing to show, so re-run it.

**If the validator's reason is "the validator returned no decision"**, the model
emitted no `decide` call. The trace records `decided_by: runtime` for that, so it
is never confused with a considered refusal.

**If `edit` keeps being refused**, read the refusal in the trace. The repository
indents with tabs, and `edit` matches an exact, unique string.

**If the first run of the day is slow**, the model is loading. Cold start is
about 25 seconds against 10 warm.
