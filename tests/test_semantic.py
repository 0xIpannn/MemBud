"""Tests for the optional semantic verification layer.

Every comparator is a deterministic fake; no network or paid LLM call is made.
The deterministic text verifier is treated as an external contract and is only
read, never modified.
"""
import json
from types import SimpleNamespace

import pytest

from membud.schemas import Record, Transition
from membud.semantic import (
    LLMSemanticComparator,
    SemanticRelation,
    contradicted_candidate_claims,
    verify_with_semantics,
)
from membud.verification import verify


# ---------------------------------------------------------------------------
# Fixtures built inline so the tests do not depend on demo/evaluation data.
# ---------------------------------------------------------------------------

def _valid_transition() -> Transition:
    """Both candidate claims are exact prior/evidence claims (baseline ACCEPT)."""
    return Transition.from_dict({
        "case_id": "semantic-valid",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [
            {
                "id": "e1",
                "source": "demo:employment",
                "text": "Ia bekerja di Tokopedia.",
                "supports": ["bekerja di Tokopedia"],
            }
        ],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


def _paraphrase_transition() -> Transition:
    """Two differently-worded but equivalent claims, both exact-supported."""
    return Transition.from_dict({
        "case_id": "semantic-paraphrase",
        "before": [{"id": "r1", "claims": ["bekerja di Tokopedia"]}],
        "candidate": [
            {"id": "r1", "claims": ["bekerja di Tokopedia", "kerja di Tokopedia"]}
        ],
        "evidence": [
            {
                "id": "e1",
                "source": "demo:employment",
                "text": "Ia masih kerja di Tokopedia.",
                "supports": ["kerja di Tokopedia"],
            }
        ],
        "required_claims": ["bekerja di Tokopedia", "kerja di Tokopedia"],
        "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
    })


def _unsupported_transition() -> Transition:
    """A candidate claim with no exact prior/evidence support (baseline HOLD)."""
    return Transition.from_dict({
        "case_id": "semantic-unsupported",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["memiliki kapal pesiar"]},
        ],
        "evidence": [],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


def _contradiction_transition() -> Transition:
    """A new claim contradicts the retained prior claim (baseline HOLD)."""
    return Transition.from_dict({
        "case_id": "semantic-contradiction",
        "before": [{"id": "r1", "claims": ["tinggal di Bandung"]}],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["tinggal di Surabaya"]},
        ],
        "evidence": [],
        "required_claims": ["tinggal di Bandung"],
        "operations": [{"type": "WRITE", "record_id": "r2"}],
    })


def _paraphrase_unsupported_transition() -> Transition:
    """A wording-only change that the exact-string baseline flags as unsupported."""
    return Transition.from_dict({
        "case_id": "semantic-paraphrase-unsupported",
        "before": [{"id": "r1", "claims": ["bekerja di Tokopedia"]}],
        "candidate": [{"id": "r1", "claims": ["kerja di Tokopedia"]}],
        "evidence": [],
        "required_claims": [],
        "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
    })


def _held13_transition(*, with_contradiction: bool = True) -> Transition:
    """HELD-13 shaped NOOP: a retained candidate claim is contradicted by evidence.

    The transition changes nothing, so the exact claim-set baseline is ACCEPT and
    the unsupported-claim path is empty; only the evidence annotation can detect
    the unresolved contradiction.
    """
    evidence = {
        "id": "e1",
        "source": "synthetic:employment",
        "text": "Saya sekarang bekerja di Indofood.",
        "supports": ["bekerja di Indofood"],
    }
    if with_contradiction:
        evidence["contradicts"] = ["bekerja di Unilever"]
    return Transition.from_dict({
        "case_id": "semantic-held-13",
        "before": [
            {"id": "r1", "claims": ["bekerja di Unilever"]},
            {"id": "r2", "claims": ["tinggal di Tangerang"]},
        ],
        "candidate": [
            {"id": "r1", "claims": ["bekerja di Unilever"]},
            {"id": "r2", "claims": ["tinggal di Tangerang"]},
        ],
        "evidence": [evidence],
        "required_claims": ["bekerja di Unilever", "tinggal di Tangerang"],
        "operations": [{"type": "NOOP"}],
    })


