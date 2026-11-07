# Transition annotation guidelines

Use these rules for any evaluation cases adapted to MemBud. Keep annotations separate from transition inputs so the verifier cannot read the answer labels. Record annotator IDs, disagreements, adjudication, and evidence references. These rules are a protocol draft; validate and freeze them before collecting evaluation results.

## Annotation unit

The primary unit is one transition: prior records, the source dialogue/evidence available for that update, one fixed proposed candidate state, and the intended update. Give every case, record, claim, and evidence item a stable ID. Compare conditions on the same frozen candidate.

## Claim and materiality

- Split records into atomic claims without adding facts or changing qualifiers, time, uncertainty, or attribution.
- A claim is **material** if changing, omitting, or adding it can alter the record's meaning, identity, temporal status, or the decision to accept the update. Stylistic changes alone are not material.
- Record the source span or reference supporting each claim. Mark ambiguous claims unresolved; do not silently infer equivalence. The current deterministic implementation compares claim strings exactly, so any semantic normalization must be explicitly annotated and must not be attributed to that implementation.

## Required claims / coverage

Under the proposed benchmark policy, an annotated-valid prior claim is required to persist unless evidence supports its correction/supersession or an authorized deletion applies. This is an explicit safety policy, not an assumption that all memory must persist forever. Mark the persistence/retirement annotation explicitly for every prior claim.

For new information, designate `in_scope_claims` before reviewing verifier results. The proposed coverage policy requires every such fact to be represented in the candidate. Derive the verifier-input `required_claims` from in-scope claims plus prior claims required by the persistence policy. An omitted in-scope claim is a coverage error; a source detail is not automatically required merely because it appears in the conversation. Record why each fact is in-scope or excluded. Keep these input fields distinct from gold outcome, failed-check labels, and affected IDs.

## Prior validity and expected persistence

For each prior record, label:

- **valid**: supported by its provenance or accepted as a valid prior fact for this case;
- **invalid/uncertain**: contradicted, unsupported, or not assessable;
- **expected to persist**: its material claims should remain after this transition absent a supported explicit operation.

Only valid prior records expected to persist contribute to the corruption denominator. If the transition legitimately changes a claim, annotate the targeted record and the specific supported replacement; do not count that authorized change as corruption.

## Faithfulness and unsupported additions

For each material candidate claim, label it supported by the supplied source, supported by valid prior memory, unsupported, contradicted, or uncertain. A claim is unsupported when neither the supplied evidence nor valid prior state justifies it. A contradicted claim conflicts with the evidence or established prior fact without a supported correction. Do not use external world knowledge to fill gaps. Mark a check uncertain when evidence conflicts without supported ordering/resolution, or the verifier cannot evaluate it from the available inputs. A failed or uncertain required check maps to `HOLD`; schema validation failure maps to `INVALID_INPUT` before verification and must not write state.

## Operations and authorization

Annotate intended operation (`WRITE`, `REVISE`, `DELETE`, `SUPERSEDE`, or `NOOP`) and exact target/replacement record IDs. Use `REVISE` for a correction/alteration of the same fact and record; use `SUPERSEDE` when a later state replaces an earlier state over time. Both require evidence for new material claims and should receive the same preservation and faithfulness evidence standard. A change/removal is intentional only when supported by the source and represented by the corresponding explicit operation. For deletion, record the authorization basis independently; a non-empty reason string is only a syntactic annotation and is not proof of authorization. Prefer a structured, attributable deletion directive linked to the target record. The current schema does not model this authorization; deletion cases cannot serve as valid gold tests until that limitation is addressed. A missing operation does not authorize disappearance or mutation. `NOOP` means the candidate state is unchanged; unrelated incoming evidence alone does not make a mutation valid.

## Expected outcomes and review

Annotate expected check findings and accept/hold decision only after the factual annotations are complete. Have two reviewers independently label each final evaluation case, adjudicate disagreements, and retain the resolution trail. Pilot examples used to refine these rules must not be mixed into a held-out evaluation without being identified as development cases. Report inter-annotator agreement and unresolved cases; exclude unresolved cases only under a predeclared rule and report their count.
