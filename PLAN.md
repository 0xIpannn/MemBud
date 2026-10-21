# MemBud — Software Implementation Plan

**Status:** Proposed implementation plan  
**Target timeline:** 6–8 weeks (1–2 months)  
**Primary objective:** Build and evaluate a graph-based safety layer for validating proposed memory updates before they are committed to persistent storage.

## 1. Project Summary

MemBud is a verification layer placed between a host memory system and its persistent write operation. It receives a proposed memory transition, the relevant prior memory, and the source evidence; represents these inputs in a canonical internal graph; checks the proposed transition; and returns a decision.

MemBud is not intended to replace the host memory architecture or become a complete memory system. Its graph is an internal representation used for verification. Backend adapters translate between host-specific memory records and MemBud's canonical representation.

The research contribution to test is whether graph-based verification catches more memory-transition errors than a non-graph verifier using the same verification criteria, model, and approximate computational budget.

## 2. Scope and Non-Goals

### In scope
- A canonical schema for proposed updates, evidence, memory records, entities, temporal information, and relations.
- A small, explicit update lifecycle that supports validation before persistence.
- Three checks: coverage, preservation, and faithfulness.
- Three experimental conditions: unguarded updates, graph-based verification, and non-graph verification.
- A common verifier interface and backend adapters.
- Reproducible experiments with caching, structured logs, cost/latency measurements, and saved decisions.
- Evaluation on a documented, fixed subset of HaluMem, subject to confirming its data format and usage requirements.

### Out of scope for the initial version
- Training a new foundation model or reinforcement-learning update policy.
- Building a production-grade memory product or user interface.
- Claiming support for arbitrary memory backends without testing them.
- Integrating every candidate architecture.
- Making graph-based verification the default until evaluation shows its value.

## 3. Research Questions and Experimental Claims

**RQ1 — Reliability:** Does MemBud reduce omissions, corruption of valid prior memory, and unsupported additions compared with an unguarded update pipeline? Does the graph-based verifier outperform a non-graph verifier applying the same criteria under the same model and budget?

**RQ2 — Modularity:** Can the same verification logic operate across different host memory backends through adapters? What information or transformations are required by each adapter, and what information is lost?

**RQ3 — Trade-offs:** How does verification affect valid-update acceptance, downstream question answering, latency, token usage, and API cost, including graph-construction overhead?

Treat graph superiority as a hypothesis to test, not an assumed result.

## 4. System Architecture

```text
Conversation / source evidence
            |
            v
     Host memory system
            |
            v
     Proposed update
            |
            v
      Backend adapter
            |
            v
  Canonical transition package
       /             \
      v               v
Graph representation  Textual representation
      |               |
Graph-based verifier  Non-graph verifier
       \             /
        v           v
     Decision policy
            |
     ACCEPT / REVISE / HOLD
            |
            v
 Host commit (only if accepted)
            |
            v
       Audit log
```

The unguarded experimental condition bypasses verification and applies the host's normal update behavior. The non-graph condition receives the same relevant records and evidence in a compact textual representation. The graph condition receives equivalent information encoded in the canonical graph. Keep the underlying model, source data, decision options, and token/cost budget as comparable as possible.

### 4.1 Core components

- **Canonical schema:** Stable internal types for records, evidence, candidate operations, relations, timestamps, and verification results.
- **Adapter interface:** Converts host memory and candidate updates into the canonical schema; commits an accepted update using the host's supported write path.
- **Graph builder:** Creates nodes and typed edges from the canonical transition package.
- **Checks:** Evaluate coverage, preservation, and faithfulness.
- **Decision policy:** Maps check results to `ACCEPT`, `REVISE`, or `HOLD`.
- **Experiment runner:** Executes fixed cases under each experimental condition.
- **LLM client:** Calls a pinned API model, validates structured responses, caches results, and records usage.
- **Audit logger:** Stores inputs/references, outputs, decisions, errors, timings, and token usage without silently overwriting prior runs.

For the first implementation, use `HOLD` as the safe outcome when a check fails or returns an invalid response. `REVISE` should mean a bounded retry or a request to regenerate the proposed update; do not let it become an unbounded loop. Record retries separately. Avoid adding `REJECT` as a separate terminal state unless the experiments need to distinguish a definitively invalid update from one that requires review.

## 5. Canonical Data Model

Keep the first schema small and versioned. Use Pydantic models or equivalent validation.

