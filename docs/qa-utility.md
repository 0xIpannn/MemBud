# Downstream conversational QA utility: guarded vs unguarded memory

**Date:** 2026-11-19
**Artifacts:** `data/results/qa-utility-v1/summary.json` (byte-stable), `timing.json`
**Reproduce:** `PYTHONPATH=src .venv/bin/python scripts/run_qa_utility.py --output data/results/qa-utility-v1` (≈40 s, rules-only, 0 paid API calls)
**Code:** `src/membud/qa_benchmark.py`, `src/membud/paired_stats.py`; **tests:** `tests/test_qa_benchmark.py`

## Question

Gate decisions are only worth what they do to the memory an assistant later
answers from. This benchmark asks how much question-answering utility each
gating policy keeps or loses. It covers the cases where a relational verifier
holds an update, the cases where it lets one through, and its relational reach.
It addresses PLAN RQ3 and Definition of Done #8.

## Design

All runs are seeded, deterministic simulations with no model calls. See the
module docstring for full detail.

- **World and conversation.** Each conversation has a user and a friend, each
  with a nickname chain (`same_as`, 1–3 links), three functional attributes
  (`lives_in`, `works_at`, `favorite_food`) and the user's `likes` set. Session 0
  introduces everyone. Sessions 1–5 each carry 3 events. A non-faulty event is a
  genuine change stated in a user turn, sometimes under a nickname.
- **Noisy extractor.** In 60% of proposals, a summary chain of 1–3 derivations
  sits between the turn and the cited evidence. The extractor reads the store it
  is updating to choose SUPERSEDE targets. At the configured fault rate it injects
  one of five faults instead of a genuine update:

  | Fault | Corrupts | Spec check |
  |---|---|---|
  | hallucination | assistant speculation, possibly laundered through summaries, written as a user fact | provenance |
  | misattribution | the friend's attribute written under the user's nickname, without a supersede | consistency |
  | stale_revival | a summary of the earliest session re-asserts the old value as an update | history (temporal) |
  | wrong_target | a **real** user change applied as a SUPERSEDE of the *friend's* record | history (slot) |
  | stale_target | a **real** user change superseding an already-superseded record | history (stale) |

  The last two carry real information inside a malformed transition, so a gate
  that holds them protects the store's structure but loses the update. These two
  are the false-rejection side of the trade-off.
- **Policies.** `unguarded` commits everything. `oracle_gate` commits exactly the
  generator-labelled valid proposals: a perfect gate, but not a perfect memory.
  The other policies are the relational verifiers from
  [relational-hypothesis.md](relational-hypothesis.md).
- **QA.** After every session, the benchmark asks for the user's three functional
  attributes and `likes`, and the friend's three attributes. One fixed reader
  answers for every policy. It resolves nicknames via `same_as`, reads current
  records, and when a slot holds several values it picks the most recently
  committed one.
- **Unit and statistics.** The unit is the conversation: 200 per fault rate,
  paired across policies on identical proposals and questions. Comparisons use a
  paired percentile bootstrap, 4,000 resamples, 95% CI.

## Results at the primary fault rate (0.35; 200 conversations, 5,003 proposals)

| Policy | Overall EM | User EM | Friend EM | Likes F1 | User stale | User wrong | User missing | False accept | False hold |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| unguarded | 0.852 | 0.848 | 0.856 | 0.927 | 0.044 | 0.108 | 0 | 1.000 | 0 |
| text_k1_optimistic | 0.897 | 0.824 | 0.971 | 0.980 | 0.128 | 0.049 | 0 | 0.292 | 0.013 |
| text_k1_pessimistic | 0.250 | 0.366 | 0.135 | 0.681 | 0.239 | 0.008 | 0.387 | 0.027 | 0.547 |
| text_k2_optimistic | 0.921 | 0.852 | 0.990 | 0.993 | 0.126 | 0.022 | 0 | 0.138 | 0.006 |
| text_closure | **0.942** | 0.884 | **1.000** | **1.000** | 0.116 | **0** | 0 | **0** | **0** |
| graph | **0.942** | 0.884 | **1.000** | **1.000** | 0.116 | **0** | 0 | **0** | **0** |
| oracle_gate | 0.942 | 0.884 | 1.000 | 1.000 | 0.116 | 0 | 0 | 0 | 0 |

Paired differences (mean, 95% CI):

