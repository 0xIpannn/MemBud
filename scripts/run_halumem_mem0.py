#!/usr/bin/env python3
"""Phase 2 natural-error evaluation from a real Mem0 2.2.1 run via StagedMem0.

Purpose: measure memory-transition errors that unmodified ``mem0ai==2.2.1``
actually makes on multi-session HaluMem dialogues, rather than injected faults.

Safety & fairness controls:
1. **CC BY-NC-ND data-policy confirmation gate**: prints a warning that free
   OpenRouter endpoints may log or train on prompts while HaluMem is licensed
   under CC BY-NC-ND 4.0, and refuses to send any HaluMem dialogue until explicit
   confirmation is provided.
2. **Zero-price guard**: queries OpenRouter's public ``/api/v1/models`` catalog,
   resolves the exact model ID, and refuses to start if prompt, completion, or
   request pricing is non-zero unless ``--allow-paid`` is explicitly passed.
3. **Rate-limit resilience**: concurrency 1, bounded exponential backoff, hard
   caps on total requests (``--max-requests``) and total tokens
   (``--max-total-tokens``), and crash-tolerant checkpoint/resume
   (:class:`membud.checkpoint.RunCheckpoint`).
4. **Propose once, judge many**: runs each sampled session once through
   :class:`membud.adapters.mem0_staged.StagedMem0`, records the derived
   schema-v2 :class:`Transition` and raw Mem0 output to a gitignored directory,
   and evaluates ``unguarded``, ``graph``, ``text``, and ``text_fair`` offline
   on those identical transitions with zero extra model calls.
5. **Provisional silver labels & blind human audit**: labels natural proposals
   against HaluMem gold ``memory_points`` (deterministic rules first, optional
   cached free-model LLM claim matcher), reports paraphrase grounding false
   holds, generates a blind HTML quiz via ``scripts/review_quiz.py``, and keeps
   results marked unvalidated until human review answers are graded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import random
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# Disable Mem0 telemetry before any mem0 import (unstageable outward network side effect).
os.environ["MEM0_TELEMETRY"] = "False"
os.environ.setdefault("MEM0_DIR", tempfile.mkdtemp(prefix="membud-phase2-mem0-dir-"))
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.checkpoint import CHECKPOINT_FILENAME, RunCheckpoint  # noqa: E402
from membud.conversation import verify_with_transcript  # noqa: E402
from membud.cost import (  # noqa: E402
    TokenCounts,
    build_price_tables,
    estimate_cost,
    extract_token_counts,
    sum_token_counts,
)
from membud.experiment import source_fingerprint  # noqa: E402
from membud.fairness import verify_text_fair  # noqa: E402
from membud.halumem import (  # noqa: E402
    FAULT_TYPES,
    SILVER_CATEGORIES,
    SILVER_LABEL_LIMITATION,
    SYNTHETIC_FIXTURE_REVISION,
    HaluMemSession,
    HaluMemUser,
    LLMClaimMatchJudge,
    SilverLabelResult,
    build_phase2_blind_quiz,
    derive_silver_label,
    load_halumem_dataset,
    select_stratified_audit_sample,
    write_synthetic_halumem_fixture,
)
from membud.llm.client import (  # noqa: E402
    LLMJSONError,
    LLMRequestError,
    OpenAICompatibleChatClient,
    enforce_deepseek_price,
    enforce_openrouter_zero_price,
    load_dotenv_0600,
)
from membud.paired_stats import mcnemar_exact, paired_bootstrap, wilson  # noqa: E402
from membud.schemas import (  # noqa: E402
    SCHEMA_VERSION,
    Evidence,
    Operation,
    Record,
    Transition,
)
from membud.serialization import transition_dict  # noqa: E402
from membud.verification import verify  # noqa: E402

CONDITIONS: tuple[str, ...] = ("unguarded", "graph", "text", "text_fair")
DEFAULT_SEED = 20261121
DEFAULT_NUM_USERS = 20
DEFAULT_SESSIONS_PER_USER = 2
DEFAULT_MAX_REQUESTS = 120
DEFAULT_MAX_TOTAL_TOKENS = 200_000
DEFAULT_AUDIT_TARGET = 100
DEFAULT_RESAMPLES = 4000
DEFAULT_OUTPUT = ROOT / "halumem-output" / "phase2-mem0"
CHECKPOINT_IDENTITY_FIELDS: tuple[str, ...] = ("case_id",)

SAMPLING_JUSTIFICATION = (
    "OpenRouter free-tier endpoints enforce strict rate limits (typically 20 RPM "
    "and 50 RPD on accounts without a credit balance, plus upstream provider throttling). "
    "Sampling 20 users x first 2 sessions each = 40 sessions requires 40 Mem0 "
    "extraction calls at concurrency 1, fitting comfortably inside the 50 RPD "
    "single-day free-tier budget while providing paired multi-session transitions "
    "(Session 1 initial extraction + Session 2 stateful update/retention)."
)

OPENROUTER_HALUMEM_DATA_POLICY_WARNING = """\
================================================================================
DATA POLICY & LICENSE WARNING (HaluMem CC BY-NC-ND 4.0 / OpenRouter Free Tier)
================================================================================
1. HaluMem is distributed under the CC BY-NC-ND 4.0 license (non-commercial,
   no distribution of derivative works).
