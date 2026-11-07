"""Fair text-side verifier for the graph-vs-text verification ablation.

The plain text baseline in :mod:`membud.verification` compares exact claim sets
and never reads the ``Evidence.contradicts`` field. The structural graph
verifier, by contrast, derives ``CONTRADICTED_CLAIM`` failures from graph
``CONTRADICTS`` edges. Comparing those two implementations therefore conflates
two things: *representation* (graph adjacency vs claim sets) and *information
access* (contradiction annotations vs none).

:func:`verify_text_fair` is the fairness control that removes that confound. It
is the exact claim-set baseline *plus* the same contradiction information the
graph verifier consumes: if any claim in the candidate state also appears in an
``evidence.contradicts`` list, the faithfulness check fails with reason code
``CONTRADICTED_CLAIM`` and the sorted contradicted claim ids. When no candidate
claim is contradicted, the plain baseline result is returned unchanged (the very
same object), so any difference from ``verify_text`` is attributable only to the
added contradiction annotations.

This module deliberately does not import or call
:mod:`membud.graph_verification`: the control must remain an independent
text-side implementation. It is used by ``scripts/run_fairness_audit.py`` and is
not part of the production verification dispatch.
"""
from __future__ import annotations

from .schemas import Check, Result, Transition
from .verification import _verify_claim_sets

CONTRADICTION_REASON_CODE = "CONTRADICTED_CLAIM"


def contradicted_candidate_claims(t: Transition) -> list[str]:
    """Return sorted candidate claims that appear in any ``evidence.contradicts``."""
    candidate_claims = {claim for record in t.candidate for claim in record.claims}
    contradicted = {
        claim
        for evidence in t.evidence
        for claim in evidence.contradicts
        if claim in candidate_claims
    }
    return sorted(contradicted)


def verify_text_fair(t: Transition) -> Result:
    """Run the claim-set baseline with contradiction annotations added.

    This is the fairness control for the graph-vs-text ablation: it receives the
    same transition and the same contradiction information as the structural
    graph verifier, so a graph advantage over this control cannot be explained
    merely by the graph side seeing ``contradicts`` while the text side does not.

    The baseline coverage/preservation checks are untouched. Only faithfulness is
    overridden, and only when a claim present in the candidate state is also
    listed in an evidence ``contradicts`` annotation; otherwise the baseline
    result is returned unchanged.
    """
    baseline = _verify_claim_sets(t)
    contradicted = contradicted_candidate_claims(t)
    if not contradicted:
        return baseline

    checks: list[Check] = []
    for check in baseline.checks:
        if check.name != "faithfulness":
            checks.append(check)
            continue
        checks.append(
            Check(
                "faithfulness",
                False,
                "candidate claims contradicted by evidence: " + "; ".join(contradicted),
                contradicted,
                reason_code=CONTRADICTION_REASON_CODE,
            )
        )
    decision = "ACCEPT" if all(check.passed for check in checks) else "HOLD"
    return Result(t.case_id, decision, checks)
