"""Deterministic coverage, preservation, and faithfulness verification."""
from .graph import build_graph
from .schemas import Check, Result, Transition
from .serialization import textual_representation


def verify(t: Transition, representation: str = "graph") -> Result:
    """Run explicit annotation-based checks through graph or text input path.

    Both representations preserve identical data. These checks operate on the
    canonical transition to avoid pretending the toy graph has semantic power.
    """
    if representation not in {"graph", "text"}:
        raise ValueError("representation must be 'graph' or 'text'")
    t.validate()
    # Materialize both encodings; validate graph/text path is available.
    _ = build_graph(t) if representation == "graph" else textual_representation(t)
    candidate_claims = {c for r in t.candidate for c in r.claims}
    before_claims = {c for r in t.before for c in r.claims}
    evidence_claims = {c for e in t.evidence for c in e.supports}

    missing = sorted(set(t.required_claims) - candidate_claims)
    checks = [Check("coverage", not missing,
                    "all required claims retained" if not missing else "required claims omitted: " + "; ".join(missing), missing)]

    candidate_by_id = {r.id: r for r in t.candidate}
    before_by_id = {r.id: r for r in t.before}
    operations_by_target = {op.target_id: op for op in t.operations if op.target_id}
    damaged = []
    for rid, old in before_by_id.items():
        operation = operations_by_target.get(rid)
        if operation is None:
            new = candidate_by_id.get(rid)
            if new is None or not set(old.claims).issubset(new.claims):
                damaged.append(rid)
            continue
        if operation.type == "DELETE":
            continue  # An explicit, reasoned deletion is the authorization annotation.
        replacement = candidate_by_id[operation.record_id]
        old_claims, new_claims = set(old.claims), set(replacement.claims)
        added_claims = new_claims - old_claims
        evidence_backed_change = bool(added_claims) and added_claims.issubset(evidence_claims)
        if not old_claims.issubset(new_claims) and not evidence_backed_change:
            damaged.append(rid)
    checks.append(Check("preservation", not damaged,
                        "prior records preserved or explicitly changed with evidence" if not damaged else "valid prior records changed or removed without an evidenced operation: " + ", ".join(damaged), damaged))

    unsupported = sorted(candidate_claims - before_claims - evidence_claims)
    checks.append(Check("faithfulness", not unsupported,
                        "candidate claims supported by evidence or prior memory" if not unsupported else "unsupported claims: " + "; ".join(unsupported), unsupported))
    decision = "ACCEPT" if all(c.passed for c in checks) else "HOLD"
    return Result(t.case_id, decision, checks)
