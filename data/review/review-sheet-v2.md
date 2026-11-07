# Human review sheet — `membud-evaluation-v2`

> **Review status: single reviewer only — second independent human review PENDING.**
>
> The "proposed label" columns below are the existing single-reviewer project
> annotations. All second-reviewer and adjudication fields are intentionally
> blank. Do not fill them without a real independent reviewer; do not report
> agreement or double-annotation statistics until the process in
> `data/review/README.md` has actually been executed.


- Manifest: `/home/ifan/Projects/MemBud/data/manifests/evaluation-v2.json`
- Cases: 36
- Conditions under evaluation: unguarded, graph, text
- Instructions: verify each case's inputs independently of the proposed label; write your own decision (`ACCEPT`, `HOLD`, or `INVALID_INPUT`) and notes; do not edit the case files.
- Disagreements are resolved by a third adjudicator using `data/review/adjudication-template.md`.

## Agreement summary (to be completed only after real second review)

| Field | Value |
|---|---|
| Second reviewer ID | _(blank — pending)_ |
| Cases reviewed / total | _0 / 36 (pending)_ |
| Raw agreement | _not computed — no second-review data_ |
| Cohen's kappa | _not computed — no second-review data_ |
| Adjudicated disagreements | _none recorded_ |

---

## DEV-01 — valid_write — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Supported new employment claim; prior residence preserved.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e1` (source `synthetic:employment`): “Ia bekerja di Tokopedia.” — supports “bekerja di Tokopedia”

**Candidate state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”

**Declared operations:**

- WRITE; record_id=r2

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-02 — valid_revise — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Supported correction of the same record; old claim retired by evidence.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e1` (source `synthetic:correction`): “Maaf, saya salah. Saya tinggal di Surabaya, bukan Bandung.” — supports “tinggal di Surabaya”

**Candidate state:** `r1`: “tinggal di Surabaya”

**Required claims (verifier input):** “tinggal di Surabaya”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-03 — valid_supersede — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Supported temporal supersession of residence; old record retired.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e2` (source `synthetic:relocation`): “Saya sudah pindah dan sekarang tinggal di Surabaya.” — supports “tinggal di Surabaya”

**Candidate state:** `r2`: “tinggal di Surabaya”

**Required claims (verifier input):** “tinggal di Surabaya”

**Declared operations:**

- SUPERSEDE; record_id=r2; target_id=r1; reason=“pengguna pindah”

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-04 — valid_delete_authorized — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1, r3
**Corrupted prior record IDs:** (none)
**Reviewer note:** Structured DELETE authorization linked to directive evidence and to the exact target record.

**Prior state:** `r1`: “bekerja di Tokopedia”; `r2`: “memiliki langganan gym”; `r3`: “tinggal di Bandung”

**Evidence:**

- `e3` (source `synthetic:user-request`): “Tolong hapus catatan langganan gym saya.” — supports (none)

**Candidate state:** `r1`: “bekerja di Tokopedia”; `r3`: “tinggal di Bandung”

**Required claims (verifier input):** “bekerja di Tokopedia”, “tinggal di Bandung”

**Declared operations:**

- DELETE; target_id=r2; reason=“pengguna meminta penghapusan eksplisit”; authorization={source=synthetic:user-request, evidence_id=e3, target_id=r2, rationale=“pengguna secara eksplisit meminta catatan langganan gym dihapus”}

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-05 — valid_noop — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Explicit no-op; candidate state equals prior state.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

(none)

**Candidate state:** `r1`: “tinggal di Bandung”

**Required claims (verifier input):** “tinggal di Bandung”

**Declared operations:**

