"""Canonical data structures and validation for memory-state transitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
        return {
            "case_id": self.case_id,
            "decision": self.decision,
            "checks": [asdict(check) for check in self.checks],
        }
