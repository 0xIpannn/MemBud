# Quickstart: guard a memory write in 10 minutes

MemBud checks a **proposed memory update** before it is committed. You hand it the memory state
before, the state you want to write, and the evidence behind the change. It answers `ACCEPT` or
`HOLD`. A `HOLD` means "do not write this", and MemBud never writes on a `HOLD`.

> MemBud is an experimental prototype. Read [What ACCEPT does and does not mean](#what-accept-does-and-does-not-mean)
> before relying on it.

You need Python 3.11+ on Linux (developed and tested there; macOS is untested). The core has no third-party dependencies. Steps 5 and
beyond additionally use Node (or bun) and [opencode](https://opencode.ai).

## 1. Install

```bash
git clone git@github.com:0xIpannn/MemBud.git && cd MemBud
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[test]"
python -m pytest -q          # optional: confirms your environment works
```

This installs four commands: `membud-verify` (the sidecar), `membud-demo`, `membud-conversation`
and `membud-experiment`.

## 2. See it work (30 seconds)

```bash
membud-demo
```

This runs 11 bundled cases (valid updates plus omission, corruption and unsupported claims) and writes
isolated stores, an append-only `membud-output/audit.jsonl` and Mermaid diagrams under `membud-output/`.

## 3. Ask the verifier over HTTP

Start the localhost sidecar. It is stdlib-only, listens on `127.0.0.1` only and persists nothing.

```bash
membud-verify --port 8787
# membud-verify listening on http://127.0.0.1:8787
```

In another terminal, send the two example transitions from [`docs/examples/`](examples/):

```bash
curl -s localhost:8787/healthz
curl -s -X POST localhost:8787/verify -H 'content-type: application/json' --data-binary @docs/examples/accept.json
curl -s -X POST localhost:8787/verify -H 'content-type: application/json' --data-binary @docs/examples/hold.json
```

The first returns `"decision": "ACCEPT"`. The second adds the claim `owns a private jet` while the only
evidence says `I played tennis this weekend.`, so it returns `"decision": "HOLD"`, and the failing
check names the reason:

```json
{"name": "faithfulness", "status": "FAIL", "reason_code": "UNSUPPORTED_CLAIM", "...": "..."}
```

A transition has five parts, all shown in [`examples/accept.json`](examples/accept.json):

| Field | Meaning |
|---|---|
| `before` | The full memory state as it is now (`[{id, claims[]}]`). |
| `candidate` | The full state you want to write. |
| `evidence` | Items that justify new claims: `{id, source, text, supports[]}`. |
| `required_claims` | Claims that must survive the update. |
| `operations` | What changed: `WRITE`, `REVISE`, `DELETE`, `SUPERSEDE` or `NOOP`. Every change must be declared. |

Anything malformed, oversized or unexpected is also a `HOLD` (never an `ACCEPT`). The full field and
operation rules are in [reference.md](reference.md).

## 4. Use it as a library (verify, then write)

`run_case` verifies a transition and writes a versioned JSON store **only on `ACCEPT`**. It takes an
exclusive lock, checks that the store matches `before`, and appends an audit record.

```python
import json
from pathlib import Path

from membud.schemas import Transition
from membud.store import run_case

for name in ("accept", "hold"):
    transition = Transition.from_dict(json.loads(Path(f"docs/examples/{name}.json").read_text()))
    store = Path(f"membud-output/quickstart/{name}-store.json")
    result = run_case(transition, store, "membud-output/quickstart/audit.jsonl")
    failed = [(c.name, c.reason_code) for c in result.checks if not c.passed]
    print(name, result.decision, failed, "store written:", store.exists())
```

Expected output:

```text
accept ACCEPT [] store written: True
hold HOLD [('faithfulness', 'UNSUPPORTED_CLAIM')] store written: False
```

If you only need the decision and own the write yourself, call `membud.verification.verify(transition)`
and write only when `result.decision == "ACCEPT"`.

## 5. Guard an agent's memory tool (opencode)

The opencode plugin runs before every call to your memory tool and **denies the call** unless the
sidecar answers `ACCEPT`. If the sidecar is down, slow or unreachable, the call is denied too.

First, see the guard work without any model, in about five seconds:

```bash
PYTHONPATH=src node integrations/opencode/harness.mjs --samples 5
# PASS scenario accept -> ALLOW ...
# PASS scenario hold-unsupported -> DENY ...
```

(`bun` works as well. The harness starts the real sidecar and replays opencode's tool-call order.)

To guard your own tool:

1. **Name and shape.** Your memory tool must be called `memory_write` (or list other names in
   `toolNames`) and take arguments `{candidate, evidence, required_claims, operations}`. If `operations`
   is omitted, the plugin derives `WRITE`/`REVISE` from the diff. It never derives deletions; a silent
   removal is denied. The argument contract is documented in
   [`integrations/opencode/mock-memory-tool.mjs`](../integrations/opencode/mock-memory-tool.mjs).
2. **State file.** The plugin reads your tool's current memory as JSON: `{"records": [{"id": ..., "claims": [...]}]}`
   (a bare list also works). It only ever reads the file.
3. **Install the plugin.** Copy `integrations/opencode/plugin/membud-verify-guard.js` to
   `<your-project>/.opencode/plugin/` and set environment variables, or use the config snippet in
   [`integrations/opencode/config.snippet.jsonc`](../integrations/opencode/config.snippet.jsonc):

   ```bash
   export MEMBUD_VERIFY_URL=http://127.0.0.1:8787/verify
   export MEMBUD_MEMORY_STATE=$HOME/.local/share/membud/memory-state.json
   ```
4. **Run the sidecar** (step 3) before you start opencode.

Tested against opencode 1.19.5. Other versions are unverified. Details, latency measurements and the
list of what is **not** proven are in [`integrations/opencode/README.md`](../integrations/opencode/README.md).

Not using opencode? Do the same thing yourself: before your agent's memory write, `POST` the
transition to `/verify` and write only on `ACCEPT`.

## What ACCEPT does and does not mean

`ACCEPT` means the candidate state and its declared operations are **consistent with the evidence you
supplied**. It does not mean:

- **The evidence is real.** In the plain transition API, `evidence` is caller-declared. To bind evidence
  to an actual conversation, also send the transcript (`"transcript"` or `"conversation"` in the
  `/verify` request, or `transcriptPath` in the plugin). MemBud then requires every evidence quote to
  appear verbatim in an allowed (default: user) turn. See [conversation-pipeline.md](conversation-pipeline.md).
- **Paraphrases match.** Claims are compared as exact strings. If your extractor writes
  `works at Tokopedia` in one place and `employed by Tokopedia` in another, that is two different
  claims. Normalize claim text before verifying.
- **It is production-ready.** The sidecar has no authentication or TLS, relies on loopback binding, and
  has not been tested with concurrent writers. See the "not proven" lists in the
  [opencode README](../integrations/opencode/README.md) and [mem0-guarded-integration.md](mem0-guarded-integration.md).

## When something is denied or fails

| Symptom | Meaning |
|---|---|
| `UNSUPPORTED_CLAIM` | A claim in the candidate is not in the prior state and no evidence `supports` it. |
| `CONTRADICTED_CLAIM` | A candidate claim is listed in an evidence item's `contradicts`. |
| `REQUIRED_CLAIM_OMITTED` | A claim in `required_claims` is missing from the candidate. |
| `PRIOR_RECORD_UNAUTHORIZED_CHANGE` | A prior record changed without a matching `REVISE`, `SUPERSEDE` or `DELETE`. |
| `INVALID_TRANSITION` (HTTP 400) | The input breaks the schema, for example an undeclared removal or a missing `DELETE` authorization. |
| `UNGROUNDED_*` | A transcript was supplied and the evidence is not backed by it. |
| `ValueError: persisted store does not match transition.before` | Your `before` is stale: the store changed after you built the transition. Reload the state and rebuild. |
| Every call is denied and the plugin says "failing closed" | The sidecar is not reachable at `MEMBUD_VERIFY_URL`. Start it, or check the port. |

## Next steps

- [reference.md](reference.md): schema, operation rules, code layout.
- [conversation-pipeline.md](conversation-pipeline.md): verify updates extracted from real conversations.
- [mem0-guarded-integration.md](mem0-guarded-integration.md): guarding a real Mem0 store.
- [research-status.md](research-status.md): what has been measured and what has not.
