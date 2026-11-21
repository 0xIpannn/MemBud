# Relational graph hypothesis: multi-hop provenance, alias contradictions, supersession chains

**Date:** 2026-11-19
**Artifacts:** `data/results/relational-v1/evaluation.json` (byte-stable), `timing.json`
**Case package:** `data/manifests/relational-v1.json` → `data/cases/relational-v1.jsonl`, `data/labels/relational-v1.jsonl` (SHA-256 pinned)
**Reproduce:** `PYTHONPATH=src .venv/bin/python scripts/run_relational_eval.py --output data/results/relational-v1` (rules-only, 0 paid API calls)
**Code:** `src/membud/relational.py` (format), `src/membud/relational_verify.py` (verifiers), `src/membud/relational_cases.py` (generator)
**Tests:** `tests/test_relational.py`

## Why this experiment

The [fairness audit](fairness-audit.md) found that the v2 graph-vs-text advantage
came from **information access**. The text baseline never read
`evidence.contradicts`, and once a fair text control read it, the two verifiers
agreed on all 36 cases. The audit's implication #2 named what a real test needs.
The cases must turn on relational structure, specifically multi-hop
provenance/derivation, entity-relation contradictions across records, and
temporal/supersession ordering, and the text control must get the same facts.
The canonical v2 format cannot express any of that, because claims are opaque
strings. This experiment adds a relational format and runs that test.

## Format (`membud-relational` v1)

This is an experiment-only interchange format, separate from canonical transitions
v2 (see [format-policy.md](format-policy.md)).

- **Facts** are typed triples `subject|relation|object`. Relations declare
  `cardinality: one | many`. `same_as` is built in and links entity names
  (nicknames, aliases).
- **Sources** are roots with `trusted` (a user turn) or untrusted (an assistant
  turn), plus a monotonic `time`.
- **Evidence** is either a root observation (`source`) or derived from other
  evidence (`derived_from`, e.g. session summaries).
- **History**: prior records cite evidence and may `supersede` an older record.
  Superseded records stay in history. Forked history is representable on
  purpose, because an unguarded store can contain it.
- **Operations**: `WRITE {record}` and `SUPERSEDE {target_id, record}`.

## Validity specification (identical for every verifier)

| Check | Passes when | Fail codes |
|---|---|---|
| provenance | each proposed fact is supported by cited evidence that is *grounded*: a trusted root, or derived evidence whose parents are **all** grounded; derivation cycles are never grounded | `UNSUPPORTED_CLAIM`, `UNGROUNDED_CLAIM` |
| consistency | after the candidate, no entity, taken as the closure of current `same_as`, holds two values of a `cardinality: one` relation | `FUNCTIONAL_CONFLICT` |
| history | each SUPERSEDE targets a *current* record, keeps the slot (same relation, same resolved entity), and is grounded in strictly newer observations than the target (record time = latest reachable root time) | `STALE_TARGET`, `SLOT_MISMATCH`, `TEMPORAL_ORDER_VIOLATION` |

## Strategies: separating representation from reasoning reach

All verifiers get **the same information**. The text verifiers read a lossless
flat-text serialization: `relational_text`/`parse_relational_text` round-trip
every case exactly, and a test checks this.

| Strategy | Representation | Reach | When a check can't be decided within reach |
|---|---|---|---|
| `unguarded` | none | none | — (applies everything) |
| `text_k1_optimistic` | flat text lines | 1 relational hop | assume PASS |
| `text_k1_pessimistic` | flat text lines | 1 relational hop | UNCERTAIN → HOLD |
| `text_k2_optimistic` | flat text lines | 2 hops | assume PASS |
| `text_closure` | flat text lines | unbounded (closure) | — |
| `graph` | typed property graph (`OBSERVED_IN`, `DERIVED_FROM`, `SUPPORTS`, `CITES`, `SUBJECT`, `OBJECT`, `OF_RELATION`, `SUPERSEDES`, `SAME_AS`) | graph algorithms: DFS colouring + Tarjan SCC (cycles), reachability, connected components, in-degree | — |

A "hop" is one `derived_from` edge or one `same_as` edge. `graph` and
`text_closure` are **independent implementations**: `relational_verify.py`
builds the graph and never calls the text reasoner. This design isolates two
effects:

- `text_k → text_closure`: the value of **reasoning reach** with information held equal.
- `text_closure → graph`: the value of the **graph representation** with information *and* reach held equal.

## Case suite (480 cases, 240 minimal pairs)

A seeded generator (`seed 20261119`, 8 instances per cell) builds **minimal
pairs**. The valid case and the faulty case share their structure and differ only
in the decisive relational fact. Labels are fixed **by construction** from the
injected fault, and no verifier is consulted. `required_hops` records how many
hops must be followed to decide the case.

