# Intake

Runs the two scanners over the pinned fixture and normalizes both into one
alert contract. This is the fixed chain the lecture notes describe: scan →
normalize → categorize → emit. No model call happens anywhere in it.

## Regenerate

```sh
cyberbird intake                  # data/scans/*.json -> data/findings.json
pytest intake/tests -q
```

To re-scan from scratch (a few minutes):

```sh
bandit -r fixture/ -f json -o data/scans/bandit.json -q
semgrep scan --config intake/rules/python.yml \
             --config intake/rules/owasp-top-ten.yml \
             --json -o data/scans/semgrep.json --metrics=off fixture/
```

## The alert contract

Five fields, plus two derived values:

| field | meaning |
|---|---|
| `rule` | scanner-prefixed rule id, e.g. `bandit:B608` |
| `file` | path relative to the fixture root |
| `line` | 1-based line number |
| `message` | single-line description |
| `severity` | `high` / `medium` / `low` |
| `alert_id` | derived: stable 8-hex over (rule, file, line, col) |
| `category` | derived: the fixture's own vocabulary |

Severity is normalized across scanners — Semgrep `ERROR/WARNING/INFO` and
Bandit `HIGH/MEDIUM/LOW` both collapse onto the same three levels.

## Categories

The vocabulary is the fixture's own, taken from `expectedresults-0.1.csv`, so a
category is directly comparable to the ground-truth label. Mapping is by CWE,
with rule-level overrides in three situations, all documented in
`normalize.py`:

- Bandit files its whole XML cluster under generic **CWE-20**; the rule id
  resolves those to `xxe`.
- Bandit overloads **CWE-78** across `subprocess` and `eval`/`exec`, which the
  fixture separates into `cmdi` and `codeinj`.
- One Semgrep rule is mislabelled upstream (`tainted-sql-string` is published
  as CWE-704) and is corrected to `sqli`.

Weaknesses with no counterpart in the fixture vocabulary stay in `other`
rather than being forced into a nearest fit.

## Reproducibility

`data/findings.json` records the pinned commit, both scanner versions, and the
sha256 of each vendored Semgrep ruleset. The rulesets are vendored rather than
fetched from the registry at scan time, so the run does not drift when the
registry moves.

`data/findings.json` is derived. Don't hand-edit it; re-run the builder.
