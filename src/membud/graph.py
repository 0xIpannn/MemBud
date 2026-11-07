"""Canonical graph construction and Mermaid rendering."""
from typing import Any

from .schemas import Transition


def build_graph(t: Transition) -> dict[str, Any]:
    """Build a graph from a validated transition, preserving explicit relationships."""
    t.validate()
    nodes = []
    edges = []
    transition_node = f"transition:{t.case_id}"
    nodes.append({"id": transition_node, "type": "transition", "label": f"candidate update: {t.case_id}"})
    claims = sorted(
        {c for r in (*t.before, *t.candidate) for c in r.claims}
        | {c for e in t.evidence for c in e.supports}
        | set(t.required_claims)
    )
    for claim in claims:
        nodes.append({"id": f"claim:{claim}", "type": "claim", "text": claim})
    for stage, records in (("before", t.before), ("candidate", t.candidate)):
        for r in records:
            record_node = f"record:{stage}:{r.id}"
            nodes.append({"id": record_node, "type": "record", "stage": stage, "claims": list(r.claims)})
            edges.append({
                "from": transition_node,
                "to": record_node,
                "type": "BEFORE" if stage == "before" else "PROPOSES",
            })
            for claim in r.claims:
                edges.append({"from": record_node, "to": f"claim:{claim}", "type": "CONTAINS"})
    for e in t.evidence:
        evidence_node = f"evidence:{e.id}"
        nodes.append({
            "id": evidence_node,
            "type": "evidence",
            "supports": list(e.supports),
            "source": e.source,
            "text": e.text,
        })
        edges.append({"from": transition_node, "to": evidence_node, "type": "HAS_EVIDENCE"})
        for claim in e.supports:
            edges.append({"from": evidence_node, "to": f"claim:{claim}", "type": "SUPPORTS"})
    for claim in sorted(set(t.required_claims)):
        edges.append({"from": transition_node, "to": f"claim:{claim}", "type": "REQUIRES"})
    for index, operation in enumerate(t.operations):
        operation_node = f"operation:{index}:{operation.type.lower()}"
        nodes.append({
            "id": operation_node,
            "type": "operation",
            "label": operation.type,
            "operation": operation.type,
            "record_id": operation.record_id,
            "target_id": operation.target_id,
            "reason": operation.reason,
        })
        edges.append({"from": transition_node, "to": operation_node, "type": "HAS_OPERATION"})
        if operation.record_id:
            edges.append({
                "from": operation_node,
                "to": f"record:candidate:{operation.record_id}",
                "type": "PROPOSES",
            })
        if operation.target_id:
            relation = {"REVISE": "REVISES", "DELETE": "DELETES", "SUPERSEDE": "SUPERSEDES"}[operation.type]
            edges.append({
                "from": operation_node,
                "to": f"record:before:{operation.target_id}",
                "type": relation,
            })
    return {"nodes": nodes, "edges": edges}


def to_mermaid(t: Transition) -> str:
    """Render the transition graph as Mermaid flowchart source."""
    graph = build_graph(t)
    ids = {node["id"]: f"n{i}" for i, node in enumerate(graph["nodes"])}
    lines = ["flowchart LR"]
    for node in graph["nodes"]:
        label = str(node.get("label", node.get("text", node["id"])))
        if node["type"] == "evidence":
            provenance = " — ".join(value for value in (node.get("source", ""), node.get("text", "")) if value)
            label = provenance or node["id"]
        label = label.replace('"', "'").replace("\r", " ").replace("\n", " ")
        shape = "([\"" + label + "\"])" if node["type"] == "evidence" else "[\"" + label + "\"]"
        lines.append(f"  {ids[node['id']]}{shape}")
    for edge in graph["edges"]:
        source = ids[edge["from"]]
        target = ids[edge["to"]]
        label = edge["type"].replace('"', "'")
        lines.append(f"  {source} -->|{label}| {target}")
    return "\n".join(lines) + "\n"
