"""Deterministic and LLM-assisted transition verification."""
import json
from typing import Any

from .graph import build_graph
from .schemas import Check, Result, Transition
from .serialization import textual_representation

LLM_PROMPT_VERSION = "membud-transition-checks-v1"
_REQUIRED_CHECKS = ("coverage", "preservation", "faithfulness")
_CHECK_REASON_CODES = {
    "coverage": {"PASS": {"COVERAGE_OK"}, "FAIL": {"REQUIRED_CLAIM_OMITTED"}, "UNCERTAIN": {"CHECK_UNCERTAIN"}},
    "preservation": {"PASS": {"PRESERVATION_OK"}, "FAIL": {"PRIOR_RECORD_UNAUTHORIZED_CHANGE"}, "UNCERTAIN": {"CHECK_UNCERTAIN"}},
    "faithfulness": {"PASS": {"FAITHFULNESS_OK"}, "FAIL": {"UNSUPPORTED_CLAIM", "CONTRADICTED_CLAIM"}, "UNCERTAIN": {"CHECK_UNCERTAIN"}},
}
# LLM/transport failures produce UNCERTAIN checks with INVALID_VERIFIER_OUTPUT;
# that code is internal to the fallback and is never accepted from the model.


def verify(
    t: Transition,
    representation: str = "graph",
    *,
    prepared_input: Any | None = None,
) -> Result:
    """Run explicit annotation-based checks through graph or text input path.

    Both representations preserve identical data. These checks operate on the
    canonical transition to avoid pretending the toy graph has semantic power.
    """
    if representation not in {"graph", "text"}:
        raise ValueError("representation must be 'graph' or 'text'")
    t.validate()
    # Materialize both encodings; validate graph/text path is available.
    if prepared_input is None:
        _ = build_graph(t) if representation == "graph" else textual_representation(t)
    candidate_claims = {c for r in t.candidate for c in r.claims}
    before_claims = {c for r in t.before for c in r.claims}
    evidence_claims = {c for e in t.evidence for c in e.supports}

    missing = sorted(set(t.required_claims) - candidate_claims)
    checks = [Check("coverage", not missing,
                    "all required claims retained" if not missing else "required claims omitted: " + "; ".join(missing), missing,
                    reason_code="COVERAGE_OK" if not missing else "REQUIRED_CLAIM_OMITTED")]

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
                        "prior records preserved or explicitly changed with evidence" if not damaged else "valid prior records changed or removed without an evidenced operation: " + ", ".join(damaged), damaged,
                        reason_code="PRESERVATION_OK" if not damaged else "PRIOR_RECORD_UNAUTHORIZED_CHANGE"))

    unsupported = sorted(candidate_claims - before_claims - evidence_claims)
    checks.append(Check("faithfulness", not unsupported,
                        "candidate claims supported by evidence or prior memory" if not unsupported else "unsupported claims: " + "; ".join(unsupported), unsupported,
                        reason_code="FAITHFULNESS_OK" if not unsupported else "UNSUPPORTED_CLAIM"))
    decision = "ACCEPT" if all(c.passed for c in checks) else "HOLD"
    return Result(t.case_id, decision, checks)


