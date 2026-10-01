# Cyberbird

This is an instruction companion for COMSE6998-019. One thread runs through the course: **Cyberbird**, this is our an agent that helps identify and remediate security vulnerabilities. It starts as a loop on a laptop and ends as a full agent deployed on KIND as the course progresses.

## Project Structure

The agent implementations are:

| Folder | What it is |
|---|---|
| [`cyberbird/reactive/`](cyberbird/reactive) | Reactive agent |
| [`cyberbird/plan_and_validation/`](cyberbird/plan_and_validation) | Plan-and-validation agent |


## Set up

### Prerequisites
1. You'll need to have Python installed (I recommend Python 3.12 or later via [pyenv](https://github.com/pyenv/pyenv)). 
2. Also get [`uv`](https://docs.astral.sh/uv/) to install the necessary dependencies and actually run the agents.
3. The agents call a local model through [Ollama](https://ollama.com):
`ollama pull qwen3.8`. So get that set up before running the agents.

### Installation

```bash
uv sync
```

Thats it! You should now have all the necessary dependencies installed and be ready to run the agents.

```bash
uv run cyberbird --help
```

will get you going 

```help
❯ uv run cyberbird --help
Usage: cyberbird [OPTIONS] COMMAND [ARGS]...

  Cyberbird: vulnerability-patching agents, one subcommand each.

Options:
  --help  Show this message and exit.

Commands:
  plan-and-validation  A planner up front, replanning, and a validator.
  reactive             The model picks each tool call; the runtime checks the
                       result.
```