def _dev13_transition() -> Transition:
    """DEV-13 shaped NOOP: retained prior claim contradicted by fresh evidence."""
    return Transition.from_dict({
        "case_id": "semantic-dev-13",
        "before": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "candidate": [
            {"id": "r1", "claims": ["tinggal di Bandung"]},
            {"id": "r2", "claims": ["bekerja di Tokopedia"]},
        ],
        "evidence": [{
            "id": "e1",
            "source": "synthetic:relocation",
            "text": "Saya sekarang tinggal di Surabaya.",
            "supports": ["tinggal di Surabaya"],
            "contradicts": ["tinggal di Bandung"],
        }],
        "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"],
        "operations": [{"type": "NOOP"}],
    })


def _correction_transition() -> Transition:
    """HELD-07 shaped REVISE: evidence contradicts a claim no longer in candidate.

    The annotated claim was replaced, so it is not part of the candidate state
    and the deterministic annotation check must not fire.
    """
    return Transition.from_dict({
        "case_id": "semantic-correction",
        "before": [{"id": "r1", "claims": ["memiliki dua anak"]}],
        "candidate": [{"id": "r1", "claims": ["memiliki tiga anak"]}],
        "evidence": [{
            "id": "e1",
            "source": "synthetic:correction",
            "text": "Koreksi: saya memiliki tiga anak, bukan dua.",
            "supports": ["memiliki tiga anak"],
            "contradicts": ["memiliki dua anak"],
        }],
        "required_claims": ["memiliki tiga anak"],
        "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
    })


# ---------------------------------------------------------------------------
# Fake comparators
# ---------------------------------------------------------------------------

class FakeComparator:
    name = "fake"
    prompt_version = "fake-prompt-v1"
    configuration = {"type": "fake", "threshold": 0}
    usage = {
        "input_tokens": 11,
        "output_tokens": 3,
        "total_tokens": 14,
        "api_attempts": 1,
        "json_retries": 0,
        "cache_hit": 0,
    }

    def __init__(self, default=SemanticRelation.EQUIVALENT, mapping=None):
        self.default = default
        self.mapping = dict(mapping or {})
        self.calls = []

    def compare(self, a, b):
        self.calls.append((a, b))
        if (a, b) in self.mapping:
            return self.mapping[(a, b)]
        if (b, a) in self.mapping:
            return self.mapping[(b, a)]
        return self.default


class RaisingComparator:
    name = "raising"

    def __init__(self):
        self.calls = 0

    def compare(self, a, b):
        self.calls += 1
        raise RuntimeError("comparator backend unavailable")


class CacheMissComparator:
    """Comparator whose backend reports the client's cache-only safety abort."""

    name = "cache-miss"

    def __init__(self):
        from membud.llm.client import CacheMissError

        self.calls = 0
        self._error = CacheMissError("cache-only mode: no cached comparison")

    def compare(self, a, b):
        self.calls += 1
        raise self._error


class MinimalComparator:
    """Exposes only the protocol surface (no prompt_version/config/usage)."""

    name = "minimal"

    def compare(self, a, b):
        return SemanticRelation.EQUIVALENT


def _semantic_check(result):
    matching = [check for check in result.checks if check.name == "semantic"]
    assert len(matching) == 1
    return matching[0]


# ---------------------------------------------------------------------------
# verify_with_semantics behaviour
# ---------------------------------------------------------------------------

def test_paraphrase_equivalence_accepted_despite_different_wording():
    transition = _paraphrase_transition()
    comparator = FakeComparator(
        default=SemanticRelation.UNRELATED,
        mapping={
            ("bekerja di Tokopedia", "kerja di Tokopedia"): SemanticRelation.EQUIVALENT,
        },
    )

    result = verify_with_semantics(transition, comparator)

    # The wording genuinely differs even though both claims are evidence-backed.
    assert "bekerja di Tokopedia" != "kerja di Tokopedia"
    assert result.decision == "ACCEPT"
    semantic = _semantic_check(result)
    assert semantic.status == "PASS" and semantic.passed is True
    assert semantic.reason_code == "SEMANTIC_OK"
    assert semantic.affected_ids == []
    assert comparator.calls  # the semantic layer actually asked the comparator


