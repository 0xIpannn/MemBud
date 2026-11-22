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
