"""Downstream conversational QA utility benchmark: guarded vs unguarded memory.

The relational suite (``membud.relational_cases``) measures decisions on
isolated transitions. This module measures what those decisions are *worth*:
it simulates multi-session conversations, runs a noisy memory extractor that
proposes relational transitions turn by turn, commits them to a memory store
under a gating policy, and then answers questions from that store. Answers are
scored against the conversation's ground-truth world state.

Pipeline per conversation (seeded, deterministic, no model calls):

1. **World.** A user and a friend, each with nickname chains (``same_as``),
   functional attributes (``lives_in``, ``works_at``, ``favorite_food``) and,
   for the user, a many-valued ``likes``. Over ``sessions`` sessions the
   user's attributes change.
2. **Extraction.** Each ground-truth event becomes a user turn. The extractor
   proposes a transition for it, sometimes through session-summary chains
   (derived evidence) and sometimes under a nickname. The extractor reads the
   store it is updating to pick SUPERSEDE targets, so a held update changes
   what later proposals target. Independently it injects faults:

   ``hallucination``  assistant speculation, possibly laundered through
                      summaries, proposed as a user fact (provenance fault)
   ``misattribution`` the friend's attribute written under the user's
                      name/nickname without superseding (consistency fault)
   ``stale_revival``  a summary of an old session re-asserts the previous
                      value as an update (temporal history fault)
   ``wrong_target``   a genuine user update applied as a SUPERSEDE of the
                      friend's record (slot fault; the friend's memory is
                      silently overwritten)
   ``stale_target``   a genuine user update superseding an already-superseded
                      record (forked history)

   ``wrong_target`` and ``stale_target`` carry *real* information in a
   malformed transition: a gate that holds them protects the store's
   structure but loses the update. This is the false-rejection side of the
   trade-off and is deliberately part of the benchmark.
3. **Gating.** Each proposal is committed only on ACCEPT. ``unguarded`` commits
   everything that applies; ``oracle_gate`` commits exactly the proposals the
   generator labels valid (a perfect gate, not a perfect memory); the other
   policies run the relational verifiers.
4. **QA.** After every session, questions ask for each functional attribute
   and the ``likes`` set of the user, and each functional attribute of the
   friend. A fixed deterministic reader resolves nicknames through
   ``same_as`` memories, reads current (non-superseded) records, and for a
   functional slot with several values answers with the most recently
   committed one. The same reader serves every policy.

Validity labels come from the injected fault type, never from a verifier. When
the store a policy built lacks what a fault needs, the proposal is recorded as
a *fallback* and labelled valid: no superseded record exists for
``stale_target`` or no friend record for ``wrong_target`` (the extractor then
targets the current head), no head exists for ``stale_revival`` (a plain
write), or the store holds no differing value for ``misattribution`` (no
conflict arises). These are mechanical store conditions, not verifier calls.
"""
from __future__ import annotations

import random
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .relational import SAME_AS, Fact, RelationalTransition, RelEvidence, RelOperation, RelRecord, Source
from .relational_cases import FUNCTIONAL, PEOPLE, RELATIONS, VALUES
from .relational_verify import STRATEGIES, run_strategy

FAULT_TYPES = ("hallucination", "misattribution", "stale_revival", "wrong_target", "stale_target")
POLICIES = ("unguarded", *STRATEGIES, "oracle_gate")


@dataclass(frozen=True)
class BenchmarkConfig:
    conversations: int = 200
    sessions: int = 6
    updates_per_session: int = 3
    fault_rate: float = 0.35
    summary_depth_max: int = 3
    alias_depth_max: int = 3
    seed: int = 20261119

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# --------------------------------------------------------------------------- #
# Memory store and reader
# --------------------------------------------------------------------------- #


