# Appendix — Bab 4 table reproduction and source notes

The commands below were executed with `.venv/bin/python` from the workspace root while preparing this file; each printed exactly the values used above. At the pinned basis the working-tree artifacts were byte-identical to `git show 1ce84ce:<path>` (SHA-256 compared), so reading the working-tree path is equivalent to reading the committed artifact.

1. Decision counts and split counts (Table 4.3):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['decision_counts'],m['condition_metrics']['text']['decision_counts'],m['condition_metrics']['unguarded']['decision_counts'])"`
2. HOLD-only as-completed / ITT blocks (Tables 4.4, 4.5):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['text']['as_completed'],m['condition_metrics']['text']['fail_closed_itt'])"`
3. Combined view and its per-split blocks (Table 4.6):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['faulty_detection'],m['condition_metrics']['text']['faulty_detection'])"`
4. Invalid-input blocks (Table 4.7):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['invalid_input'],m['invalid_input_summary'])"`
5. Candidate-error rates (Table 4.2):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['candidate_error_rates'])"`
6. Paired graph-vs-text differences (Table 4.8):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['paired_differences'])"`
7. Fairness-audit strategies and contrasts (Tables 4.9, 4.10):
   `.venv/bin/python -c "import json;a=json.load(open('data/results/fairness-audit-v2/audit.json'));print(a['per_strategy']['text_fair']);print(a['paired_differences'])"`
8. Reason codes (Table 4.11):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print(m['condition_metrics']['graph']['reason_code_counts'],m['condition_metrics']['text']['reason_code_counts'])"`
9. Held-out decisions and outcomes (Table 4.12):
   `.venv/bin/python -c "import json;m=json.load(open('data/results/llm-heldout-v2/metrics.json'));print({c:{k:m['condition_metrics'][c][k] for k in ('decision_counts','false_accepts','false_holds','valid_acceptance','faulty_detection_hold_only','faulty_detection_combined','case_coverage')} for c in ('graph','text','semantic')})"`
10. Held-out tokens, attempts, latency, cost (Tables 4.13, 4.14):
    `.venv/bin/python -c "import json;m=json.load(open('data/results/llm-heldout-v2/metrics.json'));print(m['run']['usage']['reported'],m['run']['estimated_cost_range_usd'],m['run']['budget']);print({c:(m['condition_metrics'][c]['attempts'],m['condition_metrics'][c]['mean_latency_ms'],m['condition_metrics'][c]['usage']['reported'],m['condition_metrics'][c]['estimated_cost_range_usd']) for c in ('graph','text','semantic')})"`
11. Rules timing and graph size (Table 4.15):
    `.venv/bin/python -c "import json;m=json.load(open('data/results/evaluation-v2/metrics.json'));print({c:m['condition_metrics'][c]['timing_ms'] for c in ('graph','text','unguarded')})"`
12. Derived ratios (Table 4.13 notes):
    `.venv/bin/python -c "print(63211/28487, 20432.41176923077/8533.353076923077, 2/36)"`

## Source notes and discrepancies

1. **Rules-run code revision.** The rules run records `code_revision = working-tree-sha256:044a09c92325d90499c00616222144c5d0ea81d59b2ef5c769c71fbdebd92bfc`, a working-tree digest rather than a git commit; the mapping to a commit is not recorded in the artifact. The paper's evaluation basis is the committed artifact set at `1ce84ce` (hash pins in the table above), and this chapter does not claim that the rules run is pinned to that commit. The held-out LLM artifacts record `41905266e29a4ae699655d8b66e92474a4ee8921`, and this chapter cites that revision for the model-based computation.
2. **Unguarded valid-acceptance rendering.** `docs/fairness-audit.md` prints the unguarded "Valid accepted" cell as "—"; `audit.json` records `14/14` with rate 1.0, which is raw application, not verifier acceptance. Table 4.9 shows the value with that qualifier; both sources are consistent once the semantics are stated.
3. **Narrative 12/12 claim in the milestone report.** `docs/technical-report-milestone-2.md` §7 contains a bullet stating that both conditions detect 12/12 HOLD cases with 0 false accepts; the artifact (`metrics.json` → `$.condition_metrics.text.as_completed`) records text detection `10/12` and false accepts `2/12`. The artifact is authoritative, and Table 4.4 follows it. The report's later §11 addendum and `docs/fairness-audit.md` agree with the artifact.
4. **Narrative cost figure.** The pinned `docs/llm-heldout.md` mentions an approximate 0.40 USD upper bound "at DeepSeek's top published per-token rate". That figure is not in any JSON artifact; the artifact envelope at 2.00 USD per million accounted tokens is 0.202462 USD. Neither is a billed amount; the provider reported no billing data. Table 4.13 reports the artifact range only.
5. **Held-out false-hold cause.** The pinned artifacts record the decisions and the per-case check outcomes for the four graph false holds (all four fail `preservation` with `PRIOR_RECORD_UNAUTHORIZED_CHANGE`; `HELD-01` also fails `coverage` with `REQUIRED_CLAIM_OMITTED`), but they do not establish a cause. This chapter does not attribute the false holds to serialization length, token budget, or any other mechanism.
6. **Semantic annotation gap.** At the evaluated revision `4190526`, the semantic module does not consume `evidence.contradicts`; the held-out artifact records `HELD-13` as semantic `ACCEPT` with the `semantic` check `PASS` (`SEMANTIC_OK`). Later revisions are outside the evaluated scope and are not reported in this chapter.
