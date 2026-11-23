#!/usr/bin/env python3
"""Live HaluMem run of open-source Mem0, with and without MemBud, following the official eval_memzero procedure.

The official script (MemTensor/HaluMem ``eval/eval_memzero.py``) drives the *hosted* Mem0 platform
(``MemoryClient``), which MemBud cannot stage. This runner follows the same per-user procedure on the
open-source ``mem0.Memory`` (2.2.1, pinned) and writes the official results layout
(``results/memzero-<version>/memzero_eval_results.jsonl``), so the official ``evaluation.py`` scores it
unchanged (``scripts/run_halumem_official_eval.py``). Nothing is copied from the HaluMem repository
(CC BY-NC-ND 4.0): ``custom_instructions`` and ``TEMPLATE_MEM0`` are read from ``eval_memzero.py`` and
``PROMPT_MEMZERO`` from ``prompts.py`` in a local clone at run time; answers use the official
``llms.llm_request``.

Per user, as in the official script: a fresh store; each session's dialogue is added with the session
start time (as ``metadata["created_at"]``: OSS Mem0 rejects the hosted-only ``timestamp`` argument); the memories written in that session are recorded as ``extracted_memories``;
each update point with original memories is searched (top 10) and the results recorded as
``memories_from_system``; each question is answered from a top-20 search with ``PROMPT_MEMZERO``.

Arms (``--arm``):
  pure         plain ``Memory.add``; no MemBud.
  membud-hold  ``StagedMem0`` with the claim-judge grounding policy (``SYSTEM_ACCEPTED``, DeepSeek): a session
               whose proposal contains any unsupported claim is HELD and writes nothing (shipped semantics).
  membud-drop  as membud-hold, but on HOLD the judge-supported ADD claims are committed through the same guard
               with ``infer=False`` and the judge's evidence; only the unsupported claims are dropped.

Documented deviations from the official run: open-source Mem0 instead of the hosted platform; Mem0's LLM is
the configured extractor model (DeepSeek or Llama-3.1-8B via OpenRouter); local embedder fastembed
``BAAI/bge-small-en-v1.5``; answer model is DeepSeek, not the official default; only the first
``--sessions-per-user`` sessions per user; users processed sequentially.
Modes: --estimate (offline) and --run (needs --allow-paid and --max-spend-usd). A user is written only when
all of its sessions completed; an interrupted user is redone from scratch on resume.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

os.environ.setdefault("MEM0_TELEMETRY", "False")
ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "src", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import halumem_official_shim as shim  # noqa: E402

ARMS = ("pure", "membud-hold", "membud-drop")
EMBEDDER = {"provider": "fastembed", "config": {"model": "BAAI/bge-small-en-v1.5", "embedding_dims": 384}}
DATE_FORMAT = "%b %d, %Y, %H:%M:%S"  # HaluMem session start_time format
UPDATE_TOP_K, QA_TOP_K = 10, 20  # as in the official script


def extract_user_name(persona_info: str) -> str:
    m = re.search(r"Name:\s*(.*?); Gender:", persona_info)
    if not m:
        raise ValueError("no name in persona_info")
    return m.group(1).strip()


def session_timestamp(start_time: str) -> int:
    return int(datetime.strptime(start_time, DATE_FORMAT).replace(tzinfo=timezone.utc).timestamp())


class MemorySystem(Protocol):
    def reset(self, user_id: str) -> None: ...
    def add(self, messages: list[dict[str, str]], user_id: str, timestamp: int, case_id: str) -> list[str]: ...
    def search(self, query: str, user_id: str, top_k: int) -> list[dict[str, Any]]: ...


def format_context(template: str, user_name: str, results: Sequence[dict[str, Any]]) -> tuple[str, list[str]]:
    memories = [f"{r.get('created_at')}: {r.get('memory')}" for r in results]
    return template.format(user_id=user_name, memories=json.dumps(memories, indent=4)), memories


def process_user(user: dict, system: MemorySystem, answer: Callable[[str], str], *, template: str, prompt: str,
                 sessions_cap: int) -> dict:
    """One user, official procedure and output layout. ``answer`` is the official ``llms.llm_request``."""
    user_name = extract_user_name(user["persona_info"])
    system.reset(user_name)
    out = {"uuid": user["uuid"], "user_name": user_name, "sessions": []}
    for s_idx, session in enumerate(user["sessions"][:sessions_cap], 1):
        new = {"memory_points": copy.deepcopy(session["memory_points"]), "dialogue": session["dialogue"]}
        messages = [{"role": t["role"], "content": t["content"]} for t in session["dialogue"]]
        t0 = time.time()
        extracted = system.add(messages, user_name, session_timestamp(session["start_time"]), f"{user['uuid']}__s{s_idx}")
        duration = (time.time() - t0) * 1000
        if session.get("is_generated_qa_session", False):
            out["sessions"].append({"add_dialogue_duration_ms": duration, "is_generated_qa_session": True})
            continue
        new["extracted_memories"] = list(extracted)
        new["add_dialogue_duration_ms"] = duration
        for memory in new["memory_points"]:
            if memory["is_update"] == "False" or not memory["original_memories"]:
                continue
            _, found = format_context(template, user_name, system.search(memory["memory_content"], user_name, UPDATE_TOP_K))
            memory["memories_from_system"] = found
        if "questions" not in session:
            out["sessions"].append(new)
            continue
        new["questions"] = []
        for qa in session["questions"]:
            t1 = time.time()
            context, _ = format_context(template, user_name, system.search(qa["question"], user_name, QA_TOP_K))
            new_qa = copy.deepcopy(qa)
            new_qa["context"] = context
            new_qa["search_duration_ms"] = (time.time() - t1) * 1000
            t2 = time.time()
            new_qa["system_response"] = answer(prompt.format(context=context, question=qa["question"]))
            new_qa["response_duration_ms"] = (time.time() - t2) * 1000
            new["questions"].append(new_qa)
        out["sessions"].append(new)
    return out


class Mem0System:
    """Open-source Mem0 on a fresh on-disk store per user; optionally guarded by MemBud's StagedMem0."""

    def __init__(self, root: Path, arm: str, llm: Any, custom_instructions: str, grounding: Any = None,
                 embedder: dict = EMBEDDER, embedding_model: Any = None) -> None:
        if arm not in ARMS:
            raise ValueError(arm)
        if arm != "pure" and grounding is None:
            raise ValueError("guarded arms need a grounding policy")
        self.root, self.arm, self.llm, self.instructions, self.grounding = Path(root), arm, llm, custom_instructions, grounding
        self.embedder, self.embedding_model = embedder, embedding_model  # embedding_model: test override
        self.memory = self.stager = None
        self.log: list[dict[str, Any]] = []

    @staticmethod
    def store_id(user_id: str) -> str:
        """OSS Mem0 rejects whitespace in user_id (the hosted platform accepts names); the prompt keeps the name."""
        return re.sub(r"\s+", "_", user_id.strip())

    def _close(self) -> None:
        if self.memory is not None:
            for closer in (self.memory.vector_store.client.close, self.memory.db.close):
                try:
                    closer()
                except Exception:
                    pass
        self.memory = self.stager = None

    def reset(self, user_id: str) -> None:
        from mem0 import Memory

        self._close()
        d = self.root / hashlib.sha256(user_id.encode()).hexdigest()[:16]
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
        cfg = {
            "vector_store": {"provider": "qdrant", "config": {"path": str(d / "qdrant"), "collection_name": "halumem_live",
                                                               "embedding_model_dims": self.embedder["config"]["embedding_dims"],
                                                               "on_disk": True}},
            "llm": {"provider": "openai", "config": {"api_key": "managed-by-membud-client"}},
            "embedder": self.embedder,
            "history_db_path": str(d / "history.db"),
            "custom_instructions": self.instructions,
        }
        self.memory = Memory.from_config(cfg)
        self.memory.llm = self.llm
        if self.embedding_model is not None:
            self.memory.embedding_model = self.embedding_model
        if self.arm != "pure":
            from membud.adapters.mem0_staged import StagedMem0

            self.stager = StagedMem0(self.memory, workdir=d / "staged", grounding=self.grounding)

    def _live(self, user_id: str) -> list[str]:
        res = self.memory.get_all(filters={"user_id": self.store_id(user_id)})
        return [r["memory"] for r in (res.get("results", []) if isinstance(res, dict) else res)]

    def add(self, messages, user_id, timestamp, case_id):
        # OSS Mem0 2.2.1 rejects ``timestamp`` (hosted-only); it keeps a ``created_at`` given in metadata, which
        # is what the official context shows next to each memory, so the session time is passed that way.
        meta = {"created_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat()}
        uid = self.store_id(user_id)
        if self.arm == "pure":
            res = self.memory.add(messages, user_id=uid, metadata=meta)
            return [r["memory"] for r in res.get("results", []) if r.get("memory")]
        self.grounding.set_context(messages, self._live(user_id))
        out = self.stager.add(messages, user_id=uid, metadata=meta, case_id=case_id)
        entry = {"case_id": case_id, "decision": out.decision, "applied": out.applied, "reason": out.reason,
                 "verdicts": dict(self.grounding.last_verdicts)}
        written: list[str] = []
        if out.applied:
            written = [p["memory"] for p in out.proposals if p.get("event") in ("ADD", "UPDATE") and p.get("memory")]
        elif self.arm == "membud-drop" and out.decision == "HOLD":
            adds = [p["memory"] for p in out.proposals if p.get("event") == "ADD" and p.get("memory")]
            kept = [c for c in adds if self.grounding.last_verdicts.get(c)]
            entry["dropped"] = [c for c in adds if c not in kept]
            entry["skipped_non_add"] = sum(p.get("event") not in ("ADD", "NONE", None) for p in out.proposals)
            if kept:
                ev = [{"id": f"kept-{i}", "supports": [c], "source": f"claim-judge:{case_id}", "text": c} for i, c in enumerate(kept)]
                out2 = self.stager.add([{"role": "user", "content": c} for c in kept], user_id=uid, infer=False,
                                       evidence=ev, metadata=meta, case_id=f"{case_id}#claim-drop")
                entry.update(drop_decision=out2.decision, drop_applied=out2.applied, drop_reason=out2.reason)
                if out2.applied:
                    written = [p["memory"] for p in out2.proposals if p.get("event") == "ADD" and p.get("memory")]
        entry["written"] = len(written)
        self.log.append(entry)
        return written

    def search(self, query, user_id, top_k):
        res = self.memory.search(query, top_k=top_k, filters={"user_id": self.store_id(user_id)})
        return list(res.get("results", []) if isinstance(res, dict) else res)


class RetryingLLM:
    """Wraps a Mem0 LLM adapter: retries transient failures (free endpoints rate-limit upstream) with backoff."""

    def __init__(self, inner: Any, attempts: int = 6, wait_s: float = 20.0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.inner, self.attempts, self.wait_s, self.sleep = inner, attempts, wait_s, sleep
        self.retries = 0

    def __getattr__(self, name: str) -> Any:  # call_records, failures, reset_step_telemetry, ...
        return getattr(self.inner, name)

    def generate_response(self, messages, response_format=None, **kwargs):
        for attempt in range(1, self.attempts + 1):
            try:
                return self.inner.generate_response(messages, response_format=response_format, **kwargs)
            except Exception:
                if attempt == self.attempts:
                    raise
                self.retries += 1
                self.sleep(min(self.wait_s * attempt, 120.0))


def load_users(path: Path, n_users: int) -> list[dict]:
    users = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            users.append(json.loads(line))
        if len(users) >= n_users:
            break
    return users


def estimate(users: Sequence[dict], cap: int, arm: str) -> dict[str, Any]:
    """Peak-price token estimate (chars/4). Mem0 extraction ~2 calls per session; judge 1 per guarded session."""
    sess = [s for u in users for s in u["sessions"][:cap]]
    dlg = sum(sum(len(t["content"]) for t in s["dialogue"]) // 4 for s in sess)
    qs = sum(len(s.get("questions") or []) for s in sess)
    mem0_in, mem0_out = 2 * dlg + 3000 * len(sess), 800 * len(sess)
    judge_in, judge_out = (dlg + 1500 * len(sess), 400 * len(sess)) if arm != "pure" else (0, 0)
    ans_in, ans_out = 2500 * qs, 300 * qs
    tin, tout = mem0_in + judge_in + ans_in, mem0_out + judge_out + ans_out
    return {"arm": arm, "users": len(users), "sessions": len(sess), "questions": qs,
            "update_searches": sum(1 for s in sess for m in s["memory_points"] if m["is_update"] != "False" and m["original_memories"]),
            "est_input_tokens": tin, "est_output_tokens": tout,
            "est_cost_usd_peak_if_all_deepseek": round(tin * 3e-7 + tout * 1.2e-6, 3)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--halumem-path", default=str(Path.home() / "Repositories/HaluMem/HaluMem-Medium.jsonl"))
    ap.add_argument("--reference-repo", default=str(shim.DEFAULT_REPO))
    ap.add_argument("--arm", choices=ARMS, required=True)
    ap.add_argument("--extractor", choices=("deepseek", "openrouter"), default="deepseek")
    ap.add_argument("--extractor-model", default="meta-llama/llama-3.1-8b-instruct")
    ap.add_argument("--confirm-data-policy", action="store_true", help="required for --extractor openrouter")
    ap.add_argument("--users", type=int, default=20)
    ap.add_argument("--sessions-per-user", type=int, default=5)
    ap.add_argument("--version", default=None, help="results/memzero-<version>; default <extractor>-<arm>-s<N>")
    ap.add_argument("--out-root", default=str(ROOT / "data" / "halumem-live"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--run", action="store_true")
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--max-spend-usd", type=float)
    a = ap.parse_args()

    users = load_users(Path(a.halumem_path), a.users)
    if a.estimate:
        print(json.dumps(estimate(users, a.sessions_per_user, a.arm), indent=2))
        return 0
    if not a.allow_paid or a.max_spend_usd is None:
        raise SystemExit("--run needs --allow-paid and --max-spend-usd")
    if a.extractor == "openrouter" and not a.confirm_data_policy:
        raise SystemExit("--confirm-data-policy required: HaluMem dialogue is sent to OpenRouter")

    from membud.llm import OpenAICompatibleChatClient, enforce_deepseek_price, load_dotenv_0600
    from membud.adapters.claim_judge_grounding import ClaimJudgeGrounding

    load_dotenv_0600(ROOT / ".env", require_0600=True, populate_environ=True)
    judge_client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
    ds = enforce_deepseek_price(str(judge_client.model), allow_paid=True)
    ds_in, ds_out = ds["prompt_per_mtok"] / 1e6, ds["completion_per_mtok"] / 1e6
    meter = shim.Meter(ds_in, ds_out, a.max_spend_usd)  # official answer calls; other usage added as extra_usd
    official = shim.load_official(Path(a.reference_repo), base_url=str(judge_client.base_url), api_key=os.environ["DEEPSEEK_API_KEY"],
                                  model=str(judge_client.model), meter=meter,
                                  extra_body=dict(judge_client.extra_body) or None)
    consts = shim.read_constants(Path(a.reference_repo) / "eval" / "eval_memzero.py", ("custom_instructions", "TEMPLATE_MEM0"))
    prompt = official["prompts"].PROMPT_MEMZERO

    if a.extractor == "deepseek":
        ext_client, ext_in, ext_out = judge_client, ds_in, ds_out
    else:
        from membud.llm import fetch_openrouter_models, resolve_openrouter_model_pricing

        os.environ["OPENROUTER_MODEL"] = a.extractor_model
        pr = resolve_openrouter_model_pricing(a.extractor_model, fetch_openrouter_models())
        ext_in, ext_out = pr["prompt_price_per_token"], pr["completion_price_per_token"]
        ext_client = OpenAICompatibleChatClient.from_openrouter_env()
    spec = importlib.util.spec_from_file_location("run_halumem_mem0", ROOT / "scripts" / "run_halumem_mem0.py")
    rhm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rhm)
    llm = RetryingLLM(rhm.ClientBackedMem0LLM(ext_client)) if a.extractor == "openrouter" else rhm.ClientBackedMem0LLM(ext_client)

    def judge(messages):
        meter.check()
        res = judge_client.complete_json(messages, validator=lambda j: None if isinstance(j.get("claims"), list)
                                         else (_ for _ in ()).throw(ValueError("no claims list")))
        meter.extra_usd += (res.input_tokens or 0) * ds_in + (res.output_tokens or 0) * ds_out
        return res

    grounding = None
    if a.arm != "pure":
        spec2 = importlib.util.spec_from_file_location("llm_phase2", ROOT / "scripts" / "halumem_llm_phase2_eval.py")
        lp = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(lp)
        grounding = ClaimJudgeGrounding(judge=judge, system_prompt=lp.SYSTEM_ACCEPTED)

    version = a.version or f"{a.extractor}-{a.arm}-s{a.sessions_per_user}"
    base = Path(a.out_root) / "results" / f"memzero-{version}"
    tmp = base / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    system = Mem0System(Path(a.out_root) / "stores" / version, a.arm, llm, consts["custom_instructions"], grounding)
    (base / "run-config.json").write_text(json.dumps({
        "arm": a.arm, "extractor": a.extractor, "extractor_model": a.extractor_model if a.extractor != "deepseek" else str(judge_client.model),
        "judge_and_answer_model": str(judge_client.model), "embedder": EMBEDDER, "users": a.users,
        "sessions_per_user": a.sessions_per_user, "mem0": "mem0ai==2.2.1 (open source)", "reference_repo": a.reference_repo,
    }, indent=2) + "\n")

    def mem0_cost() -> float:
        recs = llm.call_records
        c = sum((r.get("input_tokens") or 0) * ext_in + (r.get("output_tokens") or 0) * ext_out for r in recs)
        llm.reset_step_telemetry()
        return c

    stopped, failed = "", []
    for user in users:
        if (tmp / f"{user['uuid']}.json").exists():
            continue
        try:
            meter.check()
            system.log = []
            result = process_user(user, system, official["llms"].llm_request, template=consts["TEMPLATE_MEM0"],
                                  prompt=prompt, sessions_cap=a.sessions_per_user)
            meter.extra_usd += mem0_cost()
        except shim.BudgetExceeded as exc:
            meter.extra_usd += mem0_cost()
            stopped = str(exc)
            break
        except Exception as exc:  # e.g. persistent upstream errors: skip this user; it is redone on resume
            meter.extra_usd += mem0_cost()
            failed.append({"uuid": user["uuid"], "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            print(f"user {user['uuid'][:8]} FAILED ({type(exc).__name__}); will be redone on resume", file=sys.stderr)
            continue
        (tmp / f"{user['uuid']}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        with (base / "guard-log.jsonl").open("a", encoding="utf-8") as fh:
            for e in system.log:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"user {user['uuid'][:8]} done; estimated spend ${meter.spent:.4f}", file=sys.stderr)
    system._close()
    done = sorted(tmp.glob("*.json"))
    with (base / "memzero_eval_results.jsonl").open("w", encoding="utf-8") as fh:
        for f in done:
            fh.write(f.read_text(encoding="utf-8") + "\n")
    print(json.dumps({"version": version, "users_done": len(done), "of": len(users), "stopped": stopped, "failed_users": failed, **meter.as_dict()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
