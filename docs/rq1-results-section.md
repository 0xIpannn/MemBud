# RQ1 results section (draft for the paper)

> (a) Does MemBud reduce omissions, corruption of valid prior records, and unsupported additions relative to an unguarded pipeline? (b) Does graph-based verification catch more errors than a non-graph verifier given the same criteria and budget?

**Short answer.** (b): **no**, with good support. (a): **supported as a conditional result, not yet validated.** The guard-and-repair pipeline improves the official weighted accuracy when unsupported claims exist: on injected faults by about 0.7, 2.8 and 4.7 points at 1.1%, 4.1% and 7.1% injected claims (dose-response, recall cost under 1 point with repair), and, on a naturally weaker extractor (Mem0 with Llama-3.1-8B, 5.4% judge-flagged claims), by about 5 points in the 31 sessions it acted on, a flag-selected set; over all 100 sessions (unselected) claim drop raises weighted accuracy by +2.3 points (95% CI 1.3 to 3.3) with no significant recall or F1 cost (+0.7 points on the final-split users alone). On Mem0 with a strong model (1.6% flagged on Medium, 1.1% on Long) there is no measurable effect. **Caveats that block a validated claim:** the judge is DeepSeek, not GPT-4o; reviewers did not confirm its flags on the strong-model output (1/8 and 0/8); on the weaker-extractor output two reviewers agreed with each other on every item they both decided (30/30) but confirmed only 48% and 39% of the judge's 28 flagged claims as unsupported (wide intervals), so the judge over-flags and the 5-point gain is not human-confirmed; the official accuracy judge is the same model family, so the two are not independent; the injected labels are circular. The guard evaluated here is the LLM claim-judge guard with the repair loop, not the graph verifier, and on MemoryAgentBench conflict resolution an LLM conflict-hold guard hurt accuracy. Do not write "MemBud reduces hallucinated memory" without these qualifiers.

## What supports an answer

**RQ1(b), graph vs non-graph.** Graph and the fair text verifier (`text_fair`) make identical decisions on every case: Phase 1 on real HaluMem-Medium (1,422 cases) and HaluMem-Long (4,508 cases), the synthetic fixture (66 cases), and Phase 2 on 97 natural Mem0 proposals (0 discordant pairs, McNemar p = 1.0 throughout). Plain `text` loses only on corruption signaled solely through `evidence.contradicts`, which it ignores. This is a robust null: **no graph advantage under equal information.**

**RQ1(a), mechanism (Phase 1, injected faults).** With transcript grounding the verifier detects every injected omission, corruption and unsupported addition with one false hold on valid transitions in the real-data runs, versus 0% for unguarded. These labels are circular by construction (the injector creates fault and label together), so this shows **mechanism sensitivity, not natural error prevalence.**

## What the natural-error evidence shows (Phase 2, real Mem0 2.2.1, 97 sessions, 20 users)

| Finding | Value |
|---|---|
| Natural omissions or corruption of prior records by Mem0 | none observed (`add()` is additive; no UPDATE/DELETE emitted) |
| Official extraction precision, unguarded | 0.985 (ceiling) |
| Guard vs unguarded: recall | -0.022 [-0.039, -0.010] |
| Guard vs unguarded: target precision | -0.0004 [-0.002, +0.001] |
| Guard+repair vs unguarded: recall / F1 | -0.001 [-0.004, +0.002] / -0.001 [-0.002, +0.001] |
| Guard+repair vs guard: recall | +0.021 [+0.009, +0.038] |
| LongMemEval pilot (30 q, oracle): guard / guard+repair vs unguarded accuracy | -6.7 pts (p = 0.5) / 0.0 pts |
| Judge flag precision, two reviewers, 8 flagged claims | 1/8 and 0/8; kappa between reviewers -0.03 |

Interpretation: with Mem0 as the extractor there is little natural error of the kinds RQ1(a) names. Unsupported additions are the only plausible target, and their ground truth is unreliable: reviewers confirm almost none of the judge's flags. The guard's recall cost is collateral: it holds a whole session (about 13 claims) to remove 1-2 flagged claims, discarding 210 unflagged claims to remove 20 flagged ones. Repair removes that cost but buys no precision, because precision was already near ceiling.

## Why this does not fully answer RQ1(a)

1. **No validated ground truth for natural unsupported additions.** The judge is DeepSeek-flash, not GPT-4o; its flags are weakly checked (36 claims, 8 flagged, kappa -0.03), and the reviewers essentially found no unsupported claims. Earlier audits gave 17% flag precision under one reviewer and 91.5% agreement under another, on different claims.
2. **One extractor, one dataset slice.** Mem0 2.2.1 on 20 HaluMem-Medium users (first <= 5 sessions each). Systems that update or delete (where omission and corruption occur) were not tested.
3. **The guard is the judge-claim guard** evaluated offline on recorded proposals, not a per-session re-run of the structural verifier, and not a live memory system.
4. **Repair is nearly deletion** (21 removals, 1 rewrite) by a DeepSeek re-prompt, not a repair of Mem0.
5. **HaluMem update and QA metrics were not computed**; LongMemEval was a 30-question oracle pilot (no distractor sessions).
6. Tuning/final split is descriptive (nothing was tuned); the final half has 6 users and 3 held sessions.

## Claims the data supports

- Graph verification gives no advantage over a fair text verifier on any tested case.
- The verifier detects injected faults (mechanism sensitivity).
- On natural Mem0 output there is **no measurable benefit** of the guard on official extraction metrics; unrepaired holds cost about 2 points of recall; guard+repair is indistinguishable from unguarded.
- On a naturally weaker extractor (Mem0 with Llama-3.1-8B, all 100 sessions, unselected), dropping judge-flagged claims raises the DeepSeek-judged weighted accuracy by +2.3 points (95% CI 1.3 to 3.3) with no significant recall or F1 cost; +0.7 points (0.1 to 1.5) on the final-split users alone. Not validated: reviewers confirmed 39 to 48% of the flagged claims as unsupported (kappa 1.0 on 30 decisive items), and the accuracy judge is the same model family as the claim judge.
- A pre-registered rubric-aligned claim-judge prompt did not reduce false flags: it kept 67 of 68 flags and added 26. No further prompt change was made.

## Experiments that would make RQ1(a) answerable

- A system that updates and deletes (or HaluMem update metrics with recorded retrieval), where omission and corruption actually arise.
- A reviewer-validated label set with enough true unsupported claims (stratified, session-level), so a detection rate can be estimated. A fresh quiz on the rubric-aligned judge's 26 extra flags was designed (`halumem_dual_review.py build-compare`) but not run, by decision.
- A GPT-4o (benchmark-standard) judge replication of the extraction metrics.
- LongMemEval S with distractor sessions at a larger n.

Source numbers: `docs/guard-effect-report.md`, `docs/halumem-evaluation.md`.
