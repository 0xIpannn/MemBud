# Draft controlled-evaluation protocol

**Status: v2 expanded synthetic set frozen; rules-only comparison run; human second review pending.** A 36-case hash-pinned v2 set (`data/manifests/evaluation-v2.json`) now backs a rules-only unguarded/graph/text comparison with a dev/held-out split; its labels remain **single-reviewer** and a second independent human review is pending (see `data/review/README.md`). The earlier 12-case v1 set and DeepSeek pilot remain archived for reproducibility (see `data/pilot-report.md` and `data/results/`). None of these runs is a powered effectiveness study.

## Conditions

All conditions receive the exact same frozen prior state, evidence/source excerpts, candidate state, operation annotations, and case identifier. Candidate proposals must be generated once (hand-authored or produced by a pinned proposal model) and reused unchanged across conditions. Keep proposal generation separate from verifier calls and costs.

1. **Unguarded:** Apply that candidate state without verification. This measures the proposed update's raw effect; it is not a verifier decision.
2. **Text guarded:** Present the transition as deterministic compact JSON/text, including all fields and provenance, then apply the common decision policy.
3. **Graph guarded:** Present the same canonical transition as graph structure, including exactly the same facts, source text, provenance, required claims, and operation annotations; apply the same decision policy.

The rules-only default `verify()` dispatch now differs by representation: `verify(t, "graph")` runs the structural graph verifier in `src/membud/graph_verification.py` (provenance paths, required-claim reachability, operation edges, contradiction edges, connectivity), while `verify(t, "text")` runs the original claim-set baseline and is deliberately independent of the graph verifier. Both conditions still receive the exact same canonical transition input, so any decision difference is attributable to verifier construction, not input information. An optional `verify_with_llm()` path uses a shared prompt/policy and the same JSON output schema, model, temperature, retry settings, and candidate transition for graph/text; only the serialized representation differs. Its prompt version is `membud-transition-checks-v1`, with JSON mode and temperature 0. The recommended API provider is DeepSeek's official OpenAI-compatible endpoint with model ID `deepseek-flash` (configurable via `DEEPSEEK_MODEL`); the API-returned model field is logged, but a catalog alias may not identify an immutable backend revision. Command Code remains an optional provider route. An exploratory live comparative run on the 12-case synthetic v1 set has been performed with DeepSeek; it is not a powered effectiveness test. Do not claim graph superiority without outcome data. A text renderer must not receive less relational/provenance information than the graph.

For guarded conditions, commit the full candidate only if coverage, preservation, and faithfulness all return PASS. Any FAIL or UNCERTAIN yields HOLD. Invalid transition/schema input yields `INVALID_INPUT` before verification and must never write memory. RETRY, if added for model calls, is an execution event and cannot authorize a commit.

## v2 case set: schema-valid and intentionally invalid inputs

`data/cases/evaluation-v2.jsonl` contains 36 synthetic transitions, each with `schema_version: 2`, SHA-256 pinned by `data/manifests/evaluation-v2.json` with labels in a separate file. The set deliberately includes both transitions that must verify normally and transitions whose *construction* is invalid under the strict v2 schema:

- valid updates (WRITE, REVISE, SUPERSEDE, authorized DELETE, NOOP, multi-operation, contradiction-backed correction);
- omission, corruption without evidence, corruption with insufficient/unrelated evidence, unsupported additions, and an unsupported-provenance case;
- contradiction evidence, including an unresolved contradiction against a retained prior claim (gold HOLD; the claim-set text baseline cannot read the `contradicts` field, so this is the intended verifier-construction difference);
- invalid inputs: DELETE without structured authorization, DELETE authorization referencing the wrong target/evidence, and undeclared insertion/modification/removal.

Invalid-input cases are expected to raise `ValueError` from `Transition.from_dict`. The runner records them as `decision="INVALID_INPUT"`, `status="invalid_input"` for every condition, continues the run, and writes no memory store. They are excluded from verifier precision/recall and reported in a separate invalid-input metric block. A manifest whose `expected_invalid` flag disagrees with actual schema validation is a data-integrity error and the run refuses to start.

The 18 dev and 18 held-out cases were authored together and frozen before any v2 run (per-category balance: 7 valid, 6 faulty, 5 invalid per split). Results are reported by split; no tuning on held-out outcomes was performed. The labels are **single-reviewer exploratory**; `scripts/review_cases.py`, `data/review/README.md`, and `data/review/adjudication-template.md` document the pending second-review and adjudication process without fabricating it.

## Gold annotations and unit of analysis

The unit is one transition case, paired across all conditions. Keep verifier inputs in the case package and gold labels in a separate annotation file not included in verifier prompts. `in_scope_claims` and `required_claims` are inputs. Gold labels include expected findings, affected claim/record IDs, expected decision/outcome, and correctness annotations for prior claims/candidate claims. The current frozen synthetic case set is single-reviewed and exploratory, not independently double-annotated. Any final evaluation should add independent review and adjudication.

## Metrics and denominators

Report every numerator and denominator; do not count an absent denominator as zero.

