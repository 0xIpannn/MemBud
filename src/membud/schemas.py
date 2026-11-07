"""Canonical data structures and validation for memory-state transitions."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 2
_SUPPORTED_SCHEMA_VERSIONS = {1, SCHEMA_VERSION}


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _non_empty_string(value: Any, field_name: str) -> str:
    text = _string(value, field_name)
    if not text.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of strings")
    items = tuple(value)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{field_name} must contain only strings")
    return items


@dataclass(frozen=True)
class Record:
    id: str
    claims: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Record":
        return cls(_string(d["id"], "record id"), _string_tuple(d["claims"], "record claims"))


@dataclass(frozen=True)
class Evidence:
    id: str
    supports: tuple[str, ...]
    source: str = ""
    text: str = ""
    contradicts: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(
            _string(d["id"], "evidence id"),
            _string_tuple(d["supports"], "evidence supports"),
            source=_string(d.get("source", ""), "evidence source"),
            text=_string(d.get("text", ""), "evidence text"),
            contradicts=_string_tuple(d.get("contradicts", []), "evidence contradicts"),
        )


@dataclass(frozen=True)
class DeletionAuthorization:
    """Structured authorization metadata for DELETE operations."""

    source: str
    evidence_id: str
    target_id: str
    rationale: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DeletionAuthorization":
        if not isinstance(d, dict):
            raise ValueError("deletion authorization must be an object")
        return cls(
            _non_empty_string(d["source"], "authorization source"),
            _non_empty_string(d["evidence_id"], "authorization evidence_id"),
            _non_empty_string(d["target_id"], "authorization target_id"),
            _non_empty_string(d["rationale"], "authorization rationale"),
        )


@dataclass(frozen=True)
class Operation:
    type: str
    record_id: str | None = None
    target_id: str | None = None
    reason: str = ""
    authorization: DeletionAuthorization | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Operation":
        record_id, target_id = d.get("record_id"), d.get("target_id")
        if record_id is not None and not isinstance(record_id, str):
            raise ValueError("operation record_id must be a string or null")
        if target_id is not None and not isinstance(target_id, str):
            raise ValueError("operation target_id must be a string or null")
        authorization = d.get("authorization")
        return cls(
            type=_string(d["type"], "operation type").upper(),
            record_id=record_id,
            target_id=target_id,
            reason=_string(d.get("reason", ""), "operation reason"),
            authorization=(
                None if authorization is None else DeletionAuthorization.from_dict(authorization)
            ),
        )


@dataclass(frozen=True)
class Transition:
    case_id: str
    before: tuple[Record, ...]
    candidate: tuple[Record, ...]  # Full proposed resulting state.
    evidence: tuple[Evidence, ...]
    required_claims: tuple[str, ...] = ()
    operations: tuple[Operation, ...] = ()
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transition":
        if not isinstance(d, dict):
            raise ValueError("transition must be a mapping")
        # Version-less input is treated as frozen v1 legacy data: it is still
        # loadable for archival/reproducibility, but strict operation-coverage
        # invariants only apply to schema_version 2 transitions.
        declared_version = d.get("schema_version", 1)
        if declared_version not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported schema_version: {declared_version}")
        if not isinstance(d.get("before"), list) or not isinstance(d.get("candidate"), list):
            raise ValueError("before and candidate must be lists of records")
        if not isinstance(d.get("evidence"), list):
            raise ValueError("evidence must be a list")
        if not isinstance(d.get("operations", []), list):
            raise ValueError("operations must be a list")
        before = tuple(Record.from_dict(x) for x in d["before"])
        candidate = tuple(Record.from_dict(x) for x in d["candidate"])
        raw_operations = list(d.get("operations", []))
        legacy_supersedes = _string_tuple(d.get("supersedes", []), "supersedes")
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
            required_claims=_string_tuple(d.get("required_claims", []), "required_claims"),
            operations=tuple(Operation.from_dict(x) for x in raw_operations),
            schema_version=declared_version,
        )
        t.validate()
        return t

    def validate(self) -> None:
        if self.schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
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
        if any(not c.strip() for e in self.evidence for c in (*e.supports, *e.contradicts)):
            raise ValueError("evidence claims must be non-empty")
        if any(not c.strip() for c in self.required_claims):
            raise ValueError("required claims must be non-empty")

        evidence_id_set = set(evidence_ids)
        before_by_id = {r.id: r for r in self.before}
        candidate_by_id = {r.id: r for r in self.candidate}
        before_state = {r.id: frozenset(r.claims) for r in self.before}
        candidate_state = {r.id: frozenset(r.claims) for r in self.candidate}

        seen_targets: set[str] = set()
        seen_candidate_records: set[str] = set()
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
                if operation.record_id in seen_candidate_records:
                    raise ValueError("a candidate record can only be proposed by one operation")
                seen_candidate_records.add(operation.record_id)
                continue

            if not operation.target_id or operation.target_id not in before_by_id:
                raise ValueError(f"{kind} target_id must reference a prior record")
            if operation.target_id in seen_targets:
                raise ValueError("a prior record can only be targeted by one operation")
            seen_targets.add(operation.target_id)

            if kind == "DELETE":
                if operation.record_id:
                    raise ValueError("DELETE forbids record_id")
                if operation.target_id in candidate_by_id:
                    raise ValueError("DELETE target must be absent from candidate state")
                if not operation.reason.strip():
                    raise ValueError("DELETE requires an explicit reason")
                if operation.authorization is None:
                    raise ValueError("DELETE requires structured authorization metadata")
                if operation.authorization.target_id != operation.target_id:
                    raise ValueError("DELETE authorization target_id must match the operation target")
                if operation.authorization.evidence_id not in evidence_id_set:
                    raise ValueError("DELETE authorization evidence_id must reference transition evidence")
                continue

            if not operation.record_id or operation.record_id not in candidate_by_id:
                raise ValueError(f"{kind} record_id must reference a candidate record")
            if kind == "REVISE":
                if operation.record_id != operation.target_id:
                    raise ValueError("REVISE must retain the targeted record ID")
                if before_state[operation.target_id] == candidate_state[operation.record_id]:
                    raise ValueError("REVISE must change the targeted record")
            if kind == "SUPERSEDE":
                if operation.record_id == operation.target_id:
                    raise ValueError("SUPERSEDE requires a distinct replacement record ID")
                if operation.target_id in candidate_by_id:
                    raise ValueError("SUPERSEDE target must be absent from candidate state")
                if operation.record_id in before_by_id:
                    raise ValueError("SUPERSEDE requires a new replacement record")
            if operation.record_id in seen_candidate_records:
                raise ValueError("a candidate record can only be proposed by one operation")
            seen_candidate_records.add(operation.record_id)

        if self.schema_version < 2:
            return

        # Every actual state change must be declared by an operation, and every
        # declared operation must correspond to an actual change.
        added = set(candidate_by_id) - set(before_by_id)
        write_ids = {op.record_id for op in self.operations if op.type == "WRITE"}
        supersede_replacements = {op.record_id for op in self.operations if op.type == "SUPERSEDE"}
        for record_id in sorted(added):
            if record_id not in write_ids | supersede_replacements:
                raise ValueError(f"undeclared insertion of candidate record {record_id}")

        removed = set(before_by_id) - set(candidate_by_id)
        removed_authorized = {
            op.target_id for op in self.operations if op.type in {"DELETE", "SUPERSEDE"}
        }
        for record_id in sorted(removed):
            if record_id not in removed_authorized:
                raise ValueError(f"undeclared removal of prior record {record_id}")

        changed = {
            record_id
            for record_id in set(before_by_id) & set(candidate_by_id)
            if before_state[record_id] != candidate_state[record_id]
        }
        revised = {op.target_id for op in self.operations if op.type == "REVISE"}
        for record_id in sorted(changed):
            if record_id not in revised:
                raise ValueError(f"undeclared modification of prior record {record_id}")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str
    affected_ids: list[str] = field(default_factory=list)
    status: str | None = None
    reason_code: str = ""

    def __post_init__(self) -> None:
        if self.status is None:
            object.__setattr__(self, "status", "PASS" if self.passed else "FAIL")
        if self.status not in {"PASS", "FAIL", "UNCERTAIN"}:
            raise ValueError("check status must be PASS, FAIL, or UNCERTAIN")
        if self.passed != (self.status == "PASS"):
            raise ValueError("passed must be true exactly when check status is PASS")


@dataclass(frozen=True)
class Result:
    case_id: str
    decision: str
    checks: list[Check]
    verifier: str = "rules"
    prompt_version: str = ""
    verifier_config: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int | None] = field(default_factory=dict)
    latency_ms: float | None = None
    raw_response: dict[str, Any] | None = None
    provider_request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "decision": self.decision,
            "verifier": self.verifier,
            "prompt_version": self.prompt_version,
            "verifier_config": self.verifier_config,
            "checks": [asdict(check) for check in self.checks],
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "raw_response": self.raw_response,
            "provider_request_id": self.provider_request_id,
        }
