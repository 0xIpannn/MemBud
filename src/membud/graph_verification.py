"""Structural verification derived from canonical graph adjacency.

Unlike the text baseline in :mod:`membud.verification` (which compares exact
claim sets), this verifier consumes the canonical graph and derives every
decision from node/edge structure: provenance paths from evidence to claims,
required-claim reachability, operation edges that authorize record removals and
changes, contradiction edges, and orphan/disconnected claim nodes.
"""
from __future__ import annotations

from collections import deque
from typing import Any

from .graph import build_graph
from .schemas import Check, Result, Transition

_RELATION_BY_TYPE = {"REVISE": "REVISES", "DELETE": "DELETES", "SUPERSEDE": "SUPERSEDES"}


def _outgoing(edges: list[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    outgoing: dict[str, list[tuple[str, str]]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append((edge["type"], edge["to"]))
    return outgoing


def _targets(outgoing: dict[str, list[tuple[str, str]]], node_id: str, edge_type: str) -> set[str]:
    return {target for kind, target in outgoing.get(node_id, []) if kind == edge_type}


def _claim_text(nodes: dict[str, dict[str, Any]], claim_node: str) -> str | None:
    node = nodes.get(claim_node)
    if node is None or node.get("type") != "claim":
        return None
    text = node.get("text")
    return text if isinstance(text, str) else claim_node.removeprefix("claim:")


def _operation_index(node_id: str) -> int:
    try:
        return int(node_id.split(":")[1])
    except (IndexError, ValueError):
        return -1


def _claims_of(outgoing: dict[str, list[tuple[str, str]]], nodes: dict[str, dict[str, Any]], record_node: str) -> set[str]:
    claims: set[str] = set()
    for target in _targets(outgoing, record_node, "CONTAINS"):
        text = _claim_text(nodes, target)
        if text is not None:
            claims.add(text)
    return claims


def verify_graph(t: Transition, *, graph: dict[str, Any] | None = None) -> Result:
    """Verify a transition using graph relationships; never trusts claim sets alone."""
    t.validate()
    graph = graph if graph is not None else build_graph(t)
    raw_nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    raw_edges = graph.get("edges", []) if isinstance(graph, dict) else []
    structural: list[str] = []

    node_ids = [node.get("id") for node in raw_nodes if isinstance(node, dict)]
    if len(node_ids) != len(raw_nodes):
        structural.append("malformed graph nodes")
    if len(node_ids) != len(set(node_ids)):
        structural.append("duplicate graph node ids")
    nodes = {
        node["id"]: node for node in raw_nodes if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    edges = [edge for edge in raw_edges if isinstance(edge, dict)]
    if len(edges) != len(raw_edges):
        structural.append("malformed graph edges")
    for edge in edges:
        if edge.get("from") not in nodes or edge.get("to") not in nodes:
            structural.append(f"dangling graph edge: {edge.get('from')} -> {edge.get('to')}")

    transition_node = f"transition:{t.case_id}"
    expected_claims = {
        claim
        for record in (*t.before, *t.candidate)
        for claim in record.claims
    } | {claim for evidence in t.evidence for claim in (*evidence.supports, *evidence.contradicts)} | set(t.required_claims)
    expected_nodes = {transition_node} | {f"claim:{claim}" for claim in expected_claims}
    expected_nodes |= {f"record:before:{record.id}" for record in t.before}
    expected_nodes |= {f"record:candidate:{record.id}" for record in t.candidate}
    expected_nodes |= {f"evidence:{evidence.id}" for evidence in t.evidence}
    expected_nodes |= {
        f"operation:{index}:{operation.type.lower()}" for index, operation in enumerate(t.operations)
    }
    missing_nodes = sorted(expected_nodes - set(nodes))
    unexpected_nodes = sorted(set(nodes) - expected_nodes)
    if missing_nodes:
        structural.append("missing graph nodes: " + ", ".join(missing_nodes))
    if unexpected_nodes:
        structural.append("unexpected graph nodes: " + ", ".join(unexpected_nodes))
    if nodes.get(transition_node, {}).get("type") != "transition":
        structural.append("transition node missing or mistyped")

    outgoing = _outgoing(edges)

    # Required claims must be explicitly attached to the transition via REQUIRES.
    required_claims = {
        claim
        for claim in t.required_claims
        if (f"transition:{t.case_id}", "REQUIRES", f"claim:{claim}")
        in {(edge["from"], edge["type"], edge["to"]) for edge in edges}
    }
    if required_claims != set(t.required_claims):
        structural.append("required claims are not attached to the transition node")

    # Operation nodes must structurally encode the declared operations.
    operation_nodes = [node_id for node_id in nodes if node_id.startswith("operation:")]
    if len(operation_nodes) != len(t.operations):
        structural.append("operation node count does not match operations")
    for node_id in operation_nodes:
        index = _operation_index(node_id)
        if not 0 <= index < len(t.operations):
            structural.append(f"operation node out of range: {node_id}")
            continue
        operation = t.operations[index]
        node = nodes[node_id]
        if node.get("operation") != operation.type:
            structural.append(f"operation node type mismatch: {node_id}")
        proposals = _targets(outgoing, node_id, "PROPOSES")
        if operation.record_id:
            if proposals != {f"record:candidate:{operation.record_id}"}:
                structural.append(f"operation {node_id} has inconsistent PROPOSES edges")
        elif proposals:
            structural.append(f"operation {node_id} proposes a record it does not reference")
        if operation.target_id:
            relation = _RELATION_BY_TYPE[operation.type]
            if (relation, f"record:before:{operation.target_id}") not in outgoing.get(node_id, []):
                structural.append(f"operation {node_id} is missing its {relation} edge")
        if operation.authorization is not None:
            if ("AUTHORIZED_BY", f"evidence:{operation.authorization.evidence_id}") not in outgoing.get(node_id, []):
                structural.append(f"operation {node_id} is missing its AUTHORIZED_BY edge")

    # Provenance paths: evidence connected to the transition supports claims.
    evidence_nodes = _targets(outgoing, transition_node, "HAS_EVIDENCE")
    supported_claims: set[str] = set()
    contradicted_claims: set[str] = set()
    for evidence_node in evidence_nodes:
        for target in _targets(outgoing, evidence_node, "SUPPORTS"):
            text = _claim_text(nodes, target)
            if text is not None:
                supported_claims.add(text)
        for target in _targets(outgoing, evidence_node, "CONTRADICTS"):
            text = _claim_text(nodes, target)
            if text is not None:
                contradicted_claims.add(text)

    candidate_claims_by_record = {
        record.id: _claims_of(outgoing, nodes, f"record:candidate:{record.id}")
        for record in t.candidate
    }
    before_claims_by_record = {
        record.id: _claims_of(outgoing, nodes, f"record:before:{record.id}")
        for record in t.before
    }
    candidate_claims = {claim for claims in candidate_claims_by_record.values() for claim in claims}
    before_claims = {claim for claims in before_claims_by_record.values() for claim in claims}

    # Coverage: required claims must be contained by candidate records.
    missing_required = sorted(set(t.required_claims) - candidate_claims)

    # Preservation: removals/changes need the right operations; changed records
    # need evidence-backed additions (provenance path) rather than claim-set trust.
    operation_target_type: dict[str, str] = {}
    operation_replacement: dict[str, str] = {}
    for node_id in operation_nodes:
        index = _operation_index(node_id)
        if not 0 <= index < len(t.operations):
            continue
        operation = t.operations[index]
        if operation.target_id:
            operation_target_type[operation.target_id] = operation.type
        if operation.record_id and operation.target_id:
            operation_replacement[operation.target_id] = operation.record_id
    damaged: list[str] = []
    for record in t.before:
        new_claims = candidate_claims_by_record.get(record.id)
        operation_type = operation_target_type.get(record.id)
        if operation_type is None:
            if new_claims is None or not before_claims_by_record[record.id] <= new_claims:
                damaged.append(record.id)
            continue
        if operation_type == "DELETE":
            continue
        replacement_id = operation_replacement.get(record.id)
        replacement_claims = (
            candidate_claims_by_record.get(replacement_id, set()) if replacement_id else set()
        )
        if replacement_id is None:
            damaged.append(record.id)
            continue
        added = replacement_claims - before_claims_by_record[record.id]
        evidence_backed = bool(added) and added <= supported_claims
        if not before_claims_by_record[record.id] <= replacement_claims and not evidence_backed:
            damaged.append(record.id)

    # Connectivity: every node must be reachable from the transition node.
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for edge in edges:
        if edge.get("from") in adjacency and edge.get("to") in adjacency:
            adjacency[edge["from"]].add(edge["to"])
            adjacency[edge["to"]].add(edge["from"])
    reachable: set[str] = set()
    queue = deque([transition_node] if transition_node in adjacency else [])
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        queue.extend(adjacency[current] - reachable)
    orphaned_claims = sorted(
        text
        for node_id, node in nodes.items()
        if node.get("type") == "claim" and node_id not in reachable
        for text in (_claim_text(nodes, node_id) or node_id,)
    )

    unsupported = sorted(candidate_claims - before_claims - supported_claims)
    contradicted = sorted(candidate_claims & contradicted_claims)

    checks: list[Check] = []
    checks.append(Check(
        "coverage",
        not missing_required,
        "all required claims reachable in candidate state" if not missing_required
        else "required claims omitted: " + "; ".join(missing_required),
        missing_required,
        reason_code="COVERAGE_OK" if not missing_required else "REQUIRED_CLAIM_OMITTED",
    ))
    checks.append(Check(
        "preservation",
        not damaged,
        "prior records preserved or changed through evidenced operations" if not damaged
        else "prior records changed/removed without an authorizing operation: " + ", ".join(damaged),
        damaged,
        reason_code="PRESERVATION_OK" if not damaged else "PRIOR_RECORD_UNAUTHORIZED_CHANGE",
    ))

    faithfulness_ok = not structural and not orphaned_claims and not unsupported and not contradicted
    if structural:
        faithfulness_reason = "graph structure is inconsistent: " + "; ".join(structural)
        faithfulness_code = "GRAPH_STRUCTURE_INVALID"
        affected = sorted({item for problem in structural for item in (problem.split(": ", 1)[-1].split(", ") if ": " in problem else [problem])})
    elif orphaned_claims:
        faithfulness_reason = "disconnected claim nodes: " + "; ".join(orphaned_claims)
        faithfulness_code = "DISCONNECTED_CLAIM"
        affected = orphaned_claims
    elif contradicted:
        faithfulness_reason = "claims contradicted by connected evidence: " + "; ".join(contradicted)
        faithfulness_code = "CONTRADICTED_CLAIM"
        affected = contradicted
    else:
        faithfulness_reason = (
            "candidate claims have a provenance path from evidence or prior memory"
            if not unsupported else "claims without evidence provenance: " + "; ".join(unsupported)
        )
        faithfulness_code = "FAITHFULNESS_OK" if not unsupported else "UNSUPPORTED_CLAIM"
        affected = unsupported
    checks.append(Check(
        "faithfulness",
        faithfulness_ok,
        faithfulness_reason,
        affected,
        reason_code=faithfulness_code,
    ))
    decision = "ACCEPT" if all(check.passed for check in checks) else "HOLD"
    return Result(t.case_id, decision, checks)
