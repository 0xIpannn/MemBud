# RQ3 results section (draft for the paper)

> How does verification affect valid-update acceptance, downstream question answering, latency, token usage, and API cost, including graph-construction overhead? (PLAN.md §3; graph superiority is a hypothesis, not an assumption.)

**Short answer.**

- **Valid-update acceptance.** The cost depends on how much a guard throws away.
  - **Deterministic structural verifier** (graph or fair text, which decide identically): on labelled transitions it holds almost no valid updates, at most 1 of 311 per set.
  - **LLM claim judge, dropping only flagged claims:** removes 1.6% of claims on a strong extractor. Reviewers call most of those valid.
  - **Holding a whole session when any claim is flagged:** holds 17.9% of claims on that extractor.
- **Downstream QA.** No guard condition improved QA on real data.
  - Session hold *lowers* LongMemEval accuracy by **6.5 points** (95% CI −10.0 to −3.5, 200 questions, McNemar p = 0.0002).
  - Guard+repair costs **1.0 point** (CI −2.5 to 0.0, p = 0.5).
  - Claim drop left live HaluMem QA unchanged (98 questions).
- **Latency, tokens and API cost.** These are set by the LLM judge, not by the graph.
  - **LLM judge:** one call per write, median 1.2–1.6 s, about 2.8k–9.7k input tokens, about $0.0012–0.0034 per write at list price. That is about 1.3× (LongMemEval) and about 0.3× (Mem0) of the host's own extraction call.
  - **Repair:** adds about 3.8 s and $0.003–0.004 per held write.
  - **Deterministic verification:** milliseconds.
- **Graph-construction overhead.**
  - **Graph build:** linear and cheap, 8 ms at 2,000 prior records.
  - **Standalone graph verifier:** quadratic, 2.2 s at 2,000 records against 4.9 ms for text. The cause is an implementation artifact.
  - **Inside the Mem0 adapters:** graph vs text is **not detectable**. The adapter's state copy dominates and is linear (StagedMem0 1.1 s, BlackBoxStaged 5.5 s at 2,000 records).
- **Spend.** The only billed figure is the owner's DeepSeek export: **$4.21** for 2026-11-21 to 11-24, all project traffic. The repository's peak-price estimate rule over-estimates the same tokens **2.87×**.
- **Trade-off.** On injected faults, guard+repair's net gain (weighted accuracy plus recall) is positive from the lowest dose. Its interval excludes zero from the 4.1% dose. At natural error rates of a strong extractor it is indistinguishable from zero. F1 never improves in the measured range.
- **Do not write:** "verification is cheap", "graph verification costs X", or "the guard improves QA" without these qualifiers.

## Setup

| Item | Value |
|---|---|
| Guards compared | **Unguarded** (commit all); **Session hold** (hold a session if the claim judge flags any claim); **Claim drop** (drop only flagged claims); **Guard + repair** (repair loop, at most 2 retries, re-verification); the deterministic structural verifier as **graph**, **text** and **text_fair** |
| Claim judge | DeepSeek `deepseek-flash`, batch prompt, accepted-assistant-facts policy; one call per write (= per session in every run here) |
| Data | HaluMem-Medium (Mem0 2.2.1 with DeepSeek, 97 sessions; with Llama-3.1-8B, 100 sessions; injected faults, nested doses 1.1 / 4.1 / 7.1% of claims); HaluMem-Long (30 sessions, judge only); LongMemEval oracle (200 questions, 324 sessions); live OSS Mem0 vs Mem0 + MemBud (10 users, 50 sessions, 98 questions) |
| Overhead benchmark | `scripts/bench_rq3_overhead.py`; i5-12450HX, 12 logical CPUs, governor `powersave`, Linux 7.3.4; verifier part Python 3.14.10, adapter part Python 3.12.16 with `mem0ai==2.2.1` (embedded Qdrant, offline LLM and embedder fakes) |
| Timing method | Warm-up (adaptive, at most 20 calls or 0.5 s), calibrated loops of at least 5 ms per sample, 25 interleaved repeats with shuffled order, GC off during a verifier sample; adapters 11 repeats (7 at N ≥ 1,000) after 2 warm-up writes, GC on; medians with IQR. Noise: empty-call floor 0.022 µs; A/A floor (two identical callables) 1.1% at the 95th percentile. A difference is **detectable** only if the IQRs do not overlap and it exceeds the A/A floor |
| Tokens | Provider-reported per completion, read from the local response cache by rebuilding each script's exact cache key (100% of rebuilt keys found in every component) |
| Prices | DeepSeek published list price ($0.30 / $1.20 per M input/output at peak, half off-peak, cache miss); calibrated with the owner's billing export (factor 0.349) |
| Statistics | Paired session bootstrap, 2,000 resamples, seed 20261124, joint across nested doses; Wilson intervals; exact McNemar; paired question bootstrap (4,000) for LongMemEval |

