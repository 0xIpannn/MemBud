"""Deterministic MVP: annotated claims are the oracle; no LLM is used."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class Record:
    id: str
    claims: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Record":
        return cls(str(d["id"]), tuple(map(str, d["claims"])))


@dataclass(frozen=True)
class Evidence:
    id: str
    supports: tuple[str, ...]
    source: str = ""
    text: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(
            str(d["id"]),
            tuple(map(str, d["supports"])),
            source=str(d.get("source", "")),
            text=str(d.get("text", "")),
        )


@dataclass(frozen=True)
class Operation:
    type: str
    record_id: str | None = None
    target_id: str | None = None
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Operation":
        record_id, target_id = d.get("record_id"), d.get("target_id")
        return cls(
            type=str(d["type"]).upper(),
            record_id=None if record_id is None else str(record_id),
            target_id=None if target_id is None else str(target_id),
            reason=str(d.get("reason", "")),
        )


@dataclass(frozen=True)
class Transition:
    case_id: str
    before: tuple[Record, ...]
    candidate: tuple[Record, ...]  # Full proposed resulting state.
    evidence: tuple[Evidence, ...]
    required_claims: tuple[str, ...] = ()
    operations: tuple[Operation, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transition":
        before = tuple(Record.from_dict(x) for x in d["before"])
        candidate = tuple(Record.from_dict(x) for x in d["candidate"])
        raw_operations = list(d.get("operations", []))
        legacy_supersedes = tuple(map(str, d.get("supersedes", [])))
        if legacy_supersedes:
            if raw_operations:
                raise ValueError("use operations or legacy supersedes, not both")
            if len(candidate) != 1:
                raise ValueError("legacy supersedes requires exactly one candidate record")
            replacement_id = candidate[0].id
            for target_id in legacy_supersedes:
                raw_operations.append({
                    "type": "REVISE" if replacement_id == target_id else "SUPERSEDE",
                    "record_id": replacement_id,
                    "target_id": target_id,
                })
        t = cls(
            case_id=str(d["case_id"]),
            before=before,
            candidate=candidate,
            evidence=tuple(Evidence.from_dict(x) for x in d["evidence"]),
            required_claims=tuple(map(str, d.get("required_claims", []))),
            operations=tuple(Operation.from_dict(x) for x in raw_operations),
        )
        t.validate()
        return t

    def validate(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must be non-empty")
        for label, records in (("before", self.before), ("candidate", self.candidate)):
            ids = [r.id for r in records]
            if any(not x.strip() for x in ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{label} record IDs must be non-empty and unique")
            for record in records:
                if not record.claims or any(not c.strip() for c in record.claims):
                    raise ValueError(f"record {record.id} must have non-empty claims")

        evidence_ids = [e.id for e in self.evidence]
        if any(not x.strip() for x in evidence_ids) or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be non-empty and unique")
        if any(not c.strip() for e in self.evidence for c in e.supports):
            raise ValueError("evidence supports must contain non-empty claims")
        if any(not c.strip() for c in self.required_claims):
            raise ValueError("required claims must be non-empty")

        before_by_id = {r.id: r for r in self.before}
        candidate_by_id = {r.id: r for r in self.candidate}
        before_state = {r.id: frozenset(r.claims) for r in self.before}
        candidate_state = {r.id: frozenset(r.claims) for r in self.candidate}
        seen_targets = set()
        for operation in self.operations:
            kind = operation.type
            if kind not in {"WRITE", "REVISE", "DELETE", "NOOP", "SUPERSEDE"}:
                raise ValueError(f"unsupported operation type: {kind}")
            if kind == "NOOP":
                if len(self.operations) != 1 or operation.record_id or operation.target_id:
                    raise ValueError("NOOP must be the only operation and have no record targets")
                if before_state != candidate_state:
                    raise ValueError("NOOP candidate state must equal prior state")
                continue
            if kind == "WRITE":
                if not operation.record_id or operation.target_id:
                    raise ValueError("WRITE requires record_id and forbids target_id")
                if operation.record_id not in candidate_by_id:
                    raise ValueError("WRITE record_id must reference a candidate record")
                if operation.record_id in before_by_id:
                    raise ValueError("WRITE must reference a new record; use REVISE for an existing record")
                continue

            if not operation.target_id or operation.target_id not in before_by_id:
                raise ValueError(f"{kind} target_id must reference a prior record")
            if operation.target_id in seen_targets:
                raise ValueError("a prior record can only be targeted by one operation")
            seen_targets.add(operation.target_id)
            if kind == "DELETE":
                if operation.record_id:
                    raise ValueError("DELETE forbids record_id")
                if not operation.reason.strip():
                    raise ValueError("DELETE requires an explicit reason")
                if operation.target_id in candidate_by_id:
                    raise ValueError("DELETE target must be absent from candidate state")
                continue

            if not operation.record_id or operation.record_id not in candidate_by_id:
                raise ValueError(f"{kind} record_id must reference a candidate record")
            if kind == "REVISE" and operation.record_id != operation.target_id:
                raise ValueError("REVISE must retain the targeted record ID")
            if kind == "SUPERSEDE":
                if operation.record_id == operation.target_id:
                    raise ValueError("SUPERSEDE requires a distinct replacement record ID")
                if operation.target_id in candidate_by_id:
                    raise ValueError("SUPERSEDE target must be absent from candidate state")


@dataclass
class Check:
    name: str
    passed: bool
    reason: str
    affected_ids: list[str] = field(default_factory=list)


@dataclass
class Result:
    case_id: str
    decision: str
    checks: list[Check]

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "decision": self.decision,
                "checks": [asdict(x) for x in self.checks]}


def build_graph(t: Transition) -> dict[str, Any]:
    """Canonical toy graph: nodes preserve source text; edges are explicit."""
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
        label = str(node.get("label", node["id"]))
        label = label.replace('"', "'").replace("\r", " ").replace("\n", " ")
        shape = "([\"" + label + "\"])" if node["type"] == "evidence" else "[\"" + label + "\"]"
        lines.append(f"  {ids[node['id']]}{shape}")
    for edge in graph["edges"]:
        source = ids[edge["from"]]
        target = ids[edge["to"]]
        label = edge["type"].replace('"', "'")
        lines.append(f"  {source} -->|{label}| {target}")
    return "\n".join(lines) + "\n"


def textual_representation(t: Transition) -> str:
    """Lossless compact text serialization, including all verifier inputs."""
    return json.dumps(transition_dict(t), ensure_ascii=False, sort_keys=True)


def transition_dict(t: Transition) -> dict[str, Any]:
    return {
        "case_id": t.case_id,
        "before": [asdict(r) for r in t.before],
        "candidate": [asdict(r) for r in t.candidate],
        "evidence": [asdict(e) for e in t.evidence],
        "required_claims": list(t.required_claims),
        "operations": [asdict(operation) for operation in t.operations],
    }


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


def commit(store: dict[str, Record], t: Transition, result: Result) -> dict[str, Record]:
    """Replace store only on ACCEPT; returns a copy and never mutates input."""
    if result.case_id != t.case_id:
        raise ValueError("result does not belong to transition")
    return {r.id: r for r in t.candidate} if result.decision == "ACCEPT" else dict(store)


def run_case(t: Transition, store_path: str | Path, log_path: str | Path,
             representation: str = "graph") -> Result:
    """Load local JSON store, verify, conditionally commit, append JSONL audit."""
    store_path, log_path = Path(store_path), Path(log_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    store = {}
    if store_path.exists():
        store = {r.id: r for r in (Record.from_dict(x) for x in json.loads(store_path.read_text(encoding="utf-8")))}
    else:
        store = {r.id: r for r in t.before}
    result = verify(t, representation)
    after = commit(store, t, result)
    store_path.write_text(
        json.dumps([asdict(r) for r in after.values()], indent=2) + "\n",
        encoding="utf-8",
    )
    event = {"input": transition_dict(t), "representation": representation,
             "result": result.to_dict(), "store_after": [asdict(r) for r in after.values()]}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return result
