from membud.repair import (
    EMPTY_CANDIDATE,
    EXTRACTOR_ERROR,
    REVERIFY_FAILED,
    VerifyOutcome,
    run_repair,
    summarize,
)


def _verify(bad: set[str]):
    def v(claims):
        off = tuple(c for c in claims if c in bad)
        return VerifyOutcome(not off, ("UNSUPPORTED_CLAIM",) if off else (), off)
    return v


def test_clean_proposal_needs_no_retry():
    called = []
    r = run_repair(("a", "b"), _verify(set()), lambda *a: called.append(a) or ("x",))
    assert r.final_decision == "ACCEPT" and r.retries_used == 0 and not called and not r.recovered


def test_retry_removes_offending_claim_and_commits_after_reverify():
    seen = []

    def fix(prev, offenders, codes, n):
        seen.append((prev, offenders, codes, n))
        return tuple(c for c in prev if c not in offenders)

    r = run_repair(("a", "bad"), _verify({"bad"}), fix)
    assert r.recovered and r.final_claims == ("a",) and r.retries_used == 1
    assert seen == [(("a", "bad"), ("bad",), ("UNSUPPORTED_CLAIM",), 1)]
    assert [a.accepted for a in r.attempts] == [False, True]


def test_exhausted_retries_leave_write_held_and_commit_nothing():
    r = run_repair(("bad",), _verify({"bad"}), lambda prev, *_: ("bad", "more"), max_retries=2)
    assert r.final_decision == "HOLD" and r.final_claims == () and r.retries_used == 2
    assert len(r.attempts) == 3 and not r.recovered


def test_empty_candidate_is_not_a_success():
    r = run_repair(("bad",), _verify({"bad"}), lambda *a: (), max_retries=2)
    assert r.final_decision == "HOLD" and r.final_claims == ()
    assert all(a.reason_codes == (EMPTY_CANDIDATE,) for a in r.attempts[1:])


def test_extractor_error_is_a_failed_attempt():
    def boom(*a):
        raise RuntimeError("api down")

    r = run_repair(("bad",), _verify({"bad"}), boom, max_retries=1)
    assert r.final_decision == "HOLD" and r.attempts[1].reason_codes == (EXTRACTOR_ERROR,)
    assert r.attempts[1].error == "RuntimeError"


def test_a_verifier_that_flips_on_reverify_blocks_the_commit():
    state = {"n": 0}

    def flaky(claims):
        if claims == ("bad",):
            return VerifyOutcome(False, ("X",), ("bad",))
        state["n"] += 1
        return VerifyOutcome(state["n"] == 1, ("Y",), ("ok",))  # accepts once, then rejects

    r = run_repair(("bad",), flaky, lambda *a: ("ok",), max_retries=1)
    assert r.final_decision == "HOLD" and r.final_claims == ()
    assert any(REVERIFY_FAILED in a.reason_codes for a in r.attempts)


def test_summarize_counts_only_initially_held():
    ok = run_repair(("a",), _verify(set()), lambda *a: ("a",))
    rec = run_repair(("a", "bad"), _verify({"bad"}), lambda p, o, c, n: tuple(x for x in p if x not in o))
    held = run_repair(("bad",), _verify({"bad"}), lambda *a: ("bad",), max_retries=1)
    s = summarize([ok, rec, held])
    assert (s.held, s.recovered, s.still_held, s.attempts_total) == (2, 1, 1, 2)
    assert s.retry_success_rate == 0.5
    assert summarize([ok]).retry_success_rate is None
