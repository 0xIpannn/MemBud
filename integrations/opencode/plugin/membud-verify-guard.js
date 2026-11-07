/**
 * MemBud verify guard — minimal, no-build ESM opencode plugin (Phase 0 spike).
 *
 * Contract implemented (see integrations/opencode/README.md for evidence):
 *
 *   export default { id, server }
 *
 * `server(input, options)` returns opencode hooks.  The
 * `"tool.execute.before"` hook intercepts the configured memory tool name
 * (default `memory_write`), reads the local JSON memory state, builds a
 * canonical schema-v2 MemBud transition from the tool args + that prior state,
 * POSTs it to the local MemBud `/verify` sidecar, and:
 *
 *   - returns normally (ALLOW) when the sidecar returns decision "ACCEPT";
 *   - THROWS when the decision is HOLD, the request fails/times out, the
 *     response is malformed, or the local state/args cannot be read.
 *
 * Throwing is the denial mechanism: opencode's tool wrapper awaits
 * `tool.execute.before` before calling `tool.execute`, so a throw prevents the
 * memory tool from running and surfaces the message as the tool-call error.
 * The plugin never mutates host state and never rewrites tool arguments.
 *
 * This module uses only Node.js built-ins (`node:fs/promises`, `node:path`)
 * and the platform `fetch`, so it loads with no package installation.  The
 * real opencode process is Bun, which runs this ESM file as-is.
 */
import { appendFile, readFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";

export const PLUGIN_ID = "membud-verify-guard";
export const DEFAULT_VERIFY_URL = "http://127.0.0.1:8787/verify";
export const DEFAULT_MEMORY_STATE_FILE = ".membud-memory.json";
export const DEFAULT_TOOL_NAMES = Object.freeze(["memory_write"]);
export const DEFAULT_TIMEOUT_MS = 5000;
export const SCHEMA_VERSION = 2;

const REPRESENTATIONS = new Set(["graph", "text"]);

/**
 * Optional diagnostic probe (env var, no network): when set, `server()` appends
 * one JSON line so a smoke test can prove that the real opencode host loaded
 * this module and called `server()`.  Off by default; never used by the guard.
 */
export const PROBE_ENV = "MEMBUD_PLUGIN_PROBE_FILE";

function writeLoadProbe(input, settings) {
  const probePath = process.env[PROBE_ENV];
  if (typeof probePath !== "string" || !probePath.trim()) return;
  const line =
    JSON.stringify({
      event: "server_hook_created",
      plugin: PLUGIN_ID,
      directory: input?.directory ?? null,
      verifyUrl: settings.verifyUrl,
      memoryStatePath: settings.memoryStatePath,
      toolNames: settings.toolNames,
      at: new Date().toISOString(),
    }) + "\n";
  // Best effort: a broken probe path must never affect guard behavior.
  void appendFile(probePath, line, "utf8").catch(() => {});
}

/** Error thrown from the hook to deny a tool call (fail closed). */
export class MembudDeniedError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = "MembudDeniedError";
    /** Structured details for harnesses/telemetry; not consumed by opencode. */
    this.membud = details;
  }
}

function deny(message, details = {}) {
  throw new MembudDeniedError(message, details);
}

function describeCause(cause) {
  if (!cause) return "unknown error";
  if (typeof cause === "string") return cause;
  const name = cause.name && cause.name !== "Error" ? `${cause.name}: ` : "";
  return `${name}${cause.message ?? String(cause)}`;
}

function resolveMemoryStatePath(input, options) {
  const configured =
    typeof options.memoryStatePath === "string" && options.memoryStatePath.trim()
      ? options.memoryStatePath
      : typeof process.env.MEMBUD_MEMORY_STATE === "string" && process.env.MEMBUD_MEMORY_STATE.trim()
        ? process.env.MEMBUD_MEMORY_STATE
        : "";
  if (configured) {
    return isAbsolute(configured) ? configured : resolve(input?.directory ?? process.cwd(), configured);
  }
  return resolve(input?.directory ?? process.cwd(), DEFAULT_MEMORY_STATE_FILE);
}

/**
 * Normalize plugin options once per `server()` call.
 *
 * Options come from opencode config `plugin: [[spec, options]]` (or the
 * package export config); environment variables are a convenience fallback
 * for local demos.
 */
