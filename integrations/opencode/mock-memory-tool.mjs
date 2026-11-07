/**
 * Mock host memory tool for the MemBud ⇄ opencode Phase 0 spike.
 *
 * This is the *untrusted* side of the demo: a memory-write tool that mutates a
 * local JSON memory state when it runs.  The guard plugin must prevent it from
 * running on HOLD/error/timeout; the harness asserts the state file bytes are
 * unchanged in those outcomes.
 *
 * The object shape mirrors an `@opencode-ai/plugin` ToolDefinition
 * (`{description, args, execute}`).  A real opencode registration would build
 * `args` with the `tool()`/zod helper from `@opencode-ai/plugin`; this spike
 * deliberately avoids that package so it needs no installs.  The arg contract
 * is documented below and shared with the guard plugin.
 */
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { basename, dirname, join } from "node:path";

/**
 * memory_write args contract (what the model would emit):
 *
 *   {
 *     case_id?: string,
 *     candidate: Array<{ id: string, claims: string[] }>,
 *     evidence?: Array<{
 *       id: string,
 *       supports: string[],
 *       source?: string,
 *       text?: string,
 *       contradicts?: string[]
 *     }>,
 *     required_claims?: string[],
 *     operations?: Array<{
 *       type: "WRITE" | "REVISE" | "DELETE" | "SUPERSEDE" | "NOOP",
 *       record_id?: string | null,
 *       target_id?: string | null,
 *       reason?: string,
 *       authorization?: {source, evidence_id, target_id, rationale} | null
 *     }>
 *   }
 */
export const MEMORY_WRITE_ARGS = Object.freeze({
  case_id: "string (optional; defaults to memory-write-<callID>)",
  candidate: "array of canonical records {id, claims[]} (required)",
  evidence: "array of canonical evidence {id, supports[], source?, text?, contradicts?}",
  required_claims: "array of claim strings that must survive the write",
  operations: "array of schema-v2 operations; derived from the diff when omitted",
});

/** Read the versioned JSON memory state (missing file means empty state). */
export async function readMemoryState(statePath) {
  let raw;
  try {
    raw = await readFile(statePath, "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return { schema_version: 2, version: 0, records: [] };
    }
    throw error;
  }
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.records)) {
    throw new Error(`memory state at ${statePath} must be {records: [...]}`);
  }
  return parsed;
}

/** Atomically replace the memory state (temp file + rename in same dir). */
export async function writeMemoryState(statePath, data) {
  await mkdir(dirname(statePath), { recursive: true });
  const temporary = join(dirname(statePath), `.${basename(statePath)}.${randomUUID()}.tmp`);
  await writeFile(temporary, JSON.stringify(data, null, 2) + "\n", "utf8");
  await rename(temporary, statePath);
}

/**
 * The mock memory tool itself.  It writes exactly `candidate` as the new state;
 * it performs no verification of its own — the guard hook is the only gate.
 */
export const MEMORY_WRITE_TOOL = {
  name: "memory_write",
  description:
    "Replace the user's memory records with a proposed candidate state. " +
    "The MemBud verify guard intercepts this tool before it executes.",
  args: MEMORY_WRITE_ARGS,
  async execute(args, context = {}) {
    const statePath = context.memoryStatePath;
    if (typeof statePath !== "string" || !statePath) {
      throw new Error("mock memory_write requires a memoryStatePath in its tool context");
    }
    if (!args || typeof args !== "object" || !Array.isArray(args.candidate)) {
      throw new Error("memory_write requires a candidate record array");
    }
    const previous = await readMemoryState(statePath);
    await writeMemoryState(statePath, {
      schema_version: 2,
      version: Number.isInteger(previous.version) ? previous.version + 1 : 1,
      records: args.candidate,
    });
    return {
      title: `memory_write (${args.candidate.length} record(s))`,
      output: `memory_write applied ${args.candidate.length} record(s)`,
    };
  },
};