## Table 1. The RQ3 answer in one table

Labels: **[M]** measured (from run artifacts or the provider's billing export), **[E]** estimated (dollars from measured tokens at list price, or a reviewer-precision extrapolation), **[U]** unavailable. "pts" = percentage points. Write = one memory-system `add` (one session here).

| Dimension | Unguarded | Session hold | Claim drop | Guard + repair | Deterministic verifier (graph / text / text_fair) |
|---|---|---|---|---|---|
| **Valid-update acceptance / false holds** | All accepted, 0 false holds [M] | Holds 17.9% of claims (230 of 1,286; Wilson 15.9–20.1%), Mem0+DeepSeek; 31 of 100 sessions on Mem0+Llama [M]. Src: T5 | Removes 1.6% of claims (20 of 1,286) on Mem0+DeepSeek. Reviewers confirm 1/8 and 0/8 of flags, so ≈1.4–1.6% of claims are valid claims removed [M; share E]. Llama: 5.4% removed, ≈2.8–3.3% valid removed [E]. Src: T5 | Recovers 14 of 14 held sessions (strong), 31 of 31 (weak), 23 of 23 (LongMemEval). Repair mostly deletes the flagged claims [M]. Src: T5 | Same decision on every case. Valid accepted 14/14 (v2), 17/17 (conversations), 310/311 and 1,339/1,340 (HaluMem Phase 1) [M]. LLM transition verifier (prompt v1): graph held 4 of 7 valid, text 1 of 7 [M]. Src: inventory §A–B |
| **Extraction quality** (HaluMem official, strong extractor; Src: T5) | baseline | Recall −2.2 pts [−3.9, −1.0]; net −2.0 [−3.7, −0.8] [M] | Recall −0.1 [−0.4, +0.2]; WA +0.2 [−0.0, +0.6] [M] | Same as claim drop on this extractor [M] | U (not run on HaluMem metrics) |
| **Downstream QA** | baseline (LongMemEval 0.650; live HaluMem 0.939) [M] | LongMemEval **−6.5 pts [−10.0, −3.5]**, 13 vs 0 discordant, p = 0.0002 [M]. Src: T6 | Live HaluMem QA 0.939 vs 0.939, 4 vs 4 discordant of 98 [M]. LongMemEval: U (not a pipeline condition). Src: T6 | LongMemEval −1.0 pt [−2.5, 0.0], 2 vs 0, p = 0.5 [M]. Src: T6 | Simulator only: full-reach verifier +9.0 EM pts at fault rate 0.35 (simulated, spec labels) [M]. Real-dialogue QA: U |
| **Latency per write** | Host write only: Mem0 extraction call median 3.2 s (DeepSeek) [M] | + judge call: median 1.23 s, p90 2.10 s (Medium); 1.57 s (Long); 1.30 s (LongMemEval) [M]. Src: T3 | Same judge call as session hold [M] | + judge; held writes add about 3.8 s (repair extractor 2.1 s + re-judge 1.7 s, medians) [M]. Src: T3 | Verify in adapter path ≤ 24 ms at N = 2,000. Adapter state copy: StagedMem0 17 ms → 1.07 s, BlackBoxStaged 27 ms → 5.5 s (N = 0 → 2,000, ACCEPT) [M]. Live end-to-end guard overhead: U (confounded). Src: T2 |
| **Tokens per write** | Host extraction ≈11.0k in / 0.7k out (Mem0+DeepSeek); 3.1k / 0.2k (LongMemEval extractor) [M] | Judge 2,845 in / 316 out (Medium), 2,772 / 300 (Llama run), 9,713 / 384 (Long), 3,813 / 258 (LongMemEval) [M]. Src: T3 | Same as session hold [M] | Judge + 14.4% (strong) or 31% (weak) of writes × ≈9.4k in / 0.8k out of repair [M]. Src: T3 | 0 tokens [M] |
| **API cost per write** | Host extraction $0.0041 at peak list price [E] | $0.00123 per write at peak, ≈$0.00043 calibrated (Medium) [E]; per-condition billed U | $0.00123 per write at peak [E] | $0.00177 per write at peak (strong), $0.00220 (weak) [E] | $0 [M] |
| **API cost per prevented error** | – | U (holding loses more than it removes) | Injected: $0.0109 / $0.0027 / $0.0016 per removed fault at 1.1 / 4.1 / 7.1% (peak) [E]. Natural: $0.0036–0.0045 (weak), $0.048 to unbounded (strong) [E]. Src: T5 | Injected: $0.0172 / $0.0054 / $0.0037 [E]. Src: T5 | – |
| **Billed spend** | All project DeepSeek traffic 2026-11-21 to 11-24: **$4.21** billed, 16,624 requests [M, owner export]. Per condition: U. OpenRouter (Llama-3.1-8B): U. Src: T4 | | | | |
| **Graph-construction overhead** | – | – | – | – | Build: linear (N^1.10), 8.2 ms at N = 2,000, ≈1.1× the text serialization (7.2 ms) [M]. Standalone `verify_graph`: quadratic (N^2.18), 2.2 s vs text 4.9 ms at N = 2,000 [M]. Adapter path builds the graph twice per write; graph vs text total **not detectable** in 18 of 20 cells [M]. Src: T2 |

## Table 2. Overhead benchmark (offline, deterministic inputs)

Source: `data/results/rq3-overhead-v1/verifier.json` (Python 3.14.10) and `adapters.json` (Python 3.12.16, real Mem0 2.2.1, offline fakes). Synthetic schema-v2 transitions: N prior records and k operations cycling WRITE / REVISE / SUPERSEDE, all with evidence, so every verifier returns ACCEPT (HOLD variants at k = 1 give the same times within noise).

**(a) Verifier micro-benchmark, k = 4, µs per call, median [IQR]:**

| Prior records N | Graph build | Graph verify | Graph total | Text serialize | Text verify | Fair text verify | Graph total / text |
|---|---|---|---|---|---|---|---|
| 0 | 21.5 [0.6] | 72.8 [2.0] | 101.5 [2.1] | 30.7 [0.4] | 18.0 [0.2] | 18.6 [0.2] | 5.6× |
| 10 | 52.5 [0.6] | 189 [4] | 250 [5] | 58.3 [0.5] | 39.4 [0.7] | 41.0 [0.5] | 6.3× |
| 100 | 322 [4] | 4,281 [63] | 4,604 [75] | 344 [4] | 225 [4] | 231 [4] | 20× |
| 250 | 825 [11] | 25,107 [1,524] | 25,916 [696] | 823 [17] | 540 [9] | 549 [11] | 48× |
| 500 | 1,772 [77] | 104,678 [4,096] | 108,210 [3,611] | 1,667 [34] | 1,128 [32] | 1,157 [31] | 96× |
| 1,000 | 3,844 [201] | 539,908 [6,781] | 560,008 [8,696] | 3,446 [163] | 2,296 [64] | 2,353 [74] | 244× |
| 2,000 | 8,159 [321] | 2,227,953 [45,387] | 2,344,574 [56,939] | 7,152 [380] | 4,924 [196] | 5,116 [271] | 476× |

- **Scaling (log-log slope over N = 250 to 2,000):**
  - linear: graph build 1.10, text serialize 1.04, text verify 1.06, fair text 1.07, schema validation 1.05;
  - quadratic: graph verify 2.18.
- **Cause of the quadratic term:** the required-claims check in `src/membud/graph_verification.py:104-108` rebuilds the full `(from, type, to)` edge set once per required claim. Rebuilding that set accounts for 70–96% of graph-verify time at N ≥ 100 (`edge_set_rebuild_share_of_graph_verify`). This is an implementation artifact, not a property of graphs: building the set once would make the check linear. It was not changed, because RQ3 measures the evaluated code.
- **Transition size k:** at fixed N, k adds little (N = 500: 108 ms at k = 4, 134 ms at k = 64). At N = 0 the graph path grows faster with k than text (2.9 ms vs 0.18 ms at k = 64).
- **Fair text vs plain text:** +1 to +5%, detectable in only 8 of 35 cells. Mostly **not detectable**.
- **A/A check:** two identical graph callables were never detectable (ratios 0.97–1.01), so the harness does not invent differences.
- **This qualifies the earlier claim that rules-only latency was "timer noise"** (`docs/technical-report-milestone-2.md`). At fixture size, verification takes tens of µs, about 1,000× the 0.022 µs harness floor, so it is measurable but negligible next to any model call. At store sizes beyond a few hundred records, the standalone graph verifier is not negligible.

**(b) Guarded `add` on real Mem0, one fact, median ms [IQR] (ACCEPT; HOLD is 9–34% lower):**

| Live records N | StagedMem0, graph | StagedMem0, text | BlackBoxStaged, graph | BlackBoxStaged, text | Verify share (graph, Staged) |
|---|---|---|---|---|---|
| 0 | 17.1 [1.2] | 16.4 [1.4] | 27.1 [1.0] | 26.5 [0.4] | 0.6% |
| 100 | 58.1 [1.5] | 57.0 [2.5] | 220 [7] | 222 [10] | 2.6% |
| 500 | 226 [5] | 224 [7] | 1,074 [21] | 1,065 [39] | 3.4% |
| 1,000 | 440 [1] | 441 [7] | 2,328 [20] | 2,369 [49] | 3.6% |
| 2,000 | 1,069 [29] | 1,060 [6] | 5,487 [75] | 5,505 [71] | (GC-affected, see below) |

- **Graph vs text within an adapter:** not detectable in 18 of 20 (adapter, N, decision) cells. The two flagged cells point in opposite directions.
- **Why the verifier stays linear here:** the Mem0 bridge passes no `required_claims`, so the quadratic term never runs on this path. Verify takes 7.4 ms at N = 500 and 15.6 ms at N = 1,000.
- **N = 2,000 anomaly:** graph verify jumps to 174–189 ms in 3 of 4 graph cells and stays at 35 ms in the fourth. Text stays at 24 ms. The garbage collector runs during writes, so this is most likely GC pauses landing in that phase. Totals are unaffected (graph vs text not detectable).
- **Adapter mechanism scaling:** StagedMem0 ≈ N^1.12 (snapshot, shadow clone, diff). BlackBoxStaged ≈ N^1.18 and is about 5× slower, because it seeds a fresh twin with every record through the public API and observes it (twin creation, seeding and observation are about 95% of its time at N ≥ 500).
- **Transition size:** k = 16 facts adds 17% (Staged) and 7% (BlackBox) at N = 500.
- **Reproduction:** these StagedMem0 numbers reproduce the earlier `data/results/mem0-guarded-v1/timing.json` within a few percent (HOLD 14.5 / 43.6 / 148.8 ms vs 14.3 / 45.4 / 146.9 ms at N = 0 / 100 / 500).
- **Graph built twice per adapter write:** the adapter path builds the graph twice per write (`calls_per_write.graph_build = 2`). `membud.store.run_case` builds it, and `verify()` rebuilds it to check the prepared input.

## Table 3. Tokens, latency and cost per write: deployed vs evaluation-only

Source: `data/results/rq3-cost-v1/token-attribution.json`. Means per call over all matched calls. Dollars use the peak cache-miss list price [E]. Multiply by 0.349 for the billing-calibrated value (Table 4). Latency is the recorded wall-clock of the first (uncached) call.

| Component | Role | Calls | Input / output tokens per call [M] | Median latency (p90) [M] | $ per call at peak [E] |
|---|---|---|---|---|---|
| Mem0 extraction, DeepSeek (shape-attributed, mixed runs) | host, reference | 178 | 11,013 / 696 | 3.16 s (5.03) | 0.00414 |
| Claim judge, Mem0+DeepSeek Medium | **deployed** (every guard) | 97 | 2,845 / 316 | 1.23 s (2.10) | 0.00123 |
| Claim judge, Mem0+Llama Medium | deployed | 100 | 2,772 / 300 | 1.57 s (2.73) | 0.00119 |
| Claim judge, Mem0+DeepSeek Long | deployed | 27 | 9,713 / 384 | 1.57 s (1.85) | 0.00337 |
| Claim judge, LongMemEval (incl. re-verification) | deployed | 343 (324 sessions) | 3,602 / 244 | 1.30 s | 0.00137 |
| LongMemEval extractor | host, reference | 324 | 3,110 / 177 | 1.30 s | 0.00115 |
| Repair extractor + re-judge, per held session (Medium) | **deployed** (repair only) | 14 + 14 | 9,386 / 772 (both calls) | 2.08 s + 1.70 s | 0.00374 per held session |
| Repair, per held session (Llama run) | deployed | 33 + 33 | 7,778 / 616 | 1.94 s + 1.74 s | 0.00327 |
| HaluMem official scoring, per session (Medium) | **evaluation only** | 3,219 (33.2 per session) | 2,160 / 90 per call | 1.24 s | 0.0251 per session |
| Official re-scoring of repaired / claim-dropped output | evaluation only | 304 / 94 | 3,692 / 128; 976 / 105 | 1.61 s; 1.13 s | 0.38 / 0.04 per run |
| LongMemEval reader / benchmark judge | evaluation (QA) | 234 / 227 | 397 / 28; 210 / 6 | 0.71 s; 0.57 s | 0.036 / 0.016 per run |

Per write, deployed, Mem0+DeepSeek Medium:

| Condition | Cost per write at peak [E] | Per 97-session run at peak [E] | Calibrated per write [E] |
|---|---|---|---|
| Session hold | $0.00123 | $0.120 | ≈$0.00043 |
| Claim drop | $0.00123 | $0.120 | ≈$0.00043 |
| Guard + repair | $0.00177 | $0.172 | ≈$0.00062 |
| Evaluation only (official scoring incl. re-scoring) | $0.029 per session | $2.86 | ≈$0.010 |

Evaluation-only scoring is **94%** of the spend of a HaluMem condition comparison. The deployed guard adds about 30% to the host's own extraction cost on Mem0+DeepSeek, and about 127% on the lighter LongMemEval extractor.

## Table 4. Billing reconciliation (owner-supplied DeepSeek export)

Source: `data/results/rq3-cost-v1/billing-reconciliation.json` (`scripts/rq3_billing_reconcile.py`; aggregates only, no account identifiers). Model `deepseek-flash`, API key "MemBud-Experiment", no usage before 2026-11-21 in the export.

| Day | Requests | Input cache-hit tokens | Input cache-miss tokens | Output tokens | Billed USD [M] |
|---|---|---|---|---|---|
| 2026-11-21 | 97 | 733,568 | 221,032 | 59,473 | 0.071 |
| 2026-11-22 | 4,077 | 4,489,984 | 4,477,576 | 489,840 | 0.979 |
| 2026-11-23 | 8,484 | 7,920,804 | 7,242,657 | 886,126 | 2.409 |
| 2026-11-24 (export taken mid-day, includes part of step 5) | 3,966 | 4,517,662 | 3,198,323 | 430,216 | 0.751 |
| **Total** | **16,624** | **17,662,018** | **15,139,588** | **1,865,655** | **4.210** |

- **Price-basis error:** the repository's estimate rule (all input at the peak cache-miss price) applied to these *billed* tokens gives $12.08, **2.87× the bill (+187%)**. The off-peak rule gives $6.04 (1.43×).
  - **Why:** 54% of input tokens were context-cache hits, billed at $0.003–0.006 per M instead of $0.15–0.30, and most traffic ran at off-peak rates.
  - **Calibration factor:** billed / peak rule = **0.349**, valid only for this workload mix.
- **Coverage of the local cache:** 70% of billed input tokens, 74% of output tokens and 70% of requests. The gap is failed calls and calls made outside `membud.llm`, such as the live-run official `evaluation.py` judge and answer calls. The cache-based peak estimate ($8.56) is therefore 2.03× the bill.
- **Earlier "about 1/5 of peak" claim:** over this window, billing ran at about 1/2.9 of the peak rule.
- **Earlier $0.56 for the live round:** cannot be checked against this export. It has daily granularity, and 2026-11-23 also contains the weak-extractor rescoring.
- **Per-condition billed cost:** unavailable. The bill has no per-call breakdown.
- **OpenRouter:** no export was supplied, so the Llama-3.1-8B extraction (100 calls, 976k input / 176k output tokens) has no billed figure.

## Table 5. Trade-off model: benefit, collateral and cost per prevented error

Source: `data/results/rq3-tradeoff-v1/tradeoff.json` (`scripts/rq3_tradeoff.py`). HaluMem official metrics, 97 sessions, DeepSeek official judge. Injected doses are nested over the same sessions. Paired differences vs unguarded in points, 95% bootstrap CI. **net = ΔWA + Δrecall** (benefit minus recall cost, equal weight). Injected labels are circular.

| Injected claim rate | Condition | ΔWA | ΔRecall | ΔF1 | **Net** | Faults removed | Valid claims removed (rate) | $ per removed fault, peak [E] |
|---|---|---|---|---|---|---|---|---|
| 0 (natural, 1.6% flagged) | Claim drop = repair | +0.19 [−0.03, 0.57] | −0.11 [−0.41, 0.18] | −0.07 [−0.23, 0.09] | +0.08 [−0.34, 0.63] | – | 20 of 1,286 (1.56%) | see natural rows |
| | Session hold | +0.18 [−0.32, 0.78] | −2.20 [−3.94, −0.99] | −1.18 [−2.15, −0.52] | **−2.02 [−3.66, −0.81]** | – | 230 of 1,286 (17.9%) | – |
| 1.1% | Claim drop | +0.71 [0.26, 1.27] | −0.59 [−1.17, −0.14] | −0.32 [−0.63, −0.08] | +0.12 [−0.33, 0.53] | 11 of 14 | 18 of 1,279 (1.41%) | 0.0109 |
| | Guard + repair | +0.72 [0.26, 1.29] | −0.21 [−0.52, 0.06] | −0.12 [−0.29, 0.03] | +0.50 [−0.01, 1.15] | 11 of 14 | – | 0.0172 |
| 4.1% | Claim drop | +2.67 [1.82, 3.58] | −1.93 [−3.17, −1.03] | −1.02 [−1.70, −0.55] | +0.73 [−0.25, 1.58] | 45 of 54 | 17 of 1,259 (1.35%) | 0.0027 |
| | Guard + repair | +2.79 [1.90, 3.74] | −0.81 [−1.67, −0.19] | −0.42 [−0.87, −0.10] | **+1.98 [1.00, 3.03]** | 45 of 54 | – | 0.0054 |
| 7.1% | Claim drop | +4.50 [3.52, 5.48] | −2.74 [−4.12, −1.62] | −1.45 [−2.20, −0.86] | **+1.76 [0.55, 2.80]** | 78 of 94 | 15 of 1,239 (1.21%) | 0.0016 |
| | Guard + repair | +4.69 [3.67, 5.72] | −0.75 [−1.67, −0.09] | −0.41 [−0.89, −0.06] | **+3.94 [2.68, 5.11]** | 78 of 94 | – | 0.0037 |
| | Session hold | +5.32 [3.74, 6.98] | −44.8 [−58.6, −31.2] | −30.3 [−44.0, −19.1] | −39.5 [−53.4, −25.7] | 94 of 94 | 57.7% of valid claims | – |

**Linear fit over the measured doses (0 to 7.1%), points per point of injected rate (joint bootstrap):**
- **Weighted accuracy:** claim drop +0.62 [0.47, 0.77], guard+repair +0.65 [0.50, 0.81].
- **Net:** claim drop +0.24 [0.05, 0.39], guard+repair +0.55 [0.37, 0.72].
- **F1:** claim drop −0.20 [−0.30, −0.11], guard+repair −0.05 [−0.13, −0.003].

**Where benefit exceeds cost:**
- **Break-even of the net fit:** at or below the bottom of the measured range. Claim drop: 0.27% (CI −3.0 to 2.6%). Guard+repair: 0.08% (CI −1.0 to 0.9%).
- **Point estimates and intervals:** net is positive at every dose. Its 95% interval first excludes zero at **4.1% for guard+repair** and at **7.1% for claim drop**.
- **F1:** the official balance of precision and recall never benefits in the range. Target precision does not move, because injected fabrications are not gold memories, so recall loss dominates F1.
- **Session hold** is net-negative everywhere.
- **Above 7.1%:** no dose was measured (the 14.5% dose was not scored). Any statement there, including that the gain keeps rising linearly, is an **assumption**.

**Natural errors.** Prevented errors are estimated as flagged claims × reviewer-confirmed flag precision (Wilson), and are therefore **estimates**.

| Extractor | Flagged | Flag precision (ifan; nabhan) | Est. true errors removed | Valid claims removed | $ per prevented error, judge only, peak [E] |
|---|---|---|---|---|---|
| Mem0 + DeepSeek (97 sessions) | 20 | 1/8; 0/8 | 2.5 (0.4–9.4); 0 (0–6.5) | 17.5–20 (≈1.4–1.6% of claims) | $0.048 (best case $0.013); unbounded if 0/8 holds |
| Mem0 + Llama-3.1-8B (100 sessions) | 68 | 13/27; 9/23 | 32.7 (20.9–44.9); 26.6 (15.1–40.3) | 35–41 (≈2.8–3.3% of claims) | $0.0036–0.0045 (range $0.0027–0.0079); guard+repair $0.0067–0.0083 |

**Natural vs injected:** the Llama run's flagged rate (5.4%) maps onto the injected slope as an **assumption**, because natural flags are not injected faults. That mapping predicts about +3.4 weighted-accuracy points, while the measured gain is +2.3 [1.3, 3.3] for claim drop (`docs/rq1-results-section.md`, Table 5). Natural flags therefore buy about two-thirds of what injected faults do, consistent with reviewers confirming only 39–48% of them.

## Table 6. Downstream QA

| Benchmark | Questions | Unguarded | Session hold | Claim drop | Guard + repair | Source |
|---|---|---|---|---|---|---|
| LongMemEval oracle, our pipeline (seed 20261122, round-robin by type) | 200 | 0.650 | 0.585: **−6.5 pts [−10.0, −3.5]**, 13 vs 0, p = 0.0002 | not a condition [U] | 0.640: −1.0 [−2.5, 0.0], 2 vs 0, p = 0.5 | `data/results/rq3-tradeoff-v1/longmemeval-n200-paired.json` |
| Same, 170 questions excluding the 30-question pilot | 170 | – | −6.5 [−10.6, −2.9], 11 vs 0, p = 0.001 | – | −1.2 [−2.9, 0.0], 2 vs 0, p = 0.5 | same |
| Live HaluMem, OSS Mem0 vs Mem0 + MemBud (10 users) | 98 | 0.939 | not run | 0.939: 4 vs 4 discordant (noise; the guard acted in 3 of 50 sessions) | not run | `docs/rq1-results-section.md` Table 9; counts from the local run directory |
| MemoryAgentBench FactConsolidation 6k (LLM conflict guard, not the claim judge) | 100 + 100 | 1.00 / 0.13 | 0.26 / 0.00 | – | 0.52 / 0.09 | `docs/guard-effect-report.md` |

- **Sample and run:** in the 200-question LongMemEval run, the judge flagged 83 of 3,286 claims (2.5%) and held 23 of 324 sessions (7.1%). Repair recovered all 23.
- **Session hold:** lost answers in all six question types (−1 to −3 questions each) and gained none. Holding discards whole sessions whose claims the reader needs.
- **Guard + repair:** lost 2 single-session-assistant questions.
- **Power:** at n = 200 and the observed discordance, the run could detect about 5 points (Table 7). The guard+repair interval rules out a QA gain larger than 0 points and a loss larger than 2.5 points.

## Table 7. Power for a larger QA comparison (step 5 sizing)

McNemar test, α = 0.05, 80% power. Discordance comes from the pilot runs.

| Discordance | n for 2 / 3 / 5 pts | Detectable at n = 200 / 500 |
|---|---|---|
| 8.2% (live HaluMem, pure vs claim drop; mostly run-to-run noise) | 1,600 / 710 / 254 | 5.6 / 3.6 pts |
| 6.7% (LongMemEval pilot, session hold) | 1,306 / 580 / 207 | 5.1 / 3.2 pts |
| 3% (assumed for guard+repair) | 587 / 260 / 92 | 3.4 / 2.2 pts |

The owner approved a 200-question run with a $1.50 cap. Estimated spend was $0.77 at peak list price, about $0.27 after calibration. LongMemEval oracle has 500 questions, so a 2-point effect on QA cannot be resolved on this benchmark alone.

## Claims the data supports

- On real Mem0 with offline fakes, the per-write latency of **deterministic** verification is small next to the adapter's state copy, and graph vs text is not detectable end to end (N = 0 to 2,000).
- Graph construction is linear and costs about as much as the text serialization (8.2 vs 7.2 ms at 2,000 records). The standalone graph verifier is quadratic because of a specific, fixable implementation detail. Fair text costs at most about 5% more than plain text.
- The adapter mechanism, not verification, sets deterministic overhead. StagedMem0 is linear, at about 1.1 s per write at 2,000 records. BlackBoxStaged is about 5× slower (5.5 s), because it re-seeds a twin through the public API.
- The LLM claim judge sets the guard's real cost: one call per write, median 1.2–1.6 s, about 2.8k–9.7k input tokens, about $0.0012–0.0034 per write at list price. Repair adds about 3.8 s and about $0.003–0.004 per held write. Evaluation-only scoring costs about 20× the deployed guard per session.
- The repository's peak cache-miss estimate rule over-estimates DeepSeek billing 2.87× on the same tokens. Billed spend for the window was $4.21.
- Holding a whole session costs QA accuracy (−6.5 points on LongMemEval, replicated on 170 fresh questions) and recall (−2.2 to −16 points on HaluMem).
- Guard+repair and claim drop do not measurably change QA. On injected faults their net extraction benefit grows with the error rate. For guard+repair it is detectable from the 4.1% dose.

## Claims the data does not support

- **Any graph advantage:** graph is never cheaper and never decides differently from fair text (RQ1). It is slower standalone, and not detectably different inside the adapters.
- **"The guard improves downstream QA":** no condition improved QA in any real-data run.
- **"Verification is free":** only deterministic verification at small store sizes is close to free. The deployed LLM judge adds about 30–130% to the host's model cost per write.
- **Any billed per-condition or per-write cost:** only the aggregate DeepSeek bill exists. Per-write dollars are estimates, calibrated or at list price.
- **A general benefit, or a break-even above 7.1%:** the injected faults are synthetic and circular, and no dose above 7.1% was measured.
- **The live-run latency difference** (median 4.78 s pure vs 4.98 s with MemBud per session) as guard overhead: the MemBud arm's Mem0 extraction calls were largely served from the response cache, so the comparison does not isolate the guard.

## Caveats (read before citing any number)

1. **Unvalidated, same-family judges.**
   - The claim judge (what to remove) and the HaluMem official accuracy judge (what to score) are both DeepSeek. The benchmark's GPT-4o was not used.
   - The LongMemEval answer judge is also DeepSeek.
2. **Small reviewer samples.**
   - Flag precision rests on 8 flagged claims for the strong extractor (1/8, 0/8) and 28 for the weak one (13/27, 9/23).
   - The deterministic evaluation sets are single-reviewer.
   - Prevented-error and false-hold estimates inherit these wide intervals.
3. **Synthetic injections.** Dose-response labels are circular, so they show mechanism sensitivity, not natural prevalence.
4. **One extractor family.** Every HaluMem number is Mem0 2.2.1 (DeepSeek or Llama-3.1-8B inside it). LongMemEval uses our own DeepSeek extractor.
5. **Timing is from one machine** (i5-12450HX, `powersave` governor) and uses offline fakes for Mem0's model calls. Absolute milliseconds are machine-dependent; the scaling exponents and ratios are the transferable part.
6. **Token attribution limits.** Tokens come from the local response cache, a lower bound of metered usage (70% of billed input). Mem0's own extraction calls were attributed by response shape, not by key, and mix runs.
7. **Simulated QA.** The QA simulator (`docs/qa-utility.md`) uses spec-derived labels. Its +9 EM points are not evidence about real dialogue.

## Not done

- **OpenRouter billing:** not supplied, so the Llama-3.1-8B extraction cost is not billed-verified.
- **Per-condition billed cost:** needs per-call billing, which the provider does not offer.
- **Claim-drop condition on LongMemEval;** LongMemEval S/M with distractor sessions; QA under injected or weak-extractor errors.
- **A dose above 7.1%** (14.5% not scored).
- **A fix of the quadratic graph check;** timing with the GC disabled in the adapters, which would explain the N = 2,000 verify anomaly.
- **Live end-to-end guard latency with the response cache disabled,** and repeated live runs to measure run-to-run QA noise directly.
- **LLM transition-verifier latency at store scale** (only the 13-case held-out run exists).
- **Validation of the judges** by an independent model family or more reviewer labels.

## Sources and reproducibility

| Purpose | Script | Output |
|---|---|---|
| Inventory of prior numbers, gaps, contradictions | – | `docs/rq3-inventory.md` |
| Overhead benchmark | `scripts/bench_rq3_overhead.py --part verifier` / `--part adapters` | `data/results/rq3-overhead-v1/{verifier,adapters}.json` |
| Token attribution | `scripts/rq3_token_attribution.py` | `data/results/rq3-cost-v1/token-attribution.json` |
| Billing reconciliation | `scripts/rq3_billing_reconcile.py` (owner's DeepSeek export) | `data/results/rq3-cost-v1/billing-reconciliation.json` |
| Trade-off model | `scripts/rq3_tradeoff.py` | `data/results/rq3-tradeoff-v1/tradeoff.json` |
| LongMemEval QA (200 questions) | `scripts/run_longmemeval.py --run --n 200` | run data in gitignored `data/longmemeval-runs/n200/`; paired analysis `data/results/rq3-tradeoff-v1/longmemeval-n200-paired.json` |

- **Environments:**
  - verifier benchmark, analyses and tests: `.venv` (Python 3.14.10);
  - adapter benchmark: `.venv-mem0` (Python 3.12.16, `requirements-mem0.txt`).
- **Seeds:** benchmark and bootstrap 20261124; LongMemEval sample 20261122.
- **Read only, never committed:** run directories with dataset text (`data/halumem-runs/`, `data/halumem-live/`, `data/longmemeval-runs/`), the HaluMem and LongMemEval datasets, and the LLM response cache.
