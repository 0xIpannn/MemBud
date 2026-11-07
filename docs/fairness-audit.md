# Fairness audit: does graph verification add detection power beyond information access?

**Date:** 2026-11-07
**Artifact:** `data/results/fairness-audit-v2/audit.json` (deterministic, rules-only, 0 paid API calls)
**Reproduce:** `PYTHONPATH=src .venv/bin/python scripts/run_fairness_audit.py --output data/results/fairness-audit-v2`

## Question

The v2 evaluation showed graph returning HOLD where plain text returned ACCEPT on exactly two designed contradiction cases. Before attributing that to structural graph reasoning, we tested whether it is explained by **information access**: the structural graph verifier consumes `evidence[].contradicts`, while the historical exact-claim-set text verifier does not.

## Design

Four strategies on the same 36 frozen v2 cases (identical transitions, identical labels; 10 expected-invalid):

| Strategy | Mechanism | Reads `evidence.contradicts` |
|---|---|---|
| `unguarded` | applies the candidate with no verifier | no |
| `text` | exact claim-set baseline (historical control) | no |
| `text_fair` | claim-set baseline **plus the same contradiction access** graph has: candidate claim ∈ any `evidence.contradicts` ⇒ faithfulness FAIL `CONTRADICTED_CLAIM` | yes |
| `graph` | structural graph verifier (provenance paths, reachability, operation edges, contradiction edges, connectivity) | yes |

This design separates two effects: `text → text_fair` isolates the value of contradiction **information access**; `text_fair → graph` isolates verifier **construction** (graph structure vs claim sets) with information held equal.

## Results

| Strategy | ACCEPT | HOLD | INVALID_INPUT | False accepts | Valid accepted | Label agreement |
|---|---:|---:|---:|---:|---:|---:|
| unguarded | 26 | 0 | 10 | 12/12 | — | 24/36 |
| text | 16 | 10 | 10 | 2/12 (DEV-13, HELD-13) | 14/14 | 34/36 |
| text_fair | 14 | 12 | 10 | **0/12** | 14/14 | **36/36** |
| graph | 14 | 12 | 10 | **0/12** | 14/14 | **36/36** |

- `text → text_fair`: exactly the two contradiction cases change (DEV-13, HELD-13), both from `ACCEPT` to `HOLD` with `CONTRADICTED_CLAIM`. The false accepts disappear.
- `text_fair → graph`: **zero differences** across all 36 cases. Fair text and graph agree on every non-invalid case.
- Invalid-input handling is identical (10/10 classified correctly) under every guarded strategy.

## Conclusion

On this manifest, the visible graph-vs-text advantage is **entirely explained by information access**: the plain text baseline never reads `evidence.contradicts`. Once a text verifier is given the same annotation access, it matches the structural graph verifier on every decision. The audit finds **no decision-level detection power added by graph structure here** — only a difference in how the failure is attributed (reason codes and affected ids).

This is narrow evidence (36 synthetic, single-reviewer, hand-authored cases; second review pending), so it does not prove general equivalence between representations. It does mean earlier graph-vs-text results must **not** attribute contradiction detection to graph structure.

## Implications for the research hypothesis

1. Comparing "graph verifier" against a text baseline that ignores a field the graph verifier consumes is not a test of representation; it is a test of annotation availability. Future comparisons must hold information constant (as `text_fair` does).
2. To establish a genuine graph advantage, the graph representation must expose relational structure that a text verifier cannot reconstruct from the same canonical facts — e.g. multi-hop provenance/derivation chains, entity-relationship contradictions across records, or temporal ordering — and the fair-text control must receive the same underlying facts.
3. Until then, the defensible claims are narrower: the schema/operation invariants, the store's fail-safe behavior, the adversarial-graph structural checks (malformed/injected graph inputs that canonical text input cannot even express), and the evaluation harness's ability to run controlled comparisons.

## Limitations

- 36 synthetic, single-reviewed cases authored alongside the verifiers (circularity risk), no statistical power.
- Only contradiction annotations differ between the verifiers; the analysis does not cover other potential relational mechanisms.
- `text_fair` is a control constructed for this audit, not a new production verifier; it is deliberately minimal (claim-set checks + contradiction access).
- The graph verifier's structural checks remain valuable for defense against malformed graph inputs and for attribution, but this audit does not measure that value.
