# Conversation Ingestion, Transcript Grounding, and Multi-Turn Verification

`membud.conversation` connects MemBud's canonical transition verifier and guarded
storage pipeline to actual multi-turn conversation transcripts rather than
pre-packaged transition fixtures alone.

## 1. Capabilities

1. **Canonical transcript schema (`ConversationTurn`, `ConversationTranscript`)**
   - Normalizes roles (`user`, `assistant`, `system`, `tool`, plus aliases `human`/`pengguna` -> `user`, `ai`/`bot`/`model`/`asisten` -> `assistant`), enforces unique `turn_id`s, preserves verbatim `timestamp`s without inventing them, and validates string-keyed `scope`s and JSON-serializable `metadata`.
   - `slice_turns(up_to_turn_id=...)` and `slice_turns(turn_ids=...)` provide deterministic turn-level views for multi-turn execution.

2. **Multi-format parsers (`parse_conversation`, `load_conversation`, `load_conversations_jsonl`)**
   - `membud`: canonical `{"conversation_id", "turns", "scope", "metadata"}` object.
   - `openai`: list of `{role, content}` messages (including multi-part `[{"type": "text", "text": "..."}]` content blocks) or `{"messages" | "dialogue": [...]}`.
   - `opencode`: `opencode run --format json` JSONL event streams (`user`, `text`, `tool_use`, `step_start`, `step_finish`), preserving `sessionID` and timestamps and mapping `tool_use` events to `role="tool"`.
   - `halumem`: HaluMem-shaped `{"uuid", "persona_info", "sessions": [{"session_id", "dialogue": [...]}]}` schemas (parser support only; no HaluMem dataset content is bundled or committed).
   - `text`: plain-text / Markdown transcripts (`[t1] User: ...`, `Assistant: ...`, `Pengguna: ...`, `Asisten: ...`, including multi-line turns).

3. **Transcript-grounded evidence verification (`verify_transcript_grounding`, `verify_with_transcript`)**
   - Previously (`docs/software-review-2026-11-19.md` finding `m8`), evidence items were caller-self-asserted. When a `ConversationTranscript` is supplied, `verify_transcript_grounding` appends a `transcript_grounding` check enforcing that:
     - every `evidence` item resolves to an actual transcript turn (`UNGROUNDED_EVIDENCE_SOURCE`),
     - the cited turn's role is allowed (`DEFAULT_GROUNDING_ROLES = ("user",)`; `DISALLOWED_EVIDENCE_ROLE` blocks assistant hallucinations and tool outputs from acting as memory evidence unless explicitly permitted),
     - `evidence.text` is a non-empty, whitespace-normalized verbatim substring of the cited turn (`UNGROUNDED_EVIDENCE_QUOTE`),
     - `DELETE` authorizations resolve to an allowed-role grounded turn with deletion intent or content-token overlap with the deleted prior record (`UNGROUNDED_DELETE_AUTHORIZATION`),
     - supported/contradicted claims have lexical content-token grounding against the cited quote (`UNGROUNDED_EVIDENCE_CLAIM`).
   - `membud.store.commit` / `run_case` validate `"transcript_grounding"` alongside `"coverage"`, `"preservation"`, `"faithfulness"`, and optional `"semantic"`.
   - `POST /verify` (`membud.service`) accepts an optional `"transcript"` or `"conversation"` field (`MAX_TURNS = 5_000`), returning 400 `HOLD` (`INVALID_TRANSCRIPT` / `INPUT_TOO_LARGE`) on invalid transcripts and enforcing `transcript_grounding` when present.
   - `integrations/opencode/plugin/membud-verify-guard.js` accepts an optional `transcriptPath` option (or `MEMBUD_TRANSCRIPT_PATH` env fallback / `args.transcript`) and fails closed if a configured transcript file cannot be read or parsed.

4. **Extractors and multi-turn session runner**
   - `build_transition_from_conversation` builds validated schema-v2 `Transition` objects from transcript-grounded operation proposals and derives `required_claims` per MemBud's persistence policy.
   - `RuleBasedConversationExtractor` deterministically extracts Indonesian and English `WRITE`, `REVISE`, `SUPERSEDE`, `DELETE`, and `NOOP` transitions from user turns while ignoring chatter/questions and non-user roles.
   - `LLMConversationExtractor` uses MemBud's JSON-mode `complete_json` client (`membud-conversation-extract-v1`) with strict schema + verbatim-quote validation before constructing a transition.
   - `run_conversation_session` and `python -m membud.conversation` run multi-turn conversations end-to-end against versioned stores and owner-only (`0600`) audit logs.

## 2. Multi-Turn Evaluation (`conversations-v1`)

Reproduce the rules-only evaluation (0 paid calls, $0.00):

```bash
PYTHONPATH=src .venv/bin/python scripts/run_conversation_evaluation.py \
    --output data/results/conversations-v1 --overwrite
```

- **Manifest:** `data/manifests/conversations-v1.json` (`membud-conversations-v1`, 8 synthetic multi-turn Indonesian and English conversations, 28 evaluated steps: 17 `ACCEPT`, 10 `HOLD`, 1 `INVALID_INPUT`; single-reviewer exploratory annotations).
- **Paired step evaluation (`data/results/conversations-v1/metrics.json`):**
  - `unguarded`: `APPLIED=27`, `INVALID_INPUT=1`; safe final state in **4/8** conversations (4 conversations retained spoofed/ungrounded records).
  - `graph`: `ACCEPT=17`, `HOLD=10`, `INVALID_INPUT=1`; valid acceptance **17/17**, faulty detection **10/10**, false accepts **0/10**, false holds **0/17**, label agreement **28/28**, safe final state **8/8**.
  - `text`: `ACCEPT=18`, `HOLD=9`, `INVALID_INPUT=1`; valid acceptance **17/17**, faulty detection **9/10**, false accepts **1/10** (`CONV-06-S3`), label agreement **27/28**, safe final state **8/8**.
  - `text_fair`: `ACCEPT=17`, `HOLD=10`, `INVALID_INPUT=1`; matches `graph` on **28/28** steps (`0` discordant steps).
- **Raw-transcript extraction replay (`RuleBasedConversationExtractor` -> `graph` + `transcript_grounding`):**
  - **8/8** conversations converged to `expected_final_records`, with **20/20** extracted user-turn steps `ACCEPT`ed and **0** `HOLD`.

## 3. Limitations

- `conversations-v1` is a small, synthetic, single-reviewer exploratory set (second human review pending) and does not establish population accuracy or graph superiority.
- `RuleBasedConversationExtractor` covers explicit Indonesian and English slot/deletion patterns; open-ended phrasing requires `LLMConversationExtractor` or a host-supplied proposal pipeline, and every claim still passes through deterministic verification and transcript grounding before commit.
- `LLMConversationExtractor` is unit-tested with a deterministic fake JSON client; no live paid extraction run was executed without spend approval.
