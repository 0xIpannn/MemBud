"""Budget-capped LLM + semantic held-out evaluation harness.

This runner evaluates the held-out split of a hash-pinned manifest with three
LLM-backed conditions:

* ``graph``    -- ``run_case(representation="graph")`` + ``verify_with_llm``
* ``text``     -- ``run_case(representation="text")`` + ``verify_with_llm``
* ``semantic`` -- ``run_case(representation="text")`` + ``verify_with_semantics``
  (the deterministic text baseline plus one appended ``semantic`` check)

Only held-out cases are scored.  Cases whose raw input fails schema validation
(``expected_invalid`` in the v2 manifest) are recorded once per condition as
``INVALID_INPUT`` with zero model calls; they never touch a memory store.

Budget policy
-------------
``--max-total-tokens`` is an accounting cap enforced by this script.  After a
row completes, its accounted tokens are added; if the running total exceeds the
cap the runner aborts **before starting the next row** and still writes the
summary/metrics files with ``budget.aborted = true``.  Token counts are known
only after a provider response, so a single in-flight row can overshoot the cap;
the overshoot is bounded by that one row and reported explicitly.

Rows with no reported token usage (for example a transport failure where the
provider returned no usage) are charged the documented conservative fallback
``ESTIMATED_TOKENS_PER_ATTEMPT = 2500`` tokens per attempt, using the client's
reported attempt count when available and ``--max-attempts`` otherwise.  Such
rows are marked ``usage_estimated = true``.  Cache hits report zero new token
usage and are not charged the fallback.

No monetary billing data is available from the provider.  Cost figures are an
explicitly-labelled *estimate* derived from token-type-aware accounting
(:mod:`membud.cost`): per-million-token rates supplied via CLI flags, or the
documented placeholder range ``$0.10-$2.00/M`` when no quote is supplied.  They
are not a bill and must not be reported as verified spend.

Checkpoint / resume
-------------------
Every row is appended to ``<output>/checkpoint.jsonl`` (flushed and fsynced)
after it completes, together with a configuration fingerprint covering the
manifest SHA-256, split, model/provider, prompt versions, conditions, max
attempts, and code revision.  ``--resume`` reuses an existing output directory:
it validates the fingerprint (refusing any mismatch before a client call),
skips every recorded row without calling the client, carries forward the
checkpoint's accounted-token/attempt/cache totals, and rewrites
``summary.json``/``metrics.json`` as a complete run would.  A row whose durable
store/audit was written but whose checkpoint line was lost to a crash is
recovered from that store + audit before re-execution, so a completed paid
ACCEPT is not re-run or silently turned into an ``ERROR`` row.  Without
``--resume`` the default fresh-run behaviour is unchanged (a non-empty output
directory is still refused).  Resume must work with ``--require-cache``: skipped
rows make no client calls, so they cannot trigger a cache miss.

Cache-only / offline mode
-------------------------
``--require-cache`` (alias ``--offline``) enables the client's cache-only mode.
A cache miss raises :class:`membud.llm.client.CacheMissError` before any network
I/O and aborts the run loudly: it never falls back to a paid provider request.
Cache hits replay exactly as in a normal run and are reported with zero new
token usage.  Cache key material is unchanged, so this flag can replay a
previous paid run without altering cached entries.

Usage (supply the real key in process environment; never commit it)::

    set -a; source .env; set +a
    export CLAUDE_CACHE_PATH="$PWD/.cache/membud/claude-cache.sqlite3"
    mkdir -p .tmp-llm
    TMPDIR="$PWD/.tmp-llm" PYTHONPATH=src .venv/bin/python scripts/run_llm_heldout.py \
        --provider claude \
        --manifest data/manifests/evaluation-v2.json \
        --split heldout \
        --output data/results/llm-heldout-v2 \
        --conditions graph,text,semantic \
        --max-total-tokens 250000 \
        --max-attempts 3 \
        --cache-path "$CLAUDE_CACHE_PATH"

Tests inject ``FakeClient``/``FakeComparator`` objects and make no
network or paid calls.  This module itself never constructs an LLM client at
import time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]

def _relative(path: str | Path) -> str:
    """Repository-relative display path; absolute only when outside the repo."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from membud.checkpoint import (  # noqa: E402
    CHECKPOINT_FILENAME,
    CHECKPOINT_SCHEMA_VERSION,
    RunCheckpoint,
)
from membud.cost import (  # noqa: E402
    PLACEHOLDER_HIGH_USD_PER_MTOK,
    PLACEHOLDER_LOW_USD_PER_MTOK,
    PriceTable,
    TokenCounts,
    build_price_tables,
    estimate_cost,
    estimate_usage_cost,
    extract_token_counts,
    sum_token_counts,
)
from membud.experiment import (  # noqa: E402
    _last_event,
    _record_invalid_input,
    load_manifest_entries,
    load_manifest_labels,
    source_fingerprint,
)
from membud.llm import OpenAICompatibleChatClient  # noqa: E402
from membud.llm.client import CacheMissError  # noqa: E402
from membud.semantic import (  # noqa: E402
    LLMSemanticComparator,
    SEMANTIC_CHECK_NAME,
    SEMANTIC_PROMPT_VERSION,
    verify_with_semantics,
)
from membud.serialization import transition_dict  # noqa: E402
from membud.store import run_case  # noqa: E402
from membud.verification import LLM_PROMPT_VERSION, verify_with_llm  # noqa: E402

DEFAULT_MANIFEST = ROOT / "data/manifests/evaluation-v2.json"
CONDITION_ORDER = ("graph", "text", "semantic")
DEFAULT_CONDITIONS = ",".join(CONDITION_ORDER)
DEFAULT_MAX_TOTAL_TOKENS = 250_000
DEFAULT_MAX_ATTEMPTS = 3

# Conservative accounting fallback when the provider reports no usage.  This is
# deliberately larger than a typical small JSON-mode request so a failed row is
# never under-charged in the budget ledger.
ESTIMATED_TOKENS_PER_ATTEMPT = 2500

# Placeholder cost envelope, in USD per 1,000,000 accounted tokens.  Provider
# billing is unavailable for this workflow and no verified account pricing was
# recorded, so the metrics report a labelled RANGE rather than a single unearned
# number.  Kept as module constants for backwards compatibility with earlier
# analysis code; :mod:`membud.cost` is the source of truth.
COST_LOW_USD_PER_MILLION_TOKENS = PLACEHOLDER_LOW_USD_PER_MTOK
COST_HIGH_USD_PER_MILLION_TOKENS = PLACEHOLDER_HIGH_USD_PER_MTOK
COST_ASSUMPTION_NOTE = (
    "Estimated cost range, not a billed amount: the provider did not report "
    "monetary cost and no verified account price is recorded. The placeholder "
    f"assumption ${COST_LOW_USD_PER_MILLION_TOKENS:.2f}-"
    f"${COST_HIGH_USD_PER_MILLION_TOKENS:.2f} USD per 1,000,000 accounted tokens "
    "is applied to reported token classes plus conservatively estimated tokens "
    f"({ESTIMATED_TOKENS_PER_ATTEMPT} tokens/attempt where usage is absent). "
    "Token counts are telemetry, not a billing record."
)

