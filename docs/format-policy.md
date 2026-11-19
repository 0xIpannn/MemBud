# Format versioning and migration policy

**Status:** normative for the canonical transition package and the persisted store.
**Implementation:** `src/membud/migrations.py`, `src/membud/schemas.py`, `src/membud/store.py`.
**Tests:** `tests/test_format_migration.py` (cross-version matrix), plus the existing schema/store suites.
**Plan item:** `PLAN.md` §19 / TODO “Next up” #5 (stable versioned interchange formats).

## 1. Scope

MemBud has two persisted interchange formats:

| Format | Where | Current version |
|---|---|---|
| Canonical transition package | JSON accepted by `Transition.from_dict`, emitted by `transition_dict` | **2** |
| Versioned store | JSON file written by `membud.store` | **2** |

`schema_version` identifies the **format**. The store additionally carries
`version`, an integer **optimistic-concurrency counter** incremented exactly once
per `ACCEPT`; it is not a format version.

Everything else (audit records, result JSON, manifests) is read-only experiment
output and is versioned by its own artifact path, not by this policy.

## 2. Version registry and compatibility matrix

| Input | Read | Write/round-trip | Notes |
|---|---|---|---|
| Transition, no `schema_version` (v1) | yes | stays **v1**; **not committable** | Frozen archival data. Legacy semantics: strict operation-coverage invariants are not enforced, so undeclared state changes may exist. `store.run_case`/`store.commit` refuse it unless `allow_legacy=True` is passed for an explicit archival replay. |
| Transition, `schema_version: 1` | yes | stays **v1**; **not committable** | Same as version-less input; `supersedes` is normalized to explicit operations on load. |
| Transition, `schema_version: 2` | yes | **v2** | Current format; strict operation-coverage invariants are enforced and this is the only format the commit path accepts by default. |
| Transition, any other version or non-integer `schema_version` | **rejected** | — | `ValueError`; booleans/floats/strings are never reinterpreted as an integer version. No forward compatibility is promised. |
| Store, absent file | yes (empty) | becomes **v2** on ACCEPT | Treated as version 0 for `expected_version`. |
| Store, bare JSON list (v1) | yes | stays **v1** on HOLD; **v2** on ACCEPT | Read as schema v1 / `version: 1`; migrated by the next ACCEPT only. |
| Store, versioned object (`schema_version: 2`) | yes | **v2** | Current format. |
| Store, any other `schema_version` | **rejected** | — | Fail closed with an audit error; bytes are left untouched. |

### Guarantees

1. **HOLD never writes.** A held decision leaves the store file byte-identical,
   including a legacy v1 store; a HOLD never creates, rewrites, or reformats a
   store.
2. **No silent upgrade of transitions.** Loading v1 data and serializing it again
   yields v1. Upgrading is an explicit API call (section 3).
3. **No silent downgrade or guess.** Unknown future versions are rejected, and
   `schema_version` must be a JSON integer (`true`/`1.0`/`"2"` are rejected
   rather than coerced).
4. **Legacy transitions are archival only on the commit path.** `verify()` still
   accepts v1 for archival analysis, but `store.run_case`/`store.commit` require
   the current transition version unless the caller passes an explicit
   `allow_legacy=True` replay flag; the written store envelope is always v2.
5. **Store migration is explicit in timing, implicit in trigger.** The first
   `ACCEPT` after a legacy/absent store writes the v2 envelope and increments
   the store `version` once. Later `ACCEPT`s increment the counter
   (`2 -> 3 -> ...`) without re-migrating.
6. **Audit trail.** Audit events record `store_schema_version_before`,
   `store_schema_version_after`, `store_version_before`, `store_version_after`,
   and `store_written`/`rolled_back`, so a migration and a failed/rolled-back
   write are visible after the fact.
7. **Deterministic JSON.** Stores are written with a fixed key set
   (`schema_version`, `version`, `records`), sorted/deterministic record order
   from the transition, `indent=2`, and a trailing newline. Transitions emit
   `schema_version`, `case_id`, `before`, `candidate`, `evidence`,
   `required_claims`, and `operations`.

## 3. Transition migration API

```python
from membud.migrations import (
    MIGRATION_POLICY,
    migrate_transition_to_v2,
    transition_needs_migration,
)

legacy = Transition.from_dict(raw_v1)          # schema_version == 1
assert transition_needs_migration(legacy)

current = migrate_transition_to_v2(legacy)     # schema_version == 2
assert not transition_needs_migration(current)
```

- `migrate_transition_to_v2()` is **idempotent**: passing an already-v2
  transition returns the same object.
- It re-validates the transition under v2's strict invariants. If the legacy
  data contains an undeclared insertion/modification/removal, migration raises
  `ValueError`; such data must stay archival v1 because upgrading would change
  its meaning. Do not "fix" it by inventing operations.
- It raises `TypeError` for non-`Transition` input and `ValueError` for an
  unsupported version.

The compatibility facade `membud.core` re-exports
`migrate_transition_to_v2`, `transition_needs_migration`,
`store_needs_migration`, and `MIGRATION_POLICY`.

## 4. Store migration mechanics

- Parsing: absent file -> version 0; bare list -> schema v1 / version 1;
  versioned object -> schema v2 with the stored counter. Anything else is
  rejected before verification.
- Prior-state reconciliation happens **after** parsing: the parsed records must
  match `transition.before`, otherwise the run fails closed with an audit error.
- On ACCEPT the complete candidate is written atomically (temp file + fsync +
  rename) as a v2 envelope with `version = version_before + 1`; on HOLD nothing
  is written.
- `expected_version` compares against the **store counter** (0 for an absent
  store, 1 for a legacy store). A conflict fails closed and leaves state
  untouched.
- A legacy store is therefore migrated exactly once, on the first ACCEPT. There
  is no separate offline migration command; re-serializing a store without an
  accepted transition is not supported.

## 5. Release checklist for a format change

A format change is a breaking change until this checklist is complete:

1. Bump the relevant version constant (`SCHEMA_VERSION` /
   `STORE_SCHEMA_VERSION`) and add the old version to the accepted set only if
   archival reads must keep working.
2. Add an explicit migration/reader path; never reinterpret old bytes silently.
   For transitions, follow the explicit `migrate_transition_to_v2` model.
3. Add hand-written frozen fixtures for every still-readable old version, not
   fixtures produced by the current writer.
4. Add the version to the compatibility matrix here and to
   `docs/format-policy.md`'s tests (migration success, migration refusal, HOLD
   byte-identity, unknown-version rejection).
5. Keep historical result artifacts and their recorded `schema_version` /
   `prompt_version` unchanged; regenerate rather than mutate.
6. Run the full test suite and update `README.md` / `TODO.md` / `PLAN.md`.
7. If forward compatibility is ever promised, it requires a separate design;
   it is not promised today.

## 6. Not promised

- Forward compatibility with future format versions.
- Automatic v1 -> v2 transition upgrades (explicit by design).
- Semantic preservation across arbitrary transformations; a migration changes
  the format, not the meaning, and fails closed when it cannot prove that.
- Migration of unknown versions or of corrupted stores.
- Stable audit/result JSON as an interchange format; those are experiment
  artifacts, not versioned protocols.
