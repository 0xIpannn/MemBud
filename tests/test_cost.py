"""Tests for token-type-aware cost estimation and provenance.

No network or paid calls: everything is derived from token usage blocks and
explicit rate/provenance inputs.
"""
from __future__ import annotations

import json

import pytest

from membud.cost import (
    PLACEHOLDER_HIGH_USD_PER_MTOK,
    PLACEHOLDER_LOW_USD_PER_MTOK,
    PriceTable,
    TokenCounts,
    build_price_tables,
    estimate_cost,
    estimate_usage_cost,
    extract_token_counts,
    pricing_dict,
    sum_token_counts,
)

DATE = "2026-11-08"
SOURCE = "test quote"


def _explicit():
    return build_price_tables(
        input_per_mtok=0.5,
        output_per_mtok=1.5,
        cached_per_mtok=0.1,
        source=SOURCE,
        date=DATE,
    )


def _placeholder():
    return build_price_tables(date=DATE)


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def test_extract_token_counts_reported_fields():
    counts = extract_token_counts(
        {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}
    )
    assert counts == TokenCounts(input=100, output=40, total_reported=140)
    assert counts.reported_total == 140


def test_extract_token_counts_provider_aliases():
    counts = extract_token_counts(
        {
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "cached_tokens": 80,
            "reasoning_tokens": 10,
            "total_tokens": 250,
        }
    )
    assert counts.input == 200
    assert counts.output == 50
    assert counts.cached == 80
    assert counts.reasoning == 10
    assert counts.total_reported == 250


def test_extract_token_counts_deepseek_cache_alias():
    counts = extract_token_counts({"input_tokens": 10, "prompt_cache_hit_tokens": 6})
    assert counts.cached == 6


@pytest.mark.parametrize("usage", [None, {}, {"cache_hit": 1}, {"api_attempts": 2}])
def test_extract_token_counts_without_tokens_is_zero(usage):
    assert extract_token_counts(usage) == TokenCounts()


def test_extract_token_counts_ignores_invalid_values():
    counts = extract_token_counts(
        {"input_tokens": None, "output_tokens": -5, "total_tokens": True}
    )
    assert counts == TokenCounts()


def test_sum_token_counts():
    total = sum_token_counts(
        [
            TokenCounts(input=10, output=5, total_reported=15),
            TokenCounts(input=20, output=8, cached=4, total_reported=28),
        ]
    )
    assert total == TokenCounts(input=30, output=13, cached=4, total_reported=43)


def test_token_counts_reject_negative_or_bool():
    with pytest.raises(ValueError):
        TokenCounts(input=-1)
    with pytest.raises(ValueError):
        TokenCounts(output=True)


# ---------------------------------------------------------------------------
# PriceTable provenance
# ---------------------------------------------------------------------------


def test_price_table_requires_provenance():
    with pytest.raises(ValueError, match="source"):
        PriceTable(1, 1, 1, 1, source="", date=DATE)
    with pytest.raises(ValueError, match="date"):
        PriceTable(1, 1, 1, 1, source=SOURCE, date="")
    with pytest.raises(ValueError, match="currency"):
        PriceTable(1, 1, 1, 1, source=SOURCE, date=DATE, currency="")


def test_price_table_refuses_verified_billing_claim():
    with pytest.raises(ValueError, match="verified_billed must be False"):
        PriceTable(1, 1, 1, 1, source=SOURCE, date=DATE, verified_billed=True)


@pytest.mark.parametrize("rate", [-0.1, float("nan"), float("inf"), "1.0", True])
def test_price_table_rejects_invalid_rates(rate):
    with pytest.raises(ValueError):
        PriceTable(rate, 1, 1, 1, source=SOURCE, date=DATE)


def test_price_table_normalizes_currency():
    table = PriceTable(1, 1, 1, 1, source=SOURCE, date=DATE, currency="usd")
    assert table.currency == "USD"


# ---------------------------------------------------------------------------
# Price-table construction from CLI-style inputs
# ---------------------------------------------------------------------------


