"""Token-type-aware cost accounting with explicit provenance.

The provider used by this project returns token telemetry, not billed dollars.
This module therefore *estimates* cost from token counts and operator-supplied
or placeholder per-million-token rates.  Every estimate carries mandatory
provenance (``source``, ``date``, ``currency``, ``verified_billed: false``) and
``billing_data_available: false``; an estimate must never be reported as a bill.

Token classes
-------------
``input`` / ``output`` are the provider-reported prompt/completion tokens.
``cached`` tokens are assumed to be a subset of ``input`` when ``cached <=
input`` (the usual OpenAI/DeepSeek ``prompt_tokens`` includes cache hits), and
an additive class otherwise.  ``reasoning`` tokens are treated the same way
relative to ``output``.  This avoids double-billing in the common case; the
assumption is recorded in ``pricing["note"]``.

Placeholder range
-----------------
When no explicit rates are supplied, the default assumption is a flat
``$0.10-$2.00 USD per 1,000,000 tokens`` range.  The reported ``low_usd`` /
``high_usd`` are that range applied to accounted tokens and ``estimated_usd``
is its midpoint.  It is a planning envelope, not a price quote.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

DEFAULT_CURRENCY = "USD"
PLACEHOLDER_LOW_USD_PER_MTOK = 0.10
PLACEHOLDER_HIGH_USD_PER_MTOK = 2.00
PLACEHOLDER_SOURCE = "placeholder assumption: no provider quote or billing data recorded"
EXPLICIT_SOURCE = "operator-supplied per-million-token rates (no provider billing data)"
PRICING_NOTE = (
    "Provider billing data is unavailable. Cached-input tokens are treated as a "
    "subset of input tokens when cached <= input, and reasoning tokens as a subset "
    "of output tokens when reasoning <= output; when only one usage field is "
    "reported it falls back to the output rate for the missing class. Fallback "
    "tokens charged because a provider response reported no usage are priced at "
    "the output rate (conservative)."
)

_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "input": ("input_tokens", "prompt_tokens"),
    "output": ("output_tokens", "completion_tokens"),
    "cached": (
        "cached_tokens",
        "cached_input_tokens",
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
    ),
    "reasoning": (
        "reasoning_tokens",
        "reasoning_output_tokens",
    ),
    "total": ("total_tokens",),
}


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _first_usage_value(usage: Mapping[str, Any], aliases: Iterable[str]) -> int | None:
    for alias in aliases:
        value = _non_negative_int(usage.get(alias))
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class TokenCounts:
    """Token counts for one row or an aggregate, by billing class."""

    input: int = 0
    output: int = 0
    cached: int = 0
    reasoning: int = 0
    total_reported: int = 0

    def __post_init__(self) -> None:
        for name in ("input", "output", "cached", "reasoning", "total_reported"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} tokens must be a non-negative integer")

    def __add__(self, other: "TokenCounts") -> "TokenCounts":
        if not isinstance(other, TokenCounts):
            return NotImplemented
        return TokenCounts(
            input=self.input + other.input,
            output=self.output + other.output,
            cached=self.cached + other.cached,
            reasoning=self.reasoning + other.reasoning,
            total_reported=self.total_reported + other.total_reported,
        )

    @property
    def reported_total(self) -> int:
        """Provider-level total for the two main classes (input + output)."""
        return self.input + self.output

    def to_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "output": self.output,
            "cached": self.cached,
            "reasoning": self.reasoning,
            "total_reported": self.total_reported,
        }


def extract_token_counts(usage: Mapping[str, Any] | None) -> TokenCounts:
    """Extract billing-class token counts from a Result ``usage`` block."""
    if not isinstance(usage, Mapping):
        return TokenCounts()
    return TokenCounts(
        input=_first_usage_value(usage, _TOKEN_ALIASES["input"]) or 0,
        output=_first_usage_value(usage, _TOKEN_ALIASES["output"]) or 0,
        cached=_first_usage_value(usage, _TOKEN_ALIASES["cached"]) or 0,
        reasoning=_first_usage_value(usage, _TOKEN_ALIASES["reasoning"]) or 0,
        total_reported=_first_usage_value(usage, _TOKEN_ALIASES["total"]) or 0,
    )


def sum_token_counts(items: Iterable[TokenCounts]) -> TokenCounts:
    """Sum token counts across rows."""
    total = TokenCounts()
    for item in items:
        total = total + item
    return total


# ---------------------------------------------------------------------------
# Price table and provenance
# ---------------------------------------------------------------------------


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _validated_rate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number of currency units per 1M tokens")
    rate = float(value)
    if not math.isfinite(rate) or rate < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return rate


def _validated_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"PriceTable requires a non-empty {name} for provenance")
    return value.strip()


@dataclass(frozen=True)
class PriceTable:
    """Per-million-token rates with mandatory, non-negotiable provenance.

    ``verified_billed`` must remain ``False``: the provider does not return
    billed cost, so a price table in this project is always an assumption or an
    operator-supplied quote, never a verified bill.
    """

    input_per_mtok: float
    output_per_mtok: float
    cached_per_mtok: float
    reasoning_per_mtok: float
    source: str
    date: str
    currency: str = DEFAULT_CURRENCY
    verified_billed: bool = False
    placeholder: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_per_mtok", _validated_rate(self.input_per_mtok, "input_per_mtok")
        )
        object.__setattr__(
            self, "output_per_mtok", _validated_rate(self.output_per_mtok, "output_per_mtok")
        )
        object.__setattr__(
            self, "cached_per_mtok", _validated_rate(self.cached_per_mtok, "cached_per_mtok")
        )
        object.__setattr__(
            self, "reasoning_per_mtok", _validated_rate(self.reasoning_per_mtok, "reasoning_per_mtok")
        )
        object.__setattr__(self, "source", _validated_text(self.source, "source"))
        object.__setattr__(self, "date", _validated_text(self.date, "date"))
        currency = _validated_text(self.currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("PriceTable currency must be a 3-letter currency code such as USD")
        object.__setattr__(self, "currency", currency)
        if self.verified_billed is not False:
            raise ValueError(
                "verified_billed must be False: the provider does not return billed "
                "cost, so no price table in this project may claim verified billing"
            )
        if not isinstance(self.placeholder, bool):
            raise ValueError("placeholder must be a boolean")

    def rates_per_mtok(self) -> dict[str, float]:
        return {
            "input": self.input_per_mtok,
            "output": self.output_per_mtok,
            "cached": self.cached_per_mtok,
            "reasoning": self.reasoning_per_mtok,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_per_mtok": self.input_per_mtok,
            "output_per_mtok": self.output_per_mtok,
            "cached_per_mtok": self.cached_per_mtok,
            "reasoning_per_mtok": self.reasoning_per_mtok,
            "source": self.source,
            "date": self.date,
            "currency": self.currency,
            "verified_billed": self.verified_billed,
            "placeholder": self.placeholder,
        }

    def cost_usd(self, tokens: TokenCounts, *, fallback_tokens: int = 0) -> float:
        """Estimate USD for one or more rows under these rates.

        Returns an unrounded value in ``currency``.
        """
        if not isinstance(tokens, TokenCounts):
            raise TypeError("tokens must be a TokenCounts instance")
        fallback = _non_negative_int(fallback_tokens) or 0
        if tokens.cached <= tokens.input:
            billed_cached = tokens.cached
            billed_uncached_input = tokens.input - tokens.cached
        else:
            billed_cached = tokens.cached
            billed_uncached_input = tokens.input
        if tokens.reasoning <= tokens.output:
            billed_reasoning = tokens.reasoning
            billed_other_output = tokens.output - tokens.reasoning
        else:
            billed_reasoning = tokens.reasoning
            billed_other_output = tokens.output
        cost = (
            billed_uncached_input * self.input_per_mtok
            + billed_cached * self.cached_per_mtok
            + billed_other_output * self.output_per_mtok
            + billed_reasoning * self.reasoning_per_mtok
            + fallback * self.output_per_mtok
        ) / 1_000_000
        return cost


_CLAUDE_HAIKU_4_5_RATES: dict[str, Any] = {
    "input_per_mtok": 1.00,
    "output_per_mtok": 5.00,
    "cached_per_mtok": 0.10,
    "source": "Anthropic published API pricing for Claude Haiku 4.5 (not a provider bill)",
}
_CLAUDE_SONNET_4_5_RATES: dict[str, Any] = {
    "input_per_mtok": 3.00,
    "output_per_mtok": 15.00,
    "cached_per_mtok": 0.30,
    "source": "Anthropic published API pricing for Claude Sonnet 4.5 (not a provider bill)",
}

# Published per-1M-token reference rates (still estimates; verified_billed=False).
PROVIDER_MODEL_PRICING: dict[str, dict[str, Any]] = {
    "claude-haiku-4-5-20251001": dict(_CLAUDE_HAIKU_4_5_RATES),
    "claude-haiku-4-5": dict(_CLAUDE_HAIKU_4_5_RATES),
    "claude-sonnet-4-5-20250929": dict(_CLAUDE_SONNET_4_5_RATES),
    "claude-sonnet-4-5": dict(_CLAUDE_SONNET_4_5_RATES),
    "gemini-2.5-flash": {
        "input_per_mtok": 0.30,
        "output_per_mtok": 2.50,
        "cached_per_mtok": 0.03,
        "source": "Google Gemini API published pricing for gemini-2.5-flash (not a provider bill)",
    },
    "gemini-2.5-pro": {
        "input_per_mtok": 1.25,
        "output_per_mtok": 10.00,
        "cached_per_mtok": 0.125,
        "source": "Google Gemini API published pricing for gemini-2.5-pro (not a provider bill)",
    },
}


def price_tables_for_model(
    model: str,
    *,
    date: str | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> tuple[PriceTable, PriceTable]:
    """Return ``(low, high)`` PriceTables for a known provider model or placeholder."""
    key = model.strip().lower()
    spec = PROVIDER_MODEL_PRICING.get(key)
    if spec is None and "/" in key:
        _, bare = key.split("/", 1)
        spec = PROVIDER_MODEL_PRICING.get(bare)
    if spec is None:
        return build_price_tables(date=date, currency=currency)
    return build_price_tables(
        input_per_mtok=spec["input_per_mtok"],
        output_per_mtok=spec["output_per_mtok"],
        cached_per_mtok=spec["cached_per_mtok"],
        source=spec["source"],
        date=date,
        currency=currency,
    )


def build_price_tables(
    *,
    input_per_mtok: float | None = None,
    output_per_mtok: float | None = None,
    cached_per_mtok: float | None = None,
    source: str | None = None,
    date: str | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> tuple[PriceTable, PriceTable]:
    """Build ``(low, high)`` price tables from CLI-style inputs.

    * No explicit input/output rate -> placeholder range ``$0.10-$2.00/M``.
    * Both explicit rates -> point estimate (``low == high``); ``cached``
      defaults to the input rate, ``reasoning`` defaults to the output rate.
    """
    source_text = (source or "").strip()
    date_text = (date or "").strip() or _utc_today()
    explicit = input_per_mtok is not None or output_per_mtok is not None
    if not explicit:
        if cached_per_mtok is not None:
            raise ValueError(
                "cached pricing requires explicit --price-input-per-mtok and "
                "--price-output-per-mtok"
            )
        low = PriceTable(
            input_per_mtok=PLACEHOLDER_LOW_USD_PER_MTOK,
            output_per_mtok=PLACEHOLDER_LOW_USD_PER_MTOK,
            cached_per_mtok=PLACEHOLDER_LOW_USD_PER_MTOK,
            reasoning_per_mtok=PLACEHOLDER_LOW_USD_PER_MTOK,
            source=source_text or PLACEHOLDER_SOURCE,
            date=date_text,
            currency=currency,
            placeholder=True,
        )
        high = PriceTable(
            input_per_mtok=PLACEHOLDER_HIGH_USD_PER_MTOK,
            output_per_mtok=PLACEHOLDER_HIGH_USD_PER_MTOK,
            cached_per_mtok=PLACEHOLDER_HIGH_USD_PER_MTOK,
            reasoning_per_mtok=PLACEHOLDER_HIGH_USD_PER_MTOK,
            source=source_text or PLACEHOLDER_SOURCE,
            date=date_text,
            currency=currency,
            placeholder=True,
        )
        return low, high
    if input_per_mtok is None or output_per_mtok is None:
        raise ValueError(
            "supply both --price-input-per-mtok and --price-output-per-mtok, or neither"
        )
    input_rate = _validated_rate(input_per_mtok, "input_per_mtok")
    output_rate = _validated_rate(output_per_mtok, "output_per_mtok")
    cached_rate = input_rate if cached_per_mtok is None else _validated_rate(
        cached_per_mtok, "cached_per_mtok"
    )
    table = PriceTable(
        input_per_mtok=input_rate,
        output_per_mtok=output_rate,
        cached_per_mtok=cached_rate,
        reasoning_per_mtok=output_rate,
        source=source_text or EXPLICIT_SOURCE,
        date=date_text,
        currency=currency,
        placeholder=False,
    )
    return table, table


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostEstimate:
    """An explicitly-labelled cost estimate (never a bill)."""

    estimated_usd: float
    low_usd: float
    high_usd: float
    currency: str
    is_range: bool
    basis: str
    tokens: dict[str, int]
    pricing: dict[str, Any]
    billing_data_available: bool = False
    verified_billed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON payload, keeping the legacy ``estimated_cost_range_usd`` fields."""
        payload: dict[str, Any] = {
            "estimated_usd": self.estimated_usd,
            "low_usd": self.low_usd,
            "high_usd": self.high_usd,
            "currency": self.currency,
            "is_range": self.is_range,
            "estimated": True,
            "billing_data_available": False,
            "verified_billed": False,
            "accounted_tokens": self.tokens.get("accounted", 0),
            "tokens": dict(self.tokens),
            "basis": self.basis,
            "pricing": dict(self.pricing),
        }
        if self.pricing.get("placeholder"):
            payload["assumption"] = {
                "low_usd_per_million_tokens": PLACEHOLDER_LOW_USD_PER_MTOK,
                "high_usd_per_million_tokens": PLACEHOLDER_HIGH_USD_PER_MTOK,
                "note": self.basis,
            }
        else:
            payload["assumption"] = {
                "rate_basis": "explicit_per_mtok",
                "source": self.pricing.get("source"),
                "date": self.pricing.get("date"),
                "note": self.basis,
            }
        return payload


