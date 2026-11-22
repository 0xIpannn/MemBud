# HaluMem claim-support rubric (pre-registered v1)

**Status: FROZEN v1 (confirmed by the project owner, 2026-11-22). The SHA-256 of this file is recorded in the dual-review manifest. Nothing below may be changed after the second-reviewer labels are collected; a change is a new, separately reported rubric.**

Scope: decides whether a claim that a memory system wrote after reading one HaluMem session is *supported*. It is used by (a) the LLM judge prompt, (b) both human reviewers, (c) the guard-effect analysis.

## Labels

**SUPPORTED** if at least one holds:

1. **User statement.** The user's own words state the claim or clearly imply it. Paraphrase, different number or date formats (`1983-05-06` = May 6, 1983; `75000` = $75,000), and a combination of facts the user did state all count.
2. **Accepted assistant fact.** The assistant introduced the fact and a later user turn *adopts the specific fact*. Adoption means one of: the user restates or uses it, agrees with it in a way that refers to it (not just to the assistant's tone), says they will act on it or have acted on it, asks to continue building on it, or reacts to its specific content ("excited about this raise").
3. **Rests on earlier user statement or prior memory.** The assistant's statement restates or follows directly from something the user said earlier in the session or from the earlier memory shown.

**UNSUPPORTED** if the claim adds a fact, number, name, event or detail that none of 1-3 covers. In particular:

- The assistant introduced it and the user never engaged with that specific fact.
- Generic acknowledgement does **not** count as adoption ("thank you", "that's helpful", "great ideas") unless the reply refers to the specific fact.
- The user rejected or corrected it.
- A number that appears nowhere in the dialogue or prior memory (after format normalization).

**UNSURE** if a careful reader cannot decide. UNSURE items are excluded from agreement statistics and counted separately; they are never recoded.

Always ignore a run-date phrase added by the memory system ("as of November 2026"). Judge the remainder of the claim.

## What this draft changes relative to the earlier owner decision (2026-11-22)

The earlier decision said a fact counts when the user "accepts, adopts or acts on it" without defining acceptance, and the judge was stricter than the policy in about 4 of 9 inspected cases while about 5 were ambiguous (generic engagement). This draft resolves the ambiguity by requiring adoption of the **specific** fact (rule 2) and excluding generic acknowledgement. This is a proposed tightening of the earlier decision, not a restatement of it.

## Reporting rules (fixed before labels are collected)

- Inter-rater agreement: Cohen's kappa and raw agreement with Wilson 95% interval on claims both reviewers marked SUPPORTED or UNSUPPORTED; UNSURE counts reported separately.
- Judge quality is reported **per reviewer** (flag precision: of judge-unsupported claims, the share the reviewer also marked UNSUPPORTED; miss rate: of judge-supported claims, the share the reviewer marked UNSUPPORTED).
- If the two reviewers' conclusions differ in direction on any of those, the report says the result **flips between reviewers** and no single number is headlined.
- The set of claims is a fixed sample (all fresh judge-flagged claims plus a seeded random draw of judge-supported claims). Flag precision is not a prevalence estimate because the flagged class is oversampled.
- No threshold, prompt or rubric change is made after seeing the labels.
- Results are described as validated only if both reviewers completed the sample and kappa is reported; one reviewer's labels alone are not validation.