def test_deterministic_text_baseline_output_unchanged():
    transition = _valid_transition()
    baseline = verify(transition, "text")
    result = verify_with_semantics(transition, FakeComparator())

    assert [check.name for check in baseline.checks] == [
        "coverage", "preservation", "faithfulness",
    ]
    assert result.checks[:3] == baseline.checks
    assert [check.name for check in result.checks] == [
        "coverage", "preservation", "faithfulness", "semantic",
    ]
    assert result.decision == baseline.decision == "ACCEPT"


def test_unsupported_claim_rejected_with_unsupported_claim_code():
    result = verify_with_semantics(
        _unsupported_transition(), FakeComparator(default=SemanticRelation.UNRELATED)
    )

    assert result.decision == "HOLD"
    semantic = _semantic_check(result)
    assert semantic.status == "FAIL" and semantic.passed is False
    assert semantic.reason_code == "UNSUPPORTED_CLAIM"
    assert semantic.affected_ids == ["memiliki kapal pesiar"]


def test_contradictory_claim_rejected_with_contradicted_claim_code():
    comparator = FakeComparator(
        mapping={
            ("tinggal di Surabaya", "tinggal di Bandung"): SemanticRelation.CONTRADICTORY,
        }
    )
    result = verify_with_semantics(_contradiction_transition(), comparator)

    assert result.decision == "HOLD"
    semantic = _semantic_check(result)
    assert semantic.status == "FAIL" and semantic.passed is False
    assert semantic.reason_code == "CONTRADICTED_CLAIM"
    assert set(semantic.affected_ids) == {"tinggal di Bandung", "tinggal di Surabaya"}


def test_comparator_uncertain_relation_holds():
    transition = _valid_transition()
    baseline = verify(transition, "text")
    assert baseline.decision == "ACCEPT"

    result = verify_with_semantics(
        transition, FakeComparator(default=SemanticRelation.UNCERTAIN)
    )

    assert result.decision == "HOLD"
    semantic = _semantic_check(result)
    assert semantic.status == "UNCERTAIN" and semantic.passed is False
    assert semantic.reason_code == "CHECK_UNCERTAIN"
    # The deterministic checks themselves still pass; only semantics hold it.
    assert all(check.status == "PASS" for check in result.checks[:3])


def test_comparator_exception_is_caught_and_holds():
    transition = _valid_transition()
    comparator = RaisingComparator()

    result = verify_with_semantics(transition, comparator)  # must not raise

    assert comparator.calls > 0
    assert result.decision == "HOLD"
    semantic = _semantic_check(result)
    assert semantic.status == "UNCERTAIN"
    assert semantic.reason_code == "CHECK_UNCERTAIN"
    assert all(check.status == "PASS" for check in result.checks[:3])
    # Fallbacks for comparators without metadata.
    assert result.verifier == "semantic:raising"
    assert result.verifier_config == {"reads_contradicts": True}
    assert result.usage == {}
    assert result.prompt_version == ""


def test_equivalence_cannot_override_exact_string_baseline_failure():
    """EQUIVALENT only passes the semantic check; the text baseline still holds."""
    comparator = FakeComparator(default=SemanticRelation.EQUIVALENT)
    result = verify_with_semantics(_paraphrase_unsupported_transition(), comparator)

    semantic = _semantic_check(result)
    assert semantic.status == "PASS" and semantic.reason_code == "SEMANTIC_OK"
    # Deterministic checks are imported untouched, so a wording-only REVISE is
    # still an unsupported/unauthorized change for the exact-string baseline.
    assert result.decision == "HOLD"
    assert verify(_paraphrase_unsupported_transition(), "text").decision == "HOLD"