@dataclass
class MemoryStore:
    """Append-only relational history plus all provenance it has seen."""

    relations: dict[str, str]
    sources: dict[str, Source] = field(default_factory=dict)
    evidence: dict[str, RelEvidence] = field(default_factory=dict)
    records: list[RelRecord] = field(default_factory=list)
    commit_order: dict[str, int] = field(default_factory=dict)

    def transition(
        self,
        case_id: str,
        sources: list[Source],
        evidence: list[RelEvidence],
        operations: list[RelOperation],
    ) -> RelationalTransition:
        all_sources = {**self.sources, **{s.id: s for s in sources}}
        all_evidence = {**self.evidence, **{e.id: e for e in evidence}}
        t = RelationalTransition(
            case_id=case_id,
            relations=dict(self.relations),
            sources=tuple(all_sources.values()),
            evidence=tuple(all_evidence.values()),
            before=tuple(self.records),
            operations=tuple(operations),
        )
        t.validate()
        return t

    def commit(self, t: RelationalTransition) -> None:
        for s in t.sources:
            self.sources[s.id] = s
        for e in t.evidence:
            self.evidence[e.id] = e
        for op in t.operations:
            self.commit_order[op.record.id] = len(self.commit_order)
            self.records.append(op.record)

    def current(self) -> list[RelRecord]:
        superseded = {r.supersedes for r in self.records if r.supersedes}
        return [r for r in self.records if r.id not in superseded]

    def resolve(self, entity: str) -> set[str]:
        adj: dict[str, set[str]] = defaultdict(set)
        for r in self.current():
            if r.fact.relation == SAME_AS:
                adj[r.fact.subject].add(r.fact.object)
                adj[r.fact.object].add(r.fact.subject)
        seen, stack = {entity}, [entity]
        while stack:
            for n in adj[stack.pop()]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        return seen

    def slot(self, entity: str, relation: str, *, current: bool = True) -> list[RelRecord]:
        names = self.resolve(entity)
        pool = self.current() if current else self.records
        hits = [r for r in pool if r.fact.relation == relation and r.fact.subject in names]
        return sorted(hits, key=lambda r: self.commit_order.get(r.id, -1))

    def superseded_slot(self, entity: str, relation: str) -> list[RelRecord]:
        current_ids = {r.id for r in self.current()}
        return [r for r in self.slot(entity, relation, current=False) if r.id not in current_ids]

    def answer(self, entity: str, relation: str) -> str | set[str] | None:
        hits = self.slot(entity, relation)
        if RELATIONS[relation] == "many":
            return {r.fact.object for r in hits}
        return hits[-1].fact.object if hits else None


# --------------------------------------------------------------------------- #
# Conversation simulator and noisy extractor
# --------------------------------------------------------------------------- #


@dataclass
class Proposal:
    """A store-independent extraction; targets are resolved against a store."""

    case_id: str
    session: int
    kind: str  # "update" or a fault type
    sources: list[Source]
    evidence: list[RelEvidence]
    record_id: str
    fact: Fact
    cites: str
    selector: str | None  # None (WRITE) | "head" | "stale" | "friend_head"
    valid: bool
    depth: int

    def materialize(self, store: MemoryStore, user: str, friend: str) -> tuple[RelationalTransition, bool, bool]:
        """Return (transition, valid, fell_back) for this store."""
        target: str | None = None
        valid, fell_back = self.valid, False
        if self.selector is not None:
            rel = self.fact.relation
            head = store.slot(user, rel)
            candidates: list[RelRecord] = []
            if self.selector == "head":
                candidates = head[-1:]
            elif self.selector == "stale":
                candidates = store.superseded_slot(user, rel)[-1:]
            elif self.selector == "friend_head":
                candidates = store.slot(friend, rel)[-1:]
            if not candidates and self.selector != "head":
                candidates, fell_back, valid = head[-1:], True, True
            if not candidates and self.kind == "stale_revival":
                fell_back, valid = True, True  # nothing to revive over: a plain write
            if candidates:
                target = candidates[0].id
        if self.kind == "misattribution" and not any(
            r.fact.object != self.fact.object for r in store.slot(user, self.fact.relation)
        ):
            fell_back, valid = True, True  # no differing value stored: no conflict
        record = RelRecord(self.record_id, self.fact, (self.cites,), target)
        op = RelOperation("SUPERSEDE" if target else "WRITE", record, target)
        return store.transition(self.case_id, self.sources, self.evidence, [op]), valid, fell_back


@dataclass
class _World:
    rng: random.Random
    cid: str
    cfg: BenchmarkConfig
    user: str = ""
    friend: str = ""
    clock: int = 0
    n: int = 0
    truth: dict[str, Any] = field(default_factory=dict)
    friend_truth: dict[str, str] = field(default_factory=dict)
    past: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    said: dict[str, list[tuple[str, int]]] = field(default_factory=lambda: defaultdict(list))
    nicknames: list[str] = field(default_factory=list)
    friend_nicks: list[str] = field(default_factory=list)

    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{self.cid}.{prefix}{self.n}"

    def set_truth(self, rel: str, value: str) -> None:
        if rel in self.truth and self.truth[rel] != value:
            self.past[rel].add(self.truth[rel])
        self.truth[rel] = value
        self.said[rel].append((value, self.clock))

    def subject(self) -> tuple[str, int]:
        if self.nicknames and self.rng.random() < 0.5:
            k = self.rng.randint(1, len(self.nicknames))
            return self.nicknames[k - 1], k
        return self.user, 0

    def propose(
        self,
        session: int,
        kind: str,
        fact: Fact,
        selector: str | None,
        valid: bool,
        *,
        trusted: bool = True,
        root_time: int | None = None,
        summarize: bool = True,
        extra_depth: int = 0,
    ) -> Proposal:
        self.clock += 1
        src = Source(
            self._id("u" if trusted else "a"),
            "user_turn" if trusted else "assistant_turn",
            trusted,
            self.clock if root_time is None else root_time,
        )
        root = RelEvidence(self._id("e"), (fact,), src.id, ())
        depth = 0
        if summarize and self.rng.random() < 0.6:
            depth = self.rng.randint(1, self.cfg.summary_depth_max)
        chain, top = [], root
        for _ in range(depth):
            top = RelEvidence(self._id("e"), (fact,), None, (top.id,))
            chain.append(top)
        return Proposal(
            self._id("p"), session, kind, [src], [root, *chain], self._id("r"), fact, top.id,
            selector, valid, max(depth, extra_depth),
        )


