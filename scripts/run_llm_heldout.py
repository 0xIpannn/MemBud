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
explicitly-labelled *estimate range* derived from the documented placeholder
per-million-token assumptions below; they are not a bill and must not be
reported as verified spend.

Usage (the Lead supplies the real key; never commit it)::

    set -a; source .env; set +a
    export DEEPSEEK_CACHE_PATH="$PWD/.cache/membud/deepseek-cache.sqlite3"
    mkdir -p .tmp-llm
    TMPDIR="$PWD/.tmp-llm" PYTHONPATH=src .venv/bin/python scripts/run_llm_heldout.py \
        --manifest data/manifests/evaluation-v2.json \
        --split heldout \
        --output data/results/llm-heldout-v2 \
        --conditions graph,text,semantic \
        --max-total-tokens 250000 \
        --max-attempts 3 \
        --cache-path "$DEEPSEEK_CACHE_PATH"

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from membud.experiment import (  # noqa: E402
    _last_event,
    _record_invalid_input,
    load_manifest_entries,
    load_manifest_labels,
    source_fingerprint,
)
from membud.llm import OpenAICompatibleChatClient  # noqa: E402
from membud.semantic import (  # noqa: E402
    LLMSemanticComparator,
    SEMANTIC_CHECK_NAME,
    SEMANTIC_PROMPT_VERSION,
    verify_with_semantics,
)
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
# recorded, so the metrics report a RANGE rather than a single unearned number.
COST_LOW_USD_PER_MILLION_TOKENS = 0.10
COST_HIGH_USD_PER_MILLION_TOKENS = 2.00
COST_ASSUMPTION_NOTE = (
    "Estimated cost range, not a billed amount: the provider did not report "
    "monetary cost and no verified account price is recorded. The range applies "
    f"${COST_LOW_USD_PER_MILLION_TOKENS:.2f}-${COST_HIGH_USD_PER_MILLION_TOKENS:.2f} "
    "USD per 1,000,000 accounted tokens to reported plus conservatively "
    f"estimated tokens ({ESTIMATED_TOKENS_PER_ATTEMPT} tokens/attempt where "
    "usage is absent). Token counts are telemetry, not a billing record."
)

COMPARISON_POLICY = (
    "Descriptive held-out evaluation only. Conditions use different verifier "
    "constructions (structural graph LLM check, text LLM check, deterministic "
    "text baseline plus semantic LLM check); this run does not establish or "
    "claim superiority of any condition."
)

_ATTEMPTED_STATUSES = frozenset({"completed", "verifier_error", "error"})
_SAFE_DECISIONS = frozenset({"HOLD", "INVALID_INPUT"})


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