| Comparison | Overall EM | User EM | User wrong | User stale | Friend EM |
|---|---|---|---|---|---|
| graph − unguarded | **+0.090 [+0.079, +0.101]** | +0.036 [+0.014, +0.059] | −0.108 [−0.123, −0.093] | **+0.072 [+0.055, +0.089]** | +0.144 [+0.123, +0.167] |
| graph − text_k1_optimistic | +0.045 [+0.037, +0.054] | +0.061 [+0.048, +0.075] | −0.049 [−0.060, −0.038] | −0.012 [−0.022, −0.003] | +0.029 [+0.019, +0.041] |
| graph − text_k2_optimistic | +0.021 [+0.016, +0.027] | | | | |
| graph − text_k1_pessimistic | +0.692 [+0.664, +0.718] | | | | |
| graph − text_closure | 0.000 [0, 0] | | | | |
| oracle_gate − graph | 0.000 [0, 0] | | | | |

Fault-rate sweep, overall EM (graph − unguarded):

| Fault rate | unguarded | k1-opt | k2-opt | closure = graph | graph − unguarded | graph − unguarded, **user EM only** |
|---|---:|---:|---:|---:|---|---|
| 0.15 | 0.934 | 0.953 | 0.968 | 0.975 | +0.041 [+0.033, +0.050] | +0.010 **[−0.003, +0.023]** |
| 0.35 | 0.852 | 0.897 | 0.921 | 0.942 | +0.090 [+0.079, +0.101] | +0.036 [+0.014, +0.059] |
| 0.55 | 0.768 | 0.838 | 0.876 | 0.901 | +0.133 [+0.121, +0.145] | +0.033 [+0.006, +0.059] |

## Findings

1. **Full-reach guarding improves downstream utility, and the gain grows with
   extractor noise.** Overall exact match rises by 4.1, 9.0 and 13.3 points at
   fault rates 0.15, 0.35 and 0.55. Every CI excludes zero. Wrong user answers
   go to zero, the friend's memory is never overwritten (friend EM is 1.000
   against 0.856 unguarded), and the `likes` set stays clean.
2. **Guarding changes the error type rather than removing every error.** When a
   gate holds a malformed but genuine update (`wrong_target`, `stale_target`),
   the user's answer goes **stale** instead of wrong: user stale +7.2 points at
   0.35. On the user's own questions the net gain is small. At fault rate 0.15
   its CI includes zero (+1.0 [−0.3, +2.3]), and the final-session user EM at
   0.55 also includes zero. A gate that only holds, with no repair or
   re-extraction, gives up recall. That cost is not visible in false-hold rates,
   because the transitions really are invalid.
3. **Reach matters downstream exactly as it did on isolated transitions.** The
   1-hop optimistic verifier lets 29% of faults through. It loses 4.5 overall EM
   points to graph/closure and still answers 4.9% of user questions wrongly. The
   1-hop pessimistic verifier is catastrophic. It holds 55% of valid updates:
   every update whose evidence sits behind a summary chain two or more
   derivations deep (provenance), or whose subject is two or more alias hops
   from what the consistency check must reach. That includes many of the
   friend's introductory facts, so friend EM falls to 0.135. Overall EM falls
   to 0.25, far *below* unguarded. Insufficient
   reach is worse than no gate when it fails closed, and only partly better when
   it fails open.
4. **Graph = closure text = oracle gate.** On every conversation, metric and
   fault rate, the graph and closure-text policies commit identical proposals
   and so reach identical utility. Both match the oracle gate because the labels
   come from the same specification (see Limitations). The downstream result
   repeats the relational-hypothesis conclusion: the utility comes from
   **full-reach relational verification**, and the graph is one implementation
   of it.
5. **Cost.** Per-proposal verification on stores of about 16–24 records (plus provenance) has a
   median of about 0.25 ms for both graph and closure text (`timing.json`). That
   is negligible next to any extraction model call.

## Limitations

- **Simulated conversations, not real dialogue.** Facts arrive as clean triples.
  The extractor's faults are injected at chosen rates from five templates rather
  than learned from a real extractor's error distribution. Absolute EM values
  describe this simulator. The *direction* and the *relative ordering* of
  policies are the transferable part, and they would still need confirming on
  real data (e.g. LoCoMo-style multi-session QA with a model extractor).
- **Label/specification circularity.** `oracle_gate` uses the generator's labels.
  Those labels encode the same validity specification the verifiers implement,
  so graph = oracle is expected, not discovered. Where a fault cannot
  materialize against a given store, the proposal is relabelled valid by fixed
  store conditions, never by a verifier. This happens in 1–4% of proposals, with
  counts in `commits.fallbacks`.
- **The reader is fixed and simple** (most recent committed value). A reader
  that surfaces conflicts or abstains would score unguarded stores differently.
- **No repair path.** Held updates are dropped. A production gate would route a
  HOLD to clarification or re-extraction, which would recover most of the stale
  answers in finding 2. That is not measured here.
- Deterministic verifiers only. There is no LLM verifier condition and no LLM
  reader.
