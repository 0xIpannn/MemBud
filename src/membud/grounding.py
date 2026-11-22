"""Pluggable host-side evidence grounding for natural memory proposals.

The verifier never invents evidence: the *host* decides which user turn supports
which claim. This module makes that policy an explicit interface so the same
recorded proposal can be judged under any grounder (strict, normalized, or a
future NLI/LLM one) without touching the verifier or re-calling a model.

Every grounder returns a :class:`GroundingReport`: the evidence items it
attached *and*, for each claim it could not ground, the reason why. A reason
separates "the claim is unsupported" from "the host policy could not find the
support", which the verifier's own HOLD cannot tell apart.

Claim semantics are unchanged: claims are still compared by exact string.
Normalization only affects how a claim is *matched to a turn*, never the claim
text that reaches the verifier.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import re

from .conversation import _content_tokens, _normalize_ws
from .schemas import Evidence

# Reason codes for claims left without evidence (stable strings; they are reported).
GROUNDED = "grounded"
ALREADY_SUPPORTED = "already_supported"
EMPTY_CLAIM_TOKENS = "empty_claim_tokens"
NUMERAL_NOT_IN_ANY_TURN = "numeral_not_in_any_turn"
NO_TOKEN_OVERLAP = "no_token_overlap"
NEEDS_MULTIPLE_TURNS = "needs_multiple_turns"
BELOW_THRESHOLD = "below_threshold"

UNGROUNDED_REASONS = (
    EMPTY_CLAIM_TOKENS,
    NUMERAL_NOT_IN_ANY_TURN,
    NO_TOKEN_OVERLAP,
    NEEDS_MULTIPLE_TURNS,
    BELOW_THRESHOLD,
)


@dataclass(frozen=True)
class GroundingSource:
    """A trusted source (one user turn) a claim may be grounded in."""

    ref: str
    text: str


@dataclass(frozen=True)
class ClaimGrounding:
    claim: str
    reason: str  # GROUNDED | ALREADY_SUPPORTED | one of UNGROUNDED_REASONS
    best_coverage: float = 0.0  # best single-source content-token coverage (0..1)
    union_coverage: float = 0.0  # coverage by all sources together (diagnostic only)
    source_ref: str = ""  # the source used when grounded

    @property
    def is_supported(self) -> bool:
        return self.reason in (GROUNDED, ALREADY_SUPPORTED)


@dataclass(frozen=True)
class GroundingReport:
    evidence: tuple[Evidence, ...]
    claims: tuple[ClaimGrounding, ...]

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.claims:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts

    def ungrounded(self) -> tuple[ClaimGrounding, ...]:
        return tuple(c for c in self.claims if not c.is_supported)


@runtime_checkable
class EvidenceGrounder(Protocol):
    """Host policy: attach evidence to claims, and say why when it cannot."""

    name: str

    def ground(
        self,
        claims: Sequence[str],
        sources: Sequence[GroundingSource],
        *,
        already_supported: frozenset[str] = frozenset(),
    ) -> GroundingReport: ...


_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _numbers_in_text(text: str, *, normalize: bool = False) -> set[str]:
    """Digit runs of ``text``.

    ``normalize=True`` (numeral normalization, opt-in) first drops thousands
    separators (``75,000`` -> ``75000``) and then strips leading zeros
    (``05`` -> ``5``), so ``1983-05-06`` and "May 6, 1983" agree. It never
    converts words or month names, and never changes the claim text.
    """
    if normalize:
        text = _THOUSANDS_RE.sub("", text)
        return {str(int(n)) for n in re.findall(r"\d+", text)}
    return set(re.findall(r"\b\d+\b", text))


def _fold_tokens(tokens: frozenset[str] | set[str], fold: bool) -> set[str]:
    if not fold:
        return set(tokens)
    return {
        tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok
        for tok in tokens
    }


@dataclass(frozen=True)
class CoverageGrounder:
    """Best-single-turn content-token coverage with verbatim-quote evidence.

    A claim is grounded in the single source with the highest coverage (earliest
    on ties) when coverage >= ``min_coverage`` and every numeral of the claim
    occurs in that source. ``min_coverage=1.0`` is the strict policy (every
    content token in one turn). Optional normalization N: ``persona_name`` drops
    the name tokens from claims that begin with that name (first-person speech),
    and ``fold_inflection`` folds a trailing ``-s`` on claim and source tokens. ``normalize_numerals`` compares
    numerals after dropping thousands separators and leading zeros.
    """

    min_coverage: float = 1.0
    persona_name: str | None = None
    fold_inflection: bool = False
    normalize_numerals: bool = False
    evidence_prefix: str = "nev"

    def __post_init__(self) -> None:
        if not 0.0 < self.min_coverage <= 1.0:
            raise ValueError("min_coverage must be in (0, 1]")

    @property
    def name(self) -> str:
        parts = [f"coverage@{self.min_coverage}"]
        if self.persona_name or self.fold_inflection:
            parts.append("N")
        if self.normalize_numerals:
            parts.append("numnorm")
        return "+".join(parts)

    def ground(
        self,
        claims: Sequence[str],
        sources: Sequence[GroundingSource],
        *,
        already_supported: frozenset[str] = frozenset(),
    ) -> GroundingReport:
        fold = self.fold_inflection
        name_tokens = (
            _fold_tokens(_content_tokens(self.persona_name), fold) if self.persona_name else set()
        )
        name_prefix = _normalize_ws(self.persona_name).lower() if self.persona_name else ""
        src_tokens = [_fold_tokens(_content_tokens(s.text), fold) for s in sources]
        nn = self.normalize_numerals
        src_numbers = [_numbers_in_text(s.text, normalize=nn) for s in sources]
        all_src_tokens: set[str] = set().union(*src_tokens) if src_tokens else set()

        by_source: dict[int, list[str]] = {}
        results: list[ClaimGrounding] = []
        for claim in dict.fromkeys(claims):
            if claim in already_supported:
                results.append(ClaimGrounding(claim, ALREADY_SUPPORTED))
                continue
            tokens = _fold_tokens(_content_tokens(claim), fold)
            if name_prefix and _normalize_ws(claim).lower().startswith(name_prefix):
                tokens = tokens - name_tokens
            if not tokens:
                results.append(ClaimGrounding(claim, EMPTY_CLAIM_TOKENS))
                continue
            numbers = _numbers_in_text(claim, normalize=nn)
            union_cov = len(tokens & all_src_tokens) / len(tokens)
            best_idx, best_cov, numeral_ok = -1, 0.0, False
            for idx, st in enumerate(src_tokens):
                if not numbers <= src_numbers[idx]:
                    continue
                numeral_ok = True
                cov = len(tokens & st) / len(tokens)
                if cov > best_cov:
                    best_idx, best_cov = idx, cov
            if best_idx >= 0 and best_cov >= self.min_coverage:
                by_source.setdefault(best_idx, []).append(claim)
                results.append(
                    ClaimGrounding(claim, GROUNDED, best_cov, union_cov, sources[best_idx].ref)
                )
                continue
            if not numeral_ok:
                reason = NUMERAL_NOT_IN_ANY_TURN
            elif best_cov == 0.0:
                reason = NO_TOKEN_OVERLAP
            elif union_cov >= self.min_coverage:
                reason = NEEDS_MULTIPLE_TURNS
            else:
                reason = BELOW_THRESHOLD
            results.append(ClaimGrounding(claim, reason, best_cov, union_cov))

        evidence = tuple(
            Evidence(
                id=f"{self.evidence_prefix}-{k}",
                supports=tuple(grounded_claims),
                source=sources[idx].ref,
                text=sources[idx].text,
                contradicts=(),
            )
            for k, (idx, grounded_claims) in enumerate(sorted(by_source.items()), start=1)
        )
        return GroundingReport(evidence=evidence, claims=tuple(results))


__all__ = [
    "ALREADY_SUPPORTED",
    "BELOW_THRESHOLD",
    "ClaimGrounding",
    "CoverageGrounder",
    "EMPTY_CLAIM_TOKENS",
    "EvidenceGrounder",
    "GROUNDED",
    "GroundingReport",
    "GroundingSource",
    "NEEDS_MULTIPLE_TURNS",
    "NO_TOKEN_OVERLAP",
    "NUMERAL_NOT_IN_ANY_TURN",
    "UNGROUNDED_REASONS",
]
