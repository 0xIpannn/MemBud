# MemBud: experiment results (fact-checked, for the paper)

Compiled 2026-11-24 from repository commit `5fcf4bd` (main). Part A is English, Part B is the Bahasa Indonesia version of the same content. The numbers are identical in both parts.

Source drafts: `docs/rq1-results-section.md`, `docs/rq2-modularity-results.md`, `docs/rq3-results-section.md`, `docs/rq3-inventory.md`, `docs/guard-effect-report.md`.

---

# Part A: English

## A0. How the numbers were checked

Every number below was **recomputed from the run artifacts**, not copied from the drafts. For each table, the source artifact is named in the table caption.

- **Recomputed from raw files:**
  - official HaluMem metrics: `data/halumem-runs/*/official/official-metrics.json`, `judge-results.jsonl`;
  - claim-judge verdicts: `llm-claim-verdicts-*.jsonl`;
  - reviewer answer files and quiz keys;
  - Phase 1 checkpoints, with per-case graph vs `text_fair` decisions;
  - repair logs;
  - live-run `memzero_eval_stat_result.json` and `guard-log.jsonl`;
  - LongMemEval and MemoryAgentBench reports;
  - adapter contract evidence;
  - real-model evidence, including earlier versions in git history;
  - RQ3 `verifier.json`, `adapters.json`, `token-attribution.json`, `billing-reconciliation.json`, `tradeoff.json`, `longmemeval-n200-paired.json`.
- **Re-executed:** `scripts/halumem_dose_response.py`, offline from cached scores with no API calls. With 1,000 resamples it reproduces the committed `dose-response.json` exactly. With 2,000 resamples the confidence intervals differ in the 4th decimal.
- **Not re-executed:** any paid model call (no new spend), the test suites, the Neo4j/Graphiti tests.

Labels used in tables:

- **[M]** measured from artifacts.
- **[E]** estimated, for example dollars from measured tokens at list price.
- **[U]** unavailable.
- **pts** means percentage points. **WA** means HaluMem weighted accuracy. **CI** means 95% confidence interval.

## A1. Fact-check findings

### A1.1 Errors corrected in this file

| # | Draft location | Draft says | Artifacts say | Action here |
|---|---|---|---|---|
| 1 | RQ1 Table 6, note on claim judge vs official judge | Official judge scored 72% of weak-extractor flags below 2, "against 21% of unflagged claims" | The 21% is 83/395, counting only unflagged claims inside the 31 sessions that had a flag. The strong-extractor comparison (5.4%) counts all sessions. Over all 100 sessions, the weak-extractor unflagged rate is **180/1,196 = 15.1%**. The draft compared different denominators. | Corrected to 72% vs 15.1% (all sessions) |
| 2 | RQ1 Table 9 text and Sources | Live round "billed about $0.56"; "billing ran at about a fifth of the estimate" | No committed record of $0.56. The billing export has daily granularity, and that day also contains other runs. Over the export window, billing was **1/2.87** of the peak-price rule, not 1/5. | Both removed; only the billed total $4.21 is used |
| 3 | RQ3 Table 1 | Deterministic verify in the adapter path "≤ 24 ms at N = 2,000" | True for text only (≈24 ms). At N = 2,000, graph verification took **174–189 ms in 3 of 4 graph cells**, attributed to garbage-collector pauses, and 35 ms in the fourth. | Corrected |
| 4 | RQ3 Table 2a | Fair text vs plain text "detectable in only 8 of 35 cells" | **10 of 35** cells are flagged detectable; ratios 0.99–1.05 | Corrected |
| 5 | RQ3 Table 2a | Edge-set rebuild is "70–96%" of graph-verify time at N ≥ 100 | The per-cell estimate ranges 0.70–1.44; one cell exceeds 100%, so it is only an estimate. At k = 4 the range is 0.79–1.00. | Reworded to "about 70% up to essentially all" |
| 6 | RQ3 Table 2b | HOLD is "9–34% lower" than ACCEPT | **5–34%** lower | Corrected |
| 7 | RQ3 Table 2b | StagedMem0 ≈ N^1.12, BlackBoxStaged ≈ N^1.18 | These exponents hold only for a fit over N = 500–2,000. Over 100–2,000 they are 0.95 and 1.06. | Fit range stated |
| 8 | RQ3 Tables 1 and 3 | Medium claim judge = one call per write, $0.00123 | As run, the Mem0+DeepSeek guard used **two passes**: a strict prompt on all 97 sessions (2,845 in / 316 out tokens), then the accepted-assistant-facts prompt on the 30 sessions the strict pass flagged (4,809 / 345). As-run cost is **$0.00181 per write** at peak price. $0.00123 is the single-pass estimate, consistent with the single-pass Llama run ($0.00119). | Both values reported, labelled |
| 9 | RQ1 Table 2 | particle-1 neutral prompt: 2,740 claims | The artifacts hold 2,067 proposals and 2,772 verdict rows (2,308 unique), so the denominator is not reproducible. All rows have 0 flags. | Count dropped, "0 flags" kept |
| 10 | RQ1 Table 2 | Weak extractor: 1,259 claims | Mem0 emitted 1,265 ADD claims. 6 claims in 3 sessions got no verdict and were kept by claim drop. Official metrics score all 1,265. | Footnote added |
| 11 | RQ2 §7 | Mem0 first real-model run used 13,986 tokens | No artifact holds this. The committed evidence reads 0 because it was served from the cache. The earlier lexical-run evidence in git (`bb5b142`, `fc1bcdc`) records 8,449 (Mem0) and 5,300 (LangMem). | 13,986 removed |
| 12 | RQ2 §3.1 | 27 ms vs 17 ms accepted add at 2 memories | An older single measurement, superseded by the RQ3 benchmark (N = 0: 27.1 ms vs 17.1 ms ACCEPT, 11 repeats, IQR) | RQ3 numbers used |
| 13 | RQ2 §10 | Test counts (806 passed, …) | Not re-run here; these are branch-time numbers | Excluded from paper material |

### A1.2 Verified exactly

Everything else in the RQ1, RQ2 and RQ3 drafts matched the artifacts exactly at the reported precision. This includes:

- **RQ1:**
  - all official-metric values and paired CIs;
  - the user-split numbers;
  - the dose-response values;
  - the reviewer precision and kappa;
  - the official-judge vs reviewer agreement (20/35, 16/31, 27/35, 24/31, 8 of 21, 9 of 14);
  - the rubric-judge overlap (68 / 93 / 67 / 1 / 26);
  - the live-run metrics (4/643 claims flagged, 3/50 sessions, QA 4 vs 4 discordant);
  - LongMemEval and MemoryAgentBench;
  - Phase 1 detection and the single false hold;
  - graph vs `text_fair` 0 discordant on 66 + 1,422 + 4,508 + 97 = 6,093 cases;
  - repair recovery (14/14, 31/31, 54/54; 14, 33 and 58 retries) and 21 removals + 1 rewrite.
- **RQ2:**
  - contract suite 16/16, 16/16, 15/16 + 1 skipped, 15/16 + 1 skipped, 16/16;
  - blind spots per backend;
  - real-model decisions;
  - the Graphiti fidelity assertions.
- **RQ3:**
  - verifier and adapter medians and IQRs;
  - token, latency and dollar figures per component;
  - billing totals;
  - every trade-off cell and linear fit;
  - LongMemEval n = 200 paired results.

Two facts are added here that were not in the drafts:

- Without transcript grounding, Phase 1 detection is 956/1,111 (Medium) and 2,499/3,168 (Long).
- On the weak extractor, repair made 74 removals and 14 new or rewritten claims.

### A1.3 Weak data (real, but must be qualified when cited)

1. **The claim-judge flags are only partly human-confirmed.**
   - Strong extractor: 1/8 and 0/8 flags confirmed. Weak extractor: 13/27 and 9/23 confirmed.
   - The samples are small: 8 and 28 flagged claims.
2. **Same-family judges.** The claim judge, the HaluMem official accuracy judge and the LongMemEval answer judge are all DeepSeek. The benchmark uses GPT-4o, which is no longer available through the OpenAI API.
3. **Injected-fault labels are circular.** The injector creates both the fault and its label, so these runs show mechanism sensitivity, not natural prevalence.
4. **The live run is a single run.** It has no repeats, so run-to-run noise is unmeasured. The guard acted on only 3 of 50 sessions.
5. **The weak-extractor gain shrinks on the final users:** +0.9 pts (final) vs +2.8 pts (tuning).
6. **The Graphiti real-model run is near-vacuous.** 3 of 4 ACCEPTs were empty extractions, and the Berlin→Lisbon contradiction was not exercised.
7. **MemoryAgentBench uses a different guard,** an LLM conflict verifier rather than the claim judge or the structural verifier. Its detections were not reviewer-validated.
8. **The QA simulator (+9.0 EM pts) uses spec-derived labels.** It is not evidence about real dialogue.
9. **The LongMemEval 30-question pilot is superseded** by the 200-question run. Cite the n = 200 numbers.
10. **Timing comes from one machine** (i5-12450HX, `powersave` governor) with offline model fakes. The exponents and ratios transfer, the absolute milliseconds do not.
11. **The deterministic evaluation sets are single-reviewer** (synthetic v2: 36 cases; conversations: 28 steps).

## A2. RQ1: Reliability

> (a) Does MemBud reduce omissions, corruption of valid prior records and unsupported additions relative to an unguarded pipeline? (b) Does graph-based verification catch more errors than a non-graph verifier given the same criteria and budget?

### A2.1 Answer

**(b) No.** Graph and the fair text verifier (`text_fair`, which sees the same contradiction annotations) made identical decisions on all 6,093 tested cases (0 discordant). There is no graph advantage under equal information.

**(a) Conditional, not validated.**

- **Strong extractor.** With Mem0 on a strong model, natural errors are rare: 0.6–1.6% of claims are flagged. The guard has no measurable effect on HaluMem's official metrics, offline (97 sessions) or live (10 users).
- **When errors exist.** Dropping only the flagged claims raises the official weighted accuracy:
  - +0.7 to +4.7 pts on injected faults, rising with the dose;
  - +2.3 pts (CI 1.3–3.3) on a naturally weaker extractor (Mem0 + Llama-3.1-8B, 100 sessions), with no significant recall or F1 cost.
- **Why this is not validated.** Reviewers confirmed only 39–48% of the weak-extractor flags, and the metric judge is the same model family as the claim judge.
- **Session-level holding is not a useful operating point.** It costs recall everywhere.
- **Wording for the paper.** Do not write "MemBud reduces hallucinated memory" without these qualifiers.

### A2.2 Setup

| Item | Value |
|---|---|
| Main benchmark | HaluMem-Medium (rev. `cb04336`), 20 users × first ≤5 sessions, seed 20261121. Official extraction metrics re-implemented from MemTensor/HaluMem `eval/evaluation.py` @ `d653749`, differentially tested against the reference |
| Extractors | Mem0 2.2.1 + DeepSeek `deepseek-flash` (97 sessions, 1,286 claims); Mem0 2.2.1 + `meta-llama/llama-3.1-8b-instruct` (100 sessions, 1,265 claims, same users) |
| Claim judge (guard) | DeepSeek, batch prompt, accepted-assistant-facts policy |
| Official metric judge | DeepSeek with the official HaluMem prompts (deviation: the benchmark uses GPT-4o) |
| Conditions | **Unguarded** (commit all); **Session hold** (hold a session if any claim is flagged); **Claim drop** (drop only the flagged claims); **Guard + repair** (repair loop, ≤2 retries, then re-verification) |
| Statistics | Paired session bootstrap, 2,000 resamples (dose-response: 1,000), seed 20261122; Wilson 95% intervals; exact McNemar |
| User split | Seeded hash into tuning and final halves; descriptive only, nothing was tuned |
| Reviewer rubric | `docs/halumem-grounding-rubric.md`, frozen v1 (SHA-256 `9830c317…`) |

### A2.3 Table R1-1. Graph vs fair text verifier (RQ1b)

Source: `data/halumem-runs/phase1-*/checkpoint.jsonl`, `phase2-deepseek/report.json`, `docs/halumem-evaluation.md`.

| Data | Cases | Discordant decisions (graph vs `text_fair`) | McNemar p |
|---|---|---|---|
| Synthetic HaluMem-shaped fixture | 66 | 0 | 1.0 |
| HaluMem-Medium Phase 1 (real data, injected faults) | 1,422 | 0 | 1.0 |
| HaluMem-Long Phase 1 | 4,508 | 0 | 1.0 |
| HaluMem-Medium Phase 2 (natural Mem0 proposals) | 97 | 0 | 1.0 |
| **Total** | **6,093** | **0** | |

- Graph and `text_fair` agree in both grounding modes (with and without transcript grounding).
- Plain `text`, which does not see `evidence.contradicts`, loses only on corruption signalled solely through that field. It accepts 527 vs 310 cases on Medium.
- **An LLM transition verifier is different** (13-case synthetic held-out set, DeepSeek). With prompt v1, graph held 4 of 7 valid cases and text held 1 of 7. With prompt v2 all three conditions accepted 7/7. Prompt v2 was chosen on that held-out split, so it is not an independent test.

### A2.4 Table R1-2. Claim-judge flag rate (natural errors)

Source: `llm-claim-verdicts-*.jsonl` per run.

