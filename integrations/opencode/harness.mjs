#!/usr/bin/env node
/**
 * Local, no-model harness for the MemBud ⇄ opencode guard spike.
 *
 * What it does (no network egress, no opencode model calls, no installs):
 *
 * 1. starts the real Python `/verify` sidecar as a child process
 *    (`python -m membud.service --host 127.0.0.1 --port 0`, ephemeral port);
 * 2. imports the no-build ESM plugin exactly as a host would and builds its
 *    hooks with `plugin.default.server(input, options)`;
 * 3. replays the opencode 1.19.5 tool-execution order that was read from the
 *    installed binary:
 *
 *        await trigger("tool.execute.before", {tool, sessionID, callID}, {args})
 *        let result = await tool.execute(args, ctx)
 *
 *    (the real host `await`s the before-hook; a throw skips execution);
 * 4. exercises ACCEPT, HOLD (unsupported claim), HOLD (contradiction),
 *    sidecar-down fail-closed, invalid transition, unreadable local state and
 *    a non-memory tool passthrough, asserting on each whether the mock memory
 *    tool executed and whether the JSON memory state changed;
 * 5. measures latency over >=30 real localhost round trips: direct sidecar
 *    HTTP RTT and full plugin-hook end-to-end, printing p50/p95/max.
 *
 * Usage: `node integrations/opencode/harness.mjs [--samples 40]`
 * Exits 0 only if every scenario and assertion passes.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

import {
  MEMORY_WRITE_TOOL,
  readMemoryState,
  writeMemoryState,
} from "./mock-memory-tool.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../..");
const PLUGIN_PATH = join(HERE, "plugin", "membud-verify-guard.js");
const TMP_ROOT = join(REPO_ROOT, ".tmp-plugin");
const DEMO_DIR = join(TMP_ROOT, "opencode-demo");
const STATE_PATH = join(DEMO_DIR, "memory-state.json");
const PYTHON = process.env.MEMBUD_PYTHON || join(REPO_ROOT, ".venv", "bin", "python");

const SAMPLE_TARGET = (() => {
  const index = process.argv.indexOf("--samples");
  const value = index >= 0 ? Number(process.argv[index + 1]) : 40;
  return Number.isInteger(value) && value >= 30 ? value : 40;
})();

const BEFORE_RECORDS = [{ id: "r1", claims: ["tinggal di Bandung"] }];

const ACCEPT_ARGS = {
  case_id: "harness-accept",
  candidate: [
    { id: "r1", claims: ["tinggal di Bandung"] },
    { id: "r2", claims: ["bekerja di Tokopedia"] },
  ],
  evidence: [
    {
      id: "e1",
      source: "harness:employment",
      text: "Ia bekerja di Tokopedia.",
      supports: ["bekerja di Tokopedia"],
    },
  ],
  required_claims: ["tinggal di Bandung"],
};

const HOLD_UNSUPPORTED_ARGS = {
  case_id: "harness-hold-unsupported",
  candidate: [...BEFORE_RECORDS, { id: "r2", claims: ["memiliki kapal pesiar"] }],
  evidence: [],
  required_claims: [],
};

const HOLD_CONTRADICTION_ARGS = {
  case_id: "harness-hold-contradiction",
  candidate: [...BEFORE_RECORDS, { id: "r2", claims: ["bekerja di Tokopedia"] }],
  evidence: [
    {
      id: "e1",
      source: "harness:conflict",
      text: "Ia tidak lagi bekerja di Tokopedia.",
      supports: ["bekerja di Tokopedia"],
      contradicts: ["bekerja di Tokopedia"],
    },
  ],
  required_claims: [],
};

const INVALID_TRANSITION_ARGS = {
  case_id: "harness-invalid-removal",
  candidate: [], // removes r1 without a declared DELETE -> invalid schema-v2 transition
  evidence: [],
  required_claims: [],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms) {
  return new Promise((done) => setTimeout(done, ms));
}

function formatMs(value) {
  return Number.isFinite(value) ? value.toFixed(3) : "n/a";
}

function percentile(sortedValues, percent) {
  if (sortedValues.length === 0) return Number.NaN;
  const rank = Math.ceil((percent / 100) * sortedValues.length);
  return sortedValues[Math.min(sortedValues.length - 1, Math.max(0, rank - 1))];
}

function summarize(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return {
    n: sorted.length,
    p50: percentile(sorted, 50),
    p95: percentile(sorted, 95),
    max: sorted.length ? sorted[sorted.length - 1] : Number.NaN,
    min: sorted.length ? sorted[0] : Number.NaN,
  };
}

async function readFirstLine(stream, timeoutMs) {
  return await new Promise((resolvePromise, rejectPromise) => {
    const reader = createInterface({ input: stream });
    const timer = setTimeout(() => {
      reader.close();
      rejectPromise(new Error(`timed out after ${timeoutMs}ms waiting for the sidecar URL`));
    }, timeoutMs);
    reader.once("line", (line) => {
      clearTimeout(timer);
      reader.close();
      resolvePromise(line);
    });
  });
}

async function waitForHealth(baseUrl, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/healthz`);
      if (response.ok) return await response.json();
      lastError = new Error(`healthz HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  throw new Error(`sidecar did not become healthy: ${lastError}`);
}

async function startSidecar() {
  const child = spawn(
    PYTHON,
    ["-m", "membud.service", "--host", "127.0.0.1", "--port", "0"],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        PYTHONPATH: join(REPO_ROOT, "src"),
        PYTHONDONTWRITEBYTECODE: "1",
        TMPDIR: TMP_ROOT,
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let stderr = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const exitPromise = new Promise((done) => child.once("exit", (code, signal) => done({ code, signal })));
  try {
    const line = await readFirstLine(child.stdout, 15_000);
    const match = /listening on (http:\/\/127\.0\.0\.1:\d+)/.exec(line);
    if (!match) throw new Error(`unexpected sidecar startup line: ${line}`);
    const baseUrl = match[1];
    const health = await waitForHealth(baseUrl);
    return { child, baseUrl, verifyUrl: `${baseUrl}/verify`, health, exitPromise, stderr: () => stderr };
  } catch (error) {
    child.kill("SIGKILL");
    throw new Error(`${error.message}\nsidecar stderr:\n${stderr}`);
  }
}

async function stopSidecar(sidecar) {
  if (!sidecar) return;
  sidecar.child.kill("SIGTERM");
  const finished = await Promise.race([sidecar.exitPromise, sleep(5000).then(() => "timeout")]);
  if (finished === "timeout") {
    sidecar.child.kill("SIGKILL");
    await sidecar.exitPromise;
  }
}

function fakePluginInput() {
  return {
    client: {},
    project: { id: "harness", worktree: REPO_ROOT },
    directory: DEMO_DIR,
    worktree: REPO_ROOT,
    experimental_workspace: { register() {} },
    serverUrl: new URL("http://127.0.0.1:4096"),
    $: async () => ({ exitCode: 0 }),
  };
}

async function loadPluginModule() {
  const module = await import(pathToFileURL(PLUGIN_PATH).href);
  const plugin = module.default;
  assert.ok(plugin && typeof plugin === "object", "plugin must default-export an object");
  assert.equal(typeof plugin.server, "function", "plugin default export must expose server()");
  return module;
}

async function createHooks(options) {
  const module = await loadPluginModule();
  const hooks = await module.default.server(fakePluginInput(), options);
  assert.equal(typeof hooks["tool.execute.before"], "function", "hook missing");
  return hooks;
}

/**
 * Replay the opencode 1.19.5 order observed in the installed binary:
 * the before-hook is awaited before the tool executes.
 */