2. Free OpenRouter endpoints (:free models and zero-cost providers) may log
   prompts/completions or use them for model training/evaluation depending on
   the upstream provider and your OpenRouter Privacy & Data Policy settings
   (https://openrouter.ai/settings/privacy).
3. Before sending any HaluMem dialogue to OpenRouter, confirm that your
   OpenRouter data-policy settings and intended research use are acceptable.
================================================================================\
"""


DEEPSEEK_HALUMEM_DATA_POLICY_WARNING = """\
================================================================================
DATA POLICY & LICENSE WARNING (HaluMem CC BY-NC-ND 4.0 / DeepSeek API, PAID)
================================================================================
1. HaluMem is distributed under the CC BY-NC-ND 4.0 license (non-commercial,
   no distribution of derivative works).
2. HaluMem dialogue will be sent as prompts to DeepSeek's own API
   (api.deepseek.com). DeepSeek is operated by a China-based company and its
   terms may allow it to retain prompts and use them to improve its models.
3. This is a PAID model. Spend is capped by --max-spend-usd (estimated at peak
   list prices; not a provider bill). Confirm the DeepSeek platform usage page.
================================================================================\
"""


def _subseed(seed: int, key: str) -> int:
    digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


# ---------------------------------------------------------------------------
# Local embedders for Mem0 (zero network calls)
# ---------------------------------------------------------------------------


class SpacyLocalEmbedder:
    """Local 96-dim dense embedder backed by the pinned ``en_core_web_sm-3.8.0`` model."""

    name = "spacy:en_core_web_sm"
    dims = 96

    def __init__(self, model_name: str = "en_core_web_sm") -> None:
        import spacy

        self.model_name = model_name
        self._nlp = spacy.load(model_name, disable=["parser", "ner", "lemmatizer"])
        self.version = importlib_metadata.version("en-core-web-sm")
        self.spacy_version = importlib_metadata.version("spacy")

    def embed(self, text: str, memory_action: str | None = None) -> list[float]:
        cleaned = " ".join(str(text).split()) or "empty"
        doc = self._nlp(cleaned)
        vec = doc.vector
        if vec is None or len(vec) == 0:
            return [0.01] * self.dims
        out = [float(x) for x in vec]
        if all(x == 0.0 for x in out):
            out[0] = 0.01
        return out

    def embed_batch(
        self, texts: Sequence[str], memory_action: str | None = None
    ) -> list[list[float]]:
        return [self.embed(t, memory_action=memory_action) for t in texts]


def build_local_embedder(kind: str = "spacy") -> tuple[Any, dict[str, Any]]:
    """Construct the local embedder and return ``(embedder, metadata_dict)``."""
    selected = kind.strip().lower()
    if selected == "spacy":
        emb = SpacyLocalEmbedder("en_core_web_sm")
        return emb, {
            "kind": "spacy",
            "model": "en_core_web_sm",
            "version": emb.version,
            "spacy_version": emb.spacy_version,
            "dims": emb.dims,
            "network_calls": 0,
        }
    if selected == "hash":
        from membud.adapters.mem0_offline import DIMS, HashEmbedder

        return HashEmbedder(), {
            "kind": "hash",
            "model": "sha256-hash-embedder",
            "version": "1.0",
            "dims": DIMS,
            "network_calls": 0,
        }
    if selected == "fastembed":
        from mem0.configs.embeddings.base import BaseEmbedderConfig
        from mem0.embeddings.fastembed import FastEmbedEmbedding

        fe_version = importlib_metadata.version("fastembed")
        cfg = BaseEmbedderConfig(model="BAAI/bge-small-en-v1.5", embedding_dims=384)
        fe = FastEmbedEmbedding(cfg)
        return fe, {
            "kind": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
            "version": fe_version,
            "dims": int(fe.config.embedding_dims or 384),
        }
    raise ValueError(f"unsupported local embedder {kind!r}; choose 'spacy', 'fastembed', or 'hash'")


# ---------------------------------------------------------------------------
# Client-backed Mem0 LLM adapter (concurrency 1, cached, bounded retry, telemetry)
# ---------------------------------------------------------------------------


class ClientBackedMem0LLM:
    """Adapter exposing ``generate_response`` for ``mem0.Memory`` over ``OpenAICompatibleChatClient``."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.call_records: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []

    def reset_step_telemetry(self) -> None:
        self.call_records.clear()
        self.failures.clear()

    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: Any = None,
        **kwargs: Any,
    ) -> str:
        try:
            completion = self.client.complete_json(messages)
        except (LLMRequestError, LLMJSONError) as exc:
            self.failures.append(
                {
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "usage": dict(exc.usage),
                }
            )
            raise
        self.call_records.append(
            {
                "model": completion.model,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "total_tokens": completion.total_tokens,
                "request_attempts": completion.request_attempts,
                "json_retries": completion.json_retries,
                "cached": completion.cached,
                "latency_ms": completion.latency_ms,
                "request_id": completion.request_id,
            }
        )
        return completion.content


# ---------------------------------------------------------------------------
# Sampling & data-policy confirmation
# ---------------------------------------------------------------------------


def sample_halumem_sessions(
    users: Sequence[HaluMemUser],
    *,
    num_users: int = DEFAULT_NUM_USERS,
    sessions_per_user: int = DEFAULT_SESSIONS_PER_USER,
    seed: int = DEFAULT_SEED,
) -> list[tuple[HaluMemUser, HaluMemSession, int]]:
    """Select a deterministic, seeded subset of users and their first N sessions."""
    if num_users <= 0 or sessions_per_user <= 0:
        raise ValueError("num_users and sessions_per_user must be positive")
    pool = list(users)
    if len(pool) > num_users:
        rng = random.Random(_subseed(seed, "phase2_user_sample"))
        indices = sorted(rng.sample(range(len(pool)), num_users))
        selected_users = [pool[i] for i in indices]
    else:
        selected_users = pool

    sampled: list[tuple[HaluMemUser, HaluMemSession, int]] = []
    for user in selected_users:
        for s_idx, session in enumerate(user.sessions[:sessions_per_user], start=1):
            sampled.append((user, session, s_idx))
    return sampled


def require_data_policy_confirmation(
    *,
    confirmed_flag: bool,
    is_synthetic_fixture: bool,
    network_enabled: bool,
    input_fn: Callable[[str], str] = input,
    provider: str = "openrouter",
) -> bool:
    """Print the CC BY-NC-ND / provider warning and require confirmation before network calls."""
    if not network_enabled:
        return True
    print(
        DEEPSEEK_HALUMEM_DATA_POLICY_WARNING
        if provider == "deepseek"
        else OPENROUTER_HALUMEM_DATA_POLICY_WARNING,
        file=sys.stderr,
    )
    if confirmed_flag:
        return True
    if sys.stdin is not None and sys.stdin.isatty():
        answer = input_fn(
            "Type 'YES' to confirm your OpenRouter data-policy settings are acceptable for HaluMem: "
        ).strip()
        if answer == "YES":
            return True
    raise PermissionError(
        f"Refusing to send HaluMem dialogue to {provider} without explicit data-policy "
        "confirmation. Verify the provider's privacy settings and pass "
        "--confirm-data-policy."
    )


# ---------------------------------------------------------------------------
# Fallback transition construction when StagedMem0 returns REJECTED / error
# ---------------------------------------------------------------------------


