# Lecture 2: from a reactive agent to planning and validation

Code: [`cyberbird/reactive`](../../cyberbird/reactive) (V1) and
[`cyberbird/plan_and_validation`](../../cyberbird/plan_and_validation) (V2/V3).

The worked example is alert `bc284c6b`: Bandit `B608`, SQL injection at
`testcode/BenchmarkTest00283.py:46`.

    cyberbird lec02 reactive --alert-id bc284c6b
    cyberbird lec02 plan-and-validation --alert-id bc284c6b

In this folder:

- `seq.md`: the V1 run above as a sequence diagram.
- `runs/`: the traces shown in lecture (`v1-*` from V1, `v23-*` from V2/V3).
  Render one from the repo root with
  `cyberbird trace lectures/lec02/runs/v1-bc284c6b.jsonl --format mermaid`.
