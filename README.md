# Cyberbird

Demos for COMSE6998-019. One thread runs through the course: **Cyberbird**, an agent that patches
security vulnerabilities. It starts as a loop on a laptop and ends as a full
agent deployed on KIND.

    fixture/  ──scan──▶  data/scans/  ──intake──▶  data/findings.json  ──▶  cyberbird  ──▶  runs/
    (vulnerable app)     (Bandit, Semgrep)         (720 alerts)             (patches one)

| Folder | What it is |
|---|---|
| `fixture/` | OWASP BenchmarkPython at a pinned commit: the code being patched |
| `data/` | scanner output, the alert queue, and the ground truth (kept out of `fixture/` so the agent cannot read it) |
| `intake/` | turns scanner output into the alert queue |
| `cyberbird/` | the agent, one folder per version ([versions](cyberbird/README.md)) |
| `lectures/` | per-lecture notes, diagrams, and example runs |

## Lectures

| Lecture | Command | Code |
|---|---|---|
| 2 | `cyberbird lec02 reactive` | [`cyberbird/reactive`](cyberbird/reactive) |
| 2 | `cyberbird lec02 plan-and-validation` | [`cyberbird/plan_and_validation`](cyberbird/plan_and_validation) |

## Set up

    python3 -m venv .venv
    .venv/bin/pip install -e '.[dev]'
    source .venv/bin/activate
    cyberbird --help

The agents call a local model through [Ollama](https://ollama.com):
`ollama pull qwen3.8`. See each version's README for the pre-flight checks.

## Commands

    cyberbird lec02 reactive --alert-id bc284c6b   # run V1 on one alert
    cyberbird alerts --list                        # browse the queue
    cyberbird trace runs/<run>.jsonl               # replay a run
    cyberbird intake                               # rebuild data/findings.json
    pytest                                         # intake + CLI tests