def simulate_conversation(index: int, cfg: BenchmarkConfig) -> tuple[_World, list[list[Proposal]], list[dict[str, Any]]]:
    """Return the world, per-session proposals, and the truth after each session."""
    rng = random.Random(f"{cfg.seed}:{index}")
    w = _World(rng, f"c{index:03d}", cfg)
    w.user, w.friend = rng.sample(PEOPLE, 2)
    sessions: list[list[Proposal]] = []
    truths: list[dict[str, Any]] = []
    for session in range(cfg.sessions):
        props: list[Proposal] = []
        if session == 0:
            for who, nicks in ((w.user, w.nicknames), (w.friend, w.friend_nicks)):
                prev = who
                for i in range(1, rng.randint(1, cfg.alias_depth_max) + 1):
                    nick = f"{who}.n{i}"
                    props.append(w.propose(session, "update", Fact(prev, SAME_AS, nick), None, True))
                    nicks.append(nick)
                    prev = nick
            for rel in FUNCTIONAL:
                value = rng.choice(VALUES[rel])
                props.append(w.propose(session, "update", Fact(w.user, rel, value), None, True))
                w.set_truth(rel, value)
                fvalue = rng.choice([v for v in VALUES[rel] if v != value])
                w.friend_truth[rel] = fvalue
                props.append(w.propose(session, "update", Fact(w.friend_nicks[-1], rel, fvalue), None, True))
            w.truth["likes"] = set()
        else:
            for _ in range(cfg.updates_per_session):
                if rng.random() < cfg.fault_rate:
                    props.append(_fault(w, session, rng.choice(FAULT_TYPES)))
                else:
                    props.append(_genuine(w, session))
        sessions.append(props)
        truths.append({
            "user": {k: (set(v) if isinstance(v, set) else v) for k, v in w.truth.items()},
            "friend": dict(w.friend_truth),
            "past": {k: set(v) for k, v in w.past.items()},
        })
    return w, sessions, truths


def _genuine(w: _World, session: int) -> Proposal:
    rel = w.rng.choice([*FUNCTIONAL, "likes"])
    subject, hops = w.subject()
    if rel == "likes":
        value = w.rng.choice([v for v in VALUES["likes"] if v not in w.truth["likes"]] or VALUES["likes"])
        w.truth["likes"].add(value)
        return w.propose(session, "update", Fact(subject, rel, value), None, True, extra_depth=hops)
    value = w.rng.choice([v for v in VALUES[rel] if v != w.truth[rel]])
    p = w.propose(session, "update", Fact(subject, rel, value), "head", True, extra_depth=hops)
    w.set_truth(rel, value)
    return p


def _fault(w: _World, session: int, kind: str) -> Proposal:
    rng = w.rng
    subject, hops = w.subject()
    rel = rng.choice(FUNCTIONAL)
    if kind == "stale_revival" and len(w.said[rel]) < 2:
        kind = "hallucination"
    if kind == "misattribution":
        unused = [v for v in VALUES[rel] if v not in {value for value, _ in w.said[rel]}]
        if not unused:
            kind = "hallucination"
    if kind == "hallucination":
        rel = rng.choice([*FUNCTIONAL, "likes"])
        value = rng.choice(VALUES[rel])
        selector = None if rel == "likes" else "head"
        return w.propose(session, kind, Fact(subject, rel, value), selector, False, trusted=False)
    if kind == "misattribution":
        # A value the user never stated (it is the friend's), so it can never
        # coincide with anything the store legitimately holds.
        value = rng.choice(unused)
        return w.propose(session, kind, Fact(subject, rel, value), None, False, extra_depth=hops)
    if kind == "stale_revival":
        # The earliest statement: never newer than any head a store can hold.
        old_value, old_time = w.said[rel][0]
        return w.propose(session, kind, Fact(subject, rel, old_value), "head", False, root_time=old_time)
    value = rng.choice([v for v in VALUES[rel] if v != w.truth[rel]])
    selector = "friend_head" if kind == "wrong_target" else "stale"
    p = w.propose(session, kind, Fact(subject, rel, value), selector, False, extra_depth=hops)
    w.set_truth(rel, value)  # the user really did say this
    return p


