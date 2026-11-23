# RQ3 inventory: every existing trade-off number, before new measurement

Date: 2026-11-24. Step 1 of the RQ3 consolidation (no new runs, $0). Each cell shows the value, its source file, and a label: **M** = measured, **E** = estimated (for example dollars from token counts or char/4), **U** = unavailable. "Local only" marks gitignored run directories under `data/halumem-runs/`, `data/longmemeval-runs/` and `data/memagentbench-runs/`. They hold dataset text, are not committed, and were read only.

Abbreviations: VA = valid-update acceptance; FH = false-hold rate on valid updates; WA = HaluMem weighted accuracy; pts = percentage points. Guard-effect = `docs/guard-effect-report.md`; RQ1 = `docs/rq1-results-section.md`.

## A. Deterministic verifiers (rules, $0)

| Setting / condition | VA, FH | Recall / utility cost | QA delta | Tokens | Latency | Est. cost |
|---|---|---|---|---|---|---|
| evaluation-v2, 36 synthetic cases: graph / text / text_fair | VA 14/14 for all three, FH 0 (M; `data/results/evaluation-v2/metrics.json`, `data/results/fairness-audit-v2/audit.json`) | U | U | 0 (M) | mean end-to-end 0.250 / 0.293 ms, graph build 0.043 ms vs text prep 0.058 ms (M, but called "timer noise", `docs/technical-report-milestone-2.md:109`) | $0 (M: no calls) |
| conversations-v1, 28 steps: graph / text / text_fair | VA 17/17, FH 0/17 for all three (M; `data/results/conversations-v1/metrics.json`) | U | U | 0 | U (per-step latency in `summary.json`, not summarized) | $0 |
| HaluMem Phase 1, injected, transcript grounding: graph = text_fair | VA 310/311 Medium, 1,339/1,340 Long; FH 1 case each (M; `data/halumem-runs/phase1-{medium,long}/report.json`, local only; `docs/halumem-evaluation.md:451`) | U | U | 0 | U | $0 |
| relational-v1, 480 cases: graph, text_closure / text_k1_pess / k1_opt, k2_opt | FH 0/240 / 144/240 / 0/240 (M; `data/results/relational-v1/evaluation.json`) | U | U | 0 | median 0.11 / 0.12 / 0.12-0.13 ms per case (M, no noise floor; `data/results/relational-v1/timing.json`) | $0 |
| qa-utility-v1 simulator, fault rate 0.35: graph = text_closure | FH 0 (M, spec labels; `data/results/qa-utility-v1/summary.json`) | user "stale" answers +7.2 pts [5.5, 8.9] from holding malformed real updates (M, simulated) | overall EM +9.0 pts [7.9, 10.1] vs unguarded; user EM +3.6 [1.4, 5.9]; at fault rate 0.15 user EM +1.0 [-0.3, 2.3] (M, simulated; `docs/qa-utility.md`) | 0 | median 0.254 ms graph, 0.266 ms closure text per proposal, stores of 16-24 records (M, single run; `data/results/qa-utility-v1/timing.json`) | $0 |
| qa-utility-v1: text_k1_pessimistic | FH 0.547 (M, simulated) | EM 0.250 vs 0.852 unguarded (M, simulated) | -60.2 pts (M, simulated) | 0 | median 0.135 ms | $0 |

## B. LLM transition verifier (synthetic held-out, DeepSeek)

| Condition | VA, FH | Recall cost | QA delta | Tokens | Latency | Est. cost |
|---|---|---|---|---|---|---|
| graph LLM, prompt v1 (13 cases) | VA 3/7, FH 4/7 (M; `data/results/llm-heldout-v2/metrics.json`) | U | U | 63,211 (M) | mean 20.4 s per case (M) | run total ≤ $0.40 at "top published rate", ≈$0.20 at $2/M placeholder (E; `docs/llm-heldout.md`) |
| text LLM, prompt v1 | VA 6/7, FH 1/7 (M) | U | U | 28,487 (M) | mean 8.5 s (M) | included above (E) |
| semantic (text + LLM comparator) | VA 7/7, FH 0/7 (M) | U | U | 9,533 (M) | mean 3.0 s (M) | included above (E) |
| graph / text / semantic, prompt v2 | VA 7/7, FH 0/7 each (M; `data/results/llm-heldout-v2-prompt-v2/metrics.json`; held-out split used to pick the prompt) | U | U | text 24,265 new; graph and semantic served from cache, 0 (M) | text mean 7.4 s (M) | $0.0024-$0.049 placeholder range (E) |

## C. HaluMem offline guard on recorded Mem0 proposals (DeepSeek claim judge, DeepSeek official judge)

