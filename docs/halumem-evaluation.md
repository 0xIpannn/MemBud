# HaluMem RQ1 evaluation: Phase 1 offline and Phase 2 natural errors

This document describes the HaluMem-based test bed for MemBud's RQ1:

> (a) Does MemBud reduce omissions, corruption of valid prior records, and
> unsupported additions relative to an unguarded pipeline? (b) Does graph-based
> verification catch more errors than a non-graph verifier given the same
> criteria and budget?

The implementation is split into:

| Phase | Source | Network | Status in this repository |
| --- | --- | --- | --- |
| 1. Controlled, offline | `src/membud/halumem.py`, `scripts/run_halumem_eval.py` | none | Implemented and run on the synthetic fixture |
| 2. Natural errors from real Mem0 | `scripts/run_halumem_mem0.py`, `src/membud/llm/client.py` | OpenRouter only, after confirmation | Implemented, **not run on real HaluMem** (see [Phase 2 status](#phase-2-status)) |

No HaluMem content, transformed cases, Mem0 proposals, judge outputs, or derived
annotations are committed. All such artifacts belong under `halumem-output/`,
which is gitignored together with `data/halumem/` and `*halumem*.jsonl`.

## Phase 1: controlled, offline

### Mapping from HaluMem to MemBud

HaluMem is a multi-session dialogue dataset. Each user row contains a persona and
chronological sessions; each session contains a dialogue, gold `memory_points`,
a timestamp, and questions. MemBud needs a *proposed state transition* with a
prior state, a candidate state, evidence, required claims, and declared
operations.

| HaluMem field | MemBud field | Rule |
| --- | --- | --- |
| `uuid` | `Transition.case_id` prefix / `scope.user_id` | Stable per user. |
| `sessions` (in file order) | One `Transition` per session | `session_id` is appended to `case_id`. |
| Accumulated gold memory from earlier sessions | `before` (`Record` list) | Starts empty; advances after every session, including ungroundable ones. |
| Gold `memory_points` after the session | `candidate` | `is_update=false` points become new records; `is_update=true` points revise/supersede the record whose claims contain `original_memories`. |
| `memory_points[].memory_content` | `Record.claims` | Exact string, one claim per record for HaluMem-derived gold states. |
| `memory_points[].is_update` | Operation type | `false` -> `WRITE`; `true` -> `REVISE` (default) or `SUPERSEDE` (`--update-operation SUPERSEDE`). |
| `memory_points[].original_memories` | Operation target + retired claims | Must resolve to a claim in `before`; otherwise the session is reported as ungroundable. |
| `memory_points[].memory_source` | Evidence source hint | Used first as an explicit turn ID, then as a verbatim quote inside a user turn. |
| User-role `dialogue` turns | `Evidence.source`, `Evidence.text` | The quote is always a verbatim user turn; never synthesized. |
| Prior claims not retired by an update | `required_claims` | Every surviving prior claim is required to persist. |
| `dialogue` + `memory_points` | `ConversationTranscript` | Stored on the `HaluMemTransitionCase` for transcript-grounded verification. |

### Evidence assumption

HaluMem memory points are annotated at the session level and do not consistently
carry direct `turn_id` pointers. The builder therefore uses this deterministic
order:

1. If `memory_source` resolves to a user-role turn ID and the claim is grounded
   in that turn, use it (`explicit_turn_ref`).
2. If `memory_source` appears verbatim inside a user-role turn and the claim is
   grounded there, use that turn (`explicit_turn_quote`).
3. Otherwise, choose the user-role turn with the highest content-token overlap
   with the claim (`lexical_match`). The quote is the full verbatim turn.
4. If no user turn has any content-token overlap, report the point as
   `no_user_turn_lexical_match` and exclude the session. No evidence is invented.

The constant `LEXICAL_EVIDENCE_ASSUMPTION` in `src/membud/halumem.py` records
this assumption in every run report. An `is_update` point whose
`original_memories` cannot be found in the prior gold state is reported as
`missing_original_memory_in_prior_state`. A point that would consolidate
multiple distinct prior records is reported as
`multi_record_consolidation_without_delete_authorization`, because deleting the
secondary records would require an explicit user deletion directive.

Ungroundable sessions are excluded from the evaluation cases, but their gold
memory still advances so later sessions for the same user stay synchronized with
HaluMem's reference state.

### Seeded fault injector

`inject_halumem_faults(cases, seed=...)` produces, for each valid gold
transition:

| Variant | Construction | Expected decision |
| --- | --- | --- |
| `valid` | The unmodified gold transition. | `ACCEPT` |
| `omission_prior_overwrite` | Redirect an evidence-backed new claim onto a surviving prior record, so the prior required claim disappears while all added claims remain evidence-backed. | `HOLD` (`REQUIRED_CLAIM_OMITTED`) |
| `corruption_unbacked` | Mutate a surviving prior record to a claim that is not supported by the session transcript. | `HOLD` (`PRIOR_RECORD_UNAUTHORIZED_CHANGE`, `UNSUPPORTED_CLAIM`) |
| `corruption_contradicted` | When a gold update carries grounded `evidence.contradicts`, retain the contradicted old claim next to the new one. | `HOLD` (`CONTRADICTED_CLAIM`) |
| `unsupported_unbacked` | Append a fabricated claim with a `WRITE` operation and no evidence. | `HOLD` (`UNSUPPORTED_CLAIM`) |
| `unsupported_spoofed_evidence` | Append a fabricated claim and falsely declare a real user turn as its support. | `HOLD` only with transcript grounding (`UNGROUNDED_EVIDENCE_CLAIM`) |

Selection is deterministic from `SHA-256(seed | case_id | variant)`. The seed is
recorded in every label. The injector's labels are **circular by construction**:
the injector creates the fault and the label at the same time, so Phase 1
measures mechanism sensitivity, not natural error prevalence. This limitation is
recorded in every label and report.

### Phase 1 results

The Phase 1 report below was produced on the built-in **synthetic
HaluMem-shaped fixture** (6 users, 18 sessions, 17 groundable transitions, 66
evaluation cases), not on real HaluMem. It uses seed `20261121` and 4000 paired
bootstrap resamples.

Dataset: `synthetic-halumem-fixture-v1`, SHA-256
`5bbf4c5ab878...` (see `halumem-output/phase1-offline/report.json` for the full
digest). 1 session is ungroundable and excluded. 17 valid cases, 11 omission
cases, 21 corruption cases, 17 unsupported-addition cases.

**Detection by fault type (detected / total):**

| Track | Condition | Omission | Corruption | Unsupported | Valid accepted |
| --- | --- | --- | --- | --- | --- |
| without transcript grounding | `unguarded` | 0/11 | 0/21 | 0/17 | 17/17 |
| without transcript grounding | `graph` | 11/11 | 21/21 | 9/17 | 17/17 |
| without transcript grounding | `text` | 11/11 | 11/21 | 9/17 | 17/17 |
| without transcript grounding | `text_fair` | 11/11 | 21/21 | 9/17 | 17/17 |
| with transcript grounding | `unguarded` | 0/11 | 0/21 | 0/17 | 17/17 |
| with transcript grounding | `graph` | 11/11 | 21/21 | 17/17 | 17/17 |
| with transcript grounding | `text` | 11/11 | 11/21 | 17/17 | 17/17 |
| with transcript grounding | `text_fair` | 11/11 | 21/21 | 17/17 | 17/17 |

**Paired comparisons.** Differences are `left_correct - right_correct` over
paired case units; brackets are 95% paired-bootstrap intervals; `p` is the exact
two-sided McNemar p-value.

| Track | Comparison | Units | Left only | Right only | Difference [95% CI] | McNemar p |
| --- | --- | --- | --- | --- | --- | --- |
| without grounding | `graph` vs `text_fair` (overall) | 66 | 0 | 0 | +0.0000 [+0.0000, +0.0000] | 1.0 |
| without grounding | `graph` vs `text_fair` (omission) | 11 | 0 | 0 | +0.0000 [+0.0000, +0.0000] | 1.0 |
| without grounding | `graph` vs `text_fair` (corruption) | 21 | 0 | 0 | +0.0000 [+0.0000, +0.0000] | 1.0 |
| without grounding | `graph` vs `text_fair` (unsupported) | 17 | 0 | 0 | +0.0000 [+0.0000, +0.0000] | 1.0 |
| without grounding | `graph` vs `unguarded` (faulty only) | 49 | 41 | 0 | +0.8367 [+0.7347, +0.9388] | <1e-9 |
| without grounding | `graph` vs `unguarded` (omission) | 11 | 11 | 0 | +1.0000 [+1.0000, +1.0000] | 0.00098 |
| without grounding | `graph` vs `unguarded` (corruption) | 21 | 21 | 0 | +1.0000 [+1.0000, +1.0000] | 9.5e-7 |
| without grounding | `graph` vs `unguarded` (unsupported) | 17 | 9 | 0 | +0.5294 [+0.2941, +0.7647] | 0.0039 |
| with grounding | `graph` vs `text_fair` (overall) | 66 | 0 | 0 | +0.0000 [+0.0000, +0.0000] | 1.0 |
| with grounding | `graph` vs `text_fair` (all fault types) | 49 | 0 | 0 | +0.0000 [+0.0000, +0.0000] | 1.0 |
| with grounding | `graph` vs `unguarded` (faulty only) | 49 | 49 | 0 | +1.0000 [+1.0000, +1.0000] | <1e-9 |
| with grounding | `graph` vs `unguarded` (omission) | 11 | 11 | 0 | +1.0000 [+1.0000, +1.0000] | 0.00098 |
| with grounding | `graph` vs `unguarded` (corruption) | 21 | 21 | 0 | +1.0000 [+1.0000, +1.0000] | 9.5e-7 |
| with grounding | `graph` vs `unguarded` (unsupported) | 17 | 17 | 0 | +1.0000 [+1.0000, +1.0000] | 1.5e-5 |

**Interpretation.** Guarded verification detects every injected fault once
transcript grounding is enabled. `graph` and `text_fair` tie on every case,
consistent with the repository's prior finding: with equal information they make
the same decisions. Plain `text` loses only on the `corruption_contradicted`
variant because it ignores `evidence.contradicts` (11/21 vs 21/21). Without
transcript grounding, the spoofed-evidence addition is indistinguishable from a
supported claim (8/17 false accepts for `graph` and `text_fair`); transcript
grounding closes that gap. **No graph-superiority claim is supported.**

### Run commands

```bash
# Synthetic fixture (no HaluMem path, no network, no spend)
PYTHONPATH=src .venv/bin/python scripts/run_halumem_eval.py \
  --output halumem-output/phase1-offline --resamples 4000

# User-supplied local HaluMem file
PYTHONPATH=src .venv/bin/python scripts/run_halumem_eval.py \
  --halumem-path /path/to/HaluMem-Medium.jsonl \
  --dataset-revision medium-<revision> \
  --output halumem-output/phase1-real --resamples 4000

# Resume after an interruption
PYTHONPATH=src .venv/bin/python scripts/run_halumem_eval.py \
  --output halumem-output/phase1-real --resume
```

The report records the file SHA-256, dataset revision, seed, condition list,
checkpoint fingerprint, per-condition numerators/denominators, Wilson intervals,
paired bootstrap intervals, exact McNemar p-values, and reason-code counts. It
does not record dialogue or claim text.

## Phase 2: natural errors from a real Mem0 run

### Design: propose once, judge many

For each sampled session, `scripts/run_halumem_mem0.py`:

1. Runs **unmodified `mem0ai==2.2.1`** once through `StagedMem0`, with Mem0's LLM
   backed by the OpenRouter client and its embedder replaced by a **local**
   embedder (no embedding network calls).
2. Records the derived schema-v2 transition and Mem0's raw output under
   `halumem-output/phase2-mem0/proposals/`.
3. Evaluates `unguarded`, `graph`, `text`, and `text_fair` **offline** on those
   identical recorded transitions. No verifier condition triggers a model call.
4. Derives provisional silver labels and builds a blind human-audit quiz.

The sampling rule is seeded and written to `run-config.json` before the run:
`seeded_random_users_first_n_sessions_each`, default 20 users × first 2 sessions
= 40 sessions. The justification is the OpenRouter free-tier request budget
(typically 20 RPM / 50 RPD without credit), at concurrency 1.

### Local embedder

OpenRouter does not reliably serve embeddings. The default local embedder is the
pinned spaCy `en_core_web_sm-3.8.0` tok2vec model (96 dimensions, zero network
calls), recorded in `requirements-mem0.txt` and in the run config with its
package version. `--embedder hash` and `--embedder fastembed` are available for
tests and alternate local stacks.

The Mem0 2.2.1 configuration used by the runner was checked against the
installed package: `OpenAIConfig` exposes `openrouter_base_url` and
`openai_base_url`, and `BaseEmbedderConfig` exposes `embedding_dims`. The runner
constructs `Memory` with a Qdrant on-disk store, then replaces
`memory.llm` with a `ClientBackedMem0LLM` adapter and
`memory.embedding_model` with the local embedder. It does not modify
`StagedMem0`'s derivation logic.

### OpenRouter provider and zero-price guard

`src/membud/llm/client.py` now supports `provider="openrouter"`:

- Base URL `https://openrouter.ai/api/v1` (OpenAI-compatible chat completions).
- Key from `OPENROUTER_API_KEY` or a gitignored `0600` `.env`.
- Model from `OPENROUTER_MODEL`; cache from `OPENROUTER_CACHE_PATH`.
- `fetch_openrouter_models()` reads the public `/models` catalog.
- `resolve_openrouter_model_pricing()` requires an exact slug match and parses
  prompt, completion, and request prices.
- `enforce_openrouter_zero_price()` refuses to start unless every resolved price
  is `$0`, unless `--allow-paid` is explicitly passed.
- Provider error bodies are redacted before an exception message is built, and
  the key is never included in `repr(client)` or `client.configuration`.

### Phase 2 status

**Phase 2 was not run on real HaluMem.** Two preconditions are unmet:

1. No local HaluMem path was supplied in this workspace. The runner defaults to
   the synthetic fixture when `--halumem-path` is omitted.
2. The requested free DeepSeek V4.1 Flash variant does not exist in the
   OpenRouter catalog as of **2026-11-21**. The catalog was queried directly:

   | Model ID | Prompt ($/token) | Completion ($/token) | Free? |
   | --- | --- | --- | --- |
   | `deepseek/deepseek-v4.1-flash` | `0.0000000498` | `0.0000001992` | No |
   | `deepseek/deepseek-v4.1-flash-1017` | `0.00000015` | `0.0000006` | No |
   | `deepseek/deepseek-v4.1-flash:batch` | `0.00000005` | `0.00000015` | No |

   The only zero-priced models in the catalog are other families (for example
   `inclusionai/ling-3.2-flash:free`, `google/gemma-4-31b-it:free`); there is no
   zero-priced DeepSeek V4.1 Flash. Per the task instructions, the run stops
   here and asks for explicit approval before using any non-free model.

**Decision (2026-11-21): use a zero-priced model instead of approving spend.**
The paid DeepSeek slug is not used. The selected model is:

| Model ID | Prompt | Completion | Request | Context | JSON mode |
| --- | --- | --- | --- | --- | --- |
| `inclusionai/ling-3.2-flash:free` | `$0` | `$0` | `$0` | 1,048,576 | `response_format`, `structured_outputs` |

Fallback if the primary endpoint is unavailable or rate-limited:
`baidu/ernie-5.5-flash-preview:free` (also `$0` prompt/completion/request,
1,024,000 context, `response_format` + `structured_outputs`). Both were verified
against `https://openrouter.ai/api/v1/models` on 2026-11-21; only slugs whose
prompt, completion, and request prices parse to exactly zero are eligible.

Because the model is weaker than the originally requested DeepSeek slug, any
Phase 2 result is **model-dependent** and must be reported with the resolved
slug and its catalog entry. The zero-price guard and `--allow-paid` refusal are
unchanged.

**Budget caveat.** Free-tier limits are typically 20 RPM / 50 RPD without a
credit balance. Mem0 2.2.1 performs more than one LLM call per `add()` (fact
extraction plus an update decision), so the pre-registered 20 users × 2 sessions
= 40 sessions can exceed 50 requests in a single day. Run with `--max-requests`
set to the account's actual daily budget, checkpoint/resume across days
(`--resume`), and record the account tier in the run notes. Do not raise
`--allow-paid` to work around the limit.

Phase 2 still needs, before it can run on real data:

1. A local HaluMem file (`HaluMem-Medium.jsonl` or `HaluMem-Long.jsonl`) passed
   via `--halumem-path`. **Outstanding**: no such file exists in this workspace.
2. An `OPENROUTER_API_KEY` in a private `0600` `.env`. **Outstanding**.
3. Explicit confirmation that sending HaluMem dialogue (CC BY-NC-ND 4.0) to a
   free OpenRouter endpoint, under the account's current privacy/data-policy
   settings, is acceptable — supplied as `--confirm-openrouter-data-policy`.
   **Confirmed by the user on 2026-11-21**; the run will still pass the flag
   explicitly, and the confirmation applies to the account's settings at run
   time.

The code path is exercised offline by tests with an injected proposal stager, so
the propose-once/judge-many pairing, silver-label derivation, blind quiz
generation, cost accounting, checkpoint/resume, and budget caps are covered
without network access.

### Silver labels

Natural proposals have no injection label. `derive_silver_label()` compares the
proposed added, changed, and removed claims against the session's gold
`memory_points` and the prior state:

- **omission**: a gold point for the session, or a prior claim that should
  survive (not retired by an `is_update` point), is missing from the candidate.
- **corruption**: a prior record that should not change was removed or altered;
  or a prior record retired by an `is_update` still carries the retired claim.
- **unsupported_addition**: a new claim matches neither gold nor the session's
  user-turn content tokens (with a numeral-conflict guard).

Ambiguous lexical overlaps (Jaccard 0.15–0.5) may consult an optional cached LLM
claim matcher that uses the same free model. Every judge call is logged to
`judge-calls.jsonl`. All labels are marked
`provisional_silver_unvalidated`, `is_ground_truth: false`, and are not used to
claim validated results before human review.

The labeler also records `paraphrased_valid_claims_ungrounded_by_lexical`:
claims that are valid by gold/dialogue but receive no `evidence.supports` entry
from StagedMem0's strict lexical grounding. These are reported as a separate
false-hold finding, not used to weaken grounding.

### Human audit

`select_stratified_audit_sample()` selects a seeded, round-robin sample across
the silver categories (target 100, or all proposals if fewer).
`build_phase2_blind_quiz()` writes a hash-pinned manifest, a label-free case
file, a separate label file, and a self-contained blind HTML quiz via
`scripts/review_quiz.py`. The case file is rejected by the quiz builder if it
contains any label-only field.

After the reviewer returns answers:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_halumem_mem0.py \
  --output halumem-output/phase2-mem0 \
  --grade-human-audit /path/to/downloaded-answers.json
```

The report remains `unvalidated_pending_human_audit` until every case is
answered; only then is `validation_status` set to `human_audit_graded`.

### Phase 2 run commands (after approval)

```bash
# 1. Install the pinned Mem0 stack (Python 3.12)
uv venv --python 3.12 .venv-mem0
VIRTUAL_ENV=$PWD/.venv-mem0 uv pip install -r requirements-mem0.txt

# 2. Put the key in a private .env (never commit it)
chmod 600 .env   # OPENROUTER_API_KEY=... and OPENROUTER_MODEL=inclusionai/ling-3.2-flash:free

# 3. Run with the data-policy confirmation and hard caps
MEM0_TELEMETRY=False PYTHONPATH=src .venv-mem0/bin/python \
  scripts/run_halumem_mem0.py \
  --halumem-path /path/to/HaluMem-Medium.jsonl \
  --dataset-revision medium-<revision> \
  --confirm-openrouter-data-policy \
  --output halumem-output/phase2-mem0 \
  --num-users 20 --sessions-per-user 2 \
  --max-requests 120 --max-total-tokens 200000 \
  --embedder spacy

# 4. Resume after a stall
... --resume
```

`--allow-paid` is required to run a non-free model and should not be used
without explicit approval. The runner prints the CC BY-NC-ND / OpenRouter
data-policy warning before any network call and refuses to proceed without
`--confirm-openrouter-data-policy` or an interactive `YES`.

### Cost and dashboard check

Token and request totals are accumulated from `membud.cost` token classes and
reported with `verified_billed: false` (an estimate, not a provider bill).
After a run, verify in the OpenRouter dashboard:

1. `https://openrouter.ai/activity`: filter by the experiment timestamp and
   model; confirm every request was served by the resolved `$0` endpoint and the
   credit delta is `$0.00`.
2. `https://openrouter.ai/settings/privacy`: confirm the prompt-logging /
   training setting matches the experiment record.

### Findings and bugs

- **Fixed: `SCHEMA_VERSION` NameError in the Phase 2 fallback path.**
  `scripts/run_halumem_mem0.py` used `SCHEMA_VERSION` in
  `_fallback_transition_from_outcome()` without importing it. The fallback runs
  when `StagedMem0` returns no `GuardedDecision`; it would have crashed exactly
  when a rejected/errored Mem0 proposal needed recording. Covered by
  `tests/test_halumem_eval.py::test_phase2_fallback_transition_builder_uses_schema_version`.
- **StagedMem0 does not pass `required_claims` to the bridge.**
  `StagedMem0._execute()` calls `prepare_proposals()` without
  `required_claims`, so the bridge defaults to `()`. Coverage
  (`REQUIRED_CLAIM_OMITTED`) is therefore vacuously PASS on StagedMem0-derived
  transitions unless the caller supplies required claims. Phase 2's silver
  labeler still detects omissions against the prior state, but the verifier's
  coverage check cannot. This is reported as a finding, not patched inside
  `StagedMem0`.
- **`mem0ai==2.2.1` `add()` is additive in the V3 phased batch path.** It emits
  `ADD` proposals (mapped to `WRITE`) with MD5 exact-string dedup and does not
  emit `UPDATE`/`DELETE` during `add()`, so contradictory multi-session updates
  can accumulate alongside stale prior records. This is a property of the real
  system being measured.
- **StagedMem0's default `LexicalGrounding` is strict.** Every stemmed content
  token of an extracted claim must appear in a single user turn. Paraphrased or
  multi-turn-synthesized claims that are silver-valid can receive no
  `evidence.supports` entry and be held with `UNSUPPORTED_CLAIM`. The runner
  reports this false-hold rate separately and does not loosen grounding.

## Limitations

1. Phase 1 uses a synthetic fixture in this workspace. The numbers above are a
   mechanism check, not a HaluMem result. A real HaluMem run requires the local
   `HaluMem-Medium/Long.jsonl` path.
2. Phase 1 fault labels are assigned by construction (circularity); they do not
   estimate natural error prevalence.
3. The lexical evidence matcher is an assumption, not HaluMem ground truth.
   Ungroundable sessions are excluded rather than given fabricated evidence.
4. HaluMem memory points are session-level; the mapping to per-record states and
   operations is a reconstruction, not the dataset's native representation.
5. Phase 2 silver labels are provisional and unvalidated until the human audit
   is returned and graded. The human audit sample is small by design and is not
   a substitute for a fully annotated test set.
6. Phase 2 was not run on real HaluMem because no dataset path was supplied and
   the requested DeepSeek V4.1 Flash variant is paid, not free. No non-free model
   was used.
7. Free OpenRouter endpoints may log or train on prompts; the data-policy gate
   records the operator's confirmation but cannot enforce the upstream policy.
8. Token/cost figures are telemetry estimates (`verified_billed: false`), not
   provider bills.
9. No graph-superiority claim is made: `graph` and `text_fair` tie whenever they
   receive equal information.

## Remaining work for the paper

1. Run Phase 1 on the real HaluMem-Medium and HaluMem-Long splits (after
   obtaining the licensed local files) and report per-fault-type results with
   paired CIs on both splits.
2. Obtain explicit approval for a model choice, or identify a genuinely $0
   OpenRouter model that is adequate for Mem0 extraction; then run the
   pre-registered 20-user × 2-session Phase 2 sample.
3. Review the blind human-audit sample and grade silver-vs-human agreement;
   report disagreement cases and any label-rule revisions as a separate,
   pre-registered change.
4. Decide whether the `StagedMem0` `required_claims` gap should be fixed in the
   adapter (with tests) or reported as a limitation of the real Mem0 baseline;
   do not change it silently between Phase 2 conditions.
5. Report natural error prevalence per type, per-condition catch and false-hold
   rates with paired CIs, OpenRouter free-tier failures/retries, token and
   request totals, and a plain statement of what is and is not shown.

## Real-data findings (HaluMem revision cb04336, run 2026-11-21)

Files were obtained from the official Hugging Face dataset page at the pinned revision and stored outside the repository (`/home/ifan/Repositories/HaluMem/`, with `REVISION` and `SHA256SUMS`). SHA-256: Medium `486fbc13…45634e7`, Long `dfdbed57…55cb0c9`. Each file holds 20 users.

Real-format facts that the synthetic fixture did not capture, and how the code handles them:

- **Extra fields.** Top-level `token_cost`/`total_*`; per-session `title`, `generation_source`, `is_generated_qa_session`, counts; per-point `event_source`, `reason`, `reference_*`, `source_memory_index`, `role`. All are now allowed explicitly (unknown fields still fail closed). Sessions carry `start_time`, not `timestamp`, and Medium has no `session_id` (`s{index}` is used). `memory_points_count` is validated against the parsed list; `dialogue_turn_num` is not (it never equals `len(dialogue)`).
- **`interference` points are deliberate false distractors** (about 18% of points), each pointing at the true memory via `reference_memory_content`. They are excluded from the gold state and used by the silver labeler: a proposed claim closer to a distractor than to any gold point is labeled `unsupported_addition`.
- **`original_memories` is not an exact pointer.** Only 923 of 3,788 entries in Medium equal a prior `memory_content`. The builder resolves by exact match, then by unique best token Jaccard >= 0.6 with identical numerals (`ORIGINAL_RESOLUTION_ASSUMPTION`); anything else is reported as ungroundable. The verifier is unchanged.
- **Long** has 1,030 generated-QA distractor sessions with no memory points; they become NOOP transitions (valid by construction) and inflate the "valid" count there.
- **Yield is low.** Medium: 311 of 1,387 sessions build a fully groundable transition (the rest contain at least one unresolved original, a multi-record consolidation, or an ungroundable point). Any result is about this subset, not about HaluMem as a whole.

Phase 1 results (seed 20261121, with transcript grounding): in both Medium (1,422 cases) and Long (4,508 cases) `graph` and `text_fair` are identical on every case (0 discordant pairs); `graph` detects all injected faults at 1 false hold on valid cases; plain `text` misses corruption that is signalled only through `evidence.contradicts`. These are injected, label-by-construction faults, so this measures mechanism sensitivity, not natural error prevalence, and shows no graph advantage.

Known artifacts: (1) the single false hold on a valid transition is a builder-side inconsistency (evidence marks a prior claim contradicted while it survives in the candidate); the cause is not fully diagnosed. (2) An injector bug where 8 omission labels were false was found and fixed (omission injection now checks its own post-condition; regression test added).

Phase 2 (real Mem0 via OpenRouter) has **not** been run: it needs the OpenRouter key, the free-model check, and the data-policy confirmation described above.

## Phase 2 pilot: real Mem0 2.2.1 on `deepseek-flash` (2026-11-21, partial, unvalidated)

Run: Medium, seed 20261121, requested 20 users x first 5 sessions, local spaCy embedder, DeepSeek's own API (`deepseek-flash`, thinking disabled via `{"thinking": {"type": "disabled"}}`), paid. Command (key from the gitignored 0600 `.env`):

```
DEEPSEEK_MODEL=deepseek-flash .venv-mem0/bin/python scripts/run_halumem_mem0.py \
  --halumem-path <HaluMem-Medium.jsonl> --dataset-revision <rev> \
  --output data/halumem-runs/phase2-deepseek --provider deepseek --allow-paid \
  --max-spend-usd 0.35 --confirm-data-policy --num-users 20 --sessions-per-user 5 \
  --max-requests 160 --max-total-tokens 300000 --seed 20261121
```

Cost controls: `--allow-paid` and `--max-spend-usd` are both required for DeepSeek; the spend estimate uses DeepSeek's published peak list price (cache miss), is persisted across `--resume`, and is an estimate, not a bill.

**Outcome: stopped early by my own 300,000-token cap after 29 of 100 sessions (6 of 20 users).** Real usage was about 10,400 tokens per session, higher than the 9,000 seen in the 2-session smoke test. Estimated spend at peak rates: about $0.106. 29 requests, 0 failures, 0 retries. Because sampling is user-major, the 29 sessions cover only the first 6 sampled users; it is not a random subset of the 100.

**Result: this pilot cannot discriminate between conditions.**
- Every one of the 29 proposals was held by `graph`, `text` and `text_fair` (0 ACCEPT). `unguarded` accepts everything by construction.
- Mem0 adds about 13 claims per session, but `StagedMem0`'s lexical grounding produced only 0.10 evidence items per session, so almost every claim is "unsupported". `required_claims` is always empty (the coverage check is vacuous on `StagedMem0` transitions).
- A verifier that holds everything would score identically ("catch 29/29"). That figure shows nothing about error detection.
- Silver labels: 0/29 valid, 29/29 with an omission, 15/29 with an unsupported addition, 0 corruption. HaluMem's gold is exhaustive (many points per session), so the silver omission rule fires on essentially every session. The false-hold rate on valid proposals cannot be measured because the valid class is empty.
- Silver labels are provisional and unvalidated; a blind review file for the 29 proposals is in `data/halumem-runs/phase2-deepseek/human-audit/`. No human agreement exists yet.

What this does show: with unmodified `StagedMem0` defaults, strict lexical grounding rejects essentially all of Mem0 2.2.1's natural proposals on HaluMem dialogue (paraphrase/synthesis), as predicted in the Phase 2 plan. It does not show that MemBud catches Mem0's real errors, and it makes no graph-superiority claim.

Housekeeping: DeepSeek response caching writes to `~/.cache/membud/deepseek-cache.sqlite3` (outside the repo, never committable) and now contains HaluMem-derived prompts and completions.

## Phase 2 reanalysis plan (pre-registered 2026-11-21, before any reanalysis was run)

Motivation: the pilot above labeled 29/29 proposals faulty and held 29/29, so it cannot discriminate. Two changes are defined here, before looking at results, and applied identically to every condition. Both run offline on the 29 recorded proposals ($0).

1. **Silver label v2, `omission_scope="prior_only"`.** An omission fault is only the loss of a *surviving prior claim* (what the verifier protects). A gold point of the current session that Mem0 did not extract is a recall gap: still recorded (`omitted_gold_points`) and reported as its own statistic, but not a fault. Corruption and unsupported-addition rules are unchanged. The v1 rule (any missing gold point is an omission) is reported alongside.
2. **Normalized evidence policy (host side).** The verifier is unchanged. Evidence is re-derived for each added claim: the single user turn with the highest content-token coverage of the claim, accepted only if coverage >= theta and the claim's numerals all occur in that turn; the quote is the verbatim turn. Reported for theta in {1.0, 0.8, 0.6}. theta = 1.0 is StagedMem0's own strict rule and must reproduce the recorded decisions (sanity check); the Phase 1 one-shared-token matcher is deliberately NOT used because it would ground hallucinated claims.

Known circularity: the silver "dialogue-grounded" test uses the same token machinery at 0.6 over all user text, so theta = 0.6 evidence and silver labels are correlated. The theta grid exists to show that sensitivity, not to pick a winner. No theta is chosen on the basis of outcomes.

### Reanalysis results (29 recorded proposals, 6 users; offline, $0; `scripts/reanalyze_halumem_phase2.py`)

Sanity: normalized theta = 1.0 reproduces all 29 recorded decisions for every condition, with and without transcript grounding.

Silver labels: v1 (any missing gold point = omission): 14 omission / 15 unsupported addition / 0 valid. **v2 (`prior_only`): 14 valid / 15 unsupported addition.** The 14 v1 "omissions" are all recall gaps: Mem0 skipped session facts, but never dropped a prior claim (it only adds).

Evidence coverage of Mem0's 382 added claims: recorded strict 5 (1.3%); theta 0.8: 114 (30%); theta 0.6: 284 (74%).

| Track (all conditions identical) | Holds | Catch of 15 silver unsupported | False hold on 14 silver valid |
|---|---|---|---|
| recorded strict / theta 1.0 | 29/29 | 15/15 | 14/14 |
| theta 0.8 | 29/29 | 15/15 | 14/14 |
| theta 0.6 | 22/29 | 15/15 | 7/14 |

Reading, and what it does not show:
- `graph`, `text_fair` and plain `text` decide identically on all 29 proposals in every track (no `evidence.contradicts` occurs because Mem0's `add()` only emits ADD). No graph advantage; none claimed.
- The verifier is all-or-nothing per transition and Mem0 proposes about 13 claims per session, so one unsupported claim holds the whole session. Even at theta 0.8 (30% of claims grounded) all 29 sessions are held.
- Strict grounding fails mainly on wording, not hallucination: claims name the person ("X lives in Y"), user turns say "I", and the light stemmer treats "plays" and "play" as different tokens. 215 of 382 added claims start with one shared name prefix. I did not add name/pronoun normalization; it would be a further pre-registered, all-conditions step.
- At theta 0.6 the guard catches 15/15 silver-unsupported at a 7/14 false-hold rate, but theta 0.6 evidence and the silver dialogue-grounded test share the same 0.6 token machinery, so this agreement is partly circular. Whether the 7 false holds are multi-turn syntheses was not checked.
- n = 29 from 6 users, silver labels unvalidated (no human review yet). This is a pilot, not an estimate of prevalence or of MemBud's catch rate.

### Billing note (user-reported, 2026-11-21)

The user reports about $0.02 actually billed for the 29-session pilot, against my $0.106 peak-rate estimate (about 5x conservative; likely cache hits and off-peak pricing). The billed figure is user-reported and unverified by this code (`verified_billed: false`).

## Normalization N (pre-registered 2026-11-21, before it was run on any proposal)

Targets the wording mismatch found above. Host-side evidence derivation only; verifier unchanged; applied identically to every condition; reported next to the unnormalized tracks.

- **N1 (first-person subject).** The persona's name tokens (from the `Name: ...;` field of `persona_info`) are removed from the required tokens of a claim that *begins with* that name, because user turns are the persona speaking in the first person. Claims that do not begin with the name are untouched.
- **N2 (inflection fold).** Trailing `-s` (token length > 3, not `-ss`) is folded on both claim and turn tokens ("plays" = "play").
- Nothing else (no synonym, tense, `-ed`/`-ing`, or paraphrase handling). Grid: theta in {1.0, 0.8, 0.6} with and without N. No setting is selected from outcomes.

### Phase 2 pilot, resumed to 97 sessions (20 users; offline reanalysis of recorded proposals; unvalidated)

Run resumed from the checkpoint and stopped by the `--max-spend-usd 0.35` cap (estimated $0.3515 at peak rates) after 97 of 100 sessions; 95 requests, 0 failures, 0 retries, about 996k tokens. The user reports far lower billed spend (see billing note); not verified by this code. The report/quiz were then regenerated from the recorded proposals with the v2 silver rule (`--silver-omission-scope prior_only`, now the default; 0 new API calls). `scripts/reanalyze_halumem_phase2.py` produced the numbers below.

Silver v2: 45 valid / 52 unsupported addition (v1 would give 45 omission / 52 unsupported addition; the 45 are recall gaps only). 1,286 claims were added by Mem0 across the 97 proposals.

Graph decisions, silver v2 (false-hold = holds on the 45 silver-valid; catch = holds on the 52 silver-unsupported). `unguarded` accepts everything (catch 0/52, false hold 0/45). `text_fair` and plain `text` decide identically to `graph` in every track (0 discordant pairs).

| Evidence rule | Claims with evidence | Holds w/o -> with transcript grounding | Catch (w/o -> with) | False hold on valid (w/o -> with) |
|---|---|---|---|---|
| recorded strict | 17 / 1286 | 97 -> 97 | 52 -> 52 | 45 -> 45 |
| theta 1.0 | 19 | 97 -> 97 | 52 -> 52 | 45 -> 45 |
| theta 0.8 | 341 | 97 -> 97 | 52 -> 52 | 45 -> 45 |
| theta 0.6 | 946 | 76 -> 77 | 52 -> 52 | 24 -> 25 |
| N, theta 1.0 | 157 | 97 -> 97 | 52 -> 52 | 45 -> 45 |
| N, theta 0.8 | 768 | 85 -> 86 | 52 -> 52 | 33 -> 34 |
| N, theta 0.6 | 1023 | 61 -> 63 | 51 -> 51 | 10 -> 12 |

N (persona-name + inflection fold) at theta 0.6: false-hold rate on silver-valid 12/45 (27%, Wilson 95% [16%, 41%]) with transcript grounding, versus 25/45 without N and 45/45 under the recorded strict rule; 1 of 52 silver-unsupported proposals is accepted.

What this does and does not show: strict grounding rejects essentially every natural Mem0 proposal; with the pre-registered normalization the guard can accept a majority of silver-valid proposals while holding nearly all silver-unsupported ones, but only at the loosest threshold, and the silver labels and theta = 0.6 evidence share the same 0.6 token machinery (partly circular). Silver labels are unvalidated, one model and one dataset slice are involved, per-transition all-or-nothing verification holds a whole session for one unsupported claim, and no graph advantage appears (all conditions tie). The blind review file (`human-audit/blind-review-quiz.html`, all 97 proposals, regenerated with v2 labels) is waiting for human review; no agreement has been measured.

## Evidence grounding as an interface, with reasons (refactor, 2026-11-22)

`src/membud/grounding.py` makes host-side evidence grounding an explicit `EvidenceGrounder` (`ground(claims, sources, already_supported) -> GroundingReport`). `CoverageGrounder(min_coverage, persona_name, fold_inflection)` implements the pre-registered policy (θ=1.0 is the strict policy; N is opt-in). `halumem.ground_transition(transition, transcript, grounder)` applies any grounder to a recorded proposal; `rederive_evidence_normalized` is now a thin wrapper over it. The verifier and its exact-string claim semantics are unchanged, and any future grounder (NLI, LLM) plugs in without re-calling the model.

Each claim now gets a reason: `grounded`, `already_supported`, or one of `empty_claim_tokens`, `numeral_not_in_any_turn`, `no_token_overlap`, `needs_multiple_turns` (all tokens present across turns but no single turn reaches θ), `below_threshold`. The reasons live beside the transition (in `reanalysis.json`: `claim_grounding_reasons`, and the same restricted to silver-valid proposals); the `Transition` schema is not changed. Reasons are derived offline by the reanalysis; the recorded strict evidence from `StagedMem0` has none.

Check: the reanalysis reproduces every earlier track, silver-scope and sanity number exactly (compared against the pre-refactor `reanalysis.json`).

Why 97-session N θ=0.6 claims lack evidence (claims; 1,286 added): grounded 1,006 (+17 recorded), `needs_multiple_turns` 133, `numeral_not_in_any_turn` 65, `below_threshold` 64, `no_token_overlap` 1. So about 10% of added claims are multi-turn syntheses a single-turn grounder cannot support by design, and about 5% carry a numeral no user turn contains (candidate hallucinations or reformatted numbers; not yet inspected).

### Inspection of the 65 `numeral_not_in_any_turn` claims (2026-11-22, 97 proposals, N θ=0.6)

The numeral rule compares raw digit strings (`\b\d+\b`), for both the grounder and the silver label. The 65 claims (40 proposals) break down as follows (diagnostic only; nothing in the analysis was changed):

| Cause | Claims | Silver label |
|---|---|---|
| Format only: ISO date `1983-05-06` vs "May 6, 1983" (`05` != `6`), or `75000` vs `75,000` (split into `75`/`000`) | 23 | all `unsupported_addition` |
| Number appears only in an assistant turn | 17 | all `unsupported_addition` |
| "2026": Mem0's own run date ("as of November 2026", "around November 2026"), absent from the dialogue | 24 | 17 `unsupported_addition`, 7 `valid` |
| Number absent from the whole dialogue (one claim: income 8210 to 8700) | 1 | `unsupported_addition` |

Consequences:
- **Silver labels are affected, not just evidence.** `derive_silver_label` uses the same raw numeral comparison, so the 23 format-only claims (true restatements) are probably labelled `unsupported_addition` wrongly. That inflates the silver-faulty set and the "catch" rate. Reported catch numbers must stay marked unvalidated.
- **Mem0 adds a run date** ("November 2026") that no user turn contains. Dialogue-grounded verification will always reject it; it is a Mem0 artifact, not a hallucination about the user.
- Not yet done: a numeral normalizer (strip leading zeros, drop thousands separators) shared by grounder and silver rule, which must be pre-registered before use. Quick check: it resolves all 23 format-only claims and leaves 1 absent.

### Numeral normalization (numnorm) — rule and results (2026-11-22)

**Rule (fixed before looking at its results; opt-in, off by default):** when comparing numerals, drop thousands separators (`75,000` -> `75000`, only when exactly three digits follow the comma) and strip leading zeros (`05` -> `5`). No month-name or word-number conversion; claim text is never changed. It applies identically to the grounder (`CoverageGrounder(normalize_numerals=True)`) and the silver rule (`derive_silver_label(normalize_numerals=True)`, runner flag `--silver-normalize-numerals`). Mem0's run date ("2026") is deliberately **not** handled here.

Offline reanalysis (97 proposals, `reanalysis.json`; every pre-existing track and silver scope is byte-identical to before; text_fair and text tie graph in all new tracks, McNemar p = 1.0):

- Silver labels, `prior_only`: raw 45 valid / 52 unsupported; with numnorm 57 valid / 40 unsupported. So 12 proposals the raw rule called unsupported were format-only mismatches.
- Evidence coverage, N θ=0.6: 1,023 -> 1,045 of 1,286 claims.

| Track (graph, with transcript grounding) | Silver | Catch | False hold on valid |
|---|---|---|---|
| N θ=0.6 (raw numerals) | raw | 51/52 | 12/45 |
| N θ=0.6 (raw numerals) | numnorm | 39/40 | 24/57 |
| N + numnorm θ=0.6 | raw | 43/52 | 12/45 |
| N + numnorm θ=0.6 | numnorm | 39/40 | 16/57 (28%) |

The consistent pair (numnorm evidence and numnorm silver) gives catch 39/40 and false hold 16/57. The mixed rows are shown only to separate the two effects: with raw silver, numnorm evidence "misses" 9 more because those proposals were mislabelled faulty. These remain unvalidated silver labels with the circularity caveat; the human audit is still needed. The existing 40-case quiz was built with raw silver labels and is not regenerated.

### Human audit v1 (reviewer: ifan, 2026-11-22; 40 cases, numnorm silver labels) — not a valid test of the silver labels

Graded with `--grade-human-audit`: agreement 20/39 (51.3%; 1 blank). The reviewer chose HOLD 37 times and ACCEPT twice; the silver labels expected 20 ACCEPT / 20 HOLD (confusion: silver ACCEPT/human HOLD 18, silver HOLD/human HOLD 19, silver HOLD/human ACCEPT 1, silver ACCEPT/human ACCEPT 1).

**Why this does not measure label validity.** The quiz asks for the verifier's decision from the case's own evidence. The case evidence is the recorded strict `StagedMem0` evidence, which is empty in 38 of 40 cases (19 of the 20 silver-valid ones). A reviewer who follows the rubric correctly must HOLD an added claim with no evidence, so the 18 "disagreements" on silver-valid cases are the strict grounder's emptiness, not silver-label errors. The silver labels ask a different question (are the added claims supported by the dialogue?). Agreement here is therefore uninformative, and the silver labels remain unvalidated. A second audit must show the session's user turns and ask whether each added claim is supported, with no verifier-decision framing.

### Claim-level audit v2 (built 2026-11-22; awaiting reviewer)

`scripts/halumem_claim_audit.py build|grade`. Question per claim: did the user say it in the session (dialogue and prior memory shown, silver verdict hidden)? 72 claims from 24 seeded proposals (12 with a silver-unsupported claim, 12 without); 21 silver-unsupported and 51 silver-supported claims. Silver rule: `prior_only` + numeral normalization. Reviewer options: SUPPORTED / UNSUPPORTED / UNSURE; Mem0's run-date phrase ("as of November 2026") is to be ignored. Grading reports agreement with a Wilson interval and Cohen's kappa; UNSURE is excluded from agreement and counted separately. The silver-unsupported claims are oversampled by design, so report agreement per silver class, not just overall.

### Claim-level audit v2 — result (reviewer: ifan, 2026-11-22; 72 claims, 71 decisive, 1 blank)

Graded with `halumem_claim_audit.py grade` (`claim-agreement.json`). Agreement 55/71 = 77.5% (Wilson 66.5–85.6%), Cohen's kappa 0.31.

| Silver verdict | Human SUPPORTED | Human UNSUPPORTED |
|---|---|---|
| SUPPORTED (50 claims) | 50 | 0 |
| UNSUPPORTED (21 claims, oversampled) | 16 | 5 |

- The silver "supported" class is clean in this sample (50/50). The silver "unsupported" class is mostly wrong: only 5 of 21 flagged claims (24%) were judged unsupported by the human.
- The 16 disagreements are mostly abstractive paraphrase or inference ("finds the ambiance transported him", "reflects his openness to new experiences"): all the user's content is present in meaning but the claim's tokens cover only 0.3–0.6 of the user's words, below the 0.6 rule. A few contain a run-date phrase.
- The 5 confirmed unsupported claims mostly carry numbers absent from the dialogue (e.g. savings 7,340).
- Consequence: the silver `unsupported_addition` label cannot be used as ground truth, and the catch / false-hold figures computed against it must not be reported as detection performance. The lexical grounder that decides evidence shares this weakness (token coverage cannot separate faithful paraphrase from hallucination).
- Limits: one reviewer, no second rater, silver-unsupported oversampled (precision of that class is not a prevalence), 72 claims, one model, one dataset slice.

### Semantic (LLM) grounder vs the human claim audit (2026-11-22; deepseek-flash; unvalidated)

`JudgeGrounder` (`src/membud/grounding.py`) plugs a semantic judge into the `EvidenceGrounder` interface (evidence is always the verbatim cited turn; support without a valid citation is rejected). `scripts/halumem_llm_grounder_eval.py` runs a DeepSeek judge per claim (thinking disabled, peak-price estimate hard-capped at $0.25; HaluMem text sent to DeepSeek under the data-policy confirmation given earlier) and compares claim-level verdicts with the human audit (71 decisive claims).

| Judge | Agreement with human | 95% CI | says UNSUPPORTED, human SUPPORTED | says SUPPORTED, human UNSUPPORTED |
|---|---|---|---|---|
| LLM, user turns only | 50/71 (70.4%) | 59–80% | 21 | 0 |
| LLM, full dialogue + prior memory | 65/71 (91.5%) | 83–96% | 3 | 3 |
| Lexical grounder (θ=0.6, N + numnorm) | 46/71 (64.8%) | 53–75% | 25 | 0 |
| Silver rule | 55/71 (77.5%) | 66–86% | 16 | 0 |

- With user turns only the LLM was stricter than the reviewer because about half of its 21 misses are facts the assistant stated and the user accepted, or that appear in prior memory; the reviewer saw the full dialogue. Showing the judge the same context as the reviewer (same rubric) lifts agreement to 91.5%.
- Caveats: the second configuration was chosen after seeing the first run's disagreements, on the same 72 claims (selection on the test set), so 91.5% is optimistic. One reviewer, silver-unsupported oversampled, one model, one dataset slice. Needs a held-out sample (new claims, new reviewer if possible) before any claim.
- Estimated spend (peak list price, not a bill): about $0.021 for the first run and $0.062 for the second, about $0.08 in total, 144 requests. Responses are cached locally in `~/.cache/membud/deepseek-cache.sqlite3` (contains HaluMem text).

### All four conditions with semantic (LLM) evidence (2026-11-22; deepseek-flash; unvalidated, circular)

`scripts/halumem_llm_phase2_eval.py`: one DeepSeek request per recorded proposal (97) judges every added claim against the full session and prior memory (the human audit's rubric); supported claims get verbatim user-turn evidence through `JudgeGrounder`; the unchanged verifier then runs all four conditions with transcript grounding. The recorded `StagedMem0` evidence is discarded: it cites `messages[i]:user` refs that do not exist in the HaluMem transcript, so transcript grounding rejected it (this alone caused 12 extra holds in a first pass that kept it).

| Quantity | Value |
|---|---|
| Added claims judged | 1,286 |
| Judge-supported (grounded) | 1,222 |
| Judge-unsupported | 64 (5.0%) |
| Proposals with every claim supported | 67 of 97 |
| Held by graph, text, text_fair | 31 of 97 each (unguarded: 0) |
| Graph vs text_fair discordant | 0 / 0 (McNemar p = 1.0) |
| Holds explained by a judge-unsupported claim | 30 of 31 |
| Agreement of this batch judge with the human claim gold | 63/71 (88.7%, 95% CI 79–94%) |

- Hold rate under strict recorded evidence was 100%; with semantic evidence it is 32%, and nearly all holds come from the 64 claims the judge calls unsupported.
- The one remaining hold without a judge-unsupported claim, and 4 of the others, carry `UNGROUNDED_EVIDENCE_CLAIM`: the verifier's own transcript check requires the claim's tokens to appear in the cited quote, which a paraphrase or multi-turn synthesis supported semantically cannot satisfy. This is a tension between semantic evidence and the verifier's lexical quote check; the verifier was not changed.
- Against the numeral-normalized silver labels: silver-valid 57/57 accepted; silver-unsupported 31 held, 9 accepted. The human audit showed the silver-unsupported class is mostly wrong (5 of 21 confirmed), so this cross-tab is not a detection rate.
- **Circularity:** the judge both supplies the evidence and determines why a proposal is held, so hold rates are not detection performance. Whether the judge's 64 "unsupported" claims are really unsupported (precision) and whether it misses real hallucinations (recall) is untested beyond 71 human-labelled claims (oversampled design, one reviewer, selection on those items when choosing the prompt context).
- Estimated spend, peak list price (not a bill): about $0.119 for the first batch pass plus $0.007 for the re-judge of 12 proposals; with the earlier $0.083 about $0.21 in total, under the $0.25 cap.

### Claim audit v3: fresh sample to audit the LLM judge (built 2026-11-22; awaiting reviewer)

`scripts/halumem_claim_audit.py build-judge` draws 40 claims not in audit v1 (72 excluded): 24 the judge called unsupported (tests the precision of what drives holds) and 16 it called supported (tests whether it lets unsupported claims through). Same blind format and rubric as audit v1; the judge verdict is hidden. Grade with `grade --audit-dir claim-audit-v2`. The 24/16 split oversamples the rare unsupported class, so report precision and recall separately, not one overall agreement. One reviewer is still a limit; a second reviewer on the same file would give inter-rater agreement.

### Claim audit v3 — result: judge precision is low under this reviewer (reviewer: nabhan, 2026-11-22; 40 claims, all decisive)

Graded with `grade --audit-dir claim-audit-v2`. Different reviewer from audits v1/v2, different claims, so no inter-rater agreement can be computed.

| Judge verdict | Reviewer SUPPORTED | Reviewer UNSUPPORTED |
|---|---|---|
| SUPPORTED (16 claims) | 16 | 0 |
| UNSUPPORTED (24 claims) | 20 | 4 |

- Recall side: all 16 judge-supported claims were confirmed (nothing the judge let through was flagged by the reviewer; sample of 16).
- Precision side: only 4 of the 24 judge-unsupported claims (17%, Wilson 95% CI about 7–36%) were judged unsupported by the reviewer. The 30 holds that the judge's flagged claims drive are therefore mostly not confirmed by this reviewer.
- Pattern in the 20 disagreements: dialogue-wide token coverage is high (0.7–0.9) but user-turn-only coverage is low (0.0–0.4). The content was stated by the assistant (advice and suggestions "was advised to ...", names of friends and colleagues the assistant introduced, figures the assistant gave) and the user never restated it. The written rubric says such facts do not count unless the user confirms them; both reviewers appear to treat assistant-originated content as supported (the first reviewer did the same in audit v2, where the user-turns-only judge disagreed on 21 claims). 13 of these 20 claims also match no HaluMem gold memory point; 2 best-match a HaluMem `interference` (false distractor) point yet were marked supported.
- This is a policy question, not a model bug: whether facts introduced by the assistant count as supported memory. Until it is decided, "unsupported" is not well defined for these claims, and the judge's 5% unsupported rate and the 31 holds cannot be called correct or incorrect.
- Limits: one reviewer per item, no overlap between reviewers, small samples, oversampled classes.

### Policy decision and re-judge: assistant-introduced facts (2026-11-22; PARTIAL: 77 of 97 proposals; unvalidated)

**Decision (project owner):** a fact introduced by the assistant counts as supported when the user accepts, adopts or acts on it, or when it rests on something the user said earlier. Facts the user never engaged with, or rejected, stay unsupported. `halumem_llm_phase2_eval.py --policy accepted` applies this rubric (`SYSTEM_ACCEPTED`) to the proposals the strict run flagged (30); unflagged proposals reuse the strict verdicts (loosening a rubric can only turn unsupported into supported). Outputs: `llm-phase2-eval-accepted.json`, `llm-claim-verdicts-accepted.jsonl`.

**The run is incomplete.** It stopped at the cap check with 77 of 97 proposals processed (24 paid requests; estimated $0.044 against a remaining allowance of $0.042). Counting all earlier runs, estimated spend is about $0.25 at list price (an estimate; the earlier first 29 sessions were billed about 1/5 of the estimate). The 20 unprocessed proposals are not in any number below.

Over the 77 processed proposals (999 added claims judged afresh or reused):

| Quantity | Value |
|---|---|
| Judge-supported (grounded) | 977 |
| Judge-unsupported | 14 |
| Supported by the judge but no user turn citable (`judge_invalid_citation`) | 8 |
| Held by graph, text, text_fair | 21 of 77 each (discordant 0/0) |
| Holds with a judge-unsupported claim | 10 |
| Holds without one (`UNGROUNDED_EVIDENCE_CLAIM`: claim words absent from the cited user quote) | 11 |
| Agreement with the audit v2 labels (first reviewer), claims in processed proposals | 47/50 (94%) |
| Agreement with audit v3 (second reviewer), claims in processed proposals | 24/35 (69%) |

- The new policy removed 12 of the 20 flagged claims that reviewer 2 called supported; 9 remain flagged (precision of the judge's remaining flags on this sample: 1 of 10). Not investigated yet.
- 11 holds persist where the policy says the claim is supported: when the support is the user's acceptance or an earlier user statement, the cited user quote does not contain the claim's words, and the verifier's lexical quote check rejects it. Under this policy the verifier, left unchanged, still holds assistant-introduced facts. Closing that gap would mean changing what the quote check accepts, which is a design decision, not done here.
- Agreement with reviewer 1 (94%) is partly in-sample: the policy was chosen after seeing both reviewers' behaviour. The 24/35 figure is the more sober one. Neither is validation.

### Policy re-judge completed: all 97 proposals (2026-11-22; deepseek-flash; unvalidated)

The remaining 20 proposals were judged under the accepted-assistant-facts policy (6 paid requests). Estimated spend, peak list price, whole project of judge runs: about $0.26 (above the $0.25 cap by about $0.014, with the owner's agreement to finish; an estimate, not a bill).

| Quantity (97 proposals, 1,286 added claims) | Value |
|---|---|
| Judge-supported (grounded) | 1,254 |
| Judge-unsupported | 20 (1.6%) |
| Supported by the judge, no user turn citable (`judge_invalid_citation`) | 12 |
| Proposals with every claim supported | 76 |
| Held by graph, text, text_fair | 27 of 97 each (unguarded 0; discordant 0/0, McNemar p = 1.0) |
| Holds with a judge-unsupported claim | 14 |
| Holds without one | 13 |
| Agreement with the audit v2 labels (reviewer 1) | 65/71 (91.5%, 95% CI 83–96%) |

Effect of the policy: unsupported claims 64 -> 20, holds 31 -> 27 (strict policy -> accepted policy; both with semantic evidence; strict-evidence holds were 97 of 97).

The 13 holds with no unsupported claim are the verifier's lexical quote check (`UNGROUNDED_EVIDENCE_CLAIM`, 12 proposals) and claims with no citable user turn. If the quote check accepted a user turn that confirms an assistant-introduced fact, those holds would not occur: 27 would become about 14 (28% -> 14%). That is a large share of the remaining holds, but it is a verifier design change, not made.

Remaining flagged claims reviewer 2 called supported (9 in audit v3): by inspection of the dialogue, about 4 have a user reply that adopts the specific fact ("the increased income certainly helps" after the assistant's income figure; "excited about this raise"), so the judge is stricter than the written policy there. About 5 have only generic engagement ("thank you for the encouragement", a reply that does not touch the specific friend, hobby or diagnosis); those are ambiguous: "accepts" needs a definition (explicit agreement or adoption of the specific fact vs any non-rejection).

### Prototype: opt-in reply-context quote check (branch `feature/halumem-quote-check`, since merged into `main`; 2026-11-22; unvalidated)

`verify_transcript_grounding(..., allow_reply_context=True)` (default off) accepts an evidence claim that shares no content token with its cited **user** turn when the claim does overlap the assistant turn that the user turn directly replies to. The quote must still be a verbatim user turn and the role rules are unchanged; the verifier only checks that the citation is adjacent to an assistant turn carrying the claim. Whether the user actually accepted the fact is the grounder's (LLM judge's) judgement, not verified. In the evaluation script, a judge citation of an assistant turn is mapped to the user turn that replies to it (`--reply-context`). 0 new API requests (cached judge responses).

Same 97 proposals, accepted-assistant-facts policy:

| | Default check | Reply-context check |
|---|---|---|
| Claims grounded | 1,254 | 1,266 |
| Judge-supported but no citable user turn | 12 | 0 |
| Held by graph / text / text_fair | 27 / 27 / 27 | 14 / 14 / 14 |
| Holds with a judge-unsupported claim | 14 | 14 |
| Holds without one | 13 | 0 |
| Graph vs text_fair discordant | 0 / 0 | 0 / 0 |

So the opt-in check removes exactly the 13 holds that came from the quote check and from missing user citations; every remaining hold has a claim the judge calls unsupported. Cautions: the new check is lexical and weak (any shared content token with the assistant turn; the original check is the same strength), it moves the "did the user accept it" decision onto the judge, and it was measured on the same unvalidated judge verdicts. It does not make the 14 remaining holds correct either. Tests: 815 pass (6 new in `tests/test_reply_context.py`, default-off behaviour covered).