export function normalizeOptions(input, options = {}) {
  const rawOptions = options && typeof options === "object" ? options : {};
  const verifyUrl =
    typeof rawOptions.verifyUrl === "string" && rawOptions.verifyUrl.trim()
      ? rawOptions.verifyUrl.trim()
      : typeof process.env.MEMBUD_VERIFY_URL === "string" && process.env.MEMBUD_VERIFY_URL.trim()
        ? process.env.MEMBUD_VERIFY_URL.trim()
        : DEFAULT_VERIFY_URL;

  const timeoutCandidate =
    rawOptions.timeoutMs ?? Number(process.env.MEMBUD_VERIFY_TIMEOUT_MS ?? NaN);
  const timeoutMs =
    Number.isFinite(timeoutCandidate) && timeoutCandidate > 0 ? timeoutCandidate : DEFAULT_TIMEOUT_MS;

  const representation =
    typeof rawOptions.representation === "string" && rawOptions.representation.trim()
      ? rawOptions.representation.trim()
      : "graph";
  if (!REPRESENTATIONS.has(representation)) {
    throw new TypeError(`representation must be one of ${[...REPRESENTATIONS].join(", ")}`);
  }

  const toolNames =
    Array.isArray(rawOptions.toolNames) && rawOptions.toolNames.length
      ? rawOptions.toolNames.filter((name) => typeof name === "string" && name.trim()).map((n) => n.trim())
      : [...DEFAULT_TOOL_NAMES];

  return {
    verifyUrl,
    memoryStatePath: resolveMemoryStatePath(input, rawOptions),
    timeoutMs,
    representation,
    toolNames,
  };
}

/** Read the local JSON memory state that represents the host's prior state. */
export async function loadMemoryState(statePath) {
  let raw;
  try {
    raw = await readFile(statePath, "utf8");
  } catch (cause) {
    deny(
      `MemBud guard failed closed: cannot read local memory state at ${statePath} (${describeCause(cause)})`,
      { stage: "state", statePath, cause: describeCause(cause) },
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (cause) {
    deny(
      `MemBud guard failed closed: local memory state is not valid JSON (${describeCause(cause)})`,
      { stage: "state", statePath, cause: describeCause(cause) },
    );
  }
  const records = Array.isArray(parsed) ? parsed : parsed?.records;
  if (!Array.isArray(records)) {
    deny(
      `MemBud guard failed closed: local memory state must contain a "records" array`,
      { stage: "state", statePath },
    );
  }
  return records;
}

function claimKey(claims) {
  return JSON.stringify(Array.isArray(claims) ? claims : []);
}

/**
 * Derive canonical WRITE/REVISE operations from the before/candidate diff.
 *
 * Removals are intentionally *not* auto-authorized: a schema-v2 DELETE or
 * SUPERSEDE needs an explicit operation plus structured authorization
 * metadata, so a proposal that silently removes prior records stays an invalid
 * transition and the sidecar HOLDs it.  Callers can pass explicit operations.
 */
export function deriveOperations(beforeRecords, candidateRecords) {
  if (!Array.isArray(beforeRecords) || !Array.isArray(candidateRecords)) return [];
  const beforeById = new Map();
  for (const record of beforeRecords) {
    if (record && typeof record.id === "string") beforeById.set(record.id, record);
  }
  const operations = [];
  for (const record of candidateRecords) {
    if (!record || typeof record.id !== "string") continue;
    const prior = beforeById.get(record.id);
    if (!prior) {
      operations.push({ type: "WRITE", record_id: record.id });
    } else if (claimKey(prior.claims) !== claimKey(record.claims)) {
      operations.push({ type: "REVISE", record_id: record.id, target_id: record.id });
    }
  }
  return operations;
}

/** Build the canonical schema-v2 transition posted to the sidecar. */
export function buildTransition(args, beforeRecords, callID) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    deny(`MemBud guard failed closed: memory_write args must be a JSON object`, { stage: "args" });
  }
  if (!Array.isArray(args.candidate)) {
    deny(
      `MemBud guard failed closed: memory_write args must include a "candidate" record array`,
      { stage: "args" },
    );
  }
  const caseId =
    typeof args.case_id === "string" && args.case_id.trim()
      ? args.case_id.trim()
      : `memory-write-${String(callID || "call")}`;
  return {
    schema_version: SCHEMA_VERSION,
    case_id: caseId,
    before: beforeRecords,
    candidate: args.candidate,
    evidence: Array.isArray(args.evidence) ? args.evidence : [],
    required_claims: Array.isArray(args.required_claims) ? args.required_claims : [],
    operations: Array.isArray(args.operations)
      ? args.operations
      : deriveOperations(beforeRecords, args.candidate),
  };
}

