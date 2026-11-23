# RQ1 results section (draft for the paper)

> (a) Does MemBud reduce omissions, corruption of valid prior records, and unsupported additions relative to an unguarded pipeline? (b) Does graph-based verification catch more errors than a non-graph verifier given the same criteria and budget?

**Short answer.**

- **(b): no.** Graph and a fair text verifier make identical decisions on every tested case (0 discordant pairs on 6,093 cases), so there is no graph advantage under equal information.
- **(a): conditional, not validated.**
  - **Strong extractor:** with Mem0 on a strong model, natural errors are rare (1.1 to 1.6% of claims flagged). The guard has no measurable effect on the official metrics.
  - **Errors present:** when errors exist, dropping the flagged claims raises HaluMem's official weighted accuracy. The gain is +0.7 to +4.7 points on injected faults, rising with the dose. It is +2.3 points (95% CI 1.3 to 3.3) on all 100 sessions of a naturally weaker extractor (Mem0 with Llama-3.1-8B), with no significant recall or F1 cost.
  - **Why this is not validated:**
    - Reviewers confirmed only 39 to 48% of the weak-extractor flags as unsupported.
    - The official accuracy judge is DeepSeek, the same model family as the claim judge, not the benchmark's GPT-4o.
    - The injected labels are circular by construction.
  - **Session-level holding:** holding a whole session cost recall everywhere.
  - **MemoryAgentBench:** an LLM conflict-hold guard lowered accuracy on conflict resolution.
- **Wording for the paper:** do not write "MemBud reduces hallucinated memory" without these qualifiers.

## Setup

| Item | Value |
|---|---|
| Benchmark (main) | HaluMem-Medium (revision `cb04336`), 20 users x first up to 5 sessions, seed 20261121; official extraction metrics re-implemented from MemTensor/HaluMem `eval/evaluation.py` at commit `d653749` (differential test against the reference code) |
| Extractors | Mem0 2.2.1 with DeepSeek `deepseek-flash` (97 sessions, 1,286 claims); Mem0 2.2.1 with `meta-llama/llama-3.1-8b-instruct` (100 sessions, 1,259 claims, same users and sessions) |
| Claim judge (guard flags) | DeepSeek, batch prompt, accepted-assistant-facts policy (`SYSTEM_ACCEPTED`) |
| Official metric judge | DeepSeek with the official HaluMem prompts (deviation: the benchmark uses GPT-4o, no longer offered by the OpenAI API) |
| Conditions | **Unguarded** (commit all); **Session hold** (hold a session if any claim is flagged); **Claim drop** (drop only flagged claims); **Guard + repair** (bounded repair loop, at most 2 retries, then re-verification) |
| Statistics | Paired session-level bootstrap, 2,000 resamples, seed 20261122; Wilson 95% intervals for proportions; exact McNemar for paired accuracy |
| Split | Users split by seeded hash into tuning and final halves; descriptive only, nothing was tuned on either |
| Rubric | `docs/halumem-grounding-rubric.md`, frozen v1 (SHA-256 `9830c317...`), used by both reviewers |

## Table 1. RQ1(b): graph vs fair text verifier

| Data | Cases | Discordant decisions (graph vs `text_fair`) | McNemar p |
|---|---|---|---|
| Synthetic fixture | 66 | 0 | 1.0 |
| HaluMem-Medium, Phase 1 (real data, injected faults) | 1,422 | 0 | 1.0 |
| HaluMem-Long, Phase 1 | 4,508 | 0 | 1.0 |
| HaluMem-Medium, Phase 2 (natural Mem0 proposals) | 97 | 0 | 1.0 |

Plain `text`, which ignores `evidence.contradicts`, loses only on corruption signalled solely through that field. This is a robust null: **no graph advantage under equal information.**

## Table 2. How often the claim judge flags claims as unsupported

| Extractor and data | Claims | Flagged | Rate (Wilson 95%) | Sessions with a flag |
|---|---|---|---|---|
| Mem0 + DeepSeek, HaluMem-Medium | 1,286 | 20 | 1.6% (1.0, 2.4) | 14 / 97 |
| Mem0 + DeepSeek, HaluMem-Long (first 3 sessions, 10 users) | 443 | 5 | 1.1% (0.5, 2.6) | 2 / 30 |
| DeepSeek, over-inferring prompt (not Mem0) | 1,722 | 20 | 1.2% | 15 / 97 |
| particle-1 (free), neutral prompt (not Mem0) | 2,740 | 0 | 0% | 0 / 97 |
| **Mem0 + Llama-3.1-8B, HaluMem-Medium** | 1,259 | 68 | **5.4% (4.3, 6.8)** | 31 / 100 |
| Same, rubric-aligned judge prompt (Table 8) | 1,259 | 93 | 7.4% | 38 / 100 |