### 5.1 Transition package
- `case_id`: stable identifier for the experimental case.
- `backend`: host backend identifier.
- `operation`: proposed `WRITE`, `REVISE`, or `DELETE`/invalidation operation, normalized where necessary.
- `before_records`: relevant records before the update.
- `candidate_records`: proposed records or changes.
- `evidence`: source snippets or references supporting the candidate.
- `metadata`: timestamps, source IDs, and other provenance that the backend can supply.
- `schema_version`: canonical schema version.

### 5.2 Graph elements
Nodes should initially represent:
- Memory records or facts.
- Evidence/source spans.
- Entities when explicitly identifiable.
- Candidate update operations.

Typed edges should initially represent:
- `SUPPORTED_BY`: record or candidate supported by evidence.
- `ABOUT`: record associated with an entity.
- `SUPERSEDES`: candidate or record supersedes another record.
- `CONTRADICTS`: relation flagged by a rule or verifier, not an unquestioned ground truth.
- `DERIVED_FROM`: candidate derived from a prior record or source.

Include validity time or event time only when the source or backend provides it. Do not invent timestamps. Preserve original text and provenance alongside normalized fields so the graph does not become the sole source of truth.

### 5.3 Verification result
Each check should return:
- `check_name`
- `passed` / `failed` / `uncertain`
- `reason`
- `affected_record_ids`
- `evidence_refs`
- `confidence` only if the selected verifier meaningfully provides it
- `model_name` and `prompt_version` when an LLM is used

A final decision should include the per-check results and a machine-readable reason code.

## 6. Verification Criteria

Use the same three criteria in graph and non-graph conditions.

### Coverage
Does the candidate preserve all required, supported information from the source that should survive the update? Measure omissions against benchmark annotations or a documented reference procedure. Do not assume every source detail must be stored.

### Preservation
Does the transition avoid damaging, deleting, or silently changing valid prior records that should remain valid? Treat legitimate corrections and supersession as valid only when supported by evidence and represented explicitly.

### Faithfulness
Is every material claim in the candidate supported by the supplied evidence, without fabricated details or unjustified increases in certainty? Preserve hedging, uncertainty, and temporal qualifiers where present.

### Graph-specific hypothesis
The graph verifier may use explicit links between evidence, records, entities, and supersession/contradiction relations to identify relational failures. The non-graph verifier must receive the same underlying facts and evidence as text, not less information. This makes the representation—not access to extra evidence—the main difference.

Start with transparent rules where practical and use an LLM for semantic judgments that cannot be reliably handled by deterministic rules. Record which mechanism produced each finding. Do not treat an LLM judge as ground truth; audit a sample and report its limitations.

## 7. Backend Strategy

### Required: controlled JSON/key-value baseline
Implement a small host store first. It is fully controllable, makes it easy to generate reproducible transitions, and helps debug the validator independently of third-party write-path behavior. It is an experimental control, not evidence of broad compatibility by itself.

### Primary integration 1: Mem0
Test the exact library version and determine whether proposed updates can be intercepted before persistence. Record which data and provenance fields the adapter can access. If no clean hook exists, document any wrapper or fork rather than describing the integration as plug-and-play.

### Primary integration 2: A-Mem
Run the same feasibility test for note creation and updates to existing notes. Pay particular attention to preservation because a new note may trigger changes to prior notes. Document how links and metadata map into the canonical schema.

### Stretch goal: Zep/Graphiti
Attempt only after the core experiment works end to end and the first two integrations are stable. It is a graph-native, temporally oriented host and could test whether MemBud adds value when the host already represents relations. Do not make the paper depend on completing this integration.

**Early go/no-go test:** Within week 1, produce a minimal script for each proposed backend that creates or updates one memory and identifies the exact point at which MemBud could intervene. If an integration is too costly, use the JSON/key-value baseline plus the most feasible real backend and report the reduced scope accurately.

## 8. Experimental Design

### 8.1 Conditions
1. **Unguarded:** Apply the host's proposed update without MemBud verification.
2. **Non-graph verifier:** Check the transition using the three criteria from a textual representation.
3. **Graph verifier:** Check the same transition using the canonical graph.

Run all conditions on the same fixed cases. Where possible, hold the host update model constant and use the same verifier model for graph and non-graph checks. If the host architecture cannot be configured to use the same model, report that as a confound rather than implying a perfectly controlled comparison.

### 8.2 Dataset
Use a fixed, documented subset of HaluMem if its operation-level annotations support the planned measurements. Before committing to it:
- Inspect the released data schema and license/usage conditions.
- Confirm which labels identify omissions, corruption, and unsupported additions.
- Define selection criteria before examining comparative results.
- Save case IDs and a dataset manifest so every condition uses identical cases.
- Keep any tuning cases separate from the final evaluation cases.

