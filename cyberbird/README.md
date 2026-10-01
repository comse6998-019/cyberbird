# Cyberbird

Several implementations of a vulnerability-patching agent, one per folder. Each
is a complete, runnable copy, so you can read one on its own or diff it against
another:

    diff -r cyberbird/reactive cyberbird/plan_and_validation

| Folder | Lecture | What it does | Run |
|---|---|---|---|
| `reactive/` | 2 | the loop: model picks a tool, runtime executes and checks | `cyberbird reactive` |
| `plan_and_validation/` | 2 | a planner, replanning, and a validator | `cyberbird plan-and-validation` |

Every agent starts the same way: a `scan` node runs Bandit over the fixture,
and a `normalize` node turns the report into `runs/findings.json` and picks the
run's alert. Each agent owns its copy of that code in `scan.py`.

Every agent reads the same input, the pinned fixture in
`fixtures/owasp-benchmark-python/src/`. Runs are written to `runs/`.