| Extractor / condition | VA, FH | Recall cost vs unguarded (95% CI) | QA delta | Tokens | Latency | Est. cost |
|---|---|---|---|---|---|---|
| Mem0+DeepSeek, session hold (97 sessions) | 83/97 sessions committed; 210 of 230 held claims were unflagged (collateral) (M); 7/8 and 8/8 of sampled flags not confirmed by reviewers (M, 8 claims) | recall -2.2 [-3.9, -1.0] pts, F1 -1.2 (M) | U (no HaluMem QA offline) | U in docs (in LLM cache, step 3) | U | claim judge: not itemized (E) |
| Mem0+DeepSeek, claim drop | 20 of 1,286 claims dropped (1.6%) (M) | recall -0.11 [-0.42, +0.20] pts (M) | U | U | U | rescoring 94 judge calls, not itemized (E) |
| Mem0+DeepSeek, guard+repair | 14/14 held sessions recovered, 1 retry each (M) | recall -0.1 [-0.4, +0.2] pts (M) | U | U | U | repair $0.05, judge on repaired $0.04 (E, peak price; Guard-effect) |
| Mem0+Llama-3.1-8B, session hold (100) | 69/100 committed (M) | recall -16.2 [-24.9, -8.1] pts (M; RQ1 Table 5) | U | U | U | U |
| Mem0+Llama-3.1-8B, claim drop | 68/1,259 claims dropped (5.4%); reviewers confirm 39-48% of flags (M, 28 flagged claims) | recall -0.26 [-1.25, +0.84] pts; WA +2.26 [1.33, 3.32] (M) | U | U | U | claim judge $0.12 (E) |
| Mem0+Llama-3.1-8B, guard+repair | 31/31 held recovered, 33 attempts (M) | recall -0.57 [-1.06, -0.11] pts; WA +2.04 [0.96, 3.39] (M) | U | U | U | repair $0.10 (E) |
| Injected dose 1.1 / 4.1 / 7.1%, claim drop | judge catches 47/47 fabricated, 31/47 corrupted (M) | recall -0.59 / -1.93 / -2.74 pts; WA +0.71 / +2.67 / +4.50 (M; `data/halumem-runs/dose-response.json`, local only) | U | U | U | injection+judge $0.06 (E) |
| Injected dose 1.1 / 4.1 / 7.1%, guard+repair | 54/54 held sessions recovered, 58 attempts (M) | recall -0.21 / -0.81 / -0.75 pts; WA +0.72 / +2.79 / +4.69 (M) | U | U | U | repair $0.12 (E) |
| Official scoring (evaluation only, all of the above) | - | - | - | U in docs | U | $2.43 (strong), $0.85 (injected), $1.25 + $1.26 (weak) (E, peak) |
| Second judge particle-1 (free), session hold | 23/97 held, 0/6 sampled flags confirmed (M) | 77 recalled gold points lost = 4.1% (M) | U | U | U | $0 (free endpoint) |

## D. Downstream QA on real benchmarks

| Setting / condition | VA, FH | Recall cost | QA delta (vs unguarded) | Tokens | Latency | Est. cost |
|---|---|---|---|---|---|---|
| LongMemEval oracle pilot, guard (session hold) | 5/52 sessions held (M) | U | -6.7 pts (16/30 vs 18/30), McNemar p = 0.5 (M; `data/longmemeval-runs/pilot/results.jsonl`, local only) | U in docs | U | whole pilot $0.15 (E) |
| LongMemEval, guard+repair | 5/5 recovered (M) | U | 0.0 pts, 0 discordant (M) | U | U | included above |
| Live HaluMem, Mem0 + MemBud claim drop (10 users, 50 sessions) | 47/50 sessions unchanged, 4/643 claims dropped (M) | recall -0.3 pts, no CI (M) | QA correct 0.939 vs 0.939; update correct -1.8 pts; untested, noise unmeasured (M; RQ1 Table 9) | U | U | live round "billed about $0.56" from the DeepSeek balance vs about $2.6 peak estimate (user-reported, see contradiction 4) |
| MemoryAgentBench FactConsolidation 6k, LLM conflict guard | 21/23 chunks held, 157 new facts dropped (M) | U | single-hop -74 pts, multi-hop -13 pts (M) | U | U | $0.13 (E) |
| MemoryAgentBench, guard+repair | 10/21 held chunks recovered (M) | U | single-hop -48 pts, multi-hop -4 pts (p = 0.45) (M) | U | U | included above |

## E. Adapters, host integration and latency

| Component | VA, FH | Recall cost | QA delta | Tokens | Latency | Est. cost |
|---|---|---|---|---|---|---|
| StagedMem0 guarded add (offline fakes), store 0 / 100 / 500 | U | U | U | 0 | median HOLD 14.3 / 45.4 / 146.9 ms; ACCEPT 20.5 / 60.6 / 225.6 ms; one run, no IQR, no repeats documented (M; `data/results/mem0-guarded-v1/timing.json`) | $0 |
| BlackBoxStaged (Mem0, LangMem, Graphiti) | real-model runs: Mem0 lexical 0/5 accepted (paraphrase false holds); LangMem speaker-normalized 3/5 (2 false holds); Mem0 speaker-normalized 1/5 (holds mostly correct) (M, 5 synthetic sessions each; `data/results/*-blackbox-realmodel-v1/evidence.json`) | U | U | 11,762 LangMem; 74,258 Graphiti; 13,986 Mem0 first run (M) | U (never timed) | $0 (free models) |
| StagedMem0 default LexicalGrounding on HaluMem | held 97/97 and 100/100 sessions (M; RQ1) | total (nothing committed) | U | 0 | U | $0 |
| opencode `/verify` sidecar hook | U | U | U | 0 | p50 2.0 / p95 3.7 ms (Node), 1.4 / 2.0 ms (bun), tiny fixtures; reviewer re-run p50 3.97 / p95 7.01 ms (M; `integrations/opencode/README.md`, `docs/software-review-2026-11-19.md:272`) | $0 |
| Claim judge call (per session) | - | - | - | U in docs | U in docs (per-call latency recorded in the LLM cache) | U per write |

