# MemBud Mermaid Graph Review

## Overall assessment

The 11 files are useful as early visualization fixtures, but they are not yet a reliable test suite for MemBud. Several diagrams are duplicates, some intended error cases are not encoded explicitly, and most diagrams contain an isolated `record:candidate` node. A diagram's missing edge should not automatically be interpreted as a verifier finding: it may simply be absent from the fixture.

Review scope: this report evaluates the contents of the supplied `.mmd` files, not the runtime code that constructs or verifies the graph.

## Findings by file

| File | Apparent scenario | Assessment | Main issue |
|---|---|---|---|
| `valid-noop.mmd` | Valid no-op | **Does not clearly encode a no-op** | The candidate preserves the Bandung claim, but evidence supports a Tokopedia claim that is not contained by a candidate record. There is no explicit no-op operation or unchanged-state marker. |
| `valid.mmd` | Valid update | **Mostly plausible, but incomplete** | Bandung is preserved; the Tokopedia claim is included and supported. The top-level candidate node is isolated, and evidence provenance is unspecified. |
| `unsupported-plus-valid.mmd` | One supported and one unsupported candidate claim | **Mostly matches intent** | The candidate contains the Bandung claim, the supported Tokopedia claim, and the unsupported pilot claim. Evidence supports only Tokopedia. The candidate root is isolated, and the source evidence itself is not shown. |
| `unsupported.mmd` | Unsupported addition | **Partly matches intent** | The yacht claim has no visible supporting edge. However, the evidence supports a Tokopedia claim that is not contained in any candidate record, making that evidence/claim branch extraneous or incomplete. |
| `supported-revision.mmd` | Supported revision/addition | **Represents a supported addition more than a revision** | The existing Bandung claim is retained and a supported Tokopedia claim is added. There is no changed claim, revision relation, or explicit operation. |
| `remove-record.mmd` | Remove a record | **Removal is only implicit** | The old record has no candidate counterpart, but the graph does not explicitly encode a deletion operation or distinguish intentional removal from accidental omission. |
| `preserve-added-claim.mmd` | Preserve existing memory while adding a claim | **Same graph as `supported-revision.mmd`** | The diagram cannot distinguish the two intended scenarios. Define distinct expected behavior or merge the cases. |
| `omission.mmd` | Omission | **Does not encode the apparent omission** | The Bandung claim appears in both the before and candidate records, so it is preserved. The Tokopedia claim has evidence but is not contained in the candidate. This may be an omission of Tokopedia only if Tokopedia is explicitly marked as required for this transition. |
| `explicit-supersession.mmd` | Explicit supersession | **Supersession intent is incomplete** | `record:candidate` points to the old record with `SUPERSEDES`, but the root is not linked to the new candidate record or replacement claim. The new claim is not shown as the replacement of the old claim. |
| `example-graph.mmd` | General example | **Duplicate of `valid.mmd`** | It does not add a distinct scenario or behavior. |
| `corruption.mmd` | Corruption of prior memory | **Partly matches intent** | `before:r1` contains Bandung while `candidate:r1` contains Surabaya, suggesting a changed record. The graph does not explicitly link the old and new claims as a replacement, and it does not show evidence for Surabaya. The supported Tokopedia claim is a separate, valid-looking addition. |

## Cross-cutting issues

### 1. The top-level candidate node is disconnected

Every file declares `record:candidate`, but none connects it to its candidate records. Either remove this node if it has no semantic role, or model it as a transition/operation node and connect it to the candidate records and operation metadata. Do not leave it as an unexplained isolated node.

### 2. Evidence needs provenance and scope

An edge such as `evidence:e1 --> claim:...` is useful only if the system can identify what `e1` contains and why it supports the claim. Include a source ID and preferably a source span or excerpt in the underlying data. The diagram can display a short excerpt while retaining the source reference. A missing `SUPPORTS` edge should mean “no support is represented in this graph,” not automatically “the claim is false.”

