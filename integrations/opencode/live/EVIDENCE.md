# Live model-originated denial test (opencode 1.19.5 + DeepSeek)

**Date:** 2026-11-07
**Status:** PASS. A real model, in a real opencode session, called a plugin-registered `memory_write` tool; the MemBud guard denied the unsafe write before the tool executed and allowed the evidence-backed write.
**Reproduce:** `scripts/run_live_opencode_denial.py --confirm-paid-call` (opt-in; makes real paid provider calls, see below).
**Artifact:** [`evidence-2026-11-07.json`](evidence-2026-11-07.json).

## Why this test exists

The offline harness (`../harness.mjs`) replays the wrapper order observed in the
opencode 1.19.5 binary, and the `opencode serve` smoke proves the plugin module
is loaded. Neither proved that an actual *model-originated* tool call is denied
and surfaced back to the model. This test closes that gap.

## Environment

| Item | Value |
| --- | --- |
| opencode | 1.19.5 (`/usr/bin/opencode`) |
| Plugin typings available | `@opencode-ai/plugin@1.18.30` (only used to define the mock tool; the guard itself imports Node built-ins only) |
| Provider | DeepSeek via opencode's catalog (`@ai-sdk/openai-compatible`, `https://api.deepseek.com`) |
| Model | `deepseek/deepseek-flash` |
| Sidecar | `python -m membud.service` on `127.0.0.1:8787` |
| Isolation | throw-away `HOME`/XDG dirs inside the work directory, copied plugin package + model catalog cache; no npm install performed |
| Data | synthetic claims only; no licensed dataset data |

## Procedure

`scripts/run_live_opencode_denial.py` builds the isolated workspace, starts the
real sidecar, and runs two `opencode run --format json` sessions with scripted
prompts (the memory state starts as `[{"id":"r1","claims":["tinggal di Bandung"]}]`):

1. **HOLD prompt** — candidate adds `{"id":"r2","claims":["memiliki kapal pesiar"]}`
   with no evidence. Expected: sidecar HOLD, guard throws, tool never executes.
2. **ACCEPT prompt** — candidate adds `{"id":"r2","claims":["bekerja di Tokopedia"]}`
   with supporting evidence. Expected: sidecar ACCEPT, tool executes once and
   the state persists exactly the candidate.

The script asserts the tool-call status, the denial reason, byte-identical state
and absent capture file for HOLD, and the persisted candidate and execution
capture for ACCEPT. It requires explicit `--confirm-paid-call`; it refuses to
run otherwise.

## Observed results

| Check | HOLD session | ACCEPT session |
| --- | --- | --- |
| Tool call seen in opencode events | `memory_write` (1 call) | `memory_write` (1 call) |
| Tool status | **error** | **completed** |
| Guard message / output | `MemBud denied memory_write: HOLD [UNSUPPORTED_CLAIM] — faithfulness=FAIL(UNSUPPORTED_CLAIM: claims without evidence provenance: memiliki kapal pesiar)` | `memory_write applied 2 record(s)` |
| Memory state | byte-identical (SHA-256 unchanged) | advanced `version 0 -> 1`, records == candidate |
| Tool executed? | **no** (no capture line) | **yes** (exactly one capture line) |
| Model-reported outcome | `TOOL_OUTCOME=Denied by MemBud HOLD (UNSUPPORTED_CLAIM: "memiliki kapal pesiar" lacks evidence provenance).` | `TOOL_OUTCOME=memory_write applied 2 record(s)…` |
| Tokens (input/output) | 476 / 201 | 566 / 234 |
| Cost (provider-side estimate) | $0.00024948 | $0.00028338 |

**Total: 1,042 input / 435 output tokens, ≈$0.00053** (opencode's
provider-side estimate from its cached model catalog; the provider does not
return a bill here). Total spend across all development runs of this live test,
including one assertion-fix iteration and an earlier manual run, was **under
$0.01**.

## Sidecar log (evidence that verification ran per session)

```
GET /healthz 200
POST /verify 200   # HOLD session
POST /verify 200   # ACCEPT session
```

The committed `evidence-2026-11-07.json` contains the machine-readable summary
(denial text, model text, state hashes, usage). Session transcripts were not
committed; they contain the same information plus local session IDs.

## What this proves

- A real opencode 1.19.5 session, driven by a real model, can call a
  plugin-registered tool that is guarded by the MemBud `tool.execute.before`
  hook.
- On HOLD, the hook's throw prevents tool execution, the host state is
  byte-identical, and the denial (with MemBud reason code) is surfaced to the
  model as the tool error.
- On ACCEPT, the same path executes exactly once and persists exactly the
  verified candidate.
- The full loop is reproducible with one opt-in command and costs a fraction of
  a cent per run.

## What this does NOT prove

- General unscripted agent behavior: this is two scripted prompts, one model
  (`deepseek/deepseek-flash`), one provider, one opencode version.
- Compatibility with the 1.18.30 CLI (only 1.19.5 was exercised) or other
  providers/models.
- MCP or real memory-backend integration: the guarded tool is a local mock that
  writes a JSON file; nothing hooks Mem0/A-Mem/etc.
- Production hardening: the sidecar has no auth/TLS/rate limiting, and the
  guard validates a proposed state against a local snapshot, not a
  transactional host store.
- Argument rewriting: the plugin only allows/denies; it does not mutate args.