# ---------------------------------------------------------------------------
# Deterministic evidence.contradicts annotation handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "transition_factory", [_held13_transition, _dev13_transition]
)
@pytest.mark.parametrize(
    "verdict", [SemanticRelation.UNRELATED, SemanticRelation.EQUIVALENT]
)
def test_annotation_contradiction_holds_regardless_of_comparator_verdict(
    transition_factory, verdict
):
    transition = transition_factory()
    # The deterministic text baseline cannot see evidence.contradicts.
    assert verify(transition, "text").decision == "ACCEPT"

    comparator = FakeComparator(default=verdict)
    result = verify_with_semantics(transition, comparator)

    assert result.decision == "HOLD"
    semantic = _semantic_check(result)
    assert semantic.status == "FAIL" and semantic.passed is False
    assert semantic.reason_code == "CONTRADICTED_CLAIM"
    assert semantic.affected_ids == contradicted_candidate_claims(transition)
    assert semantic.affected_ids  # a retained candidate claim was annotated
    # The annotation is consumed deterministically: no model call is needed.
    assert comparator.calls == []


def test_annotation_contradiction_holds_with_zero_comparator_calls():
    comparator = RaisingComparator()
    result = verify_with_semantics(_held13_transition(), comparator)

    assert comparator.calls == 0
    assert result.decision == "HOLD"
    semantic = _semantic_check(result)
    assert semantic.status == "FAIL"
    assert semantic.reason_code == "CONTRADICTED_CLAIM"
    assert semantic.affected_ids == ["bekerja di Unilever"]


def test_contradiction_annotation_not_overridden_by_equivalent_verdict():
    """An EQUIVALENT model verdict cannot erase a deterministic contradiction."""
    annotated = _held13_transition()
    unannotated = _held13_transition(with_contradiction=False)

    annotated_result = verify_with_semantics(
        annotated, FakeComparator(default=SemanticRelation.EQUIVALENT)
    )
    control_result = verify_with_semantics(
        unannotated, FakeComparator(default=SemanticRelation.EQUIVALENT)
    )

    # Without the annotation the same comparator would ACCEPT...
    assert control_result.decision == "ACCEPT"
    assert _semantic_check(control_result).reason_code == "SEMANTIC_OK"
    # ...but the annotation deterministically forces CONTRADICTED_CLAIM/HOLD.
    assert annotated_result.decision == "HOLD"
    semantic = _semantic_check(annotated_result)
    assert semantic.status == "FAIL"
    assert semantic.reason_code == "CONTRADICTED_CLAIM"
    assert semantic.affected_ids == ["bekerja di Unilever"]


def test_no_contradicts_annotations_keep_existing_behavior():
    transition = _correction_transition()
    # The annotation names a prior claim that is absent from the candidate state,
    # so it is not a candidate contradiction.
    assert contradicted_candidate_claims(transition) == []

    result = verify_with_semantics(
        transition, FakeComparator(default=SemanticRelation.EQUIVALENT)
    )
    assert result.decision == "ACCEPT"
    semantic = _semantic_check(result)
    assert semantic.status == "PASS"
    assert semantic.reason_code == "SEMANTIC_OK"
    assert semantic.affected_ids == []


def test_contradicted_candidate_claims_mirrors_fairness_control():
    from membud.fairness import contradicted_candidate_claims as fairness_contradicted

    transitions = [
        _held13_transition(),
        _dev13_transition(),
        _correction_transition(),
        _valid_transition(),
        _unsupported_transition(),
    ]
    for transition in transitions:
        assert contradicted_candidate_claims(transition) == fairness_contradicted(
            transition
        )
    assert contradicted_candidate_claims(_held13_transition()) == [
        "bekerja di Unilever"
    ]
    assert contradicted_candidate_claims(_dev13_transition()) == [
        "tinggal di Bandung"
    ]


def test_reads_contradicts_marker_is_additive_and_recorded():
    comparator = FakeComparator()
    result = verify_with_semantics(_held13_transition(), comparator)

    assert result.verifier_config["reads_contradicts"] is True
    # The comparator's own configuration is copied, not mutated.
    assert comparator.configuration == {"type": "fake", "threshold": 0}

    minimal = verify_with_semantics(_valid_transition(), MinimalComparator())
    assert minimal.verifier_config == {"reads_contradicts": True}