- NOOP

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-06 — valid_multi_op — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Three declared operations (two REVISE, one WRITE), each evidence-backed.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:relocation`): “Saya pindah ke Surabaya.” — supports “tinggal di Surabaya”
- `e2` (source `synthetic:employment`): “Saya sekarang bekerja di Traveloka.” — supports “bekerja di Traveloka”
- `e3` (source `synthetic:hobby`): “Saya mulai belajar bahasa Jepang.” — supports “belajar bahasa Jepang”

**Candidate state:** `r1`: “tinggal di Surabaya”; `r2`: “bekerja di Traveloka”; `r3`: “belajar bahasa Jepang”

**Required claims (verifier input):** “tinggal di Surabaya”, “bekerja di Traveloka”, “belajar bahasa Jepang”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1
- REVISE; record_id=r2; target_id=r2
- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-07 — valid_contradiction_correction — split: dev

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Contradiction evidence supports the replacement and explicitly retires the contradicted prior claim; graph CONTRADICTS edge should not flag the new candidate claim.

**Prior state:** `r1`: “tinggal di Jakarta”; `r2`: “bekerja di PT Sinar”

**Evidence:**

- `e1` (source `synthetic:correction`): “Koreksi: saya bekerja di PT Astra, bukan PT Sinar.” — supports “bekerja di PT Astra”; contradicts “bekerja di PT Sinar”

**Candidate state:** `r1`: “tinggal di Jakarta”; `r2`: “bekerja di PT Astra”

**Required claims (verifier input):** “tinggal di Jakarta”, “bekerja di PT Astra”

**Declared operations:**

- REVISE; record_id=r2; target_id=r2

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-08 — omission — split: dev

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** omission
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** In-scope employment fact omitted from candidate; no operation declares the needed write.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e1` (source `synthetic:employment`): “Ia bekerja di Tokopedia.” — supports “bekerja di Tokopedia”

**Candidate state:** `r1`: “tinggal di Bandung”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”

**Declared operations:**

- NOOP

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-09 — corruption_no_evidence — split: dev

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** corruption, omission
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** r1
**Reviewer note:** Valid prior record changed with no evidence at all; also drops the required Bandung claim.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

(none)

**Candidate state:** `r1`: “tinggal di Surabaya”; `r2`: “bekerja di Tokopedia”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-10 — corruption_insufficient_evidence — split: dev

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** corruption, omission
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** r1
**Reviewer note:** Evidence exists but supports an unrelated employment fact; it does not justify the residence change.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya sekarang bekerja di Traveloka.” — supports “bekerja di Traveloka”

**Candidate state:** `r1`: “tinggal di Surabaya”; `r2`: “bekerja di Tokopedia”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-11 — unsupported_addition — split: dev

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** unsupported_addition, omission
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Candidate adds an unsupported yacht claim and omits the in-scope language-learning fact.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:hobby`): “Saya mulai belajar bahasa Jepang.” — supports “belajar bahasa Jepang”

**Candidate state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”; `r3`: “memiliki kapal pesiar”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”, “belajar bahasa Jepang”

**Declared operations:**

- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-12 — unsupported_provenance — split: dev

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** unsupported_provenance
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** One candidate claim has no provenance path from evidence or prior memory; the graph verifier should attribute it to missing provenance rather than claim-set comparison.

**Prior state:** `r1`: “tinggal di Jakarta”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di Bank Mandiri.” — supports “bekerja di Bank Mandiri”

**Candidate state:** `r1`: “tinggal di Jakarta”; `r2`: “bekerja di Bank Mandiri”, “memiliki gaji 25 juta per bulan”

**Required claims (verifier input):** “tinggal di Jakarta”, “bekerja di Bank Mandiri”

**Declared operations:**

- WRITE; record_id=r2

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-13 — contradiction_unresolved — split: dev

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** unresolved_contradiction
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Gold HOLD per protocol: connected evidence directly contradicts a retained prior claim and the transition neither resolves nor drops it. Intended structural graph case; the claim-set text baseline cannot read the contradicts field and is expected to false-accept.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:relocation`): “Saya sekarang tinggal di Surabaya.” — supports “tinggal di Surabaya”; contradicts “tinggal di Bandung”

**Candidate state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”

**Declared operations:**

- NOOP

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-14 — invalid_unauthorized_delete — split: dev

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** unauthorized_deletion
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** DELETE declares the removal but carries no structured authorization; schema rejects before verification, so no store write and no ACCEPT/HOLD decision.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di Tokopedia.” — supports “bekerja di Tokopedia”

**Candidate state:** (none)

**Required claims (verifier input):** “tinggal di Bandung”

**Declared operations:**

- DELETE; target_id=r1; reason=“hapus data lama”

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-15 — invalid_delete_authorization_target — split: dev

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** unauthorized_deletion
**Valid prior records expected to persist:** r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Authorization points at a non-existent evidence entry; schema rejects the invalid authorization reference.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di Tokopedia.” — supports “bekerja di Tokopedia”

**Candidate state:** `r2`: “bekerja di Tokopedia”

**Required claims (verifier input):** “bekerja di Tokopedia”

**Declared operations:**

