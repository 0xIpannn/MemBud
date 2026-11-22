"""Seeded generator for the relational hypothesis suite.

Cases are generated as *minimal pairs*: for each mechanism and relational depth,
a valid case and a faulty case share the same structure and differ only in the
decisive relational fact (a root source's trust, a derivation cycle, which
entity holds a conflicting value, a root observation time, whether an alias
link exists). The expected decision is fixed by construction from the fault the
generator injected; no verifier is consulted to label a case.

``required_hops`` is the number of relational hops (derivation edges or
``same_as`` edges) that must be followed to decide the case. A verifier with a
hop budget ``k`` can decide a case only when ``required_hops <= k``.

Families (``mechanism / family``):

* ``provenance/untrusted_root`` - the supporting evidence derives, ``depth``
  derivations down, from an untrusted (assistant) root vs a trusted user turn.
* ``provenance/cycle`` - the supporting summary derives from a derivation cycle
  of length ``depth`` vs a chain of the same length ending in a user turn.
* ``consistency/alias_write`` - a WRITE of a functional fact whose subject is
  ``depth`` same_as hops from an entity already holding a different value, vs
  the same chain where the holder is a different, unlinked person.
* ``consistency/alias_merge`` - a new same_as fact merges two entities that
  hold different values ``depth`` hops away, vs the same merge without the
  conflicting value.
* ``history/stale_target`` - SUPERSEDE of an already-superseded record vs the
  current head (a 1-hop control family).
* ``history/temporal`` - SUPERSEDE whose evidence is a summary ``depth``
  derivations above an observation older than (fault) or newer than (valid)
  the target's - the "stale summary revives an old value" failure.
* ``history/slot_alias`` - SUPERSEDE of a record about an entity ``depth``
  same_as hops from the replacement's subject (valid) vs about an unlinked
  person (fault: silently replaces someone else's memory).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .relational import RelationalTransition

RELATIONS = {
    "lives_in": "one",
    "works_at": "one",
    "favorite_food": "one",
    "likes": "many",
}
PEOPLE = [
    "ifan", "budi", "sari", "dewi", "andi", "rina", "joko", "tika", "agus", "lina",
    "eko", "nina", "dian", "rudi", "maya", "yogi", "putri", "bayu", "wati", "hadi",
]
VALUES = {
    "lives_in": ["Bandung", "Jakarta", "Surabaya", "Medan", "Yogyakarta", "Semarang", "Malang", "Bogor"],
    "works_at": ["Tokopedia", "Gojek", "Traveloka", "Bukalapak", "Telkom", "BCA", "Shopee", "Grab"],
    "favorite_food": ["rendang", "soto", "nasi_goreng", "bakso", "sate", "gado-gado", "pempek", "rawon"],
    "likes": ["badminton", "jazz", "hiking", "coffee", "chess", "anime", "cycling", "cooking"],
}
FUNCTIONAL = [r for r, c in RELATIONS.items() if c == "one"]
DEPTHS = (0, 1, 2, 3, 4)


@dataclass
class _Builder:
    rng: random.Random
    case_id: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    before: list[dict[str, Any]] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    _n: int = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def source(self, time: int, trusted: bool) -> str:
        kind = "user_turn" if trusted else "assistant_turn"
        sid = f"{'u' if trusted else 'a'}{time}.{self._id('s')}"
        self.sources.append({"id": sid, "kind": kind, "trusted": trusted, "time": time})
        return sid

    def observe(self, facts: list[str], time: int, trusted: bool = True) -> str:
        eid = self._id("e")
        self.evidence.append({"id": eid, "source": self.source(time, trusted), "supports": facts})
        return eid

    def derive(self, parents: list[str], facts: list[str]) -> str:
        eid = self._id("e")
        self.evidence.append({"id": eid, "derived_from": parents, "supports": facts})
        return eid

    def chain(self, facts: list[str], depth: int, time: int, trusted: bool = True) -> str:
        """Root observation plus ``depth`` stacked summaries; returns the top id."""
        top = self.observe(facts, time, trusted)
        for _ in range(depth):
            top = self.derive([top], facts)
        return top

    def record(self, fact: str, ev: str, supersedes: str | None = None) -> str:
        rid = self._id("r")
        rec: dict[str, Any] = {"id": rid, "fact": fact, "evidence": [ev]}
        if supersedes:
            rec["supersedes"] = supersedes
        self.before.append(rec)
        return rid

    def grounded_record(self, fact: str, time: int) -> str:
        return self.record(fact, self.observe([fact], time))

    def alias_chain(self, person: str, length: int, time: int) -> list[str]:
        """Return [person, alias1, ..., alias_length] linked by grounded same_as."""
        names = [person]
        for i in range(1, length + 1):
            alias = f"{person}.a{i}"
            self.grounded_record(f"{names[-1]}|same_as|{alias}", time + i)
            names.append(alias)
        return names

    def write(self, fact: str, ev: str) -> None:
        self.operations.append({"type": "WRITE", "record": {"id": self._id("n"), "fact": fact, "evidence": [ev]}})

    def supersede(self, target: str, fact: str, ev: str) -> None:
        self.operations.append({
            "type": "SUPERSEDE",
            "target_id": target,
            "record": {"id": self._id("n"), "fact": fact, "evidence": [ev]},
        })

    def distractors(self, people: list[str], count: int, time: int) -> None:
        for _ in range(count):
            who = self.rng.choice(people)
            rel = self.rng.choice(list(RELATIONS))
            self.grounded_record(f"{who}|{rel}|{self.rng.choice(VALUES[rel])}", time)

    def build(self, metadata: dict[str, Any]) -> RelationalTransition:
        return RelationalTransition.from_dict({
            "case_id": self.case_id,
            "relations": {name: {"cardinality": c} for name, c in RELATIONS.items()},
            "sources": self.sources,
            "evidence": self.evidence,
            "before": self.before,
            "operations": self.operations,
            "metadata": metadata,
        })


def _two_values(rng: random.Random, relation: str) -> tuple[str, str]:
    a, b = rng.sample(VALUES[relation], 2)
    return a, b


def _provenance(b: _Builder, depth: int, valid: bool, family: str) -> int:
    person, *others = b.rng.sample(PEOPLE, 4)
    b.distractors(others, 3, time=1)
    relation = b.rng.choice(list(RELATIONS))
    fact = f"{person}|{relation}|{b.rng.choice(VALUES[relation])}"
    if family == "untrusted_root":
        if b.rng.random() < 0.5 and depth > 0:
            # Branching: the top summary also cites a shallow trusted turn about
            # something else; grounding still requires every parent. The deep
            # branch is one level shorter so its root stays `depth` hops away.
            deep = b.chain([fact], depth - 1, time=10, trusted=valid)
            side = b.observe([f"{person}|likes|{b.rng.choice(VALUES['likes'])}"], time=9)
            top = b.derive([deep, side], [fact])
        else:
            top = b.chain([fact], depth, time=10, trusted=valid)
        b.write(fact, top)
        return depth
    # cycle: chain of `depth` summaries; fault closes it back on itself instead
    # of bottoming out in a user turn.
    assert depth >= 1
    if valid:
        top = b.chain([fact], depth, time=10)
    else:
        ids = [b._id("e") for _ in range(depth)]
        for i, eid in enumerate(ids):
            parent = ids[(i + 1) % depth]
            b.evidence.append({"id": eid, "derived_from": [parent], "supports": [fact]})
        top = ids[0]
    b.write(fact, top)
    return depth


def _consistency(b: _Builder, depth: int, valid: bool, family: str) -> int:
    person, other, *rest = b.rng.sample(PEOPLE, 5)
    b.distractors(rest, 3, time=1)
    relation = b.rng.choice(FUNCTIONAL)
    old, new = _two_values(b.rng, relation)
    if family == "alias_write":
        names = b.alias_chain(person, depth, time=2)
        # A same-length chain for an unrelated person keeps the pair minimal.
        other_names = b.alias_chain(other, depth, time=2)
        holder = names[-1] if not valid else other_names[-1]
        b.grounded_record(f"{holder}|{relation}|{old}", time=5)
        fact = f"{person}|{relation}|{new}"
        b.write(fact, b.observe([fact], time=12))
        return depth
    # alias_merge: person~...~p_d holds `old`; other holds `new` (fault) or a
    # non-functional fact (valid); the candidate asserts person same_as other.
    names = b.alias_chain(person, depth, time=2)
    b.grounded_record(f"{names[-1]}|{relation}|{old}", time=5)
    if valid:
        b.grounded_record(f"{other}|likes|{b.rng.choice(VALUES['likes'])}", time=6)
    else:
        b.grounded_record(f"{other}|{relation}|{new}", time=6)
    fact = f"{person}|same_as|{other}"
    b.write(fact, b.observe([fact], time=12))
    # Both endpoints of the new edge are focus entities (distance 0); the
    # conflicting holder is `depth` same_as hops from one of them.
    return depth


def _history(b: _Builder, depth: int, valid: bool, family: str) -> int:
    person, other, *rest = b.rng.sample(PEOPLE, 5)
    b.distractors(rest, 3, time=1)
    relation = b.rng.choice(FUNCTIONAL)
    values = b.rng.sample(VALUES[relation], 4)
    if family == "stale_target":
        r1 = b.grounded_record(f"{person}|{relation}|{values[0]}", time=2)
        r2 = b.record(f"{person}|{relation}|{values[1]}", b.observe([f"{person}|{relation}|{values[1]}"], 4), r1)
        r3 = b.record(f"{person}|{relation}|{values[2]}", b.observe([f"{person}|{relation}|{values[2]}"], 6), r2)
        fact = f"{person}|{relation}|{values[3]}"
        b.supersede(r3 if valid else b.rng.choice([r1, r2]), fact, b.observe([fact], time=12))
        return 1
    if family == "temporal":
        # History: v0 (t=2) -> v1 (t=6, current head). The candidate restates
        # v0 from a summary of an old session (fault, root t=2) or reports a
        # genuinely new value from a recent session (valid, root t=11).
        old_fact = f"{person}|{relation}|{values[0]}"
        r1 = b.record(old_fact, b.chain([old_fact], 0, time=2))
        head_fact = f"{person}|{relation}|{values[1]}"
        head_depth = b.rng.randint(0, depth)
        r2 = b.record(head_fact, b.chain([head_fact], head_depth, time=6), r1)
        fact = old_fact if not valid else f"{person}|{relation}|{values[2]}"
        root_time = 2 if not valid else 11
        b.supersede(r2, fact, b.chain([fact], depth, time=root_time))
        return depth
    # slot_alias: target is about the replacement subject's alias at distance
    # `depth` (valid) or about an unlinked person (fault).
    names = b.alias_chain(person, depth, time=2)
    other_names = b.alias_chain(other, depth, time=2)
    subject = names[-1] if valid else other_names[-1]
    target = b.grounded_record(f"{subject}|{relation}|{values[0]}", time=5)
    fact = f"{person}|{relation}|{values[1]}"
    b.supersede(target, fact, b.observe([fact], time=12))
    return depth


FAMILIES: dict[tuple[str, str], Any] = {
    ("provenance", "untrusted_root"): _provenance,
    ("provenance", "cycle"): _provenance,
    ("consistency", "alias_write"): _consistency,
    ("consistency", "alias_merge"): _consistency,
    ("history", "stale_target"): _history,
    ("history", "temporal"): _history,
    ("history", "slot_alias"): _history,
}
FAULT_CODES = {
    ("provenance", "untrusted_root"): "UNGROUNDED_CLAIM",
    ("provenance", "cycle"): "UNGROUNDED_CLAIM",
    ("consistency", "alias_write"): "FUNCTIONAL_CONFLICT",
    ("consistency", "alias_merge"): "FUNCTIONAL_CONFLICT",
    ("history", "stale_target"): "STALE_TARGET",
    ("history", "temporal"): "TEMPORAL_ORDER_VIOLATION",
    ("history", "slot_alias"): "SLOT_MISMATCH",
}


def generate_suite(seed: int = 20261119, per_cell: int = 8) -> list[RelationalTransition]:
    """Generate the full minimal-pair suite deterministically from ``seed``."""
    rng = random.Random(seed)
    cases: list[RelationalTransition] = []
    for (mechanism, family), make in FAMILIES.items():
        depths = (1,) if family == "stale_target" else (
            tuple(d for d in DEPTHS if d >= 1) if family == "cycle" else DEPTHS
        )
        for depth in depths:
            for instance in range(per_cell):
                pair_seed = rng.randrange(2**32)
                for valid in (True, False):
                    case_id = f"REL-{mechanism[:4].upper()}-{family}-d{depth}-{instance:02d}-{'ok' if valid else 'bad'}"
                    b = _Builder(random.Random(pair_seed), case_id)
                    hops = make(b, depth, valid, family)
                    cases.append(b.build({
                        "mechanism": mechanism,
                        "family": family,
                        "depth": depth,
                        "required_hops": hops,
                        "valid": valid,
                        "expected_decision": "ACCEPT" if valid else "HOLD",
                        "fault_code": None if valid else FAULT_CODES[(mechanism, family)],
                        "pair_id": f"{mechanism}-{family}-d{depth}-{instance:02d}",
                        "split": "dev" if instance < per_cell // 2 else "heldout",
                    }))
    return cases