def _estimated_cost_range(tokens: int) -> dict[str, Any]:
    """Return the explicitly-labelled estimated cost range for accounted tokens."""
    low = round(tokens * COST_LOW_USD_PER_MILLION_TOKENS / 1_000_000, 6)
    high = round(tokens * COST_HIGH_USD_PER_MILLION_TOKENS / 1_000_000, 6)
    return {
        "low_usd": low,
        "high_usd": high,
        "estimated": True,
        "billing_data_available": False,
        "accounted_tokens": tokens,
        "assumption": {
            "low_usd_per_million_tokens": COST_LOW_USD_PER_MILLION_TOKENS,
            "high_usd_per_million_tokens": COST_HIGH_USD_PER_MILLION_TOKENS,
            "note": COST_ASSUMPTION_NOTE,
        },
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
    """Run one non-invalid case/condition row; never lets a row abort the run."""
    base = output / condition
    store_path = base / (quote(transition.case_id, safe="") + ".json")
    audit_path = base / "audit.jsonl"
    representation = "text" if condition == "semantic" else condition

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
    recorded_invalid = [
        row
        for row in condition_rows
        if row["case_id"] in expected_invalid_ids and row["status"] == "invalid_input"
    ]
    combined_faulty = attempted_faulty + recorded_invalid

    hold_detections = [row for row in attempted_faulty if row["decision"] == "HOLD"]
    combined_detections = [
        row for row in combined_faulty if row["decision"] in _SAFE_DECISIONS
    ]
    false_accepts = [row for row in attempted_faulty if row["decision"] == "ACCEPT"]
    false_holds = [row for row in attempted_valid if row["decision"] == "HOLD"]
    valid_accepts = [row for row in attempted_valid if row["decision"] == "ACCEPT"]

    verifier_error_rows = [
        row for row in attempted if row["status"] in {"verifier_error", "error"}
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

    def ids(rows_: list[dict[str, Any]]) -> list[str]:
        return sorted({row["case_id"] for row in rows_})

    return {
        "condition": condition,
        "row_count": len(condition_rows),
        "invalid_input_rows": len(recorded_invalid),
        "executed_rows": len(attempted),
        "planned_non_invalid_cases": len(non_invalid_ids),
        "case_coverage": _rate(len(attempted_ids & non_invalid_ids), len(non_invalid_ids)),
        "decision_counts": dict(sorted(decision_counts.items())),
        "faulty_detection_hold_only": {
            **_rate(len(hold_detections), len(attempted_faulty)),
            "case_ids": ids(hold_detections),
        },
        "faulty_detection_combined": {
            **_rate(len(combined_detections), len(combined_faulty)),
            "case_ids": ids(combined_detections),
            "note": (
                "Combined safe-rejection view: HOLD or INVALID_INPUT across expected "
                "HOLD cases plus recorded expected-invalid (INVALID_INPUT) cases. "
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
        },
        "estimated_cost_range_usd": _estimated_cost_range(accounted_tokens),
    }


def build_metrics(
    *,
    summary: dict[str, Any],
    heldout_entries: list[Any],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = summary["results"]
    condition_metrics = {
        condition: _condition_metrics(
            condition=condition,
            rows=rows,
            heldout_entries=heldout_entries,
            labels=labels,
        )
        for condition in summary["conditions"]
    }
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
            "estimated_cost_range_usd": _estimated_cost_range(
                summary["budget"]["accounted_tokens_used"]
            ),
        },
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
        "cost_assumption": {
            "estimated": True,
            "billing_data_available": False,
            "low_usd_per_million_tokens": COST_LOW_USD_PER_MILLION_TOKENS,
            "high_usd_per_million_tokens": COST_HIGH_USD_PER_MILLION_TOKENS,
            "fallback_tokens_per_attempt": ESTIMATED_TOKENS_PER_ATTEMPT,
            "note": COST_ASSUMPTION_NOTE,
        },
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
) -> dict[str, Any]:
    """Run the held-out LLM evaluation and write ``summary.json``/``metrics.json``.

    ``client`` is required and must be a fake in tests.  Every non-invalid row
    is executed through :func:`membud.store.run_case`; expected-invalid rows are
    recorded with zero client/comparator calls.
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
    code_revision = code_revision or source_fingerprint()
    generated_at = _iso_now()

    rows: list[dict[str, Any]] = []
    recorded_invalid: set[tuple[str, str]] = set()
    accounted_total = 0
    reported_total = 0
    estimated_total = 0
    attempts_total = 0
    cache_hits_total = 0
    aborted = False
    abort_reason = ""

    def record_invalid(entry: Any, condition: str) -> dict[str, Any]:
        row = _record_invalid_row(
            entry=entry,
            condition=condition,
            output=output,
            run_id=run_id,
            code_revision=code_revision,
            manifest_id=manifest["manifest_id"],
            manifest_sha256=manifest_sha256,
        )
        row["label_expected_decision"] = labels.get(entry.case_id, {}).get(
            "expected_decision"
        )
        recorded_invalid.add((condition, entry.case_id))
        rows.append(row)
        return row

    for condition in conditions:
        if aborted:
            break
        for entry in heldout_entries:
            if entry.invalid:
                record_invalid(entry, condition)
                continue
            if aborted:
                break
            if accounted_total >= max_total_tokens:
                aborted = True
                abort_reason = (
                    f"token budget reached before {condition}/{entry.case_id}: "
                    f"accounted {accounted_total} >= cap {max_total_tokens}"
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
            row["label_expected_decision"] = labels.get(entry.case_id, {}).get(
                "expected_decision"
            )
            rows.append(row)
            accounted_total += int(row["accounted_tokens"])
            reported_total += int(
                row["usage"].get("total_tokens") or 0
            )
            if row["usage_estimated"]:
                estimated_total += int(row["accounted_tokens"])
            attempts_total += int(row.get("attempts") or 0)
            cache_hits_total += int(row.get("cache_hit") or 0)
            if accounted_total > max_total_tokens:
                aborted = True
                abort_reason = (
                    f"token budget exceeded after {condition}/{entry.case_id}: "
                    f"accounted {accounted_total} > cap {max_total_tokens}; "
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
        "note": (
            "The cap is checked after each row and before the next row starts. "
            "Because token usage is only known after a provider response, one "
            "in-flight row can overshoot the cap; overshoot_tokens reports it. "
            f"Rows without reported usage are charged {ESTIMATED_TOKENS_PER_ATTEMPT} "
            "tokens/attempt (marked estimated)."
        ),
    }
    summary = {
        "run_id": run_id,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha256,
        "manifest_path": str(manifest_path),
        "cases_sha256": manifest.get("cases_sha256"),
        "labels_sha256": manifest.get("labels_sha256"),
        "split": split,
        "conditions": list(conditions),
        "case_count": len(heldout_entries),
        "valid_case_count": len(valid_entries),
        "invalid_case_count": len(invalid_entries),
        "model": getattr(client, "model", ""),
        "provider": (getattr(client, "configuration", {}) or {}).get("provider", ""),
        "cache_path": (
            str(getattr(client, "cache_path", ""))
            if getattr(client, "cache_path", None) is not None
            else None
        ),
        "max_attempts": max_attempts,
        "estimated_tokens_per_attempt": ESTIMATED_TOKENS_PER_ATTEMPT,
        "prompt_versions": {
            "llm": LLM_PROMPT_VERSION,
            "semantic": SEMANTIC_PROMPT_VERSION,
        },
        "results": rows,
        "budget": budget,
        "usage": {
            "reported": {
                "input_tokens": sum(
                    int(row["usage"].get("input_tokens") or 0) for row in rows
                ),
                "output_tokens": sum(
                    int(row["usage"].get("output_tokens") or 0) for row in rows
                ),
                "total_tokens": reported_total,
                "rows_with_reported_usage": sum(
                    1 for row in rows if row["usage"].get("reported")
                ),
            },
            "estimated": {
                "accounted_tokens": estimated_total,
                "rows": sum(1 for row in rows if row["usage_estimated"]),
                "basis": (
                    f"{ESTIMATED_TOKENS_PER_ATTEMPT} tokens/attempt when usage is "
                    "absent and the row is not a cache hit"
                ),
            },
            "accounted_total_tokens": accounted_total,
        },
        "reported_tokens_used": reported_total,
        "estimated_tokens_used": estimated_total,
        "accounted_tokens_used": accounted_total,
        "attempts": attempts_total,
        "cache_hits": cache_hits_total,
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
        summary=summary, heldout_entries=heldout_entries, labels=labels
    )
    _write_json(output / "summary.json", summary)
    _write_json(output / "metrics.json", metrics)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_client(*, max_attempts: int, cache_path: str | None = None) -> Any:
    """Build the paid DeepSeek client (only called from the CLI, never tests)."""
    client = OpenAICompatibleChatClient.from_deepseek_env()
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
        "--manifest", default=str(DEFAULT_MANIFEST), help="hash-pinned manifest"
    )
    parser.add_argument("--split", default="heldout", help="manifest split to evaluate")
    parser.add_argument(
        "--output", required=True, help="fresh output directory (refuses reuse)"
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
            "SQLite response cache path; defaults to DEEPSEEK_CACHE_PATH or "
            "~/.cache/membud/deepseek-cache.sqlite3"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    conditions = parse_conditions(args.conditions)
    output = Path(args.output)
    # Refuse a stale output directory before constructing a paid client.
    _require_fresh_output(output)
    code_revision = args.code_revision or source_fingerprint()
    run_id = args.run_id or (
        f"llm-heldout-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    client = build_client(max_attempts=args.max_attempts, cache_path=args.cache_path)
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
    )
    metrics_path = output / "metrics.json"
    print(
        f"manifest={summary['manifest_id']} sha256={summary['manifest_sha256'][:12]} "
        f"split={summary['split']} conditions={','.join(summary['conditions'])} "
        f"cases={summary['case_count']} (valid={summary['valid_case_count']}, "
        f"invalid={summary['invalid_case_count']})"
    )
    print(f"run_id={summary['run_id']} model={summary['model']}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    for condition in summary["conditions"]:
        block = metrics["condition_metrics"][condition]
        counts = " ".join(
            f"{name}={value}" for name, value in block["decision_counts"].items()
        )
        cost = block["estimated_cost_range_usd"]
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
    print(
        "cost: ESTIMATE ONLY, provider billing unavailable; "
        f"{metrics['cost_assumption']['note']}"
    )
    print(f"no superiority claim: {metrics['comparison_policy']}")
    if budget["aborted"]:
        print(f"ABORTED: {budget['abort_reason']}", file=sys.stderr)
    print(f"wrote {output / 'summary.json'}")
    print(f"wrote {metrics_path}")
    return summary


if __name__ == "__main__":
    main()