If the labels do not support one of the proposed metrics, state that limitation and use a separate, explicitly described annotation procedure or remove the unsupported metric. Do not infer ground truth from the verifier's own outputs.

### 8.3 Fairness controls
- Pin the exact API model identifier and record the experiment date.
- Use deterministic settings where supported (for example, temperature 0); note that API output may still vary.
- Give graph and non-graph verifiers equivalent evidence, prior-memory context, output schema, and retry policy.
- Set a shared token/cost budget or report both matched-budget and natural-cost comparisons.
- Cache model calls using a key that includes model, prompt version, parameters, and input hash.
- Store raw responses and parsed outputs so results can be reanalyzed without paying for repeated calls.
- Do not tune thresholds on final test cases.
- Run the same cases in all conditions and report failures and missing outputs.

## 9. Metrics

Report metrics separately by backend, condition, and error type where sample size permits.

### Primary reliability metrics
- **Omission rate:** Fraction of required supported information omitted.
- **Corruption rate:** Fraction of valid prior records incorrectly changed or removed.
- **Unsupported-addition rate:** Fraction of material stored claims unsupported by the evidence.
- **Error detection rate / recall:** Fraction of labeled faulty transitions flagged by the verifier.
- **False-rejection rate:** Fraction of valid transitions incorrectly held or rejected.
- **Valid-update acceptance rate:** Fraction of valid transitions allowed to commit.

Define denominators explicitly. A high detection rate is not sufficient if it is achieved by holding almost every update.

### Utility and cost metrics
- Downstream QA exact match/F1 or the metric appropriate to the selected dataset.
- End-to-end latency and verifier-only latency.
- Input/output tokens, API calls, retry count, and estimated API cost.
- Graph-construction time and size (nodes/edges).
- Decision distribution (`ACCEPT`, `REVISE`, `HOLD`).
- Adapter failures and unavailable fields.

Report uncertainty where feasible, such as bootstrap confidence intervals over cases. Include a small manual review of false positives and false negatives. If an LLM is used as an evaluator, disclose that dependence and validate a sample independently.

## 10. Implementation Stack and Repository Layout

Use the simplest stack the team can maintain:
- Python 3.11+
- Pydantic for schemas
- `pytest` for unit/integration tests
- SQLite or JSONL for experiment logs and cached results
- A provider-neutral LLM client wrapper
- `pandas` for analysis
- `networkx` for the initial in-memory graph, unless a more specialized graph package is justified

Suggested layout:

```text
membud/
├── README.md
├── plan.md
├── pyproject.toml
├── .env.example
├── configs/
│   ├── experiment.yaml
│   └── prompts/
├── data/
│   ├── manifests/
│   └── README.md
├── src/membud/
│   ├── schemas.py
│   ├── graph_builder.py
│   ├── checks/
│   │   ├── coverage.py
│   │   ├── preservation.py
│   │   └── faithfulness.py
│   ├── decision.py
│   ├── adapters/
│   │   ├── base.py
│   │   ├── json_store.py
│   │   ├── mem0_adapter.py
│   │   └── amem_adapter.py
│   ├── llm/
│   │   ├── client.py
│   │   ├── cache.py
│   │   └── prompts.py
│   ├── experiments/
│   │   ├── runner.py
│   │   └── metrics.py
│   └── logging.py
├── tests/
│   ├── test_schemas.py
│   ├── test_graph_builder.py
│   ├── test_checks.py
│   ├── test_decision.py
│   └── test_adapters.py
├── scripts/
│   ├── smoke_test_backend.py
│   ├── run_experiment.py
│   └── analyze_results.py
└── results/
    └── README.md
```

Do not commit API keys, private conversation data, or benchmark files when the license does not permit redistribution. Keep secrets in environment variables and provide `.env.example` without real credentials.

## 11. API Reliability and Reproducibility

Implement these before running large experiments:
- Structured-output validation and a bounded retry for malformed responses.
- Request timeout and rate-limit handling with exponential backoff.
- Persistent cache keyed by model, prompt version, parameters, and input hash.
- Append-only experiment logs with a unique run ID.
- Checkpoint/resume behavior so interrupted runs do not repeat completed calls.
- Per-call input/output token counts, latency, status, and provider-reported cost where available.
- A record of model identifier, API/provider, date, prompt version, configuration, dataset manifest, and code revision.

Use synthetic or benchmark data with permission for API processing. Do not send real personal conversations or confidential project information to an external API.

## 12. Testing Strategy

### Unit tests
- Schema validation and canonicalization.
- Graph node/edge construction and provenance retention.
- Coverage, preservation, and faithfulness behavior on small hand-constructed cases.
- Decision mapping for pass, fail, uncertain, and malformed outputs.
- Cache-key uniqueness and log serialization.

