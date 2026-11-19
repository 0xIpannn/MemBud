# Paired statistical analysis of committed evaluation evidence

**Status:** exploratory, artifact-only analysis. No network I/O, no paid model calls, and no new model runs. Report ID: `paired-statistical-analysis-v1`.

**Report artifact:** `data/results/statistical-analysis/report.json` (SHA-256 `cb60cfee69b417eeb9d295a71d6e0f6c871f3e018a6499357d6646d099d5929a`).

**Reproduce:**

```bash
TMPDIR=$PWD/.tmp-stats PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
    .venv/bin/python scripts/analyze_paired_metrics.py
```

## Scope and data

All numbers below are computed from committed artifacts. Conditions are paired by case: every condition sees the same frozen transitions and labels. The analysis never pools rules and LLM artifacts, never pools prompt v1 and v2, and refuses to run if artifacts disagree about the manifest, case set, labels or configuration.

Manifest `membud-evaluation-v2` (`e1adf173a3f8`), 36 cases, splits dev 18, heldout 18; expected decisions ACCEPT 14, HOLD 12, INVALID_INPUT 10. Review status: single reviewer, second independent review pending.

| Dataset | Kind | Conditions | Cases | Configuration |
| --- | --- | --- | ---: | --- |
| `rules-fair-v2` | rules | `unguarded`, `text`, `text_fair`, `graph` | 36 | `rules:fairness-audit-v2:verifier=rules:splits=dev+heldout` |
| `rules-v2-run` | rules | `unguarded`, `graph`, `text` | 36 | `rules:evaluation-v2-rules:verifier=rules:splits=dev+heldout` |
| `llm-heldout-prompt-v1` | llm | `graph`, `text`, `semantic` | 18 | `llm:deepseek-flash:deepseek-openai-compatible:prompt=membud-transition-checks-v1:semantic=membud-semantic-compare-v1:split=heldout` |
| `llm-heldout-prompt-v2` | llm | `graph`, `text`, `semantic` | 18 | `llm:deepseek-flash:deepseek-openai-compatible:prompt=membud-transition-checks-v2:semantic=membud-semantic-compare-v1:split=heldout` |

Artifact hashes, per-condition numerators/denominators and Wilson 95% intervals are in `report.json`.

Cross-check: 108 overlapping case-condition decisions agree between the fairness audit and the evaluation-v2 rules run (mismatches: 0), and the committed graph-vs-text paired-difference listing is reproduced from case rows.

## Method

- Unit of analysis: transition case. Bootstrap: percentile interval over 10,000 resampling replicates of cases (paired, seed 20261108); each replicate draws cases with replacement and keeps both conditions' outcomes for a drawn case together.
- Per-condition rates report explicit numerators/denominators with Wilson 95% confidence intervals.
- McNemar: exact two-sided binomial test on discordant pairs (no normal approximation); the table reports `b` = A correct/B wrong, `c` = B correct/A wrong.
- `INVALID_INPUT` is a schema-construction failure recorded before verification. Expected-invalid cases are counted in the invalid-handling denominator and listed as explicit exclusions from paired verifier comparisons; no case is silently dropped.
- When every paired case disagrees in one direction, the bootstrap distribution collapses and the percentile interval is a single point (for example unguarded vs guarded on faulty detection). That reflects the all-or-nothing committed case design, not high precision.
- No multiplicity correction and no threshold tuning; all comparisons are descriptive.

## Rules-only fairness audit v2 (four conditions) (`rules-fair-v2`)

| Condition | Valid accepted | False holds | Faulty detected (HOLD) | False accepts | Expected-invalid handled | Accuracy (non-invalid) | Decision counts (A/H/I) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `unguarded` | 14/14 (100.0%) | 0/14 (0.0%) | 0/12 (0.0%) | 12/12 (100.0%) | 10/10 (100.0%) | 14/26 (53.8%) | 26/0/10 |
| `text` | 14/14 (100.0%) | 0/14 (0.0%) | 10/12 (83.3%) | 2/12 (16.7%) | 10/10 (100.0%) | 24/26 (92.3%) | 16/10/10 |
| `text_fair` | 14/14 (100.0%) | 0/14 (0.0%) | 12/12 (100.0%) | 0/12 (0.0%) | 10/10 (100.0%) | 26/26 (100.0%) | 14/12/10 |
| `graph` | 14/14 (100.0%) | 0/14 (0.0%) | 12/12 (100.0%) | 0/12 (0.0%) | 10/10 (100.0%) | 26/26 (100.0%) | 14/12/10 |