def _fallback_transition_from_outcome(
    case_id: str,
    before_records: Sequence[Record],
    proposals: Sequence[Mapping[str, Any]],
    evidence_items: Sequence[Mapping[str, Any]],
) -> Transition:
    """Reconstruct a canonical Transition if StagedMem0 returned no GuardedDecision."""
    before_tuple = tuple(before_records)
    cand_map = {r.id: r for r in before_tuple}
    ops: list[Operation] = []
    for idx, prop in enumerate(proposals):
        ev = str(prop.get("event", "NONE")).upper()
        mem = str(prop.get("memory") or "").strip()
        rid = str(prop.get("id") or f"mem0-prop-{idx}")
        if ev == "ADD" and mem and rid not in cand_map:
            cand_map[rid] = Record(id=rid, claims=(mem,))
            ops.append(Operation(type="WRITE", record_id=rid))
        elif ev == "UPDATE" and mem and rid in cand_map:
            if cand_map[rid].claims != (mem,):
                cand_map[rid] = Record(id=rid, claims=(mem,))
                ops.append(Operation(type="REVISE", record_id=rid, target_id=rid))
    if not ops:
        ops.append(Operation(type="NOOP"))
    ev_objs = tuple(
        Evidence.from_dict(dict(item)) for item in evidence_items
    )
    t = Transition(
        case_id=case_id,
        before=before_tuple,
        candidate=tuple(cand_map.values()),
        evidence=ev_objs,
        required_claims=(),
        operations=tuple(ops),
        schema_version=SCHEMA_VERSION,
    )
    t.validate()
    return t


# ---------------------------------------------------------------------------
# Offline multi-condition evaluation ("judge many" on recorded transitions)
# ---------------------------------------------------------------------------


def evaluate_recorded_transition_all_conditions(
    transition: Transition,
    transcript: Any,
) -> dict[str, dict[str, Any]]:
    """Evaluate all four conditions offline on one recorded transition (0 LLM calls)."""
    results: dict[str, dict[str, Any]] = {}
    for cond in CONDITIONS:
        if cond == "unguarded":
            results[cond] = {
                "decision": "ACCEPT",
                "verified": False,
                "fail_reason_codes": [],
                "with_transcript_grounding": {
                    "decision": "ACCEPT",
                    "fail_reason_codes": [],
                },
            }
            continue

        if cond == "graph":
            base_res = verify(transition, "graph")
            tg_res = verify_with_transcript(
                transition, transcript, "graph", fair_text=False
            )
        elif cond == "text":
            base_res = verify(transition, "text")
            tg_res = verify_with_transcript(
                transition, transcript, "text", fair_text=False
            )
        elif cond == "text_fair":
            base_res = verify_text_fair(transition)
            tg_res = verify_with_transcript(
                transition, transcript, "text", fair_text=True
            )
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown condition {cond!r}")

        results[cond] = {
            "decision": base_res.decision,
            "verified": True,
            "fail_reason_codes": sorted(
                {c.reason_code for c in base_res.checks if not c.passed and c.reason_code}
            ),
            "with_transcript_grounding": {
                "decision": tg_res.decision,
                "fail_reason_codes": sorted(
                    {c.reason_code for c in tg_res.checks if not c.passed and c.reason_code}
                ),
            },
        }
    return results


# ---------------------------------------------------------------------------
# Main Phase 2 pipeline
# ---------------------------------------------------------------------------


