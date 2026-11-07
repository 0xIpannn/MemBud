# MemBud × opencode guard — Phase 0 productization spike

A minimal, **no-build / no-install** demonstration of using the MemBud
deterministic verifier as a localhost sidecar that guards a memory-write tool
call inside [opencode](https://opencode.ai).

This is a feasibility spike, **not production software**. Read
[“What is NOT proven”](#what-is-not-proven) before relying on any of it.

```
agent model ──tool call (memory_write)──► opencode tool wrapper
                                              │
                               "tool.execute.before" hook (this plugin)
                                              │  builds canonical schema-v2
                                              │  transition from tool args
                                              │  + local JSON memory state
                                              ▼
                          POST http://127.0.0.1:<port>/verify  (Python sidecar)
                                              │  deterministic graph/text verifier
                                              ▼
                              ACCEPT ──► return (opencode runs the memory tool)
                              HOLD / error / timeout ──► hook throws ──► deny
```

## Files

| Path | Purpose |
| --- | --- |
| `plugin/membud-verify-guard.js` | No-build ESM opencode plugin. Only imports Node built-ins. Default-exports `{ id, server }`. |
| `mock-memory-tool.mjs` | The untrusted mock memory tool (`memory_write`) that mutates a local JSON state file, plus atomic state helpers. |
| `harness.mjs` | Local node/bun harness: starts the real sidecar child process, replays opencode's tool-call order, asserts ACCEPT/HOLD/fail-closed behavior and measures latency. No model calls, no installs. |
| `config.snippet.jsonc` | Example opencode config (`plugin: [[spec, options]]`). |
| `live/memory-tool-plugin.mjs` | Mock `memory_write` tool used only by the opt-in live model test; writes env-configured state/capture files. |
| `live/EVIDENCE.md`, `live/evidence-2026-11-07.json` | Live model-originated denial test results (real model, real opencode session) and method. |
| `package.json` | `"type": "module"` so Node treats the `.js` plugin as ESM. No dependencies. |

The Python sidecar lives in `src/membud/service.py` at the repository root. The
opt-in live model test is `scripts/run_live_opencode_denial.py` (real paid
provider calls; requires `--confirm-paid-call`).

## Quickstart (local demo)

1. **Start the sidecar** (it prints the chosen URL on stdout; `--port 0` picks
   an ephemeral port):

   ```bash
   cd /path/to/MemBud
   TMPDIR=$PWD/.tmp-plugin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
     .venv/bin/python -m membud.service --host 127.0.0.1 --port 8787
   # membud-verify listening on http://127.0.0.1:8787
   ```

   `GET /healthz` is an unauthenticated liveness probe. `POST /verify` takes a
   canonical schema-v2 transition plus optional `"representation"` (`"graph"`
   default, `"text"` allowed) and returns
   `{decision, transition_valid, checks, verifier, representation, elapsed_ms,
   total_ms, error}`. It persists nothing.

2. **Point your memory tool's state file** at the JSON state the tool already
   owns. The guard reads it, never writes it:

   ```json
   { "schema_version": 2, "records": [{ "id": "r1", "claims": ["tinggal di Bandung"] }] }
   ```

   `memory_write` tool args (what the model emits; see `mock-memory-tool.mjs`):

   ```json
   {
     "case_id": "optional-string",
     "candidate": [{ "id": "r1", "claims": ["tinggal di Bandung"] },
                   { "id": "r2", "claims": ["bekerja di Tokopedia"] }],
     "evidence": [{ "id": "e1", "supports": ["bekerja di Tokopedia"], "source": "chat:42", "text": "..." }],
     "required_claims": ["tinggal di Bandung"],
     "operations": []
   }
   ```

   When `operations` is omitted the plugin derives `WRITE`/`REVISE` operations
   from the before/candidate diff. Removals are deliberately **not**
   auto-authorized: a `DELETE`/`SUPERSEDE` needs explicit authorization
   metadata, so a proposal that silently drops prior records is an invalid
   transition and is denied by the sidecar.

3. **Load the plugin** — copy the `plugin` array from
   `config.snippet.jsonc` into `.opencode/opencode.json`, or copy
   `plugin/membud-verify-guard.js` into `<project>/.opencode/plugin/`
   (auto-discovered by opencode 1.19.5).

Plugin options:

| Option | Default | Meaning |
| --- | --- | --- |
| `verifyUrl` | `http://127.0.0.1:8787/verify` | Sidecar endpoint. Env fallback: `MEMBUD_VERIFY_URL`. |
| `memoryStatePath` | `<plugin input.directory>/.membud-memory.json` | Local prior-state JSON file. Env fallback: `MEMBUD_MEMORY_STATE`. |
| `toolNames` | `["memory_write"]` | Tool names to gate. Everything else passes through untouched. |
| `timeoutMs` | `5000` | Per-request AbortSignal timeout. Env fallback: `MEMBUD_VERIFY_TIMEOUT_MS`. |
| `representation` | `"graph"` | `"graph"` (structural verifier) or `"text"` (claim-set baseline). |

There is also an **optional diagnostic probe**: if
`MEMBUD_PLUGIN_PROBE_FILE` is set, the plugin appends one JSON line when the
host calls `server()`. It is off by default and has no effect on guard
behavior; it exists so a smoke test can prove the real host loaded the module
(see [Real opencode smoke](#real-opencode-smoke)).

## Hook behavior (verified against the installed 1.19.5 binary)

The plugin only uses the `"tool.execute.before"` hook. Its typing in the
installed `@opencode-ai/plugin@1.18.30` `dist/index.d.ts` is:

```ts
"tool.execute.before"?: (input: {
  tool: string;
  sessionID: string;
  callID: string;
}, output: {
  args: any;
}) => Promise<void>;
```

The opencode 1.19.5 wrapper (`/usr/bin/opencode`, minified JS embedded in the
Bun binary) resolves plugin tools like this — note the `await` before
`k.execute`:

```js
yield* n.trigger("tool.execute.before",
  { tool: k.id, sessionID: Q.sessionID, callID: Q.callID }, { args: b });
let r = yield* k.execute(b, Q);   // only reached if the hook resolved
```

Consequences, all relied on by this plugin:

* **Throw = deny.** A rejected `tool.execute.before` promise aborts the wrapper
  before the tool runs; opencode records the thrown message as the tool call
  error. The plugin throws `MembudDeniedError` with the failing check names,
  `reason_code`s (e.g. `UNSUPPORTED_CLAIM`, `CONTRADICTED_CLAIM`,
  `INVALID_TRANSITION`, `PAYLOAD_TOO_LARGE`) and affected claim/record IDs.
* **Fail closed on everything else.** Sidecar unreachable, timeout
  (`AbortSignal.timeout`), non-2xx, non-JSON, missing `transition_valid`, or a
  `decision` other than exactly `ACCEPT` all throw — no ACCEPT path exists when
  the verifier is unavailable.
* **No argument rewriting.** In the 1.19.5 wrapper `execute` receives the
  *original* args (`b`), not a mutated `output.args`. This plugin therefore
  only allows/denies; it never relies on rewriting args.
* **Non-memory tools are untouched.** The hook returns immediately for tool
  names outside `toolNames`.
* `"permission.ask"` (`{status: "ask"|"deny"|"allow"}`) also exists in the
  typings, but the guard uses the before-hook as the single authoritative gate
  to keep one failure-mode surface; see “What is NOT proven”.

## Compatibility caveats — 1.18.30 typings vs 1.19.5 CLI

The local plugin API package and the CLI are **different versions**; this was
not a matched pair and is a real integration risk:

* Installed typings/package: `@opencode-ai/plugin@1.18.30`
  (`/home/ifan/.config/opencode/node_modules/@opencode-ai/plugin`).
* Installed CLI/binary: `opencode 1.19.5` (`/usr/bin/opencode`).
* 1.19.5's loader requires the module's **default export to be an object with
  a `server()` function** (`"...must default export an object with server()"`).
  This plugin complies (`export default { id, server }`) and also keeps a named
  `server` alias for older loaders/tests. The 1.18.30 package examples style
  (named export of a plugin *function*) is **not** what 1.19.5 validates, so do
  not copy that style.
* The plugin module itself needs **no** `@opencode-ai/plugin` import at
  runtime, so it is not coupled to the 1.18.30 package. The typings were used
  only as the documented hook contract.
* A real 1.18.30 CLI was not available to test; this plugin was only
  load-verified against the 1.19.5 binary. Treat hook behavior on other
  versions as unverified.
* opencode may, in the background, try to install `@opencode-ai/plugin` into
  the config directory it uses. That is a host behavior unrelated to this
  plugin (which has no dependencies). It was observed and failed harmlessly in
  an offline network namespace; on an online machine it may perform a network
  install. Pin/disable it if that matters to you.

## Local harness (no model calls, no installs)

```bash
cd /home/ifan/Projects/MemBud

# with Node v26
node integrations/opencode/harness.mjs --samples 40

# or with bun 1.4.4
bun integrations/opencode/harness.mjs --samples 40

# targeted sidecar tests
TMPDIR=$PWD/.tmp-plugin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_service.py
```

The harness starts the real Python sidecar as a child process on an ephemeral
localhost port, imports the plugin, and replays the 1.19.5 call order from the
binary (`await before-hook`, then `tool.execute`). It asserts:

| Scenario | Expected | Asserted |
| --- | --- | --- |
| Evidence-backed addition | ALLOW | Hook resolves; mock tool runs; JSON state advanced |
| Unsupported claim (`memiliki kapal pesiar`, no evidence) | DENY | Hook throws `UNSUPPORTED_CLAIM`; mock tool **not** called; state bytes unchanged |
| Contradicted claim (`evidence.contradicts`) | DENY | Hook throws `CONTRADICTED_CLAIM`; no tool call; state unchanged |
| Invalid transition (silent removal, no `DELETE` op) | DENY | Sidecar 400 `INVALID_TRANSITION`; no tool call; state unchanged |
| Sidecar down (`127.0.0.1:1`) | DENY | Hook throws “failing closed”; no tool call; state unchanged |
| Unreadable local state | DENY | Fails closed before any request; no host mutation |
| Non-memory tool (`bash`) | pass-through | Hook resolves, sidecar never contacted, state unchanged |

Recorded result (Node v26.12.1):

```
PASS plugin module imports only Node built-ins (node:fs/promises, node:path)
PASS scenario accept -> ALLOW (tool executed; state advanced to 2 record(s))
PASS scenario hold-unsupported -> DENY (reason_codes=[UNSUPPORTED_CLAIM])
PASS scenario hold-contradiction -> DENY (reason_codes=[CONTRADICTED_CLAIM])
PASS scenario invalid-transition -> DENY (reason_codes=[INVALID_TRANSITION])
PASS scenario sidecar-down -> DENY
PASS scenario non-memory-tool -> untouched
PASS scenario unreadable-local-state -> DENY (fail closed, no host mutation)
RESULT: PASS
```

## Latency (measured, real localhost process-to-process calls)

Measured on 2026-11-07 with the harness, **40 samples per path**, after 3
warm-up calls, percentile = nearest rank. “Direct” is one `fetch` round trip to
the sidecar `POST /verify` (JSON + TCP); “hook” is the full plugin
`tool.execute.before` path: read local state file → build canonical transition
→ sidecar POST → parse reply. No tool execution, no model, no installed
message bus.

Node v26.12.1:

| Path | n | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| sidecar `POST /verify` HTTP RTT (graph) | 40 | **1.539 ms** | **2.279 ms** | 3.348 ms |
| full plugin hook end-to-end | 40 | **2.025 ms** | **3.669 ms** | 5.978 ms |
| server-reported verifier `elapsed_ms` | 40 | 0.127 ms | 0.167 ms | 0.199 ms |

bun 1.4.4 (reports `node v26.3.0` under its Node emulation):

| Path | n | p50 | p95 | max |
| --- | --- | --- | --- | --- |
| sidecar `POST /verify` HTTP RTT (graph) | 40 | 1.318 ms | 1.670 ms | 1.739 ms |
| full plugin hook end-to-end | 40 | 1.433 ms | 2.006 ms | 2.313 ms |
| server-reported verifier `elapsed_ms` | 40 | 0.236 ms | 0.313 ms | 0.347 ms |

So the deterministic verification itself is ~0.1–0.35 ms; nearly all of the
observed overhead is the JSON/socket round trip plus the plugin's local state
read. **Latency caveats:** tiny fixtures (1–3 records, 1–2 claims); same
machine, loopback only; no TLS; no contention/concurrency testing; one
ephemeral connection per call (the sidecar answers HTTP/1.0 and closes); these
numbers say nothing about LLM/agent latency or large memory states. Repeat on
your target hardware with your own state sizes before quoting them.

## Real opencode smoke (plugin module load, no model)

`opencode serve` does **not** load plugins eagerly at process start; the module
is loaded when an instance bootstraps. A reproducible, no-egress procedure:

```bash
SMOKE=$PWD/.tmp-plugin/opencode-smoke
rm -rf "$SMOKE"; mkdir -p "$SMOKE/home/.config/opencode" "$SMOKE/project"
cat > "$SMOKE/opencode.json" <<'EOF'
{"$schema":"https://opencode.ai/config.json","plugin":[
 ["file:///absolute/path/to/MemBud/integrations/opencode/plugin/membud-verify-guard.js",
  {"verifyUrl":"http://127.0.0.1:1/verify","memoryStatePath":"./memory-state.json","timeoutMs":500}]]}
EOF
# bwrap --unshare-net guarantees no network egress from the host process:
env HOME="$SMOKE/home" OPENCODE_CONFIG="$SMOKE/opencode.json" \
    OPENCODE_DISABLE_MODELS_FETCH=true OPENCODE_DISABLE_AUTOUPDATE=true \
    MEMBUD_PLUGIN_PROBE_FILE="$SMOKE/probe.log" \
  timeout 120 bwrap --unshare-net --dev-bind / / --chdir "$SMOKE/project" \
    /usr/bin/opencode serve --port 4096 --print-logs --log-level INFO
# in another process (or from inside the same namespace when scripted):
#   GET /global/health, GET /experimental/tool/ids  (triggers instance bootstrap)
```

Recorded evidence from the spike (opencode 1.19.5, offline network namespace):

```text
# /usr/bin/opencode serve --port 0 --print-logs --log-level INFO
# stdio: opencode server listening on http://127.0.0.1:4096

# $SMOKE/probe.log (written by plugin server(), default export validated):
{"event":"server_hook_created","plugin":"membud-verify-guard",
 "directory":".../opencode-smoke/project",
 "verifyUrl":"http://127.0.0.1:1/verify",
 "memoryStatePath":".../project/memory-state.json",
 "toolNames":["memory_write"],"at":"2026-11-07T14:52:11.258Z"}

# $SMOKE/serve.err (host-side log; install failed only because egress was
# intentionally disabled):
timestamp=... level=WARN message="background dependency install failed"
  dir=.../home/.config/opencode
  error="...request to https://registry.npmjs.org/@opencode-ai%2fplugin failed..."
timestamp=... level=INFO message=init
```

Conclusion: the real 1.19.5 host **loaded this plugin module, validated the
default export, and invoked `server()`** — the probe line could only be written
from that code path. The bootstrap took ~70 s in the offline namespace because
opencode's background dependency-install attempt had to time out; on an online
machine it is quicker, but may attempt that install. This smoke proves **module
load only**: no tool was executed, no model was called, no hook was fired, and
no MCP host was involved (see below).

## Live model-originated denial test (2026-11-07)

The module-load smoke above proves loading only. A separate opt-in test runs
two scripted sessions through a real model, with a real sidecar and a real
opencode tool call, and asserts on the host state:

```bash
set -a; source .env; set +a   # DEEPSEEK_API_KEY
.venv/bin/python scripts/run_live_opencode_denial.py \
    --confirm-paid-call --workdir .tmp-live-denial
```

Environment: opencode 1.19.5, provider DeepSeek (`deepseek/deepseek-flash`),
isolated throw-away HOME with a copied plugin package and cached model catalog
(no npm install), and the real `membud.service` sidecar. Observed results
(opencode's provider-side estimate):

| Session | Tool call | Host state | Cost |
| --- | --- | --- | --- |
| HOLD (unsupported claim) | **error** — `MemBud denied memory_write: HOLD [UNSUPPORTED_CLAIM] — faithfulness=FAIL(...)`; model saw the denial | **byte-identical**, tool not executed (no capture line) | $0.00024948 |
| ACCEPT (evidence-backed) | **completed** — `memory_write applied 2 record(s)` | advanced `version 0 → 1` with exactly the candidate; one execution capture | $0.00028338 |

Both sessions logged `POST /verify 200`; the run used 1,042 input / 435 output
tokens, ≈**$0.00053** total. Full method, raw evidence and caveats:
[live/EVIDENCE.md](live/EVIDENCE.md). This is one model, one provider, one
opencode version and two scripted prompts — not general agent behavior.

## Security boundaries

* **Loopback only.** The sidecar refuses to bind anything but `127.0.0.1`
  (`make_server` raises on `0.0.0.0`/`localhost`) and rejects non-loopback
  peers. It has **no authentication**: any local process/user that can reach
  the port can ask for a verification decision. Trust the machine, not the
  network.
* **No egress.** The sidecar only imports the standard library; it makes no
  network calls. The plugin talks only to `verifyUrl` (default localhost) and
  reads a local file. The harness similarly spawns a local process and uses
  localhost only.
* **No persistence.** `service.py` never writes a store, audit log, temp file
  or lock. Tests assert a clean temporary working directory after ACCEPT and
  HOLD requests.
* **Untrusted model args.** Tool args are untrusted data. The sidecar enforces
  a 256 KiB body cap (configurable), `Content-Length` (no chunked bodies), a
  JSON depth limit (16), collection limits (≤5 000 records/each, ≤20 000
  claims), strict schema-version 2, a strict field allow-list, duplicate-key and
  NaN rejection, and a bounded socket timeout. Anything malformed or oversized
  is HOLD, never ACCEPT.
* **Fail closed everywhere** (client and server): bad JSON, invalid
  transitions, unknown representation, sidecar down, HTTP error, timeout,
  unreadable local state → denial. An unexpected internal error is a 500
  `HOLD`, and the handler never lets an exception escape.
* **No state mutation on HOLD.** The guard plugin is read-only over the memory
  state; only the host memory tool writes, and only after the hook resolves.
  The harness asserts byte-identical state after every denial.
* **Not a sandbox.** The hook gates *cooperative tool calls that go through
  opencode's tool wrapper*. Direct writes by another tool/process, a tool that
  ignores the hook, `--pure`, or an MCP server that writes memory itself are
  outside this boundary.

## What is NOT proven

* **One live model path, not general agent behavior.** A real
  `deepseek/deepseek-flash` session with a scripted prompt did call the
  plugin-registered `memory_write` tool: HOLD was denied (tool not executed,
  state byte-identical) and ACCEPT executed/persisted (see `live/EVIDENCE.md`).
  Not tested: other models/providers, unscripted agent behavior, or how a model
  might retry/escalate after a denial.
* **No MCP host integration.** This guards an opencode tool; it does not hook
  MCP servers, a real memory backend, or the provider layer. The guarded tool
  in the live test is a local mock that writes a JSON file.
* **No 1.18.30 CLI compatibility proof** (only the 1.19.5 binary was
  load-verified and exercised; the installed plugin typings are 1.18.30).
* **Not hardened for production:** no auth, no TLS, no rate limiting, no
  multi-tenant isolation, no concurrency/DoS testing, no metrics/audit, no
  packaging/distribution. The sidecar is a spike.
* **Argument rewriting is not used** (1.19.5 executes original args); if a
  future host version applies `output.args`, this plugin still only
  allows/denies and would not benefit.
* **No persistence design:** because the sidecar persists nothing, the guard
  verifies a *proposed* state against a local file snapshot; it does not own
  concurrent-write arbitration (no optimistic version check yet).

## Troubleshooting

* `MemBud verifier unavailable ... failing closed` — start the sidecar, check
  `verifyUrl`/port and `GET /healthz`.
* `MemBud denied ... [INVALID_TRANSITION] — undeclared removal ...` — the
  proposal removes/rewrites prior records without explicit `DELETE`/`REVISE`/
  `SUPERSEDE` operations (and DELETE requires structured authorization).
* `[PAYLOAD_TOO_LARGE]` — request exceeded the sidecar's body cap; raise
  `--max-body-bytes` only if you also accept the memory/DoS trade-off.
* Plugin not loading — confirm the file's default export is
  `{ id, server }`, the spec is a `file://` URL/absolute path, and set
  `MEMBUD_PLUGIN_PROBE_FILE` to confirm `server()` is invoked.