Paired differences resample cases within each outcome stratum (`overall_accuracy`: non-invalid cases; `faulty_detection`: expected-faulty; `valid_acceptance`: valid).

| Comparison | Outcome | Paired n | b / c | Difference | 95% bootstrap CI | McNemar exact p |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `unguarded` vs `text` | case accuracy | 26 | 0 / 10 | -0.385 | [-0.577, -0.192] | 0.002 |
| `unguarded` vs `text` | faulty detection (HOLD) | 12 | 0 / 10 | -0.833 | [-1.000, -0.583] | 0.002 |
| `unguarded` vs `text` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `unguarded` vs `text_fair` | case accuracy | 26 | 0 / 12 | -0.462 | [-0.654, -0.269] | <0.001 |
| `unguarded` vs `text_fair` | faulty detection (HOLD) | 12 | 0 / 12 | -1.000 | [-1.000, -1.000] | <0.001 |
| `unguarded` vs `text_fair` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `unguarded` vs `graph` | case accuracy | 26 | 0 / 12 | -0.462 | [-0.654, -0.269] | <0.001 |
| `unguarded` vs `graph` | faulty detection (HOLD) | 12 | 0 / 12 | -1.000 | [-1.000, -1.000] | <0.001 |
| `unguarded` vs `graph` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text` vs `text_fair` | case accuracy | 26 | 0 / 2 | -0.077 | [-0.192, 0.000] | 0.500 |
| `text` vs `text_fair` | faulty detection (HOLD) | 12 | 0 / 2 | -0.167 | [-0.417, 0.000] | 0.500 |
| `text` vs `text_fair` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text` vs `graph` | case accuracy | 26 | 0 / 2 | -0.077 | [-0.192, 0.000] | 0.500 |
| `text` vs `graph` | faulty detection (HOLD) | 12 | 0 / 2 | -0.167 | [-0.417, 0.000] | 0.500 |
| `text` vs `graph` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text_fair` vs `graph` | case accuracy | 26 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text_fair` vs `graph` | faulty detection (HOLD) | 12 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text_fair` vs `graph` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |

10 expected-invalid cases (each across 4 conditions) are explicit exclusions (`invalid_input_schema_construction_failure`), not dropped rows.

## Committed rules-only evaluation-v2 run (three conditions) (`rules-v2-run`)

| Condition | Valid accepted | False holds | Faulty detected (HOLD) | False accepts | Expected-invalid handled | Accuracy (non-invalid) | Decision counts (A/H/I) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `unguarded` | 14/14 (100.0%) | 0/14 (0.0%) | 0/12 (0.0%) | 12/12 (100.0%) | 10/10 (100.0%) | 14/26 (53.8%) | 26/0/10 |
| `graph` | 14/14 (100.0%) | 0/14 (0.0%) | 12/12 (100.0%) | 0/12 (0.0%) | 10/10 (100.0%) | 26/26 (100.0%) | 14/12/10 |
| `text` | 14/14 (100.0%) | 0/14 (0.0%) | 10/12 (83.3%) | 2/12 (16.7%) | 10/10 (100.0%) | 24/26 (92.3%) | 16/10/10 |

Paired differences resample cases within each outcome stratum (`overall_accuracy`: non-invalid cases; `faulty_detection`: expected-faulty; `valid_acceptance`: valid).

| Comparison | Outcome | Paired n | b / c | Difference | 95% bootstrap CI | McNemar exact p |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `unguarded` vs `graph` | case accuracy | 26 | 0 / 12 | -0.462 | [-0.654, -0.269] | <0.001 |
| `unguarded` vs `graph` | faulty detection (HOLD) | 12 | 0 / 12 | -1.000 | [-1.000, -1.000] | <0.001 |
| `unguarded` vs `graph` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `unguarded` vs `text` | case accuracy | 26 | 0 / 10 | -0.385 | [-0.577, -0.192] | 0.002 |
| `unguarded` vs `text` | faulty detection (HOLD) | 12 | 0 / 10 | -0.833 | [-1.000, -0.583] | 0.002 |
| `unguarded` vs `text` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `text` | case accuracy | 26 | 2 / 0 | +0.077 | [0.000, 0.192] | 0.500 |
| `graph` vs `text` | faulty detection (HOLD) | 12 | 2 / 0 | +0.167 | [0.000, 0.417] | 0.500 |
| `graph` vs `text` | valid acceptance (ACCEPT) | 14 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |

10 expected-invalid cases (each across 3 conditions) are explicit exclusions (`invalid_input_schema_construction_failure`), not dropped rows.

## Held-out LLM/semantic run, prompt v1 (development evidence) (`llm-heldout-prompt-v1`)

| Condition | Valid accepted | False holds | Faulty detected (HOLD) | False accepts | Expected-invalid handled | Accuracy (non-invalid) | Decision counts (A/H/I) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `graph` | 3/7 (42.9%) | 4/7 (57.1%) | 6/6 (100.0%) | 0/6 (0.0%) | 5/5 (100.0%) | 9/13 (69.2%) | 3/10/5 |
| `text` | 6/7 (85.7%) | 1/7 (14.3%) | 6/6 (100.0%) | 0/6 (0.0%) | 5/5 (100.0%) | 12/13 (92.3%) | 6/7/5 |
| `semantic` | 7/7 (100.0%) | 0/7 (0.0%) | 5/6 (83.3%) | 1/6 (16.7%) | 5/5 (100.0%) | 12/13 (92.3%) | 8/5/5 |

Paired differences resample cases within each outcome stratum (`overall_accuracy`: non-invalid cases; `faulty_detection`: expected-faulty; `valid_acceptance`: valid).

| Comparison | Outcome | Paired n | b / c | Difference | 95% bootstrap CI | McNemar exact p |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `graph` vs `text` | case accuracy | 13 | 0 / 3 | -0.231 | [-0.462, 0.000] | 0.250 |
| `graph` vs `text` | faulty detection (HOLD) | 6 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `text` | valid acceptance (ACCEPT) | 7 | 0 / 3 | -0.429 | [-0.857, -0.143] | 0.250 |
| `graph` vs `semantic` | case accuracy | 13 | 1 / 4 | -0.231 | [-0.538, 0.077] | 0.375 |
| `graph` vs `semantic` | faulty detection (HOLD) | 6 | 1 / 0 | +0.167 | [0.000, 0.500] | 1.000 |
| `graph` vs `semantic` | valid acceptance (ACCEPT) | 7 | 0 / 4 | -0.571 | [-0.857, -0.143] | 0.125 |
| `text` vs `semantic` | case accuracy | 13 | 1 / 1 | +0.000 | [-0.231, 0.231] | 1.000 |
| `text` vs `semantic` | faulty detection (HOLD) | 6 | 1 / 0 | +0.167 | [0.000, 0.500] | 1.000 |
| `text` vs `semantic` | valid acceptance (ACCEPT) | 7 | 0 / 1 | -0.143 | [-0.429, 0.000] | 1.000 |

5 expected-invalid cases (each across 3 conditions) are explicit exclusions (`invalid_input_schema_construction_failure`), not dropped rows.

## Held-out LLM/semantic replay, prompt v2 (development evidence) (`llm-heldout-prompt-v2`)

| Condition | Valid accepted | False holds | Faulty detected (HOLD) | False accepts | Expected-invalid handled | Accuracy (non-invalid) | Decision counts (A/H/I) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `graph` | 7/7 (100.0%) | 0/7 (0.0%) | 6/6 (100.0%) | 0/6 (0.0%) | 5/5 (100.0%) | 13/13 (100.0%) | 7/6/5 |
| `text` | 7/7 (100.0%) | 0/7 (0.0%) | 6/6 (100.0%) | 0/6 (0.0%) | 5/5 (100.0%) | 13/13 (100.0%) | 7/6/5 |
| `semantic` | 7/7 (100.0%) | 0/7 (0.0%) | 6/6 (100.0%) | 0/6 (0.0%) | 5/5 (100.0%) | 13/13 (100.0%) | 7/6/5 |

Paired differences resample cases within each outcome stratum (`overall_accuracy`: non-invalid cases; `faulty_detection`: expected-faulty; `valid_acceptance`: valid).

| Comparison | Outcome | Paired n | b / c | Difference | 95% bootstrap CI | McNemar exact p |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `graph` vs `text` | case accuracy | 13 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `text` | faulty detection (HOLD) | 6 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `text` | valid acceptance (ACCEPT) | 7 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `semantic` | case accuracy | 13 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `semantic` | faulty detection (HOLD) | 6 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `graph` vs `semantic` | valid acceptance (ACCEPT) | 7 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text` vs `semantic` | case accuracy | 13 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text` vs `semantic` | faulty detection (HOLD) | 6 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |
| `text` vs `semantic` | valid acceptance (ACCEPT) | 7 | 0 / 0 | +0.000 | [0.000, 0.000] | 1.000 |

5 expected-invalid cases (each across 3 conditions) are explicit exclusions (`invalid_input_schema_construction_failure`), not dropped rows.

## Sample-size / precision rationale

The held-out LLM analysis rests on 13 schema-valid cases (7 valid, 6 expected-faulty; 5 expected-invalid are construction failures). The rules analysis rests on 26 non-invalid cases (14 valid, 12 expected-faulty). These are exploratory samples: the intervals are wide, several paired comparisons have zero or two discordant cases, and no result supports a superiority or equivalence claim.

Expected 95% CI half-width for a paired difference under the planning approximation `1.95996 * sqrt(pi_discordant / n)`. This is a precision target, not a power guarantee.

| n (paired cases) | π=0.05 | π=0.10 | π=0.20 | π=0.30 |
|---|---|---|---|---|
| 13 | 0.122 | 0.172 | 0.243 | 0.298 |
| 18 | 0.103 | 0.146 | 0.207 | 0.253 |
| 26 | 0.086 | 0.122 | 0.172 | 0.211 |
| 36 | 0.073 | 0.103 | 0.146 | 0.179 |
| 50 | 0.062 | 0.088 | 0.124 | 0.152 |
| 75 | 0.051 | 0.072 | 0.101 | 0.124 |
| 100 | 0.044 | 0.062 | 0.088 | 0.107 |
| 150 | 0.036 | 0.051 | 0.072 | 0.088 |
| 200 | 0.031 | 0.044 | 0.062 | 0.076 |
| 300 | 0.025 | 0.036 | 0.051 | 0.062 |
| 500 | 0.020 | 0.028 | 0.039 | 0.048 |
| 1000 | 0.014 | 0.020 | 0.028 | 0.034 |

Cases needed for a target 95% CI half-width:

| Target half-width | π=0.05 | π=0.10 | π=0.20 | π=0.30 |
|---|---|---|---|---|
| 0.200 | 5 | 10 | 20 | 29 |
| 0.100 | 20 | 39 | 77 | 116 |
| 0.050 | 77 | 154 | 308 | 461 |
| 0.025 | 308 | 615 | 1230 | 1844 |

Observed paired comparisons with at least one discordant pair (the full list is in `report.json`):

| Dataset | Comparison | Outcome | Paired n | Discordant | Half-width |
|---|---|---|---|---|---|
| `rules-fair-v2` | unguarded vs text | case accuracy | 26 | 10 | 0.192 |
| `rules-fair-v2` | unguarded vs text | faulty detection (HOLD) | 12 | 10 | 0.208 |
| `rules-fair-v2` | unguarded vs text_fair | case accuracy | 26 | 12 | 0.192 |
| `rules-fair-v2` | unguarded vs text_fair | faulty detection (HOLD) | 12 | 12 | 0.000 |
| `rules-fair-v2` | unguarded vs graph | case accuracy | 26 | 12 | 0.192 |
| `rules-fair-v2` | unguarded vs graph | faulty detection (HOLD) | 12 | 12 | 0.000 |
| `rules-fair-v2` | text vs text_fair | case accuracy | 26 | 2 | 0.096 |
| `rules-fair-v2` | text vs text_fair | faulty detection (HOLD) | 12 | 2 | 0.208 |
| `rules-fair-v2` | text vs graph | case accuracy | 26 | 2 | 0.096 |
| `rules-fair-v2` | text vs graph | faulty detection (HOLD) | 12 | 2 | 0.208 |
| `rules-v2-run` | unguarded vs graph | case accuracy | 26 | 12 | 0.192 |
| `rules-v2-run` | unguarded vs graph | faulty detection (HOLD) | 12 | 12 | 0.000 |
| `rules-v2-run` | unguarded vs text | case accuracy | 26 | 10 | 0.192 |
| `rules-v2-run` | unguarded vs text | faulty detection (HOLD) | 12 | 10 | 0.208 |
| `rules-v2-run` | graph vs text | case accuracy | 26 | 2 | 0.096 |
| `rules-v2-run` | graph vs text | faulty detection (HOLD) | 12 | 2 | 0.208 |
| `llm-heldout-prompt-v1` | graph vs text | case accuracy | 13 | 3 | 0.231 |
| `llm-heldout-prompt-v1` | graph vs text | valid acceptance (ACCEPT) | 7 | 3 | 0.357 |
| `llm-heldout-prompt-v1` | graph vs semantic | case accuracy | 13 | 5 | 0.308 |
| `llm-heldout-prompt-v1` | graph vs semantic | faulty detection (HOLD) | 6 | 1 | 0.250 |
| `llm-heldout-prompt-v1` | graph vs semantic | valid acceptance (ACCEPT) | 7 | 4 | 0.357 |
| `llm-heldout-prompt-v1` | text vs semantic | case accuracy | 13 | 2 | 0.231 |
| `llm-heldout-prompt-v1` | text vs semantic | faulty detection (HOLD) | 6 | 1 | 0.250 |
| `llm-heldout-prompt-v1` | text vs semantic | valid acceptance (ACCEPT) | 7 | 1 | 0.214 |

At-n precision example: the rules `text vs graph` case-accuracy difference has n = 26, 2 discordant pairs, and an observed 95% half-width of about 0.096; a 0.05 half-width at that observed discordance rate would need roughly 119 non-invalid paired cases (planning estimate, not a power guarantee).

The table answers 'how wide would the paired-difference interval be at n?' It does not answer 'what n guarantees significance?' and it assumes a discordance rate. No power guarantee is made, and no threshold is selected from this table.

## Determinism

- Seed `20261108`, resamples `10,000`, confidence level `0.95`.
- Two consecutive runs produce byte-identical `report.json`; verify with `scripts/analyze_paired_metrics.py --check`.

## Limitations (read before citing any number)

- Labels are single-reviewer exploratory annotations; the second independent human review is pending. Label error is not modelled in these intervals.
- Cases are synthetic and were authored alongside the verifiers (circularity risk); they are not a representative benchmark and no population inference is supported.
- The v2 LLM prompt was selected using the held-out split; its held-out numbers are development evidence, not a clean independent test. Rules artifacts reuse the dev and held-out splits for descriptive analysis.
- Samples are small: 13 schema-valid held-out cases for the LLM analysis and 26 non-invalid rules cases (12 expected-faulty, 14 valid). Several paired comparisons have zero or two discordant cases.
- Zero discordance (text_fair vs graph on the rules set; all v2 LLM condition pairs) cannot establish equivalence; the exact test gives p = 1 and the discordance rate is not estimable from the data.
- Paired bootstrap intervals resample cases within outcome strata, assume exchangeable cases, and are not corrected for multiplicity or condition selection. Percentile intervals are descriptive.
- INVALID_INPUT rows are schema-construction failures, not verifier decisions; they are excluded from paired verifier comparisons and counted separately.
- The unguarded condition has no verifier decisions; its APPLIED/ACCEPT rows are a raw-effect baseline, not a verifier outcome.
- No superiority, equivalence, or causality claim is made, and no threshold was tuned on these cases.

## Interpretation

On the committed rules-only evidence, graph and the fair-text control agree on every non-invalid case (26/26); the plain text baseline misses exactly the two constructed contradiction cases, and the paired difference is not statistically distinguishable with 26 non-invalid cases (2 discordant pairs, exact McNemar p = 0.5). The prompt-v2 held-out LLM replay shows all three conditions tied at 7/7 valid accepted and 6/6 expected-faulty detected, but zero discordant pairs cannot support an equivalence claim. The prompt-v1 run is retained as development evidence only. The intervals and the sample-size table show why a fresh, better-powered, independently reviewed evaluation is required before any comparative claim. No superiority or equivalence claim is made for any condition.
