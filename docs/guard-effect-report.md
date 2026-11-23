# Does MemBud's guard change results? HaluMem and LongMemEval pilots

Date: 2026-11-22. **Status: the judge now has a two-reviewer check, but its weight is limited.** Every number still depends on an LLM judge (DeepSeek `deepseek-flash`, not the benchmarks' GPT-4o). Two reviewers (ifan, nabhan) labeled the same 36 fresh claims blind under the frozen rubric (`docs/halumem-grounding-rubric.md`); see the judge validity check below. Results are not described as validated: the check shows the judge's UNSUPPORTED flags are almost never confirmed, and the reviewers do not agree with each other on the unsupported class.

## Verdict

| Question | Verdict (this data, this judge) |
|---|---|
| HaluMem: guard alone vs unguarded | **Hurt, slightly.** Recall -2.2 points (95% CI [-3.9, -1.0]); no precision gain. |
| HaluMem: guard + repair vs unguarded | **No measurable difference.** Recall -0.1 point [-0.4, +0.2]; F1 -0.001 [-0.002, +0.001]. |
| HaluMem: does the guard improve anything? | **No measurable benefit.** Target precision is flat (every difference within +-0.002); the FMR rise is an artifact (below). |
| LongMemEval pilot (30 questions): guard or guard+repair vs unguarded | **No measurable effect.** Guard -6.7 points (2 questions, McNemar p = 0.5); guard+repair 0.0 points (0 discordant). |

"No measurable effect" is the honest outcome for guard+repair on both benchmarks. The guard alone costs a small amount of recall on HaluMem. Nothing here shows the guard improving answers.

## HaluMem (official extraction metrics)

**Sample.** 97 recorded Mem0 2.2.1 proposals (HaluMem-Medium, revision `cb04336`), 20 users x first up to 5 sessions, 1,286 extracted memories, 1,933 gold points scored (including 71 interference points), 108 update points excluded. The scorer re-implements the reference `aggregate_eval_results` (MemTensor/HaluMem commit `d653749`); a differential test compares it with the reference code on randomized records.

**Conditions.**
- `unguarded`: every Mem0 ADD committed.
- `guard`: a session is held if the judge marks any added claim unsupported (14 of 97 held; same decisions as the verifier with the opt-in reply-context check, which is merged default-off). A held session commits nothing, and per the reference rule an empty extraction scores 0 on every gold point.
- `guard_repair`: for held sessions the repair loop (`src/membud/repair.py`, <= 2 retries) is run; the repaired claims are committed only if they pass verification and re-verification.

| Condition | Recall | Weighted recall | FMR* | Target precision | F1 | Sessions committed |
|---|---|---|---|---|---|---|
| Unguarded | 0.941 | 0.968 | 0.310 | 0.985 | 0.963 | 97 |
| Guard | 0.919 | 0.938 | 0.535 | 0.985 | 0.951 | 83 |
| Guard + repair | 0.940 | 0.966 | 0.394 | 0.985 | 0.962 | 97 |

\*FMR is inflated by holding: with nothing written, every distractor is "resisted" under the reference rule. It is not evidence of detection.

Paired difference, session-level bootstrap (2,000 resamples), 95% CI:

| Metric | Guard - unguarded | Guard+repair - unguarded | Guard+repair - guard |
|---|---|---|---|
| Recall | -0.022 [-0.039, -0.010] | -0.001 [-0.004, +0.002] | +0.021 [+0.009, +0.038] |
| Weighted recall | -0.029 [-0.052, -0.014] | -0.001 [-0.003, +0.001] | +0.028 [+0.013, +0.050] |
| Target precision | -0.0004 [-0.002, +0.001] | -0.0002 [-0.0004, -0.0001] | +0.0002 [-0.002, +0.002] |
| F1 | -0.012 [-0.021, -0.005] | -0.001 [-0.002, +0.001] | +0.011 [+0.005, +0.020] |
| FMR | +0.225 [+0.106, +0.359] | +0.085 [+0.017, +0.167] | -0.141 [-0.242, -0.056] |

**Held-write omission cost.** Holding without repair loses about 2.2 points of recall (14 sessions written as nothing). Repair recovers 14 of 14 held sessions, each on the first retry.

**What repair actually did.** It removed 21 flagged claims, rewrote 1, and kept all other claims except 1. It is close to deletion of what the judge flagged. A 100% retry success rate is therefore weak evidence of repair skill; it mostly shows the repaired list no longer contains claims the same judge dislikes. Mem0 itself was not repaired: the extractor is a DeepSeek re-prompt.

**Tuning/final split (descriptive).** Users were split by seeded hash into tuning (14 users, 67 sessions, 11 held) and final (6 users, 30 sessions, 3 held). Nothing was tuned on either half: every rule was fixed before these results existed. Direction is the same in both halves; the final half is underpowered (intervals touch zero). Recall, guard - unguarded: tuning -0.026 [-0.048, -0.010], final -0.013 [-0.039, 0.000]. Recall, guard+repair - unguarded: tuning -0.003 [-0.008, 0.000], final +0.004 [0.000, +0.011].

**Not computed.** HaluMem update metrics and QA metrics: they need Mem0 retrieval per update point and generated answers, which were not recorded. The pilot also covers only the first sessions of each user, so update and QA numbers would be biased even if recorded.

## LongMemEval pilot

**Sample.** 30 questions (5 per type, seeded) from `longmemeval_oracle.json`, i.e. the evidence sessions only (52 sessions, 501 extracted claims). The oracle file has no distractor sessions, so this tests ingestion quality, not retrieval over noise. Pipeline: extractor -> claim judge -> {store all | drop held sessions | repair held sessions} -> top-10 lexical retrieval -> reader -> the benchmark's own `get_anscheck_prompt` judge (read from a clone of xiaowu0162/LongMemEval, commit `9e0b455`).

| Condition | Overall accuracy (= task-averaged here) | Held sessions | Recovered |
|---|---|---|---|
| Unguarded | 0.600 (18/30) | - | - |
| Guard | 0.533 (16/30) | 5 of 52 | - |
| Guard + repair | 0.600 (18/30) | 5 of 52 | 5 of 5 |

Per type (5 questions each): the only differences are guard on knowledge-update (0.8 vs 1.0) and single-session-user (0.8 vs 1.0). Paired vs unguarded: guard has 0 questions only it got right, 2 only unguarded got right (McNemar p = 0.5); guard+repair has 0 discordant. 30 questions cannot detect effects of this size.

## Judge validity check (two reviewers, same 36 claims, frozen rubric)

Sample: 36 claims never labeled before (8 the judge flagged UNSUPPORTED, 28 it called SUPPORTED), seeded draw; rubric SHA-256 pinned in the manifest. Both reviewers labeled all 36 decisively (0 UNSURE) and labeled alone. Offline, $0.

| | ifan | nabhan |
|---|---|---|
| Judge-flagged claims the reviewer also marked UNSUPPORTED (flag precision) | 1 of 8 (12.5%, Wilson 95% [2%, 47%]) | 0 of 8 (0%, [0%, 32%]) |
| Judge-supported claims the reviewer marked UNSUPPORTED (miss rate) | 0 of 28 ([0%, 12%]) | 1 of 28 (3.6%, [0.6%, 18%]) |
| Agreement with the judge | 29/36 (80.6%, [65%, 90%]) | 27/36 (75.0%, [59%, 86%]) |

**Inter-rater agreement between the two reviewers:** 34 of 36 (94.4%, Wilson [82%, 98%]); **Cohen's kappa = -0.03**. The raw agreement is high only because nearly everything is SUPPORTED. Each reviewer marked exactly one claim UNSUPPORTED, and they were different claims, so the reviewers do not agree on the class that matters (kappa about zero).

**Does the guard's effect flip between reviewers?** No flip in direction. Under both reviewers' labels the judge's flags are overwhelmingly not confirmed (1/8 and 0/8; the intervals overlap). The guard's holds are therefore mostly driven by claims that neither human reviewer calls unsupported. That matches the HaluMem result: holding costs recall and gains no precision, because the removed claims were mostly fine. It is claim-level evidence: with only 8 flagged claims the intervals are wide, and claim labels cannot give a session-level guard effect (that needs every claim in a proposal labeled).

**What this does and does not establish.** The pre-registered condition (two reviewers complete, kappa reported) is met. But the labels cannot serve as ground truth for unsupported claims: the reviewers essentially find none in this sample, so we learn the judge's flags are mostly false alarms, not that it detects real hallucinations. Earlier audits (different claims and reviewers) gave 17% flag precision under one reviewer and 91.5% agreement under another; this check is consistent with the low flag precision.

## Cost and failures

Estimated spend this session (peak list price, cache miss, **estimates not bills**; earlier runs billed about 1/5 of peak, user-reported and unverified):

| Run | Est. spend |
|---|---|
| HaluMem official judge, first pass (stopped at $1.00 cap) | $1.00 |
| HaluMem official judge, resumed | $1.43 |
| Repair loop (14 sessions) | $0.05 |
| Judge on repaired memories | $0.04 |
| LongMemEval pilot | $0.15 |
| Total | about $2.67 |

Failures: 0 judge errors across 3,523 HaluMem judge calls, 0 extractor errors in the repair run, no API failures in the LongMemEval pilot. Held sessions: 14 of 97 (HaluMem), 5 of 52 (LongMemEval).

## Limitations (read before citing any number)

- **Judge weakly checked, not GPT-4o.** Official metrics use the repository's prompts with deepseek-flash. The judge both supplies guard flags and scores the result, so results are circular. The two-reviewer check (36 claims, 8 flagged) found the judge's flags are mostly not confirmed and the reviewers disagree on which claims are unsupported (kappa -0.03); it supports the reading that flags are mostly false alarms, not a detection rate.
- **Guard = judge-claim guard**, not a per-session re-run of the structural verifier. Re-verification of an unchanged prompt is served from cache.
- **One extractor family, one dataset slice.** Mem0 2.2.1 on 20 HaluMem users; a custom DeepSeek ingestion pipeline on LongMemEval (the benchmark has no memory system of its own). Not a leaderboard number.
- Lexical retrieval; LongMemEval judge reply requested as JSON.
- LongMemEval used the oracle split, 30 questions, one seed.
- Graph vs text verifier ties on every case, so no graph advantage is claimed.

## Not done / not tested

- A session-level guard effect under reviewer labels (needs every claim in a proposal labeled by both reviewers) and a larger flagged-claim sample (only 8 fresh flagged claims existed).
- HaluMem update metrics and QA accuracy.
- LongMemEval S/M (distractor haystacks) and any run beyond 30 questions; GPT-4o judging.
- Extension beyond the 97-session pilot (not done because the pilot showed no benefit to chase).
- Verification of the paper's own metric text beyond the reference code.

## Why the guard hurts recall (HaluMem, 97 sessions)

Decomposition from the cached official judge scores (offline):

- The guard holds 14 sessions containing **230** extracted claims, of which only **20** (8.7%) were flagged. Holding discards the other 210.
- Each held session writes nothing, so every gold point scores 0. That costs **41** gold points the unguarded run had recalled: 2.2% of the 1,862 non-interference gold points, which is the -2.2 point recall difference.
- Repair keeps unflagged claims and drops only the flagged ones. It loses just **2** of those 41 net. So the flagged claims carried almost none of the recall; the loss is the collateral of an all-or-nothing, per-session decision (Mem0 proposes about 13 claims per session, so one flagged claim holds the rest).
- This is structural: any per-session guard with a nonzero flag rate behaves this way. It would be worth the cost only if the flags were mostly real hallucinations; the two-reviewer check found they mostly are not (1/8 and 0/8 confirmed).
- Inspection of 8 of the 20 flagged claims (not checked against the dialogue): mostly assistant-introduced or inferred facts (income and health-status changes, a diagnosis, a new medication), the case the accepted-assistant-facts rule is meant to cover.

This explains the mechanism with the same unvalidated judge; it is not new evidence about the judge.

## Second-judge replication (different model family, free tier)

The claim-level guard judge was re-run on all 97 proposals with `particle-ai/particle-1:free` (OpenRouter free endpoint, $0, 97 requests, 0 failures), same prompt and accepted-assistant-facts policy as DeepSeek. `google/gemma-4-31b-it:free` and other free models were tried first but were rate-limited upstream (HTTP 429) or failed, so this is the only free model that served. Its provenance and quality are not independently known, so treat it as a robustness check, not a better judge. HaluMem text was sent to a free endpoint (owner confirmation 2026-11-22). Script: `scripts/halumem_second_judge.py` (not unit-tested).

| Quantity | DeepSeek | particle-1 |
|---|---|---|
| Claims flagged UNSUPPORTED (of 1,286) | 20 | 34 (17 shared with DeepSeek) |
| Sessions the guard would hold (of 97) | 14 | 23 (includes all 14 DeepSeek holds) |
| Claim-level agreement with DeepSeek | - | 1,266/1,286 (98.4%, Wilson [97.6%, 99.0%]) |
| Recalled gold points lost to holding | 41 (2.2% of 1,862) | 77 (4.1% of 1,862) |
| Flags confirmed by reviewers in the 36-claim sample | ifan 1/8, nabhan 0/8 | ifan 0/6, nabhan 0/6 |

Reading: the verdict replicates across model families. A second judge also holds sessions on claims neither reviewer confirms as unsupported (0 of 6 under each reviewer), and the recall cost of holding is **larger**, not smaller (4.1 points), because this judge is stricter. The guard's cost does not look specific to DeepSeek. Limits: agreement is high mostly because about 98% of claims are supported; the reviewer check covers only 6 flagged claims; official-metric scores are still the cached DeepSeek scores (only the guard decisions come from the second judge); repair and LongMemEval were not re-run with this judge; the model is unvalidated.

## Claim-level guard (drop flagged claims instead of holding the session)

The session-level guard discards every claim in a held session (about 210 supported claims to remove 20 flagged ones). The claim-level guard (`guard_claim_drop`) drops only the judge-flagged claims and commits the rest. Every gold point in the 14 affected sessions was re-scored against the kept claims (94 new judge calls on DeepSeek; accuracy scores for kept claims are reused). Nothing else changed: same 97 sessions, same judge, same cached scores. Judge is DeepSeek, not GPT-4o, and unvalidated.

| Metric (97 sessions) | Unguarded | Session hold | Claim drop | Claim drop minus unguarded (paired 95% CI) |
|---|---|---|---|---|
| Recall (all) | 0.9409 | 0.9189 | 0.9398 | -0.0011 (-0.0042, 0.0020) |
| Weighted recall (all) | 0.9675 | 0.9382 | 0.9664 | -0.0011 (-0.0031, 0.0007) |
| Target precision (all) | 0.9852 | 0.9849 | 0.9850 | -0.0002 (-0.0004, -0.0001) |
| F1 | 0.9626 | 0.9507 | 0.9619 | -0.0007 (-0.0023, 0.0009) |
| FMR (all) | 0.3099 | 0.5352 | 0.3803 | +0.0704 (0.0128, 0.1408) |

* The recall cost shrinks from 2.2 points (session hold) to 0.1 points (CI includes 0). Most of the earlier recall loss came from the all-or-nothing hold, not from the judge's flags.
* Target precision does not improve. The flagged claims scored as accurate under the official accuracy judge, which matches the reviewers (flag precision 1/8 and 0/8). The small decrease is the removal of accurate claims.
* FMR rises 7 points, but this is the artifact documented above: removing claims makes more interference gold points score 0. It is not evidence of benefit.
* Verdict for this condition: no measurable benefit on the official metrics and a negligible cost.

## Higher-error extractor attempts (step 3): no usable error rate

Mem0 produces a judge-flagged unsupported rate of 1.6% (20 of 1,286 claims). To test whether the guard helps when errors are common, two other extractors were run on the same 97 sessions with the same batch claim judge and policy. Scripts: `scripts/run_halumem_eager.py`. Prior memory was copied from the recorded Mem0 transitions; the reply-context quote check was not applied.

| Extractor | Claims | Flagged unsupported | Sessions with a flag |
|---|---|---|---|
| Mem0 (recorded) | 1,286 | 20 (1.6%) | 14 |
| DeepSeek, over-inferring prompt | 1,722 | 20 (1.2%) | 15 |
| `particle-ai/particle-1:free`, neutral prompt | 2,740 | 0 (0%) | 0 |

Neither extractor produced more flagged claims, so there is nothing for a guard or repair loop to act on, and these runs were **not** scored with the official metrics (the guard would hold 0 to 15 sessions, about the same as the Mem0 case). The judge could be too lenient toward inferred claims; the reviewers' labels (1/8 and 0/8 flag precision) do not rule that out. This attempt therefore does not answer whether guard and repair help at high error rates. Spend for step 2 and 3 was about $0.45 of the $2.00 cap (estimated from token counts); the free extractor cost $0.

## Not done in this round
* No validated labels exist for the claim-level guard; its drop decisions rest on the unvalidated DeepSeek judge.
* A higher-error condition was not achieved (see above). Fault injection (already reported) is the only setting with a measurable error rate.
* The two extra extractors were not scored with the official metrics, repaired, or run through LongMemEval.

## Guard on injected faults, official metrics (47 sessions)

Built by `scripts/run_halumem_inject.py` from the recorded Mem0 extractions: in a seeded 50% of the 97 sessions (47 selected) one extracted claim was replaced by a corrupted version (phase-1 injector, `_corrupt_claim_text`) and one hallucinated claim was appended (`_pick_hallucinated_claim`), 94 injected claims in all. Only these 47 sessions were scored (`--only-injected`; spend). Same DeepSeek claim judge and official judge prompts, same repair loop (max 2 retries). **These faults are synthetic and the injected labels are circular by construction: this shows what the guard does when errors exist, not how often they occur naturally. Official-metric judge is DeepSeek, unvalidated.**

Claim judge on the injected claims: 47/47 hallucinations flagged, 31/47 corruptions flagged. Every injected session therefore contains a flag, so the session-level guard holds all 47 (recall 0, a degenerate result; not reported further). Repair recovered all 54 held sessions (including 7 held for Mem0's own natural flags), 58 attempts; 0/47 hallucinations and 12/47 corruptions remain in the repaired output (the unflagged ones the judge missed).

| Metric (47 injected sessions) | Unguarded | Claim drop | Guard + repair | Repair minus unguarded (paired 95% CI) |
|---|---|---|---|---|
| Weighted accuracy (all) | 0.8757 | 0.9600 | 0.9641 | **+0.0884 (0.0764, 0.1029)** |
| Recall (all) | 0.9384 | 0.8814 | 0.9244 | -0.0140 (-0.0325, -0.0010) |
| Weighted recall (all) | 0.9645 | 0.9192 | 0.9599 | -0.0047 (-0.0146, 0.0038) |
| Target precision (all) | 0.9836 | 0.9835 | 0.9833 | -0.0004 (-0.0030, 0.0013) |
| F1 | 0.9605 | 0.9296 | 0.9529 | -0.0075 (-0.0174, -0.0004) |

Claim drop minus unguarded: weighted accuracy +0.0843 (0.0734, 0.0979), recall -0.0570 (-0.0839, -0.0354).
User-split (descriptive; nothing tuned on either half): weighted accuracy gain of repair is +0.0833 (0.0697, 0.0998) on the 33 tuning-half sessions and +0.1031 (0.0806, 0.1329) on the 14 final-half sessions.

Reading:
* With injected errors the guard **helps on the official metric that penalizes false memories**: weighted accuracy rises by about 8 to 9 points, consistent across both halves. The gain exists mainly because the guard removes the injected claims the official judge scores as wrong; it is the mechanism result in official-metric form.
* Target precision does not move: it only counts claims flagged as included in the gold memories, so injected fabrications do not enter it. Do not read the flat precision as "no benefit".
* Recall drops. Corruptions replace real claims by construction, so removing them loses recall; plain claim drop pays 5.7 points, repair recovers most of it (1.4 points). The loss is partly an artifact of how corruption was injected.
* The same weighted-accuracy metric on Mem0's natural output (97 sessions) changes by +0.0019 (-0.0003, 0.0056): no measurable effect.
* Together: the guard-and-repair pipeline works when errors exist at roughly 7% of claims, and has no measurable effect at Mem0's natural 1.6% flagged rate. Whether natural errors are frequent enough in other systems or datasets to matter was not tested.

Spend (estimated from token counts): injection and judging $0.06, repair $0.12, official scoring $0.85; with step 2 and the extractor attempts (about $0.45) the total is about $1.5 of the $2.00 cap.

Not done: 16/47 injected corruptions were not flagged by the judge and the 12 that survive repair were not examined individually; injected-fault results were not run through LongMemEval; no reviewer labels exist for injected claims (the injector's label is the only label).

## HaluMem-Long: Mem0 flagged-claim rate (30 sessions)

Question: do longer sessions raise Mem0's error rate enough for a guard to matter? Real Mem0 2.2.1 on DeepSeek `deepseek-flash`, HaluMem-Long, seed 20261121, 10 users x first 3 sessions (30 sessions; Long sessions are about 2.4x longer than Medium, median 33k vs 14k characters), then the same batch claim judge and policy as on Medium (`scripts/run_halumem_claim_judge.py`; the reply-context quote check was not applied). Official metrics were **not** run on Long: the official accuracy judge sends the whole dialogue per claim, which exceeded the budget.

| | Claims | Judge-flagged unsupported | Rate (Wilson 95%) | Sessions with a flag |
|---|---|---|---|---|
| Mem0, Medium (97 sessions) | 1,286 | 20 | 1.6% | 14/97 |
| Mem0, Long (30 sessions) | 443 | 5 | 1.1% (0.5%, 2.6%) | 2/30 |

Longer sessions did **not** raise the flagged rate: 1.1% is within the Medium rate's range. The flagged claims are mostly "the assistant told the user X" summaries of the assistant's own answers, the same type as on Medium. The same caveats apply: the judge is DeepSeek, unvalidated, and reviewers confirmed only 1/8 and 0/8 of its Medium flags. With about 1% flagged, a guard has almost nothing to act on here either, so HaluMem-Long does not provide the higher-error condition RQ1(a) needs.

Spend (estimated from token counts): Mem0 extraction $0.18, claim judge $0.09; about $0.27 of the $0.50 cap. Not done: official metrics, guard and repair on Long, and a larger Long sample. This 30-session sample is the first three sessions of 10 users, not a random sample of sessions; sessions later in a user's history were not tested.

## MemoryAgentBench, Conflict Resolution (FactConsolidation, 6k variant)

Harness: `src/membud/memagentbench.py`, `scripts/run_memagentbench.py` (reference clone commit 538026089d1a, scoring and the FactConsolidation RAG prompt read from the clone; data in `~/Repositories/MemoryAgentBench-data`, not committed). The context is a numbered list of 455 facts; a later fact overrides an earlier one about the same subject and relation. Facts were written in chunks of 20 into three stores: **unguarded** (append everything), **guard** (a verifier reports a new fact that contradicts a still-active older one; the new fact is held, not stored), **guard_repair** (bounded repair loop, up to 2 retries, supersedes the stale record; if every retry fails it falls back to the guard behaviour). Retrieval is lexical top-20; the reader is DeepSeek with the benchmark's own prompt; accuracy is the benchmark's substring exact match. Settings (chunk 20, k=20, 2 retries, all 100 questions per source) were fixed before the paid run; nothing was tuned. Verifier, repair chooser and reader are the same model (DeepSeek, not the benchmark's GPT-4o-mini); verifier detections are not reviewer-validated.

| Accuracy (100 questions each, Wilson 95%) | Unguarded | Guard | Guard + repair |
|---|---|---|---|
| Single-hop (`fact_sh_6k`) | 1.00 (0.96, 1.00) | 0.26 (0.18, 0.35) | 0.52 (0.42, 0.62) |
| Multi-hop (`fact_mh_6k`) | 0.13 (0.08, 0.21) | 0.00 (0.00, 0.04) | 0.09 (0.05, 0.16) |

Paired exact McNemar: single-hop, unguarded beats guard (74 vs 0 discordant, p<0.001) and guard+repair (48 vs 0, p<0.001); repair beats guard (26 vs 0). Multi-hop: unguarded vs guard 13 vs 0 (p=0.0002); unguarded vs repair 10 vs 6 (p=0.45, no difference); repair vs guard 9 vs 0.

**Verdict on this benchmark: the guard hurt; repair reduced but did not remove the harm; no measurable gain from either.**

Why (checked from the cached run, no new calls):
* The unguarded baseline is already at ceiling on single-hop (100%): the reader sees both the stale and the new fact with their serial numbers and picks the newer, so there is nothing for a guard to fix. Multi-hop fails for every condition because single-pass lexical retrieval cannot reach facts about an intermediate entity the question does not name; that failure is not about conflicts.
* Holding a conflicting new fact keeps the stale value active and loses the new one (157 new facts dropped, 21 of 23 chunks held), which is wrong by design in a latest-wins task. Repair recovered 10 of 21 held chunks (66 supersessions, of which 64 target a fact that has a later same-subject fact; 97 new facts still dropped).
* The 11 failed repairs: after the nearest stale record is superseded, the verifier's top-5 related-fact window surfaces further older conflicting facts on the same subject (several older values per subject), so two retries are not enough; in one inspected case the verifier kept reporting a pair the repair step would not supersede. The verifier also produces false positives (for example reporting "Narendra Modi is a citizen of Australia" against "Joe Hockey is a citizen of Australia", which is not a contradiction). These are limits of this verifier and window, not of the idea; a larger window or more retries was not tried (settings were fixed in advance).

What this does and does not show: it shows that an LLM conflict-hold guard added to an append-only store does not improve a strong reader on this task and costs accuracy. It does not show that MemBud's structural verifier would do the same (this verifier is an LLM prompt, not MemBud's), and it does not test the longer variants (32k, 64k, 262k) where retrieval gets harder. Spend (estimated from token counts): about $0.13 of the $0.60 cap. Not done: 32k/64k/262k variants, other competencies (accurate retrieval, long-range understanding, test-time learning), a larger verifier window or more retries, validation of the verifier's detections.

## Dose-response of the guard on injected faults (97 sessions)

`scripts/halumem_dose_response.py` (offline, cached scores only). Injection is seeded and nested, so each dose is a subset of the next: at dose t, sessions with seeded u < t use their injected version (two faults each: one corrupted claim, one fabricated claim) and the rest use the recorded Mem0 output. Paired bootstrap over the same 97 sessions, 1,000 resamples. Faults are synthetic and the injected labels are circular; official-metric judge is DeepSeek, unvalidated. The 14.5% dose (all 97 sessions injected) was **not run**: its official scoring did not fit the budget.

| Injected claim rate | Sessions injected | Guard+repair: weighted accuracy (95% CI) | Guard+repair: recall | Claim drop: weighted accuracy | Claim drop: recall |
|---|---|---|---|---|---|
| 0 (Mem0 natural, 1.6% judge-flagged) | 0 | +0.0019 (-0.0003, 0.0058) | -0.0011 (-0.0041, 0.0018) | +0.0019 | -0.0011 |
| 1.1% | 7 | +0.0072 (0.0027, 0.0126) | -0.0021 (-0.0052, 0.0006) | +0.0071 (0.0027, 0.0125) | -0.0059 (-0.0117, -0.0014) |
| 4.1% | 27 | +0.0279 (0.0189, 0.0371) | -0.0081 (-0.0160, -0.0017) | +0.0267 (0.0180, 0.0354) | -0.0193 (-0.0309, -0.0098) |
| 7.1% | 47 | +0.0469 (0.0369, 0.0576) | -0.0075 (-0.0165, -0.0005) | +0.0450 (0.0359, 0.0551) | -0.0274 (-0.0418, -0.0153) |

The weighted-accuracy gain rises about 0.65 points per point of injected rate (0.7, 2.8, 4.7 at 1.1%, 4.1%, 7.1%), and the recall cost of repair stays under 1 point. There is no break-even rate in this range: the gain exceeds zero from the lowest dose, because the judge catches the injected faults easily (47/47 fabricated, 31/47 corrupted). Natural Mem0 output, whose judge flags are mostly not confirmed by reviewers (1/8 and 0/8), sits at the no-effect end. This says the guard pays whenever the judge's flags are real errors; it cannot say how often they are.

## A naturally weaker extractor: Mem0 with Llama-3.1-8B (100 sessions, same sample)

The prompt tricks in the earlier extractor attempts did not raise the error rate, so Mem0 itself was run with a weaker model: `meta-llama/llama-3.1-8b-instruct` through OpenRouter (paid, about $0.02 per million input tokens; data-policy confirmed by the project owner), same 20 users x 5 sessions sample and seed as the Medium Mem0 run (100 sessions, 1,265 claims). Judged with the same DeepSeek batch claim judge and policy (`scripts/run_halumem_claim_judge.py`; no reply-context quote check).

| Extractor | Claims | Judge-flagged | Rate (Wilson 95%) | Sessions with a flag |
|---|---|---|---|---|
| Mem0 + DeepSeek (recorded) | 1,286 | 20 | 1.6% (1.0%, 2.4%) | 14/97 |
| Mem0 + Llama-3.1-8B | 1,259 | 68 | **5.4% (4.3%, 6.8%)** | 31/100 |

The flagged claims are mostly elaborated reasons or consequences the user never stated ("User dislikes parrots because the constant noise ... "). This is a natural, higher error rate, 3.5 times the DeepSeek rate.

Official metrics were scored only on the 31 sessions with a flagged claim (the guard changes nothing elsewhere; the other 69 sessions were not scored, so no pooled number is given). Repair recovered 31 of 31 held sessions (33 attempts). Paired bootstrap, 31 sessions from 17 users, 2,000 resamples:

| (31 flagged sessions) | Unguarded | Claim drop | Guard + repair | Repair minus unguarded (95% CI) |
|---|---|---|---|---|
| Weighted accuracy (all) | 0.7905 | 0.8456 | 0.8400 | **+0.0495 (0.0239, 0.0818)** |
| Recall (all) | 0.6011 | 0.5916 | 0.5802 | -0.0210 (-0.0408, -0.0058) |
| Weighted recall (all) | 0.7118 | 0.6908 | 0.6823 | -0.0296 (-0.0449, -0.0171) |
| Target precision (all) | 0.9807 | 0.9882 | 0.9860 | +0.0054 (-0.0043, 0.0182) |
| F1 | 0.7454 | 0.7401 | 0.7305 | -0.0149 (-0.0322, -0.0028) |

Claim drop minus unguarded: weighted accuracy +0.0551 (0.0363, 0.0766), recall -0.0095 (-0.0484, 0.0298), F1 -0.0053 (-0.0361, 0.0245). User split (descriptive; nothing tuned): repair weighted-accuracy gain +0.0517 (0.0228, 0.0895) on 26 tuning-half sessions and +0.0419 (0.0168, 0.0637) on 5 final-half sessions (too few to conclude anything on its own).

Reading:
* On a naturally weaker extractor the guard improved weighted accuracy by about 5 points in the sessions it acted on, at a recall cost of about 1 to 2 points: the first natural-error result in this study that points toward a benefit. It is consistent with the dose-response.
* **Selection and independence caveats:** sessions were chosen because the DeepSeek claim judge flagged them, and the official accuracy judge is also DeepSeek, so the two are not independent measures. Reviewers did not confirm the DeepSeek flags on Mem0-DeepSeek output (1/8 and 0/8) and nobody labelled these new flags, so this is not a validated result. The pooled effect over all 100 sessions is smaller than the number above (the guard acts on 31 of them).
* The weak extractor's baseline recall is only 0.60 (0.94 for the DeepSeek-extractor run): most of its loss is omission, which this guard does not address.
* Spend (estimated from token counts): Mem0 $0.03, claim judge $0.12, repair $0.10, official scoring $1.25; about $1.50 for this round. The 14.5% dose and any reviewer labelling of the new flags were not done.

## Do the claim judge's flags agree with the official accuracy judge? (offline check)

For every extracted claim the official accuracy judge (DeepSeek, official prompt, sees the dialogue and HaluMem's gold memories) gave a score; 2 means accurate. Share of claims scored below 2, split by whether the batch claim judge had flagged them:

| Extractor | Flagged claims scored below 2 (Wilson 95%) | Unflagged claims scored below 2 |
|---|---|---|
| Mem0 + Llama-3.1-8B (31 scored sessions) | 49/68 = 72% (60%, 81%) | 83/395 = 21% (17%, 25%) |
| Mem0 + DeepSeek (97 sessions) | 4/20 = 20% (8%, 42%) | 68/1,266 = 5.4% (4.3%, 6.8%) |

On the weaker extractor the claim judge's flags line up well with a differently prompted, gold-anchored judgment; on the strong extractor they mostly do not (consistent with the reviewers confirming 1/8 and 0/8). This is **not** an independent validation: both judges are DeepSeek, and "below 2" includes partially accurate claims. It supports, but does not replace, reviewer labels. GPT-4o is no longer available through the OpenAI API, so the official-judge substitution cannot be replaced by the original model; it remains a stated deviation.

## Reviewer check of the weak-extractor flags (36 claims, two reviewers)

Quiz `halumem-claim-audit-weak-v1` (28 judge-flagged, 8 unflagged, blind, frozen rubric v1). Both reviewers labelled alone. Six items had an "unsure" or blank label from at least one reviewer; the 30 others were decisive for both.

| | ifan | nabhan |
|---|---|---|
| Judge flag precision (flagged claims the reviewer called unsupported) | 13/27 = 48% (31%, 66%) | 9/23 = 39% (22%, 59%) |
| Judge miss rate (unflagged claims the reviewer called unsupported) | 1/8 (2%, 47%) | 1/8 (2%, 47%) |
| Agreement with the judge | 20/35 = 57% | 16/31 = 52% |

Inter-rater agreement on the 30 items decisive for both: 30/30, Cohen's kappa 1.0 (Wilson lower bound 0.89). The two reviewers' precision differs mainly because they marked different items "unsure", not because they disagree on any item they both decided.

Reading:
* The reviewers confirm fewer than half of the judge's flags as unsupported (39 to 48%). The official judge's 72% on the same flags was higher than human confirmation. So the claim judge over-flags, and the guard acts on claims that are often acceptable by the rubric.
* This lowers confidence in the +0.0495 weighted-accuracy gain on the 31 flagged sessions: part of the repaired or dropped claims were not errors. The gain may still be real (the official judge also scored many flagged claims below 2), but it is not confirmed by human labels.
* Intervals are wide (28 flagged claims, 8 unflagged). The 6 unsure items are excluded from the agreement figure.
* `validated` stays false: precision is below what we would need, and the sample is small.

## Weak-extractor run on all 100 sessions (unselected; completed)

An earlier version of this section reported a pooled "no gain" result. **That result was retracted:** the DeepSeek balance ran out mid-run (HTTP 402) and the scorer had cached 1,918 failed calls as null scores, which the "(all)" metrics count as 0. The scorer now drops failed calls so they are retried and stops on HTTP 402 (`tests/test_score_cache_errors.py`). After a top-up, all 1,918 calls were rerun: 0 missing judgments, estimated spend $1.26 at peak list price (cap $1.75).

All 100 sessions, paired session bootstrap (2,000 resamples), DeepSeek official judge:

| 100 sessions | Unguarded | Claim drop | Guard + repair | Session hold |
|---|---|---|---|---|
| Weighted accuracy (all) | 0.868 | 0.891 | 0.888 | 0.913 |
| Recall (all) | 0.693 | 0.691 | 0.688 | 0.531 |
| F1 | 0.813 | 0.812 | 0.810 | 0.690 |
| Target precision (all) | 0.983 | 0.985 | 0.985 | 0.984 |
| Sessions committed | 100 | 100 | 100 | 69 |

| Paired difference vs unguarded (95% CI) | Claim drop | Guard + repair |
|---|---|---|
| Weighted accuracy (all) | **+0.0226 (0.0133, 0.0332)** | **+0.0204 (0.0096, 0.0339)** |
| Recall (all) | -0.0026 (-0.0125, 0.0084) | -0.0057 (-0.0106, -0.0011) |
| Weighted recall (all) | -0.0057 (-0.0141, 0.0029) | -0.0080 (-0.0130, -0.0038) |
| F1 | -0.0011 (-0.0080, 0.0064) | -0.0034 (-0.0069, -0.0004) |
| Target precision (all) | +0.0020 (-0.0003, 0.0052) | +0.0014 (-0.0012, 0.0047) |

User split (descriptive; nothing was tuned on either part): repair weighted accuracy +0.026 (0.011, 0.045) on the 70-session tuning users, +0.007 (0.001, 0.015) on the 30-session final users, where recall and F1 are unchanged.

Reading:
* On an unselected set of sessions from a naturally weaker extractor, the claim-level guard raises the official weighted accuracy by about 2 points, and the interval excludes 0. Claim drop does this with no significant recall or F1 cost; repair costs about 0.6 points of recall.
* The effect is smaller on the final users (+0.7 points) than on the tuning users (+2.6 points). Both are positive, but the size depends on which users are in the set.
* The session hold has the largest accuracy gain (+4.5 points) only because it discards whole sessions: recall falls 16 points. It is not a useful operating point.
* **Not validated.** The reviewers confirmed only 39 to 48% of the claim judge's flags as unsupported, and the official accuracy judge is also DeepSeek, so the accuracy gain is measured by a judge of the same family as the one that chose what to remove. A GPT-4o or other independent official judge was not used.

## What the reviewer labels say about the judge's false flags (descriptive, nothing tuned)

Of the first 12 quiz claims examined, both reviewers agreed with the judge's UNSUPPORTED on invented or contradicted facts ("User's father is deceased", an income figure not in the dialogue, "prefers sports simulation games over actual sports"). Both reviewers accepted claims the judge flagged that are mild elaborations of what the user said ("emotional support from their family has been a source of strength", "experiences ... have enriched their perspectives", "$7,340 in savings after her salary increase"). The judge also missed one claim both reviewers called unsupported. This suggests the judge over-flags consequence or motive wording that the rubric tolerates. I did not change the prompt: any change needs a fresh, blind quiz, which was not run. (The listing stopped at 12 because a later item had no answer from one reviewer, so this is a partial read.)

## Rubric-aligned claim judge (pre-registered prompt, commit 6adcf12)

The claim judge's prompt (SYSTEM_ACCEPTED) is looser than the frozen rubric on adoption (it counts thanks as acceptance) and does not say that framing without a new fact is supported. A rubric-aligned prompt (SYSTEM_RUBRIC in `scripts/run_halumem_claim_judge.py`) was written and committed before any of its outputs were seen, then run on the same 100 weak-extractor sessions (1,259 claims; estimated spend $0.12, cap $0.25).

| | Old judge | Rubric-aligned judge |
|---|---|---|
| Claims flagged | 68 | 93 |
| Flagged by both | 67 | 67 |
| Flagged only by this judge | 1 | 26 |
| Sessions with a flag | 31 | 38 |

* The expected effect (fewer false flags) **did not happen**. The new judge keeps 67 of the 68 old flags and adds 26.
* On the 36 claims of the earlier reviewer quiz the new judge flags exactly the same items as the old judge, so its precision there is the same (13/27 for ifan, 9/23 for nabhan). These claims informed the decision to write the prompt, so this check is not an independent evaluation, but it shows the prompt did not remove the framing-type false flags.
* The 26 new flags are mostly speculative or assistant-originated statements ("could serve as a metaphor for embracing new perspectives", "could take his work in new and exciting directions"). They look like real rubric violations, but no reviewer has labelled them.
* No further prompt change was made: iterating on prompts after seeing their outputs on this data would be tuning, and the data include final-split users.

## Live HaluMem pilot: OSS Mem0 vs Mem0 + MemBud (2 users x 5 sessions, DeepSeek extractor)

Runner `scripts/run_halumem_live.py` (official eval_memzero procedure on OSS Mem0 2.2.1, official `custom_instructions`, session time as `created_at`, fastembed `bge-small-en-v1.5`), scored by the unchanged official `evaluation.py` via `scripts/run_halumem_official_eval.py` (DeepSeek judge, thinking disabled). Purpose: check the pipeline and calibrate cost, not to estimate an effect.

* **Guard behaviour:** the claim-judge grounding policy judged all 145 claims in the 10 sessions SUPPORTED, so both MemBud arms accepted every session. Their stored memories equal the pure arm's as sets (only the order differs), so only the pure arm was scored.
* **Official metrics, pure arm (10 sessions; not a result, n too small):**
  * Extraction: recall(all) 0.965, weighted accuracy(all) 0.941, target accuracy(all) 0.986, F1 0.975.
  * Update: correct 9/15, hallucination 1/15, omission 5/15.
  * QA: correct 20/21, omission 1/21.
  * One of 206 integrity items has no score: the judge's reply failed the official JSON parser after retries, with no transport error. The official code counts such an item in the "(all)" denominators.
* **Cost (estimated from token counts, peak list price):**
  * Live runs: pure $0.047; hold and drop $0.021 each. Mem0's extraction calls were served from the client cache because the prompts were identical.
  * Official scoring of the pure arm: $0.30 (410 calls, 0 errors).
  * Total: $0.39 of the $1.00 cap.
* **Pipeline issues found and fixed:**
  * OSS Mem0 rejects whitespace in `user_id`. The store id maps whitespace to `_`, while the context keeps the name.
  * OSS Mem0 rejects the hosted-only `timestamp` argument. The session time is passed as `metadata.created_at` instead.
