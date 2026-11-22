"""HaluMem official metrics, re-implemented from the reference evaluation code.

Reference: https://github.com/MemTensor/HaluMem, ``eval/evaluation.py``
(``aggregate_eval_results``) at the commit recorded by :data:`REFERENCE_COMMIT`,
and the paper (arXiv 2511.03506, Section 5.1).  This module is pure: it takes
per-item judge outputs (0/1/2 scores, update/QA labels) and aggregates them the
way the reference code does.  It makes no network calls and holds no dataset
text.  The judge itself (the official prompts) lives in
``scripts/score_halumem_official.py``.

Where the paper and the code differ, the **code** is followed and the
difference is listed in :data:`DEVIATIONS_FROM_PAPER`:

* ``recall``          = (# non-interference gold points with integrity score 2) / (# such points).
* ``weighted_recall`` = sum(0.5 * score * importance) / sum(importance)  (score 1 = half credit).
* ``fmr``             = (# interference gold points with integrity score 0) / (# interference points);
  the code calls this ``interference_accuracy``.
* ``target_precision``= sum(0.5 * accuracy_score) over extracted memories flagged
  ``is_included_in_golden_memories`` / (# flagged); the code calls it ``target_accuracy``.
* ``weighted_accuracy`` (paper: Memory Accuracy) = sum(0.5 * accuracy_score) / (# extracted).
* ``f1``              = harmonic mean of ``target_precision(all)`` and ``recall(all)``.
* An empty extraction for a session gives every gold point integrity score 0 (including
  interference points, which then count as "resisted").
* ``(all)`` denominators count items whose judge call failed (score ``None``); ``(valid)`` drops them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

REFERENCE_REPO = "https://github.com/MemTensor/HaluMem"
REFERENCE_COMMIT = "d653749586bfc7d13f5b68e8851f52155b5b1650"
INTERFERENCE_SOURCE = "interference"
UPDATE_LABELS = ("Correct", "Hallucination", "Omission", "Other")
QA_LABELS = ("Correct", "Hallucination", "Omission")

DEVIATIONS_FROM_PAPER = (
    "Recall counts only integrity score 2; weighted recall gives score 1 half credit (code, not paper).",
    "Target precision's membership flag (is_included_in_golden_memories) is defined in the code's judge prompt only.",
    "FMR is computed from the integrity judge on interference points (code name: interference_accuracy).",
    "Extraction F1 uses unweighted recall(all) and target_accuracy(all).",
)


def _ratio(num: float, den: float) -> float | None:
    return None if den == 0 else num / den


@dataclass(frozen=True)
class IntegrityRecord:
    """One gold memory point scored by the integrity judge (0/1/2, or ``None`` if the judge failed)."""

    source: str
    importance: float
    score: int | None

    @property
    def is_interference(self) -> bool:
        return self.source == INTERFERENCE_SOURCE


@dataclass(frozen=True)
class AccuracyRecord:
    """One extracted memory scored by the accuracy judge."""

    score: int | None
    included_in_golden: bool


def compute_f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def aggregate_integrity(records: Iterable[IntegrityRecord]) -> dict[str, float | int | None]:
    n = valid_n = 0
    w_all = w_valid = w_scores = 0.0
    full = 0
    interf_n = interf_valid = interf_hit = 0
    for r in records:
        if r.score is not None and r.score not in (0, 1, 2):
            raise ValueError(f"integrity score must be 0, 1, 2 or None, got {r.score!r}")
        if r.is_interference:
            interf_n += 1
            if r.score is not None:
                interf_valid += 1
                interf_hit += r.score == 0
            continue
        n += 1
        w_all += r.importance
        if r.score is None:
            continue
        valid_n += 1
        w_valid += r.importance
        full += r.score == 2
        w_scores += 0.5 * r.score * r.importance
    return {
        "recall(all)": _ratio(full, n),
        "recall(valid)": _ratio(full, valid_n),
        "weighted_recall(all)": _ratio(w_scores, w_all),
        "weighted_recall(valid)": _ratio(w_scores, w_valid),
        "fmr(all)": _ratio(interf_hit, interf_n),
        "fmr(valid)": _ratio(interf_hit, interf_valid),
        "gold_points": n,
        "gold_points_valid": valid_n,
        "interference_points": interf_n,
        "interference_points_valid": interf_valid,
    }


def aggregate_accuracy(records: Iterable[AccuracyRecord]) -> dict[str, float | int | None]:
    n = valid_n = t_n = t_valid = 0
    t_scores = w_scores = 0.0
    for r in records:
        if r.score is not None and r.score not in (0, 1, 2):
            raise ValueError(f"accuracy score must be 0, 1, 2 or None, got {r.score!r}")
        n += 1
        t_n += r.included_in_golden
        if r.score is None:
            continue
        valid_n += 1
        w_scores += 0.5 * r.score
        if r.included_in_golden:
            t_scores += 0.5 * r.score
            t_valid += 1
    return {
        "target_precision(all)": _ratio(t_scores, t_n),
        "target_precision(valid)": _ratio(t_scores, t_valid),
        "weighted_accuracy(all)": _ratio(w_scores, n),
        "weighted_accuracy(valid)": _ratio(w_scores, valid_n),
        "extracted_memories": n,
        "extracted_memories_valid": valid_n,
        "target_memories": t_n,
        "target_memories_valid": t_valid,
    }


def aggregate_extraction(
    integrity: Sequence[IntegrityRecord], accuracy: Sequence[AccuracyRecord]
) -> dict[str, float | int | None]:
    """Full extraction block: recall, weighted recall, FMR, target precision, weighted accuracy, F1.

    With no extracted memories at all, target precision is undefined (``None``) and F1 is
    ``None``; the reference code would divide by zero there.
    """
    out = dict(aggregate_integrity(integrity))
    out.update(aggregate_accuracy(accuracy))
    out["f1"] = compute_f1(out["target_precision(all)"], out["recall(all)"])  # type: ignore[arg-type]
    return out


def _aggregate_labels(labels: Iterable[str | None], allowed: Sequence[str], prefix: str) -> dict[str, float | int | None]:
    labels = list(labels)
    n = len(labels)
    # The reference code treats any label outside the allowed set as invalid (kept in
    # the "all" denominator, dropped from "valid").
    valid = [x for x in labels if x in allowed]
    out: dict[str, float | int | None] = {f"{prefix}_num": n, f"{prefix}_valid_num": len(valid)}
    for lab in allowed:
        k = sum(x == lab for x in valid)
        out[f"{lab.lower()}_{prefix}_ratio(all)"] = _ratio(k, n)
        out[f"{lab.lower()}_{prefix}_ratio(valid)"] = _ratio(k, len(valid))
    return out


def aggregate_updates(labels: Iterable[str | None]) -> dict[str, float | int | None]:
    """Update metrics: share of Correct / Hallucination / Omission / Other over update points."""
    return _aggregate_labels(labels, UPDATE_LABELS, "update_memory")


def aggregate_qa(labels: Iterable[str | None]) -> dict[str, float | int | None]:
    """QA metrics: share of Correct / Hallucination / Omission over questions."""
    return _aggregate_labels(labels, QA_LABELS, "qa")


def apply_commit_decision(
    integrity: Sequence[IntegrityRecord],
    accuracy: Sequence[AccuracyRecord],
    committed: bool,
) -> tuple[list[IntegrityRecord], list[AccuracyRecord]]:
    """Score a session as the guard left it.

    ``integrity``/``accuracy`` are the judge records for the *unguarded* extraction of the
    session.  A committed session keeps its extraction unchanged.  A held session writes
    nothing, so, following the reference code's rule for empty extractions, every gold
    point scores 0 and there are no extracted memories to score.  A hold is therefore an
    omission and is charged to recall.
    """
    if committed:
        return list(integrity), list(accuracy)
    return [IntegrityRecord(r.source, r.importance, 0) for r in integrity], []


def per_session_summary(
    integrity: Sequence[IntegrityRecord], accuracy: Sequence[AccuracyRecord]
) -> Mapping[str, float | int | None]:
    """Convenience wrapper used for paired per-session statistics."""
    return aggregate_extraction(integrity, accuracy)