COMPARISON_POLICY = (
    "Descriptive held-out evaluation only. Conditions use different verifier "
    "constructions (structural graph LLM check, text LLM check, deterministic "
    "text baseline plus semantic LLM check); this run does not establish or "
    "claim superiority of any condition."
)

_ATTEMPTED_STATUSES = frozenset(
    {"completed", "verifier_error", "error", "recovered_without_audit"}
)
_COMPLETED_STATUSES = frozenset({"completed", "recovered_without_audit"})
_ERROR_STATUSES = frozenset({"verifier_error", "error"})
_SAFE_DECISIONS = frozenset({"HOLD", "INVALID_INPUT"})


def _default_code_revision() -> str:
    return source_fingerprint((Path(__file__),))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": (numerator / denominator) if denominator else None,
    }


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def _token(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _count(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _cache_miss_count(client: Any) -> int:
    """Cache-only misses observed by the client.

    The client raises on every miss, but fail-closed layers (for example
    ``verify_with_llm``) may convert that exception into UNCERTAIN checks.  The
    counter lets the harness detect a swallowed miss and abort the run loudly.
    """
    value = getattr(client, "cache_misses", 0)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _client_invocation_count(client: Any) -> int | None:
    """Number of provider/verifier cycles processed by this client instance.

    Real clients expose ``provider_calls`` (provider requests only, cache hits
    excluded); test fakes may expose ``calls``.  ``None`` means the client does
    not report a counter.
    """
    for attribute in ("provider_calls", "calls"):
        value = getattr(client, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _claims_by_id(records: Iterable[Any]) -> dict[str, tuple[str, ...]]:
    return {record.id: tuple(record.claims) for record in records}


def _matching_audit_event(
    *, audit_path: Path, representation: str, expected_input: dict[str, Any]
) -> dict[str, Any] | None:
    """Last durable audit event for exactly this transition/condition, if any."""
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            candidate_event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(candidate_event, dict):
            continue
        if candidate_event.get("representation") != representation:
            continue
        if candidate_event.get("input") != expected_input:
            continue
        if not isinstance(candidate_event.get("result"), dict):
            continue
        return candidate_event
    return None


def _store_matches_candidate(store_path: Path, transition: Any) -> bool:
    """True when the durable per-case store still equals the candidate state."""
    if not store_path.exists():
        return False
    try:
        raw_store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    records = raw_store.get("records") if isinstance(raw_store, dict) else raw_store
    if not isinstance(records, list):
        return False
    stored_claims: dict[str, tuple[str, ...]] = {}
    for item in records:
        if not isinstance(item, dict):
            return False
        item_id = item.get("id")
        claims = item.get("claims")
        if (
            not isinstance(item_id, str)
            or not isinstance(claims, list)
            or any(not isinstance(claim, str) for claim in claims)
        ):
            return False
        stored_claims[item_id] = tuple(claims)
    return stored_claims == _claims_by_id(transition.candidate)


def _recovered_row(
    *,
    transition: Any,
    condition: str,
    event: dict[str, Any] | None,
    max_attempts: int,
) -> dict[str, Any]:
    """Build a summary row from a durable audit event (no new provider call)."""
    row: dict[str, Any] = {
        "case_id": transition.case_id,
        "condition": condition,
        "split": "heldout",
        "decision": "ACCEPT" if event is None else "HOLD",
        "status": "completed",
        "latency_ms": None,
        "checks": [],
        "error": None,
        "cache_hit": 0,
        "recovered_from_audit": True,
    }
    if event is None:
        return row
    result_payload = event["result"]
    row["decision"] = result_payload.get("decision") or "HOLD"
    row["status"] = event.get("status") or "completed"
    row["latency_ms"] = event.get("latency_ms")
    row["checks"] = _check_summaries(result_payload)
    accounting = account_usage(
        result_payload.get("usage") or {}, max_attempts=max_attempts
    )
    row.update(accounting)
    return row


def _recover_unrecorded_row(
    *,
    transition: Any,
    condition: str,
    representation: str,
    store_path: Path,
    audit_path: Path,
    max_attempts: int,
) -> dict[str, Any] | None:
    """Recover a decision durably written before a crash, without a new call.

    ``store.run_case`` writes the per-case store (ACCEPT only) and its audit
    event before the runner appends the checkpoint row.  If the process dies in
    that window the checkpoint row is missing although the decision and its
    token usage are already durable.  Re-running the row would turn a completed
    ACCEPT into a version-conflict error row, duplicate the audit event, and
    (for HOLDs too) make a second paid provider call.  This helper reconstructs
    the row from the durable audit event (and validates the store when one
    exists) instead.

    Returns ``None`` when there is no crash residue that can be trusted.
    """
    expected_input = json.loads(json.dumps(transition_dict(transition)))
    event = _matching_audit_event(
        audit_path=audit_path,
        representation=representation,
        expected_input=expected_input,
    )
    store_matches = _store_matches_candidate(store_path, transition)
    if store_matches:
        if event is not None:
            return _recovered_row(
                transition=transition, condition=condition, event=event, max_attempts=max_attempts
            )
        # Store present but no matching audit event (crash between store write
        # and audit append): the ACCEPT is inferable from the store, token
        # usage is not.  Never invent a fallback charge for unrecorded usage.
        row = _recovered_row(
            transition=transition, condition=condition, event=None, max_attempts=max_attempts
        )
        row.update(
            {
                "status": "recovered_without_audit",
                "error": (
                    "store state matches the candidate but no matching audit event "
                    "was found; token usage for this recovered ACCEPT is unknown"
                ),
                "usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "reported": False,
                    "json_retries": None,
                },
                "accounted_tokens": 0,
                "usage_estimated": False,
                "attempts": 0,
                "attempts_estimated": False,
            }
        )
        return row
    if event is not None:
        # No store is expected for a HOLD (HOLD never writes): recover the
        # durable non-ACCEPT decision from the audit event.  An ACCEPT event
        # without a matching store cannot be trusted; let the row run again.
        decision = str((event.get("result") or {}).get("decision") or "")
        if decision and decision != "ACCEPT":
            return _recovered_row(
                transition=transition, condition=condition, event=event, max_attempts=max_attempts
            )
    return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _require_fresh_output(output: Path) -> None:
    """Refuse to mix a new run into an existing output directory."""
    if not output.exists():
        return
    if not output.is_dir():
        raise ValueError(f"--output {output} exists and is not a directory")
    if any(output.iterdir()):
        raise ValueError(
            f"--output {output} already exists and is not empty; "
            "use a fresh --output directory"
        )


def parse_conditions(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated condition list in canonical order."""
    requested = [part.strip() for part in raw.split(",") if part.strip()]
    if not requested:
        raise ValueError("--conditions must name at least one condition")
    unknown = sorted(set(requested) - set(CONDITION_ORDER))
    if unknown:
        raise ValueError(
            f"unsupported condition(s): {', '.join(unknown)}; "
            f"choose from {', '.join(CONDITION_ORDER)}"
        )
    return tuple(condition for condition in CONDITION_ORDER if condition in requested)


def _estimated_cost_range(
    tokens: int, price_tables: tuple[PriceTable, PriceTable] | None = None
) -> dict[str, Any]:
    """Legacy helper: labelled estimate when only an accounted total is known."""
    tables = price_tables or build_price_tables()
    return estimate_cost(TokenCounts(), tables, accounted_tokens=int(tokens)).to_dict()


def _cost_estimate(
    tokens: TokenCounts,
    price_tables: tuple[PriceTable, PriceTable],
    *,
    accounted_tokens: int,
) -> dict[str, Any]:
    """Token-type-aware cost estimate payload (labelled; never a bill)."""
    return estimate_cost(
        tokens, price_tables, accounted_tokens=int(accounted_tokens)
    ).to_dict()


def _cost_assumption(
    cost_estimate: dict[str, Any], fallback_tokens_per_attempt: int
) -> dict[str, Any]:
    """Provenance block describing the pricing assumption behind the metrics."""
    pricing = cost_estimate["pricing"]
    payload: dict[str, Any] = {
        "estimated": True,
        "billing_data_available": False,
        "verified_billed": False,
        "currency": pricing["currency"],
        "rate_basis": pricing["rate_basis"],
        "pricing": pricing,
        "fallback_tokens_per_attempt": fallback_tokens_per_attempt,
        "note": cost_estimate["basis"],
    }
    if pricing["placeholder"]:
        payload["low_usd_per_million_tokens"] = pricing["low_rates_per_mtok"]["output"]
        payload["high_usd_per_million_tokens"] = pricing["high_rates_per_mtok"]["output"]
    return payload


def _require_resume_checkpoint(output: Path) -> Path:
    """Refuse ``--resume`` unless the run checkpoint exists on disk."""
    checkpoint_path = output / CHECKPOINT_FILENAME
    if not output.is_dir() or not checkpoint_path.is_file():
        raise ValueError(
            f"--resume requires an existing checkpoint file at {checkpoint_path}; "
            "start a fresh run without --resume first"
        )
    return checkpoint_path


def _checkpoint_config(
    *,
    manifest_id: str,
    manifest_sha256: str,
    split: str,
    conditions: Iterable[str],
    model: str,
    provider: str,
    prompt_versions: dict[str, str],
    max_attempts: int,
    code_revision: str,
) -> dict[str, Any]:
    """Settings whose change would make a resumed run incomparable.

    Deliberately excluded: ``max_total_tokens`` (a resume may extend the budget
    cap), ``require_cache``/``cache_path`` (resume must work in cache-only mode),
    and run identity/time fields.  Their effects are accounted for separately.
    """
    return {
        "manifest_id": manifest_id,
        "manifest_sha256": manifest_sha256,
        "split": split,
        "conditions": list(conditions),
        "model": model,
        "provider": provider,
        "prompt_versions": {
            "llm": prompt_versions.get("llm", ""),
            "semantic": prompt_versions.get("semantic", ""),
        },
        "max_attempts": int(max_attempts),
        "code_revision": code_revision,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
    }


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------


def account_usage(usage: dict[str, Any], *, max_attempts: int) -> dict[str, Any]:
    """Turn a result usage block into reported + conservative accounted tokens.

    The fallback applies only when no usage is reported and the row was not a
    cache hit.  The returned dictionary is also the per-row summary payload.
    """
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _token(usage.get("input_tokens"))
    output_tokens = _token(usage.get("output_tokens"))
    total_tokens = _token(usage.get("total_tokens"))
    cache_hit = _count(usage.get("cache_hit")) or 0
    raw_attempts = _count(usage.get("api_attempts"))
    usage_reported = total_tokens is not None or input_tokens is not None or output_tokens is not None

    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
        usage_reported = True

    if total_tokens is not None:
        accounted = total_tokens
        estimated = False
        attempts = raw_attempts
        attempts_estimated = False
    elif cache_hit:
        # Cached completions replay a prior response: no new tokens are billed.
        accounted = 0
        estimated = False
        attempts = raw_attempts or 0
        attempts_estimated = False
    elif raw_attempts == 0:
        # The row failed before any provider request (store reconciliation,
        # setup, lock).  No tokens were spent, so no fallback estimate may be
        # charged: a phantom charge would distort the budget ledger and cost.
        accounted = 0
        estimated = False
        attempts = 0
        attempts_estimated = False
    else:
        fallback_attempts = raw_attempts if raw_attempts and raw_attempts > 0 else max_attempts
        accounted = ESTIMATED_TOKENS_PER_ATTEMPT * fallback_attempts
        estimated = True
        attempts = fallback_attempts
        attempts_estimated = True

    return {
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "reported": usage_reported,
            "json_retries": _count(usage.get("json_retries")),
        },
        "accounted_tokens": accounted,
        "usage_estimated": estimated,
        "attempts": attempts,
        "attempts_estimated": attempts_estimated,
        "cache_hit": cache_hit,
    }


# ---------------------------------------------------------------------------
# Row execution
# ---------------------------------------------------------------------------


def _check_summaries(result_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    checks = (result_payload or {}).get("checks")
    if not isinstance(checks, list):
        return []
    summaries = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        summaries.append({
            "name": check.get("name"),
            "status": check.get("status"),
            "reason_code": check.get("reason_code"),
        })
    return summaries


def _semantic_uncertain(checks: Iterable[dict[str, Any]]) -> bool:
    return any(
        check.get("name") == SEMANTIC_CHECK_NAME and check.get("status") == "UNCERTAIN"
        for check in checks
    )


def _run_valid_row(
    *,
    transition: Any,
    condition: str,
    output: Path,
    client: Any,
    comparator_factory: Callable[[Any], Any],
    run_id: str,
    code_revision: str,
    manifest_id: str,
    manifest_sha256: str,
    max_attempts: int,
) -> dict[str, Any]:
    """Run one non-invalid case/condition row; never lets a row abort the run.

    Cache-only misses are the one exception: they abort the whole run so a
    missing cache entry can never fall back to a paid provider request.
    """
    misses_before = _cache_miss_count(client)
    calls_before = _client_invocation_count(client)
    base = output / condition
    store_path = base / (quote(transition.case_id, safe="") + ".json")
    audit_path = base / "audit.jsonl"
    representation = "text" if condition == "semantic" else condition

    recovered = _recover_unrecorded_row(
        transition=transition,
        condition=condition,
        representation=representation,
        store_path=store_path,
        audit_path=audit_path,
        max_attempts=max_attempts,
    )
    if recovered is not None:
        return recovered

    if condition == "semantic":

        def verifier(t: Any, rep: str, prepared: Any) -> Any:
            # New comparator per row keeps its cumulative usage scoped to the row.
            return verify_with_semantics(t, comparator_factory(client))
    else:

        def verifier(t: Any, rep: str, prepared: Any) -> Any:
            return verify_with_llm(t, client, rep, prepared_input=prepared)

    error: str | None = None
    try:
        result = run_case(
            transition,
            store_path,
            audit_path,
            representation=representation,
            run_id=run_id,
            code_revision=code_revision,
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
            expected_version=0,
            verifier=verifier,
        )
        result_payload = result.to_dict()
        event = _last_event(audit_path)
        status = event.get("status", "completed")
        decision = result.decision
        latency_ms = event.get("latency_ms")
        usage = result.usage or {}
    except CacheMissError:
        # Cache-only mode: a miss is a run-level safety abort, not a per-row
        # error.  Let it propagate so the run fails loudly before any paid call.
        raise
    except Exception as exc:  # noqa: BLE001 - recorded as a row, run continues
        error = f"{type(exc).__name__}: {exc}"
        event: dict[str, Any] = {}
        if audit_path.exists():
            try:
                event = _last_event(audit_path)
            except (OSError, ValueError, IndexError):
                event = {}
        result_payload = event.get("result") if isinstance(event.get("result"), dict) else None
        status = event.get("status", "error")
        if not isinstance(status, str):
            status = "error"
        decision = (result_payload or {}).get("decision") or "ERROR"
        latency_ms = event.get("latency_ms")
        usage = (result_payload or {}).get("usage") or {}
        calls_after = _client_invocation_count(client)
        if calls_after is not None and calls_after == calls_before:
            # Failed before any provider request (store reconciliation, setup,
            # lock): record zero attempts so no phantom token estimate is
            # charged to the budget/cost ledger.
            usage = {"api_attempts": 0, "cache_hit": 0}

    # Some verifier layers fail closed on exceptions; detect a swallowed
    # cache-only miss and turn it into a loud run-level abort.
    misses_after = _cache_miss_count(client)
    if misses_after > misses_before:
        raise CacheMissError(
            f"cache-only mode: {misses_after - misses_before} cache miss(es) while "
            f"running {condition}/{transition.case_id}; aborting the run instead of "
            "falling back to a provider request"
        )

    accounting = account_usage(usage, max_attempts=max_attempts)
    checks = _check_summaries(result_payload)
    if status == "verifier_error" and error is None:
        reasons = [check.get("reason_code") for check in checks if check.get("reason_code")]
        error = "verifier_error" + (f": {reasons[0]}" if reasons else "")
    row = {
        "case_id": transition.case_id,
        "condition": condition,
        "split": "heldout",
        "decision": decision,
        "status": status,
        "latency_ms": latency_ms,
        "checks": checks,
        "error": error,
        "cache_hit": accounting["cache_hit"],
        **accounting,
    }
    return row


def _record_invalid_row(
    *,
    entry: Any,
    condition: str,
    output: Path,
    run_id: str,
    code_revision: str,
    manifest_id: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    """Record an expected-invalid case with zero model calls and zero usage."""
    base = output / condition
    recorded = _record_invalid_input(
        entry,
        condition,
        base / "audit.jsonl",
        run_id,
        code_revision,
        manifest_id,
        manifest_sha256,
    )
    return {
        "case_id": entry.case_id,
        "condition": condition,
        "split": entry.split,
        "decision": recorded["decision"],
        "status": recorded["status"],
        "latency_ms": recorded.get("latency_ms"),
        "checks": [],
        "error": recorded.get("error"),
        "cache_hit": 0,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "reported": True,
            "json_retries": 0,
        },
        "accounted_tokens": 0,
        "usage_estimated": False,
        "attempts": 0,
        "attempts_estimated": False,
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _condition_metrics(
    *,
    condition: str,
    rows: list[dict[str, Any]],
    heldout_entries: list[Any],
    labels: dict[str, dict[str, Any]],
    price_tables: tuple[PriceTable, PriceTable],
) -> dict[str, Any]:
    condition_rows = [row for row in rows if row["condition"] == condition]
    attempted = [
        row for row in condition_rows if row["status"] in _ATTEMPTED_STATUSES
    ]
    attempted_ids = {row["case_id"] for row in attempted}

    expected_decisions = {
        entry.case_id: labels.get(entry.case_id, {}).get("expected_decision")
        for entry in heldout_entries
    }
    non_invalid_ids = {
        case_id
        for case_id, expected in expected_decisions.items()
        if expected != "INVALID_INPUT"
    }
    faulty_hold_ids = {
        case_id for case_id, expected in expected_decisions.items() if expected == "HOLD"
    }
    expected_accept_ids = {
        case_id for case_id, expected in expected_decisions.items() if expected == "ACCEPT"
    }
    expected_invalid_ids = {
        case_id
        for case_id, expected in expected_decisions.items()
        if expected == "INVALID_INPUT"
    }

    attempted_faulty = [row for row in attempted if row["case_id"] in faulty_hold_ids]
    attempted_valid = [row for row in attempted if row["case_id"] in expected_accept_ids]
    completed_faulty = [
        row for row in attempted_faulty if row["status"] in _COMPLETED_STATUSES
    ]
    completed_valid = [
        row for row in attempted_valid if row["status"] in _COMPLETED_STATUSES
    ]
    error_faulty = [
        row
        for row in attempted_faulty
        if row["status"] in _ERROR_STATUSES or row["decision"] == "ERROR"
    ]
    error_valid = [
        row
        for row in attempted_valid
        if row["status"] in _ERROR_STATUSES or row["decision"] == "ERROR"
    ]
    recorded_invalid = [
        row
        for row in condition_rows
        if row["case_id"] in expected_invalid_ids and row["status"] == "invalid_input"
    ]
    combined_faulty = attempted_faulty + recorded_invalid

    hold_detections = [row for row in completed_faulty if row["decision"] == "HOLD"]
    combined_detections = [
        row
        for row in (completed_faulty + recorded_invalid)
        if row["decision"] in _SAFE_DECISIONS
    ]
    false_accepts = [row for row in completed_faulty if row["decision"] == "ACCEPT"]
    completed_valid_accepts = [
        row for row in completed_valid if row["decision"] == "ACCEPT"
    ]
    completed_valid_holds = [
        row for row in completed_valid if row["decision"] != "ACCEPT"
    ]
    # Over attempted valid rows, both explicit HOLDs and error rows leave the
    # transition unaccepted, so false_holds + valid_acceptance == 1.0.
    false_holds = [row for row in attempted_valid if row["decision"] != "ACCEPT"]
    valid_accepts = [row for row in attempted_valid if row["decision"] == "ACCEPT"]
    faulty_not_accepted = [
        row for row in attempted_faulty if row["decision"] != "ACCEPT"
    ]

    verifier_error_rows = [
        row for row in attempted if row["status"] in _ERROR_STATUSES
    ]
    semantic_rows = [row for row in attempted if condition == "semantic"]
    semantic_abstentions = [
        row for row in semantic_rows if _semantic_uncertain(row.get("checks") or [])
    ]

    decision_counts: dict[str, int] = {}
    for row in condition_rows:
        decision_counts[row["decision"]] = decision_counts.get(row["decision"], 0) + 1

    reported_input = sum(
        int((row["usage"].get("input_tokens") or 0)) for row in attempted
    )
    reported_output = sum(
        int((row["usage"].get("output_tokens") or 0)) for row in attempted
    )
    reported_total = sum(
        int((row["usage"].get("total_tokens") or 0)) for row in attempted
    )
    estimated_tokens = sum(
        int(row["accounted_tokens"]) for row in attempted if row["usage_estimated"]
    )
    accounted_tokens = sum(int(row["accounted_tokens"]) for row in attempted)
    cache_hit_rows = [row for row in attempted if (row.get("cache_hit") or 0) > 0]
    latencies = [
        float(row["latency_ms"]) for row in attempted if row.get("latency_ms") is not None
    ]
    condition_tokens = sum_token_counts(
        extract_token_counts(row.get("usage") or {}) for row in attempted
    )
    cost_estimate = _cost_estimate(
        condition_tokens, price_tables, accounted_tokens=accounted_tokens
    )

    def ids(rows_: list[dict[str, Any]]) -> list[str]:
        return sorted({row["case_id"] for row in rows_})

    unattempted_faulty = len(faulty_hold_ids) - len(attempted_faulty)
    unattempted_valid = len(expected_accept_ids) - len(attempted_valid)
    as_completed = {
        "faulty": {
            "completed": len(completed_faulty),
            "flagged": len(hold_detections),
            "detection_rate": _rate(len(hold_detections), len(completed_faulty)),
            "false_accepts": len(false_accepts),
            "false_accept_rate": _rate(len(false_accepts), len(completed_faulty)),
            "case_ids": ids(hold_detections),
        },
        "valid": {
            "completed": len(completed_valid),
            "accepted": len(completed_valid_accepts),
            "acceptance_rate": _rate(len(completed_valid_accepts), len(completed_valid)),
            "held": len(completed_valid_holds),
            "false_hold_rate": _rate(len(completed_valid_holds), len(completed_valid)),
            "case_ids": ids(completed_valid_accepts),
        },
    }
    fail_closed_itt = {
        "faulty": {
            "cases": len(faulty_hold_ids),
            "held_with_decision": len(hold_detections),
            "error_holds": len(error_faulty),
            "error_hold_case_ids": ids(error_faulty),
            "unattempted_cases": unattempted_faulty,
            "accepted": len(false_accepts),
            "false_accept_rate": _rate(len(false_accepts), len(faulty_hold_ids)),
            "detection_rate": _rate(len(hold_detections), len(faulty_hold_ids)),
            "not_accepted_rate": _rate(
                len(faulty_hold_ids) - len(false_accepts), len(faulty_hold_ids)
            ),
        },
        "valid": {
            "cases": len(expected_accept_ids),
            "accepted": len(completed_valid_accepts),
            "held_with_decision": len(completed_valid_holds),
            "error_holds": len(error_valid),
            "error_hold_case_ids": ids(error_valid),
            "unattempted_cases": unattempted_valid,
            "not_accepted": len(expected_accept_ids) - len(completed_valid_accepts),
            "acceptance_rate": _rate(
                len(completed_valid_accepts), len(expected_accept_ids)
            ),
            "false_hold_rate": _rate(
                len(expected_accept_ids) - len(completed_valid_accepts),
                len(expected_accept_ids),
            ),
        },
    }

    return {
        "condition": condition,
        "row_count": len(condition_rows),
        "invalid_input_rows": len(recorded_invalid),
        "executed_rows": len(attempted),
        "planned_non_invalid_cases": len(non_invalid_ids),
        "case_coverage": _rate(len(attempted_ids & non_invalid_ids), len(non_invalid_ids)),
        "decision_counts": dict(sorted(decision_counts.items())),
        "as_completed": as_completed,
        "fail_closed_itt": fail_closed_itt,
        "faulty_detection_hold_only": {
            **_rate(len(hold_detections), len(attempted_faulty)),
            "case_ids": ids(hold_detections),
            "error_holds": len(error_faulty),
            "error_hold_case_ids": ids(error_faulty),
            "not_accepted": {
                **_rate(len(faulty_not_accepted), len(attempted_faulty)),
                "case_ids": ids(faulty_not_accepted),
            },
        },
        "faulty_detection_combined": {
            **_rate(len(combined_detections), len(combined_faulty)),
            "case_ids": ids(combined_detections),
            "note": (
                "Combined safe-rejection view: completed HOLD or INVALID_INPUT across "
                "expected HOLD cases plus recorded expected-invalid (INVALID_INPUT) cases. "
                "Error-holds are excluded from explicit detections. "
                "Denominators use rows actually attempted (plus always-recorded "
                "expected-invalid rows); see case_coverage for split coverage."
            ),
        },
        "false_accepts": {
            **_rate(len(false_accepts), len(attempted_faulty)),
            "case_ids": ids(false_accepts),
        },
        "false_holds": {
            **_rate(len(false_holds), len(attempted_valid)),
            "case_ids": ids(false_holds),
            "decision_holds": len(completed_valid_holds),
            "decision_hold_case_ids": ids(completed_valid_holds),
            "error_holds": len(error_valid),
            "error_hold_case_ids": ids(error_valid),
        },
        "valid_acceptance": {
            **_rate(len(valid_accepts), len(attempted_valid)),
            "case_ids": ids(valid_accepts),
        },
        "verifier_error_rate": {
            **_rate(len(verifier_error_rows), len(attempted)),
            "case_ids": ids(verifier_error_rows),
        },
        "semantic_abstention_rate": (
            {
                **_rate(len(semantic_abstentions), len(semantic_rows)),
                "case_ids": ids(semantic_abstentions),
            }
            if condition == "semantic"
            else None
        ),
        "cache_hit_rate": {
            **_rate(len(cache_hit_rows), len(attempted)),
            "cache_hits": sum(int(row.get("cache_hit") or 0) for row in attempted),
        },
        "attempts": sum(int(row.get("attempts") or 0) for row in attempted),
        "mean_latency_ms": _mean(latencies),
        "usage": {
            "reported": {
                "input_tokens": reported_input,
                "output_tokens": reported_output,
                "total_tokens": reported_total,
                "rows_with_reported_usage": sum(
                    1 for row in attempted if row["usage"].get("reported")
                ),
            },
            "estimated": {
                "accounted_tokens": estimated_tokens,
                "rows": sum(1 for row in attempted if row["usage_estimated"]),
                "basis": (
                    f"{ESTIMATED_TOKENS_PER_ATTEMPT} tokens/attempt when usage is absent "
                    "and the row is not a cache hit"
                ),
            },
            "accounted_total_tokens": accounted_tokens,
            "by_token_class": condition_tokens.to_dict(),
        },
        "cost_estimate": cost_estimate,
        "estimated_cost_range_usd": cost_estimate,
    }


def build_metrics(
    *,
    summary: dict[str, Any],
    heldout_entries: list[Any],
    labels: dict[str, dict[str, Any]],
    price_tables: tuple[PriceTable, PriceTable] | None = None,
) -> dict[str, Any]:
    rows = summary["results"]
    price_tables = price_tables or build_price_tables()
    condition_metrics = {
        condition: _condition_metrics(
            condition=condition,
            rows=rows,
            heldout_entries=heldout_entries,
            labels=labels,
            price_tables=price_tables,
        )
        for condition in summary["conditions"]
    }
    run_tokens = sum_token_counts(
        extract_token_counts(row.get("usage") or {}) for row in rows
    )
    run_cost_estimate = _cost_estimate(
        run_tokens,
        price_tables,
        accounted_tokens=int(summary["budget"]["accounted_tokens_used"]),
    )
    return {
        "run_id": summary["run_id"],
        "manifest_id": summary["manifest_id"],
        "manifest_sha256": summary["manifest_sha256"],
        "code_revision": summary["code_revision"],
        "split": summary["split"],
        "conditions": summary["conditions"],
        "generated_at": summary["generated_at"],
        "condition_metrics": condition_metrics,
        "run": {
            "row_count": len(rows),
            "executed_rows": sum(
                1 for row in rows if row["status"] in _ATTEMPTED_STATUSES
            ),
            "invalid_input_rows": sum(
                1 for row in rows if row["status"] == "invalid_input"
            ),
            "usage": summary["usage"],
            "budget": summary["budget"],
            "cost_estimate": run_cost_estimate,
            "estimated_cost_range_usd": run_cost_estimate,
        },
        "cost_estimate": run_cost_estimate,
        "comparison_policy": COMPARISON_POLICY,
        "denominator_note": (
            "Detection and error rates use rows actually attempted in this run "
            "(expected-invalid rows are always recorded); per-condition "
            "case_coverage reports how much of the planned held-out split ran."
        ),
        "labels_caveat": (
            "Labels are single-reviewer exploratory annotations; a second "
            "independent human review is still pending. This is a small synthetic "
            "held-out split, not a representative benchmark."
        ),
        "cost_assumption": _cost_assumption(
            run_cost_estimate, ESTIMATED_TOKENS_PER_ATTEMPT
        ),
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_heldout(
    *,
    manifest_path: str | Path,
    output: str | Path,
    client: Any,
    split: str = "heldout",
    conditions: Iterable[str] = CONDITION_ORDER,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    run_id: str | None = None,
    code_revision: str = "",
    comparator_factory: Callable[[Any], Any] = LLMSemanticComparator,
    resume: bool = False,
    price_tables: tuple[PriceTable, PriceTable] | None = None,
) -> dict[str, Any]:
    """Run the held-out LLM evaluation and write ``summary.json``/``metrics.json``.

    ``client`` is required and must be a fake in tests.  Every non-invalid row
    is executed through :func:`membud.store.run_case`; expected-invalid rows are
    recorded with zero client/comparator calls.

    ``resume=True`` reuses an existing output directory and its
    ``checkpoint.jsonl``: the configuration fingerprint is validated first, then
    every recorded row is reused without a client call and the run continues at
    the first unrecorded row.  ``resume=False`` keeps the original fresh-run
    contract (a non-empty output directory is refused).
    """
    if max_total_tokens <= 0:
        raise ValueError("--max-total-tokens must be positive")
    if max_attempts < 1:
        raise ValueError("--max-attempts must be at least 1")
    conditions = tuple(conditions)
    if not conditions:
        raise ValueError("at least one condition is required")
    unknown = sorted(set(conditions) - set(CONDITION_ORDER))
    if unknown:
        raise ValueError(f"unsupported condition(s): {', '.join(unknown)}")
    conditions = tuple(c for c in CONDITION_ORDER if c in conditions)

    manifest_path = Path(manifest_path)
    output = Path(output)
    price_tables = price_tables or build_price_tables()
    if resume:
        _require_resume_checkpoint(output)
    else:
        _require_fresh_output(output)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    entries = load_manifest_entries(manifest_path)
    labels = load_manifest_labels(manifest_path)

    heldout_entries = [entry for entry in entries if entry.split == split]
    if not heldout_entries:
        available = sorted({entry.split for entry in entries if entry.split is not None})
        raise ValueError(
            f"manifest has no cases for split {split!r}; available splits: {available}"
        )
    valid_entries = [entry for entry in heldout_entries if not entry.invalid]
    invalid_entries = [entry for entry in heldout_entries if entry.invalid]
    row_order = {
        (condition, entry.case_id): (condition_index, case_index)
        for condition_index, condition in enumerate(conditions)
        for case_index, entry in enumerate(heldout_entries)
    }

    output.mkdir(parents=True, exist_ok=True)
    run_id = run_id or f"llm-heldout-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    code_revision = code_revision or _default_code_revision()
    generated_at = _iso_now()

    model = getattr(client, "model", "") or ""
    provider = (getattr(client, "configuration", {}) or {}).get("provider", "")
    prompt_versions = {
        "llm": LLM_PROMPT_VERSION,
        "semantic": SEMANTIC_PROMPT_VERSION,
    }
    checkpoint_path = output / CHECKPOINT_FILENAME
    checkpoint_config = _checkpoint_config(
        manifest_id=str(manifest["manifest_id"]),
        manifest_sha256=manifest_sha256,
        split=split,
        conditions=conditions,
        model=str(model),
        provider=str(provider),
        prompt_versions=prompt_versions,
        max_attempts=max_attempts,
        code_revision=code_revision,
    )
    if resume:
        checkpoint = RunCheckpoint.load(checkpoint_path)
        # Refuse a mismatched configuration before any row (and any paid call).
        checkpoint.validate(checkpoint_config)
    else:
        checkpoint = RunCheckpoint.create(checkpoint_path, config=checkpoint_config)
    preexisting_identities = set(checkpoint.completed_identities())
    checkpoint_totals = checkpoint.totals()

    rows: list[dict[str, Any]] = []
    recorded_invalid: set[tuple[str, str]] = set()
    # Seed the running budget from the checkpoint so a resumed run continues the
    # same accounting ledger instead of restarting the cap.
    accounted_running = checkpoint_totals["accounted_tokens"]
    aborted = False
    abort_reason = ""

    def recorded_row(condition: str, case_id: str) -> dict[str, Any] | None:
        stored = checkpoint.get_row({"condition": condition, "case_id": case_id})
        return dict(stored) if stored is not None else None

    def finish_row(row: dict[str, Any], entry: Any) -> dict[str, Any]:
        row["label_expected_decision"] = labels.get(entry.case_id, {}).get(
            "expected_decision"
        )
        rows.append(row)
        return row

    def record_invalid(entry: Any, condition: str) -> dict[str, Any]:
        stored = recorded_row(condition, entry.case_id)
        if stored is None:
            row = _record_invalid_row(
                entry=entry,
                condition=condition,
                output=output,
                run_id=run_id,
                code_revision=code_revision,
                manifest_id=manifest["manifest_id"],
                manifest_sha256=manifest_sha256,
            )
            finish_row(row, entry)
            checkpoint.append(row)
        else:
            row = finish_row(stored, entry)
        recorded_invalid.add((condition, entry.case_id))
        return row

    for condition in conditions:
        if aborted:
            break
        for entry in heldout_entries:
            stored = recorded_row(condition, entry.case_id)
            if entry.invalid:
                if stored is not None:
                    finish_row(stored, entry)
                    recorded_invalid.add((condition, entry.case_id))
                else:
                    record_invalid(entry, condition)
                continue
            if aborted:
                break
            if stored is not None:
                # Already attempted in an earlier (interrupted) run: reuse the
                # recorded row and make no client call.
                finish_row(stored, entry)
                continue
            if accounted_running >= max_total_tokens:
                aborted = True
                abort_reason = (
                    f"token budget reached before {condition}/{entry.case_id}: "
                    f"accounted {accounted_running} >= cap {max_total_tokens}"
                )
                break
            row = _run_valid_row(
                transition=entry.transition,
                condition=condition,
                output=output,
                client=client,
                comparator_factory=comparator_factory,
                run_id=run_id,
                code_revision=code_revision,
                manifest_id=manifest["manifest_id"],
                manifest_sha256=manifest_sha256,
                max_attempts=max_attempts,
            )
            finish_row(row, entry)
            checkpoint.append(row)
            accounted_running += int(row["accounted_tokens"])
            if accounted_running > max_total_tokens:
                aborted = True
                abort_reason = (
                    f"token budget exceeded after {condition}/{entry.case_id}: "
                    f"accounted {accounted_running} > cap {max_total_tokens}; "
                    "stopped before the next row"
                )
                break

    # Expected-invalid rows cost nothing, so record any not reached before an
    # abort.  This keeps every held-out invalid case represented once per
    # condition without any client call.
    if aborted:
        for condition in conditions:
            for entry in invalid_entries:
                if (condition, entry.case_id) not in recorded_invalid:
                    record_invalid(entry, condition)

    rows.sort(key=lambda row: (row_order[(row["condition"], row["case_id"])]))

    # Recompute the whole ledger from the rows (fresh + checkpointed) so a
    # resumed run's totals are exactly the sum of all recorded rows.
    accounted_total = sum(int(row.get("accounted_tokens") or 0) for row in rows)
    reported_total = sum(
        int((row.get("usage") or {}).get("total_tokens") or 0) for row in rows
    )
    estimated_total = sum(
        int(row.get("accounted_tokens") or 0)
        for row in rows
        if row.get("usage_estimated")
    )
    attempts_total = sum(int(row.get("attempts") or 0) for row in rows)
    cache_hits_total = sum(int(row.get("cache_hit") or 0) for row in rows)
    resumed_rows = sum(
        1
        for row in rows
        if (row["condition"], row["case_id"]) in preexisting_identities
    )
    recovered_rows = sum(1 for row in rows if row.get("recovered_from_audit"))
    recovered_without_audit_rows = sum(
        1 for row in rows if row.get("status") == "recovered_without_audit"
    )
    if not aborted and accounted_total > max_total_tokens:
        aborted = True
        abort_reason = (
            f"token budget exceeded: accounted {accounted_total} > cap "
            f"{max_total_tokens} (recomputed from recorded rows; all recorded "
            "rows were kept)"
        )

    planned_identities = {
        (condition, entry.case_id)
        for condition in conditions
        for entry in heldout_entries
    }
    recorded_identities = {(row["condition"], row["case_id"]) for row in rows}
    if not aborted and recorded_identities == planned_identities:
        checkpoint.mark_complete()

    for row in rows:
        row["cost_estimate"] = estimate_usage_cost(
            row.get("usage") or {},
            price_tables,
            accounted_tokens=int(row.get("accounted_tokens") or 0),
        )
    directory_tokens = sum_token_counts(
        extract_token_counts(row.get("usage") or {}) for row in rows
    )
    run_cost_estimate = _cost_estimate(
        directory_tokens, price_tables, accounted_tokens=accounted_total
    )

    planned_valid_rows = len(valid_entries) * len(conditions)
    executed_valid_rows = sum(
        1 for row in rows if row["status"] in _ATTEMPTED_STATUSES
    )
    verifier_errors = sum(
        1 for row in rows if row["status"] in {"verifier_error", "error"}
    )
    semantic_uncertain = sum(
        1
        for row in rows
        if row["condition"] == "semantic"
        and _semantic_uncertain(row.get("checks") or [])
    )
    budget = {
        "max_total_tokens": max_total_tokens,
        "accounted_tokens_used": accounted_total,
        "reported_tokens_used": reported_total,
        "estimated_tokens_used": estimated_total,
        "aborted": aborted,
        "abort_reason": abort_reason,
        "overshoot_tokens": max(0, accounted_total - max_total_tokens),
        "planned_non_invalid_rows": planned_valid_rows,
        "executed_non_invalid_rows": executed_valid_rows,
        "resumed": bool(resume),
        "resumed_rows": resumed_rows,
        "note": (
            "The cap is checked after each row and before the next row starts. "
            "Because token usage is only known after a provider response, one "
            "in-flight row can overshoot the cap; overshoot_tokens reports it. "
            f"Rows without reported usage are charged {ESTIMATED_TOKENS_PER_ATTEMPT} "
            "tokens/attempt (marked estimated). A resumed run carries forward the "
            "checkpoint's accounted-token totals."
        ),
    }
    summary = {
        "run_id": run_id,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha256,
        "manifest_path": _relative(manifest_path),
        "cases_sha256": manifest.get("cases_sha256"),
        "labels_sha256": manifest.get("labels_sha256"),
        "split": split,
        "conditions": list(conditions),
        "case_count": len(heldout_entries),
        "valid_case_count": len(valid_entries),
        "invalid_case_count": len(invalid_entries),
        "model": model,
        "provider": provider,
        "cache_path": (
            _relative(getattr(client, "cache_path", ""))
            if getattr(client, "cache_path", None) is not None
            else None
        ),
        "require_cache": bool(getattr(client, "require_cache", False)),
        "max_attempts": max_attempts,
        "estimated_tokens_per_attempt": ESTIMATED_TOKENS_PER_ATTEMPT,
        "prompt_versions": prompt_versions,
        "resumed": bool(resume),
        "resumed_rows": resumed_rows,
        "recovered_rows": recovered_rows,
        "recovered_without_audit_rows": recovered_without_audit_rows,
        "checkpoint_path": _relative(checkpoint_path),
        "config_fingerprint": checkpoint.fingerprint,
        "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_torn_final_line": checkpoint.torn_final_line,
        "checkpoint_is_complete": checkpoint.is_complete,
        "results": rows,
        "budget": budget,
        "cost_estimate": run_cost_estimate,
        "usage": {
            "reported": {
                "input_tokens": sum(
                    int((row.get("usage") or {}).get("input_tokens") or 0) for row in rows
                ),
                "output_tokens": sum(
                    int((row.get("usage") or {}).get("output_tokens") or 0) for row in rows
                ),
                "total_tokens": reported_total,
                "rows_with_reported_usage": sum(
                    1 for row in rows if (row.get("usage") or {}).get("reported")
                ),
            },
            "estimated": {
                "accounted_tokens": estimated_total,
                "rows": sum(1 for row in rows if row.get("usage_estimated")),
                "basis": (
                    f"{ESTIMATED_TOKENS_PER_ATTEMPT} tokens/attempt when usage is "
                    "absent and the row is not a cache hit"
                ),
            },
            "accounted_total_tokens": accounted_total,
            "by_token_class": directory_tokens.to_dict(),
        },
        "reported_tokens_used": reported_total,
        "estimated_tokens_used": estimated_total,
        "accounted_tokens_used": accounted_total,
        "attempts": attempts_total,
        "cache_hits": cache_hits_total,
        "cache_misses": _cache_miss_count(client),
        "llm_verifier_errors": {
            "rows": verifier_errors,
            "case_ids": sorted(
                {
                    row["case_id"]
                    for row in rows
                    if row["status"] in {"verifier_error", "error"}
                }
            ),
        },
        "semantic_uncertain_rows": semantic_uncertain,
    }
    metrics = build_metrics(
        summary=summary,
        heldout_entries=heldout_entries,
        labels=labels,
        price_tables=price_tables,
    )
    _write_json(output / "summary.json", summary)
    _write_json(output / "metrics.json", metrics)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_client(
    *,
    max_attempts: int,
    cache_path: str | None = None,
    require_cache: bool = False,
    provider: str = "claude",
) -> Any:
    """Build the configured provider client (only called from the CLI, never tests).

    ``require_cache=True`` enables the client's cache-only mode: a cache miss
    raises :class:`membud.llm.client.CacheMissError` before any network I/O.
    """
    factory_name = {
        "claude": "from_claude_env",
        "anthropic": "from_claude_env",
        "gemini": "from_gemini_env",
        "google": "from_gemini_env",
        "deepseek": "from_deepseek_env",
        "commandcode": "from_env",
    }.get(provider.strip().lower())
    if factory_name and hasattr(OpenAICompatibleChatClient, factory_name):
        client = getattr(OpenAICompatibleChatClient, factory_name)(
            require_cache=require_cache
        )
    elif hasattr(OpenAICompatibleChatClient, "from_provider_env"):
        client = OpenAICompatibleChatClient.from_provider_env(
            provider, require_cache=require_cache
        )
    else:
        client = OpenAICompatibleChatClient.from_deepseek_env(
            require_cache=require_cache
        )
    client.max_attempts = max_attempts
    if cache_path:
        client.cache_path = Path(cache_path).expanduser()
    return client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Budget-capped LLM + semantic held-out evaluation harness.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--provider",
        choices=("claude", "gemini", "deepseek", "commandcode"),
        default="claude",
        help="LLM provider to use for completions",
    )
    parser.add_argument(
        "--manifest", default=str(DEFAULT_MANIFEST), help="hash-pinned manifest"
    )
    parser.add_argument("--split", default="heldout", help="manifest split to evaluate")
    parser.add_argument(
        "--output",
        required=True,
        help="output directory; reused only with --resume (fresh runs refuse reuse)",
    )
    parser.add_argument(
        "--conditions",
        default=DEFAULT_CONDITIONS,
        help="comma-separated subset of graph,text,semantic",
    )
    parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=DEFAULT_MAX_TOTAL_TOKENS,
        help="hard accounting cap on reported + estimated tokens",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="provider request attempts per completion",
    )
    parser.add_argument("--run-id", default="", help="run identity string")
    parser.add_argument(
        "--code-revision",
        default="",
        help="source revision label; defaults to a package working-tree hash",
    )
    parser.add_argument(
        "--cache-path",
        default=None,
        help=(
            "SQLite response cache path; defaults to provider env "
            "(e.g. CLAUDE_CACHE_PATH or GEMINI_CACHE_PATH)"
        ),
    )
    parser.add_argument(
        "--require-cache",
        "--offline",
        dest="require_cache",
        action="store_true",
        help=(
            "cache-only mode: a cache miss aborts the run before any provider "
            "request instead of making a paid call"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "resume an interrupted run from <output>/checkpoint.jsonl: validates "
            "the config fingerprint and skips recorded rows without calling the "
            "provider"
        ),
    )
    parser.add_argument(
        "--price-input-per-mtok",
        type=float,
        default=None,
        help="explicit input-token price per 1,000,000 tokens (with output price)",
    )
    parser.add_argument(
        "--price-output-per-mtok",
        type=float,
        default=None,
        help="explicit output-token price per 1,000,000 tokens (with input price)",
    )
    parser.add_argument(
        "--price-cached-per-mtok",
        type=float,
        default=None,
        help="explicit cached-input price per 1M tokens (default: input price)",
    )
    parser.add_argument(
        "--price-source",
        default="",
        help="price provenance source, e.g. a quote URL or 'Anthropic / Gemini pricing page'",
    )
    parser.add_argument(
        "--price-date",
        default="",
        help=(
            "price provenance date (ISO-8601); defaults to the run's UTC date. "
            "Estimates are never billed amounts"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    conditions = parse_conditions(args.conditions)
    output = Path(args.output)
    # Refuse a stale/absent run directory before constructing a paid client.
    if args.resume:
        _require_resume_checkpoint(output)
    else:
        _require_fresh_output(output)
    price_tables = build_price_tables(
        input_per_mtok=args.price_input_per_mtok,
        output_per_mtok=args.price_output_per_mtok,
        cached_per_mtok=args.price_cached_per_mtok,
        source=args.price_source,
        date=args.price_date,
    )
    code_revision = args.code_revision or _default_code_revision()
    run_id = args.run_id or (
        f"llm-heldout-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    client = build_client(
        max_attempts=args.max_attempts,
        cache_path=args.cache_path,
        require_cache=args.require_cache,
        provider=args.provider,
    )
    summary = run_heldout(
        manifest_path=args.manifest,
        output=output,
        client=client,
        split=args.split,
        conditions=conditions,
        max_total_tokens=args.max_total_tokens,
        max_attempts=args.max_attempts,
        run_id=run_id,
        code_revision=code_revision,
        resume=args.resume,
        price_tables=price_tables,
    )
    metrics_path = output / "metrics.json"
    print(
        f"manifest={summary['manifest_id']} sha256={summary['manifest_sha256'][:12]} "
        f"split={summary['split']} conditions={','.join(summary['conditions'])} "
        f"cases={summary['case_count']} (valid={summary['valid_case_count']}, "
        f"invalid={summary['invalid_case_count']})"
    )
    print(f"run_id={summary['run_id']} model={summary['model']}")
    print(
        f"resumed={summary['resumed']} resumed_rows={summary['resumed_rows']} "
        f"checkpoint={summary['checkpoint_path']} "
        f"fingerprint={summary['config_fingerprint'][:12]} "
        f"torn_final_line={summary['checkpoint_torn_final_line']}"
    )
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    for condition in summary["conditions"]:
        block = metrics["condition_metrics"][condition]
        counts = " ".join(
            f"{name}={value}" for name, value in block["decision_counts"].items()
        )
        cost = block["cost_estimate"]
        print(
            f"{condition:9s} {counts} tokens={block['usage']['accounted_total_tokens']} "
            f"(reported={block['usage']['reported']['total_tokens']}, "
            f"estimated={block['usage']['estimated']['accounted_tokens']}) "
            f"cache_rate={block['cache_hit_rate']['rate']} "
            f"mean_latency_ms={block['mean_latency_ms']} "
            f"cost_estimate=${cost['low_usd']:.6f}-${cost['high_usd']:.6f} (ESTIMATE)"
        )
        if condition == "semantic":
            abstention = block["semantic_abstention_rate"]
            print(
                f"          semantic_uncertain={abstention['numerator']}/"
                f"{abstention['denominator']}"
            )
    budget = summary["budget"]
    print(
        f"tokens: accounted={budget['accounted_tokens_used']} "
        f"reported={budget['reported_tokens_used']} "
        f"estimated={budget['estimated_tokens_used']} cap={budget['max_total_tokens']} "
        f"aborted={budget['aborted']} attempts={summary['attempts']} "
        f"cache_hits={summary['cache_hits']}"
    )
    cost_run = metrics["cost_estimate"]
    print(
        "cost: ESTIMATE ONLY, provider billing unavailable "
        f"(source={cost_run['pricing']['source']!r}, date={cost_run['pricing']['date']}, "
        f"verified_billed=False); {cost_run['basis']}"
    )
    print(f"no superiority claim: {metrics['comparison_policy']}")
    if budget["aborted"]:
        print(f"ABORTED: {budget['abort_reason']}", file=sys.stderr)
    print(f"wrote {output / 'summary.json'}")
    print(f"wrote {metrics_path}")
    return summary


if __name__ == "__main__":
    main()