### Integration tests
- One end-to-end update through the JSON/key-value baseline.
- One valid update accepted and committed.
- One unsupported addition held.
- One update that omits required information flagged.
- One update that corrupts a valid prior record flagged.
- A failed verifier response does not silently commit an update.
- Adapter round-trip tests document information lost during conversion.

### Smoke test
Before a large run, execute 10–20 cases through every available condition and inspect raw outputs, token usage, failure handling, and resulting memory states.

## 13. Timeline (8-Week Target)

| Week | Deliverable | Exit criterion |
|---|---|---|
| 1 | Scope, data inspection, backend feasibility tests | Dataset labels understood; write-path interception tested; go/no-go decision recorded |
| 2 | Canonical schema, JSON store, logs/cache | Reproducible transition can be represented and replayed |
| 3 | Graph builder and baseline pipeline | Unguarded, graph, and non-graph paths run on toy cases |
| 4 | Three checks and decision policy | Unit tests cover the three error types and valid updates |
| 5 | First real backend adapter and pilot | 10–20 cases run end to end; failure modes reviewed |
| 6 | Second backend if feasible; frozen evaluation subset | Case manifest and configurations frozen; smoke test passes |
| 7 | Main runs, cost logging, error analysis | Results available for all planned conditions; missing runs documented |
| 8 | Metrics, figures, ablations if feasible, final report | Research questions answered with limitations and reproducible artifacts |

For a four-week schedule, prioritize the JSON store plus one real backend, the graph/non-graph comparison, and the primary reliability metrics. Treat the second real backend, second-model robustness check, and Graphiti as stretch goals.

## 14. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Backend does not expose a pre-commit hook | Test in week 1; use a documented wrapper/fork or drop that integration |
| Graph representation adds no measurable value | Treat this as a valid empirical result; analyze error types rather than overclaiming |
| False rejection blocks valid updates | Measure false-rejection rate and valid-update acceptance alongside error detection |
| LLM verifier hallucinates or misjudges evidence | Save outputs, use evidence references, test on hand-built cases, and manually audit a sample |
| API cost or rate limits exceed budget | Pilot first, cache calls, checkpoint runs, cap retries, and reduce the fixed subset if necessary |
| Backend adapters expose different evidence | Document missing fields and compare only cases with equivalent evidence where possible |
| Dataset labels do not fit the metrics | Inspect annotations before implementation; revise metrics before freezing the test set |
| Scope expands beyond the deadline | Make Graphiti, second-model robustness, and extensive ablations optional |

## 15. Definition of Done

The initial research prototype is complete when:
1. A proposed update can be represented as a validated canonical transition package.
2. The same case can be evaluated through graph and non-graph verification paths.
3. Coverage, preservation, and faithfulness results produce a recorded decision.
4. An unsafe or uncertain update cannot be committed silently in the guarded path.
5. At least the JSON baseline and one real backend run end to end, with adapter limitations documented.
6. All experimental conditions use a fixed case manifest and saved configuration.
7. Raw outputs, decisions, metrics, token usage, latency, and cost are logged.
8. The report compares reliability, false rejection, downstream utility where supported, and computational cost.
9. Conclusions distinguish measured findings from design intentions and limitations.

## 16. Immediate Next Actions

1. Inspect the HaluMem data schema and confirm the labels, license, and subset-selection method.
2. Create the repository and implement the JSON/key-value baseline.
3. Run minimal Mem0 and A-Mem write-path smoke tests; decide which integration is feasible.
4. Define the canonical transition and graph schema before building adapters.
5. Implement caching and structured logging before making repeated API calls.
6. Build a small hand-authored test set for omissions, corruption, unsupported additions, and valid updates.
7. Run a 10–20-case pilot and estimate total calls, tokens, latency, and cost before scaling.

## 17. Literature Positioning Notes

The plan adopts coverage, preservation, and faithfulness as criteria discussed in TRUSTMEM; it does not claim these criteria as MemBud's novelty. HaluMem is the proposed source for operation-level evaluation, subject to confirming the released annotations. LoCoMo may support downstream long-term-memory QA if its tasks fit the final evaluation design.

Mem0, A-Mem, and Zep/Graphiti are candidate host systems, not guaranteed integrations. The graph-versus-textual-verifier comparison is the central hypothesis. The claim that graph structure improves verification must be established by controlled results and a sufficiently careful literature review, not stated as a fact in advance.

Before submission, verify bibliographic details and implementation claims against the actual papers, repositories, and versions used.
