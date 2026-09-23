# Cyberbird

One vulnerability-patching agent, grown lecture by lecture. Each version is a
complete, runnable copy, so you can read one on its own or diff it against the
next:

    diff -r cyberbird/reactive cyberbird/plan_and_validation

| Version | Folder | Lecture | Adds | Run |
|---|---|---|---|---|
| V1 | `reactive/` | 2 | the loop: model picks a tool, runtime executes and checks | `cyberbird lec02 reactive` |
| V2 + V3 | `plan_and_validation/` | 2 | a planner, replanning, and a validator | `cyberbird lec02 plan-and-validation` |

Every version reads the same inputs: the pinned fixture in `fixture/` and the
alert queue in `data/findings.json`. Runs are written to `runs/`.