def pricing_dict(low: PriceTable, high: PriceTable) -> dict[str, Any]:
    """Provenance block describing the price assumption or quote in use."""
    if not isinstance(low, PriceTable) or not isinstance(high, PriceTable):
        raise TypeError("pricing_dict requires two PriceTable instances")
    if low.currency != high.currency:
        raise ValueError("low/high PriceTables must share a currency")
    placeholder = bool(low.placeholder and high.placeholder)
    sources = [low.source] if low.source == high.source else [low.source, high.source]
    dates = [low.date] if low.date == high.date else [low.date, high.date]
    return {
        "source": " / ".join(dict.fromkeys(sources)),
        "date": " / ".join(dict.fromkeys(dates)),
        "currency": low.currency,
        "verified_billed": False,
        "billing_data_available": False,
        "placeholder": placeholder,
        "rate_basis": "placeholder_range" if low != high else "explicit_per_mtok",
        "low_rates_per_mtok": low.rates_per_mtok(),
        "high_rates_per_mtok": high.rates_per_mtok(),
        "note": PRICING_NOTE,
    }


def estimate_cost(
    tokens: TokenCounts | Mapping[str, Any] | None,
    price_tables: tuple[PriceTable, PriceTable],
    *,
    accounted_tokens: int | None = None,
) -> CostEstimate:
    """Estimate cost for token counts under a ``(low, high)`` price pair.

    ``accounted_tokens`` is the run's conservative token ledger (reported plus
    estimated fallback tokens).  Any tokens beyond the reported input/output
    counts are treated as fallback tokens and priced at the output rate so a
    failure is never under-charged.
    """
    if not (
        isinstance(price_tables, tuple)
        and len(price_tables) == 2
        and all(isinstance(table, PriceTable) for table in price_tables)
    ):
        raise TypeError("price_tables must be a (low, high) tuple of PriceTable")
    low_table, high_table = price_tables
    counts = tokens if isinstance(tokens, TokenCounts) else extract_token_counts(tokens)
    if accounted_tokens is not None:
        fallback = max(0, int(accounted_tokens) - counts.input - counts.output)
    else:
        fallback = max(0, counts.total_reported - counts.input - counts.output)
    low_usd = round(low_table.cost_usd(counts, fallback_tokens=fallback), 6)
    high_usd = round(high_table.cost_usd(counts, fallback_tokens=fallback), 6)
    if high_usd < low_usd:
        low_usd, high_usd = high_usd, low_usd
    is_range = low_table != high_table
    estimated_usd = round((low_usd + high_usd) / 2, 6) if is_range else low_usd
    accounted = (
        int(accounted_tokens)
        if accounted_tokens is not None
        else counts.input + counts.output + fallback
    )
    if is_range:
        basis = (
            f"Estimated cost RANGE, not a billed amount: no provider billing data is "
            f"available, so the documented placeholder ${PLACEHOLDER_LOW_USD_PER_MTOK:.2f}-"
            f"${PLACEHOLDER_HIGH_USD_PER_MTOK:.2f} {low_table.currency} per 1,000,000 tokens "
            f"is applied to {accounted} accounted tokens; estimated_usd is the midpoint "
            "of that range. Planning estimate only."
        )
    else:
        basis = (
            "Estimated cost, not a billed amount: computed from the operator-supplied "
            f"per-1,000,000-token rates ({low_table.source}, {low_table.date}); the "
            "provider does not return billed cost, so this is an estimate only."
        )
    return CostEstimate(
        estimated_usd=estimated_usd,
        low_usd=low_usd,
        high_usd=high_usd,
        currency=low_table.currency,
        is_range=is_range,
        basis=basis,
        tokens={
            **counts.to_dict(),
            "fallback": fallback,
            "accounted": accounted,
        },
        pricing=pricing_dict(low_table, high_table),
    )


def estimate_usage_cost(
    usage: Mapping[str, Any] | None,
    price_tables: tuple[PriceTable, PriceTable],
    *,
    accounted_tokens: int | None = None,
) -> dict[str, Any]:
    """Convenience wrapper returning the JSON payload for one result usage block."""
    return estimate_cost(
        extract_token_counts(usage), price_tables, accounted_tokens=accounted_tokens
    ).to_dict()


__all__ = [
    "DEFAULT_CURRENCY",
    "EXPLICIT_SOURCE",
    "PLACEHOLDER_HIGH_USD_PER_MTOK",
    "PLACEHOLDER_LOW_USD_PER_MTOK",
    "PLACEHOLDER_SOURCE",
    "PRICING_NOTE",
    "PROVIDER_MODEL_PRICING",
    "CostEstimate",
    "PriceTable",
    "TokenCounts",
    "build_price_tables",
    "estimate_cost",
    "estimate_usage_cost",
    "extract_token_counts",
    "price_tables_for_model",
    "pricing_dict",
    "sum_token_counts",
]
