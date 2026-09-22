# The reactive agent

Run it:

```sh
cd demo
.venv/bin/python -m lec2_reactive_agent --alert-id bc284c6b
```

That is the lecture's worked example: Bandit `B608`, SQL injection at
`testcode/BenchmarkTest00283.py:46`. It takes about a minute and prints each
turn of the loop as it happens.

## Check the machine is ready

1. Confirm Ollama is serving.

   ```sh
   curl -s localhost:11434/api/tags
   ```

2. Confirm the model is pulled. The run needs `qwen3.8`.

   ```sh
   ollama list
   ```

3. Confirm the alert queue exists. It should report 720 alerts.

   ```sh
   .venv/bin/python -m lec2_reactive_agent.cli --list
   ```

If step 3 fails, rebuild it with `.venv/bin/python -m intake.build_findings`.

## Read the output

Colour answers the question every version of the agent is built around: **who
decided this?**

| Colour | Meaning |
|---|---|
| Cyan | A model decided |
| White | The runtime acted |
| Green | It worked |
| Red | It was refused, or it failed |

That matches the lecture's graph figures, where blue nodes are model calls and
grey nodes are runtime work.

Each line carries elapsed time. A `MODEL` line costs about 10 seconds; a `TOOL`
line is near-instant. The gap is the point.

```
   9.1s MODEL    controller → read_file      in=914 out=47
   9.1s ROUTE    → tools                     decided by model
   9.2s TOOL →   read_file(path='testcode/BenchmarkTest00283.py')
   9.4s TOOL ←   read_file ok                1665 chars
```

A run ends with one of five terminal statuses: `accepted`, `unresolved`,
`budget_exhausted`, `no_progress`, `error`.

## Choose what to run

Select one alert by id, or by location:

```sh
.venv/bin/python -m lec2_reactive_agent --alert-id be043aa4
.venv/bin/python -m lec2_reactive_agent --location testcode/BenchmarkTest00283.py:46
```

Browse the queue first if you want a different weakness:

```sh
.venv/bin/python -m lec2_reactive_agent.cli --category weakrand
```

Three that work, for a live demo:

| Alert | Weakness | Calls |
|---|---|---|
| `bc284c6b` | SQL injection — the worked example | 5 |
| `be043aa4` | Weak random — needs two edits in different places | 4 |
| `465a5dfe` | Weak MD5 hash | — |

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

Set the budget to zero. The run ends before it issues a single model call,
which is what "checked before the call, not after" means.

```sh
.venv/bin/python -m lec2_reactive_agent --alert-id bc284c6b --budget 0
```

## Read the trace afterwards

The console is a summary. The trace is the record, one JSON event per line, at
`runs/v1-<alert_id>.jsonl`.

```sh
.venv/bin/python -c "
from lec2_reactive_agent.trace import read_events, kinds
print(kinds(read_events('runs/v1-bc284c6b.jsonl')))"
```

To show that a run reproduces, run it twice to different files and compare.
`compare` ignores timestamps and returns an empty list when the runs match.

```sh
.venv/bin/python -c "
from lec2_reactive_agent.trace import compare
print(compare('/tmp/a.jsonl', '/tmp/b.jsonl'))"
```

## What the graph does

Three nodes. `controller` is the only one where a model decides.

```
START → controller → tools → controller → … → submit → END
            │                                   │
            └───────────── rejected ────────────┘
```

The model chooses its own next edge: it either emits a tool call, which routes
to `tools`, or it does not, which routes to `submit`. The `tools → controller`
edge is what makes this version reactive.

`submit` runs three mechanical checks — the patch applies, it touches the
flagged line, a rescan no longer reports the alert. No model judges acceptance.
The checks cannot tell whether the endpoint still returns what it returned
before, and that gap is what V3's validator exists to address.

## Fix a failed run

**If the model narrates a tool call instead of emitting one**, the loop spins
until the budget stops it and the status is `budget_exhausted`. Check the model
supports tool calling.

**If a run stops with `no_progress`**, the model repeated one tool call three
times. That is ReAct's own reported failure mode, not a crash.

**If `edit` keeps being refused**, read the refusal in the trace. The repository
indents with tabs, and `edit` matches an exact, unique string.

**If the first run of the day is slow**, the model is loading. Cold start is
about 25 seconds against 10 warm.