def run_halumem_phase2(
    *,
    halumem_path: str | Path | None = None,
    dataset_revision: str = "",
    output_dir: str | Path = DEFAULT_OUTPUT,
    llm_client: Any | None = None,
    openrouter_models_catalog: Sequence[Mapping[str, Any]] | None = None,
    allow_paid: bool = False,
    confirm_data_policy: bool = False,
    provider: str = "openrouter",
    max_spend_usd: float | None = None,
    silver_omission_scope: str = "prior_only",
    silver_normalize_numerals: bool = False,
    embedder_kind: str = "spacy",
    num_users: int = DEFAULT_NUM_USERS,
    sessions_per_user: int = DEFAULT_SESSIONS_PER_USER,
    seed: int = DEFAULT_SEED,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    enable_llm_claim_judge: bool = False,
    audit_sample_size: int = DEFAULT_AUDIT_TARGET,
    resamples: int = DEFAULT_RESAMPLES,
    resume: bool = False,
    dotenv_path: str | Path = ".env",
    code_revision: str = "",
    proposal_stager: Callable[[HaluMemUser, HaluMemSession, int, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run Phase 2: propose once via StagedMem0, then judge all conditions offline."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    proposals_dir = out / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out / CHECKPOINT_FILENAME

    # 1. Load dataset (or synthetic fixture when halumem_path is omitted).
    temp_fixture_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if halumem_path is None:
            temp_fixture_dir = tempfile.TemporaryDirectory(prefix="membud-phase2-syn-")
            fixture_path = write_synthetic_halumem_fixture(
                Path(temp_fixture_dir.name) / "synthetic-halumem.jsonl"
            )
            resolved_path = fixture_path
            resolved_revision = dataset_revision.strip() or SYNTHETIC_FIXTURE_REVISION
            is_synthetic = True
        else:
            resolved_path = Path(halumem_path)
            resolved_revision = dataset_revision.strip() or "user-local-halumem"
            is_synthetic = False

        meta, users = load_halumem_dataset(
            resolved_path,
            dataset_revision=resolved_revision,
            is_synthetic_fixture=is_synthetic,
        )
    finally:
        if temp_fixture_dir is not None:
            temp_fixture_dir.cleanup()

    # 2. Construct or validate the OpenRouter client & enforce zero-price guard.
    if provider not in {"openrouter", "deepseek"}:
        raise ValueError(f"provider must be 'openrouter' or 'deepseek', got {provider!r}")
    network_enabled = llm_client is None and proposal_stager is None
    if llm_client is None and proposal_stager is None:
        if Path(dotenv_path).is_file():
            load_dotenv_0600(dotenv_path, require_0600=True, populate_environ=True)
        if provider == "deepseek":
            # Thinking mode is on by default at DeepSeek and bills reasoning tokens.
            client = OpenAICompatibleChatClient.from_deepseek_env(thinking=False)
        else:
            client = OpenAICompatibleChatClient.from_openrouter_env()
    else:
        client = llm_client

    model_id = str(getattr(client, "model", "deepseek/deepseek-v4.1-flash"))
    base_url = str(getattr(client, "base_url", OpenAICompatibleChatClient.OPENROUTER_BASE_URL))

    if provider == "deepseek" and network_enabled:
        if max_spend_usd is None or max_spend_usd <= 0:
            raise ValueError("--max-spend-usd (> 0) is required for a paid DeepSeek run")
        pricing_info = enforce_deepseek_price(model_id, allow_paid=allow_paid)
    elif network_enabled or openrouter_models_catalog is not None:
        pricing_info = enforce_openrouter_zero_price(
            model_id,
            models=openrouter_models_catalog,
            base_url=base_url,
            allow_paid=allow_paid,
        )
    else:
        pricing_info = {
            "id": model_id,
            "name": model_id,
            "prompt_price_per_token": 0.0,
            "completion_price_per_token": 0.0,
            "request_price": 0.0,
            "prompt_per_mtok": 0.0,
            "completion_per_mtok": 0.0,
            "is_free": True,
        }

    require_data_policy_confirmation(
        confirmed_flag=confirm_data_policy,
        is_synthetic_fixture=meta.is_synthetic_fixture,
        network_enabled=network_enabled,
        provider=provider,
    )

    # 3. Pre-register sampling rule, embedder, and run config BEFORE running.
    sampled = sample_halumem_sessions(
        users,
        num_users=num_users,
        sessions_per_user=sessions_per_user,
        seed=seed,
    )
    if proposal_stager is not None and embedder_kind == "spacy":
        try:
            embedder, embedder_meta = build_local_embedder(embedder_kind)
        except Exception:
            embedder, embedder_meta = None, {
                "kind": "custom_stager",
                "model": "injected-proposal-stager",
                "version": "1.0",
                "dims": 16,
                "network_calls": 0,
            }
    else:
        embedder, embedder_meta = build_local_embedder(embedder_kind)
    rev = code_revision.strip() or source_fingerprint((Path(__file__),))

    run_config = {
        "evaluation": "halumem-rq1-phase2-mem0-v1",
        "file_sha256": meta.file_sha256,
        "dataset_revision": meta.dataset_revision,
        "is_synthetic_fixture": meta.is_synthetic_fixture,
        "seed": seed,
        "sampling": {
            "rule": "seeded_random_users_first_n_sessions_each",
            "requested_num_users": num_users,
            "actual_num_users": len({u.uuid for u, _, _ in sampled}),
            "sessions_per_user": sessions_per_user,
            "sampled_session_count": len(sampled),
            "sampled_case_ids": [f"{u.uuid}__{s.session_id}" for u, s, _ in sampled],
            "justification": SAMPLING_JUSTIFICATION,
        },
        "model": {
            "provider": getattr(client, "provider_name", "openrouter-openai-compatible"),
            "model_id": model_id,
            "base_url": base_url,
            "pricing": pricing_info,
            "allow_paid": allow_paid,
            "thinking_mode": (
                dict(getattr(client, "extra_body", {}) or {}).get("thinking")
                if provider == "deepseek"
                else None
            ),
        },
        "embedder": embedder_meta,
        "budget_caps": {
            "concurrency": 1,
            "max_requests": max_requests,
            "max_total_tokens": max_total_tokens,
            "max_spend_usd_estimated_peak_rate": max_spend_usd,
        },
        "silver_omission_scope": silver_omission_scope,
        "silver_normalize_numerals": silver_normalize_numerals,
        "conditions": list(CONDITIONS),
        "enable_llm_claim_judge": enable_llm_claim_judge,
        "code_revision": rev,
    }
    (out / "run-config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    checkpoint_config = {
        "evaluation": run_config["evaluation"],
        "file_sha256": meta.file_sha256,
        "dataset_revision": meta.dataset_revision,
        "seed": seed,
        "sampled_case_ids": run_config["sampling"]["sampled_case_ids"],
        "model_id": model_id,
        "embedder_kind": embedder_meta["kind"],
    }
    prompt_price = float(pricing_info.get("prompt_price_per_token") or 0.0)
    completion_price = float(pricing_info.get("completion_price_per_token") or 0.0)
    if resume and checkpoint_path.exists():
        checkpoint = RunCheckpoint.load(
            checkpoint_path, identity_fields=CHECKPOINT_IDENTITY_FIELDS
        )
        checkpoint.validate(checkpoint_config)
    else:
        checkpoint = RunCheckpoint.create(
            checkpoint_path,
            config=checkpoint_config,
            identity_fields=CHECKPOINT_IDENTITY_FIELDS,
        )

    # 4. Initialize real Mem0 2.2.1 + StagedMem0 unless a custom proposal_stager is injected.
    memory: Any = None
    stager: Any = None
    llm_adapter: ClientBackedMem0LLM | None = None
    if proposal_stager is None:
        from mem0 import Memory
        from membud.adapters.mem0_staged import LexicalGrounding, StagedMem0, apply_changes

        host_dir = out / "mem0-host"
        if not resume and host_dir.exists():
            import shutil

            shutil.rmtree(host_dir, ignore_errors=True)
        host_dir.mkdir(parents=True, exist_ok=True)
        mem0_config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": str(host_dir / "qdrant"),
                    "collection_name": "membud_halumem_phase2",
                    "embedding_model_dims": int(embedder_meta["dims"]),
                    "on_disk": True,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "api_key": "managed-by-membud-client",
                    "model": model_id,
                    "openrouter_base_url": base_url,
                    "openai_base_url": base_url,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "api_key": "local-embedder",
                    "embedding_dims": int(embedder_meta["dims"]),
                },
            },
            "history_db_path": str(host_dir / "history.db"),
        }
        memory = Memory.from_config(mem0_config)
        llm_adapter = ClientBackedMem0LLM(client)
        memory.llm = llm_adapter
        memory.embedding_model = embedder
        stager = StagedMem0(memory, workdir=out / "staged-workdir")

    # 5. STAGE 1 ("Propose once"): run Mem0 once per sampled session (or replay from checkpoint).
    totals = checkpoint.totals()
    running_requests = int(totals["attempts"])
    running_tokens = int(totals["accounted_tokens"])
    running_spend = 0.0
    if resume:
        for _prior in checkpoint.rows():
            _u = _prior.get("usage") or {}
            running_spend += (
                int(_u.get("input_tokens") or 0) * prompt_price
                + int(_u.get("output_tokens") or 0) * completion_price
            )
    budget_aborted = False
    abort_reason = ""
    resumed_sessions = 0
    executed_sessions = 0
    proposal_records: list[dict[str, Any]] = []
    retry_events: int = 0
    failure_events: list[dict[str, Any]] = []

    try:
        for user, session, s_idx in sampled:
            case_id = f"{user.uuid}__{session.session_id}"
            proposal_file = proposals_dir / f"{case_id}.json"
            existing_row = checkpoint.get_row((case_id,))
            if existing_row is not None and proposal_file.is_file():
                recorded = json.loads(proposal_file.read_text(encoding="utf-8"))
                proposal_records.append(recorded)
                resumed_sessions += 1
                continue

            if running_requests >= max_requests:
                budget_aborted = True
                abort_reason = f"reached max_requests cap ({running_requests} >= {max_requests})"
                break
            if running_tokens >= max_total_tokens:
                budget_aborted = True
                abort_reason = f"reached max_total_tokens cap ({running_tokens} >= {max_total_tokens})"
                break
            if max_spend_usd is not None and running_spend >= max_spend_usd:
                budget_aborted = True
                abort_reason = (
                    f"reached max_spend_usd cap (estimated ${running_spend:.4f} >= ${max_spend_usd})"
                )
                break

            if proposal_stager is not None:
                proposal_payload = dict(proposal_stager(user, session, s_idx, case_id))
                usage_dict = dict(proposal_payload.get("usage") or {})
                step_tot = int(usage_dict.get("total_tokens") or 0)
                step_attempts = int(usage_dict.get("api_attempts") or 1)
                step_cache_hits = int(usage_dict.get("cache_hit") or 0)
                step_http_retries = int(usage_dict.get("http_retries") or 0)
                step_json_retries = int(usage_dict.get("json_retries") or 0)
                retry_events += step_http_retries + step_json_retries
                running_requests += step_attempts
                running_tokens += step_tot
            else:
                from membud.adapters.mem0_staged import LexicalGrounding, apply_changes

                assert stager is not None and llm_adapter is not None
                messages = [
                    {"role": turn.role, "content": turn.content}
                    for turn in session.dialogue
                ]
                prior_snapshot = stager.adapter.snapshot({"user_id": user.uuid})
                llm_adapter.reset_step_telemetry()

                try:
                    outcome = stager.add(
                        messages,
                        user_id=user.uuid,
                        case_id=case_id,
                    )
                except Exception as exc:
                    failure_events.extend(llm_adapter.failures)
                    step_attempts = sum(
                        int((f.get("usage") or {}).get("api_attempts", 1) or 1)
                        for f in llm_adapter.failures
                    ) or 1
                    running_requests += step_attempts
                    failure_events.append(
                        {
                            "case_id": case_id,
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                    )
                    continue

                # Extract the derived transition from StagedMem0 without modifying StagedMem0.
                if outcome.guarded is not None and outcome.guarded.transition is not None:
                    derived_transition = outcome.guarded.transition
                else:
                    sources = [
                        (f"messages[{i}]:{m['role']}", m["content"])
                        for i, m in enumerate(messages)
                        if m["role"] == "user"
                    ]
                    claims = [
                        p["memory"]
                        for p in outcome.proposals
                        if p.get("event") in {"ADD", "UPDATE"} and p.get("memory")
                    ]
                    ev_items = LexicalGrounding()(claims, sources)
                    derived_transition = _fallback_transition_from_outcome(
                        case_id, prior_snapshot, outcome.proposals, ev_items
                    )

                # Advance host state when StagedMem0 held a write so subsequent sessions
                # for the same user see the natural accumulated Mem0 state in `before`.
                if not outcome.applied and outcome.changes:
                    try:
                        apply_changes(memory, outcome.changes)
                    except Exception:
                        pass

                step_in = sum(int(r.get("input_tokens") or 0) for r in llm_adapter.call_records)
                step_out = sum(int(r.get("output_tokens") or 0) for r in llm_adapter.call_records)
                step_tot = sum(
                    int(r.get("total_tokens") or ((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)))
                    for r in llm_adapter.call_records
                )
                step_attempts = sum(int(r.get("request_attempts") or 0) for r in llm_adapter.call_records)
                step_json_retries = sum(int(r.get("json_retries") or 0) for r in llm_adapter.call_records)
                step_cache_hits = sum(1 for r in llm_adapter.call_records if r.get("cached"))
                step_http_retries = sum(
                    max(0, int(r.get("request_attempts") or 0) - 1 - int(r.get("json_retries") or 0))
                    for r in llm_adapter.call_records
                )
                retry_events += step_http_retries + step_json_retries
                running_requests += step_attempts
                running_tokens += step_tot

                usage_dict = {
                    "input_tokens": step_in,
                    "output_tokens": step_out,
                    "total_tokens": step_tot,
                    "api_attempts": step_attempts,
                    "json_retries": step_json_retries,
                    "http_retries": step_http_retries,
                    "cache_hit": step_cache_hits,
                }
                proposal_payload = {
                    "case_id": case_id,
                    "user_uuid": user.uuid,
                    "session_id": session.session_id,
                    "session_index": s_idx,
                    "staged_decision": outcome.decision,
                    "staged_applied": outcome.applied,
                    "staged_reason": outcome.reason,
                    "mem0_raw_output": outcome.staged_result,
                    "mem0_proposals": list(outcome.proposals),
                    "transition": transition_dict(derived_transition),
                    "usage": usage_dict,
                    "latency_ms": outcome.latency_ms,
                }

            _u = proposal_payload.get("usage") or {}
            running_spend += (
                int(_u.get("input_tokens") or 0) * prompt_price
                + int(_u.get("output_tokens") or 0) * completion_price
            )
            proposal_file.write_text(
                json.dumps(proposal_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            checkpoint.append(
                {
                    "case_id": case_id,
                    "user_uuid": user.uuid,
                    "session_id": session.session_id,
                    "staged_decision": proposal_payload.get("staged_decision", "ACCEPT"),
                    "usage": usage_dict,
                    "accounted_tokens": step_tot,
                    "usage_estimated": False,
                    "attempts": step_attempts,
                    "cache_hit": step_cache_hits,
                }
            )
            proposal_records.append(proposal_payload)
            executed_sessions += 1
    finally:
        if memory is not None:
            for closer in (
                getattr(memory.vector_store.client, "close", None),
                getattr(memory.db, "close", None),
            ):
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass

    if not budget_aborted:
        checkpoint.mark_complete()

    # 6. STAGE 2 ("Judge many" offline): evaluate all 4 conditions on identical recorded transitions
    # and derive provisional silver labels.
    session_lookup: dict[str, tuple[HaluMemUser, HaluMemSession]] = {
        f"{u.uuid}__{s.session_id}": (u, s) for u, s, _ in sampled
    }
    judge: LLMClaimMatchJudge | None = None
    if enable_llm_claim_judge:
        judge = LLMClaimMatchJudge(client, log_path=out / "judge-calls.jsonl")

    transitions_by_id: dict[str, Transition] = {}
    silver_labels: list[SilverLabelResult] = []
    evaluated_rows: list[dict[str, Any]] = []

    for rec in proposal_records:
        cid = rec["case_id"]
        user_obj, session_obj = session_lookup[cid]
        transition = Transition.from_dict(rec["transition"])
        transitions_by_id[cid] = transition
        transcript = session_obj.to_transcript(user_uuid=user_obj.uuid)

        cond_decisions = evaluate_recorded_transition_all_conditions(
            transition, transcript
        )
        silver = derive_silver_label(
            transition,
            session_obj,
            user_uuid=user_obj.uuid,
            llm_judge=judge,
            omission_scope=silver_omission_scope,
            normalize_numerals=silver_normalize_numerals,
        )
        silver_labels.append(silver)

        evaluated_rows.append(
            {
                "case_id": cid,
                "user_uuid": user_obj.uuid,
                "session_id": session_obj.session_id,
                "silver_primary_label": silver.primary_label,
                "silver_fault_types": list(silver.fault_types),
                "silver_expected_decision": silver.expected_decision,
                "paraphrase_ungrounded_claims": list(
                    silver.paraphrased_valid_claims_ungrounded_by_lexical
                ),
                "decisions": cond_decisions,
                "usage": rec["usage"],
            }
        )

    # 7. Compute natural error prevalence, per-condition catch rates, false-hold rates, and paired CIs.
    total_proposals = len(evaluated_rows)
    silver_valid_rows = [
        r for r in evaluated_rows if r["silver_primary_label"] == "valid"
    ]
    silver_faulty_rows = [
        r for r in evaluated_rows if r["silver_primary_label"] != "valid"
    ]

    natural_prevalence: dict[str, Any] = {
        "total_proposals": total_proposals,
        "silver_valid": wilson(len(silver_valid_rows), total_proposals),
        "silver_any_fault": wilson(len(silver_faulty_rows), total_proposals),
        "by_fault_type": {
            ft: wilson(
                sum(ft in r["silver_fault_types"] for r in evaluated_rows),
                total_proposals,
            )
            for ft in FAULT_TYPES
        },
        "primary_category_counts": {
            cat: sum(r["silver_primary_label"] == cat for r in evaluated_rows)
            for cat in SILVER_CATEGORIES
        },
    }

    per_condition_metrics: dict[str, Any] = {}
    for cond in CONDITIONS:
        caught_any = [
            r["case_id"]
            for r in silver_faulty_rows
            if r["decisions"][cond]["decision"] == "HOLD"
        ]
        false_accepts = [
            r["case_id"]
            for r in silver_faulty_rows
            if r["decisions"][cond]["decision"] == "ACCEPT"
        ]
        valid_accepted = [
            r["case_id"]
            for r in silver_valid_rows
            if r["decisions"][cond]["decision"] == "ACCEPT"
        ]
        false_holds = [
            r["case_id"]
            for r in silver_valid_rows
            if r["decisions"][cond]["decision"] == "HOLD"
        ]
        paraphrase_false_holds = [
            r["case_id"]
            for r in silver_valid_rows
            if r["decisions"][cond]["decision"] == "HOLD"
            and bool(r["paraphrase_ungrounded_claims"])
        ]

        catch_by_type: dict[str, Any] = {}
        for ft in FAULT_TYPES:
            ft_rows = [r for r in evaluated_rows if ft in r["silver_fault_types"]]
            ft_caught = [
                r["case_id"]
                for r in ft_rows
                if r["decisions"][cond]["decision"] == "HOLD"
            ]
            catch_by_type[ft] = {
                "fault_cases": len(ft_rows),
                "caught": wilson(len(ft_caught), len(ft_rows)),
                "missed": wilson(len(ft_rows) - len(ft_caught), len(ft_rows)),
            }

        per_condition_metrics[cond] = {
            "proposals_evaluated": total_proposals,
            "decision_counts": {
                "ACCEPT": sum(
                    r["decisions"][cond]["decision"] == "ACCEPT" for r in evaluated_rows
                ),
                "HOLD": sum(
                    r["decisions"][cond]["decision"] == "HOLD" for r in evaluated_rows
                ),
            },
            "fault_catch_overall": wilson(len(caught_any), len(silver_faulty_rows)),
            "false_accept_overall": {
                **wilson(len(false_accepts), len(silver_faulty_rows)),
                "case_ids": false_accepts,
            },
            "catch_by_fault_type": catch_by_type,
            "silver_valid_acceptance": wilson(
                len(valid_accepted), len(silver_valid_rows)
            ),
            "false_hold_on_silver_valid": {
                **wilson(len(false_holds), len(silver_valid_rows)),
                "case_ids": false_holds,
                "paraphrase_lexical_grounding_false_holds": {
                    **wilson(len(paraphrase_false_holds), len(silver_valid_rows)),
                    "case_ids": paraphrase_false_holds,
                },
            },
        }

    # Paired comparisons across conditions on the recorded proposals.
    paired_comparisons: dict[str, Any] = {}
    if evaluated_rows:
        for pair_key, left, right in (
            ("graph_vs_text_fair", "graph", "text_fair"),
            ("graph_vs_text", "graph", "text"),
            ("graph_vs_unguarded", "graph", "unguarded"),
            ("text_fair_vs_unguarded", "text_fair", "unguarded"),
        ):
            l_scores = [
                1.0
                if r["decisions"][left]["decision"] == r["silver_expected_decision"]
                else 0.0
                for r in evaluated_rows
            ]
            r_scores = [
                1.0
                if r["decisions"][right]["decision"] == r["silver_expected_decision"]
                else 0.0
                for r in evaluated_rows
            ]
            b = sum(
                int(l_ok == 1.0 and r_ok == 0.0)
                for l_ok, r_ok in zip(l_scores, r_scores)
            )
            c = sum(
                int(r_ok == 1.0 and l_ok == 0.0)
                for l_ok, r_ok in zip(l_scores, r_scores)
            )
            paired_comparisons[pair_key] = {
                "left": left,
                "right": right,
                "units": len(evaluated_rows),
                "left_only_correct": b,
                "right_only_correct": c,
                "mcnemar_exact_p": mcnemar_exact(b, c),
                "paired_bootstrap_ci95": paired_bootstrap(
                    l_scores,
                    r_scores,
                    seed=_subseed(seed, f"phase2|{pair_key}"),
                    resamples=resamples,
                ),
            }

    # 8. Build the seeded, stratified human-audit sample and blind HTML quiz.
    audit_sample = select_stratified_audit_sample(
        silver_labels, target_size=audit_sample_size, seed=seed
    )
    quiz_info = (
        build_phase2_blind_quiz(
            transitions_by_id,
            audit_sample,
            out / "human-audit",
        )
        if audit_sample
        else None
    )

    # 9. Cost & telemetry accounting with membud.cost (verified_billed: False).
    token_counts = sum_token_counts(
        extract_token_counts(r["usage"]) for r in proposal_records
    )
    if judge is not None and judge.total_tokens:
        token_counts = token_counts + TokenCounts(
            input=0,
            output=judge.total_tokens,
            total_reported=judge.total_tokens,
        )
    price_tables = build_price_tables(
        input_per_mtok=pricing_info["prompt_per_mtok"],
        output_per_mtok=pricing_info["completion_per_mtok"],
        source=(
            f"DeepSeek list price table, peak rate, for {model_id} (not a provider bill)"
            if provider == "deepseek"
            else f"OpenRouter /api/v1/models catalog for {model_id} (not a provider bill)"
        ),
    )
    cost_estimate = estimate_cost(
        token_counts,
        price_tables,
        accounted_tokens=token_counts.reported_total,
    ).to_dict()

    report = {
        "schema": "membud-halumem-phase2-mem0-report-v1",
        "validation_status": "unvalidated_pending_human_audit",
        "silver_vs_human_agreement": {
            "status": "pending_human_review",
            "validated": False,
            "audit_sample_count": len(audit_sample),
            "quiz_package": quiz_info,
        },
        "run_config": run_config,
        "budget": {
            "aborted": budget_aborted,
            "abort_reason": abort_reason,
            "max_requests": max_requests,
            "max_total_tokens": max_total_tokens,
            "max_spend_usd_estimated_peak_rate": max_spend_usd,
            "estimated_spend_usd_peak_rate": round(running_spend, 6),
            "requests_used": running_requests,
            "tokens_used": running_tokens,
            "resumed_sessions": resumed_sessions,
            "executed_sessions": executed_sessions,
            "retry_events": retry_events,
            "failure_count": len(failure_events),
            "failures": failure_events,
        },
        "cost": cost_estimate,
        "provider_dashboard_checklist": (
            [
                "Open https://platform.deepseek.com/usage and compare billed spend with budget.estimated_spend_usd_peak_rate (an estimate, not a bill).",
                "Confirm the remaining balance dropped by no more than the estimate and not below your intended floor.",
                "Review DeepSeek's privacy policy/terms against the data-policy confirmation you gave.",
            ]
            if provider == "deepseek"
            else None
        ),
        "openrouter_dashboard_checklist": [
            "Open https://openrouter.ai/activity and filter by the experiment timestamp and model.",
            "Verify that every request was served by the resolved free ($0) model endpoint and total billed credit delta is $0.00.",
            "Open https://openrouter.ai/settings/privacy to confirm your prompt-logging / training opt-out state matches the experiment record.",
        ],
        "propose_once_judge_many": {
            "proposals_recorded": total_proposals,
            "verifier_llm_calls": 0,
            "judge_llm_calls": judge.calls if judge is not None else 0,
            "judge_cache_hits": judge.cache_hits if judge is not None else 0,
            "paired_on_identical_transitions": True,
        },
        "natural_error_prevalence": natural_prevalence,
        "conditions": per_condition_metrics,
        "paired_comparisons": paired_comparisons,
        "staged_mem0_findings": [
            (
                "StagedMem0._execute does not pass required_claims to Mem0ProposalBridge.prepare_proposals "
                "(required_claims defaults to ()), so coverage (REQUIRED_CLAIM_OMITTED) is vacuously PASS "
                "on StagedMem0-derived transitions unless required_claims are supplied."
            ),
            (
                "In mem0ai==2.2.1, Memory.add() (_add_to_vector_store V3 phased batch pipeline) uses "
                "ADDITIVE_EXTRACTION_PROMPT and only emits ADD proposals (mapped to WRITE) with MD5 "
                "exact-string dedup; it does not emit UPDATE or DELETE during add(), causing contradictory "
                "multi-session updates to accumulate alongside stale prior records."
            ),
            (
                "StagedMem0's default LexicalGrounding requires every stemmed content token of an extracted "
                "claim to appear in a single user turn; paraphrased or multi-turn-synthesized claims that "
                "are silver-valid receive no evidence.supports entry and are held with UNSUPPORTED_CLAIM."
            ),
        ],
        "what_is_and_is_not_shown": {
            "shown": [
                "End-to-end propose-once/judge-many execution of unmodified mem0ai==2.2.1 through StagedMem0 on multi-session dialogues.",
                "Exact decision parity between graph and text_fair when evaluated offline on identical recorded Mem0 transitions.",
                "Provisional silver error prevalence and paraphrase-induced false-hold rates under StagedMem0's LexicalGrounding.",
            ],
            "not_shown": [
                "Silver labels are provisional and NOT validated ground truth until the blind human audit quiz is completed and graded.",
                "No graph-superiority claim over text_fair is supported or made.",
                "Token/cost figures are telemetry estimates (verified_billed: false); actual $0 billing must be confirmed in the OpenRouter dashboard.",
            ],
        },
        "limitations": [
            SILVER_LABEL_LIMITATION,
        ],
    }

    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def grade_phase2_human_audit(
    *,
    output_dir: str | Path,
    answers_path: str | Path,
    reviewer: str = "",
) -> dict[str, Any]:
    """Grade returned human-audit quiz answers against Phase 2 silver labels."""
    import importlib.util

    out = Path(output_dir)
    manifest_path = out / "human-audit" / "audit-manifest.json"
    report_path = out / "report.json"
    if not manifest_path.is_file() or not report_path.is_file():
        raise FileNotFoundError(
            f"Phase 2 report or human-audit manifest not found in {out}"
        )

    quiz_script = ROOT / "scripts" / "review_quiz.py"
    spec = importlib.util.spec_from_file_location("membud_review_quiz", quiz_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    resolved_reviewer = reviewer.strip() or module.reviewer_from_answers(answers_path)
    agreement_path = out / "human-audit" / "silver-vs-human-agreement.json"
    agreement = module.grade_review(
        answers_path, resolved_reviewer, manifest_path, agreement_path
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["silver_vs_human_agreement"] = {
        "status": "graded",
        "validated": agreement["coverage"]["blank"] == 0,
        "reviewer": resolved_reviewer,
        "coverage": agreement["coverage"],
        "overall": agreement["overall"],
        "disagreement_count": len(agreement["disagreements"]),
        "agreement_report_path": str(agreement_path.name),
    }
    if agreement["coverage"]["blank"] == 0:
        report["validation_status"] = "human_audit_graded"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return agreement


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--halumem-path",
        help="path to local HaluMem-Medium.jsonl or HaluMem-Long.jsonl (omit to use synthetic fixture)",
    )
    parser.add_argument("--dataset-revision", default="")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--embedder",
        choices=("spacy", "fastembed", "hash"),
        default="spacy",
        help="local embedder for Mem0 (default: spacy en_core_web_sm, 96 dims, 0 network calls)",
    )
    parser.add_argument("--num-users", type=int, default=DEFAULT_NUM_USERS)
    parser.add_argument("--sessions-per-user", type=int, default=DEFAULT_SESSIONS_PER_USER)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    parser.add_argument("--max-total-tokens", type=int, default=DEFAULT_MAX_TOTAL_TOKENS)
    parser.add_argument("--audit-sample-size", type=int, default=DEFAULT_AUDIT_TARGET)
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="explicitly permit running a non-zero-priced OpenRouter model",
    )
    parser.add_argument(
        "--confirm-openrouter-data-policy",
        "--confirm-data-policy",
        dest="confirm_data_policy",
        action="store_true",
        help="confirm the selected provider's privacy/data-policy settings are acceptable for HaluMem (CC BY-NC-ND)",
    )
    parser.add_argument(
        "--silver-omission-scope",
        choices=("prior_only", "session_and_prior"),
        default="prior_only",
        help="silver omission rule: prior_only (v2, default) or session_and_prior (v1; any missing gold point)",
    )
    parser.add_argument(
        "--silver-normalize-numerals",
        action="store_true",
        help="compare numerals after dropping thousands separators and leading zeros (pre-registered numeral normalization; default off)",
    )
    parser.add_argument(
        "--provider",
        choices=("openrouter", "deepseek"),
        default="openrouter",
        help="LLM provider (deepseek = api.deepseek.com, paid, requires --allow-paid and --max-spend-usd)",
    )
    parser.add_argument(
        "--max-spend-usd",
        type=float,
        default=None,
        help="hard cap on estimated spend (peak list prices) for paid runs",
    )
    parser.add_argument(
        "--enable-llm-claim-judge",
        action="store_true",
        help="use the same free OpenRouter model as a cached claim-matching fallback for silver labels",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--grade-human-audit",
        metavar="ANSWERS_JSON",
        help="grade returned human review quiz answers JSON against Phase 2 silver labels",
    )
    parser.add_argument("--reviewer", default="")
    args = parser.parse_args(argv)

    if args.grade_human_audit:
        agreement = grade_phase2_human_audit(
            output_dir=args.output,
            answers_path=args.grade_human_audit,
            reviewer=args.reviewer,
        )
        ov = agreement["overall"]
        print(
            f"graded human audit: reviewer={agreement['reviewer']} "
            f"agreement={ov['agreements']}/{ov['answered']} ({ov['agreement_rate']})"
        )
        return 0

    report = run_halumem_phase2(
        halumem_path=args.halumem_path,
        dataset_revision=args.dataset_revision,
        output_dir=args.output,
        allow_paid=args.allow_paid,
        confirm_data_policy=args.confirm_data_policy,
        provider=args.provider,
        max_spend_usd=args.max_spend_usd,
        silver_omission_scope=args.silver_omission_scope,
        silver_normalize_numerals=args.silver_normalize_numerals,
        embedder_kind=args.embedder,
        num_users=args.num_users,
        sessions_per_user=args.sessions_per_user,
        seed=args.seed,
        max_requests=args.max_requests,
        max_total_tokens=args.max_total_tokens,
        enable_llm_claim_judge=args.enable_llm_claim_judge,
        audit_sample_size=args.audit_sample_size,
        resume=args.resume,
    )
    prev = report["natural_error_prevalence"]
    print(
        f"proposals={prev['total_proposals']} "
        f"silver_valid={prev['silver_valid']['numerator']}/{prev['silver_valid']['denominator']} "
        f"silver_faulty={prev['silver_any_fault']['numerator']}/{prev['silver_any_fault']['denominator']} "
        f"validation_status={report['validation_status']}"
    )
    for cond in CONDITIONS:
        cm = report["conditions"][cond]
        print(
            f"  {cond:10s} catch={cm['fault_catch_overall']['numerator']}/{cm['fault_catch_overall']['denominator']} "
            f"false_hold_valid={cm['false_hold_on_silver_valid']['numerator']}/{cm['false_hold_on_silver_valid']['denominator']} "
            f"(paraphrase_fh={cm['false_hold_on_silver_valid']['paraphrase_lexical_grounding_false_holds']['numerator']})"
        )
    print(f"wrote {Path(args.output) / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
