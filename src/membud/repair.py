"""Bounded repair loop for HELD memory writes.

When the verifier HOLDs a proposed write, the extractor is given the reason codes and the
offending claims and asked for a corrected candidate, up to ``max_retries`` times (default 2).

Safety rules enforced here, not left to callers:

* A retry never authorizes a commit. The only path to ``final_decision == "ACCEPT"`` is that the
  *final* candidate passes ``verify`` and then passes a second, explicit re-verification.
* Every attempt is recorded (candidate, decision, reason codes, offending claims, any error).
* An extractor error or an empty candidate is a failed attempt, never an accept. An empty
  candidate would trivially verify; counting it as a success would hide the omission.
* If every attempt fails the write stays HELD and nothing is committed (``final_claims == ()``).
  A held write is an omission; callers must score it as one.

The module is pure: ``verify`` and ``extract_repair`` are injected, so it makes no model calls
and holds no dataset text of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

DEFAULT_MAX_RETRIES = 2
EMPTY_CANDIDATE = "EMPTY_CANDIDATE"
EXTRACTOR_ERROR = "EXTRACTOR_ERROR"
REVERIFY_FAILED = "REVERIFY_FAILED"


@dataclass(frozen=True)
class VerifyOutcome:
    accepted: bool
    reason_codes: tuple[str, ...] = ()
    offending_claims: tuple[str, ...] = ()


@dataclass(frozen=True)
class Attempt:
    index: int  # 0 = the original proposal, 1.. = repair attempts
    claims: tuple[str, ...]
    accepted: bool
    reason_codes: tuple[str, ...]
    offending_claims: tuple[str, ...]
    error: str = ""


@dataclass(frozen=True)
class RepairResult:
    attempts: tuple[Attempt, ...]
    final_decision: str  # "ACCEPT" | "HOLD"
    final_claims: tuple[str, ...]  # committed claims; empty unless ACCEPT
    initial_decision: str
    retries_used: int

    @property
    def recovered(self) -> bool:
        """The original proposal was HELD and a repaired candidate was committed."""
        return self.initial_decision == "HOLD" and self.final_decision == "ACCEPT"


Verifier = Callable[[Sequence[str]], VerifyOutcome]
Extractor = Callable[[Sequence[str], Sequence[str], Sequence[str], int], Sequence[str]]


def run_repair(
    initial_claims: Sequence[str],
    verify: Verifier,
    extract_repair: Extractor,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> RepairResult:
    """Verify ``initial_claims``; on HOLD, retry with feedback up to ``max_retries`` times.

    ``extract_repair(previous_claims, offending_claims, reason_codes, attempt_number)`` returns a
    new candidate claim list. It is told what was wrong but cannot influence the decision.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    current = tuple(initial_claims)
    out = verify(current)
    attempts = [Attempt(0, current, out.accepted, out.reason_codes, out.offending_claims)]
    if out.accepted:
        return RepairResult(tuple(attempts), "ACCEPT", current, "ACCEPT", 0)

    for n in range(1, max_retries + 1):
        try:
            proposed = tuple(extract_repair(current, out.offending_claims, out.reason_codes, n))
        except Exception as exc:  # an extractor failure is a failed attempt, not a commit
            attempts.append(Attempt(n, current, False, (EXTRACTOR_ERROR,), (), type(exc).__name__))
            continue
        if not proposed:
            attempts.append(Attempt(n, proposed, False, (EMPTY_CANDIDATE,), ()))
            continue
        out = verify(proposed)
        attempts.append(Attempt(n, proposed, out.accepted, out.reason_codes, out.offending_claims))
        current = proposed
        if out.accepted:
            # Re-verify the exact final candidate before authorizing anything.
            again = verify(proposed)
            if again.accepted:
                return RepairResult(tuple(attempts), "ACCEPT", proposed, "HOLD", n)
            attempts.append(Attempt(n, proposed, False, (REVERIFY_FAILED, *again.reason_codes), again.offending_claims))
            out = again
    return RepairResult(tuple(attempts), "HOLD", (), "HOLD", max_retries)


@dataclass(frozen=True)
class RepairStats:
    held: int
    recovered: int
    still_held: int
    retry_success_rate: float | None
    attempts_total: int


def summarize(results: Sequence[RepairResult]) -> RepairStats:
    """Retry success rate over proposals that were initially HELD."""
    held = [r for r in results if r.initial_decision == "HOLD"]
    rec = sum(r.recovered for r in held)
    return RepairStats(
        held=len(held),
        recovered=rec,
        still_held=len(held) - rec,
        retry_success_rate=None if not held else rec / len(held),
        attempts_total=sum(len(r.attempts) - 1 for r in held),
    )
