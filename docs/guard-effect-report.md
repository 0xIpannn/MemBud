# Does MemBud's guard change results? HaluMem and LongMemEval pilots

Date: 2026-11-22. **Status: unvalidated.** Every number below depends on an LLM judge (DeepSeek `deepseek-flash`, not the benchmarks' GPT-4o) whose flags are not validated by a completed two-reviewer, inter-rater-checked label set. The dual-review quiz (`data/halumem-runs/phase2-deepseek/claim-audit-dual/claim-quiz.html`, rubric frozen in `docs/halumem-grounding-rubric.md`) was built but **no reviewer has labeled it**. The required "inter-rater-checked judge" condition is therefore **not met**.

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

- **Judge not validated, not GPT-4o.** Official metrics use the repository's prompts with deepseek-flash. The judge both supplies guard flags and scores the result, so results are circular. Earlier audits disagreed (judge flag precision 17% under one reviewer, 91.5% agreement under another) and used different claims, so no inter-rater agreement exists.
- **Guard = judge-claim guard**, not a per-session re-run of the structural verifier. Re-verification of an unchanged prompt is served from cache.
- **One extractor family, one dataset slice.** Mem0 2.2.1 on 20 HaluMem users; a custom DeepSeek ingestion pipeline on LongMemEval (the benchmark has no memory system of its own). Not a leaderboard number.
- Lexical retrieval; LongMemEval judge reply requested as JSON.
- LongMemEval used the oracle split, 30 questions, one seed.
- Graph vs text verifier ties on every case, so no graph advantage is claimed.

## Not done / not tested

- Second-reviewer labels and inter-rater agreement (quiz built, unlabeled).
- HaluMem update metrics and QA accuracy.
- LongMemEval S/M (distractor haystacks) and any run beyond 30 questions; GPT-4o judging.
- Extension beyond the 97-session pilot (not done because the pilot showed no benefit to chase).
- Verification of the paper's own metric text beyond the reference code.
