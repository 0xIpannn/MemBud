"""Explicit format versioning and migration policy for interchange data.

MemBud has two persisted interchange formats: the canonical transition package
(:class:`membud.schemas.Transition`) and the versioned store written by
:mod:`membud.store`. This module makes the migration policy executable:

* **Transition v2 is current.** Version 1 input (no ``schema_version``, or
  ``schema_version: 1``) is frozen archival data. It loads with legacy
  semantics and *round-trips as v1* so archived artifacts keep their original
  bytes. Upgrading a v1 transition to v2 is an explicit, caller-driven action:
  :func:`migrate_transition_to_v2`. It is not automatic because v1 permits
  undeclared state changes that v2 rejects; a silent upgrade would either
  change semantics or surprise archival readers.
* **Store v2 is current.** A legacy bare-list store is read as store schema
  version 1 / version 1 and is migrated to v2 by the next ``ACCEPT`` (never by
  a ``HOLD``). ``schema_version`` is the format version; ``version`` is the
  optimistic-concurrency counter.
* **Unknown future versions are rejected**, not guessed at. Forward
  compatibility is not promised.

The normative policy and change checklist live in ``docs/format-policy.md``.
"""
from __future__ import annotations

from .schemas import SCHEMA_VERSION, Transition
from .store import LEGACY_STORE_SCHEMA_VERSION, STORE_SCHEMA_VERSION

TRANSITION_SCHEMA_VERSION = SCHEMA_VERSION
SUPPORTED_TRANSITION_SCHEMA_VERSIONS = (LEGACY_STORE_SCHEMA_VERSION, SCHEMA_VERSION)
SUPPORTED_STORE_SCHEMA_VERSIONS = (LEGACY_STORE_SCHEMA_VERSION, STORE_SCHEMA_VERSION)

#: Normative one-paragraph statement quoted by the policy document and tests.
MIGRATION_POLICY = (
    "Transition v2 and store v2 are the current interchange formats. Transition "
    "v1 is archival-only: it loads with legacy semantics and round-trips as v1; "
    "callers must explicitly call migrate_transition_to_v2() to upgrade it, and "
    "the upgrade fails if the legacy transition has undeclared state changes. "
    "A legacy bare-list store (v1) migrates to v2 on the next ACCEPT and is never "
    "rewritten or reformatted by a HOLD. Unknown future format versions are "
    "rejected rather than guessed at; forward compatibility is not promised."
)


def transition_needs_migration(t: Transition) -> bool:
    """True when ``t`` is a legacy v1 transition that can be explicitly upgraded."""
    if not isinstance(t, Transition):
        raise TypeError("transition_needs_migration requires a Transition")
    if t.schema_version == SCHEMA_VERSION:
        return False
    if t.schema_version == LEGACY_STORE_SCHEMA_VERSION:
        return True
    raise ValueError(f"unsupported transition schema_version: {t.schema_version!r}")


def migrate_transition_to_v2(t: Transition) -> Transition:
    """Explicitly upgrade a legacy v1 transition to the current v2 format.

    Idempotent for an already-current transition (the same object is returned).
    Raises ``ValueError`` if the legacy transition has a state change that v2's
    strict operation-coverage invariants do not declare; those transitions must
    stay archival v1 because upgrading would silently change their meaning.
    """
    if not isinstance(t, Transition):
        raise TypeError("migrate_transition_to_v2 requires a Transition")
    t.validate()
    if t.schema_version == SCHEMA_VERSION:
        return t
    if t.schema_version != LEGACY_STORE_SCHEMA_VERSION:
        raise ValueError(f"unsupported transition schema_version: {t.schema_version!r}")
    upgraded = Transition(
        case_id=t.case_id,
        before=t.before,
        candidate=t.candidate,
        evidence=t.evidence,
        required_claims=t.required_claims,
        operations=t.operations,
        schema_version=SCHEMA_VERSION,
    )
    upgraded.validate()
    return upgraded


def store_needs_migration(schema_version: int | None) -> bool:
    """True when a loaded store format is legacy (v1) or absent (version 0)."""
    if schema_version is None:
        return True
    if schema_version == STORE_SCHEMA_VERSION:
        return False
    if schema_version == LEGACY_STORE_SCHEMA_VERSION:
        return True
    raise ValueError(f"unsupported store schema_version: {schema_version!r}")


__all__ = [
    "MIGRATION_POLICY",
    "SUPPORTED_STORE_SCHEMA_VERSIONS",
    "SUPPORTED_TRANSITION_SCHEMA_VERSIONS",
    "TRANSITION_SCHEMA_VERSION",
    "migrate_transition_to_v2",
    "store_needs_migration",
    "transition_needs_migration",
]