## Gaps

1. **No per-write or per-session token or dollar figure** for the claim judge, repair or official scoring. Only run totals exist, in dollars, at peak price. Per-call tokens and latency are recorded in the local LLM response cache (`~/.cache/membud/{deepseek,openrouter}-cache.sqlite3`, 11,628 + 307 rows) but were never attributed to runs (step 3).
2. **No billed figure anywhere in the repository.** Every dollar is an estimate. The only "billed" numbers ($0.56 for the live round; "about 1/5 of peak" earlier) are user-reported balance readings without a committed record.
3. **Latency of the deterministic verifier was never separated** into graph construction, verification and adapter snapshot/diff at controlled store sizes. There are no repeats, no IQR and no noise floor, so "timer noise" was asserted, not shown. BlackBoxStaged was never timed. StagedMem0 was timed only up to 500 records, once.
4. **Graph-construction overhead at scale is unmeasured.** The largest timed graph has 36 nodes (relational) or 16-24 records (qa-utility).
5. **QA effect on real dialogue:** only a 30-question LongMemEval pilot (session hold, guard+repair) and the 98-question live HaluMem run (claim drop, natural errors). There is no QA measurement under injected or weak-extractor errors and no CI on the live run.
6. **No cost-per-prevented-error figure** and no explicit break-even error rate.
7. **False-hold rate of the LLM claim judge on valid claims** exists only as small reviewer samples (8 and 28 flagged claims). There is no denominator over all valid claims.
8. **Latency of the LLM guard per write** (judge, repair) is unreported, although it dominates any deterministic cost.

## Contradictions and inconsistencies between documents

1. **Price bases differ, so dollar figures are not comparable.** Guard-effect uses DeepSeek peak list price at cache miss ($0.30 / $1.20 per M input/output, `scripts/score_halumem_official.py`). `docs/llm-heldout.md` uses the `membud.cost` placeholder range ($0.10-$2.00 per M) and also an unsourced "top published rate". `docs/research-status.md` mixes both.
2. **"No break-even in range" vs "pays at about 7%".** The dose-response section of Guard-effect says the weighted-accuracy gain excludes zero already at the 1.1% injected dose ("no break-even rate in this range"). The 47-session injected section ends "works when errors exist at roughly 7% of claims". The second is a statement about one dose, not a threshold. The goal statement's "pays at injected ~7%+" repeats it. Step 4 resolves this with an explicit model.
3. **"Timer noise" vs reported sub-millisecond medians.** `docs/technical-report-milestone-2.md` calls the rules-only latency noise, while `docs/qa-utility.md` ("negligible") and `docs/relational-hypothesis.md` ("adds no meaningful cost") quote single-run medians as costs. None reports a noise floor.
4. **"Billed" wording.** Guard-effect (live run section) and RQ1 (Table 9 text, Sources) call $0.56 "billed, from the DeepSeek balance". `docs/running-experiments.md` and `src/membud/cost.py` state that the provider returns no bill and that only the dashboard is authoritative. The earlier "billed about 1/5 of peak" is marked "user-reported and unverified" in one place (Guard-effect, Cost section) and used as a ratio in RQ1 (Sources). Neither has a committed dashboard record.
5. **Which provider bills what.** Almost all paid calls went to the DeepSeek API directly (11,628 cached completions, model `deepseek-flash`). OpenRouter carried only the Llama-3.1-8B Mem0 extraction (100 calls) and free models. An OpenRouter dashboard total therefore cannot check the DeepSeek estimates. Both dashboards are needed.
6. **Latency of StagedMem0.** The integration doc rounds the medians to "≈15-20 ms at 0 ... ≈150-225 ms at 500 (HOLD / ACCEPT)". That matches `timing.json` (14.3/20.5 ... 146.9/225.6), but the doc says "O(store size)" without a fitted scaling exponent.
7. **"Guard" means different mechanisms in different tables:** the deterministic structural verifier (sections A, E), an LLM transition verifier (B), the DeepSeek claim judge with session hold, claim drop or repair (C, D), and an LLM conflict verifier (MemoryAgentBench). Costs of one cannot be carried over to another.
8. **False-hold denominators differ:** valid cases (A, B), held sessions (C), claims (C), reviewer-sampled flagged claims (C), chunks (MemoryAgentBench). The qa-utility false-hold rate is 0 by construction while the same policy turns 7.2 points of user answers stale. Its own doc notes this.
