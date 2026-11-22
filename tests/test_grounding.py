import pytest

from membud import grounding as g
from membud.grounding import CoverageGrounder, EvidenceGrounder, GroundingSource

SRC = [
    GroundingSource("t0", "I play the cello every day"),
    GroundingSource("t1", "We moved to Paris in 2019"),
    GroundingSource("t2", "My sister works at a bakery"),
]


def reasons(grounder, claims, already=frozenset()):
    rep = grounder.ground(claims, SRC, already_supported=already)
    return {c.claim: c.reason for c in rep.claims}, rep


def test_grounder_satisfies_protocol_and_name():
    assert isinstance(CoverageGrounder(), EvidenceGrounder)
    assert CoverageGrounder(0.6).name == "coverage@0.6"
    assert CoverageGrounder(0.6, fold_inflection=True).name.endswith("+N")
    with pytest.raises(ValueError):
        CoverageGrounder(0.0)


def test_grounded_claim_gets_verbatim_evidence_and_ids():
    r, rep = reasons(CoverageGrounder(1.0), ["play the cello every day"])
    assert r == {"play the cello every day": g.GROUNDED}
    ev = rep.evidence[0]
    assert ev.id == "nev-1" and ev.source == "t0" and ev.text == SRC[0].text
    assert ev.supports == ("play the cello every day",)


def test_every_unsupported_reason_is_reported():
    r, _ = reasons(
        CoverageGrounder(0.8),
        [
            "of the",  # no content tokens
            "moved to Paris in 2021",  # numeral absent from every turn
            "owns a spaceship",  # no overlap
            "play the cello and works at a bakery",  # spread over two turns
            "play the cello every night in Rome",  # one turn, too little coverage
        ],
    )
    assert r["of the"] == g.EMPTY_CLAIM_TOKENS
    assert r["moved to Paris in 2021"] == g.NUMERAL_NOT_IN_ANY_TURN
    assert r["owns a spaceship"] == g.NO_TOKEN_OVERLAP
    assert r["play the cello and works at a bakery"] == g.NEEDS_MULTIPLE_TURNS
    assert r["play the cello every night in Rome"] == g.BELOW_THRESHOLD


def test_already_supported_is_kept_and_not_regrounded():
    r, rep = reasons(CoverageGrounder(1.0), ["play the cello every day"], already=frozenset({"play the cello every day"}))
    assert r == {"play the cello every day": g.ALREADY_SUPPORTED}
    assert rep.evidence == () and rep.ungrounded() == ()


def test_normalization_N_is_opt_in():
    claim = "Alice plays the cello every day"
    assert reasons(CoverageGrounder(1.0), [claim])[0][claim] != g.GROUNDED
    n = CoverageGrounder(1.0, persona_name="Alice", fold_inflection=True)
    assert reasons(n, [claim])[0][claim] == g.GROUNDED


def test_reason_counts_and_duplicates_collapse():
    rep = CoverageGrounder(1.0).ground(["owns a spaceship", "owns a spaceship"], SRC)
    assert rep.reason_counts() == {g.NO_TOKEN_OVERLAP: 1}
    assert len(rep.ungrounded()) == 1


def test_ground_transition_matches_legacy_wrapper():
    from membud.halumem import ground_transition, rederive_evidence_normalized
    from tests.test_halumem import _scope_session, _write_only  # reuse fixtures

    session = _scope_session()
    tr = session.to_transcript(user_uuid="u")
    t = _write_only("Alice plays the cello every day in Paris")
    new, rep = ground_transition(t, tr, CoverageGrounder(0.6))
    assert new == rederive_evidence_normalized(t, tr, min_coverage=0.6)
    assert rep.claims[0].reason == g.GROUNDED


def test_numeral_normalization_is_opt_in_and_conservative():
    from membud.grounding import _numbers_in_text

    assert _numbers_in_text("born 1983-05-06") == {"1983", "05", "06"}
    assert _numbers_in_text("born 1983-05-06", normalize=True) == {"1983", "5", "6"}
    assert _numbers_in_text("May 6, 1983", normalize=True) == {"6", "1983"}
    assert _numbers_in_text("earns $75,000 and 75000", normalize=True) == {"75000"}
    assert _numbers_in_text("pay 1,5 and 12,34", normalize=True) == {"1", "5", "12", "34"}  # not thousands
    assert _numbers_in_text("2026", normalize=True) == {"2026"}


def test_grounder_numnorm_grounds_iso_date_restatement_only_when_enabled():
    src = [GroundingSource("t0", "I was born on 1983-05-06 in San Diego")]
    claim = ["born May 6, 1983 in San Diego"]
    off = CoverageGrounder(0.6).ground(claim, src)
    on = CoverageGrounder(0.6, normalize_numerals=True).ground(claim, src)
    assert off.claims[0].reason == g.NUMERAL_NOT_IN_ANY_TURN
    assert on.claims[0].reason == g.GROUNDED and on.evidence[0].text == src[0].text
    # a different date still fails: normalization never invents a match
    bad = CoverageGrounder(0.6, normalize_numerals=True).ground(["born May 7, 1983 in San Diego"], src)
    assert bad.claims[0].reason == g.NUMERAL_NOT_IN_ANY_TURN
    assert CoverageGrounder(0.6, normalize_numerals=True).name.endswith("numnorm")


def test_judge_grounder_builds_verbatim_evidence_and_rejects_uncited_support():
    from membud.grounding import JudgeGrounder, JudgeVerdict

    def judge(claim, sources):
        if "cello" in claim:
            return JudgeVerdict(True, (0, 99))  # 99 is not a real source: ignored
        if "bakery" in claim:
            return JudgeVerdict(True, ())  # supported but nothing citable
        return JudgeVerdict(False)

    rep = JudgeGrounder(judge).ground(
        ["enjoys music daily (cello)", "sister owns a bakery", "owns a spaceship", "known"],
        SRC,
        already_supported=frozenset({"known"}),
    )
    r = {c.claim: c.reason for c in rep.claims}
    assert r["enjoys music daily (cello)"] == g.GROUNDED
    assert r["sister owns a bakery"] == g.JUDGE_INVALID_CITATION
    assert r["owns a spaceship"] == g.JUDGE_UNSUPPORTED
    assert r["known"] == g.ALREADY_SUPPORTED
    assert len(rep.evidence) == 1 and rep.evidence[0].text == SRC[0].text
    assert rep.evidence[0].supports == ("enjoys music daily (cello)",)