def test_placeholder_tables_are_a_documented_range():
    low, high = _placeholder()
    assert low.placeholder is True and high.placeholder is True
    assert low.rates_per_mtok() == {
        "input": PLACEHOLDER_LOW_USD_PER_MTOK,
        "output": PLACEHOLDER_LOW_USD_PER_MTOK,
        "cached": PLACEHOLDER_LOW_USD_PER_MTOK,
        "reasoning": PLACEHOLDER_LOW_USD_PER_MTOK,
    }
    assert high.rates_per_mtok()["output"] == PLACEHOLDER_HIGH_USD_PER_MTOK
    assert low.source and low.date == DATE
    assert low.verified_billed is False and high.verified_billed is False
    assert PLACEHOLDER_LOW_USD_PER_MTOK == 0.10
    assert PLACEHOLDER_HIGH_USD_PER_MTOK == 2.00


def test_explicit_tables_are_a_point_estimate():
    low, high = _explicit()
    assert low == high
    assert low.placeholder is False
    assert low.rates_per_mtok() == {
        "input": 0.5,
        "output": 1.5,
        "cached": 0.1,
        "reasoning": 1.5,  # defaults to the output rate
    }
    assert low.source == SOURCE and low.date == DATE


def test_explicit_tables_default_cached_to_input_rate():
    low, high = build_price_tables(input_per_mtok=2.0, output_per_mtok=8.0, date=DATE)
    assert low.cached_per_mtok == 2.0


def test_build_requires_both_explicit_rates():
    with pytest.raises(ValueError, match="both"):
        build_price_tables(input_per_mtok=1.0)
    with pytest.raises(ValueError, match="both"):
        build_price_tables(output_per_mtok=1.0)


def test_cached_rate_without_explicit_rates_is_rejected():
    with pytest.raises(ValueError, match="cached pricing requires"):
        build_price_tables(cached_per_mtok=0.1)


def test_placeholder_provenance_date_defaults_to_today():
    low, high = build_price_tables()
    assert low.date == high.date
    assert len(low.date) == 10


# ---------------------------------------------------------------------------
# Cost math
# ---------------------------------------------------------------------------


def test_cost_math_by_token_class():
    low, _ = _explicit()
    # cached is a subset of input and reasoning a subset of output:
    #   800k uncached input * 0.5/M = 0.40
    #   200k cached        * 0.1/M = 0.02
    #   900k other output  * 1.5/M = 1.35
    #   100k reasoning     * 1.5/M = 0.15
    tokens = TokenCounts(
        input=1_000_000,
        output=1_000_000,
        cached=200_000,
        reasoning=100_000,
        total_reported=2_000_000,
    )
    estimate = estimate_cost(tokens, _explicit())
    assert estimate.low_usd == pytest.approx(1.92)
    assert estimate.high_usd == pytest.approx(1.92)
    assert estimate.estimated_usd == pytest.approx(1.92)
    assert estimate.is_range is False
    assert estimate.tokens["cached"] == 200_000
    assert estimate.tokens["reasoning"] == 100_000


def test_placeholder_range_math():
    estimate = estimate_cost(
        TokenCounts(input=600_000, output=400_000, total_reported=1_000_000),
        _placeholder(),
        accounted_tokens=1_000_000,
    )
    assert estimate.low_usd == pytest.approx(0.10)
    assert estimate.high_usd == pytest.approx(2.00)
    assert estimate.estimated_usd == pytest.approx(1.05)  # range midpoint
    assert estimate.is_range is True
    assert estimate.tokens["accounted"] == 1_000_000


def test_zero_cost_cache_hit():
    # A cache hit reports no new token usage and is accounted at zero tokens.
    estimate = estimate_usage_cost(
        {"input_tokens": None, "output_tokens": None, "total_tokens": None, "cache_hit": 1},
        _explicit(),
        accounted_tokens=0,
    )
    assert estimate["estimated_usd"] == 0.0
    assert estimate["low_usd"] == 0.0
    assert estimate["high_usd"] == 0.0
    assert estimate["tokens"]["accounted"] == 0


def test_fallback_tokens_priced_at_output_rate():
    # No reported usage: the conservative 7500-token fallback is priced at the
    # (more expensive) output rate, never the input rate.
    low, high = build_price_tables(
        input_per_mtok=0.25, output_per_mtok=1.0, date=DATE
    )
    estimate = estimate_cost(TokenCounts(), (low, high), accounted_tokens=7_500)
    assert estimate.low_usd == pytest.approx(0.0075)
    assert estimate.high_usd == pytest.approx(0.0075)
    assert estimate.tokens["fallback"] == 7_500