- DELETE; target_id=r1; reason=“hapus data lama”; authorization={source=synthetic:user-request, evidence_id=e_missing, target_id=r1, rationale=“menghapus data lama”}

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-16 — invalid_undeclared_insertion — split: dev

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** undeclared_state_change
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Candidate adds a record but no WRITE/SUPERSEDE operation declares it; schema rejects.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di Tokopedia.” — supports “bekerja di Tokopedia”

**Candidate state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”

**Declared operations:**

(none declared)

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-17 — invalid_undeclared_modification — split: dev

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** undeclared_state_change
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Candidate changes a prior record while declaring no REVISE; schema rejects even though evidence exists, because the state change is undeclared.

**Prior state:** `r1`: “tinggal di Bandung”

**Evidence:**

- `e1` (source `synthetic:correction`): “Maaf, saya salah. Saya tinggal di Surabaya.” — supports “tinggal di Surabaya”

**Candidate state:** `r1`: “tinggal di Surabaya”

**Required claims (verifier input):** “tinggal di Surabaya”

**Declared operations:**

(none declared)

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## DEV-18 — invalid_undeclared_removal — split: dev

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** undeclared_state_change
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Prior record r2 disappears without DELETE/SUPERSEDE; schema rejects the undeclared removal before verification.

**Prior state:** `r1`: “tinggal di Bandung”; `r2`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:hobby`): “Saya mulai belajar bahasa Jepang.” — supports “belajar bahasa Jepang”

**Candidate state:** `r1`: “tinggal di Bandung”; `r3`: “belajar bahasa Jepang”

**Required claims (verifier input):** “tinggal di Bandung”, “bekerja di Tokopedia”, “belajar bahasa Jepang”

**Declared operations:**

- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-01 — valid_write — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out supported addition.

**Prior state:** `r1`: “tinggal di Yogyakarta”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di Gojek.” — supports “bekerja di Gojek”

**Candidate state:** `r1`: “tinggal di Yogyakarta”; `r2`: “bekerja di Gojek”

**Required claims (verifier input):** “tinggal di Yogyakarta”, “bekerja di Gojek”

**Declared operations:**

- WRITE; record_id=r2

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-02 — valid_revise — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out supported correction.

**Prior state:** `r1`: “bekerja di Tokopedia”

**Evidence:**

- `e1` (source `synthetic:correction`): “Koreksi: saya bekerja di Bukalapak.” — supports “bekerja di Bukalapak”

**Candidate state:** `r1`: “bekerja di Bukalapak”

**Required claims (verifier input):** “bekerja di Bukalapak”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-03 — valid_supersede — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out supported supersession of a possession record.

**Prior state:** `r1`: “memiliki sepeda motor”; `r2`: “tinggal di Malang”

**Evidence:**

- `e1` (source `synthetic:possession`): “Saya menjual sepeda motor dan membeli mobil.” — supports “memiliki mobil”

**Candidate state:** `r2`: “tinggal di Malang”; `r3`: “memiliki mobil”

**Required claims (verifier input):** “tinggal di Malang”, “memiliki mobil”

**Declared operations:**

- SUPERSEDE; record_id=r3; target_id=r1; reason=“kendaraan diganti”

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-04 — valid_delete_authorized — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1, r3
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out structured deletion with directive evidence and target match.

**Prior state:** `r1`: “tinggal di Semarang”; `r2`: “menderita alergi udang”; `r3`: “bekerja di PLN”

**Evidence:**

- `e2` (source `synthetic:user-request`): “Hapus catatan alergi udang saya, sudah tidak relevan.” — supports (none)

**Candidate state:** `r1`: “tinggal di Semarang”; `r3`: “bekerja di PLN”

**Required claims (verifier input):** “tinggal di Semarang”, “bekerja di PLN”

**Declared operations:**

- DELETE; target_id=r2; reason=“pengguna meminta penghapusan data alergi”; authorization={source=synthetic:user-request, evidence_id=e2, target_id=r2, rationale=“pengguna secara eksplisit meminta catatan alergi dihapus”}

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-05 — valid_noop — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out explicit no-op over two records.

**Prior state:** `r1`: “bekerja di Bank BCA”; `r2`: “tinggal di Bogor”

**Evidence:**

(none)

**Candidate state:** `r1`: “bekerja di Bank BCA”; `r2`: “tinggal di Bogor”

**Required claims (verifier input):** “bekerja di Bank BCA”, “tinggal di Bogor”

**Declared operations:**

- NOOP

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-06 — valid_multi_op — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out multi-operation valid transition.

**Prior state:** `r1`: “tinggal di Depok”

**Evidence:**

- `e1` (source `synthetic:relocation`): “Saya pindah ke Bekasi.” — supports “tinggal di Bekasi”
- `e2` (source `synthetic:employment`): “Saya bekerja di Pertamina.” — supports “bekerja di Pertamina”
- `e3` (source `synthetic:membership`): “Saya menjadi anggota koperasi.” — supports “menjadi anggota koperasi”

**Candidate state:** `r1`: “tinggal di Bekasi”; `r2`: “bekerja di Pertamina”; `r3`: “menjadi anggota koperasi”

**Required claims (verifier input):** “tinggal di Bekasi”, “bekerja di Pertamina”, “menjadi anggota koperasi”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1
- WRITE; record_id=r2
- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-07 — valid_contradiction_correction — split: heldout

**Proposed decision (single reviewer):** `ACCEPT`
**Proposed fault types:** (none)
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out contradiction-backed correction; resolved conflict should remain acceptable.

**Prior state:** `r1`: “memiliki dua anak”

**Evidence:**

- `e1` (source `synthetic:correction`): “Koreksi: saya memiliki tiga anak, bukan dua.” — supports “memiliki tiga anak”; contradicts “memiliki dua anak”

**Candidate state:** `r1`: “memiliki tiga anak”

**Required claims (verifier input):** “memiliki tiga anak”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-08 — omission — split: heldout

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** omission
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out omission of an in-scope employment fact.

**Prior state:** `r1`: “tinggal di Medan”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja sebagai perawat.” — supports “bekerja sebagai perawat”

**Candidate state:** `r1`: “tinggal di Medan”

**Required claims (verifier input):** “tinggal di Medan”, “bekerja sebagai perawat”

**Declared operations:**

- NOOP

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-09 — corruption_no_evidence — split: heldout

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** corruption, omission
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** r1
**Reviewer note:** Held-out unevidenced mutation of a valid prior record.

**Prior state:** `r1`: “tinggal di Solo”; `r2`: “bekerja di industri batik”

**Evidence:**

(none)

**Candidate state:** `r1`: “tinggal di Jakarta”; `r2`: “bekerja di industri batik”

**Required claims (verifier input):** “tinggal di Solo”, “bekerja di industri batik”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-10 — corruption_insufficient_evidence — split: heldout

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** corruption, omission
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** r1
**Reviewer note:** Held-out corruption where available evidence is unrelated to the changed claim.

**Prior state:** `r1`: “bekerja di Semen Gresik”; `r2`: “tinggal di Gresik”

**Evidence:**

- `e1` (source `synthetic:pet`): “Saya memelihara dua kucing.” — supports “memelihara dua kucing”

**Candidate state:** `r1`: “bekerja di Holcim”; `r2`: “tinggal di Gresik”

**Required claims (verifier input):** “bekerja di Semen Gresik”, “tinggal di Gresik”

**Declared operations:**

- REVISE; record_id=r1; target_id=r1

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-11 — unsupported_addition — split: heldout

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** unsupported_addition, omission
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out unsupported addition plus omitted in-scope hobby fact.

**Prior state:** `r1`: “tinggal di Bali”; `r2`: “bekerja di hotel”

**Evidence:**

- `e1` (source `synthetic:hobby`): “Saya bermain tenis setiap minggu.” — supports “bermain tenis”

**Candidate state:** `r1`: “tinggal di Bali”; `r2`: “bekerja di hotel”; `r3`: “memiliki jet pribadi”

**Required claims (verifier input):** “tinggal di Bali”, “bekerja di hotel”, “bermain tenis”

**Declared operations:**

- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-12 — unsupported_provenance — split: heldout

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** unsupported_provenance
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out candidate claim ('memiliki izin pelayaran') has no provenance path.

**Prior state:** `r1`: “tinggal di Surabaya”; `r2`: “bekerja di pelabuhan”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya menjadi nakhoda.” — supports “menjadi nakhoda”

**Candidate state:** `r1`: “tinggal di Surabaya”; `r2`: “bekerja di pelabuhan”; `r3`: “menjadi nakhoda”, “memiliki izin pelayaran”

**Required claims (verifier input):** “tinggal di Surabaya”, “bekerja di pelabuhan”, “menjadi nakhoda”

**Declared operations:**

- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-13 — contradiction_unresolved — split: heldout

**Proposed decision (single reviewer):** `HOLD`
**Proposed fault types:** unresolved_contradiction
**Valid prior records expected to persist:** r1, r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out unresolved contradiction against a retained prior claim; intended structural graph case, expected text-baseline false accept.

**Prior state:** `r1`: “bekerja di Unilever”; `r2`: “tinggal di Tangerang”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya sekarang bekerja di Indofood.” — supports “bekerja di Indofood”; contradicts “bekerja di Unilever”

**Candidate state:** `r1`: “bekerja di Unilever”; `r2`: “tinggal di Tangerang”

**Required claims (verifier input):** “bekerja di Unilever”, “tinggal di Tangerang”

**Declared operations:**

- NOOP

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-14 — invalid_unauthorized_delete — split: heldout

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** unauthorized_deletion
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out DELETE with only a free-text reason; structured authorization is required.

**Prior state:** `r1`: “tinggal di Palembang”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di puskesmas.” — supports “bekerja di puskesmas”

**Candidate state:** (none)

**Required claims (verifier input):** “tinggal di Palembang”

**Declared operations:**

- DELETE; target_id=r1; reason=“bersihkan data”

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-15 — invalid_delete_authorization_target — split: heldout

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** unauthorized_deletion
**Valid prior records expected to persist:** r2
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out authorization target_id disagrees with the operation target; schema rejects.

**Prior state:** `r1`: “tinggal di Palembang”; `r2`: “bekerja di puskesmas”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di puskesmas.” — supports “bekerja di puskesmas”

**Candidate state:** `r2`: “bekerja di puskesmas”

**Required claims (verifier input):** “bekerja di puskesmas”

**Declared operations:**

- DELETE; target_id=r1; reason=“hapus data lama”; authorization={source=synthetic:user-request, evidence_id=e1, target_id=r2, rationale=“menghapus data lama”}

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-16 — invalid_undeclared_insertion — split: heldout

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** undeclared_state_change
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out undeclared insertion of a candidate record.

**Prior state:** `r1`: “tinggal di Makassar”

**Evidence:**

- `e1` (source `synthetic:employment`): “Saya bekerja di bank.” — supports “bekerja di bank”

**Candidate state:** `r1`: “tinggal di Makassar”; `r2`: “bekerja di bank”

**Required claims (verifier input):** “tinggal di Makassar”, “bekerja di bank”

**Declared operations:**

(none declared)

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-17 — invalid_undeclared_modification — split: heldout

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** undeclared_state_change
**Valid prior records expected to persist:** (none)
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out undeclared modification; evidence presence does not excuse a missing REVISE.

**Prior state:** `r1`: “memiliki satu anjing”

**Evidence:**

- `e1` (source `synthetic:possession`): “Sekarang saya memiliki dua anjing.” — supports “memiliki dua anjing”

**Candidate state:** `r1`: “memiliki dua anjing”

**Required claims (verifier input):** “memiliki dua anjing”

**Declared operations:**

(none declared)

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## HELD-18 — invalid_undeclared_removal — split: heldout

**Proposed decision (single reviewer):** `INVALID_INPUT`
**Proposed fault types:** undeclared_state_change
**Valid prior records expected to persist:** r1
**Corrupted prior record IDs:** (none)
**Reviewer note:** Held-out undeclared removal of a prior record.

**Prior state:** `r1`: “tinggal di Papua”; `r2`: “bekerja di tambang”

**Evidence:**

- `e1` (source `synthetic:relocation`): “Saya pindah tugas.” — supports “pindah tugas”

**Candidate state:** `r1`: “tinggal di Papua”; `r3`: “pindah tugas”

**Required claims (verifier input):** “tinggal di Papua”, “bekerja di tambang”, “pindah tugas”

**Declared operations:**

- WRITE; record_id=r3

**Second-reviewer fields (blank by design):**

| Field | Value |
|---|---|
| Second reviewer decision | |
| Second reviewer notes | |
| Agrees with proposed label? | |
| Adjudicated decision (if disagreement) | |
| Adjudicator / date | |

---

## Fabrication warning

An empty second-reviewer column is an explicit record that independent review has not happened. Any downstream analysis must treat these labels as single-reviewer exploratory annotations until a real reviewer completes the sheet and adjudication is recorded.
