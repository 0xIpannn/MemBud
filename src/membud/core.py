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

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(str(d["id"]), tuple(map(str, d["supports"])))


@dataclass(frozen=True)
class Transition:
    case_id: str
    before: tuple[Record, ...]
    candidate: tuple[Record, ...]  # Full proposed resulting state.
    evidence: tuple[Evidence, ...]
    required_claims: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transition":
        t = cls(
            case_id=str(d["case_id"]),
            before=tuple(Record.from_dict(x) for x in d["before"]),
            candidate=tuple(Record.from_dict(x) for x in d["candidate"]),
            evidence=tuple(Evidence.from_dict(x) for x in d["evidence"]),
            required_claims=tuple(map(str, d.get("required_claims", []))),
            supersedes=tuple(map(str, d.get("supersedes", []))),
        )
        t.validate()
        return t

    def validate(self) -> None:
        for label, records in (("before", self.before), ("candidate", self.candidate)):
            ids = [r.id for r in records]
            if any(not x for x in ids) or len(ids) != len(set(ids)):
                raise ValueError(f"{label} record IDs must be non-empty and unique")
            for record in records:
                if not record.claims or any(not c.strip() for c in record.claims):
                    raise ValueError(f"record {record.id} must have non-empty claims")


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
    for r in (*t.before, *t.candidate):
        nodes.append({"id": f"record:{r.id}", "type": "record", "claims": list(r.claims)})
    for e in t.evidence:
        nodes.append({"id": f"evidence:{e.id}", "type": "evidence", "supports": list(e.supports)})
        for claim in e.supports:
            edges.append({"from": f"evidence:{e.id}", "to": f"claim:{claim}", "type": "SUPPORTED_BY"})
    for rid in t.supersedes:
        edges.append({"from": "candidate", "to": f"record:{rid}", "type": "SUPERSEDES"})
    return {"nodes": nodes, "edges": edges}


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
        "supersedes": list(t.supersedes),
    }


def verify(t: Transition, representation: str = "graph") -> Result:
    """Run explicit annotation-based checks through graph or text input path.

    Both representations preserve identical data. These checks operate on the
    canonical transition to avoid pretending the toy graph has semantic power.
    """
    if representation not in {"graph", "text"}:
        raise ValueError("representation must be 'graph' or 'text'")
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
    damaged = []
    for rid, old in before_by_id.items():
        if rid in t.supersedes:
            continue
        new = candidate_by_id.get(rid)
        if new is None or not set(old.claims).issubset(new.claims):
            damaged.append(rid)
    checks.append(Check("preservation", not damaged,
                        "prior records preserved" if not damaged else "valid prior records changed or removed: " + ", ".join(damaged), damaged))

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
        store = {r.id: r for r in (Record.from_dict(x) for x in json.loads(store_path.read_text()))}
    else:
        store = {r.id: r for r in t.before}
    result = verify(t, representation)
    after = commit(store, t, result)
    store_path.write_text(json.dumps([asdict(r) for r in after.values()], indent=2) + "\n")
    event = {"input": transition_dict(t), "representation": representation,
             "result": result.to_dict(), "store_after": [asdict(r) for r in after.values()]}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return result