def test_unclassified_total_tokens_are_priced_conservatively():
    # Only total_tokens reported: treated as fallback tokens at the output rate.
    low, high = build_price_tables(input_per_mtok=0.0, output_per_mtok=1.0, date=DATE)
    estimate = estimate_cost({"total_tokens": 1_000}, (low, high))
    assert estimate.low_usd == pytest.approx(0.001)
    assert estimate.tokens["fallback"] == 1_000


def test_aggregate_tokens_and_accounted_override():
    total = sum_token_counts(
        [
            extract_token_counts({"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
            extract_token_counts({"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}),
        ]
    )
    estimate = estimate_cost(total, _placeholder(), accounted_tokens=180)
    assert estimate.tokens["input"] == 150
    assert estimate.tokens["output"] == 30
    assert estimate.tokens["accounted"] == 180
    assert estimate.low_usd == pytest.approx(180 * 0.10 / 1_000_000)


def test_estimate_rejects_malformed_price_tables():
    with pytest.raises(TypeError):
        estimate_cost(TokenCounts(), "not-tables")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        estimate_cost(TokenCounts(), (build_price_tables()[0],))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Payload shape and provenance
# ---------------------------------------------------------------------------


def test_payload_records_provenance_and_never_claims_a_bill():
    payload = estimate_usage_cost(
        {"input_tokens": 1_000, "output_tokens": 500, "total_tokens": 1_500},
        _explicit(),
        accounted_tokens=1_500,
    )
    assert payload["estimated"] is True
    assert payload["billing_data_available"] is False
    assert payload["verified_billed"] is False
    assert payload["pricing"]["verified_billed"] is False
    assert payload["pricing"]["billing_data_available"] is False
    assert payload["pricing"]["source"] == SOURCE
    assert payload["pricing"]["date"] == DATE
    assert payload["pricing"]["currency"] == "USD"
    assert payload["pricing"]["rate_basis"] == "explicit_per_mtok"
    assert "not a billed amount" in payload["basis"]
    assert payload["assumption"]["rate_basis"] == "explicit_per_mtok"
    json.dumps(payload)  # JSON-serializable


def test_placeholder_payload_keeps_legacy_range_fields():
    payload = estimate_usage_cost(
        {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000},
        _placeholder(),
        accounted_tokens=1_000_000,
    )
    assert payload["low_usd"] == pytest.approx(0.10)
    assert payload["high_usd"] == pytest.approx(2.00)
    assert payload["estimated"] is True
    assert payload["billing_data_available"] is False
    assert payload["accounted_tokens"] == 1_000_000
    assert payload["assumption"]["low_usd_per_million_tokens"] == 0.10
    assert payload["assumption"]["high_usd_per_million_tokens"] == 2.00
    assert "not a billed amount" in payload["assumption"]["note"]
    assert payload["pricing"]["placeholder"] is True


def test_pricing_dict_marks_placeholder_vs_explicit():
    placeholder_pricing = pricing_dict(*_placeholder())
    assert placeholder_pricing["placeholder"] is True
    assert placeholder_pricing["rate_basis"] == "placeholder_range"
    assert placeholder_pricing["low_rates_per_mtok"]["input"] == 0.10
    assert placeholder_pricing["high_rates_per_mtok"]["input"] == 2.00

    explicit_pricing = pricing_dict(*_explicit())
    assert explicit_pricing["placeholder"] is False
    assert explicit_pricing["rate_basis"] == "explicit_per_mtok"
    assert explicit_pricing["low_rates_per_mtok"] == explicit_pricing["high_rates_per_mtok"]
    assert explicit_pricing["source"] == SOURCE


def test_pricing_dict_rejects_mixed_currencies():
    low = PriceTable(1, 1, 1, 1, source=SOURCE, date=DATE, currency="USD")
    high = PriceTable(1, 1, 1, 1, source=SOURCE, date=DATE, currency="EUR")
    with pytest.raises(ValueError, match="currency"):
        pricing_dict(low, high)