- **Claim omission rate:** required supported claims absent from candidate / all required supported claims.
- **Unsupported-addition rate:** unsupported material candidate claims / all material candidate claims.
- **Prior-record corruption rate:** valid prior records expected to persist that are improperly changed/removed / all valid prior records annotated expected to persist.
- **Faulty-case detection rate:** faulty transitions flagged with an explicit HOLD (or invalid where appropriate) / gold faulty transitions. Report both an **as-completed** denominator (verifier errors excluded from numerator and denominator) and a **fail-closed intention-to-treat** denominator (all gold faulty cases; error-holds are not credited as detections).
- **False-hold rate:** valid transitions not accepted / gold valid transitions, reported as-completed (completed non-ACCEPT decisions) and fail-closed intention-to-treat (verifier/execution errors leave the update unaccepted and count as held).
- **Valid-update acceptance:** valid transitions accepted / gold valid transitions, in both forms.
- **Case-level error rate:** cases with at least one relevant error / cases with an applicable denominator.
- **Decision counts:** ACCEPT, HOLD, INVALID_INPUT, and execution errors/missing results, separately.
- **Faulty-detection precision and recall:** over cases with an available decision (completed or invalid_input), true positives = gold faulty cases flagged HOLD/INVALID_INPUT, false positives = gold valid cases flagged, false negatives = gold faulty cases accepted. Report numerator/denominator for each; execution/verifier errors are excluded from precision/recall and reported as error rows, with a fail-closed recall variant that treats them as non-detections.
- **False-accept rate:** gold faulty cases accepted / all gold faulty cases. **False-hold rate:** gold valid cases not accepted / all gold valid cases (as-completed and fail-closed ITT, as above).
- **INVALID_INPUT handling (separate block):** expected-invalid cases recorded as `invalid_input` / expected-invalid cases; unexpected invalid rows on gold-accept/hold cases / all such cases; schema-accepted expected-invalid cases. These are construction failures, never verifier decisions; they never write memory and are excluded from verifier precision/recall.
- **Reason-code counts:** per condition, FAIL/UNCERTAIN check reason codes (`REQUIRED_CLAIM_OMITTED`, `PRIOR_RECORD_UNAUTHORIZED_CHANGE`, `UNSUPPORTED_CLAIM`, `CONTRADICTED_CLAIM`, `DISCONNECTED_CLAIM`, `GRAPH_STRUCTURE_INVALID`, ...), plus invalid-input row count.
- **Split breakdown:** every condition metric is reported for `dev` and `heldout` separately as well as pooled. These are descriptive paired counts, not powered subgroup tests.
- **Estimated monetary cost:** reported as `estimated_cost_usd`; for rules-only runs no paid calls are made (`0.0 USD`). When a paid provider is used, the field is `null` with an explicit note because provider billing is unavailable, and token counts are not treated as billed cost.
- **Latency separation:** `preparation_ms` (graph build/text serialization) is kept separate from `verification_ms`; `latency_ms` is end-to-end. Means are reported only over instrumented rows.

Do not report a single `error-adjusted` rate without its denominator definition. Errors are non-accepting outcomes, so they belong in the fail-closed intention-to-treat view even when they are excluded from the as-completed view; report error-hold counts alongside both. Claims are not independent statistical samples; use case-level paired comparisons as the primary analysis. Cases with no applicable claims/records for a metric are N/A for that metric.

## Cost, latency, and utility

For each condition record end-to-end and verifier-only latency, graph build/serialization latency, input/output tokens, provider request attempts, retries, cache hits, and error rate. Command Code's completion response does not report billed monetary cost; do not infer cost from token counts without matching account pricing and billing data. Keep proposal-generation cost separate. The 12-case graph/text run entails at least 24 uncached model completions before retries; obtain explicit spend approval before making paid calls. Report downstream QA only if the chosen dataset and frozen protocol provide an appropriate measure; do not infer QA utility from verifier acceptance.

## Sample size and analysis plan

The original 10–20-case v1 run was a **pilot** for schema/pipeline failures, not a powered effectiveness study. The expanded v2 set is 36 cases (18 dev / 18 held-out, frozen together before the v2 rules run); it adds more schema/verifier failure modes and a split, but it is still far too small for population inference or significance claims. Freeze the candidate manifest, then use a separate held-out evaluation set with a sample-size/precision justification. If the available set is too small for a defensible power target, call results exploratory and emphasize paired effect sizes and confidence intervals rather than significance claims. For final paired comparisons, bootstrap transition cases (not claims) and report confidence intervals, case counts, missing outputs, annotation disagreements, and sensitivity to adjudication. Do not tune policies on final evaluation cases.

## Required run artifacts

Save immutable run ID, timestamp, source revision/working-tree identity, dataset/manifest hash, configuration and prompt versions, exact input/candidate hashes, raw verifier responses, parsed checks, final decision, commit result, failures, latency, token usage, and retry/cache events. The local SQLite response cache (default `~/.cache/membud/commandcode-cache.sqlite3`) and audit logs contain inputs/outputs; keep them private and use only authorized data. Never commit licensed benchmark content or derived records without confirming permission; the HaluMem terms are documented in `data/README.md`.
