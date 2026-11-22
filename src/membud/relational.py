"""Relational memory transitions: entities, typed facts, provenance and history.

This module defines the experimental *relational* transition format used to test
the relational graph hypothesis (see ``docs/relational-hypothesis.md``). It is
separate from the canonical transition package in :mod:`membud.schemas` (format
``schema_version`` 2), whose claims are opaque strings and therefore cannot
express multi-hop provenance, entity aliasing, or supersession chains.

Format ``membud-relational`` version 1 (experiment-only interchange format):

``relations``
    ``{name: {"cardinality": "one" | "many"}}``. ``same_as`` is built in and
    declares that two entity names denote the same entity (symmetric).
``sources``
    Root provenance: ``{id, kind, trusted, time}``. ``trusted`` sources are
    direct user statements; untrusted ones are e.g. assistant speculation or
    retrieved snippets. ``time`` is a monotonic turn index.
``evidence``
    ``{id, source | derived_from, supports}``. Exactly one of ``source`` (a
    root observation) and a non-empty ``derived_from`` (evidence derived from
    other evidence, e.g. a session summary) must be present. ``supports`` lists
    fact keys ``"subject|relation|object"``.
``before``
    Full prior memory history: ``{id, fact, evidence, supersedes}``. A record
    with ``supersedes`` replaced an older record. Superseded records stay in
    history; the *current* state is the set of records no other record
    supersedes.
``operations``
    ``WRITE {record}`` adds a record; ``SUPERSEDE {target_id, record}`` replaces
    the target with a new record.

Validation here is purely structural (ids exist and are unique, references
resolve, facts use declared relations). Semantic validity (grounded provenance,
no unresolved contradictions, valid supersession) is decided by verifiers in
:mod:`membud.relational_verify`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FORMAT_NAME = "membud-relational"
FORMAT_VERSION = 1
SAME_AS = "same_as"
CARDINALITIES = {"one", "many"}
OPERATION_TYPES = {"WRITE", "SUPERSEDE"}


_TOKEN = re.compile(r"[A-Za-z0-9_.:@+-]+")


def _str(value: Any, name: str) -> str:
    """Identifiers and fact parts are single tokens so the text form stays lossless."""
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty token of [A-Za-z0-9_.:@+-]: {value!r}")
    return value


def _str_list(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list of strings")
    return tuple(_str(item, name) for item in value)


@dataclass(frozen=True, order=True)
class Fact:
    subject: str
    relation: str
    object: str

    @property
    def key(self) -> str:
        return f"{self.subject}|{self.relation}|{self.object}"

    @classmethod
    def parse(cls, value: Any) -> "Fact":
        if isinstance(value, dict):
            return cls(
                _str(value.get("subject"), "fact subject"),
                _str(value.get("relation"), "fact relation"),
                _str(value.get("object"), "fact object"),
            )
        if not isinstance(value, str):
            raise ValueError(f"fact key must be a string: {value!r}")
        parts = value.split("|")
        if len(parts) != 3:
            raise ValueError(f"fact key must be 'subject|relation|object': {value!r}")
        return cls(*(_str(part, "fact part") for part in parts))

    def to_dict(self) -> dict[str, str]:
        return {"subject": self.subject, "relation": self.relation, "object": self.object}


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    trusted: bool
    time: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Source":
        trusted = d.get("trusted")
        time = d.get("time")
        if not isinstance(trusted, bool):
            raise ValueError("source trusted must be a boolean")
        if not isinstance(time, int) or isinstance(time, bool) or time < 0:
            raise ValueError("source time must be a non-negative integer")
        return cls(_str(d.get("id"), "source id"), _str(d.get("kind"), "source kind"), trusted, time)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "trusted": self.trusted, "time": self.time}


@dataclass(frozen=True)
class RelEvidence:
    id: str
    supports: tuple[Fact, ...]
    source: str | None = None
    derived_from: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelEvidence":
        source = d.get("source")
        derived = _str_list(d.get("derived_from", []), "evidence derived_from")
        if (source is None) == (not derived):
            raise ValueError("evidence needs exactly one of source or non-empty derived_from")
        supports = d.get("supports", [])
        if not isinstance(supports, list):
            raise ValueError("evidence supports must be a list")
        return cls(
            _str(d.get("id"), "evidence id"),
            tuple(Fact.parse(item) for item in supports),
            None if source is None else _str(source, "evidence source"),
            derived,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "supports": [f.key for f in self.supports]}
        if self.source is not None:
            out["source"] = self.source
        else:
            out["derived_from"] = list(self.derived_from)
        return out


@dataclass(frozen=True)
class RelRecord:
    id: str
    fact: Fact
    evidence: tuple[str, ...]
    supersedes: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelRecord":
        supersedes = d.get("supersedes")
        return cls(
            _str(d.get("id"), "record id"),
            Fact.parse(d.get("fact")),
            _str_list(d.get("evidence", []), "record evidence"),
            None if supersedes is None else _str(supersedes, "record supersedes"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "fact": self.fact.key, "evidence": list(self.evidence)}
        if self.supersedes is not None:
            out["supersedes"] = self.supersedes
        return out


@dataclass(frozen=True)
class RelOperation:
    type: str
    record: RelRecord
    target_id: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelOperation":
        kind = _str(d.get("type"), "operation type").upper()
        if kind not in OPERATION_TYPES:
            raise ValueError(f"unsupported relational operation type: {kind}")
        target = d.get("target_id")
        if not isinstance(d.get("record"), dict):
            raise ValueError("operation record must be an object")
        record = RelRecord.from_dict(d["record"])
        if kind == "WRITE" and (target is not None or record.supersedes is not None):
            raise ValueError("WRITE forbids target_id and record.supersedes")
        if kind == "SUPERSEDE":
            target = _str(target, "SUPERSEDE target_id")
            if record.supersedes not in (None, target):
                raise ValueError("SUPERSEDE record.supersedes must equal target_id")
            record = RelRecord(record.id, record.fact, record.evidence, target)
        return cls(kind, record, target)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "record": self.record.to_dict()}
        if self.target_id is not None:
            out["target_id"] = self.target_id
        return out


@dataclass(frozen=True)
class RelationalTransition:
    case_id: str
    relations: dict[str, str]
    sources: tuple[Source, ...]
    evidence: tuple[RelEvidence, ...]
    before: tuple[RelRecord, ...]
    operations: tuple[RelOperation, ...]
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RelationalTransition":
        if not isinstance(d, dict):
            raise ValueError("relational transition must be an object")
        if d.get("format") not in (None, FORMAT_NAME):
            raise ValueError(f"unsupported format: {d.get('format')!r}")
        if d.get("format_version", FORMAT_VERSION) != FORMAT_VERSION:
            raise ValueError(f"unsupported format_version: {d.get('format_version')!r}")
        raw_relations = d.get("relations")
        if not isinstance(raw_relations, dict):
            raise ValueError("relations must be an object")
        relations: dict[str, str] = {}
        for name, spec in raw_relations.items():
            _str(name, "relation name")
            if name == SAME_AS:
                raise ValueError("same_as is built in and must not be declared")
            cardinality = spec.get("cardinality") if isinstance(spec, dict) else None
            if cardinality not in CARDINALITIES:
                raise ValueError(f"relation {name} needs cardinality 'one' or 'many'")
            relations[name] = cardinality
        for key in ("sources", "evidence", "before", "operations"):
            if not isinstance(d.get(key, []), list):
                raise ValueError(f"{key} must be a list")
        t = cls(
            case_id=_str(d.get("case_id"), "case_id"),
            relations=relations,
            sources=tuple(Source.from_dict(x) for x in d.get("sources", [])),
            evidence=tuple(RelEvidence.from_dict(x) for x in d.get("evidence", [])),
            before=tuple(RelRecord.from_dict(x) for x in d.get("before", [])),
            operations=tuple(RelOperation.from_dict(x) for x in d.get("operations", [])),
            metadata=dict(d.get("metadata", {})) if isinstance(d.get("metadata", {}), dict) else {},
        )
        t.validate()
        return t

    def to_dict(self, *, include_metadata: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "case_id": self.case_id,
            "relations": {name: {"cardinality": c} for name, c in sorted(self.relations.items())},
            "sources": [s.to_dict() for s in self.sources],
            "evidence": [e.to_dict() for e in self.evidence],
            "before": [r.to_dict() for r in self.before],
            "operations": [op.to_dict() for op in self.operations],
        }
        if include_metadata and self.metadata:
            out["metadata"] = self.metadata
        return out

    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        def unique(ids: list[str], label: str) -> set[str]:
            if len(ids) != len(set(ids)):
                raise ValueError(f"{label} ids must be unique")
            return set(ids)

        source_ids = unique([s.id for s in self.sources], "source")
        evidence_ids = unique([e.id for e in self.evidence], "evidence")
        before_ids = unique([r.id for r in self.before], "before record")
        new_ids = [op.record.id for op in self.operations]
        unique(new_ids, "operation record")
        if before_ids & set(new_ids):
            raise ValueError("operation records must have new ids")
        if not self.operations:
            raise ValueError("a relational transition must propose at least one operation")

        known_relations = set(self.relations) | {SAME_AS}

        def check_fact(fact: Fact, where: str) -> None:
            if fact.relation not in known_relations:
                raise ValueError(f"{where} uses undeclared relation {fact.relation!r}")
            if fact.relation == SAME_AS and fact.subject == fact.object:
                raise ValueError(f"{where} declares a trivial same_as")

        for e in self.evidence:
            if e.source is not None and e.source not in source_ids:
                raise ValueError(f"evidence {e.id} references unknown source {e.source}")
            for parent in e.derived_from:
                if parent not in evidence_ids:
                    raise ValueError(f"evidence {e.id} derives from unknown evidence {parent}")
            for fact in e.supports:
                check_fact(fact, f"evidence {e.id}")

        superseded: set[str] = set()
        for r in self.before:
            check_fact(r.fact, f"record {r.id}")
            if not r.evidence:
                raise ValueError(f"record {r.id} must cite evidence")
            for eid in r.evidence:
                if eid not in evidence_ids:
                    raise ValueError(f"record {r.id} cites unknown evidence {eid}")
            if r.supersedes is not None:
                if r.supersedes not in before_ids or r.supersedes == r.id:
                    raise ValueError(f"record {r.id} supersedes unknown record {r.supersedes}")
                # Forked history (a record superseded twice) is representable on
                # purpose: an unguarded store can contain it, and verifiers must
                # treat it as existing (possibly corrupted) memory.
                superseded.add(r.supersedes)

        targets: set[str] = set()
        for op in self.operations:
            check_fact(op.record.fact, f"operation record {op.record.id}")
            if not op.record.evidence:
                raise ValueError(f"operation record {op.record.id} must cite evidence")
            for eid in op.record.evidence:
                if eid not in evidence_ids:
                    raise ValueError(f"operation record {op.record.id} cites unknown evidence {eid}")
            if op.type == "SUPERSEDE":
                if op.target_id not in before_ids:
                    raise ValueError(f"SUPERSEDE target {op.target_id} is not a prior record")
                if op.target_id in targets:
                    raise ValueError("a prior record can only be superseded by one operation")
                targets.add(op.target_id)

    # ------------------------------------------------------------------ #
    def lookup(self) -> "Lookups":
        return Lookups(
            sources={s.id: s for s in self.sources},
            evidence={e.id: e for e in self.evidence},
            before={r.id: r for r in self.before},
        )


@dataclass(frozen=True)
class Lookups:
    sources: dict[str, Source]
    evidence: dict[str, RelEvidence]
    before: dict[str, RelRecord]


def relational_text(t: RelationalTransition) -> str:
    """Lossless flat-text serialization handed to text-side verifiers.

    One line per item; every field of the canonical transition is present, so a
    text verifier receives exactly the same information as the graph verifier.
    """
    lines = [f"CASE {t.case_id}"]
    for name, cardinality in sorted(t.relations.items()):
        lines.append(f"RELATION {name} cardinality={cardinality}")
    for s in t.sources:
        lines.append(f"SOURCE {s.id} kind={s.kind} trusted={str(s.trusted).lower()} time={s.time}")
    for e in t.evidence:
        origin = f"source={e.source}" if e.source is not None else "derived_from=" + ",".join(e.derived_from)
        lines.append(f"EVIDENCE {e.id} {origin} supports=" + ";".join(f.key for f in e.supports))
    for r in t.before:
        sup = f" supersedes={r.supersedes}" if r.supersedes else ""
        lines.append(f"RECORD {r.id} fact={r.fact.key} evidence=" + ",".join(r.evidence) + sup)
    for op in t.operations:
        target = f" target={op.target_id}" if op.target_id else ""
        lines.append(
            f"OP {op.type}{target} record={op.record.id} fact={op.record.fact.key} evidence="
            + ",".join(op.record.evidence)
        )
    return "\n".join(lines) + "\n"


def parse_relational_text(text: str) -> RelationalTransition:
    """Inverse of :func:`relational_text` (proves the serialization is lossless)."""
    d: dict[str, Any] = {"relations": {}, "sources": [], "evidence": [], "before": [], "operations": []}

    def kv(tokens: list[str]) -> dict[str, str]:
        out = {}
        for token in tokens:
            key, _, value = token.partition("=")
            out[key] = value
        return out

    for line in text.splitlines():
        if not line.strip():
            continue
        kind, *rest = line.split(" ")
        if kind == "CASE":
            d["case_id"] = rest[0]
        elif kind == "RELATION":
            d["relations"][rest[0]] = {"cardinality": kv(rest[1:])["cardinality"]}
        elif kind == "SOURCE":
            f = kv(rest[1:])
            d["sources"].append({"id": rest[0], "kind": f["kind"], "trusted": f["trusted"] == "true", "time": int(f["time"])})
        elif kind == "EVIDENCE":
            f = kv(rest[1:])
            item: dict[str, Any] = {"id": rest[0], "supports": [x for x in f["supports"].split(";") if x]}
            if "source" in f:
                item["source"] = f["source"]
            else:
                item["derived_from"] = [x for x in f["derived_from"].split(",") if x]
            d["evidence"].append(item)
        elif kind == "RECORD":
            f = kv(rest[1:])
            rec: dict[str, Any] = {"id": rest[0], "fact": f["fact"], "evidence": [x for x in f["evidence"].split(",") if x]}
            if "supersedes" in f:
                rec["supersedes"] = f["supersedes"]
            d["before"].append(rec)
        elif kind == "OP":
            f = kv(rest[1:])
            op: dict[str, Any] = {
                "type": rest[0],
                "record": {"id": f["record"], "fact": f["fact"], "evidence": [x for x in f["evidence"].split(",") if x]},
            }
            if "target" in f:
                op["target_id"] = f["target"]
            d["operations"].append(op)
        else:
            raise ValueError(f"unknown line kind: {kind}")
    return RelationalTransition.from_dict(d)