| Extractor / data | Claims judged | Flagged | Rate (Wilson 95%) | Sessions with a flag |
|---|---|---|---|---|
| Mem0 + DeepSeek, HaluMem-Medium | 1,286 | 20 | 1.6% (1.0–2.4) | 14 / 97 |
| Mem0 + DeepSeek, HaluMem-Long (10 users × 3 sessions) | 443 | 5 | 1.1% (0.5–2.6) | 2 / 30 |
| DeepSeek, over-inferring prompt (not Mem0) | 1,722 | 20 | 1.2% | 15 / 97 |
| particle-1 (free), neutral prompt (not Mem0) | – (see A1.1 #9) | 0 | 0% | 0 / 97 |
| **Mem0 + Llama-3.1-8B, HaluMem-Medium** | 1,259ᵃ | 68 | **5.4% (4.3–6.8)** | 31 / 100 |
| Same, rubric-aligned judge prompt | 1,259ᵃ | 93 | 7.4% | 38 / 100 |
| Live run, Mem0 + DeepSeek (10 users × 5 sessions) | 643 | 4 | 0.6% | 3 / 50 |

ᵃ Mem0 emitted 1,265 claims. 6 claims in 3 sessions received no verdict and were kept by claim drop.

Mem0 on a strong model produces few flagged claims. Neither longer sessions nor a different prompt raised the rate. Only a weaker model inside Mem0 did.

### A2.5 Table R1-3. Injected faults (mechanism test)

**Phase 1** (deterministic structural verifier, real HaluMem data, synthetic faults). Source: Phase 1 checkpoints.

| Set | Faulty cases detected, with transcript grounding | Without transcript grounding | Valid transitions falsely held |
|---|---|---|---|
| Medium | 1,111 / 1,111 (100%) | 956 / 1,111 (86.0%) | 1 / 311 |
| Long | 3,168 / 3,168 (100%) | 2,499 / 3,168 (78.9%) | 1 / 1,340 |

The unguarded pipeline detects 0%.

**Official metrics, dose-response** (claim judge + official judge, 97 sessions). Source: `data/halumem-runs/dose-response.json`; reproduced by re-running with 1,000 resamples.

The faults are nested by a seeded hash, two per injected session: one corrupted and one fabricated claim. Values are paired differences vs unguarded (fraction units, CI).

| Injected claim rate | Sessions injected | Guard + repair: ΔWA | Guard + repair: Δrecall | Claim drop: ΔWA | Claim drop: Δrecall |
|---|---|---|---|---|---|
| 0 (natural) | 0 | +0.0019 (−0.0003, 0.0058) | −0.0011 (−0.0041, 0.0018) | +0.0019 (−0.0003, 0.0058) | −0.0011 (−0.0041, 0.0018) |
| 1.1% | 7 | +0.0072 (0.0027, 0.0126) | −0.0021 (−0.0052, 0.0006) | +0.0071 (0.0027, 0.0125) | −0.0059 (−0.0117, −0.0014) |
| 4.1% | 27 | +0.0279 (0.0189, 0.0371) | −0.0081 (−0.0160, −0.0017) | +0.0267 (0.0180, 0.0354) | −0.0193 (−0.0309, −0.0098) |
| 7.1% | 47 | +0.0469 (0.0369, 0.0576) | −0.0075 (−0.0165, −0.0005) | +0.0450 (0.0359, 0.0551) | −0.0274 (−0.0418, −0.0153) |

- **What the judge caught:** 47/47 fabricated claims and 31/47 corrupted claims.
- **Slope:** WA gain ≈ +0.62 (claim drop) and +0.65 (repair) pts per pt of injected rate.
- **Labels are circular:** this is mechanism sensitivity only.
- **Not run:** the 14.5% dose.

### A2.6 Table R1-4. HaluMem official extraction metrics by condition (natural errors)

Source: `phase2-deepseek/official/official-metrics.json`, `phase3-weakmem0/official/official-metrics.json`.

| Extractor | Condition | WA | Recall | Weighted recall | Target precision | F1 | Sessions committed |
|---|---|---|---|---|---|---|---|
| Mem0 + DeepSeek (97) | Unguarded | 0.971 | 0.941 | 0.968 | 0.985 | 0.963 | 97 |
| | Session hold | 0.973 | 0.919 | 0.938 | 0.985 | 0.951 | 83 |
| | Claim drop | 0.973 | 0.940 | 0.966 | 0.985 | 0.962 | 97 |
| | Guard + repair | 0.973 | 0.940 | 0.966 | 0.985 | 0.962 | 97 |
| Mem0 + Llama-3.1-8B (100) | Unguarded | 0.868 | 0.693 | 0.790 | 0.983 | 0.813 | 100 |
| | Session hold | 0.913 | 0.531 | 0.598 | 0.984 | 0.690 | 69 |
| | Claim drop | 0.891 | 0.691 | 0.784 | 0.985 | 0.812 | 100 |
| | Guard + repair | 0.888 | 0.688 | 0.782 | 0.985 | 0.810 | 100 |

FMR is omitted. Under the reference rule, removing claims makes more interference points count as "resisted", so its rise is an artifact of removal.

### A2.7 Table R1-5. Paired differences vs unguarded (CI)

| Extractor | Condition | ΔWA | ΔRecall | ΔF1 |
|---|---|---|---|---|
| Mem0 + DeepSeek (97) | Session hold | +0.0018 (−0.0033, 0.0077) | **−0.0220 (−0.0392, −0.0095)** | **−0.0118 (−0.0215, −0.0051)** |
| | Claim drop | +0.0019 (−0.0003, 0.0056) | −0.0011 (−0.0042, 0.0020) | −0.0007 (−0.0023, 0.0009) |
| | Guard + repair | +0.0019 (−0.0003, 0.0056) | −0.0011 (−0.0042, 0.0020) | −0.0007 (−0.0023, 0.0009) |
| Mem0 + Llama-3.1-8B (100) | Session hold | +0.0447 (0.0231, 0.0678) | **−0.1620 (−0.2493, −0.0808)** | **−0.1231 (−0.2031, −0.0578)** |
| | Claim drop | **+0.0226 (0.0133, 0.0332)** | −0.0026 (−0.0125, 0.0084) | −0.0011 (−0.0080, 0.0064) |
| | Guard + repair | **+0.0204 (0.0096, 0.0339)** | −0.0057 (−0.0106, −0.0011) | −0.0034 (−0.0069, −0.0004) |

**User split, weak extractor** (descriptive):

| Users | Claim drop: ΔWA | Claim drop: Δrecall | Guard + repair: ΔWA | Guard + repair: Δrecall |
|---|---|---|---|---|
| Tuning (14 users, 70 sessions) | +0.028 (0.016, 0.042) | −0.003 (−0.017, 0.013) | +0.026 (0.011, 0.045) | −0.008 (−0.015, −0.002) |
| Final (6 users, 30 sessions) | +0.009 (0.002, 0.018) | −0.002 (−0.006, 0.000) | +0.007 (0.001, 0.015) | 0.000 (−0.006, 0.004) |

**User split, strong extractor, claim drop:** tuning +0.000 (−0.001, 0.001); final +0.006 (−0.000, 0.019).

**Why session hold costs recall.**

- On the strong extractor, the hold removed 230 claims, of which only 20 (8.7%) were flagged.
- That cost 41 recalled gold points (2.2% of 1,862). Claim drop loses 2.
- On the weak extractor, the larger WA gain of the hold comes from discarding 31 whole sessions.

**What repair does.** Repair is close to deletion:

- strong extractor: 21 claims removed, 1 rewritten;
- weak extractor: 74 removed, 14 new or rewritten.

### A2.8 Table R1-6. Judge validity: two blind reviewers, frozen rubric

Source: `claim-audit-dual/dual-agreement.json`, `claim-audit-weak/dual-agreement.json`, reviewer answer files.

| Sample (36 claims each) | Judge-flagged | Flag precision, reviewer 1 | Flag precision, reviewer 2 | Miss rate (unflagged called unsupported) | Inter-rater agreement |
|---|---|---|---|---|---|
| Mem0 + DeepSeek | 8 | 1/8 = 12.5% (2–47) | 0/8 = 0% (0–32) | 0/28 and 1/28 | 34/36, κ = −0.03 |
| Mem0 + Llama-3.1-8B | 28 | 13/27 = 48% (31–66) | 9/23 = 39% (22–59) | 1/8 and 1/8 | 30/30 decisive, κ = 1.0 (6 unsure/blank) |
| Second claim judge (particle-1), Mem0 + DeepSeek | 6 in sample | 0/6 | 0/6 | – | – |

**Does the official accuracy judge agree with the reviewers?** Weak-extractor quiz, 36 claims.

| Mapping of official score (0/1/2) | Reviewer 1 | Reviewer 2 |
|---|---|---|
| "Unsupported" = score < 2 | 20/35 = 57% (41–72) | 16/31 = 52% (35–68) |
| "Unsupported" = score 0 only | 27/35 = 77% (61–88) | 24/31 = 77% (60–89) |
| Reviewer-SUPPORTED claims scored 1 ("partially accurate") | 8 of 21 | 8 of 21 |
| Judge-flagged, reviewer-SUPPORTED claims scored < 2 | 9 of 14 | 9 of 14 |

**Claim judge vs official accuracy judge, all claims (corrected, see A1.1 #1).**

| Extractor | Flagged claims scored < 2 | Unflagged claims scored < 2 (all sessions) |
|---|---|---|
| Mem0 + Llama-3.1-8B | 49/68 = 72% (60–81) | 180/1,196 = 15.1% |
| Mem0 + DeepSeek | 4/20 = 20% (8–42) | 68/1,266 = 5.4% |

Reading:

- The official judge often gives partial credit (score 1) to claims the reviewers accept.
- Removing such claims raises WA, because score 1 counts as half credit.
- Part of the weak-extractor gain therefore comes from removing claims that people accept. It is a gain on a model-judged metric, not a human-confirmed reduction of hallucinations.
- Both judges are DeepSeek, so their agreement is not independent validation.

### A2.9 Table R1-7. Pre-registered rubric-aligned judge prompt (negative result)

Source: `phase3-weakmem0/llm-claim-verdicts-rubric.jsonl`.

The prompt was committed in `6adcf12` at 17:20:35. The verdict file is time-stamped 17:24:20, after the commit, which is consistent with pre-registration.

| | Original judge | Rubric-aligned judge |
|---|---|---|
| Claims flagged (1,259) | 68 | 93 |
| Flagged by both | 67 | 67 |
| Flagged by this judge only | 1 | 26 |
| On the 36 reviewer-labelled claims | 13/27 and 9/23 confirmed | identical flags, so identical precision |

The new prompt did not reduce false flags. The 26 extra flags have no reviewer labels.

### A2.10 Table R1-8. Live run: OSS Mem0 vs Mem0 + MemBud (official procedure)

Source: `data/halumem-live/results/memzero-deepseek-{pure,membud-drop}-s5/`.

Setup:

- `scripts/run_halumem_live.py`, following the official `eval_memzero.py` per-user procedure on open-source Mem0 2.2.1;
- scored by the unchanged official `evaluation.py`;
- 10 users × 5 sessions: 50 sessions, 57 update points, 98 questions;
- DeepSeek as extractor, answer model and judge;
- MemBud arm: `StagedMem0`, claim-judge grounding, claim drop.

| Official metric | Pure Mem0 | Mem0 + MemBud (claim drop) |
|---|---|---|
| Recall / weighted recall | 0.961 / 0.978 | 0.958 / 0.977 |
| Target precision / WA | 0.980 / 0.940 | 0.982 / 0.941 |
| Extraction F1 | 0.970 | 0.970 |
| Update correct / hallucination / omission | 0.614 / 0.070 / 0.316 | 0.596 / 0.035 / 0.351 |
| QA correct / hallucination / omission | 0.939 / 0.041 / 0.010 | 0.939 / 0.020 / 0.041 |

- **What the guard did:** it flagged 4 of 643 claims (3 of 50 sessions).
- **QA:** 4 vs 4 discordant questions.
- **Result: no measurable effect.** The 1–4 item differences are within unmeasured run-to-run noise. There was a single run per arm.
- **Not run:**
  - a weak-extractor live run (the free model tried failed on long sessions; no budget remained);
  - a hold arm;
  - repeats.
- **MemBud's shipped grounding cannot run on HaluMem.** The default `LexicalGrounding` held **97/97 and 100/100** sessions in the recorded runs. Every guard number above therefore uses the LLM claim judge as grounding policy.

### A2.11 Table R1-9. Other downstream checks

Source: `data/memagentbench-runs/conflict/6k/report.json`. For LongMemEval QA, see RQ3 Table R3-6 (n = 200, which supersedes the 30-question pilot).

| Benchmark | Unguarded | Guard (hold) | Guard + repair | Test |
|---|---|---|---|---|
| MemoryAgentBench FactConsolidation 6k, single-hop (100 q) | 1.00 (0.96–1.00) | 0.26 (0.18–0.35) | 0.52 (0.42–0.62) | Unguarded beats guard 74 vs 0 and repair 48 vs 0, p < 0.001 |
| Same, multi-hop (100 q) | 0.13 (0.08–0.21) | 0.00 (0.00–0.04) | 0.09 (0.05–0.16) | vs guard p = 0.0002; vs repair 10 vs 6, p = 0.45 |

- The guard here is an LLM conflict-hold verifier, not the claim judge. Its detections were not reviewer-validated.
- MemoryAgentBench is a latest-fact-wins task, so holding a conflicting new fact keeps the stale value. That is wrong by design.
- The guard held 21 of 23 chunks and dropped 157 new facts.

### A2.12 Claims the RQ1 data supports

- Graph verification gives no advantage over a fair text verifier on any tested case (6,093 cases, 0 discordant).
- The deterministic verifier detects all injected faults when transcript grounding is used, with 1 false hold per set. On the official metric, the guard's WA gain rises with the injected error rate.
- On Mem0 with a strong model, the guard has no measurable effect on the official extraction metrics. Claim drop and repair are indistinguishable from unguarded. This holds offline and in the live run.
- On Mem0 with a weaker model (unselected, 100 sessions), claim drop raises DeepSeek-judged WA by +2.3 pts (CI 1.3–3.3) with no significant recall or F1 cost: +2.8 on tuning users, +0.9 on final users. **This is not validated.**
- Session-level holding costs 2.2–16.2 pts of recall and is not a useful operating point.
- On MemoryAgentBench conflict resolution, an LLM conflict-hold guard lowered accuracy. Repair reduced the harm but did not remove it.

### A2.13 Claims the RQ1 data does not support

- "MemBud reduces hallucinated memory": no human-validated reduction.
- Any reduction of omissions or corruption of prior records on natural data. Mem0's `add()` emitted only ADD events (and 3 NONE events on Long), no UPDATE or DELETE, so these error types could not be observed.
- Any graph-specific benefit.

## A3. RQ2: Modularity

> Can the same verification logic operate across different host memory backends through adapters? What information or transformations does each adapter require, and what is lost?

### A3.1 Answer

**Yes for a defined class of backends, no in general.**

- **What ran unchanged:** the same verifier and the same 16-case contract suite ran on three real backend families through one black-box adapter (`BlackBoxStaged`) that uses only public API calls:
  - Mem0 2.2.1;
  - LangMem 0.0.30 on a LangGraph store;
  - Graphiti 0.31.2 on Neo4j 5.26.0.
- **What could not be put behind it:** five other systems, each for a recorded, executable reason.
- **What is lost:** everything the public API does not expose. For a graph memory, that is its provenance.
- **Conclusion:** this is evidence for conditional modularity, not for universality.

### A3.2 Design of the black-box adapter

For each write, `BlackBoxStaged` does five things:

1. **Observe:** it reads the live state through public `list` calls, plus optional `schema` and `history` calls.
2. **Stage:** it seeds an isolated scratch twin, runs the backend's native write on the twin, and diffs the twin before and after to derive the side effects.
3. **Reject unmodeled writes:** it refuses writes to a foreign scope and changes to schema, scope identifiers, metadata or history.
4. **Verify:** it runs the existing deterministic verifier, unchanged.
5. **Commit:** on ACCEPT only, it replays the verified set on live through public calls. It refuses stale state, checks the result, and compensates through an undo list on failure.

The one extra requirement on the backend is `scratch_factory()`, a second isolated instance.

### A3.3 Table R2-1. Contract suite per backend

Source: `data/results/adapter-contract-v1/<backend>/evidence.json`.

| Backend (pinned) | Adapter | Contract suite (16 cases) | Verdict |
|---|---|---|---|
| Mem0 `2.2.1` (spaCy 3.8.18) | `mem0_staged` (shadow clone) | 16/16 passed | Sees every write surface |
| Mem0 2.2.1 | `BlackBoxStaged` | 16/16 passed | Blind to DDL and entity-store rows |
| LangMem `0.0.30` on LangGraph `InMemoryStore` (langgraph 1.2.24, trustcall 0.0.39) | `BlackBoxStaged` | 15 passed, 1 skipped | No history surface |
| Graphiti `0.31.2` on Neo4j Community `5.26.0` (driver 6.4.1) | `BlackBoxStaged` | 15 passed, 1 skipped | Loses provenance |
| stdlib SQLite fake | `BlackBoxStaged` | 16/16 passed | Control with hidden surfaces |

The skipped case is "history rewrite rejected". LangMem and Graphiti keep no history, so that effect cannot be expressed.

The suite covers:

- HOLD byte-identity;
- unauthorized delete rejected;
- ACCEPT exactness;
- scope isolation;
- stale-state refusal;
- commit-failure compensation;
- loud unrecoverable failure;
- fail-closed on backend error;
- direct writes refused;
- foreign-scope and history-rewrite rejection;
- declared blind spots.

Mutation checks confirm that the suite catches regressions. Disabling the stale check, disabling compensation, or making HOLD write each makes the corresponding tests fail.

### A3.4 Table R2-2. Black-box vs shadow-clone adapter on the same real Mem0

| If the native write also … | `BlackBoxStaged` | `mem0_staged` |
|---|---|---|
| writes to a foreign scope | REJECTED | REJECTED |
| rewrites history | REJECTED | REJECTED |
| issues DDL | ACCEPTED, unseen | REJECTED |
| writes an entity-store row the API does not expose | ACCEPTED, unseen | REJECTED |

Cost, from the RQ3 benchmark (Table R3-2b): an accepted one-fact add takes 27.1 ms (black-box) vs 17.1 ms (shadow clone) at N = 0. At N = 2,000 it takes 5.5 s vs 1.07 s.

This is the central trade-off. The shadow clone sees everything but must know Mem0's storage layout. The black-box adapter generalizes but sees only the public API.

### A3.5 What a backend must offer (RQ2, first half)

1. **Observable state:** the whole state, with stable ids, readable through public calls.
2. **An isolatable twin:** a second instance whose writes cannot reach the first.
3. **A reducible native write:** the write reduces to plain inserts, updates and deletes of observable items.
4. **Public undo:** a way to reverse writes, for compensation.
5. *(Optional)* `schema()` and `history(id)`, which widen what can be detected.

### A3.6 Table R2-3. What each adapter had to supply

| Backend | Id / text | Scopes | Twin | Extra work |
|---|---|---|---|---|
| Mem0 | memory id / memory | Explicit scope list (`get_all` cannot enumerate); `top_k` raised; truncation refused | Second embedded Qdrant + SQLite host | History normalized to `[event, old, new]` |
| LangMem | store key / content | Namespace `(memories, user)`, enumerable | Second `InMemoryStore` | Timestamps excluded |
| Graphiti | fact-edge uuid / `fact` | `group_id` partition, enumerated by Cypher | Another `group_id` in the same database | Empty partitions raise errors, which are caught; endpoint nodes are created on replay |

### A3.7 Table R2-4. What is lost (RQ2, second half)

| Backend | Seen | Not seen (declared blind spots) |
|---|---|---|
| Mem0 (black-box) | Memory text, metadata, history, foreign scopes | DDL, entity-store rows, other collections |
| LangMem | Items, foreign namespaces | Vector index, store internals, schema |
| Graphiti | Fact edges, relation, endpoint names, expiry flag, foreign partitions | Nodes, episodes, `MENTIONS`, summaries, embeddings, provenance, temporal fields, indexes |

**Graphiti fidelity test** (`tests/test_graphiti_fidelity.py`). The same add, unguarded vs guarded:

| | Fact edges | Entity nodes | Episode nodes | `MENTIONS` edges |
|---|---|---|---|---|
| Unguarded native write | 2 | 3 | 1 | 3 |
| Guarded ACCEPT | 2 | 3 | 0 | 0 |

The guarded graph has the same facts but no episode (provenance) and no mentions. A graph memory whose value lies in provenance loses exactly that under a facts-only observation model.

### A3.8 Table R2-5. Systems that could not be put behind the adapter

Each reason below is an executable test or a recorded observation.

| System | Reason |
|---|---|
| A-Mem (git `ceffb86`) | A second in-process instance resets the shared chroma client, so no twin is possible. `add_note` always runs the evolution LLM and may rewrite neighbouring notes. |
| LlamaIndex `0.14.26` fact memory | Facts have no ids. Facts are extracted only on token overflow. Exceeding `max_facts` replaces the whole list. |
| AutoGen `autogen-core 0.7.5` | add/query/clear only, with no ids and no native write |
| Graphiti on embedded Kuzu `0.11.3` | `group_id` raises on `KuzuDriver`. The full-text extension needs a network download. |
| Letta `0.16.4` | Requires Postgres; no SQLite path |
| Zep Cloud | SaaS, not tried |

**The pattern.** Backends that qualify have stable ids, a readable state, an isolatable twin, and a write that reduces to plain inserts. Backends that fail typically rewrite neighbours or the whole store on write, lack ids, or have nothing to stage.

### A3.9 Table R2-6. Real-model runs

Source: `data/results/*-blackbox-realmodel-v1/evidence.json`; lexical runs from git `bb5b142`, `fc1bcdc`.

All runs used the free OpenRouter model `nvidia/nemotron-3.5-lightning:free` ($0), synthetic dialogues, and add operations only.

| Backend | Grounding policy | Sessions | ACCEPT | HOLD | Tokens | Note |
|---|---|---|---|---|---|---|
| Mem0 | `LexicalGrounding` | 5 | 0 | 5 | 8,449 | Third-person "User …" rewrite of first-person speech not grounded |
| LangMem | `LexicalGrounding` | 5 | 0 | 5 | 5,300 | Same cause |
| LangMem | `SpeakerNormalizedGrounding` | 5 | 3 | 2 | 11,762 | Holds caused by synonym ("resides" vs "live") and spelling ("favorite" vs "favourite") |
| Mem0 | `SpeakerNormalizedGrounding` | 5 | 1 | 4 | n/a (cached) | Holds include hallucinated "Germany" and invented dates |
| Graphiti | `SpeakerNormalizedGrounding` | 4 | 4 | 0 | 74,258 | **3 of 4 ACCEPTs were empty extractions**; only "moved from Berlin to Lisbon" persisted |

Reading:

- **Fail-closed:** the guard failed closed on real model output on every backend, and hallucinated details were held.
- **False holds:** it also held true facts because of paraphrase.
- **Weak evidence:** each run is one free model and 4–5 sessions, with no update or delete.
- **Graphiti:** the run is near-vacuous, and its contradiction path was not exercised.

### A3.10 Limits of RQ2

- The contract suite ran with scripted, offline chat models.
- The real-model evidence is small and add-only, with no statistics and no HaluMem data.
- Only one store variant was tested per backend: embedded Qdrant for Mem0, `InMemoryStore` for LangMem, Neo4j Community for Graphiti.
- No concurrent writers were tested.
- Versions other than the pinned ones are untested.

## A4. RQ3: Trade-offs

> How does verification affect valid-update acceptance, downstream QA, latency, token usage and API cost, including graph-construction overhead?

### A4.1 Answer

- **Valid-update acceptance.** The cost depends on how much a guard throws away.
  - **Deterministic verifier** (graph and `text_fair` decide identically): it falsely holds at most 1 valid update per labelled set (1/311, 1/1,340).
  - **Claim drop:** it removes 1.6% of claims on the strong extractor, and reviewers call most of those valid.
  - **Session hold:** it holds 17.9% of claims.
- **Downstream QA.** No guard condition improved QA on real data.
  - Session hold *lowers* LongMemEval accuracy by 6.5 pts (CI −10.0 to −3.5; n = 200; p = 0.0002).
  - Guard + repair costs 1.0 pt (CI −2.5 to 0.0; p = 0.5).
  - Claim drop left live HaluMem QA unchanged.
- **Latency, tokens and cost.** These are set by the LLM judge, not by the graph.
  - The judge makes one call per write: median 1.2–1.6 s, 2.8k–9.7k input tokens, about $0.0012–0.0034 per write at list price.
  - That is about 0.3× (Mem0) to 1.3× (LongMemEval) of the host's own extraction cost.
  - Repair adds about 3.8 s and $0.003–0.004 per held write.
  - Deterministic verification costs microseconds to milliseconds.
- **Graph-construction overhead.**
  - Graph build is linear and cheap: 8.2 ms at 2,000 records, about 1.1× text serialization.
  - The standalone graph verifier is quadratic (2.2 s at 2,000 records, vs 4.9 ms for text). The cause is a fixable implementation detail.
  - Inside the Mem0 adapters, graph vs text is not detectable, because the adapter's state copy dominates.
- **Spend.** The only billed figure is the DeepSeek export: $4.21 for 2026-11-21 to 11-24, all project traffic. The repository's peak-price rule over-estimates the same tokens 2.87×.
- **Trade-off.** On injected faults, guard + repair has a net gain (ΔWA + Δrecall) that is positive from the lowest dose. Its CI excludes zero from the 4.1% dose. At natural error rates on a strong extractor, the net is indistinguishable from zero. F1 never improves in the measured range.

### A4.2 Table R3-1. RQ3 in one table

| Dimension | Unguarded | Session hold | Claim drop | Guard + repair | Deterministic verifier (graph / text / text_fair) |
|---|---|---|---|---|---|
| **Valid-update acceptance** | All accepted [M] | Holds 17.9% of claims (230/1,286; Wilson 15.9–20.1%) on Mem0+DeepSeek; 31/100 sessions on Mem0+Llama [M] | Removes 1.6% of claims (20/1,286). Reviewers confirm 1/8 and 0/8 of the flags, so ≈1.4–1.6% of claims are valid claims removed [M; share E]. Llama: 5.4% removed, ≈2.8–3.3% valid removed [E] | Recovers 14/14 (strong), 31/31 (weak), 23/23 (LongMemEval) held sessions; mostly by deleting the flagged claims [M] | Same decision on every case. Valid accepted 14/14 (synthetic v2), 17/17 (conversations), 310/311 and 1,339/1,340 (HaluMem Phase 1) [M] |
| **Extraction quality** (strong extractor) | baseline | Recall −2.2 pts [−3.9, −1.0]; net −2.0 [−3.7, −0.8] [M] | Recall −0.1 [−0.4, +0.2]; WA +0.2 [−0.0, +0.6] [M] | Same as claim drop [M] | U |
| **Downstream QA** | LongMemEval 0.650; live HaluMem 0.939 [M] | LongMemEval **−6.5 pts [−10.0, −3.5]**, 13 vs 0, p = 0.0002 [M] | Live HaluMem 0.939 vs 0.939, 4 vs 4 discordant [M] | LongMemEval −1.0 pt [−2.5, 0.0], 2 vs 0, p = 0.5 [M] | Simulator only: +9.0 EM pts at fault rate 0.35 (spec labels) [M]; real QA U |
| **Latency per write** | Mem0 extraction median 3.16 s (p90 5.03) [M] | + judge 1.23 s (p90 2.10) Medium; 1.57 s Long; 1.30 s LongMemEval [M] | Same judge [M] | + about 3.8 s per held write (repair 2.08 s + re-judge 1.70 s) [M] | Verify phase in adapter at N = 2,000: text ≈24 ms; graph 174–189 ms in 3 of 4 cells (GC-affected) [M]. State copy: StagedMem0 17 ms → 1.07 s; BlackBoxStaged 27 ms → 5.5 s (N = 0 → 2,000) [M] |
| **Tokens per write** | Mem0 extraction ≈11.0k in / 0.7k out; LongMemEval extractor 3.1k / 0.2k [M] | Judge 2,845 / 316 (Medium, strict pass); 2,772 / 300 (Llama run); 9,713 / 384 (Long); 3,602 / 244 (LongMemEval) [M] | Same [M] | + 14.4% (strong) or 31% (weak) of writes × ≈9.4k / 0.8k of repair [M] | 0 [M] |
| **API cost per write (peak list price)** | Mem0 extraction $0.0041 [E] | $0.00123 single pass; $0.00181 as run with two passes (Medium) [E] | Same [E] | $0.00177 single pass, $0.00235 as run (strong); $0.00220 (weak) [E] | $0 [M] |
| **Cost per prevented error** | – | U (holding loses more than it removes) | Injected: $0.0109 / $0.0027 / $0.0016 at 1.1 / 4.1 / 7.1%. Natural: $0.0036–0.0045 (weak), $0.048 to unbounded (strong) [E] | Injected: $0.0172 / $0.0054 / $0.0037 [E] | – |
| **Billed spend** | All DeepSeek project traffic 2026-11-21 to 11-24: **$4.21**, 16,624 requests [M]. Per condition: U. OpenRouter: U | | | | |
| **Graph-construction overhead** | – | – | – | – | Build linear (N^1.10), 8.2 ms at N = 2,000. Standalone verify quadratic (N^2.18), 2.2 s vs 4.9 ms text. In adapters, graph vs text not detectable in 18 of 20 cells [M] |

### A4.3 Table R3-2a. Verifier micro-benchmark

Source: `data/results/rq3-overhead-v1/verifier.json`.

Settings:

- k = 4 operations, µs per call, median [IQR];
- 25 interleaved repeats, GC off;
- Python 3.14.10, i5-12450HX, governor `powersave`;
- noise floors: empty-call floor 0.022 µs; A/A floor 1.1% at the 95th percentile.

| Prior records N | Graph build | Graph verify | Graph total | Text serialize | Text verify | Fair text verify | Graph total / text verify |
|---|---|---|---|---|---|---|---|
| 0 | 21.5 [0.6] | 72.8 [2.0] | 101.5 [2.1] | 30.7 [0.4] | 18.0 [0.2] | 18.6 [0.2] | 5.6× |
| 10 | 52.5 [0.6] | 189 [4] | 250 [5] | 58.3 [0.5] | 39.4 [0.7] | 41.0 [0.5] | 6.3× |
| 100 | 322 [4] | 4,281 [63] | 4,604 [75] | 344 [4] | 225 [4] | 231 [4] | 20× |
| 250 | 825 [11] | 25,107 [1,524] | 25,916 [696] | 823 [17] | 540 [9] | 549 [11] | 48× |
| 500 | 1,772 [77] | 104,678 [4,096] | 108,210 [3,611] | 1,667 [34] | 1,128 [32] | 1,157 [31] | 96× |
| 1,000 | 3,844 [201] | 539,908 [6,781] | 560,008 [8,696] | 3,446 [163] | 2,296 [64] | 2,353 [74] | 244× |
| 2,000 | 8,159 [321] | 2,227,953 [45,387] | 2,344,574 [56,939] | 7,152 [380] | 4,924 [196] | 5,116 [271] | 476× |

- **Scaling** (log-log slope, N = 250–2,000):
  - linear: graph build 1.10, text serialize 1.04, text verify 1.06, fair text 1.07;
  - quadratic: graph verify 2.18.
- **Cause of the quadratic term.** The required-claims check (`src/membud/graph_verification.py:104-108`) rebuilds the full edge set once per required claim. By estimate, this accounts for about 70% to essentially all of graph-verify time at N ≥ 100. It is an implementation artifact, not a property of graphs, and it was not changed because RQ3 measures the evaluated code.
- **Fair text vs plain text:** +1% to +5%, detectable in 10 of 35 cells.
- **A/A check:** never detectable (ratios 0.97–1.01).
- **Transition size k:** at N = 500, graph total is 108 ms at k = 4 and 134 ms at k = 64.

### A4.4 Table R3-2b. Guarded `add` on real Mem0

Source: `data/results/rq3-overhead-v1/adapters.json`.

Settings: one fact, ACCEPT, median ms [IQR]; Python 3.12.16; Mem0 2.2.1 with offline LLM and embedder fakes; 11 repeats (7 at N ≥ 1,000).

| Live records N | StagedMem0, graph | StagedMem0, text | BlackBoxStaged, graph | BlackBoxStaged, text |
|---|---|---|---|---|
| 0 | 17.1 [1.2] | 16.4 [1.4] | 27.1 [1.0] | 26.5 [0.4] |
| 100 | 58.1 [1.5] | 57.0 [2.5] | 220 [7] | 222 [10] |
| 500 | 226 [5] | 224 [7] | 1,074 [21] | 1,065 [39] |
| 1,000 | 440 [1] | 441 [7] | 2,328 [20] | 2,369 [49] |
| 2,000 | 1,069 [29] | 1,060 [6] | 5,487 [75] | 5,505 [71] |

- **Graph vs text within an adapter:** not detectable in 18 of 20 (adapter, N, decision) cells. The two flagged cells point in opposite directions.
- **Scaling** (fit N = 500–2,000): StagedMem0 ≈ N^1.12; BlackBoxStaged ≈ N^1.18 and about 5× slower. The black-box adapter re-seeds a twin through the public API, which takes about 95% of its time at N ≥ 500.
- **HOLD vs ACCEPT:** HOLD is 5–34% faster than ACCEPT.
- **Transition size:** k = 16 facts adds 17% (Staged) and 7% (BlackBox) at N = 500.
- **Verify share of total (StagedMem0, graph):** 0.6% / 2.6% / 3.4% / 3.6% at N = 0 / 100 / 500 / 1,000.
- **Reproduction:** StagedMem0 HOLD 14.5 / 43.6 / 148.8 ms at N = 0 / 100 / 500. This reproduces the earlier `mem0-guarded-v1/timing.json` (14.3 / 45.4 / 146.9) within a few percent.

### A4.5 Table R3-3. Tokens, latency and cost per call

Source: `data/results/rq3-cost-v1/token-attribution.json`.

Tokens are provider-reported. Dollars use the peak cache-miss list price ($0.30 / $1.20 per M input / output) and are estimates [E]. Multiply by 0.349 for the billing-calibrated value.

| Component | Role | Calls | Input / output tokens per call | Median latency (p90) | $ per call, peak |
|---|---|---|---|---|---|
| Mem0 extraction, DeepSeek (attributed by response shape, mixed runs) | host | 178 | 11,013 / 696 | 3.16 s (5.03) | 0.00414 |
| Claim judge, strict pass, Mem0+DeepSeek Medium | guard | 97 | 2,845 / 316 | 1.23 s (2.10) | 0.00123 |
| Claim judge, accepted-policy re-judge of the 30 strict-flagged sessions | guard (as run) | 30 | 4,809 / 345 | 2.01 s (2.65) | 0.00186 |
| Claim judge, Mem0+Llama Medium (single pass) | guard | 100 | 2,772 / 300 | 1.57 s (2.73) | 0.00119 |
| Claim judge, Mem0+DeepSeek Long | guard | 27 | 9,713 / 384 | 1.57 s (1.85) | 0.00337 |
| Claim judge, LongMemEval (incl. re-verification) | guard | 343 (324 sessions) | 3,602 / 244 | 1.30 s | 0.00137 |
| LongMemEval extractor | host | 324 | 3,110 / 177 | 1.30 s | 0.00115 |
| Repair extractor + re-judge, per held session (Medium) | repair | 14 + 14 | 9,386 / 772 (both calls) | 2.08 s + 1.70 s | 0.00374 |
| Repair, per held session (Llama run) | repair | 33 + 33 | 7,778 / 616 | 1.94 s + 1.75 s | 0.00327 |
| HaluMem official scoring (Medium) | evaluation only | 3,219 (33.2 per session) | 2,160 / 90 | 1.24 s | 0.0251 per session |

Evaluation-only scoring is **94%** of the spend of a HaluMem condition comparison: $2.86 vs $0.17 per 97-session run at peak.

### A4.6 Table R3-4. Billing reconciliation (owner-supplied DeepSeek export)

Source: `data/results/rq3-cost-v1/billing-reconciliation.json`.

| Day | Requests | Input cache-hit tokens | Input cache-miss tokens | Output tokens | Billed USD [M] |
|---|---|---|---|---|---|
| 2026-11-21 | 97 | 733,568 | 221,032 | 59,473 | 0.071 |
| 2026-11-22 | 4,077 | 4,489,984 | 4,477,576 | 489,840 | 0.979 |
| 2026-11-23 | 8,484 | 7,920,804 | 7,242,657 | 886,126 | 2.409 |
| 2026-11-24 (export taken mid-day) | 3,966 | 4,517,662 | 3,198,323 | 430,216 | 0.751 |
| **Total** | **16,624** | **17,662,018** | **15,139,588** | **1,865,655** | **4.210** |

- **Price-basis error.** The peak cache-miss rule applied to the billed tokens gives $12.08, which is **2.87×** the bill. The off-peak rule gives $6.04 (1.43×).
  - **Why:** 54% of input tokens were cache hits, which bill at a much lower rate.
  - **Calibration factor:** 0.349, valid only for this workload mix.
- **Local-cache coverage:** 70% of billed input tokens, 74% of output tokens, 70% of requests.
- **Not available:**
  - per-condition billed cost (the provider gives no per-call breakdown);
  - an OpenRouter bill, so the Llama-3.1-8B extraction is not bill-verified.

### A4.7 Table R3-5. Trade-off model: benefit, collateral and cost per prevented error

Source: `data/results/rq3-tradeoff-v1/tradeoff.json`.

Setup: HaluMem official metrics, 97 sessions, nested injected doses, paired bootstrap with 2,000 resamples (seed 20261124), pts with CI. **Net = ΔWA + Δrecall.** The injected labels are circular.

| Injected rate | Condition | ΔWA | ΔRecall | ΔF1 | **Net** | Faults removed | Valid claims removed | $ per removed fault, peak |
|---|---|---|---|---|---|---|---|---|
| 0 (natural) | Claim drop = repair | +0.19 [−0.03, 0.57] | −0.11 [−0.41, 0.18] | −0.07 [−0.23, 0.09] | +0.08 [−0.34, 0.63] | – | 20/1,286 (1.56%) | see natural |
| | Session hold | +0.18 [−0.32, 0.78] | −2.20 [−3.94, −0.99] | −1.18 [−2.15, −0.52] | **−2.02 [−3.66, −0.81]** | – | 230/1,286 (17.9%) | – |
| 1.1% | Claim drop | +0.71 [0.26, 1.27] | −0.59 [−1.17, −0.14] | −0.32 [−0.63, −0.08] | +0.12 [−0.33, 0.53] | 11/14 | 18/1,279 | 0.0109 |
| | Guard + repair | +0.72 [0.26, 1.29] | −0.21 [−0.52, 0.06] | −0.12 [−0.29, 0.03] | +0.50 [−0.01, 1.15] | 11/14 | – | 0.0172 |
| 4.1% | Claim drop | +2.67 [1.82, 3.58] | −1.93 [−3.17, −1.03] | −1.02 [−1.70, −0.55] | +0.73 [−0.25, 1.58] | 45/54 | 17/1,259 | 0.0027 |
| | Guard + repair | +2.79 [1.90, 3.74] | −0.81 [−1.67, −0.19] | −0.42 [−0.87, −0.10] | **+1.98 [1.00, 3.03]** | 45/54 | – | 0.0054 |
| 7.1% | Claim drop | +4.50 [3.52, 5.48] | −2.74 [−4.12, −1.62] | −1.45 [−2.20, −0.86] | **+1.76 [0.55, 2.80]** | 78/94 | 15/1,239 | 0.0016 |
| | Guard + repair | +4.69 [3.67, 5.72] | −0.75 [−1.67, −0.09] | −0.41 [−0.89, −0.06] | **+3.94 [2.68, 5.11]** | 78/94 | – | 0.0037 |
| | Session hold | +5.32 [3.74, 6.98] | −44.8 [−58.6, −31.2] | −30.3 [−44.0, −19.1] | −39.5 [−53.4, −25.7] | 94/94 | 57.7% of valid claims | – |

**Linear fit over the measured doses (0–7.1%),** pts per pt of injected rate:

- **WA:** claim drop +0.62 [0.47, 0.77]; repair +0.65 [0.50, 0.81].
- **Net:** claim drop +0.24 [0.05, 0.39]; repair +0.55 [0.37, 0.72].
- **F1:** claim drop −0.20 [−0.30, −0.11]; repair −0.05 [−0.13, −0.003].

**Where benefit exceeds cost:**

- **Break-even of the net fit:** at or below the bottom of the measured range. Claim drop: 0.27% (CI −3.0 to 2.6%). Repair: 0.08% (CI −1.0 to 0.9%).
- **Net CI excludes zero** from 4.1% (repair) and from 7.1% (claim drop).
- **F1** never benefits in the measured range.
- **Session hold** is net-negative everywhere.
- **Above 7.1%:** there is no measurement.

**Natural errors.** Prevented errors are estimated as flagged claims × reviewer-confirmed precision.

| Extractor | Flagged | Flag precision (reviewer 1; reviewer 2) | Estimated true errors removed | Valid claims removed | $ per prevented error, judge only, peak |
|---|---|---|---|---|---|
| Mem0 + DeepSeek (97) | 20 | 1/8; 0/8 | 2.5 (0.4–9.4); 0 (0–6.5) | 17.5–20 | $0.048 (best case $0.013); unbounded if 0/8 holds |
| Mem0 + Llama-3.1-8B (100) | 68 | 13/27; 9/23 | 32.7 (20.9–44.9); 26.6 (15.1–40.3) | 35–41 | $0.0036–0.0045; guard + repair $0.0067–0.0083 |

**Natural vs injected.** Mapping the Llama run's 5.4% flag rate onto the injected WA slope predicts +3.4 pts. The measured gain is +2.3 [1.3, 3.3]. Natural flags therefore buy about two-thirds of what injected faults do, consistent with reviewers confirming only 39–48% of them. The mapping itself is an assumption.

### A4.8 Table R3-6. Downstream QA

Sources:

- `data/results/rq3-tradeoff-v1/longmemeval-n200-paired.json`;
- `data/longmemeval-runs/n200/report.json`;
- the live-run files.

| Benchmark | Questions | Unguarded | Session hold | Claim drop | Guard + repair |
|---|---|---|---|---|---|
| LongMemEval oracle (seed 20261122, round-robin by type) | 200 | 0.650 | 0.585: **−6.5 pts [−10.0, −3.5]**, 13 vs 0, p = 0.0002 | not a condition | 0.640: −1.0 [−2.5, 0.0], 2 vs 0, p = 0.5 |
| Same, 170 questions excluding the 30-question pilot | 170 | – | −6.5 [−10.6, −2.9], 11 vs 0, p = 0.001 | – | −1.2 [−2.9, 0.0], 2 vs 0, p = 0.5 |
| Live HaluMem, OSS Mem0 vs Mem0 + MemBud | 98 | 0.939 | not run | 0.939, 4 vs 4 discordant | not run |
| MemoryAgentBench 6k single-hop / multi-hop (LLM conflict guard) | 100 + 100 | 1.00 / 0.13 | 0.26 / 0.00 | – | 0.52 / 0.09 |

- **What the judge did on LongMemEval n = 200:** it flagged 83 of 3,286 claims (2.5%) and held 23 of 324 sessions (7.1%). Repair recovered all 23.
- **Session hold** lost answers in all six question types (−1 to −3 each) and gained none.
- **Guard + repair** lost 2 single-session-assistant questions.

**Power** (McNemar, α = 0.05, 80% power):

| Discordance | n for 2 / 3 / 5 pts | Detectable at n = 200 / 500 |
|---|---|---|
| 8.2% (live HaluMem, mostly noise) | 1,600 / 710 / 254 | 5.6 / 3.6 pts |
| 6.7% (LongMemEval pilot, session hold) | 1,306 / 580 / 207 | 5.1 / 3.2 pts |
| 3% (assumed for guard + repair) | 587 / 260 / 92 | 3.4 / 2.2 pts |

LongMemEval oracle has only 500 questions, so a 2-point QA effect cannot be resolved on it alone.

### A4.9 Claims the RQ3 data supports

- Deterministic verification is small next to the adapter's state copy. Graph vs text is not detectable end to end inside the Mem0 adapters (N = 0–2,000).
- Graph construction is linear and costs about as much as text serialization (8.2 vs 7.2 ms at 2,000 records). The standalone graph verifier is quadratic because of a fixable implementation detail.
- The adapter mechanism, not verification, sets the deterministic overhead. StagedMem0 takes about 1.1 s per write at 2,000 records. BlackBoxStaged is about 5× slower.
- The LLM claim judge sets the guard's real cost. Repair adds about 3.8 s and $0.003–0.004 per held write. Evaluation-only scoring costs about 20× the deployed guard per session.
- The peak cache-miss estimate rule over-estimates DeepSeek billing 2.87×. Billed spend for the window was $4.21.
- Holding whole sessions costs QA accuracy (−6.5 pts on LongMemEval, replicated on 170 fresh questions) and recall (−2.2 to −16.2 pts).
- Guard + repair and claim drop do not measurably change QA. On injected faults their net extraction benefit grows with the error rate. For guard + repair it is detectable from the 4.1% dose.

### A4.10 Claims the RQ3 data does not support

- Any graph advantage in cost or decisions.
- "The guard improves downstream QA."
- "Verification is free." The deployed LLM judge adds about 30–130% to the host's model cost per write.
- Any billed per-condition or per-write cost. Only the aggregate bill exists.
- A general benefit, or a break-even above 7.1%.
- The live-run latency difference (4.78 s vs 4.98 s per session) as guard overhead. The MemBud arm's Mem0 calls were largely served from the response cache.

## A5. Cross-RQ summary for the paper

| RQ | Supported claim | Key evidence | Main qualifier |
|---|---|---|---|
| RQ1b | No graph advantage under equal information | 0 discordant decisions on 6,093 cases | Deterministic verifier; the LLM verifier differed only before prompt selection |
| RQ1a | Guard removes injected faults; gain scales with dose | WA +0.7 → +4.7 pts at 1.1 → 7.1% | Circular labels |
| RQ1a | Strong extractor: no measurable effect | Offline 97 sessions, live 50 sessions | Live run not repeated |
| RQ1a | Weak extractor: +2.3 WA pts, no recall cost | 100 sessions, CI 1.3–3.3 | Not validated: 39–48% reviewer precision, same-family judges |
| RQ2 | Conditional modularity | 3 real backends pass one contract suite; 5 systems fail for recorded reasons | Black-box adapter loses unobservable state (Graphiti provenance) |
| RQ3 | Cost is set by the LLM judge and the adapter, not the graph | Benchmarks and token attribution | One machine; dollars are estimates, except the $4.21 total |
| RQ3 | Session hold harms QA and recall | LongMemEval −6.5 pts; recall −2.2 to −16.2 | – |

## A6. Reproducibility

| Purpose | Script | Output |
|---|---|---|
| Official HaluMem scoring | `scripts/score_halumem_official.py` | `data/halumem-runs/*/official/` (gitignored) |
| Claim judging, repair | `scripts/run_halumem_claim_judge.py`, `scripts/run_halumem_repair.py` | `llm-claim-verdicts-*.jsonl`, `repair/` |
| Dose-response | `scripts/run_halumem_inject.py`, `scripts/halumem_dose_response.py` | `data/halumem-runs/dose-response.json` |
| Reviewer quizzes | `scripts/halumem_dual_review.py` | `claim-audit-*/dual-agreement.json` |
| Live run | `scripts/run_halumem_live.py`, `scripts/run_halumem_official_eval.py` | `data/halumem-live/` (gitignored) |
| MemoryAgentBench, LongMemEval | `scripts/run_memagentbench.py`, `scripts/run_longmemeval.py` | `data/memagentbench-runs/`, `data/longmemeval-runs/` |
| Adapter contract | `scripts/run_adapter_contract_evidence.py` | `data/results/adapter-contract-v1/` |
| Real-model adapter runs | `scripts/run_{blackbox,langmem,graphiti}_realmodel.py` | `data/results/*-blackbox-realmodel-v1/` |
| RQ3 overhead, tokens, billing, trade-off | `scripts/bench_rq3_overhead.py`, `scripts/rq3_token_attribution.py`, `scripts/rq3_billing_reconcile.py`, `scripts/rq3_tradeoff.py` | `data/results/rq3-*-v1/` |

Seeds: sample 20261121; RQ1 bootstrap 20261122; injection and weak quiz 20261123; RQ3 benchmark and bootstrap 20261124.

The run directories hold dataset text (HaluMem is CC BY-NC-ND) and are gitignored. The LongMemEval n = 200 run directory is `data/longmemeval-runs/n200/` (moved from the former `MemBud-rq3` worktree).

---

# Part B: Bahasa Indonesia

## B0. Cara angka-angka diperiksa

Setiap angka di bawah ini **dihitung ulang dari artefak hasil eksekusi**, bukan disalin dari draf. Untuk setiap tabel, artefak sumbernya disebutkan pada keterangan tabel.

- **Dihitung ulang dari berkas mentah:**
  - metrik resmi HaluMem: `official-metrics.json`, `judge-results.jsonl`;
  - verdict *claim judge*: `llm-claim-verdicts-*.jsonl`;
  - berkas jawaban reviewer dan kunci kuis;
  - *checkpoint* Fase 1, dengan keputusan graph vs `text_fair` per kasus;
  - log *repair*;
  - hasil *live run* (`memzero_eval_stat_result.json`, `guard-log.jsonl`);
  - laporan LongMemEval dan MemoryAgentBench;
  - bukti kontrak adapter;
  - bukti model nyata, termasuk versi sebelumnya di riwayat git;
  - berkas JSON RQ3.
- **Dijalankan ulang:** `scripts/halumem_dose_response.py`, secara luring dari skor yang tersimpan di cache tanpa panggilan API. Dengan 1.000 *resample*, hasilnya identik dengan `dose-response.json` yang sudah di-*commit*. Dengan 2.000 *resample*, interval kepercayaan berbeda di desimal ke-4.
- **Tidak dijalankan ulang:** panggilan model berbayar (tanpa biaya baru), *test suite*, dan uji Neo4j/Graphiti.

Label pada tabel:

- **[M]** terukur dari artefak.
- **[E]** estimasi, misalnya dolar dari token terukur dengan harga daftar.
- **[U]** tidak tersedia.
- **pts** berarti poin persentase. **WA** berarti *weighted accuracy* HaluMem. **CI** berarti interval kepercayaan 95%.

Catatan gaya: angka memakai titik desimal seperti pada Bagian A. Ubah ke koma desimal bila gaya jurnal berbahasa Indonesia memintanya.

Istilah:

- *guard*: lapisan verifikasi MemBud.
- *claim judge*: hakim klaim berbasis LLM.
- *unguarded*: tanpa *guard*; semua klaim di-*commit*.
- *session hold*: menahan seluruh sesi jika ada satu klaim yang ditandai.
- *claim drop*: hanya membuang klaim yang ditandai.
- *guard + repair*: *guard* dengan *loop* perbaikan.
- *extractor*: model yang mengekstrak memori dari percakapan.

## B1. Temuan pemeriksaan fakta

### B1.1 Kesalahan yang dikoreksi dalam berkas ini

| # | Lokasi draf | Isi draf | Isi artefak | Tindakan |
|---|---|---|---|---|
| 1 | RQ1 Tabel 6, catatan *claim judge* vs *official judge* | Hakim resmi memberi skor < 2 pada 72% klaim yang ditandai (*extractor* lemah), "dibanding 21% klaim yang tidak ditandai" | Angka 21% adalah 83/395, yang hanya menghitung klaim tak-ditandai di dalam 31 sesi yang memiliki tanda. Pembanding *extractor* kuat (5,4%) menghitung semua sesi. Atas seluruh 100 sesi, angkanya **180/1.196 = 15,1%**. Draf membandingkan penyebut yang berbeda. | Dikoreksi menjadi 72% vs 15,1% (semua sesi) |
| 2 | Teks RQ1 Tabel 9 dan bagian Sumber | *Live run* "ditagih sekitar $0,56"; "tagihan sekitar seperlima estimasi" | Tidak ada catatan ter-*commit* untuk $0,56. Ekspor tagihan berganularitas harian, dan hari itu juga berisi eksekusi lain. Selama jendela ekspor, tagihan = **1/2,87** dari aturan harga puncak, bukan 1/5. | Keduanya dihapus; hanya total tagihan $4,21 yang dipakai |
| 3 | RQ3 Tabel 1 | Verifikasi deterministik di jalur adapter "≤ 24 ms pada N = 2.000" | Hanya benar untuk teks (≈24 ms). Pada N = 2.000, verifikasi graph memakan **174–189 ms pada 3 dari 4 sel graph**, diduga jeda *garbage collector*, dan 35 ms pada sel keempat. | Dikoreksi |
| 4 | RQ3 Tabel 2a | *Fair text* vs teks biasa "terdeteksi hanya pada 8 dari 35 sel" | **10 dari 35** sel; rasio 0,99–1,05 | Dikoreksi |
| 5 | RQ3 Tabel 2a | Pembangunan ulang *edge set* = "70–96%" waktu verifikasi graph pada N ≥ 100 | Estimasi per sel berkisar 0,70–1,44; satu sel melebihi 100%, jadi ini hanya estimasi. Pada k = 4 rentangnya 0,79–1,00. | Diubah menjadi "sekitar 70% hingga hampir seluruhnya" |
| 6 | RQ3 Tabel 2b | HOLD "9–34% lebih cepat" dari ACCEPT | **5–34%** | Dikoreksi |
| 7 | RQ3 Tabel 2b | StagedMem0 ≈ N^1,12, BlackBoxStaged ≈ N^1,18 | Eksponen ini hanya berlaku untuk *fit* N = 500–2.000. Pada 100–2.000 nilainya 0,95 dan 1,06. | Rentang *fit* dicantumkan |
| 8 | RQ3 Tabel 1 dan 3 | *Claim judge* Medium = satu panggilan per penulisan, $0,00123 | Sebagaimana dijalankan, *guard* Mem0+DeepSeek memakai **dua tahap**: *prompt* ketat pada 97 sesi (2.845 token masuk / 316 keluar), lalu *prompt accepted-assistant-facts* pada 30 sesi yang ditandai tahap pertama (4.809 / 345). Biaya aktual **$0,00181 per penulisan** pada harga puncak. $0,00123 adalah estimasi satu tahap, konsisten dengan eksekusi Llama yang satu tahap ($0,00119). | Kedua nilai dilaporkan dengan label |
| 9 | RQ1 Tabel 2 | particle-1, *prompt* netral: 2.740 klaim | Artefak berisi 2.067 proposal dan 2.772 baris verdict (2.308 unik), jadi penyebutnya tidak dapat direproduksi. Semua baris berisi 0 tanda. | Jumlah klaim dihapus, "0 tanda" dipertahankan |
| 10 | RQ1 Tabel 2 | *Extractor* lemah: 1.259 klaim | Mem0 menghasilkan 1.265 klaim ADD. 6 klaim di 3 sesi tidak mendapat verdict dan dipertahankan oleh *claim drop*. Metrik resmi menilai seluruh 1.265 klaim. | Catatan kaki ditambahkan |
| 11 | RQ2 §7 | Eksekusi model nyata pertama Mem0 memakai 13.986 token | Tidak ada artefak yang memuat angka ini. Bukti ter-*commit* menunjukkan 0 karena dilayani dari cache. Bukti eksekusi leksikal di git (`bb5b142`, `fc1bcdc`) mencatat 8.449 (Mem0) dan 5.300 (LangMem). | 13.986 dihapus |
| 12 | RQ2 §3.1 | 27 ms vs 17 ms untuk *add* yang diterima pada 2 memori | Pengukuran tunggal lama, digantikan *benchmark* RQ3 (N = 0: 27,1 vs 17,1 ms, 11 ulangan, IQR) | Angka RQ3 dipakai |
| 13 | RQ2 §10 | Jumlah tes (806 lulus, …) | Tidak dijalankan ulang; angka dari saat *branch* dibuat | Dikeluarkan dari bahan makalah |

### B1.2 Terverifikasi tepat

Semua angka lain di draf RQ1, RQ2 dan RQ3 cocok persis dengan artefak pada presisi yang dilaporkan. Termasuk di dalamnya:

- **RQ1:**
  - semua metrik resmi dan CI berpasangan;
  - pembagian pengguna;
  - *dose-response*;
  - presisi reviewer dan kappa;
  - kesesuaian hakim resmi vs reviewer;
  - tumpang tindih *rubric judge*;
  - metrik *live run*;
  - LongMemEval dan MemoryAgentBench;
  - deteksi Fase 1;
  - 0 keputusan berbeda antara graph dan `text_fair` pada 6.093 kasus;
  - pemulihan *repair*.
- **RQ2:**
  - hasil *contract suite*;
  - *blind spot* per *backend*;
  - keputusan model nyata;
  - asersi uji fidelitas Graphiti.
- **RQ3:**
  - median dan IQR *benchmark*;
  - token, latensi dan dolar per komponen;
  - total tagihan;
  - setiap sel *trade-off* dan *fit* linear;
  - LongMemEval n = 200.

Dua fakta ditambahkan di sini yang belum ada di draf:

- Tanpa *transcript grounding*, deteksi Fase 1 adalah 956/1.111 (Medium) dan 2.499/3.168 (Long).
- Pada *extractor* lemah, *repair* menghapus 74 klaim dan menulis 14 klaim baru atau yang ditulis ulang.

### B1.3 Data lemah (nyata, tetapi wajib diberi kualifikasi saat dikutip)

1. **Tanda dari *claim judge* hanya sebagian dikonfirmasi manusia.**
   - *Extractor* kuat: 1/8 dan 0/8 tanda dikonfirmasi. *Extractor* lemah: 13/27 dan 9/23.
   - Sampelnya kecil: 8 dan 28 klaim yang ditandai.
2. **Hakim dari keluarga model yang sama.** *Claim judge*, hakim akurasi resmi HaluMem dan hakim jawaban LongMemEval semuanya DeepSeek. *Benchmark* aslinya memakai GPT-4o, yang tidak lagi tersedia melalui API OpenAI.
3. **Label kesalahan injeksi bersifat sirkular.** Injektor membuat kesalahan sekaligus labelnya, jadi eksekusi ini hanya menunjukkan sensitivitas mekanisme, bukan prevalensi alami.
4. ***Live run* hanya satu kali.** Tidak ada ulangan, jadi *noise* antar-eksekusi tidak terukur. *Guard* hanya bertindak pada 3 dari 50 sesi.
5. **Peningkatan pada *extractor* lemah mengecil pada pengguna final:** +0,9 pts (final) vs +2,8 pts (*tuning*).
6. **Eksekusi Graphiti dengan model nyata hampir hampa.** 3 dari 4 ACCEPT adalah ekstraksi kosong, dan kontradiksi Berlin→Lisbon tidak teruji.
7. **MemoryAgentBench memakai *guard* yang berbeda,** yaitu verifikator konflik LLM, bukan *claim judge* atau verifikator struktural. Deteksinya tidak divalidasi reviewer.
8. **Simulator QA (+9,0 pts EM) memakai label turunan spesifikasi.** Ini bukan bukti tentang dialog nyata.
9. **Pilot LongMemEval 30 pertanyaan sudah digantikan** oleh eksekusi 200 pertanyaan. Kutip angka n = 200.
10. **Pengukuran waktu berasal dari satu mesin** (i5-12450HX, *governor* `powersave`) dengan tiruan model luring. Eksponen dan rasio dapat ditransfer, milidetik absolutnya tidak.
11. **Set evaluasi deterministik dilabeli satu reviewer** (sintetis v2: 36 kasus; percakapan: 28 langkah).

## B2. RQ1: Keandalan

> (a) Apakah MemBud mengurangi omisi, korupsi catatan lama yang valid, dan penambahan yang tidak didukung dibanding *pipeline* tanpa *guard*? (b) Apakah verifikasi berbasis graph menangkap lebih banyak kesalahan daripada verifikator non-graph dengan kriteria dan anggaran yang sama?

### B2.1 Jawaban

**(b) Tidak.** Graph dan verifikator teks yang adil (`text_fair`, yang melihat anotasi kontradiksi yang sama) membuat keputusan identik pada seluruh 6.093 kasus yang diuji (0 berbeda). Tidak ada keunggulan graph ketika informasinya setara.

**(a) Bersyarat, belum tervalidasi.**

- ***Extractor* kuat.** Pada Mem0 dengan model kuat, kesalahan alami jarang: 0,6–1,6% klaim ditandai. *Guard* tidak berpengaruh terukur pada metrik resmi HaluMem, baik luring (97 sesi) maupun *live* (10 pengguna).
- **Ketika kesalahan ada.** Membuang hanya klaim yang ditandai menaikkan *weighted accuracy* resmi:
  - +0,7 hingga +4,7 pts pada kesalahan injeksi, naik seiring dosis;
  - +2,3 pts (CI 1,3–3,3) pada *extractor* yang secara alami lebih lemah (Mem0 + Llama-3.1-8B, 100 sesi), tanpa penurunan *recall* atau F1 yang signifikan.
- **Mengapa belum tervalidasi.** Reviewer hanya mengonfirmasi 39–48% tanda pada *extractor* lemah, dan hakim metriknya berasal dari keluarga model yang sama dengan *claim judge*.
- **Menahan per sesi bukan titik operasi yang berguna.** *Recall* selalu turun.
- **Rumusan untuk makalah.** Jangan menulis "MemBud mengurangi memori halusinasi" tanpa kualifikasi ini.

### B2.2 Pengaturan

| Butir | Nilai |
|---|---|
| *Benchmark* utama | HaluMem-Medium (rev. `cb04336`), 20 pengguna × maksimal 5 sesi pertama, *seed* 20261121. Metrik ekstraksi resmi diimplementasi ulang dari MemTensor/HaluMem `eval/evaluation.py` @ `d653749` dan diuji diferensial terhadap kode referensi |
| *Extractor* | Mem0 2.2.1 + DeepSeek `deepseek-flash` (97 sesi, 1.286 klaim); Mem0 2.2.1 + `meta-llama/llama-3.1-8b-instruct` (100 sesi, 1.265 klaim, pengguna yang sama) |
| *Claim judge* | DeepSeek, *prompt batch*, kebijakan *accepted-assistant-facts* |
| Hakim metrik resmi | DeepSeek dengan *prompt* resmi HaluMem (deviasi: *benchmark* memakai GPT-4o) |
| Kondisi | ***Unguarded***; ***Session hold***; ***Claim drop***; ***Guard + repair*** (maks. 2 percobaan ulang, lalu verifikasi ulang) |
| Statistik | *Bootstrap* berpasangan per sesi, 2.000 *resample* (*dose-response*: 1.000), *seed* 20261122; interval Wilson 95%; McNemar eksak |
| Pembagian pengguna | *Hash* ber-*seed* menjadi separuh *tuning* dan separuh final; deskriptif saja, tidak ada yang di-*tuning* |
| Rubrik reviewer | `docs/halumem-grounding-rubric.md`, v1 dibekukan (SHA-256 `9830c317…`) |

### B2.3 Tabel R1-1. Graph vs verifikator teks adil (RQ1b)

Sumber: *checkpoint* Fase 1, `phase2-deepseek/report.json`, `docs/halumem-evaluation.md`.

| Data | Kasus | Keputusan berbeda (graph vs `text_fair`) | McNemar p |
|---|---|---|---|
| Fixture sintetis berbentuk HaluMem | 66 | 0 | 1,0 |
| HaluMem-Medium Fase 1 (data nyata, kesalahan injeksi) | 1.422 | 0 | 1,0 |
| HaluMem-Long Fase 1 | 4.508 | 0 | 1,0 |
| HaluMem-Medium Fase 2 (proposal alami Mem0) | 97 | 0 | 1,0 |
| **Total** | **6.093** | **0** | |

- Graph dan `text_fair` sama pada kedua mode *grounding* (dengan dan tanpa *transcript grounding*).
- `text` biasa, yang tidak melihat `evidence.contradicts`, hanya kalah pada korupsi yang ditandai lewat bidang itu saja. `text` menerima 527 kasus vs 310 pada Medium.
- **Verifikator transisi berbasis LLM berbeda** (13 kasus *held-out* sintetis, DeepSeek). Dengan *prompt* v1, graph menahan 4 dari 7 kasus valid dan teks 1 dari 7. Dengan *prompt* v2 ketiga kondisi menerima 7/7. *Prompt* v2 dipilih pada *split held-out* itu, jadi ini bukan uji independen.

### B2.4 Tabel R1-2. Tingkat penandaan *claim judge* (kesalahan alami)

Sumber: `llm-claim-verdicts-*.jsonl` per eksekusi.

| *Extractor* / data | Klaim dinilai | Ditandai | Tingkat (Wilson 95%) | Sesi bertanda |
|---|---|---|---|---|
| Mem0 + DeepSeek, HaluMem-Medium | 1.286 | 20 | 1,6% (1,0–2,4) | 14 / 97 |
| Mem0 + DeepSeek, HaluMem-Long (10 pengguna × 3 sesi) | 443 | 5 | 1,1% (0,5–2,6) | 2 / 30 |
| DeepSeek, *prompt* yang mendorong inferensi berlebih (bukan Mem0) | 1.722 | 20 | 1,2% | 15 / 97 |
| particle-1 (gratis), *prompt* netral (bukan Mem0) | – (lihat B1.1 #9) | 0 | 0% | 0 / 97 |
| **Mem0 + Llama-3.1-8B, HaluMem-Medium** | 1.259ᵃ | 68 | **5,4% (4,3–6,8)** | 31 / 100 |
| Sama, *prompt judge* selaras rubrik | 1.259ᵃ | 93 | 7,4% | 38 / 100 |
| *Live run*, Mem0 + DeepSeek (10 pengguna × 5 sesi) | 643 | 4 | 0,6% | 3 / 50 |

ᵃ Mem0 menghasilkan 1.265 klaim. 6 klaim di 3 sesi tidak mendapat verdict dan dipertahankan oleh *claim drop*.

Mem0 dengan model kuat menghasilkan sedikit klaim yang ditandai. Sesi yang lebih panjang maupun *prompt* yang berbeda tidak menaikkan tingkat ini. Hanya model yang lebih lemah di dalam Mem0 yang menaikkannya.

### B2.5 Tabel R1-3. Kesalahan injeksi (uji mekanisme)

**Fase 1** (verifikator struktural deterministik, data HaluMem nyata, kesalahan sintetis). Sumber: *checkpoint* Fase 1.

| Set | Kasus bermasalah terdeteksi, dengan *transcript grounding* | Tanpa *transcript grounding* | Transisi valid yang salah ditahan |
|---|---|---|---|
| Medium | 1.111 / 1.111 (100%) | 956 / 1.111 (86,0%) | 1 / 311 |
| Long | 3.168 / 3.168 (100%) | 2.499 / 3.168 (78,9%) | 1 / 1.340 |

*Pipeline* tanpa *guard* mendeteksi 0%.

**Metrik resmi, *dose-response*** (*claim judge* + hakim resmi, 97 sesi). Sumber: `dose-response.json`; direproduksi dengan 1.000 *resample*.

Kesalahan disarangkan dengan *hash* ber-*seed*, dua per sesi yang diinjeksi: satu klaim dikorupsi dan satu klaim dikarang. Nilai adalah selisih berpasangan terhadap *unguarded* (satuan pecahan, CI).

| Tingkat klaim injeksi | Sesi diinjeksi | *Guard + repair*: ΔWA | *Guard + repair*: Δ*recall* | *Claim drop*: ΔWA | *Claim drop*: Δ*recall* |
|---|---|---|---|---|---|
| 0 (alami) | 0 | +0,0019 (−0,0003; 0,0058) | −0,0011 (−0,0041; 0,0018) | +0,0019 (−0,0003; 0,0058) | −0,0011 (−0,0041; 0,0018) |
| 1,1% | 7 | +0,0072 (0,0027; 0,0126) | −0,0021 (−0,0052; 0,0006) | +0,0071 (0,0027; 0,0125) | −0,0059 (−0,0117; −0,0014) |
| 4,1% | 27 | +0,0279 (0,0189; 0,0371) | −0,0081 (−0,0160; −0,0017) | +0,0267 (0,0180; 0,0354) | −0,0193 (−0,0309; −0,0098) |
| 7,1% | 47 | +0,0469 (0,0369; 0,0576) | −0,0075 (−0,0165; −0,0005) | +0,0450 (0,0359; 0,0551) | −0,0274 (−0,0418; −0,0153) |

- **Yang ditangkap *judge*:** 47/47 klaim karangan dan 31/47 klaim terkorupsi.
- **Kemiringan:** kenaikan WA ≈ +0,62 (*claim drop*) dan +0,65 (*repair*) pts per pts tingkat injeksi.
- **Label sirkular:** ini hanya sensitivitas mekanisme.
- **Tidak dijalankan:** dosis 14,5%.

### B2.6 Tabel R1-4. Metrik ekstraksi resmi HaluMem per kondisi (kesalahan alami)

Sumber: `official-metrics.json` dari `phase2-deepseek` dan `phase3-weakmem0`.

| *Extractor* | Kondisi | WA | *Recall* | *Weighted recall* | *Target precision* | F1 | Sesi di-*commit* |
|---|---|---|---|---|---|---|---|
| Mem0 + DeepSeek (97) | *Unguarded* | 0,971 | 0,941 | 0,968 | 0,985 | 0,963 | 97 |
| | *Session hold* | 0,973 | 0,919 | 0,938 | 0,985 | 0,951 | 83 |
| | *Claim drop* | 0,973 | 0,940 | 0,966 | 0,985 | 0,962 | 97 |
| | *Guard + repair* | 0,973 | 0,940 | 0,966 | 0,985 | 0,962 | 97 |
| Mem0 + Llama-3.1-8B (100) | *Unguarded* | 0,868 | 0,693 | 0,790 | 0,983 | 0,813 | 100 |
| | *Session hold* | 0,913 | 0,531 | 0,598 | 0,984 | 0,690 | 69 |
| | *Claim drop* | 0,891 | 0,691 | 0,784 | 0,985 | 0,812 | 100 |
| | *Guard + repair* | 0,888 | 0,688 | 0,782 | 0,985 | 0,810 | 100 |

FMR tidak dilaporkan. Menurut aturan referensi, menghapus klaim membuat lebih banyak titik interferensi dihitung "tertahan", sehingga kenaikannya adalah artefak penghapusan.

### B2.7 Tabel R1-5. Selisih berpasangan terhadap *unguarded* (CI)

| *Extractor* | Kondisi | ΔWA | Δ*Recall* | ΔF1 |
|---|---|---|---|---|
| Mem0 + DeepSeek (97) | *Session hold* | +0,0018 (−0,0033; 0,0077) | **−0,0220 (−0,0392; −0,0095)** | **−0,0118 (−0,0215; −0,0051)** |
| | *Claim drop* | +0,0019 (−0,0003; 0,0056) | −0,0011 (−0,0042; 0,0020) | −0,0007 (−0,0023; 0,0009) |
| | *Guard + repair* | +0,0019 (−0,0003; 0,0056) | −0,0011 (−0,0042; 0,0020) | −0,0007 (−0,0023; 0,0009) |
| Mem0 + Llama-3.1-8B (100) | *Session hold* | +0,0447 (0,0231; 0,0678) | **−0,1620 (−0,2493; −0,0808)** | **−0,1231 (−0,2031; −0,0578)** |
| | *Claim drop* | **+0,0226 (0,0133; 0,0332)** | −0,0026 (−0,0125; 0,0084) | −0,0011 (−0,0080; 0,0064) |
| | *Guard + repair* | **+0,0204 (0,0096; 0,0339)** | −0,0057 (−0,0106; −0,0011) | −0,0034 (−0,0069; −0,0004) |

**Pembagian pengguna, *extractor* lemah** (deskriptif):

| Pengguna | *Claim drop*: ΔWA | *Claim drop*: Δ*recall* | *Guard + repair*: ΔWA | *Guard + repair*: Δ*recall* |
|---|---|---|---|---|
| *Tuning* (14 pengguna, 70 sesi) | +0,028 (0,016; 0,042) | −0,003 (−0,017; 0,013) | +0,026 (0,011; 0,045) | −0,008 (−0,015; −0,002) |
| Final (6 pengguna, 30 sesi) | +0,009 (0,002; 0,018) | −0,002 (−0,006; 0,000) | +0,007 (0,001; 0,015) | 0,000 (−0,006; 0,004) |

**Pembagian pengguna, *extractor* kuat, *claim drop*:** *tuning* +0,000 (−0,001; 0,001); final +0,006 (−0,000; 0,019).

**Mengapa *session hold* menurunkan *recall*.**

- Pada *extractor* kuat, penahanan sesi membuang 230 klaim, dan hanya 20 di antaranya (8,7%) yang ditandai.
- Akibatnya 41 titik emas yang ter-*recall* hilang (2,2% dari 1.862). *Claim drop* hanya kehilangan 2.
- Pada *extractor* lemah, kenaikan WA yang lebih besar dari *session hold* berasal dari pembuangan 31 sesi utuh.

**Apa yang dilakukan *repair*.** *Repair* hampir sama dengan penghapusan:

- *extractor* kuat: 21 klaim dihapus, 1 ditulis ulang;
- *extractor* lemah: 74 dihapus, 14 baru atau ditulis ulang.

### B2.8 Tabel R1-6. Validitas *judge*: dua reviewer buta, rubrik dibekukan

Sumber: `dual-agreement.json` dan berkas jawaban reviewer.

| Sampel (masing-masing 36 klaim) | Ditandai *judge* | Presisi tanda, reviewer 1 | Presisi tanda, reviewer 2 | Tingkat luput (klaim tak-ditandai yang dinilai tidak didukung) | Kesepakatan antar-reviewer |
|---|---|---|---|---|---|
| Mem0 + DeepSeek | 8 | 1/8 = 12,5% (2–47) | 0/8 = 0% (0–32) | 0/28 dan 1/28 | 34/36, κ = −0,03 |
| Mem0 + Llama-3.1-8B | 28 | 13/27 = 48% (31–66) | 9/23 = 39% (22–59) | 1/8 dan 1/8 | 30/30 yang tegas, κ = 1,0 (6 ragu/kosong) |
| *Claim judge* kedua (particle-1), Mem0 + DeepSeek | 6 dalam sampel | 0/6 | 0/6 | – | – |

**Apakah hakim akurasi resmi sepakat dengan reviewer?** Kuis *extractor* lemah, 36 klaim.

| Pemetaan skor resmi (0/1/2) | Reviewer 1 | Reviewer 2 |
|---|---|---|
| "Tidak didukung" = skor < 2 | 20/35 = 57% (41–72) | 16/31 = 52% (35–68) |
| "Tidak didukung" = hanya skor 0 | 27/35 = 77% (61–88) | 24/31 = 77% (60–89) |
| Klaim yang dinilai reviewer DIDUKUNG tetapi diberi skor 1 ("sebagian akurat") | 8 dari 21 | 8 dari 21 |
| Klaim yang ditandai *judge*, dinilai reviewer DIDUKUNG, tetapi berskor < 2 | 9 dari 14 | 9 dari 14 |

***Claim judge* vs hakim akurasi resmi, semua klaim (dikoreksi, lihat B1.1 #1).**

| *Extractor* | Klaim ditandai berskor < 2 | Klaim tak-ditandai berskor < 2 (semua sesi) |
|---|---|---|
| Mem0 + Llama-3.1-8B | 49/68 = 72% (60–81) | 180/1.196 = 15,1% |
| Mem0 + DeepSeek | 4/20 = 20% (8–42) | 68/1.266 = 5,4% |

Interpretasi:

- Hakim resmi sering memberi nilai sebagian (skor 1) pada klaim yang diterima reviewer.
- Menghapus klaim seperti itu menaikkan WA, karena skor 1 dihitung setengah.
- Jadi sebagian kenaikan pada *extractor* lemah berasal dari penghapusan klaim yang diterima manusia. Ini kenaikan pada metrik yang dinilai model, bukan pengurangan halusinasi yang dikonfirmasi manusia.
- Kedua hakim adalah DeepSeek, jadi kesepakatan keduanya bukan validasi independen.

### B2.9 Tabel R1-7. *Prompt judge* selaras rubrik yang dipra-registrasi (hasil negatif)

Sumber: `llm-claim-verdicts-rubric.jsonl`.

*Prompt* di-*commit* pada `6adcf12` pukul 17:20:35. Berkas verdict bercap waktu 17:24:20, setelah *commit*, sehingga konsisten dengan pra-registrasi.

| | *Judge* asli | *Judge* selaras rubrik |
|---|---|---|
| Klaim ditandai (dari 1.259) | 68 | 93 |
| Ditandai keduanya | 67 | 67 |
| Hanya ditandai *judge* ini | 1 | 26 |
| Pada 36 klaim berlabel reviewer | 13/27 dan 9/23 terkonfirmasi | tanda identik, jadi presisi identik |

*Prompt* baru tidak mengurangi tanda yang keliru. Ke-26 tanda tambahan belum dilabeli reviewer.

### B2.10 Tabel R1-8. *Live run*: Mem0 OSS vs Mem0 + MemBud (prosedur resmi)

Sumber: `data/halumem-live/results/memzero-deepseek-{pure,membud-drop}-s5/`.

Pengaturan:

- `scripts/run_halumem_live.py`, mengikuti prosedur per pengguna `eval_memzero.py` resmi pada Mem0 2.2.1 *open source*;
- dinilai oleh `evaluation.py` resmi tanpa perubahan;
- 10 pengguna × 5 sesi: 50 sesi, 57 titik pembaruan, 98 pertanyaan;
- DeepSeek sebagai *extractor*, model penjawab dan hakim;
- lengan MemBud: `StagedMem0`, *grounding* dengan *claim judge*, *claim drop*.

| Metrik resmi | Mem0 murni | Mem0 + MemBud (*claim drop*) |
|---|---|---|
| *Recall* / *weighted recall* | 0,961 / 0,978 | 0,958 / 0,977 |
| *Target precision* / WA | 0,980 / 0,940 | 0,982 / 0,941 |
| F1 ekstraksi | 0,970 | 0,970 |
| Pembaruan benar / halusinasi / omisi | 0,614 / 0,070 / 0,316 | 0,596 / 0,035 / 0,351 |
| QA benar / halusinasi / omisi | 0,939 / 0,041 / 0,010 | 0,939 / 0,020 / 0,041 |

- **Yang dilakukan *guard*:** menandai 4 dari 643 klaim (3 dari 50 sesi).
- **QA:** 4 vs 4 pertanyaan berbeda.
- **Hasil: tidak ada efek terukur.** Selisih 1–4 butir berada dalam *noise* antar-eksekusi yang tidak terukur. Hanya ada satu eksekusi per lengan.
- **Tidak dijalankan:**
  - *live run* dengan *extractor* lemah (model gratis yang dicoba gagal pada sesi panjang; anggaran habis);
  - lengan *hold*;
  - ulangan.
- ***Grounding* bawaan MemBud tidak dapat dipakai pada HaluMem.** `LexicalGrounding` bawaan menahan **97/97 dan 100/100** sesi pada eksekusi yang tercatat. Jadi semua angka *guard* di atas memakai *claim judge* LLM sebagai kebijakan *grounding*.

### B2.11 Tabel R1-9. Pemeriksaan *downstream* lain

Sumber: `data/memagentbench-runs/conflict/6k/report.json`. Untuk QA LongMemEval, lihat RQ3 Tabel R3-6 (n = 200, menggantikan pilot 30 pertanyaan).

| *Benchmark* | *Unguarded* | *Guard (hold)* | *Guard + repair* | Uji |
|---|---|---|---|---|
| MemoryAgentBench FactConsolidation 6k, *single-hop* (100 pertanyaan) | 1,00 (0,96–1,00) | 0,26 (0,18–0,35) | 0,52 (0,42–0,62) | *Unguarded* unggul atas *guard* 74 vs 0 dan *repair* 48 vs 0, p < 0,001 |
| Sama, *multi-hop* (100 pertanyaan) | 0,13 (0,08–0,21) | 0,00 (0,00–0,04) | 0,09 (0,05–0,16) | vs *guard* p = 0,0002; vs *repair* 10 vs 6, p = 0,45 |

- *Guard* di sini adalah verifikator konflik LLM, bukan *claim judge*. Deteksinya tidak divalidasi reviewer.
- MemoryAgentBench adalah tugas "fakta terbaru menang", jadi menahan fakta baru yang berkonflik justru mempertahankan nilai usang. Ini salah secara desain.
- *Guard* menahan 21 dari 23 *chunk* dan membuang 157 fakta baru.

### B2.12 Klaim yang didukung data RQ1

- Verifikasi graph tidak memberi keunggulan atas verifikator teks yang adil pada kasus mana pun yang diuji (6.093 kasus, 0 berbeda).
- Verifikator deterministik mendeteksi semua kesalahan injeksi bila *transcript grounding* dipakai, dengan 1 penahanan keliru per set. Pada metrik resmi, kenaikan WA *guard* meningkat seiring tingkat kesalahan injeksi.
- Pada Mem0 dengan model kuat, *guard* tidak berpengaruh terukur pada metrik ekstraksi resmi. *Claim drop* dan *repair* tidak dapat dibedakan dari *unguarded*. Ini berlaku luring dan pada *live run*.
- Pada Mem0 dengan model lebih lemah (tidak diseleksi, 100 sesi), *claim drop* menaikkan WA yang dinilai DeepSeek sebesar +2,3 pts (CI 1,3–3,3) tanpa penurunan *recall* atau F1 yang signifikan: +2,8 pada pengguna *tuning*, +0,9 pada pengguna final. **Ini belum tervalidasi.**
- Menahan per sesi menurunkan *recall* 2,2–16,2 pts dan bukan titik operasi yang berguna.
- Pada resolusi konflik MemoryAgentBench, *guard* penahan konflik berbasis LLM menurunkan akurasi. *Repair* mengurangi kerugian itu tetapi tidak menghilangkannya.

### B2.13 Klaim yang tidak didukung data RQ1

- "MemBud mengurangi memori halusinasi": tidak ada pengurangan yang tervalidasi manusia.
- Pengurangan omisi atau korupsi catatan lama pada data alami. `add()` Mem0 hanya menghasilkan *event* ADD (dan 3 *event* NONE pada Long), tanpa UPDATE atau DELETE, sehingga jenis kesalahan ini tidak dapat diamati.
- Manfaat yang khas graph.

## B3. RQ2: Modularitas

> Dapatkah logika verifikasi yang sama bekerja pada berbagai *backend* memori melalui adapter? Informasi atau transformasi apa yang dibutuhkan tiap adapter, dan apa yang hilang?

### B3.1 Jawaban

**Ya untuk kelas *backend* tertentu, tidak secara umum.**

- **Yang berjalan tanpa perubahan:** verifikator yang sama dan *contract suite* 16 kasus yang sama berjalan pada tiga keluarga *backend* nyata melalui satu adapter *black-box* (`BlackBoxStaged`) yang hanya memakai panggilan API publik:
  - Mem0 2.2.1;
  - LangMem 0.0.30 di atas *store* LangGraph;
  - Graphiti 0.31.2 di atas Neo4j 5.26.0.
- **Yang tidak dapat dipasang:** lima sistem lain, masing-masing dengan alasan yang tercatat dan dapat dieksekusi.
- **Yang hilang:** semua yang tidak diekspos API publik. Pada memori graph, itu adalah provenansnya.
- **Kesimpulan:** ini bukti modularitas bersyarat, bukan universalitas.

### B3.2 Desain adapter *black-box*

Untuk setiap penulisan, `BlackBoxStaged` melakukan lima hal:

1. **Mengamati:** membaca *state live* melalui panggilan publik `list`, ditambah `schema` dan `history` bila ada.
2. **Menyiapkan (*stage*):** mengisi kembaran (*scratch twin*) yang terisolasi, menjalankan penulisan asli *backend* pada kembaran itu, lalu membandingkan kembaran sebelum dan sesudah untuk menurunkan efek sampingnya.
3. **Menolak penulisan yang tidak dimodelkan:** menolak penulisan ke *scope* asing serta perubahan skema, identitas *scope*, metadata atau riwayat.
4. **Memverifikasi:** menjalankan verifikator deterministik yang ada, tanpa perubahan.
5. **Meng-*commit*:** hanya pada ACCEPT, memutar ulang himpunan yang terverifikasi ke *live* melalui panggilan publik. Adapter menolak *state* usang, memeriksa hasilnya, dan melakukan kompensasi lewat daftar *undo* bila gagal.

Satu-satunya syarat tambahan bagi *backend* adalah `scratch_factory()`, yaitu instans kedua yang terisolasi.

### B3.3 Tabel R2-1. *Contract suite* per *backend*

Sumber: `data/results/adapter-contract-v1/<backend>/evidence.json`.

| *Backend* (versi dipatok) | Adapter | *Contract suite* (16 kasus) | Kesimpulan |
|---|---|---|---|
| Mem0 `2.2.1` (spaCy 3.8.18) | `mem0_staged` (*shadow clone*) | 16/16 lulus | Melihat semua permukaan tulis |
| Mem0 2.2.1 | `BlackBoxStaged` | 16/16 lulus | Buta terhadap DDL dan baris *entity store* |
| LangMem `0.0.30` di atas LangGraph `InMemoryStore` | `BlackBoxStaged` | 15 lulus, 1 dilewati | Tidak ada permukaan riwayat |
| Graphiti `0.31.2` di atas Neo4j Community `5.26.0` | `BlackBoxStaged` | 15 lulus, 1 dilewati | Kehilangan provenans |
| Tiruan SQLite stdlib | `BlackBoxStaged` | 16/16 lulus | Kontrol dengan permukaan tersembunyi |

Kasus yang dilewati adalah "penulisan ulang riwayat ditolak". LangMem dan Graphiti tidak menyimpan riwayat, jadi efek itu tidak dapat diekspresikan.

*Suite* ini mencakup:

- HOLD identik per *byte*;
- penghapusan tanpa otorisasi ditolak;
- ketepatan ACCEPT;
- isolasi *scope*;
- penolakan *state* usang;
- kompensasi kegagalan *commit*;
- kegagalan yang tidak terpulihkan dilaporkan secara jelas;
- gagal-tertutup saat *backend error*;
- penulisan langsung ditolak;
- penolakan *scope* asing dan penulisan ulang riwayat;
- *blind spot* yang dideklarasikan.

Uji mutasi membuktikan *suite* ini menangkap regresi. Mematikan pemeriksaan *state* usang, mematikan kompensasi, atau membuat HOLD menulis masing-masing membuat tes yang bersangkutan gagal.

### B3.4 Tabel R2-2. Adapter *black-box* vs *shadow clone* pada Mem0 nyata yang sama

| Jika penulisan asli juga … | `BlackBoxStaged` | `mem0_staged` |
|---|---|---|
| menulis ke *scope* asing | DITOLAK | DITOLAK |
| menulis ulang riwayat | DITOLAK | DITOLAK |
| menjalankan DDL | DITERIMA, tak terlihat | DITOLAK |
| menulis baris *entity store* yang tidak diekspos API | DITERIMA, tak terlihat | DITOLAK |

Biaya, dari *benchmark* RQ3 (Tabel R3-2b): *add* satu fakta yang diterima memakan 27,1 ms (*black-box*) vs 17,1 ms (*shadow clone*) pada N = 0. Pada N = 2.000 angkanya 5,5 s vs 1,07 s.

Inilah *trade-off* utamanya. *Shadow clone* melihat segalanya tetapi harus memahami tata letak penyimpanan Mem0. Adapter *black-box* dapat digeneralisasi tetapi hanya melihat API publik.

### B3.5 Syarat bagi *backend* (RQ2, bagian pertama)

1. ***State* yang dapat diamati:** seluruh *state*, dengan id yang stabil, dapat dibaca lewat panggilan publik.
2. **Kembaran yang dapat diisolasi:** instans kedua yang penulisannya tidak dapat menyentuh instans pertama.
3. **Penulisan asli yang dapat direduksi:** penulisan dapat direduksi menjadi *insert*, *update* dan *delete* biasa atas butir yang teramati.
4. ***Undo* publik:** cara untuk membalik penulisan, untuk kompensasi.
5. *(Opsional)* `schema()` dan `history(id)`, yang memperluas apa yang dapat dideteksi.

### B3.6 Tabel R2-3. Yang harus disediakan tiap adapter

| *Backend* | Id / teks | *Scope* | Kembaran | Pekerjaan tambahan |
|---|---|---|---|---|
| Mem0 | id memori / memori | Daftar *scope* eksplisit (`get_all` tidak dapat mengenumerasi); `top_k` dinaikkan; pemotongan ditolak | *Host* Qdrant + SQLite tertanam kedua | Riwayat dinormalisasi menjadi `[event, old, new]` |
| LangMem | kunci *store* / konten | *Namespace* `(memories, user)`, dapat dienumerasi | `InMemoryStore` kedua | *Timestamp* dikecualikan |
| Graphiti | uuid *fact-edge* / `fact` | Partisi `group_id`, dienumerasi dengan Cypher | `group_id` lain di basis data yang sama | Partisi kosong memunculkan *error* yang ditangkap; *node* ujung dibuat saat pemutaran ulang |

### B3.7 Tabel R2-4. Apa yang hilang (RQ2, bagian kedua)

| *Backend* | Terlihat | Tidak terlihat (*blind spot* yang dideklarasikan) |
|---|---|---|
| Mem0 (*black-box*) | Teks memori, metadata, riwayat, *scope* asing | DDL, baris *entity store*, koleksi lain |
| LangMem | Butir, *namespace* asing | Indeks vektor, internal *store*, skema |
| Graphiti | *Fact edge*, relasi, nama ujung, penanda kedaluwarsa, partisi asing | *Node*, episode, `MENTIONS`, ringkasan, *embedding*, provenans, bidang temporal, indeks |

**Uji fidelitas Graphiti** (`tests/test_graphiti_fidelity.py`). *Add* yang sama, tanpa vs dengan *guard*:

| | *Fact edge* | *Node* entitas | *Node* episode | *Edge* `MENTIONS` |
|---|---|---|---|---|
| Penulisan asli tanpa *guard* | 2 | 3 | 1 | 3 |
| ACCEPT dengan *guard* | 2 | 3 | 0 | 0 |

Graph yang dijaga memiliki fakta yang sama tetapi tanpa episode (provenans) dan tanpa *mention*. Memori graph yang nilainya terletak pada provenans kehilangan tepat hal itu di bawah model observasi yang hanya melihat fakta.

### B3.8 Tabel R2-5. Sistem yang tidak dapat dipasang di balik adapter

Setiap alasan di bawah adalah tes yang dapat dieksekusi atau pengamatan yang tercatat.

| Sistem | Alasan |
|---|---|
| A-Mem (git `ceffb86`) | Instans kedua dalam proses yang sama me-*reset* klien chroma bersama, jadi kembaran tidak mungkin. `add_note` selalu menjalankan LLM evolusi dan dapat menulis ulang catatan tetangga. |
| LlamaIndex `0.14.26` (*fact memory*) | Fakta tidak memiliki id. Fakta hanya diekstrak saat token meluap. Melebihi `max_facts` mengganti seluruh daftar. |
| AutoGen `autogen-core 0.7.5` | Hanya add/query/clear, tanpa id dan tanpa penulisan asli |
| Graphiti di atas Kuzu tertanam `0.11.3` | `group_id` memunculkan *error* di `KuzuDriver`. Ekstensi *full-text* butuh unduhan jaringan. |
| Letta `0.16.4` | Membutuhkan Postgres; tidak ada jalur SQLite |
| Zep Cloud | SaaS, tidak dicoba |

**Polanya.** *Backend* yang memenuhi syarat memiliki id stabil, *state* yang dapat dibaca, kembaran yang dapat diisolasi, dan penulisan yang tereduksi menjadi *insert* biasa. *Backend* yang gagal biasanya menulis ulang tetangga atau seluruh *store* saat menulis, tidak memiliki id, atau tidak punya apa pun untuk di-*stage*.

### B3.9 Tabel R2-6. Eksekusi dengan model nyata

Sumber: `data/results/*-blackbox-realmodel-v1/evidence.json`; eksekusi leksikal dari git `bb5b142`, `fc1bcdc`.

Semua eksekusi memakai model gratis OpenRouter `nvidia/nemotron-3.5-lightning:free` ($0), dialog sintetis, dan operasi *add* saja.

| *Backend* | Kebijakan *grounding* | Sesi | ACCEPT | HOLD | Token | Catatan |
|---|---|---|---|---|---|---|
| Mem0 | `LexicalGrounding` | 5 | 0 | 5 | 8.449 | Penulisan ulang orang ketiga "User …" atas ucapan orang pertama tidak ter-*ground* |
| LangMem | `LexicalGrounding` | 5 | 0 | 5 | 5.300 | Penyebab sama |
| LangMem | `SpeakerNormalizedGrounding` | 5 | 3 | 2 | 11.762 | Penahanan karena sinonim ("resides" vs "live") dan ejaan ("favorite" vs "favourite") |
| Mem0 | `SpeakerNormalizedGrounding` | 5 | 1 | 4 | t/a (cache) | Penahanan termasuk "Germany" dan tanggal yang dikarang |
| Graphiti | `SpeakerNormalizedGrounding` | 4 | 4 | 0 | 74.258 | **3 dari 4 ACCEPT adalah ekstraksi kosong**; hanya "moved from Berlin to Lisbon" yang tersimpan |

Interpretasi:

- **Gagal-tertutup:** *guard* gagal-tertutup pada keluaran model nyata di semua *backend*, dan detail halusinasi ditahan.
- **Penahanan keliru:** *guard* juga menahan fakta benar karena parafrasa.
- **Bukti lemah:** tiap eksekusi hanya satu model gratis dan 4–5 sesi, tanpa *update* atau *delete*.
- **Graphiti:** eksekusinya hampir hampa, dan jalur kontradiksinya tidak teruji.

### B3.10 Batasan RQ2

- *Contract suite* dijalankan dengan model *chat* berskrip yang luring.
- Bukti model nyata kecil dan hanya *add*, tanpa statistik dan tanpa data HaluMem.
- Hanya satu varian *store* yang diuji per *backend*: Qdrant tertanam untuk Mem0, `InMemoryStore` untuk LangMem, Neo4j Community untuk Graphiti.
- Tidak ada penulis konkuren yang diuji.
- Versi selain yang dipatok belum diuji.

## B4. RQ3: *Trade-off*

> Bagaimana verifikasi memengaruhi penerimaan pembaruan valid, QA *downstream*, latensi, penggunaan token dan biaya API, termasuk *overhead* pembangunan graph?

### B4.1 Jawaban

- **Penerimaan pembaruan valid.** Biayanya bergantung pada seberapa banyak yang dibuang *guard*.
  - **Verifikator deterministik** (graph dan `text_fair` memutuskan identik): paling banyak 1 pembaruan valid salah ditahan per set berlabel (1/311, 1/1.340).
  - ***Claim drop*:** membuang 1,6% klaim pada *extractor* kuat, dan reviewer menilai sebagian besar di antaranya valid.
  - ***Session hold*:** menahan 17,9% klaim.
- **QA *downstream*.** Tidak ada kondisi *guard* yang meningkatkan QA pada data nyata.
  - *Session hold* *menurunkan* akurasi LongMemEval 6,5 pts (CI −10,0 hingga −3,5; n = 200; p = 0,0002).
  - *Guard + repair* turun 1,0 pts (CI −2,5 hingga 0,0; p = 0,5).
  - *Claim drop* tidak mengubah QA HaluMem *live*.
- **Latensi, token dan biaya.** Semuanya ditentukan oleh *judge* LLM, bukan oleh graph.
  - *Judge* melakukan satu panggilan per penulisan: median 1,2–1,6 s, 2,8 ribu–9,7 ribu token masuk, sekitar $0,0012–0,0034 per penulisan pada harga daftar.
  - Itu sekitar 0,3× (Mem0) hingga 1,3× (LongMemEval) biaya ekstraksi *host* sendiri.
  - *Repair* menambah sekitar 3,8 s dan $0,003–0,004 per penulisan yang ditahan.
  - Verifikasi deterministik hanya mikrodetik hingga milidetik.
- ***Overhead* pembangunan graph.**
  - Pembangunan graph linear dan murah: 8,2 ms pada 2.000 catatan, sekitar 1,1× serialisasi teks.
  - Verifikator graph mandiri bersifat kuadratik (2,2 s pada 2.000 catatan, vs 4,9 ms untuk teks). Penyebabnya detail implementasi yang dapat diperbaiki.
  - Di dalam adapter Mem0, perbedaan graph vs teks tidak terdeteksi, karena salinan *state* adapter mendominasi.
- **Pengeluaran.** Satu-satunya angka yang benar-benar ditagih adalah ekspor DeepSeek: $4,21 untuk 21–24 November 2026, seluruh lalu lintas proyek. Aturan harga puncak di repositori menaksir token yang sama 2,87× terlalu tinggi.
- ***Trade-off*.** Pada kesalahan injeksi, *guard + repair* memiliki manfaat bersih (ΔWA + Δ*recall*) yang positif sejak dosis terendah. CI-nya tidak memuat nol mulai dosis 4,1%. Pada tingkat kesalahan alami *extractor* kuat, manfaat bersihnya tidak dapat dibedakan dari nol. F1 tidak pernah membaik dalam rentang terukur.

### B4.2 Tabel R3-1. RQ3 dalam satu tabel

| Dimensi | *Unguarded* | *Session hold* | *Claim drop* | *Guard + repair* | Verifikator deterministik (graph / text / text_fair) |
|---|---|---|---|---|---|
| **Penerimaan pembaruan valid** | Semua diterima [M] | Menahan 17,9% klaim (230/1.286; Wilson 15,9–20,1%) pada Mem0+DeepSeek; 31/100 sesi pada Mem0+Llama [M] | Membuang 1,6% klaim (20/1.286). Reviewer mengonfirmasi 1/8 dan 0/8 tanda, jadi ≈1,4–1,6% klaim adalah klaim valid yang terbuang [M; porsi E]. Llama: 5,4% dibuang, ≈2,8–3,3% valid terbuang [E] | Memulihkan 14/14 (kuat), 31/31 (lemah), 23/23 (LongMemEval) sesi yang ditahan; sebagian besar dengan menghapus klaim bertanda [M] | Keputusan sama pada setiap kasus. Valid diterima 14/14 (sintetis v2), 17/17 (percakapan), 310/311 dan 1.339/1.340 (HaluMem Fase 1) [M] |
| **Kualitas ekstraksi** (*extractor* kuat) | acuan | *Recall* −2,2 pts [−3,9; −1,0]; bersih −2,0 [−3,7; −0,8] [M] | *Recall* −0,1 [−0,4; +0,2]; WA +0,2 [−0,0; +0,6] [M] | Sama dengan *claim drop* [M] | U |
| **QA *downstream*** | LongMemEval 0,650; HaluMem *live* 0,939 [M] | LongMemEval **−6,5 pts [−10,0; −3,5]**, 13 vs 0, p = 0,0002 [M] | HaluMem *live* 0,939 vs 0,939, 4 vs 4 berbeda [M] | LongMemEval −1,0 pts [−2,5; 0,0], 2 vs 0, p = 0,5 [M] | Hanya simulator: +9,0 pts EM pada tingkat kesalahan 0,35 (label spesifikasi) [M]; QA nyata U |
| **Latensi per penulisan** | Ekstraksi Mem0 median 3,16 s (p90 5,03) [M] | + *judge* 1,23 s (p90 2,10) Medium; 1,57 s Long; 1,30 s LongMemEval [M] | *Judge* sama [M] | + sekitar 3,8 s per penulisan yang ditahan (*repair* 2,08 s + penilaian ulang 1,70 s) [M] | Fase verifikasi di adapter pada N = 2.000: teks ≈24 ms; graph 174–189 ms pada 3 dari 4 sel (terdampak GC) [M]. Salinan *state*: StagedMem0 17 ms → 1,07 s; BlackBoxStaged 27 ms → 5,5 s (N = 0 → 2.000) [M] |
| **Token per penulisan** | Ekstraksi Mem0 ≈11,0 ribu masuk / 0,7 ribu keluar; *extractor* LongMemEval 3,1 ribu / 0,2 ribu [M] | *Judge* 2.845 / 316 (Medium, tahap ketat); 2.772 / 300 (Llama); 9.713 / 384 (Long); 3.602 / 244 (LongMemEval) [M] | Sama [M] | + 14,4% (kuat) atau 31% (lemah) penulisan × ≈9,4 ribu / 0,8 ribu untuk *repair* [M] | 0 [M] |
| **Biaya API per penulisan (harga daftar puncak)** | Ekstraksi Mem0 $0,0041 [E] | $0,00123 satu tahap; $0,00181 sebagaimana dijalankan dengan dua tahap (Medium) [E] | Sama [E] | $0,00177 satu tahap, $0,00235 sebagaimana dijalankan (kuat); $0,00220 (lemah) [E] | $0 [M] |
| **Biaya per kesalahan yang dicegah** | – | U (menahan membuang lebih banyak daripada yang dicegah) | Injeksi: $0,0109 / $0,0027 / $0,0016 pada 1,1 / 4,1 / 7,1%. Alami: $0,0036–0,0045 (lemah), $0,048 hingga tak terbatas (kuat) [E] | Injeksi: $0,0172 / $0,0054 / $0,0037 [E] | – |
| **Pengeluaran tertagih** | Seluruh lalu lintas DeepSeek proyek 21–24 Nov 2026: **$4,21**, 16.624 permintaan [M]. Per kondisi: U. OpenRouter: U | | | | |
| ***Overhead* pembangunan graph** | – | – | – | – | Pembangunan linear (N^1,10), 8,2 ms pada N = 2.000. Verifikasi mandiri kuadratik (N^2,18), 2,2 s vs 4,9 ms untuk teks. Di adapter, graph vs teks tidak terdeteksi pada 18 dari 20 sel [M] |

### B4.3 Tabel R3-2a. *Micro-benchmark* verifikator

Sumber: `data/results/rq3-overhead-v1/verifier.json`.

Pengaturan:

- k = 4 operasi, µs per panggilan, median [IQR];
- 25 ulangan berselang-seling, GC dimatikan;
- Python 3.14.10, i5-12450HX, *governor* `powersave`;
- batas *noise*: panggilan kosong 0,022 µs; batas A/A 1,1% pada persentil ke-95.

| Catatan lama N | Bangun graph | Verifikasi graph | Total graph | Serialisasi teks | Verifikasi teks | Verifikasi *fair text* | Total graph / verifikasi teks |
|---|---|---|---|---|---|---|---|
| 0 | 21,5 [0,6] | 72,8 [2,0] | 101,5 [2,1] | 30,7 [0,4] | 18,0 [0,2] | 18,6 [0,2] | 5,6× |
| 10 | 52,5 [0,6] | 189 [4] | 250 [5] | 58,3 [0,5] | 39,4 [0,7] | 41,0 [0,5] | 6,3× |
| 100 | 322 [4] | 4.281 [63] | 4.604 [75] | 344 [4] | 225 [4] | 231 [4] | 20× |
| 250 | 825 [11] | 25.107 [1.524] | 25.916 [696] | 823 [17] | 540 [9] | 549 [11] | 48× |
| 500 | 1.772 [77] | 104.678 [4.096] | 108.210 [3.611] | 1.667 [34] | 1.128 [32] | 1.157 [31] | 96× |
| 1.000 | 3.844 [201] | 539.908 [6.781] | 560.008 [8.696] | 3.446 [163] | 2.296 [64] | 2.353 [74] | 244× |
| 2.000 | 8.159 [321] | 2.227.953 [45.387] | 2.344.574 [56.939] | 7.152 [380] | 4.924 [196] | 5.116 [271] | 476× |

- **Penskalaan** (kemiringan log-log, N = 250–2.000):
  - linear: bangun graph 1,10, serialisasi teks 1,04, verifikasi teks 1,06, *fair text* 1,07;
  - kuadratik: verifikasi graph 2,18.
- **Penyebab suku kuadratik.** Pemeriksaan *required claims* (`src/membud/graph_verification.py:104-108`) membangun ulang seluruh *edge set* sekali untuk setiap klaim wajib. Menurut estimasi, ini memakan sekitar 70% hingga hampir seluruh waktu verifikasi graph pada N ≥ 100. Ini artefak implementasi, bukan sifat graph, dan tidak diubah karena RQ3 mengukur kode yang dievaluasi.
- ***Fair text* vs teks biasa:** +1% hingga +5%, terdeteksi pada 10 dari 35 sel.
- **Uji A/A:** tidak pernah terdeteksi (rasio 0,97–1,01).
- **Ukuran transisi k:** pada N = 500, total graph 108 ms pada k = 4 dan 134 ms pada k = 64.

### B4.4 Tabel R3-2b. `add` yang dijaga pada Mem0 nyata

Sumber: `data/results/rq3-overhead-v1/adapters.json`.

Pengaturan: satu fakta, ACCEPT, median ms [IQR]; Python 3.12.16; Mem0 2.2.1 dengan tiruan LLM dan *embedder* luring; 11 ulangan (7 pada N ≥ 1.000).

| Catatan *live* N | StagedMem0, graph | StagedMem0, teks | BlackBoxStaged, graph | BlackBoxStaged, teks |
|---|---|---|---|---|
| 0 | 17,1 [1,2] | 16,4 [1,4] | 27,1 [1,0] | 26,5 [0,4] |
| 100 | 58,1 [1,5] | 57,0 [2,5] | 220 [7] | 222 [10] |
| 500 | 226 [5] | 224 [7] | 1.074 [21] | 1.065 [39] |
| 1.000 | 440 [1] | 441 [7] | 2.328 [20] | 2.369 [49] |
| 2.000 | 1.069 [29] | 1.060 [6] | 5.487 [75] | 5.505 [71] |

- **Graph vs teks di dalam adapter:** tidak terdeteksi pada 18 dari 20 sel (adapter, N, keputusan). Dua sel yang terdeteksi menunjuk arah berlawanan.
- **Penskalaan** (*fit* N = 500–2.000): StagedMem0 ≈ N^1,12; BlackBoxStaged ≈ N^1,18 dan sekitar 5× lebih lambat. Adapter *black-box* mengisi ulang kembaran lewat API publik, yang memakan sekitar 95% waktunya pada N ≥ 500.
- **HOLD vs ACCEPT:** HOLD 5–34% lebih cepat daripada ACCEPT.
- **Ukuran transisi:** k = 16 fakta menambah 17% (Staged) dan 7% (BlackBox) pada N = 500.
- **Porsi verifikasi terhadap total (StagedMem0, graph):** 0,6% / 2,6% / 3,4% / 3,6% pada N = 0 / 100 / 500 / 1.000.
- **Reproduksi:** StagedMem0 HOLD 14,5 / 43,6 / 148,8 ms pada N = 0 / 100 / 500. Ini mereproduksi `mem0-guarded-v1/timing.json` sebelumnya (14,3 / 45,4 / 146,9) dalam selisih beberapa persen.

### B4.5 Tabel R3-3. Token, latensi dan biaya per panggilan

Sumber: `data/results/rq3-cost-v1/token-attribution.json`.

Token dilaporkan penyedia. Dolar memakai harga daftar puncak *cache-miss* ($0,30 / $1,20 per juta token masuk / keluar) dan merupakan estimasi [E]. Kalikan 0,349 untuk nilai yang dikalibrasi dengan tagihan.

| Komponen | Peran | Panggilan | Token masuk / keluar per panggilan | Median latensi (p90) | $ per panggilan, puncak |
|---|---|---|---|---|---|
| Ekstraksi Mem0, DeepSeek (diatribusikan dari bentuk respons, campuran eksekusi) | *host* | 178 | 11.013 / 696 | 3,16 s (5,03) | 0,00414 |
| *Claim judge*, tahap ketat, Mem0+DeepSeek Medium | *guard* | 97 | 2.845 / 316 | 1,23 s (2,10) | 0,00123 |
| *Claim judge*, penilaian ulang *accepted-policy* atas 30 sesi bertanda | *guard* (sebagaimana dijalankan) | 30 | 4.809 / 345 | 2,01 s (2,65) | 0,00186 |
| *Claim judge*, Mem0+Llama Medium (satu tahap) | *guard* | 100 | 2.772 / 300 | 1,57 s (2,73) | 0,00119 |
| *Claim judge*, Mem0+DeepSeek Long | *guard* | 27 | 9.713 / 384 | 1,57 s (1,85) | 0,00337 |
| *Claim judge*, LongMemEval (termasuk verifikasi ulang) | *guard* | 343 (324 sesi) | 3.602 / 244 | 1,30 s | 0,00137 |
| *Extractor* LongMemEval | *host* | 324 | 3.110 / 177 | 1,30 s | 0,00115 |
| *Extractor repair* + penilaian ulang, per sesi yang ditahan (Medium) | *repair* | 14 + 14 | 9.386 / 772 (kedua panggilan) | 2,08 s + 1,70 s | 0,00374 |
| *Repair*, per sesi yang ditahan (Llama) | *repair* | 33 + 33 | 7.778 / 616 | 1,94 s + 1,75 s | 0,00327 |
| Penilaian resmi HaluMem (Medium) | hanya evaluasi | 3.219 (33,2 per sesi) | 2.160 / 90 | 1,24 s | 0,0251 per sesi |

Penilaian khusus evaluasi menyumbang **94%** pengeluaran untuk satu perbandingan kondisi HaluMem: $2,86 vs $0,17 per eksekusi 97 sesi pada harga puncak.

### B4.6 Tabel R3-4. Rekonsiliasi tagihan (ekspor DeepSeek dari pemilik proyek)

Sumber: `data/results/rq3-cost-v1/billing-reconciliation.json`.

| Hari | Permintaan | Token masuk *cache-hit* | Token masuk *cache-miss* | Token keluar | USD tertagih [M] |
|---|---|---|---|---|---|
| 2026-11-21 | 97 | 733.568 | 221.032 | 59.473 | 0,071 |
| 2026-11-22 | 4.077 | 4.489.984 | 4.477.576 | 489.840 | 0,979 |
| 2026-11-23 | 8.484 | 7.920.804 | 7.242.657 | 886.126 | 2,409 |
| 2026-11-24 (ekspor diambil siang hari) | 3.966 | 4.517.662 | 3.198.323 | 430.216 | 0,751 |
| **Total** | **16.624** | **17.662.018** | **15.139.588** | **1.865.655** | **4,210** |

- **Kesalahan basis harga.** Aturan puncak *cache-miss* yang diterapkan pada token tertagih menghasilkan $12,08, yaitu **2,87×** tagihan. Aturan di luar jam puncak menghasilkan $6,04 (1,43×).
  - **Penyebab:** 54% token masuk adalah *cache hit*, yang ditagih jauh lebih murah.
  - **Faktor kalibrasi:** 0,349, hanya berlaku untuk campuran beban kerja ini.
- **Cakupan cache lokal:** 70% token masuk tertagih, 74% token keluar, 70% permintaan.
- **Tidak tersedia:**
  - biaya tertagih per kondisi (penyedia tidak memberi rincian per panggilan);
  - tagihan OpenRouter, jadi ekstraksi Llama-3.1-8B tidak terverifikasi tagihannya.

### B4.7 Tabel R3-5. Model *trade-off*: manfaat, kerugian sampingan dan biaya per kesalahan yang dicegah

Sumber: `data/results/rq3-tradeoff-v1/tradeoff.json`.

Pengaturan: metrik resmi HaluMem, 97 sesi, dosis injeksi bersarang, *bootstrap* berpasangan 2.000 *resample* (*seed* 20261124), pts dengan CI. **Bersih = ΔWA + Δ*recall*.** Label injeksi sirkular.

| Tingkat injeksi | Kondisi | ΔWA | Δ*Recall* | ΔF1 | **Bersih** | Kesalahan dibuang | Klaim valid dibuang | $ per kesalahan dibuang, puncak |
|---|---|---|---|---|---|---|---|---|
| 0 (alami) | *Claim drop* = *repair* | +0,19 [−0,03; 0,57] | −0,11 [−0,41; 0,18] | −0,07 [−0,23; 0,09] | +0,08 [−0,34; 0,63] | – | 20/1.286 (1,56%) | lihat alami |
| | *Session hold* | +0,18 [−0,32; 0,78] | −2,20 [−3,94; −0,99] | −1,18 [−2,15; −0,52] | **−2,02 [−3,66; −0,81]** | – | 230/1.286 (17,9%) | – |
| 1,1% | *Claim drop* | +0,71 [0,26; 1,27] | −0,59 [−1,17; −0,14] | −0,32 [−0,63; −0,08] | +0,12 [−0,33; 0,53] | 11/14 | 18/1.279 | 0,0109 |
| | *Guard + repair* | +0,72 [0,26; 1,29] | −0,21 [−0,52; 0,06] | −0,12 [−0,29; 0,03] | +0,50 [−0,01; 1,15] | 11/14 | – | 0,0172 |
| 4,1% | *Claim drop* | +2,67 [1,82; 3,58] | −1,93 [−3,17; −1,03] | −1,02 [−1,70; −0,55] | +0,73 [−0,25; 1,58] | 45/54 | 17/1.259 | 0,0027 |
| | *Guard + repair* | +2,79 [1,90; 3,74] | −0,81 [−1,67; −0,19] | −0,42 [−0,87; −0,10] | **+1,98 [1,00; 3,03]** | 45/54 | – | 0,0054 |
| 7,1% | *Claim drop* | +4,50 [3,52; 5,48] | −2,74 [−4,12; −1,62] | −1,45 [−2,20; −0,86] | **+1,76 [0,55; 2,80]** | 78/94 | 15/1.239 | 0,0016 |
| | *Guard + repair* | +4,69 [3,67; 5,72] | −0,75 [−1,67; −0,09] | −0,41 [−0,89; −0,06] | **+3,94 [2,68; 5,11]** | 78/94 | – | 0,0037 |
| | *Session hold* | +5,32 [3,74; 6,98] | −44,8 [−58,6; −31,2] | −30,3 [−44,0; −19,1] | −39,5 [−53,4; −25,7] | 94/94 | 57,7% klaim valid | – |

***Fit* linear atas dosis terukur (0–7,1%),** pts per pts tingkat injeksi:

- **WA:** *claim drop* +0,62 [0,47; 0,77]; *repair* +0,65 [0,50; 0,81].
- **Bersih:** *claim drop* +0,24 [0,05; 0,39]; *repair* +0,55 [0,37; 0,72].
- **F1:** *claim drop* −0,20 [−0,30; −0,11]; *repair* −0,05 [−0,13; −0,003].

**Kapan manfaat melebihi biaya:**

- **Titik impas *fit* bersih:** di atau di bawah batas bawah rentang terukur. *Claim drop*: 0,27% (CI −3,0 hingga 2,6%). *Repair*: 0,08% (CI −1,0 hingga 0,9%).
- **CI manfaat bersih tidak memuat nol** mulai 4,1% (*repair*) dan mulai 7,1% (*claim drop*).
- **F1** tidak pernah membaik dalam rentang terukur.
- ***Session hold*** bernilai bersih negatif di semua dosis.
- **Di atas 7,1%:** tidak ada pengukuran.

**Kesalahan alami.** Kesalahan yang dicegah diestimasi sebagai jumlah klaim bertanda × presisi yang dikonfirmasi reviewer.

| *Extractor* | Ditandai | Presisi tanda (reviewer 1; reviewer 2) | Estimasi kesalahan benar yang dibuang | Klaim valid dibuang | $ per kesalahan dicegah, *judge* saja, puncak |
|---|---|---|---|---|---|
| Mem0 + DeepSeek (97) | 20 | 1/8; 0/8 | 2,5 (0,4–9,4); 0 (0–6,5) | 17,5–20 | $0,048 (terbaik $0,013); tak terbatas jika 0/8 benar |
| Mem0 + Llama-3.1-8B (100) | 68 | 13/27; 9/23 | 32,7 (20,9–44,9); 26,6 (15,1–40,3) | 35–41 | $0,0036–0,0045; *guard + repair* $0,0067–0,0083 |

**Alami vs injeksi.** Memetakan tingkat tanda 5,4% dari eksekusi Llama ke kemiringan WA injeksi memprediksi +3,4 pts. Kenaikan terukurnya +2,3 [1,3; 3,3]. Jadi tanda alami menghasilkan sekitar dua pertiga dari manfaat kesalahan injeksi, konsisten dengan reviewer yang hanya mengonfirmasi 39–48% tanda. Pemetaan itu sendiri adalah asumsi.

### B4.8 Tabel R3-6. QA *downstream*

Sumber:

- `data/results/rq3-tradeoff-v1/longmemeval-n200-paired.json`;
- `data/longmemeval-runs/n200/report.json`;
- berkas *live run*.

| *Benchmark* | Pertanyaan | *Unguarded* | *Session hold* | *Claim drop* | *Guard + repair* |
|---|---|---|---|---|---|
| LongMemEval *oracle* (*seed* 20261122, bergilir per jenis) | 200 | 0,650 | 0,585: **−6,5 pts [−10,0; −3,5]**, 13 vs 0, p = 0,0002 | bukan kondisi | 0,640: −1,0 [−2,5; 0,0], 2 vs 0, p = 0,5 |
| Sama, 170 pertanyaan di luar pilot 30 pertanyaan | 170 | – | −6,5 [−10,6; −2,9], 11 vs 0, p = 0,001 | – | −1,2 [−2,9; 0,0], 2 vs 0, p = 0,5 |
| HaluMem *live*, Mem0 OSS vs Mem0 + MemBud | 98 | 0,939 | tidak dijalankan | 0,939, 4 vs 4 berbeda | tidak dijalankan |
| MemoryAgentBench 6k *single-hop* / *multi-hop* (*guard* konflik LLM) | 100 + 100 | 1,00 / 0,13 | 0,26 / 0,00 | – | 0,52 / 0,09 |

- **Yang dilakukan *judge* pada LongMemEval n = 200:** menandai 83 dari 3.286 klaim (2,5%) dan menahan 23 dari 324 sesi (7,1%). *Repair* memulihkan ke-23 sesi.
- ***Session hold*** kehilangan jawaban pada keenam jenis pertanyaan (−1 hingga −3 masing-masing) dan tidak mendapat satu pun.
- ***Guard + repair*** kehilangan 2 pertanyaan *single-session-assistant*.

**Daya uji** (McNemar, α = 0,05, daya 80%):

| Diskordansi | n untuk 2 / 3 / 5 pts | Terdeteksi pada n = 200 / 500 |
|---|---|---|
| 8,2% (HaluMem *live*, sebagian besar *noise*) | 1.600 / 710 / 254 | 5,6 / 3,6 pts |
| 6,7% (pilot LongMemEval, *session hold*) | 1.306 / 580 / 207 | 5,1 / 3,2 pts |
| 3% (asumsi untuk *guard + repair*) | 587 / 260 / 92 | 3,4 / 2,2 pts |

LongMemEval *oracle* hanya memiliki 500 pertanyaan, jadi efek QA 2 poin tidak dapat dipastikan dari *benchmark* ini saja.

### B4.9 Klaim yang didukung data RQ3

- Verifikasi deterministik kecil dibanding salinan *state* adapter. Perbedaan graph vs teks tidak terdeteksi secara ujung-ke-ujung di dalam adapter Mem0 (N = 0–2.000).
- Pembangunan graph linear dan biayanya setara serialisasi teks (8,2 vs 7,2 ms pada 2.000 catatan). Verifikator graph mandiri kuadratik karena detail implementasi yang dapat diperbaiki.
- Mekanisme adapter, bukan verifikasi, yang menentukan *overhead* deterministik. StagedMem0 memakan sekitar 1,1 s per penulisan pada 2.000 catatan. BlackBoxStaged sekitar 5× lebih lambat.
- *Claim judge* LLM menentukan biaya nyata *guard*. *Repair* menambah sekitar 3,8 s dan $0,003–0,004 per penulisan yang ditahan. Penilaian khusus evaluasi sekitar 20× biaya *guard* per sesi.
- Aturan estimasi puncak *cache-miss* menaksir tagihan DeepSeek 2,87× terlalu tinggi. Pengeluaran tertagih untuk jendela ini $4,21.
- Menahan seluruh sesi menurunkan akurasi QA (−6,5 pts pada LongMemEval, tereplikasi pada 170 pertanyaan baru) dan *recall* (−2,2 hingga −16,2 pts).
- *Guard + repair* dan *claim drop* tidak mengubah QA secara terukur. Pada kesalahan injeksi, manfaat ekstraksi bersihnya tumbuh seiring tingkat kesalahan. Untuk *guard + repair*, manfaat itu terdeteksi mulai dosis 4,1%.

### B4.10 Klaim yang tidak didukung data RQ3

- Keunggulan graph dalam biaya maupun keputusan.
- "*Guard* meningkatkan QA *downstream*."
- "Verifikasi itu gratis." *Judge* LLM yang dipasang menambah sekitar 30–130% biaya model *host* per penulisan.
- Biaya tertagih per kondisi atau per penulisan. Hanya tagihan agregat yang ada.
- Manfaat umum, atau titik impas di atas 7,1%.
- Selisih latensi *live run* (4,78 s vs 4,98 s per sesi) sebagai *overhead guard*. Panggilan Mem0 pada lengan MemBud sebagian besar dilayani dari cache respons.

## B5. Ringkasan lintas-RQ untuk makalah

| RQ | Klaim yang didukung | Bukti utama | Kualifikasi utama |
|---|---|---|---|
| RQ1b | Tidak ada keunggulan graph bila informasi setara | 0 keputusan berbeda pada 6.093 kasus | Verifikator deterministik; verifikator LLM hanya berbeda sebelum pemilihan *prompt* |
| RQ1a | *Guard* membuang kesalahan injeksi; manfaat naik seiring dosis | WA +0,7 → +4,7 pts pada 1,1 → 7,1% | Label sirkular |
| RQ1a | *Extractor* kuat: tidak ada efek terukur | Luring 97 sesi, *live* 50 sesi | *Live run* tidak diulang |
| RQ1a | *Extractor* lemah: +2,3 pts WA, tanpa penurunan *recall* | 100 sesi, CI 1,3–3,3 | Belum tervalidasi: presisi reviewer 39–48%, hakim sekeluarga model |
| RQ2 | Modularitas bersyarat | 3 *backend* nyata lulus satu *contract suite*; 5 sistem gagal dengan alasan tercatat | Adapter *black-box* kehilangan *state* yang tak teramati (provenans Graphiti) |
| RQ3 | Biaya ditentukan *judge* LLM dan adapter, bukan graph | *Benchmark* dan atribusi token | Satu mesin; dolar adalah estimasi, kecuali total $4,21 |
| RQ3 | *Session hold* merugikan QA dan *recall* | LongMemEval −6,5 pts; *recall* −2,2 hingga −16,2 | – |

## B6. Reproduksibilitas

Skrip, berkas keluaran dan *seed* sama dengan Bagian A6:

- *seed* sampel 20261121;
- *bootstrap* RQ1 20261122;
- injeksi dan kuis *extractor* lemah 20261123;
- *benchmark* dan *bootstrap* RQ3 20261124.

Direktori eksekusi berisi teks *dataset* (HaluMem berlisensi CC BY-NC-ND) dan diabaikan oleh git. Direktori eksekusi LongMemEval n = 200 berada di `data/longmemeval-runs/n200/` (dipindahkan dari *worktree* `MemBud-rq3` yang sudah dihapus).