# --------------------------------------------------------------------------- #
# Running policies and scoring
# --------------------------------------------------------------------------- #


def _decide(policy: str, valid: bool, t: RelationalTransition) -> tuple[str, float | None]:
    if policy == "unguarded":
        return "ACCEPT", None
    if policy == "oracle_gate":
        return ("ACCEPT" if valid else "HOLD"), None
    start = _time.perf_counter()
    result = run_strategy(policy, t)
    return result.decision, (_time.perf_counter() - start) * 1000


def _outcome(predicted: Any, gold: str, past: set[str]) -> str:
    if predicted == gold:
        return "correct"
    if predicted is None:
        return "missing"
    return "stale" if predicted in past else "wrong"


def _score(store: MemoryStore, user: str, friend: str, truth: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"functional": {}, "friend": {}}
    for rel in FUNCTIONAL:
        out["functional"][rel] = _outcome(store.answer(user, rel), truth["user"][rel], truth["past"].get(rel, set()))
        out["friend"][rel] = _outcome(store.answer(friend, rel), truth["friend"][rel], set())
    predicted = store.answer(user, "likes")
    gold = truth["user"]["likes"]
    assert isinstance(predicted, set)
    tp = len(predicted & gold)
    precision = tp / len(predicted) if predicted else 1.0
    recall = tp / len(gold) if gold else 1.0
    out["likes_f1"] = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return out


def run_conversation(index: int, cfg: BenchmarkConfig, policies: tuple[str, ...] = POLICIES) -> dict[str, Any]:
    world, sessions, truths = simulate_conversation(index, cfg)
    rows: dict[str, Any] = {}
    for policy in policies:
        store = MemoryStore(dict(RELATIONS))
        commits = {"accepted_valid": 0, "accepted_invalid": 0, "held_valid": 0, "held_invalid": 0, "fallbacks": 0}
        by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"ACCEPT": 0, "HOLD": 0})
        latencies: list[float] = []
        per_session = []
        for s_idx, props in enumerate(sessions):
            for p in props:
                t, valid, fell_back = p.materialize(store, world.user, world.friend)
                decision, ms = _decide(policy, valid, t)
                if ms is not None:
                    latencies.append(ms)
                commits["fallbacks"] += int(fell_back)
                kind = "update" if fell_back else p.kind
                by_kind[kind][decision] += 1
                key = ("accepted" if decision == "ACCEPT" else "held") + ("_valid" if valid else "_invalid")
                commits[key] += 1
                if decision == "ACCEPT":
                    store.commit(t)
            per_session.append(_score(store, world.user, world.friend, truths[s_idx]))
        rows[policy] = {
            "commits": commits,
            "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
            "latency_ms": latencies,
            "sessions": per_session,
        }
    return {"conversation": index, "user": world.user, "friend": world.friend, "policies": rows}


def run_benchmark(cfg: BenchmarkConfig, policies: tuple[str, ...] = POLICIES) -> list[dict[str, Any]]:
    return [run_conversation(i, cfg, policies) for i in range(cfg.conversations)]


def conversation_metrics(row: dict[str, Any], policy: str) -> dict[str, float]:
    """Per-conversation utility summary used for paired statistics."""
    data = row["policies"][policy]
    user = [o for s in data["sessions"] for o in s["functional"].values()]
    friend = [o for s in data["sessions"] for o in s["friend"].values()]
    final = list(data["sessions"][-1]["functional"].values())
    likes = [s["likes_f1"] for s in data["sessions"]]
    n = len(user)
    return {
        "user_em": sum(o == "correct" for o in user) / n,
        "final_user_em": sum(o == "correct" for o in final) / len(final),
        "friend_em": sum(o == "correct" for o in friend) / len(friend),
        "overall_em": sum(o == "correct" for o in (*user, *friend)) / (n + len(friend)),
        "user_stale": sum(o == "stale" for o in user) / n,
        "user_wrong": sum(o == "wrong" for o in user) / n,
        "user_missing": sum(o == "missing" for o in user) / n,
        "likes_f1": sum(likes) / len(likes),
    }