The flagged rate is low for Mem0 on a strong model. Neither longer sessions nor prompting raised it. Only a weaker model inside Mem0 did.

## Table 3. RQ1(a) mechanism: injected faults

Phase 1 (structural verifier, transcript grounding): every injected omission, corruption and unsupported addition was detected, with one false hold on valid transitions in the real-data runs. The unguarded pipeline detects 0%.

Official metrics, dose-response (claim judge + official judge, 97 sessions). The faults are nested by a seeded hash, two per injected session: one corrupted claim and one fabricated claim. Paired bootstrap, 1,000 resamples:

| Injected claim rate | Sessions injected | Guard + repair: weighted accuracy | Guard + repair: recall | Claim drop: weighted accuracy | Claim drop: recall |
|---|---|---|---|---|---|
| 0 (natural Mem0) | 0 | +0.0019 (-0.0003, 0.0058) | -0.0011 (-0.0041, 0.0018) | +0.0019 | -0.0011 |
| 1.1% | 7 | +0.0072 (0.0027, 0.0126) | -0.0021 (-0.0052, 0.0006) | +0.0071 (0.0027, 0.0125) | -0.0059 (-0.0117, -0.0014) |
| 4.1% | 27 | +0.0279 (0.0189, 0.0371) | -0.0081 (-0.0160, -0.0017) | +0.0267 (0.0180, 0.0354) | -0.0193 (-0.0309, -0.0098) |
| 7.1% | 47 | +0.0469 (0.0369, 0.0576) | -0.0075 (-0.0165, -0.0005) | +0.0450 (0.0359, 0.0551) | -0.0274 (-0.0418, -0.0153) |

The claim judge caught 47/47 fabricated and 31/47 corrupted claims. The gain rises by about 0.65 points of weighted accuracy per point of injected rate. These labels are circular, since the injector creates the fault and the label together. This shows **mechanism sensitivity, not natural error prevalence.** The 14.5% dose was not run.

## Table 4. HaluMem official extraction metrics by condition (natural errors)

| Extractor | Condition | Weighted accuracy | Recall | Weighted recall | Target precision | F1 | Sessions committed |
|---|---|---|---|---|---|---|---|
| Mem0 + DeepSeek (97 sessions) | Unguarded | 0.971 | 0.941 | 0.968 | 0.985 | 0.963 | 97 |
| | Session hold | 0.973 | 0.919 | 0.938 | 0.985 | 0.951 | 83 |
| | Claim drop | 0.973 | 0.940 | 0.966 | 0.985 | 0.962 | 97 |
| | Guard + repair | 0.973 | 0.940 | 0.966 | 0.985 | 0.962 | 97 |
| Mem0 + Llama-3.1-8B (100 sessions) | Unguarded | 0.868 | 0.693 | 0.790 | 0.983 | 0.813 | 100 |
| | Session hold | 0.913 | 0.531 | 0.598 | 0.984 | 0.690 | 69 |
| | Claim drop | 0.891 | 0.691 | 0.785 | 0.985 | 0.812 | 100 |
| | Guard + repair | 0.888 | 0.688 | 0.782 | 0.985 | 0.810 | 100 |

FMR is omitted. Under the reference rule, removing claims makes more interference points score as "resisted", so its rise is an artifact of removal, not evidence of detection.

## Table 5. Paired differences vs unguarded (95% CI)

| Extractor | Condition | Weighted accuracy | Recall | F1 |
|---|---|---|---|---|
| Mem0 + DeepSeek (97) | Session hold | +0.0018 (-0.0033, 0.0077) | **-0.0220 (-0.0392, -0.0095)** | **-0.0118 (-0.0215, -0.0051)** |
| | Claim drop | +0.0019 (-0.0003, 0.0056) | -0.0011 (-0.0042, 0.0020) | -0.0007 (-0.0023, 0.0009) |
| | Guard + repair | +0.0019 (-0.0003, 0.0056) | -0.0011 (-0.0042, 0.0020) | -0.0007 (-0.0023, 0.0009) |
| Mem0 + Llama-3.1-8B (100) | Session hold | +0.0447 (0.0231, 0.0678) | **-0.1620 (-0.2493, -0.0808)** | **-0.1231 (-0.2031, -0.0578)** |
| | Claim drop | **+0.0226 (0.0133, 0.0332)** | -0.0026 (-0.0125, 0.0084) | -0.0011 (-0.0080, 0.0064) |
| | Guard + repair | **+0.0204 (0.0096, 0.0339)** | -0.0057 (-0.0106, -0.0011) | -0.0034 (-0.0069, -0.0004) |

