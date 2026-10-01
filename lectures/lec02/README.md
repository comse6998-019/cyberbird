# Lecture 2: from a reactive agent to planning and validation

Code: [`cyberbird/reactive`](../../cyberbird/reactive) and
[`cyberbird/plan_and_validation`](../../cyberbird/plan_and_validation).

The worked example is alert `bc284c6b`: Bandit `B608`, SQL injection at
`testcode/BenchmarkTest00283.py:46`.

    cyberbird reactive --alert-id bc284c6b
    cyberbird plan-and-validation --alert-id bc284c6b

In this folder:

- `seq.md`: the reactive agent's run above as a sequence diagram.
- `runs/`: the traces shown in lecture (`reactive-*` and `plan-and-validation-*`, one per agent).
  Render one from the repo root with
  `python -m cyberbird.plan_and_validation.trajectory lectures/lec02/runs/reactive-bc284c6b.jsonl --format mermaid`.