async function guardedToolCall(hooks, args, callID) {
  let executed = false;
  try {
    await hooks["tool.execute.before"](
      { tool: MEMORY_WRITE_TOOL.name, sessionID: "ses_harness", callID },
      { args },
    );
    executed = true;
    await MEMORY_WRITE_TOOL.execute(args, { memoryStatePath: STATE_PATH, directory: DEMO_DIR });
    return { allowed: true, executed, error: null };
  } catch (error) {
    return { allowed: false, executed, error };
  }
}

async function seedState() {
  await rm(STATE_PATH, { force: true });
  await writeMemoryState(STATE_PATH, { schema_version: 2, version: 1, records: BEFORE_RECORDS });
}

function denialCodes(error) {
  if (Array.isArray(error?.membud?.reason_codes)) return error.membud.reason_codes;
  const match = /\[([A-Z_]+(?:, [A-Z_]+)*)\]/.exec(error?.message ?? "");
  return match ? match[1].split(", ") : [];
}

async function runScenario({ name, args, options, expected, expectedCodes = [], messageIncludes = [] }) {
  await seedState();
  const beforeBytes = await readFile(STATE_PATH);
  const hooks = await createHooks(options);
  const callID = `call_${name.replace(/[^a-z0-9]+/gi, "_")}`;
  const outcome = await guardedToolCall(hooks, args, callID);
  const afterBytes = await readFile(STATE_PATH);

  if (expected === "allow") {
    assert.equal(outcome.allowed, true, `${name}: expected ALLOW, got denial: ${outcome.error?.message}`);
    assert.equal(outcome.executed, true, `${name}: expected the mock tool to execute`);
    assert.notDeepEqual(beforeBytes, afterBytes, `${name}: accepted write must be applied by the tool`);
    const applied = await readMemoryState(STATE_PATH);
    assert.deepEqual(applied.records, args.candidate, `${name}: candidate state not applied`);
    console.log(`PASS scenario ${name} -> ALLOW (tool executed; state advanced to ${applied.records.length} record(s))`);
    return;
  }

  assert.equal(outcome.allowed, false, `${name}: expected DENY, but the tool call was allowed`);
  assert.equal(outcome.executed, false, `${name}: tool executed despite a denial — guard is not enforcing`);
  assert.deepEqual(afterBytes, beforeBytes, `${name}: HOLD/error must not mutate host state`);
  const codes = denialCodes(outcome.error);
  for (const code of expectedCodes) {
    assert.ok(
      codes.includes(code) || (outcome.error?.message ?? "").includes(code),
      `${name}: expected reason code ${code}; got [${codes.join(", ")}] in: ${outcome.error?.message}`,
    );
  }
  for (const fragment of messageIncludes) {
    assert.ok(
      (outcome.error?.message ?? "").includes(fragment),
      `${name}: expected message to include ${JSON.stringify(fragment)}; got: ${outcome.error?.message}`,
    );
  }
  console.log(
    `PASS scenario ${name} -> DENY (tool not executed; state unchanged; reason_codes=[${codes.join(", ")}])`,
  );
}