User split for guard + repair, weak extractor (descriptive, nothing tuned):

| | Weighted accuracy | Recall |
|---|---|---|
| Tuning users (70 sessions) | +0.026 (0.011, 0.045) | -0.008 (-0.015, -0.002) |
| Final users (30 sessions) | +0.007 (0.001, 0.015) | 0.000 (-0.006, 0.004) |

Both halves are positive, but the size depends on the users. The split was not computed for claim drop.

**Why session hold costs recall.** In the strong-extractor run, the hold removed 230 claims, of which only 20 (8.7%) were flagged. Each held session writes nothing, which cost 41 recalled gold points (2.2% of 1,862). Claim drop keeps the unflagged claims and loses only 2 of those. Any guard that decides per session behaves this way. On the weak extractor, the hold's larger accuracy gain comes only from discarding 31 whole sessions.

## Table 6. Judge validity: two blind reviewers, frozen rubric

| Check | Flagged claims | Flag precision, ifan | Flag precision, nabhan | Miss rate (unflagged claims called unsupported) | Inter-rater agreement |
|---|---|---|---|---|---|
| Mem0 + DeepSeek, 36 claims | 8 | 1/8 = 12.5% (2, 47) | 0/8 = 0% (0, 32) | 0/28 and 1/28 | 34/36, kappa -0.03 |
| Mem0 + Llama-3.1-8B, 36 claims | 28 | 13/27 = 48% (31, 66) | 9/23 = 39% (22, 59) | 1/8 and 1/8 | 30/30 decisive, kappa 1.0 (6 unsure or blank) |
| Second claim judge (particle-1), Mem0 + DeepSeek sample | 6 | 0/6 | 0/6 | - | - |

Agreement between the claim judge and the official accuracy judge: the official judge scored 72% of the claim judge's flags on the weak extractor below 2 (60, 81), against 21% of unflagged claims. On the strong extractor the figures are 20% (8, 42) against 5.4%. Both judges are DeepSeek, so this agreement is not independent validation.

Reading:
- **Strong extractor:** the flags are almost never confirmed by people.
- **Weak extractor:** fewer than half of the flags are confirmed, although the two reviewers agree with each other completely on the items both decided.
- **What this means for the gain:** part of the weak-extractor accuracy gain comes from removing claims that reviewers find acceptable. It is a gain on a model-judged metric, not a human-confirmed reduction of hallucinations.

## Table 7. Downstream checks

| Benchmark | Setting | Unguarded | Guard | Guard + repair | Test |
|---|---|---|---|---|---|
| LongMemEval (oracle split, 30 questions, 5 per type) | Accuracy, benchmark judge | 0.600 | 0.533 | 0.600 | Guard vs unguarded: McNemar p = 0.5 (0 vs 2 discordant); guard + repair: 0 discordant |
| MemoryAgentBench FactConsolidation 6k, single-hop (100 questions) | Substring exact match | 1.00 (0.96, 1.00) | 0.26 (0.18, 0.35) | 0.52 (0.42, 0.62) | Unguarded beats guard (74 vs 0, p < 0.001) and repair (48 vs 0) |
| MemoryAgentBench FactConsolidation 6k, multi-hop (100 questions) | Substring exact match | 0.13 (0.08, 0.21) | 0.00 (0.00, 0.04) | 0.09 (0.05, 0.16) | Unguarded vs guard p = 0.0002; vs repair p = 0.45 |

On MemoryAgentBench, the reader already resolves conflicts using the facts' serial numbers, so single-hop is at ceiling. Holding a conflicting new fact keeps the stale value, which is wrong by design in a latest-wins task. The guard there is an LLM conflict verifier, not MemBud's structural verifier, and the verifier's detections were not reviewer-validated.

## Table 8. Pre-registered rubric-aligned claim-judge prompt (negative result)

| | Original judge | Rubric-aligned judge |
|---|---|---|
| Claims flagged (weak extractor, 1,259) | 68 | 93 |
| Flagged by both | 67 | 67 |
| Flagged by this judge only | 1 | 26 |
| On the 36 reviewer-labelled claims | 13/27 and 9/23 confirmed | identical flags, so identical precision |

