#!/usr/bin/env python3
"""Phase 1 controlled, offline HaluMem RQ1 evaluation runner for MemBud.

Evaluates ``unguarded``, ``graph``, ``text``, and ``text_fair`` on identical
HaluMem-derived schema-v2 transitions, both **without** and **with** transcript
grounding, with zero network calls and zero API spend.

Features:
- Streams and strictly validates a local ``HaluMem-Medium.jsonl`` or
  ``HaluMem-Long.jsonl`` file (or runs against the built-in synthetic
  HaluMem-shaped fixture when ``--halumem-path`` is omitted, clearly marking
  ``is_synthetic_fixture: true``).
- Records only the dataset file SHA-256, dataset revision, case/session IDs,
  decisions, reason codes, counts, rates, and confidence intervals — never raw
  HaluMem dialogue or claim text.
- Uses deterministic seeded fault injection (:func:`membud.halumem.inject_halumem_faults`)
  for ``omission``, ``corruption``, and ``unsupported_addition``, explicitly
  stating label circularity as a limitation.
- Uses :class:`membud.checkpoint.RunCheckpoint` for crash-tolerant
  checkpoint/resume (``--resume``) with canonical configuration fingerprinting.
- Reports explicit numerators/denominators, Wilson 95% confidence intervals,
  paired case-level bootstrap 95% CIs, and exact two-sided McNemar p-values
  overall and per fault type for ``graph`` vs ``text_fair`` and guarded vs
  ``unguarded``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.checkpoint import CHECKPOINT_FILENAME, RunCheckpoint  # noqa: E402
from membud.conversation import verify_with_transcript  # noqa: E402
from membud.experiment import source_fingerprint  # noqa: E402
from membud.fairness import verify_text_fair  # noqa: E402
from membud.halumem import (  # noqa: E402
    FAULT_TYPES,
    INJECTOR_CIRCULARITY_LIMITATION,
    LEXICAL_EVIDENCE_ASSUMPTION,
    SYNTHETIC_FIXTURE_REVISION,
    UPDATE_OPERATIONS,
    InjectedEvalCase,
    build_halumem_transitions,
    inject_halumem_faults,
    load_halumem_dataset,
    write_synthetic_halumem_fixture,
)
from membud.paired_stats import mcnemar_exact, paired_bootstrap, wilson  # noqa: E402
from membud.verification import verify  # noqa: E402

CONDITIONS: tuple[str, ...] = ("unguarded", "graph", "text", "text_fair")
GROUNDING_MODES: tuple[str, ...] = (
    "without_transcript_grounding",
    "with_transcript_grounding",
)
CHECKPOINT_IDENTITY_FIELDS: tuple[str, ...] = (
    "grounding_mode",
    "condition",
    "case_id",
)
DEFAULT_SEED = 20261121
DEFAULT_RESAMPLES = 4000
DEFAULT_OUTPUT = ROOT / "halumem-output" / "phase1-offline"


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def _subseed(seed: int, key: str) -> int:
    digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _evaluate_one(
    case: InjectedEvalCase,
    condition: str,
    grounding_mode: str,
) -> dict[str, Any]:
    """Run one condition on one transition case and return a content-free row."""
    started = time.perf_counter()
    use_transcript = grounding_mode == "with_transcript_grounding"
    t = case.transition

    if condition == "unguarded":
        decision = "ACCEPT"
        fail_codes: list[str] = []
        verified = False
    elif not use_transcript:
        if condition == "graph":
            res = verify(t, "graph")
        elif condition == "text":
            res = verify(t, "text")
        elif condition == "text_fair":
            res = verify_text_fair(t)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported condition: {condition}")
        decision = res.decision
        fail_codes = sorted({c.reason_code for c in res.checks if not c.passed and c.reason_code})
        verified = True
    else:
        if condition == "graph":
            res = verify_with_transcript(t, case.transcript, "graph", fair_text=False)
        elif condition == "text":
            res = verify_with_transcript(t, case.transcript, "text", fair_text=False)
        elif condition == "text_fair":
            res = verify_with_transcript(t, case.transcript, "text", fair_text=True)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unsupported condition: {condition}")
        decision = res.decision
        fail_codes = sorted({c.reason_code for c in res.checks if not c.passed and c.reason_code})
        verified = True

    latency_ms = round((time.perf_counter() - started) * 1000, 4)
    return {
        "grounding_mode": grounding_mode,
        "condition": condition,
        "case_id": case.case_id,
        "base_case_id": case.base_case_id,
        "user_uuid": case.user_uuid,
        "session_id": case.session_id,
        "category": case.fault_type or "valid",
        "fault_type": case.fault_type,
        "fault_variant": case.fault_variant,
        "expected_decision": case.expected_decision,
        "decision": decision,
        "verified": verified,
        "fail_reason_codes": fail_codes,
        "latency_ms": latency_ms,
        "accounted_tokens": 0,
        "attempts": 0,
        "cache_hit": 0,
    }


def _candidate_error_denominators(
    suite: Sequence[InjectedEvalCase],
) -> dict[str, Any]:
    """Compute candidate-level claim/record error rates with explicit denominators."""
    required_claim_count = 0
    omitted_claim_count = 0
    material_candidate_count = 0
    unsupported_claim_count = 0
    valid_prior_record_count = 0
    corrupted_prior_record_count = 0
    faulty_case_count = 0

    for item in suite:
        t = item.transition
        before_claims = {c for r in t.before for c in r.claims}
        candidate_claims = {c for r in t.candidate for c in r.claims}
        evidence_claims = {c for e in t.evidence for c in e.supports}
        required = set(t.required_claims)

        required_claim_count += len(required)
        omitted_claim_count += len(required - candidate_claims)
        material_candidate_count += len(candidate_claims)
        unsupported_claim_count += len(
            (candidate_claims - before_claims - evidence_claims)
            | set(item.unsupported_added_claims)
        )
        valid_prior_record_count += len(item.valid_prior_records_expected_to_persist)
        corrupted_prior_record_count += len(item.corrupted_prior_record_ids)
        if item.expected_decision == "HOLD":
            faulty_case_count += 1

    return {
        "omission": _rate(omitted_claim_count, required_claim_count),
        "prior_record_corruption": _rate(
            corrupted_prior_record_count, valid_prior_record_count
        ),
        "unsupported_addition": _rate(
            unsupported_claim_count, material_candidate_count
        ),
        "faulty_case_prevalence": _rate(faulty_case_count, len(suite)),
    }


def _condition_metrics(
    rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    valid_rows = [r for r in rows if r["expected_decision"] == "ACCEPT"]
    faulty_rows = [r for r in rows if r["expected_decision"] == "HOLD"]

    valid_accepted = [r["case_id"] for r in valid_rows if r["decision"] == "ACCEPT"]
    false_holds = [r["case_id"] for r in valid_rows if r["decision"] != "ACCEPT"]
    faulty_detected = [r["case_id"] for r in faulty_rows if r["decision"] == "HOLD"]
    false_accepts = [r["case_id"] for r in faulty_rows if r["decision"] == "ACCEPT"]
    agreed = sum(r["decision"] == r["expected_decision"] for r in rows)

    per_fault_type: dict[str, Any] = {}
    for ft in FAULT_TYPES:
        ft_rows = [r for r in faulty_rows if r["fault_type"] == ft]
        ft_detected = [r["case_id"] for r in ft_rows if r["decision"] == "HOLD"]
        ft_fa = [r["case_id"] for r in ft_rows if r["decision"] == "ACCEPT"]
        per_fault_type[ft] = {
            "case_count": len(ft_rows),
            "detection": wilson(len(ft_detected), len(ft_rows)),
            "false_accepts": {
                **wilson(len(ft_fa), len(ft_rows)),
                "case_ids": ft_fa,
            },
        }

    reason_counts: dict[str, int] = {}
    for r in rows:
        for code in r["fail_reason_codes"]:
            reason_counts[code] = reason_counts.get(code, 0) + 1

    return {
        "case_count": len(rows),
        "decision_counts": {
            "ACCEPT": sum(r["decision"] == "ACCEPT" for r in rows),
            "HOLD": sum(r["decision"] == "HOLD" for r in rows),
        },
        "label_agreement": wilson(agreed, len(rows)),
        "faulty_overall": {
            "case_count": len(faulty_rows),
            "detection": wilson(len(faulty_detected), len(faulty_rows)),
            "false_accepts": {
                **wilson(len(false_accepts), len(faulty_rows)),
                "case_ids": false_accepts,
            },
        },
        "valid_updates": {
            "case_count": len(valid_rows),
            "acceptance": wilson(len(valid_accepted), len(valid_rows)),
            "false_holds": {
                **wilson(len(false_holds), len(valid_rows)),
                "case_ids": false_holds,
            },
        },
        "per_fault_type": per_fault_type,
        "fail_reason_code_counts": dict(sorted(reason_counts.items())),
    }


def _paired_comparison(
    left_rows: Sequence[dict[str, Any]],
    right_rows: Sequence[dict[str, Any]],
    *,
    left_label: str,
    right_label: str,
    seed: int,
    resamples: int = DEFAULT_RESAMPLES,
) -> dict[str, Any]:
    """Compute paired correctness comparison (bootstrap CI + exact McNemar)."""
    left_by_id = {r["case_id"]: r for r in left_rows}
    right_by_id = {r["case_id"]: r for r in right_rows}
    case_ids = [r["case_id"] for r in left_rows]
    if set(case_ids) != set(right_by_id):
        raise ValueError("paired comparison requires identical case_ids")

    if not case_ids:
        return {
            "left": left_label,
            "right": right_label,
            "units": 0,
            "left_correct": 0,
            "right_correct": 0,
            "left_only_correct": 0,
            "right_only_correct": 0,
            "discordant_case_ids": [],
            "mcnemar_exact_p": None,
            "paired_bootstrap_ci95": None,
        }

    left_scores: list[float] = []
    right_scores: list[float] = []
    b = c = 0
    discordant: list[str] = []
    for cid in case_ids:
        l_row = left_by_id[cid]
        r_row = right_by_id[cid]
        l_ok = l_row["decision"] == l_row["expected_decision"]
        r_ok = r_row["decision"] == r_row["expected_decision"]
        left_scores.append(1.0 if l_ok else 0.0)
        right_scores.append(1.0 if r_ok else 0.0)
        if l_ok and not r_ok:
            b += 1
            discordant.append(cid)
        elif r_ok and not l_ok:
            c += 1
            discordant.append(cid)

    boot = paired_bootstrap(
        left_scores,
        right_scores,
        seed=_subseed(seed, f"{left_label}|vs|{right_label}|{len(case_ids)}"),
        resamples=resamples,
    )
    return {
        "left": left_label,
        "right": right_label,
        "units": len(case_ids),
        "left_correct": int(sum(left_scores)),
        "right_correct": int(sum(right_scores)),
        "left_only_correct": b,
        "right_only_correct": c,
        "discordant_case_ids": discordant,
        "mcnemar_exact_p": mcnemar_exact(b, c),
        "paired_bootstrap_ci95": boot,
    }


def _mode_paired_comparisons(
    rows_by_condition: dict[str, list[dict[str, Any]]],
    *,
    seed: int,
    resamples: int = DEFAULT_RESAMPLES,
) -> dict[str, Any]:
    pairs = (
        ("graph_vs_text_fair", "graph", "text_fair"),
        ("graph_vs_text", "graph", "text"),
        ("text_fair_vs_text", "text_fair", "text"),
        ("graph_vs_unguarded", "graph", "unguarded"),
        ("text_fair_vs_unguarded", "text_fair", "unguarded"),
    )
    out: dict[str, Any] = {}
    for pair_key, left, right in pairs:
        l_all = rows_by_condition[left]
        r_all = rows_by_condition[right]
        overall = _paired_comparison(
            l_all,
            r_all,
            left_label=left,
            right_label=right,
            seed=_subseed(seed, f"{pair_key}|overall"),
            resamples=resamples,
        )
        l_faulty = [r for r in l_all if r["expected_decision"] == "HOLD"]
        r_faulty = [r for r in r_all if r["expected_decision"] == "HOLD"]
        faulty_comp = _paired_comparison(
            l_faulty,
            r_faulty,
            left_label=left,
            right_label=right,
            seed=_subseed(seed, f"{pair_key}|faulty"),
            resamples=resamples,
        )
        l_valid = [r for r in l_all if r["expected_decision"] == "ACCEPT"]
        r_valid = [r for r in r_all if r["expected_decision"] == "ACCEPT"]
        valid_comp = _paired_comparison(
            l_valid,
            r_valid,
            left_label=left,
            right_label=right,
            seed=_subseed(seed, f"{pair_key}|valid"),
            resamples=resamples,
        )
        by_ft: dict[str, Any] = {}
        for ft in FAULT_TYPES:
            l_ft = [r for r in l_all if r["fault_type"] == ft]
            r_ft = [r for r in r_all if r["fault_type"] == ft]
            by_ft[ft] = _paired_comparison(
                l_ft,
                r_ft,
                left_label=left,
                right_label=right,
                seed=_subseed(seed, f"{pair_key}|{ft}"),
                resamples=resamples,
            )
        out[pair_key] = {
            "overall": overall,
            "faulty_only": faulty_comp,
            "valid_only": valid_comp,
            "per_fault_type": by_ft,
        }
    return out


def run_halumem_phase1(
    *,
    halumem_path: str | Path | None = None,
    dataset_revision: str = "",
    output_dir: str | Path = DEFAULT_OUTPUT,
    seed: int = DEFAULT_SEED,
    update_operation: str = "REVISE",
    resamples: int = DEFAULT_RESAMPLES,
    resume: bool = False,
    code_revision: str = "",
) -> dict[str, Any]:
    """Run the Phase 1 offline HaluMem evaluation and write ``report.json``."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out / CHECKPOINT_FILENAME

    temp_fixture_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if halumem_path is None:
            temp_fixture_dir = tempfile.TemporaryDirectory(prefix="membud-halumem-syn-")
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
        build_report = build_halumem_transitions(
            users,
            dataset_meta=meta,
            update_operation=update_operation,
        )
        suite = inject_halumem_faults(build_report.cases, seed=seed)
    finally:
        if temp_fixture_dir is not None:
            temp_fixture_dir.cleanup()

    rev = code_revision.strip() or source_fingerprint((Path(__file__),))
    config = {
        "evaluation": "halumem-rq1-phase1-offline-v1",
        "file_sha256": meta.file_sha256,
        "dataset_revision": meta.dataset_revision,
        "is_synthetic_fixture": meta.is_synthetic_fixture,
        "seed": seed,
        "update_operation": build_report.update_operation_mode,
        "conditions": list(CONDITIONS),
        "grounding_modes": list(GROUNDING_MODES),
        "case_count": len(suite),
        "code_revision": rev,
    }

    if resume and checkpoint_path.exists():
        checkpoint = RunCheckpoint.load(
            checkpoint_path, identity_fields=CHECKPOINT_IDENTITY_FIELDS
        )
        checkpoint.validate(config)
    else:
        checkpoint = RunCheckpoint.create(
            checkpoint_path,
            config=config,
            identity_fields=CHECKPOINT_IDENTITY_FIELDS,
        )

    rows_by_mode_cond: dict[str, dict[str, list[dict[str, Any]]]] = {
        mode: {cond: [] for cond in CONDITIONS} for mode in GROUNDING_MODES
    }
    resumed_row_count = 0
    executed_row_count = 0

    for mode in GROUNDING_MODES:
        for cond in CONDITIONS:
            for case in suite:
                key = (mode, cond, case.case_id)
                cached_row = checkpoint.get_row(key)
                if cached_row is not None:
                    rows_by_mode_cond[mode][cond].append(cached_row)
                    resumed_row_count += 1
                    continue
                row = _evaluate_one(case, cond, mode)
                checkpoint.append(row)
                rows_by_mode_cond[mode][cond].append(row)
                executed_row_count += 1

    checkpoint.mark_complete()

    tracks: dict[str, Any] = {}
    for mode in GROUNDING_MODES:
        cond_blocks = {
            cond: _condition_metrics(rows_by_mode_cond[mode][cond])
            for cond in CONDITIONS
        }
        paired_blocks = _mode_paired_comparisons(
            rows_by_mode_cond[mode], seed=seed, resamples=resamples
        )
        tracks[mode] = {
            "conditions": cond_blocks,
            "paired_comparisons": paired_blocks,
        }

    # Cross-mode paired comparison: with vs without transcript grounding for graph and text_fair.
    grounding_effect: dict[str, Any] = {}
    for cond in ("graph", "text_fair", "text"):
        grounding_effect[cond] = _paired_comparison(
            rows_by_mode_cond["with_transcript_grounding"][cond],
            rows_by_mode_cond["without_transcript_grounding"][cond],
            left_label=f"{cond}:with_transcript_grounding",
            right_label=f"{cond}:without_transcript_grounding",
            seed=_subseed(seed, f"grounding_effect|{cond}"),
            resamples=resamples,
        )

    case_counts_by_type = {
        "total": len(suite),
        "valid": sum(c.fault_type is None for c in suite),
        "faulty_total": sum(c.fault_type is not None for c in suite),
        "by_fault_type": {
            ft: sum(c.fault_type == ft for c in suite) for ft in FAULT_TYPES
        },
    }

    report = {
        "schema": "membud-halumem-phase1-report-v1",
        "paid_api_calls": 0,
        "network_calls": 0,
        "config": config,
        "checkpoint": {
            "path": checkpoint_path.name,
            "fingerprint": checkpoint.fingerprint,
            "complete": checkpoint.is_complete,
            "resumed_rows": resumed_row_count,
            "executed_rows": executed_row_count,
            "totals": checkpoint.totals(),
        },
        "build_summary": build_report.summary_dict(),
        "case_counts": case_counts_by_type,
        "candidate_error_rates": _candidate_error_denominators(suite),
        "tracks": tracks,
        "transcript_grounding_effect": grounding_effect,
        "limitations": [
            INJECTOR_CIRCULARITY_LIMITATION,
            LEXICAL_EVIDENCE_ASSUMPTION,
            (
                "With equal information (including evidence.contradicts), graph and "
                "text_fair tie on all decisions; plain text differs only where "
                "evidence.contradicts is present because plain text ignores that field. "
                "No graph-superiority claim is made."
            ),
        ],
    }

    report_path = out / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _print_summary(report: dict[str, Any], output_dir: Path) -> None:
    meta = report["build_summary"]["dataset"]
    counts = report["case_counts"]
    print(
        f"dataset_revision={meta['dataset_revision']} "
        f"sha256={meta['file_sha256'][:12]} "
        f"synthetic_fixture={meta['is_synthetic_fixture']} "
        f"users={meta['user_count']} sessions={meta['session_count']} "
        f"ungroundable={report['build_summary']['ungroundable_count']}"
    )
    print(
        f"cases={counts['total']} (valid={counts['valid']}, "
        f"omission={counts['by_fault_type']['omission']}, "
        f"corruption={counts['by_fault_type']['corruption']}, "
        f"unsupported_addition={counts['by_fault_type']['unsupported_addition']})"
    )
    for mode in GROUNDING_MODES:
        print(f"\n=== Track: {mode} ===")
        track = report["tracks"][mode]
        for cond in CONDITIONS:
            cm = track["conditions"][cond]
            fo = cm["faulty_overall"]
            vu = cm["valid_updates"]
            pft = cm["per_fault_type"]
            print(
                f"  {cond:10s} agree={cm['label_agreement']['numerator']}/{cm['label_agreement']['denominator']} "
                f"faulty_det={fo['detection']['numerator']}/{fo['detection']['denominator']} "
                f"false_acc={fo['false_accepts']['numerator']}/{fo['false_accepts']['denominator']} "
                f"false_hold={vu['false_holds']['numerator']}/{vu['false_holds']['denominator']} | "
                f"omission_det={pft['omission']['detection']['numerator']}/{pft['omission']['detection']['denominator']} "
                f"corruption_det={pft['corruption']['detection']['numerator']}/{pft['corruption']['detection']['denominator']} "
                f"unsupported_det={pft['unsupported_addition']['detection']['numerator']}/{pft['unsupported_addition']['detection']['denominator']}"
            )
        g_vs_tf = track["paired_comparisons"]["graph_vs_text_fair"]["overall"]
        g_vs_ug = track["paired_comparisons"]["graph_vs_unguarded"]["faulty_only"]
        print(
            f"  paired graph vs text_fair (overall): diff={g_vs_tf['paired_bootstrap_ci95']['mean_diff']:+.4f} "
            f"[{g_vs_tf['paired_bootstrap_ci95']['ci95_low']:+.4f}, {g_vs_tf['paired_bootstrap_ci95']['ci95_high']:+.4f}] "
            f"discordant=+{g_vs_tf['left_only_correct']}/-{g_vs_tf['right_only_correct']} p={g_vs_tf['mcnemar_exact_p']}"
        )
        print(
            f"  paired graph vs unguarded (faulty detection): diff={g_vs_ug['paired_bootstrap_ci95']['mean_diff']:+.4f} "
            f"[{g_vs_ug['paired_bootstrap_ci95']['ci95_low']:+.4f}, {g_vs_ug['paired_bootstrap_ci95']['ci95_high']:+.4f}] "
            f"discordant=+{g_vs_ug['left_only_correct']}/-{g_vs_ug['right_only_correct']} p={g_vs_ug['mcnemar_exact_p']}"
        )
    print(f"\nwrote {output_dir / 'report.json'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--halumem-path",
        help="path to local HaluMem-Medium.jsonl or HaluMem-Long.jsonl (omit to use synthetic fixture)",
    )
    parser.add_argument(
        "--dataset-revision",
        default="",
        help="dataset revision label (recorded alongside file SHA-256)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="gitignored output directory for report and checkpoint",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="deterministic RNG seed for fault injection and bootstrap CIs",
    )
    parser.add_argument(
        "--update-operation",
        choices=UPDATE_OPERATIONS,
        default="REVISE",
        help="operation type generated for HaluMem is_update memory points",
    )
    parser.add_argument(
        "--resamples",
        type=int,
        default=DEFAULT_RESAMPLES,
        help="paired bootstrap resamples (default 4000)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="resume from an existing checkpoint in --output",
    )
    parser.add_argument("--code-revision", default="")
    args = parser.parse_args(argv)

    report = run_halumem_phase1(
        halumem_path=args.halumem_path,
        dataset_revision=args.dataset_revision,
        output_dir=args.output,
        seed=args.seed,
        update_operation=args.update_operation,
        resamples=args.resamples,
        resume=args.resume,
        code_revision=args.code_revision,
    )
    _print_summary(report, Path(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