function assertNoRuntimeDependencies() {
  // The plugin must load with no package installs: only Node built-ins.
  return readFile(PLUGIN_PATH, "utf8").then((source) => {
    const specifiers = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)"/gm)].map((match) => match[1]);
    for (const specifier of specifiers) {
      assert.ok(specifier.startsWith("node:"), `plugin imports a non-builtin module: ${specifier}`);
    }
    console.log(`PASS plugin module imports only Node built-ins (${specifiers.join(", ")})`);
    const forbidden = ["@opencode-ai", "zod", "npm:", "https:"];
    for (const token of forbidden) {
      assert.ok(!source.includes(`from "${token}`), `plugin must not depend on ${token}`);
    }
  });
}

// ---------------------------------------------------------------------------
// Latency measurement
// ---------------------------------------------------------------------------

async function postVerifyDirect(verifyUrl, transition, representation = "graph") {
  const body = JSON.stringify({ ...transition, representation });
  const started = performance.now();
  const response = await fetch(verifyUrl, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body,
  });
  const text = await response.text();
  const elapsed = performance.now() - started;
  assert.equal(response.status, 200, `direct /verify HTTP ${response.status}: ${text}`);
  return { payload: JSON.parse(text), elapsed };
}

function buildAcceptTransition() {
  return {
    schema_version: 2,
    case_id: "harness-latency",
    before: BEFORE_RECORDS,
    candidate: ACCEPT_ARGS.candidate,
    evidence: ACCEPT_ARGS.evidence,
    required_claims: ACCEPT_ARGS.required_claims,
    operations: [{ type: "WRITE", record_id: "r2" }],
  };
}

