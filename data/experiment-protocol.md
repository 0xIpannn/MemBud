# Draft controlled-evaluation protocol

**Status: pilot performed; final evaluation design draft.** An exploratory 12-case DeepSeek run has been performed on the frozen synthetic set (see `data/pilot-report.md` and `data/results/`). The final manifest and budget must still be approved and frozen before collecting evaluation outcomes; the pilot is not a powered effectiveness study.

## Conditions

All conditions receive the exact same frozen prior state, evidence/source excerpts, candidate state, operation annotations, and case identifier. Candidate proposals must be generated once (hand-authored or produced by a pinned proposal model) and reused unchanged across conditions. Keep proposal generation separate from verifier calls and costs.

1. **Unguarded:** Apply that candidate state without verification. This measures the proposed update's raw effect; it is not a verifier decision.
2. **Text guarded:** Present the transition as deterministic compact JSON/text, including all fields and provenance, then apply the common decision policy.
3. **Graph guarded:** Present the same canonical transition as graph structure, including exactly the same facts, source text, provenance, required claims, and operation annotations; apply the same decision policy.

The default `verify()` implementation accepts a representation selector but both routes call the same deterministic canonical checks; that is a plumbing baseline, **not** a graph-vs-text effectiveness test. An optional `verify_with_llm()` path uses a shared prompt/policy and the same JSON output schema, model, temperature, retry settings, and candidate transition for graph/text; only the serialized representation differs. Its prompt version is `membud-transition-checks-v1`, with JSON mode and temperature 0. The recommended API provider is DeepSeek's official OpenAI-compatible endpoint with model ID `deepseek-flash` (configurable via `DEEPSEEK_MODEL`); the API-returned model field is logged, but a catalog alias may not identify an immutable backend revision. Command Code remains an optional provider route. An exploratory live comparative run on the 12-case synthetic set has been performed with DeepSeek; it is not a powered effectiveness test. Do not claim graph superiority without outcome data. A text renderer must not receive less relational/provenance information than the graph.

For guarded conditions, commit the full candidate only if coverage, preservation, and faithfulness all return PASS. Any FAIL or UNCERTAIN yields HOLD. Invalid transition/schema input yields `INVALID_INPUT` before verification and must never write memory. RETRY, if added for model calls, is an execution event and cannot authorize a commit.

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

Do not report a single `error-adjusted` rate without its denominator definition. Errors are non-accepting outcomes, so they belong in the fail-closed intention-to-treat view even when they are excluded from the as-completed view; report error-hold counts alongside both. Claims are not independent statistical samples; use case-level paired comparisons as the primary analysis. Cases with no applicable claims/records for a metric are N/A for that metric.

## Cost, latency, and utility

For each condition record end-to-end and verifier-only latency, graph build/serialization latency, input/output tokens, provider request attempts, retries, cache hits, and error rate. Command Code's completion response does not report billed monetary cost; do not infer cost from token counts without matching account pricing and billing data. Keep proposal-generation cost separate. The 12-case graph/text run entails at least 24 uncached model completions before retries; obtain explicit spend approval before making paid calls. Report downstream QA only if the chosen dataset and frozen protocol provide an appropriate measure; do not infer QA utility from verifier acceptance.

## Sample size and analysis plan

The 10–20-case run is a **pilot** for schema/pipeline failures, not a powered effectiveness study. Freeze the candidate manifest, then use a separate held-out evaluation set with a sample-size/precision justification. If the available set is too small for a defensible power target, call results exploratory and emphasize paired effect sizes and confidence intervals rather than significance claims. For final paired comparisons, bootstrap transition cases (not claims) and report confidence intervals, case counts, missing outputs, annotation disagreements, and sensitivity to adjudication. Do not tune policies on final evaluation cases.

## Required run artifacts

Save immutable run ID, timestamp, source revision/working-tree identity, dataset/manifest hash, configuration and prompt versions, exact input/candidate hashes, raw verifier responses, parsed checks, final decision, commit result, failures, latency, token usage, and retry/cache events. The local SQLite response cache (default `~/.cache/membud/commandcode-cache.sqlite3`) and audit logs contain inputs/outputs; keep them private and use only authorized data. Never commit licensed benchmark content or derived records without confirming permission; the HaluMem terms are documented in `data/README.md`.