The prompt was written to follow the frozen rubric and committed before any of its outputs existed (commit `6adcf12`). It did not reduce false flags. The 26 extra flags are mostly speculative claims; they have not been reviewer-labelled. No further prompt change was made, because iterating on prompts after seeing their outputs would be tuning on data that include final-split users.

## Threats to validity

1. **Same-family judges.** The claim judge chooses what to remove, and the official accuracy judge scores the result. Both are DeepSeek. The benchmark-standard GPT-4o could not be used.
2. **Flags only partly human-confirmed.** The strong-extractor flags were confirmed at 1/8 and 0/8, the weak-extractor flags at 39 to 48%. These samples are small (8 and 28 flagged claims), with wide intervals.
3. **Circular injected labels.** Tables 3 and Phase 1 show mechanism sensitivity only.
4. **Which guard was tested.** It is the LLM claim-judge guard, evaluated offline on recorded proposals. It is not a per-session re-run of the structural verifier, and not a live memory system. Repair is close to deletion: on the strong extractor it made 21 removals and 1 rewrite.
5. **Scope.** One memory system (Mem0 2.2.1), first sessions of 20 HaluMem-Medium users. Mem0's `add()` emitted no UPDATE or DELETE events, so natural omission and corruption of prior records were not observed and could not be tested.
6. **Metrics not computed.** HaluMem update and QA metrics were not computed, because retrieval and answers were not recorded. LongMemEval was a 30-question oracle pilot with no distractor sessions.
7. **The split is descriptive.** The weak-extractor gain is smaller on the final users (+0.7 points) than on the tuning users (+2.6 points).

## Claims the data supports

- Graph verification gives no advantage over a fair text verifier on any tested case.
- The guard detects injected faults. On the official metric, its weighted-accuracy gain rises with the injected error rate.
- On Mem0 with a strong model, the guard has no measurable effect on HaluMem's official extraction metrics. Holding whole sessions costs about 2 points of recall. Claim drop and repair are indistinguishable from unguarded.
- On Mem0 with a weaker model (unselected, 100 sessions), dropping flagged claims raises the DeepSeek-judged weighted accuracy by +2.3 points (95% CI 1.3 to 3.3), with no significant recall or F1 cost. This is **not validated**: fewer than half of the removed claims were confirmed unsupported by reviewers, and the metric judge shares a model family with the claim judge.
- Session-level holding is not a useful operating point: it costs 2 to 16 points of recall.
- On MemoryAgentBench conflict resolution, an LLM conflict-hold guard lowered accuracy. Repair reduced the harm but did not remove it.

## Claims the data does not support

- "MemBud reduces hallucinated memory" (no human-validated reduction).
- Any reduction of omissions or corruption of prior records on natural data (none observed to reduce).
- Any graph-specific benefit.

## Not done

- An independent official judge (GPT-4o or another model family) for the extraction metrics.
- Reviewer labels for the 26 extra rubric-judge flags. A fresh quiz was designed (`scripts/halumem_dual_review.py build-compare`) but not run, by decision.
- Claim drop rescored with the rubric-aligned judge's verdicts.
- The 14.5% injected dose.
- HaluMem update and QA metrics.
- LongMemEval S/M with distractor sessions.
- MemoryAgentBench 32k/64k/262k and its other competencies.
- Reviewer validation of the MemoryAgentBench verifier's conflict detections.
- A memory system that updates and deletes.

## Sources and reproducibility

- **Detailed report:** `docs/guard-effect-report.md`; Phase 1 numbers are in `docs/halumem-evaluation.md`.
- **Scripts:**

  | Purpose | Scripts |
  |---|---|
  | Official HaluMem scoring | `scripts/score_halumem_official.py` |
  | Extraction and claim judging | `scripts/run_halumem_claim_judge.py`, `scripts/run_halumem_eager.py` |
  | Repair | `scripts/run_halumem_repair.py` |
  | Injected faults and dose-response | `scripts/run_halumem_inject.py`, `scripts/halumem_dose_response.py` |
  | Reviewer quizzes | `scripts/halumem_dual_review.py` |
  | MemoryAgentBench | `scripts/run_memagentbench.py` |
  | LongMemEval | `scripts/run_longmemeval.py` |

- **Seeds:** sample 20261121, bootstrap 20261122, injection and weak-extractor quiz 20261123.
- **Run data:** run directories under `data/halumem-runs/` and `data/memagentbench-runs/` are gitignored. They contain dataset text and are not committed.
- **Spend:** estimated from token counts at peak list price. Real billing has been about a fifth of that, which is unverified.
