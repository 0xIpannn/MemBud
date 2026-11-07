#!/usr/bin/env python3
"""Controlled LLM ablation for the graph false-hold hypotheses (G0/G1/G2).

Background
----------
The held-out LLM run held 4 of 7 valid updates in the graph condition. A
cache-only analysis (``docs/graph-llm-false-holds.md``) attributed all four to
preservation failures whose stated reasons cite a null/absent ``authorization``
on an evidence-backed ``REVISE``/``SUPERSEDE``, plus one operation-scoping case.
This runner tests the two direct hypotheses with the smallest controlled probe:

* ``graph_cache`` (G0) — current graph + current prompt, replayed from the
  existing response cache. Free control; must reproduce the original HOLDs.
* ``graph_no_null_auth`` (G1) — same graph and prompt, but operation nodes with a
  null ``authorization`` omit that key from the serialization.
* ``graph_policy_clarified`` (G2) — current graph, but the shared system prompt
  adds one sentence: an evidence-backed ``REVISE``/``SUPERSEDE`` is itself an
  explicit authorized change and structured authorization is required only for
  ``DELETE``.

G1/G2 use new request material, so their completions are **new paid calls**.
The runner has a hard accounting cap, aborts before starting a row once the cap
is reached, and refuses to run any paid variant without ``--confirm-paid-call``.
The G0 control is cache-only and aborts before any network I/O on a cache miss.

The output does not by itself establish representation superiority: this is a
single-run directional probe on four hand-authored, single-reviewer cases.

Usage
-----
    # free control only
    .venv/bin/python scripts/run_graph_false_hold_ablation.py \
        --variants graph_cache --output .tmp-ablation-g0

    # paid probe (smallest directional test: 4 cases x 2 variants = 8 calls)
    set -a; source .env; set +a
    .venv/bin/python scripts/run_graph_false_hold_ablation.py \
        --variants graph_cache,graph_no_null_auth,graph_policy_clarified \
        --confirm-paid-call --output data/results/graph-false-hold-ablation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from membud.experiment import (  # noqa: E402
    load_manifest_entries,
    load_manifest_labels,
    source_fingerprint,
)
from membud.graph import build_llm_graph  # noqa: E402
from membud.llm import OpenAICompatibleChatClient  # noqa: E402
from membud.llm.client import CacheMissError  # noqa: E402
from membud.verification import (  # noqa: E402
    LLM_PROMPT_VERSION,
    LLM_PROMPT_VERSION_POLICY_CLARIFIED,
    verify_with_llm,
)

DEFAULT_MANIFEST = ROOT / "data/manifests/evaluation-v2.json"
DEFAULT_CASES = ("HELD-01", "HELD-02", "HELD-03", "HELD-07")
DEFAULT_MAX_TOTAL_TOKENS = 60_000
DEFAULT_MAX_ATTEMPTS = 3
ESTIMATED_TOKENS_PER_ATTEMPT = 2500
COST_LOW_USD_PER_MILLION_TOKENS = 0.10
COST_HIGH_USD_PER_MILLION_TOKENS = 2.00

VARIANT_ORDER = ("graph_cache", "graph_no_null_auth", "graph_policy_clarified")
VARIANT_CONFIG: dict[str, dict[str, Any]] = {
    "graph_cache": {
        "description": "G0 control: current graph + current prompt, cache-only replay",
        "omit_null_authorization": False,
        "policy_clarification": False,
        "cache_only": True,
        "prompt_version": LLM_PROMPT_VERSION,
    },
    "graph_no_null_auth": {
        "description": "G1: current graph/prompt; null authorization keys omitted",
        "omit_null_authorization": True,
        "policy_clarification": False,
        "cache_only": False,
        "prompt_version": LLM_PROMPT_VERSION,
    },
    "graph_policy_clarified": {
        "description": "G2: current graph; prompt states REVISE/SUPERSEDE authorization policy",
        "omit_null_authorization": False,
        "policy_clarification": True,
        "cache_only": False,
        "prompt_version": LLM_PROMPT_VERSION_POLICY_CLARIFIED,
    },
}
COMPARISON_POLICY = (
    "Directional ablation on four hand-authored, single-reviewer held-out cases. "
    "One cached control (G0) plus two new-prompt/graph variants; descriptive only, "
    "no causal or representation-superiority claim."
)


# ---------------------------------------------------------------------------
# Small helpers (pure; unit-tested)
# ---------------------------------------------------------------------------


def parse_cases(raw: str) -> tuple[str, ...]:
    cases = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not cases:
        raise ValueError("--cases must name at least one case id")
    return cases


def parse_variants(raw: str) -> tuple[str, ...]:
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not requested:
        raise ValueError("--variants must name at least one variant")
    unknown = sorted(set(requested) - set(VARIANT_ORDER))
    if unknown:
        raise ValueError(
            f"unsupported variant(s): {', '.join(unknown)}; choose from {', '.join(VARIANT_ORDER)}"
        )
    return tuple(name for name in VARIANT_ORDER if name in requested)


def any_paid_variant(variants: Iterable[str]) -> bool:
    return any(not VARIANT_CONFIG[name]["cache_only"] for name in variants)


def _token(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def account_usage(usage: Mapping[str, Any] | None, *, max_attempts: int) -> dict[str, Any]:
    """Turn a Result usage block into reported + conservatively accounted tokens."""
    usage = usage if isinstance(usage, Mapping) else {}
    input_tokens = _token(usage.get("input_tokens"))
    output_tokens = _token(usage.get("output_tokens"))
    total_tokens = _token(usage.get("total_tokens"))
    cache_hit = _token(usage.get("cache_hit")) or 0
    attempts = _token(usage.get("api_attempts"))
    if total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    if total_tokens is not None:
        accounted = total_tokens
        estimated = False
    elif cache_hit:
        accounted = 0
        estimated = False
    else:
        accounted = ESTIMATED_TOKENS_PER_ATTEMPT * (attempts or max_attempts)
        estimated = True
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "accounted_tokens": accounted,
        "usage_estimated": estimated,
        "cache_hit": cache_hit,
        "attempts": attempts,
    }


def failed_checks(result_payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    checks = (result_payload or {}).get("checks")
    if not isinstance(checks, list):
        return []
    return [
        check
        for check in checks
        if isinstance(check, dict) and check.get("status") != "PASS"
    ]


def authorization_mentions(result_payload: Mapping[str, Any] | None) -> int:
    """Count failed-check reasons that mention authorization (case-insensitive)."""
    count = 0
    for check in failed_checks(result_payload):
        reason = check.get("reason")
        if isinstance(reason, str) and "authoriz" in reason.lower():
            count += 1
    return count


def preservation_failed(result_payload: Mapping[str, Any] | None) -> bool:
    return any(check.get("name") == "preservation" for check in failed_checks(result_payload))


def cost_range(accounted_tokens: int) -> dict[str, Any]:
    return {
        "low_usd": round(accounted_tokens * COST_LOW_USD_PER_MILLION_TOKENS / 1_000_000, 6),
        "high_usd": round(accounted_tokens * COST_HIGH_USD_PER_MILLION_TOKENS / 1_000_000, 6),
        "estimated": True,
        "billing_data_available": False,
        "accounted_tokens": accounted_tokens,
    }


def summarize_variant(rows: Iterable[Mapping[str, Any]], variant: str) -> dict[str, Any]:
    variant_rows = [row for row in rows if row.get("variant") == variant]
    decisions = {"ACCEPT": 0, "HOLD": 0, "ERROR": 0}
    expected_accept = 0
    converted = 0
    remaining_false_holds = 0
    preservation_failures = 0
    auth_mentions = 0
    accounted = 0
    cache_hits = 0
    for row in variant_rows:
        decision = str(row.get("decision") or "ERROR")
        decisions[decision] = decisions.get(decision, 0) + 1
        if row.get("expected_decision") == "ACCEPT":
            expected_accept += 1
            if decision == "ACCEPT":
                converted += 1
            elif decision == "HOLD":
                remaining_false_holds += 1
        if row.get("preservation_failed"):
            preservation_failures += 1
        auth_mentions += int(row.get("authorization_mentions") or 0)
        accounted += int(row.get("accounted_tokens") or 0)
        cache_hits += int(row.get("cache_hit") or 0)
    return {
        "variant": variant,
        "description": VARIANT_CONFIG[variant]["description"],
        "rows": len(variant_rows),
        "decisions": decisions,
        "expected_accept_cases": expected_accept,
        "false_hold_conversions": converted,
        "false_holds_remaining": remaining_false_holds,
        "preservation_failure_rows": preservation_failures,
        "authorization_reason_mentions": auth_mentions,
        "accounted_tokens": accounted,
        "cache_hits": cache_hits,
        "estimated_cost_range_usd": cost_range(accounted),
    }


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_fresh_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"--output {output} already exists and is not empty; use a fresh directory")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cache_miss_count(client: Any) -> int:
    value = getattr(client, "cache_misses", 0)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


# ---------------------------------------------------------------------------
# Row execution
# ---------------------------------------------------------------------------


def _run_row(
    *,
    transition: Any,
    case_id: str,
    expected_decision: str,
    variant: str,
    client: Any,
    max_attempts: int,
) -> dict[str, Any]:
    config = VARIANT_CONFIG[variant]
    graph = build_llm_graph(
        transition, omit_null_authorization=bool(config["omit_null_authorization"])
    )
    misses_before = _cache_miss_count(client)
    try:
        result = verify_with_llm(
            transition,
            client,
            "graph",
            prepared_input=graph,
            policy_clarification=bool(config["policy_clarification"]),
        )
        result_payload = result.to_dict()
        usage = result.usage or {}
        error = None
    except CacheMissError:
        raise
    except Exception as exc:  # noqa: BLE001 - record the row, do not abort the run
        result_payload = None
        usage = {}
        error = f"{type(exc).__name__}: {exc}"
    if _cache_miss_count(client) > misses_before:
        raise CacheMissError(
            f"cache-only variant {variant} missed for {case_id}; refusing a provider request"
        )
    accounting = account_usage(usage, max_attempts=max_attempts)
    failed = failed_checks(result_payload)
    return {
        "case_id": case_id,
        "variant": variant,
        "expected_decision": expected_decision,
        "decision": (result_payload or {}).get("decision") or "ERROR",
        "error": error,
        "prompt_version": (result_payload or {}).get("prompt_version"),
        "failed_checks": [
            {
                "name": check.get("name"),
                "status": check.get("status"),
                "reason_code": check.get("reason_code"),
                "reason": str(check.get("reason") or "")[:400],
            }
            for check in failed
        ],
        "preservation_failed": preservation_failed(result_payload),
        "authorization_mentions": authorization_mentions(result_payload),
        "latency_ms": (result_payload or {}).get("latency_ms"),
        **accounting,
        "result": result_payload,
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run_ablation(
    *,
    manifest_path: Path,
    output: Path,
    clients: Mapping[str, Any],
    cases: Iterable[str],
    variants: Iterable[str],
    max_total_tokens: int,
    max_attempts: int,
    run_id: str | None = None,
    code_revision: str = "",
) -> dict[str, Any]:
    """Run the selected variants over the selected cases and write artifacts."""
    cases = tuple(cases)
    variants = tuple(variants)
    if max_total_tokens <= 0:
        raise ValueError("--max-total-tokens must be positive")
    if max_attempts < 1:
        raise ValueError("--max-attempts must be at least 1")
    _require_fresh_output(output)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    entries = {entry.case_id: entry for entry in load_manifest_entries(manifest_path)}
    labels = load_manifest_labels(manifest_path)
    missing = [case_id for case_id in cases if case_id not in entries]
    if missing:
        raise ValueError(f"cases not in manifest: {', '.join(missing)}")
    invalid = [case_id for case_id in cases if entries[case_id].invalid]
    if invalid:
        raise ValueError(f"cases are expected-invalid and cannot be verified: {', '.join(invalid)}")

    output.mkdir(parents=True, exist_ok=True)
    run_id = run_id or f"graph-ablation-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    code_revision = code_revision or source_fingerprint()

    rows: list[dict[str, Any]] = []
    accounted_total = 0
    aborted = False
    abort_reason = ""
    for variant in variants:
        client = clients.get(variant)
        if client is None:
            raise ValueError(f"no client supplied for variant {variant}")
        for case_id in cases:
            if aborted:
                break
            if accounted_total >= max_total_tokens:
                aborted = True
                abort_reason = (
                    f"token budget reached before {variant}/{case_id}: "
                    f"accounted {accounted_total} >= cap {max_total_tokens}"
                )
                break
            row = _run_row(
                transition=entries[case_id].transition,
                case_id=case_id,
                expected_decision=labels[case_id].get("expected_decision", ""),
                variant=variant,
                client=client,
                max_attempts=max_attempts,
            )
            accounted_total += int(row.get("accounted_tokens") or 0)
            rows.append(row)
            checks = ",".join(
                f"{check['name']}={check['status']}({check['reason_code']})"
                for check in row["failed_checks"]
            ) or "all PASS"
            print(
                f"{variant:24s} {case_id:8s} expected={row['expected_decision']:6s} "
                f"decision={row['decision']:6s} cache={row['cache_hit']} "
                f"tokens={row['accounted_tokens']} checks={checks}"
            )

    summary = {
        "run_id": run_id,
        "generated_at": _iso_now(),
        "code_revision": code_revision,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha256,
        "manifest_path": str(manifest_path),
        "split": "heldout",
        "cases": list(cases),
        "variants": list(variants),
        "comparison_policy": COMPARISON_POLICY,
        "budget": {
            "max_total_tokens": max_total_tokens,
            "accounted_tokens_used": accounted_total,
            "aborted": aborted,
            "abort_reason": abort_reason,
        },
        "variant_metrics": {
            variant: summarize_variant(rows, variant) for variant in variants
        },
        "rows": rows,
    }
    _write_json(output / "summary.json", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_client(*, max_attempts: int, cache_path: str | None, cache_only: bool) -> Any:
    """Build a client; cache-only mode never needs (or uses) an API key."""
    if cache_only:
        client = OpenAICompatibleChatClient(
            api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip() or "offline-cache-only",
            model=os.environ.get("DEEPSEEK_MODEL", OpenAICompatibleChatClient.DEEPSEEK_MODEL),
            base_url=os.environ.get(
                "DEEPSEEK_BASE_URL", OpenAICompatibleChatClient.DEEPSEEK_BASE_URL
            ),
            cache_path=cache_path or os.environ.get(
                "DEEPSEEK_CACHE_PATH", "~/.cache/membud/deepseek-cache.sqlite3"
            ),
            provider_name="deepseek-openai-compatible",
            require_cache=True,
        )
    else:
        client = OpenAICompatibleChatClient.from_deepseek_env(require_cache=False)
        if cache_path:
            client.cache_path = Path(cache_path).expanduser()
    client.max_attempts = max_attempts
    return client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled LLM ablation for the graph false-hold hypotheses. "
            "G1/G2 make paid provider calls and require --confirm-paid-call."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--cases",
        default=",".join(DEFAULT_CASES),
        help="comma-separated held-out cases (default: the four graph false holds)",
    )
    parser.add_argument(
        "--variants", default=",".join(VARIANT_ORDER), help="comma-separated variants"
    )
    parser.add_argument("--output", required=True, help="fresh output directory")
    parser.add_argument(
        "--max-total-tokens",
        type=int,
        default=DEFAULT_MAX_TOTAL_TOKENS,
        help="hard accounting cap on reported + estimated tokens",
    )
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--cache-path", default=None, help="SQLite response cache path")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--code-revision", default="")
    parser.add_argument(
        "--confirm-paid-call",
        action="store_true",
        help="required acknowledgement before running any non-cache-only variant",
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, Any] | int:
    args = build_parser().parse_args(argv)
    cases = parse_cases(args.cases)
    variants = parse_variants(args.variants)
    if any_paid_variant(variants) and not args.confirm_paid_call:
        print(
            "Refusing to run: G1/G2 make real paid provider calls. "
            "Re-run with --confirm-paid-call, or select --variants graph_cache for the free control.",
            file=sys.stderr,
        )
        return 2
    output = Path(args.output)
    _require_fresh_output(output)
    clients = {
        variant: build_client(
            max_attempts=args.max_attempts,
            cache_path=args.cache_path,
            cache_only=bool(VARIANT_CONFIG[variant]["cache_only"]),
        )
        for variant in variants
    }
    summary = run_ablation(
        manifest_path=Path(args.manifest),
        output=output,
        clients=clients,
        cases=cases,
        variants=variants,
        max_total_tokens=args.max_total_tokens,
        max_attempts=args.max_attempts,
        run_id=args.run_id or None,
        code_revision=args.code_revision,
    )
    print()
    for variant in summary["variants"]:
        metrics = summary["variant_metrics"][variant]
        cost = metrics["estimated_cost_range_usd"]
        print(
            f"{variant:24s} decisions={metrics['decisions']} "
            f"converted={metrics['false_hold_conversions']}/{metrics['expected_accept_cases']} "
            f"preservation_fails={metrics['preservation_failure_rows']} "
            f"auth_mentions={metrics['authorization_reason_mentions']} "
            f"tokens={metrics['accounted_tokens']} cache_hits={metrics['cache_hits']} "
            f"cost_est=${cost['low_usd']:.6f}-${cost['high_usd']:.6f} (ESTIMATE)"
        )
    budget = summary["budget"]
    print(
        f"accounted_tokens={budget['accounted_tokens_used']} cap={budget['max_total_tokens']} "
        f"aborted={budget['aborted']}"
    )
    print(f"wrote {output / 'summary.json'}")
    return summary


if __name__ == "__main__":
    main()