async function measureLatency(sidecar) {
  await seedState();
  const hooks = await createHooks({
    verifyUrl: sidecar.verifyUrl,
    memoryStatePath: STATE_PATH,
    timeoutMs: 5000,
  });
  const transition = buildAcceptTransition();

  // Warm up both paths (not counted) so module/connection caches are warm.
  for (let index = 0; index < 3; index += 1) {
    await postVerifyDirect(sidecar.verifyUrl, transition);
    await hooks["tool.execute.before"](
      { tool: MEMORY_WRITE_TOOL.name, sessionID: "ses_warmup", callID: `warmup_${index}` },
      { args: ACCEPT_ARGS },
    );
  }

  const directRtt = [];
  const serverVerifyMs = [];
  for (let index = 0; index < SAMPLE_TARGET; index += 1) {
    const { payload, elapsed } = await postVerifyDirect(sidecar.verifyUrl, transition);
    assert.equal(payload.decision, "ACCEPT", "latency fixture must ACCEPT");
    directRtt.push(elapsed);
    serverVerifyMs.push(payload.elapsed_ms);
  }

  const hookE2e = [];
  for (let index = 0; index < SAMPLE_TARGET; index += 1) {
    const started = performance.now();
    await hooks["tool.execute.before"](
      { tool: MEMORY_WRITE_TOOL.name, sessionID: "ses_latency", callID: `latency_${index}` },
      { args: ACCEPT_ARGS },
    );
    hookE2e.push(performance.now() - started);
  }

  const direct = summarize(directRtt);
  const server = summarize(serverVerifyMs);
  const e2e = summarize(hookE2e);
  console.log("");
  console.log(
    `Latency — real localhost process-to-process calls, fixture: 1 prior record + 1 new record, ` +
      `1 evidence item, 1 required claim, ${SAMPLE_TARGET} samples (percentile = nearest rank)`,
  );
  console.log("");
  console.log("| Path | n | p50 | p95 | max |");
  console.log("| --- | --- | --- | --- | --- |");
  console.log(
    `| sidecar POST /verify HTTP RTT (includes JSON + socket) | ${direct.n} | ${formatMs(direct.p50)} ms | ${formatMs(direct.p95)} ms | ${formatMs(direct.max)} ms |`,
  );
  console.log(
    `| full plugin hook end-to-end (read state + build transition + POST /verify) | ${e2e.n} | ${formatMs(e2e.p50)} ms | ${formatMs(e2e.p95)} ms | ${formatMs(e2e.max)} ms |`,
  );
  console.log("");
  console.log(
    `Server-reported verifier elapsed_ms: p50 ${formatMs(server.p50)} ms, p95 ${formatMs(server.p95)} ms, max ${formatMs(server.max)} ms`,
  );
  console.log(
    `Min: direct ${formatMs(direct.min)} ms, hook ${formatMs(e2e.min)} ms; ` +
      `final measured samples n=${SAMPLE_TARGET} each (>= 30 required).`,
  );
  return { direct, e2e, server };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  console.log(`MemBud opencode guard harness (node ${process.version})`);
  console.log(`plugin: ${PLUGIN_PATH}`);
  console.log(`samples per latency path: ${SAMPLE_TARGET}`);
  await mkdir(DEMO_DIR, { recursive: true });
  await writeFile(join(TMP_ROOT, ".keep"), "", { flag: "a" });

  await assertNoRuntimeDependencies();

  let sidecar = null;
  try {
    sidecar = await startSidecar();
    console.log(`sidecar: ${sidecar.baseUrl} (pid ${sidecar.child.pid})`);
    assert.equal(sidecar.health.status, "ok");
    assert.equal(sidecar.health.service, "membud-verify");

    const baseOptions = {
      verifyUrl: sidecar.verifyUrl,
      memoryStatePath: STATE_PATH,
      timeoutMs: 5000,
    };

    await runScenario({ name: "accept", args: ACCEPT_ARGS, options: baseOptions, expected: "allow" });
    await runScenario({
      name: "hold-unsupported",
      args: HOLD_UNSUPPORTED_ARGS,
      options: baseOptions,
      expected: "deny",
      expectedCodes: ["UNSUPPORTED_CLAIM"],
    });
    await runScenario({
      name: "hold-contradiction",
      args: HOLD_CONTRADICTION_ARGS,
      options: baseOptions,
      expected: "deny",
      expectedCodes: ["CONTRADICTED_CLAIM"],
    });
    await runScenario({
      name: "invalid-transition",
      args: INVALID_TRANSITION_ARGS,
      options: baseOptions,
      expected: "deny",
      expectedCodes: ["INVALID_TRANSITION"],
    });
    await runScenario({
      name: "sidecar-down",
      args: ACCEPT_ARGS,
      options: { ...baseOptions, verifyUrl: "http://127.0.0.1:1/verify", timeoutMs: 1000 },
      expected: "deny",
      messageIncludes: ["failing closed"],
    });

    // A non-memory tool must not be gated by this plugin.
    await seedState();
    const beforeBytes = await readFile(STATE_PATH);
    const hooks = await createHooks(baseOptions);
    await hooks["tool.execute.before"](
      { tool: "bash", sessionID: "ses_harness", callID: "call_bash" },
      { args: { command: "true" } },
    );
    assert.deepEqual(await readFile(STATE_PATH), beforeBytes);
    console.log("PASS scenario non-memory-tool -> untouched (no sidecar call, no denial)");

    // Unreadable local state must fail closed before any write.
    await rm(STATE_PATH, { force: true });
    const stateHooks = await createHooks(baseOptions);
    const stateOutcome = await guardedToolCall(stateHooks, ACCEPT_ARGS, "call_missing_state");
    assert.equal(stateOutcome.allowed, false);
    assert.equal(stateOutcome.executed, false);
    assert.ok((stateOutcome.error?.message ?? "").includes("failed closed"));
    console.log("PASS scenario unreadable-local-state -> DENY (fail closed, no host mutation)");
    await seedState();

    const latency = await measureLatency(sidecar);
    console.log("");
    console.log(
      `RESULT: PASS — 5 guarded scenarios + 1 non-memory passthrough + 1 unreadable-state fail-closed + latency measurement (${SAMPLE_TARGET} samples/path)`,
    );
    console.log(
      `LATENCY_JSON: ${JSON.stringify({
        direct_p50_ms: Number(latency.direct.p50.toFixed(3)),
        direct_p95_ms: Number(latency.direct.p95.toFixed(3)),
        direct_max_ms: Number(latency.direct.max.toFixed(3)),
        hook_p50_ms: Number(latency.e2e.p50.toFixed(3)),
        hook_p95_ms: Number(latency.e2e.p95.toFixed(3)),
        hook_max_ms: Number(latency.e2e.max.toFixed(3)),
        server_elapsed_p50_ms: Number(latency.server.p50.toFixed(3)),
        server_elapsed_p95_ms: Number(latency.server.p95.toFixed(3)),
        samples: SAMPLE_TARGET,
      })}`,
    );
  } finally {
    await stopSidecar(sidecar);
  }
}

main().catch((error) => {
  console.error("FAIL:", error?.stack ?? error);
  process.exitCode = 1;
});
