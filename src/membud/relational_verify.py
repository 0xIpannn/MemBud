"""Verifiers for relational transitions (see ``docs/relational-hypothesis.md``).

All verifiers implement the same validity specification and return the usual
:class:`membud.schemas.Result` with three checks:

``provenance``
    Every proposed record's fact is supported by cited evidence that is
    *grounded*: a root observation from a trusted source, or derived evidence
    whose parents are all grounded (derivation cycles are never grounded).
``consistency``
    After applying the candidate, no entity holds two different values for a
    ``cardinality: one`` relation, where entity identity is the closure of
    current ``same_as`` facts.
``history``
    Every SUPERSEDE targets a *current* record (one no other record has already
    superseded), keeps the same slot (same relation, same resolved entity), and
    is grounded in strictly newer observations than the target (time of a
    record = latest root-source time reachable from its cited evidence).

Two independent implementations are provided so representation and reasoning
reach can be separated:

* :func:`verify_relational_text` reads the lossless flat-text serialization
  (:func:`membud.relational.relational_text`) and reasons over line-level
  lookups with a hop budget ``max_hops``. ``max_hops=None`` follows chains to
  closure. With a finite budget the verifier can run out of reach; the
  ``unknown`` policy decides whether an undecidable check PASSes
  (``"optimistic"``) or is UNCERTAIN and therefore HOLDs (``"pessimistic"``).
  This is the information-matched fair-text control: it receives exactly the
  same facts, sources, derivations and history as the graph verifier.
* :func:`verify_relational_graph` builds an explicit typed graph
  (:func:`build_relational_graph`) and derives every decision from graph
  algorithms: reachability over ``DERIVED_FROM``/``OBSERVED_IN`` edges, cycle
  detection, connected components over ``SAME_AS`` edges, and in-degree over
  ``SUPERSEDES`` edges. It does not import or call the text verifier.

Neither verifier reads case metadata or labels.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .relational import SAME_AS, RelationalTransition, parse_relational_text, relational_text
from .schemas import Check, Result

UNKNOWN = None  # tri-state: True / False / None (undecidable within budget)
UNKNOWN_POLICIES = ("optimistic", "pessimistic")


def _merge_all(values: list[bool | None]) -> bool | None:
    """Conjunction over tri-state values: any False wins, then any unknown."""
    if any(v is False for v in values):
        return False
    if any(v is None for v in values):
        return None
    return True


def _merge_any(values: list[bool | None]) -> bool | None:
    """Disjunction over tri-state values: any True wins, then any unknown."""
    if any(v is True for v in values):
        return True
    if any(v is None for v in values):
        return None
    return False


def _check(name: str, failures: dict[str, str], unknowns: list[str], ok_code: str, policy: str) -> Check:
    if failures:
        codes = sorted(set(failures.values()))
        return Check(
            name,
            False,
            f"{name} failed: " + "; ".join(f"{rid}={code}" for rid, code in sorted(failures.items())),
            sorted(failures),
            reason_code=codes[0] if len(codes) == 1 else "+".join(codes),
        )
    if unknowns:
        if policy == "pessimistic":
            return Check(
                name,
                False,
                f"{name} undecidable within hop budget for: " + ", ".join(sorted(unknowns)),
                sorted(unknowns),
                status="UNCERTAIN",
                reason_code="RELATIONAL_REACH_EXCEEDED",
            )
        return Check(
            name,
            True,
            f"{name} assumed to pass; undecidable within hop budget for: " + ", ".join(sorted(unknowns)),
            sorted(unknowns),
            reason_code=ok_code,
        )
    return Check(name, True, f"{name} holds", [], reason_code=ok_code)


def _decide(case_id: str, checks: list[Check], verifier: str, config: dict[str, Any]) -> Result:
    decision = "ACCEPT" if all(check.passed for check in checks) else "HOLD"
    return Result(case_id, decision, checks, verifier=verifier, verifier_config=config)


# --------------------------------------------------------------------------- #
# Text-side verifier (bounded or closure reach over flat lines)
# --------------------------------------------------------------------------- #


class _TextReasoner:
    def __init__(self, t: RelationalTransition, max_hops: int | None) -> None:
        self.t = t
        self.max_hops = max_hops
        self.sources = {s.id: s for s in t.sources}
        self.evidence = {e.id: e for e in t.evidence}
        self.before = {r.id: r for r in t.before}
        self.superseded_by: dict[str, str] = {}
        for r in t.before:
            if r.supersedes:
                self.superseded_by[r.supersedes] = r.id
        current = [r for r in t.before if r.id not in self.superseded_by]
        removed = {op.target_id for op in t.operations if op.type == "SUPERSEDE"}
        self.new_records = [op.record for op in t.operations]
        self.post = [r for r in current if r.id not in removed] + self.new_records
        self.aliases: dict[str, set[str]] = defaultdict(set)
        for r in self.post:
            if r.fact.relation == SAME_AS:
                self.aliases[r.fact.subject].add(r.fact.object)
                self.aliases[r.fact.object].add(r.fact.subject)

    def _budget(self) -> float:
        return float("inf") if self.max_hops is None else self.max_hops

    # -- provenance -------------------------------------------------------- #
    def grounded(self, evidence_id: str, budget: float, stack: frozenset[str]) -> bool | None:
        e = self.evidence[evidence_id]
        if e.source is not None:
            return self.sources[e.source].trusted
        if evidence_id in stack:
            return False  # derivation cycle: can never bottom out in an observation
        if budget <= 0:
            return UNKNOWN
        return _merge_all([
            self.grounded(parent, budget - 1, stack | {evidence_id}) for parent in e.derived_from
        ])

    def record_grounded(self, record) -> bool | None:
        supporting = [eid for eid in record.evidence if record.fact in self.evidence[eid].supports]
        if not supporting:
            return False
        return _merge_any([self.grounded(eid, self._budget(), frozenset()) for eid in supporting])

    # -- time -------------------------------------------------------------- #
    def evidence_time(self, evidence_id: str, budget: float, stack: frozenset[str]) -> int | None:
        e = self.evidence[evidence_id]
        if e.source is not None:
            return self.sources[e.source].time
        if evidence_id in stack:
            return -1  # cycle contributes no observation time
        if budget <= 0:
            return UNKNOWN
        times = [self.evidence_time(p, budget - 1, stack | {evidence_id}) for p in e.derived_from]
        if any(value is None for value in times):
            return UNKNOWN
        return max(times)

    def record_time(self, record) -> int | None:
        times = [self.evidence_time(eid, self._budget(), frozenset()) for eid in record.evidence]
        if any(value is None for value in times):
            return UNKNOWN
        return max(times)

    # -- aliases ----------------------------------------------------------- #
    def alias_neighborhood(self, entities: list[str]) -> tuple[set[str], bool]:
        """Entities within ``max_hops`` same_as hops of any start; flag truncation."""
        seen = set(entities)
        frontier = list(entities)
        depth = 0
        budget = self._budget()
        while frontier:
            if depth >= budget:
                truncated = any(n not in seen for e in frontier for n in self.aliases.get(e, ()))
                return seen, truncated
            nxt = []
            for e in frontier:
                for n in self.aliases.get(e, ()):
                    if n not in seen:
                        seen.add(n)
                        nxt.append(n)
            frontier = nxt
            depth += 1
        return seen, False


def verify_relational_text(
    t: RelationalTransition | str,
    *,
    max_hops: int | None = 1,
    unknown: str = "optimistic",
) -> Result:
    """Information-matched fair-text verifier with a configurable hop budget."""
    if unknown not in UNKNOWN_POLICIES:
        raise ValueError(f"unknown policy must be one of {UNKNOWN_POLICIES}")
    if max_hops is not None and max_hops < 1:
        raise ValueError("max_hops must be >= 1 or None")
    # The text verifier consumes the flat serialization, never Python objects.
    text = t if isinstance(t, str) else relational_text(t)
    parsed = parse_relational_text(text)
    r = _TextReasoner(parsed, max_hops)

    prov_fail: dict[str, str] = {}
    prov_unknown: list[str] = []
    for record in r.new_records:
        if not any(record.fact in r.evidence[eid].supports for eid in record.evidence):
            prov_fail[record.id] = "UNSUPPORTED_CLAIM"
            continue
        verdict = r.record_grounded(record)
        if verdict is False:
            prov_fail[record.id] = "UNGROUNDED_CLAIM"
        elif verdict is None:
            prov_unknown.append(record.id)

    cons_fail: dict[str, str] = {}
    cons_unknown: list[str] = []
    functional = {name for name, c in parsed.relations.items() if c == "one"}
    for record in r.new_records:
        focus = [record.fact.subject]
        if record.fact.relation == SAME_AS:
            focus.append(record.fact.object)
        reached, truncated = r.alias_neighborhood(focus)
        relations = functional if record.fact.relation == SAME_AS else (
            {record.fact.relation} & functional
        )
        conflict = False
        for relation in relations:
            values = {p.fact.object for p in r.post if p.fact.relation == relation and p.fact.subject in reached}
            if len(values) > 1:
                conflict = True
        if conflict:
            cons_fail[record.id] = "FUNCTIONAL_CONFLICT"
        elif truncated and relations:
            cons_unknown.append(record.id)

    hist_fail: dict[str, str] = {}
    hist_unknown: list[str] = []
    for op in parsed.operations:
        if op.type != "SUPERSEDE":
            continue
        target = r.before[op.target_id]
        new = op.record
        if op.target_id in r.superseded_by:
            hist_fail[new.id] = "STALE_TARGET"
            continue
        if target.fact.relation != new.fact.relation:
            hist_fail[new.id] = "SLOT_MISMATCH"
            continue
        unknown_here = False
        if target.fact.subject != new.fact.subject:
            seen, cut = r.alias_neighborhood([new.fact.subject])
            if target.fact.subject not in seen:
                if cut:
                    unknown_here = True
                else:
                    hist_fail[new.id] = "SLOT_MISMATCH"
                    continue
        new_time, old_time = r.record_time(new), r.record_time(target)
        if new_time is None or old_time is None:
            unknown_here = True
        elif new_time <= old_time:
            hist_fail[new.id] = "TEMPORAL_ORDER_VIOLATION"
            continue
        if unknown_here:
            hist_unknown.append(new.id)

    checks = [
        _check("provenance", prov_fail, prov_unknown, "PROVENANCE_OK", unknown),
        _check("consistency", cons_fail, cons_unknown, "CONSISTENCY_OK", unknown),
        _check("history", hist_fail, hist_unknown, "HISTORY_OK", unknown),
    ]
    name = "text_closure" if max_hops is None else f"text_k{max_hops}_{unknown}"
    return _decide(parsed.case_id, checks, name, {"max_hops": max_hops, "unknown": unknown})


# --------------------------------------------------------------------------- #
# Graph construction and graph-algorithmic verifier
# --------------------------------------------------------------------------- #


def build_relational_graph(t: RelationalTransition) -> dict[str, Any]:
    """Typed property graph of a relational transition.

    Nodes: ``source``, ``evidence``, ``record`` (stage before/new), ``entity``,
    ``value``, ``relation``. Edges: ``OBSERVED_IN`` (evidence->source),
    ``DERIVED_FROM`` (evidence->evidence), ``SUPPORTS`` (evidence->record whose
    fact it states), ``CITES`` (record->evidence), ``SUBJECT`` (record->entity),
    ``OBJECT`` (record->value or entity), ``OF_RELATION`` (record->relation),
    ``SUPERSEDES`` (record->record, including proposed SUPERSEDE operations),
    and ``SAME_AS`` (entity<->entity, from current same_as records only).
    """
    t.validate()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def node(node_id: str, **attrs: Any) -> str:
        nodes.setdefault(node_id, {"id": node_id, **attrs})
        return node_id

    def edge(a: str, kind: str, b: str) -> None:
        edges.append({"from": a, "type": kind, "to": b})

    for name, cardinality in t.relations.items():
        node(f"relation:{name}", type="relation", cardinality=cardinality)
    node(f"relation:{SAME_AS}", type="relation", cardinality="many")
    for s in t.sources:
        node(f"source:{s.id}", type="source", trusted=s.trusted, time=s.time, kind=s.kind)
    for e in t.evidence:
        node(f"evidence:{e.id}", type="evidence")
        if e.source is not None:
            edge(f"evidence:{e.id}", "OBSERVED_IN", f"source:{e.source}")
        for parent in e.derived_from:
            edge(f"evidence:{e.id}", "DERIVED_FROM", f"evidence:{parent}")

    supersede_targets = {op.target_id: op.record.id for op in t.operations if op.type == "SUPERSEDE"}
    stages = [("before", r) for r in t.before] + [("new", op.record) for op in t.operations]
    for stage, r in stages:
        rid = node(f"record:{r.id}", type="record", stage=stage)
        subject = node(f"entity:{r.fact.subject}", type="entity", name=r.fact.subject)
        edge(rid, "SUBJECT", subject)
        edge(rid, "OF_RELATION", f"relation:{r.fact.relation}")
        if r.fact.relation == SAME_AS:
            obj = node(f"entity:{r.fact.object}", type="entity", name=r.fact.object)
        else:
            obj = node(f"value:{r.fact.object}", type="value", name=r.fact.object)
        edge(rid, "OBJECT", obj)
        for eid in r.evidence:
            edge(rid, "CITES", f"evidence:{eid}")
        if r.supersedes is not None:
            edge(rid, "SUPERSEDES", f"record:{r.supersedes}")
    for e in t.evidence:
        for fact in e.supports:
            for stage, r in stages:
                if r.fact == fact:
                    edge(f"evidence:{e.id}", "SUPPORTS", f"record:{r.id}")

    # SAME_AS edges come from records that are current after the candidate.
    superseded_in_history = {r.supersedes for r in t.before if r.supersedes}
    for stage, r in stages:
        if r.fact.relation != SAME_AS:
            continue
        if stage == "before" and (r.id in superseded_in_history or r.id in supersede_targets):
            continue
        edge(f"entity:{r.fact.subject}", "SAME_AS", f"entity:{r.fact.object}")
        edge(f"entity:{r.fact.object}", "SAME_AS", f"entity:{r.fact.subject}")
    return {"case_id": t.case_id, "nodes": list(nodes.values()), "edges": edges}


def verify_relational_graph(t: RelationalTransition, *, graph: dict[str, Any] | None = None) -> Result:
    """Decide the relational checks purely from graph structure."""
    graph = graph if graph is not None else build_relational_graph(t)
    nodes = {n["id"]: n for n in graph["nodes"]}
    out: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    inc: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for e in graph["edges"]:
        out[e["from"]][e["type"]].append(e["to"])
        inc[e["to"]][e["type"]].append(e["from"])

    def one(node_id: str, kind: str) -> str:
        return out[node_id][kind][0]

    # Grounding: an evidence node is grounded iff every DERIVED_FROM successor is
    # grounded and leaves are OBSERVED_IN a trusted source; nodes on a cycle are
    # not grounded. Computed by DFS with colouring over the derivation subgraph.
    grounded: dict[str, bool] = {}
    roots_time: dict[str, int] = {}
    state: dict[str, int] = {}

    def visit(ev: str) -> None:
        state[ev] = 1
        observed = out[ev]["OBSERVED_IN"]
        if observed:
            src = nodes[observed[0]]
            grounded[ev] = bool(src["trusted"])
            roots_time[ev] = int(src["time"])
            state[ev] = 2
            return
        ok, latest = True, -1
        for parent in out[ev]["DERIVED_FROM"]:
            if state.get(parent) == 1:
                ok = False  # back edge -> cycle
                continue
            if parent not in state:
                visit(parent)
            ok = ok and grounded.get(parent, False)
            latest = max(latest, roots_time.get(parent, -1))
        grounded[ev] = ok
        roots_time[ev] = latest
        state[ev] = 2

    evidence_nodes = [n for n, d in nodes.items() if d["type"] == "evidence"]
    for ev in evidence_nodes:
        if ev not in state:
            visit(ev)
    # A node that reaches a cycle may have been finalised before the cycle was
    # closed; propagate "ungrounded" along reverse DERIVED_FROM to a fixpoint.
    on_cycle = _cyclic_nodes(evidence_nodes, out)
    queue = deque(n for n in evidence_nodes if not grounded[n] or n in on_cycle)
    for n in on_cycle:
        grounded[n] = False
    while queue:
        n = queue.popleft()
        for child in inc[n]["DERIVED_FROM"]:
            if grounded.get(child):
                grounded[child] = False
                queue.append(child)
    # Latest observation time = max over all reachable roots (cycle-safe BFS).
    for ev in evidence_nodes:
        seen, stack, latest = {ev}, [ev], -1
        while stack:
            cur = stack.pop()
            for s in out[cur]["OBSERVED_IN"]:
                latest = max(latest, int(nodes[s]["time"]))
            for p in out[cur]["DERIVED_FROM"]:
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        roots_time[ev] = latest

    records = {n: d for n, d in nodes.items() if d["type"] == "record"}
    new_records = sorted(n for n, d in records.items() if d["stage"] == "new")

    def rid(node_id: str) -> str:
        return node_id.removeprefix("record:")

    prov_fail: dict[str, str] = {}
    for rec in new_records:
        cited = set(out[rec]["CITES"])
        supporting = [ev for ev in inc[rec]["SUPPORTS"] if ev in cited]
        if not supporting:
            prov_fail[rid(rec)] = "UNSUPPORTED_CLAIM"
        elif not any(grounded[ev] for ev in supporting):
            prov_fail[rid(rec)] = "UNGROUNDED_CLAIM"

    # Post state: records with no incoming SUPERSEDES edge.
    post = [n for n in records if not inc[n]["SUPERSEDES"]]
    component: dict[str, int] = {}
    for ent in (n for n, d in nodes.items() if d["type"] == "entity"):
        if ent in component:
            continue
        cid = len(set(component.values()))
        stack = [ent]
        component[ent] = cid
        while stack:
            cur = stack.pop()
            for nb in out[cur]["SAME_AS"]:
                if nb not in component:
                    component[nb] = cid
                    stack.append(nb)

    def relation_of(rec: str) -> str:
        return one(rec, "OF_RELATION")

    slot_values: dict[tuple[int, str], set[str]] = defaultdict(set)
    for rec in post:
        rel = relation_of(rec)
        if nodes[rel]["cardinality"] == "one":
            slot_values[(component[one(rec, "SUBJECT")], rel)].add(one(rec, "OBJECT"))
    cons_fail: dict[str, str] = {}
    for rec in new_records:
        rel = relation_of(rec)
        comps = {component[one(rec, "SUBJECT")]}
        if rel == f"relation:{SAME_AS}":
            comps.add(component[one(rec, "OBJECT")])
            rels = [r for r, d in nodes.items() if d["type"] == "relation" and d["cardinality"] == "one"]
        else:
            rels = [rel] if nodes[rel]["cardinality"] == "one" else []
        if any(len(slot_values[(c, r)]) > 1 for c in comps for r in rels):
            cons_fail[rid(rec)] = "FUNCTIONAL_CONFLICT"

    def record_time(rec: str) -> int:
        return max(roots_time[ev] for ev in out[rec]["CITES"])

    hist_fail: dict[str, str] = {}
    for rec in new_records:
        targets = out[rec]["SUPERSEDES"]
        if not targets:
            continue
        target = targets[0]
        if any(nodes[s]["stage"] == "before" for s in inc[target]["SUPERSEDES"]):
            hist_fail[rid(rec)] = "STALE_TARGET"
        elif relation_of(target) != relation_of(rec) or (
            component[one(target, "SUBJECT")] != component[one(rec, "SUBJECT")]
        ):
            hist_fail[rid(rec)] = "SLOT_MISMATCH"
        elif record_time(rec) <= record_time(target):
            hist_fail[rid(rec)] = "TEMPORAL_ORDER_VIOLATION"

    checks = [
        _check("provenance", prov_fail, [], "PROVENANCE_OK", "optimistic"),
        _check("consistency", cons_fail, [], "CONSISTENCY_OK", "optimistic"),
        _check("history", hist_fail, [], "HISTORY_OK", "optimistic"),
    ]
    return _decide(graph["case_id"], checks, "graph", {})


def _cyclic_nodes(evidence_nodes: list[str], out: dict[str, dict[str, list[str]]]) -> set[str]:
    """Nodes on a DERIVED_FROM cycle (Tarjan SCCs of size > 1 or self-loops)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: set[str] = set()
    counter = [0]

    def strong(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in out[v]["DERIVED_FROM"]:
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            scc = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            if len(scc) > 1 or v in out[v]["DERIVED_FROM"]:
                result.update(scc)

    for v in evidence_nodes:
        if v not in index:
            strong(v)
    return result


# --------------------------------------------------------------------------- #
# Strategy registry
# --------------------------------------------------------------------------- #

STRATEGIES: dict[str, dict[str, Any]] = {
    "text_k1_optimistic": {"kind": "text", "max_hops": 1, "unknown": "optimistic"},
    "text_k1_pessimistic": {"kind": "text", "max_hops": 1, "unknown": "pessimistic"},
    "text_k2_optimistic": {"kind": "text", "max_hops": 2, "unknown": "optimistic"},
    "text_closure": {"kind": "text", "max_hops": None, "unknown": "optimistic"},
    "graph": {"kind": "graph"},
}


def run_strategy(name: str, t: RelationalTransition) -> Result:
    spec = STRATEGIES[name]
    if spec["kind"] == "graph":
        return verify_relational_graph(t)
    return verify_relational_text(t, max_hops=spec["max_hops"], unknown=spec["unknown"])


__all__ = [
    "STRATEGIES",
    "build_relational_graph",
    "run_strategy",
    "verify_relational_graph",
    "verify_relational_text",
]