| Mechanism / family | Fault (vs valid twin) | Required hops |
|---|---|---|
| provenance / untrusted_root | the supporting summary chain bottoms out in an assistant turn (vs a user turn); half the cases also cite a shallow trusted side branch | 0–4 |
| provenance / cycle | the summary chain loops back on itself (vs ends in a user turn) | 1–4 |
| consistency / alias_write | the new value's subject is *d* `same_as` hops from an entity holding a different value (vs the holder being an unlinked person with a same-length chain) | 0–4 |
| consistency / alias_merge | a new `same_as` merges two entities holding different values *d* hops away (vs no conflicting value) | 0–4 |
| history / stale_target | SUPERSEDE of an already-superseded record (vs the current head) | 1 |
| history / temporal | a summary *d* derivations above an **old** observation revives a superseded value (vs a new observation) | 0–4 |
| history / slot_alias | SUPERSEDE of a record about an alias *d* hops away (valid) vs about an unlinked person (fault) | 0–4 |

Case counts by required hops: 0 → 80, 1 → 112, 2 → 96, 3 → 96, 4 → 96. There
are 240 valid and 240 faulty cases, split 240 dev / 240 held-out by instance index.

## Results

Overall (95% Wilson CIs; n = 240 faulty, 240 valid):

| Strategy | Label agreement | False accepts | False holds |
|---|---:|---:|---:|
| unguarded | 0.500 | 240/240 (1.000) | 0/240 |
| text_k1_optimistic | 0.700 | **144/240 (0.600, [0.537, 0.660])** | 0/240 |
| text_k1_pessimistic | 0.700 | 0/240 | **144/240 (0.600, [0.537, 0.660])** |
| text_k2_optimistic | 0.800 | 96/240 (0.400, [0.340, 0.463]) | 0/240 |
| text_closure | **1.000** | 0/240 ([0, 0.016]) | 0/240 ([0, 0.016]) |
| graph | **1.000** | 0/240 ([0, 0.016]) | 0/240 ([0, 0.016]) |

By required hops: every bounded text verifier is perfect on cases within its
reach. Beyond its reach, it false-accepts **every** faulty case (optimistic) or
false-holds **every** valid case (pessimistic). For example, at hops ≥ 2,
`text_k1_optimistic` false-accepts 48/48 faulty cases per hop level and
`text_k1_pessimistic` false-holds 48/48 valid ones. `text_k2_optimistic` fails
only at hops ≥ 3. The three mechanisms behave the same way: each has 48 false
accepts for k1-optimistic and 32 for k2-optimistic.

Paired comparisons (correctness, exact McNemar):

| Comparison | Left-only correct | Right-only correct | p |
|---|---:|---:|---:|
| graph vs text_k1_optimistic | 144 | 0 | < 1e-40 |
| graph vs text_k1_pessimistic | 144 | 0 | < 1e-40 |
| graph vs text_k2_optimistic | 96 | 0 | < 1e-27 |
| **graph vs text_closure** | **0** | **0** | **1.0** |

- `graph` and `text_closure` produce **identical decisions and identical fail-code sets on 480/480 cases**.
- The graph names the injected fault's code on 240/240 faulty cases.
- Results are the same on the dev and held-out splits.
- Cost: the mean graph has 36.3 nodes and 47.5 edges, against a 1,326-byte text form. Per-case latency is about 0.12–0.14 ms median for every verifier (`timing.json`; machine dependent), so the graph adds no meaningful cost at this scale.

## Conclusion

1. **Relational structure does supply detection power that a bounded,
   information-matched text verifier lacks.** With identical facts, a verifier
   that checks one hop false-accepts 60% of the relational faults. If it is made
   cautious instead, it false-holds 60% of the valid updates. In both cases the
   error is exactly the set of cases needing more hops than it follows. This is
   the decision-level difference the fairness audit could not find on v2, and
   this time it is **not** an information-access artifact.
2. **The advantage comes from reasoning reach, not from the graph
   representation.** A text verifier with the same information *and* unbounded
   traversal matches the graph on every decision and every fail code. For
   deterministic verifiers, "graph beats text" holds only against a text
   verifier that does not follow chains. The graph is a natural substrate for
   closure (cycles, components, reachability), not a necessary one.
3. **Defensible claim:** relational memory updates need multi-hop verification,
   which follows chains to closure. A typed graph is a convenient and cheap way
   to implement it. **Not claimed:** that the graph representation itself detects
   anything a full-reach text verifier would miss.
4. **Where representation could still matter:** in verifiers whose reach is not
   a parameter, such as LLM verifiers. Whether an LLM follows a 3-hop
   `derived_from` chain more reliably as graph JSON or as flat text is an open,
   empirical question. The suite is ready for that run (same cases, `required_hops`
   strata, paired design). It needs paid calls, so it has not been run.

## Limitations

- **Circularity.** The same author wrote the specification, the generator and the
  verifiers. The labels follow from the generator's fault semantics, and those
  semantics match the verifiers' specification by design. The result therefore
  shows that the implementations are correct and that bounded reach fails
  predictably. It does **not** show that the specification matches human
  judgement of memory validity. The cases have not been human-reviewed.
- **Synthetic and templated.** The cases have short token identifiers, a fixed
  relation vocabulary and no natural language. Real extractors produce noisier
  structure: partial aliases, fuzzy names, and evidence without clean derivation
  links.
- **The bounded controls are a modelling choice.** `k` stands in for "a text
  verifier that checks things locally". It is a fair stand-in for verifiers that
  do not explicitly follow chains, such as many prompt-based or single-pass
  checks, but no real verifier was measured here.
- Deterministic rules only. No LLM condition and no paid calls.