function formatDecisionDenial(payload) {
  const checks = Array.isArray(payload.checks) ? payload.checks : [];
  const failed = checks.filter((check) => check?.status !== "PASS" || check?.passed !== true);
  const reasonCodes = [...new Set(failed.map((check) => check.reason_code).filter(Boolean))];
  const errorMessage = typeof payload.error?.message === "string" ? payload.error.message : "";
  const checkSummary = failed
    .map((check) => `${check.name}=${check.status}(${check.reason_code}: ${check.reason})`)
    .join("; ");
  const reason = errorMessage || checkSummary || "verification did not accept the write";
  const codeSuffix = reasonCodes.length ? ` [${reasonCodes.join(", ")}]` : "";
  return `MemBud denied memory_write: ${payload.decision ?? "HOLD"}${codeSuffix} — ${reason}`;
}

/**
 * POST one transition to the sidecar and fail closed on anything that is not a
 * complete ACCEPT.  Resolves to `{payload, roundtripMs}` on ACCEPT.
 */
export async function requestVerification(transition, settings) {
  const body = JSON.stringify({ ...transition, representation: settings.representation });
  const startedAt = performance.now();
  let response;
  try {
    response = await fetch(settings.verifyUrl, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body,
      signal: AbortSignal.timeout(settings.timeoutMs),
    });
  } catch (cause) {
    deny(
      `MemBud verifier unavailable at ${settings.verifyUrl}; failing closed (${describeCause(cause)})`,
      { stage: "transport", verifyUrl: settings.verifyUrl, cause: describeCause(cause) },
    );
  }
  const roundtripMs = performance.now() - startedAt;

  let text = "";
  let payload = null;
  try {
    text = await response.text();
    payload = text ? JSON.parse(text) : null;
  } catch (cause) {
    deny(
      `MemBud verifier returned a non-JSON response (HTTP ${response.status}); failing closed (${describeCause(cause)})`,
      { stage: "response", httpStatus: response.status, body: text.slice(0, 500) },
    );
  }
  if (!response.ok || !payload || payload.error) {
    const code = payload?.error?.code ?? "HTTP_ERROR";
    const message = payload?.error?.message ?? `HTTP ${response.status}`;
    deny(`MemBud denied memory_write: HOLD [${code}] — ${message}`, {
      stage: "response",
      httpStatus: response.status,
      reason_codes: [code],
      checks: payload?.checks ?? [],
    });
  }
  if (payload.decision !== "ACCEPT" || payload.transition_valid !== true) {
    deny(formatDecisionDenial(payload), {
      stage: "decision",
      httpStatus: response.status,
      decision: payload.decision ?? null,
      reason_codes: [...new Set((payload.checks ?? []).filter((c) => c.status !== "PASS").map((c) => c.reason_code).filter(Boolean))],
      checks: payload.checks ?? [],
      elapsed_ms: payload.elapsed_ms ?? null,
    });
  }
  return { payload, roundtripMs };
}

/** One guarded memory write: prior state + args -> transition -> /verify. */
export async function verifyMemoryWrite(args, settings, callID) {
  const beforeRecords = await loadMemoryState(settings.memoryStatePath);
  const transition = buildTransition(args, beforeRecords, callID);
  return requestVerification(transition, settings);
}

/**
 * Build the hooks object.  Exported so a no-model harness can exercise the
 * exact hook function opencode would call.
 */
export function createGuardHooks(input, options = {}) {
  const settings = normalizeOptions(input, options);
  writeLoadProbe(input, settings);
  return {
    "tool.execute.before": async (hookInput, hookOutput) => {
      if (!hookInput || !settings.toolNames.includes(hookInput.tool)) return;
      const args = hookOutput?.args ?? {};
      await verifyMemoryWrite(args, settings, hookInput.callID);
      // ALLOW: do not rewrite output.args.  The opencode 1.19.5 tool wrapper
      // passes the original args to tool.execute (see README), so a guard that
      // rewrites args here would give a false sense of enforcement.
    },
  };
}

/**
 * opencode 1.19.5 plugin loader requires a *default export object* with a
 * `server()` function (verified from the 1.19.5 binary).  Named exports are
 * kept for older loaders/tests and are harmless.
 */
const plugin = {
  id: PLUGIN_ID,
  server: async (input, options) => createGuardHooks(input, options),
};

export default plugin;
export const server = plugin.server;