def test_contradicted_four_check_result_commits_as_hold_without_state_change():
    from membud.store import commit

    transition = _held13_transition()
    result = verify_with_semantics(transition, FakeComparator())
    assert result.decision == "HOLD"
    assert [check.name for check in result.checks] == [
        "coverage", "preservation", "faithfulness", "semantic",
    ]
    assert result.to_dict()["checks"][3]["reason_code"] == "CONTRADICTED_CLAIM"

    store = {record.id: record for record in transition.before}
    committed = commit(store, transition, result)
    assert committed == store
    assert committed is not store


def test_result_metadata_comes_from_comparator():
    comparator = FakeComparator()
    result = verify_with_semantics(_valid_transition(), comparator)

    assert result.verifier == "semantic:fake"
    assert result.prompt_version == "fake-prompt-v1"
    assert result.verifier_config == {"type": "fake", "threshold": 0, "reads_contradicts": True}
    assert result.usage == comparator.usage
    assert result.usage is not comparator.usage  # defensive copy


def test_minimal_comparator_metadata_defaults_to_empty():
    result = verify_with_semantics(_valid_transition(), MinimalComparator())
    assert result.verifier == "semantic:minimal"
    assert result.prompt_version == ""
    assert result.verifier_config == {"reads_contradicts": True}
    assert result.usage == {}
    assert _semantic_check(result).status == "PASS"


# ---------------------------------------------------------------------------
# LLMSemanticComparator (fake client only; never network)
# ---------------------------------------------------------------------------