### 3. Record identity and claim identity are different

For corruption and revision, use stable record IDs to identify the record being updated, and separate claim IDs to represent old and new assertions. Then encode the change explicitly, for example with `REVISES`, `REPLACES`, or `SUPERSEDES`, rather than relying only on similar labels or node positions.

### 4. Omission requires a declared expectation

A graph cannot determine that a claim was wrongly omitted merely because it is absent from the candidate. It needs to know that the claim was required to survive this transition, based on the source, update intent, or benchmark annotation. Represent this as an expected-preservation set or transition metadata; do not treat every fact in the old memory as immutable.

### 5. Distinguish fixture facts from verifier conclusions

The fixture should contain source facts and proposed operations. The verifier should produce findings such as `UNSUPPORTED_CLAIM`, `REQUIRED_CLAIM_OMITTED`, `PRIOR_RECORD_CORRUPTED`, or `VALID_SUPERSESSION`. Avoid encoding a verifier conclusion as if it were unquestionable ground truth unless the test case explicitly labels it as an expected result.

## Recommended canonical transition representation

For each test case, retain structured data separate from the Mermaid rendering:

- `case_id` and intended scenario.
- `before_records`, each with stable record IDs and claim IDs.
- `candidate_operations`, such as `WRITE`, `REVISE`, `DELETE`, `NOOP`, or `SUPERSEDE`.
- `candidate_records` and the exact old record each operation targets.
- `evidence` with source text/reference and provenance.
- `required_preserved_claims`, where applicable.
- `expected_findings` and `expected_decision`, stored as test annotations rather than inferred from the diagram.

The graph renderer should visualize that structured data. It should not independently invent relationships to make the picture look complete.

## Suggested expected behavior by scenario

| Scenario | Expected result to test |
|---|---|
| Valid update | Existing required claim preserved; new claim supported; accept |
| Valid no-op | No memory mutation; no unrelated claim added; accept/no-op |
| Unsupported addition | Unsupported candidate claim flagged; hold or revise |
| Supported + unsupported | Supported claim accepted only if the unsupported claim is removed or the update is held/revised |
| Omission | A claim explicitly marked as required is missing from the candidate; flag coverage failure |
| Corruption | A prior valid claim is changed without adequate evidence or an authorized revision; flag preservation failure |
| Supported revision | Old claim is replaced only when source evidence supports the new claim and the change is explicit |
| Remove record | Deletion is an explicit operation with a reason/authorization; do not infer deletion merely from absence |
| Explicit supersession | New claim/record explicitly supersedes old claim/record, with evidence and temporal/provenance details |
| Valid preservation + addition | Prior claim survives and a new supported claim is added; accept |

## Priority fixes

1. **Fix `omission.mmd` first.** As drawn, it preserves Bandung. Add a clearly required claim to the before/expected set and omit it from the candidate.
2. **Fix `valid-noop.mmd`.** Make the candidate state unchanged and remove the unrelated, uncontained Tokopedia evidence branch unless the no-op case specifically tests ignored incoming evidence.
3. **Fix `corruption.mmd`.** Explicitly connect the old Bandung claim and replacement Surabaya claim through the same record's revision operation; specify whether evidence supports the change.
4. **Fix `explicit-supersession.mmd`.** Link the supersession operation to the new candidate record and old record, and identify the old and new claims.
5. **Resolve duplicate cases.** Either make `valid.mmd` differ meaningfully from `example-graph.mmd`, and `supported-revision.mmd` differ meaningfully from `preserve-added-claim.mmd`, or consolidate them.
6. **Connect or remove `record:candidate`** across all fixtures.
7. Add automated tests over the structured fixture data, not just snapshots of Mermaid output.

## Important limitation

This review is based only on the supplied Mermaid source. It does not establish whether MemBud's runtime verifier interprets these diagrams correctly, whether the underlying objects contain more metadata than the visualization displays, or whether the current code has corresponding automated tests. Those require inspecting the implementation and its test outputs.