def verify_with_llm(
    t: Transition,
    client: Any,
    representation: str = "graph",
    *,
    prepared_input: Any | None = None,
) -> Result:
    """Ask a JSON-mode chat model to assess the same three transition checks.

    Model/provider failures, malformed output, or an invalid check structure
    become UNCERTAIN checks and HOLD; they never authorize a commit. Graph and
    text calls share one prompt/policy and differ only in the serialized input.
    """
    if representation not in {"graph", "text"}:
        raise ValueError("representation must be 'graph' or 'text'")
    t.validate()
    if prepared_input is None:
        prepared_input = build_graph(t) if representation == "graph" else textual_representation(t)
    rendered_input = (
        json.dumps(prepared_input, ensure_ascii=False, sort_keys=True)
        if representation == "graph"
        else str(prepared_input)
    )
    system_prompt = (
        "You are a cautious memory-transition verifier. Evaluate coverage, preservation, and faithfulness "
        "using only the transition input. required_claims are verifier inputs. Do not infer missing graph edges "
        "or use external facts. An explicit operation alone does not authorize an unsupported change. "
        "If evidence conflicts without ordering, or the available input is insufficient to judge a check, "
        "mark it UNCERTAIN. A valid transition requires all three checks to PASS. Return only a JSON object. "
        "For every check include name, status (PASS, FAIL, UNCERTAIN), reason, reason_code, and affected_ids. "
        "Use reason codes: coverage PASS=COVERAGE_OK, FAIL=REQUIRED_CLAIM_OMITTED; "
        "preservation PASS=PRESERVATION_OK, FAIL=PRIOR_RECORD_UNAUTHORIZED_CHANGE; "
        "faithfulness PASS=FAITHFULNESS_OK, FAIL=UNSUPPORTED_CLAIM or CONTRADICTED_CLAIM. "
        "For all uncertain checks use reason_code=CHECK_UNCERTAIN. affected_ids should identify exact claims "
        "or record IDs; use an empty list if none."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Representation: {representation}\nTransition input:\n{rendered_input}",
        },
    ]

    def validate_payload(payload: dict[str, Any]) -> None:
        if set(payload) != {"checks"} or not isinstance(payload["checks"], list):
            raise ValueError("output must contain exactly one checks array")
        items = payload["checks"]
        if len(items) != len(_REQUIRED_CHECKS) or any(not isinstance(item, dict) for item in items):
            raise ValueError("checks must contain exactly three objects")
        names = [item.get("name") for item in items]
        if any(not isinstance(name, str) for name in names) or set(names) != set(_REQUIRED_CHECKS):
            raise ValueError("checks must contain exactly coverage, preservation, and faithfulness")
        for item in items:
            if set(item) != {"name", "status", "reason", "reason_code", "affected_ids"}:
                raise ValueError("check has missing or unexpected fields")
            name, status, reason_code = item["name"], item["status"], item["reason_code"]
            if status not in {"PASS", "FAIL", "UNCERTAIN"}:
                raise ValueError(f"invalid status for {name}")
            if not isinstance(reason_code, str) or reason_code not in _CHECK_REASON_CODES[name][status]:
                raise ValueError(f"invalid reason_code for {name}/{status}")
            if not isinstance(item["reason"], str) or not item["reason"].strip():
                raise ValueError(f"reason must be non-empty for {name}")
            if not isinstance(item["affected_ids"], list) or any(not isinstance(i, str) for i in item["affected_ids"]):
                raise ValueError(f"affected_ids must be a string list for {name}")

    model = str(getattr(client, "model", client.__class__.__name__))
    verifier_config = dict(getattr(client, "configuration", {}))
    try:
        completion = client.complete_json(messages, validator=validate_payload)
        items_by_name = {item["name"]: item for item in completion.json["checks"]}
        checks = [
            Check(
                name=name,
                passed=items_by_name[name]["status"] == "PASS",
                reason=items_by_name[name]["reason"],
                affected_ids=items_by_name[name]["affected_ids"],
                status=items_by_name[name]["status"],
                reason_code=items_by_name[name]["reason_code"],
            )
            for name in _REQUIRED_CHECKS
        ]
        usage = {
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "total_tokens": completion.total_tokens,
            "api_attempts": completion.request_attempts,
            "json_retries": completion.json_retries,
            "cache_hit": int(completion.cached),
        }
        verifier = completion.model
        latency_ms = completion.latency_ms
        raw_response = completion.json
        provider_request_id = completion.request_id
    except Exception as exc:
        checks = [
            Check(
                name=name,
                passed=False,
                reason=f"LLM verification unavailable or invalid: {exc}",
                status="UNCERTAIN",
                reason_code="INVALID_VERIFIER_OUTPUT",
            )
            for name in _REQUIRED_CHECKS
        ]
        attempts = getattr(exc, "attempts", None)
        usage = {"api_attempts": attempts, "cache_hit": 0} if isinstance(attempts, int) else {}
        verifier = model
        latency_ms = None
        raw_response = None
        provider_request_id = None
    decision = "ACCEPT" if all(check.status == "PASS" for check in checks) else "HOLD"
    return Result(
        t.case_id,
        decision,
        checks,
        verifier=verifier,
        prompt_version=LLM_PROMPT_VERSION,
        verifier_config=verifier_config,
        usage=usage,
        latency_ms=latency_ms,
        raw_response=raw_response,
        provider_request_id=provider_request_id,
    )
