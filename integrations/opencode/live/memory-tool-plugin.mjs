/**
 * Live-session mock memory tool for the MemBud × opencode denial test.
 *
 * This is NOT part of the shipped Phase 0 guard plugin. It exists only so a
 * real opencode session (real model) has a `memory_write` tool to call; the
 * MemBud guard plugin's `tool.execute.before` hook must deny calls that the
 * sidecar HOLDs, so `execute` must never run in those cases.
 *
 * Configuration is via environment variables so no secret or absolute path is
 * baked in:
 *   MEMBUD_LIVE_STATE   - path to the JSON memory state file (required)
 *   MEMBUD_LIVE_CAPTURE - path to a JSONL capture file written on execution
 *
 * It imports `@opencode-ai/plugin`; `scripts/run_live_opencode_denial.py`
 * installs a node_modules symlink next to a copy of this file so no package
 * install is needed. Running it requires an explicit paid-call confirmation.
 */
import { appendFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { basename, dirname, join } from "node:path";

import { tool } from "@opencode-ai/plugin";

const RECORD = tool.schema.object({
  id: tool.schema.string(),
  claims: tool.schema.array(tool.schema.string()),
});

const EVIDENCE = tool.schema.object({
  id: tool.schema.string(),
  supports: tool.schema.array(tool.schema.string()),
  source: tool.schema.string().optional(),
  text: tool.schema.string().optional(),
  contradicts: tool.schema.array(tool.schema.string()).optional(),
});

function statePath() {
  const value = process.env.MEMBUD_LIVE_STATE;
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("MEMBUD_LIVE_STATE must point at the JSON memory state file");
  }
  return value;
}

function capturePath() {
  const value = process.env.MEMBUD_LIVE_CAPTURE;
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("MEMBUD_LIVE_CAPTURE must point at a JSONL capture file");
  }
  return value;
}

async function readState() {
  try {
    return JSON.parse(await readFile(statePath(), "utf8"));
  } catch (error) {
    if (error && error.code === "ENOENT") return { version: 0, records: [] };
    throw error;
  }
}

async function writeState(data) {
  const target = statePath();
  await mkdir(dirname(target), { recursive: true });
  const temporary = join(dirname(target), `.${basename(target)}.${randomUUID()}.tmp`);
  await writeFile(temporary, JSON.stringify(data, null, 2) + "\n", "utf8");
  await rename(temporary, target);
}

export const memoryWriteTool = tool({
  description:
    "Replace the user's memory records with a proposed candidate state. " +
    "A MemBud verification guard intercepts this tool before it executes.",
  args: {
    case_id: tool.schema.string().optional(),
    candidate: tool.schema.array(RECORD),
    evidence: tool.schema.array(EVIDENCE).optional(),
    required_claims: tool.schema.array(tool.schema.string()).optional(),
    operations: tool.schema.array(tool.schema.any()).optional(),
  },
  async execute(args) {
    // This line proves the tool actually executed. Any guard denial means the
    // capture file must not contain a record for that call.
    await appendFile(
      capturePath(),
      JSON.stringify({ stage: "executed", at: new Date().toISOString(), args }) + "\n",
      "utf8",
    );
    const previous = await readState();
    await writeState({
      schema_version: 2,
      version: Number.isInteger(previous.version) ? previous.version + 1 : 1,
      records: args.candidate,
    });
    return {
      title: `memory_write (${args.candidate.length} record(s))`,
      output: `memory_write applied ${args.candidate.length} record(s)`,
    };
  },
});

/** opencode 1.19.5 plugin loader shape: default export object with server(). */
export default {
  id: "membud-live-memory-tool",
  server: async () => ({ tool: { memory_write: memoryWriteTool } }),
};