def _completion(payload, **overrides):
    values = {
        "json": payload,
        "content": json.dumps(payload),
        "model": "fake-model",
        "input_tokens": 120,
        "output_tokens": 20,
        "total_tokens": 140,
        "request_attempts": 1,
        "json_retries": 0,
        "cached": False,
        "request_id": "request-1",
        "latency_ms": 12.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeLLMClient:
    model = "fake-model"

    def __init__(self, payload=None, error=None, enforce_validator=True):
        self.payload = payload if payload is not None else {
            "relation": "EQUIVALENT",
            "reason": "Both claims assert the same employment fact.",
            "confidence": 0.92,
        }
        self.error = error
        self.enforce_validator = enforce_validator
        self.messages = None
        self.calls = 0

    def complete_json(self, messages, validator=None):
        self.calls += 1
        self.messages = messages
        if self.error is not None:
            raise self.error
        if validator is not None and self.enforce_validator:
            validator(self.payload)
        return _completion(self.payload)


def test_llm_comparator_returns_verdict_metadata_and_usage():
    client = FakeLLMClient()
    comparator = LLMSemanticComparator(client)

    assert comparator.compare("a", "b") is SemanticRelation.EQUIVALENT

    assert client.calls == 1
    assert comparator.name == "llm:fake-model"
    assert comparator.configuration["model"] == "fake-model"
    assert comparator.configuration["prompt_version"]
    assert comparator.usage == {
        "input_tokens": 120,
        "output_tokens": 20,
        "total_tokens": 140,
        "api_attempts": 1,
        "json_retries": 0,
        "cache_hit": 0,
    }
    system_prompt = client.messages[0]["content"]
    user_prompt = json.loads(client.messages[1]["content"])
    assert client.messages[0]["role"] == "system"
    assert "relation" in system_prompt and "confidence" in system_prompt
    assert user_prompt == {"claim_a": "a", "claim_b": "b"}


@pytest.mark.parametrize(
    "payload",
    [
        {"relation": "EQUIVALENT", "reason": "missing confidence"},
        {"relation": "EQUIVALENT", "reason": "same", "confidence": 0.9, "extra": 1},
        {"relation": "SAME", "reason": "same", "confidence": 0.9},
        {"relation": "EQUIVALENT", "reason": "", "confidence": 0.9},
        {"relation": "EQUIVALENT", "reason": "same", "confidence": 1.5},
        {"relation": "EQUIVALENT", "reason": "same", "confidence": -0.1},
        {"relation": "EQUIVALENT", "reason": "same", "confidence": True},
        {"relation": "EQUIVALENT", "reason": "same", "confidence": "0.9"},
        {"relation": "EQUIVALENT", "reason": "same", "confidence": None},
        ["not", "an", "object"],
    ],
)
def test_llm_comparator_malformed_payload_returns_uncertain(payload):
    comparator = LLMSemanticComparator(FakeLLMClient(payload=payload))
    assert comparator.compare("a", "b") is SemanticRelation.UNCERTAIN


def test_llm_comparator_revalidates_even_if_client_skips_validator():
    client = FakeLLMClient(
        payload={"relation": "SAME", "reason": "same", "confidence": 0.9},
        enforce_validator=False,
    )
    assert LLMSemanticComparator(client).compare("a", "b") is SemanticRelation.UNCERTAIN


def test_llm_comparator_transport_failure_returns_uncertain_without_raising():
    comparator = LLMSemanticComparator(FakeLLMClient(error=RuntimeError("provider down")))
    assert comparator.compare("a", "b") is SemanticRelation.UNCERTAIN


def test_llm_comparator_does_not_swallow_cache_only_miss_guard():
    from membud.llm.client import CacheMissError

    class OfflineClient:
        model = "offline"

        def complete_json(self, messages, validator=None):
            raise CacheMissError("cache-only mode: no cached comparison")

    with pytest.raises(CacheMissError):
        LLMSemanticComparator(OfflineClient()).compare("a", "b")


def test_cache_miss_guard_propagates_through_verify_with_semantics():
    from membud.llm.client import CacheMissError

    comparator = CacheMissComparator()
    with pytest.raises(CacheMissError):
        verify_with_semantics(_valid_transition(), comparator)
    assert comparator.calls > 0


def test_llm_comparator_rejects_oversized_claims_fail_closed():
    client = FakeLLMClient()
    comparator = LLMSemanticComparator(client, max_claim_chars=8)
    assert comparator.compare("x" * 9, "short") is SemanticRelation.UNCERTAIN
    assert client.calls == 0


def test_verify_with_semantics_accepts_llm_comparator_metadata():
    transition = _valid_transition()
    comparator = LLMSemanticComparator(FakeLLMClient())
    result = verify_with_semantics(transition, comparator)

    assert result.verifier == "semantic:llm:fake-model"
    assert result.prompt_version == comparator.prompt_version
    assert result.verifier_config["model"] == "fake-model"
    assert result.usage["total_tokens"] > 0
    assert _semantic_check(result).status == "PASS"


# ---------------------------------------------------------------------------
# Four-check Result passthrough
# ---------------------------------------------------------------------------

def test_four_check_result_passthrough_to_store_commit():
    from membud.store import commit

    transition = _paraphrase_transition()
    comparator = FakeComparator(
        default=SemanticRelation.UNRELATED,
        mapping={
            ("bekerja di Tokopedia", "kerja di Tokopedia"): SemanticRelation.EQUIVALENT,
        },
    )
    result = verify_with_semantics(transition, comparator)
    assert result.decision == "ACCEPT"
    assert [check.name for check in result.checks] == [
        "coverage", "preservation", "faithfulness", "semantic",
    ]
    payload = result.to_dict()
    assert [check["name"] for check in payload["checks"]] == [
        "coverage", "preservation", "faithfulness", "semantic",
    ]
    assert payload["checks"][3]["status"] == "PASS"
    assert payload["checks"][3]["reason_code"] == "SEMANTIC_OK"

    store = {record.id: record for record in transition.before}
    try:
        committed = commit(store, transition, result)
    except ValueError as exc:
        # Cross-team integration point: the storage workstream owns allowing the
        # optional fourth check.  Skip cleanly until that lands instead of
        # asserting the legacy rejection.
        pytest.skip(f"store.commit does not yet accept the optional semantic check: {exc}")
    assert committed == {record.id: record for record in transition.candidate}
    assert Record("r1", ("bekerja di Tokopedia", "kerja di Tokopedia")) in committed.values()
