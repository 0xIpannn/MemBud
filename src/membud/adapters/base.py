"""Structural interface and shared helpers for host memory adapters.

The adapter layer is deliberately architecture independent: every value that
crosses this boundary is a canonical :class:`membud.schemas.Record` and every
scope is a plain ``mapping[str, str]``.  Adapters translate native host state
to and from that canonical form; they must not leak host-specific objects into
the verification or persistence layers.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..schemas import Record


class AdapterContractError(RuntimeError):
    """Raised when an adapter or a guarded write violates the adapter contract."""


@runtime_checkable
class MemoryAdapter(Protocol):
    """Read canonical state and atomically replace the scoped state.

    Adapters are responsible for translating native records to/from canonical
    ``Record`` values. ``replace_state`` must be atomic for the supplied scope;
    hosts that cannot provide this guarantee must stage writes or decline use
    as a guarded adapter.

    The guarded integration in :mod:`membud.adapters.reference` NEVER calls
    ``replace_state`` unless MemBud verification returned ACCEPT, so an adapter
    that fails to be atomic cannot be made safe by this layer alone.  A host
    should only be labelled "guarded" after an interception test proves no
    mutation happens on HOLD.
    """

    backend_name: str

    def snapshot(self, scope: Mapping[str, str]) -> Sequence[Record]:
        """Return the current relevant state in canonical form."""
        ...

    def replace_state(self, scope: Mapping[str, str], records: Sequence[Record]) -> None:
        """Atomically replace the relevant state after an accepted transition."""
        ...


def scope_key(scope: Mapping[str, str]) -> str:
    """Return a deterministic JSON key for a scope mapping."""
    if not isinstance(scope, Mapping):
        raise TypeError("scope must be a mapping")
    normalized: dict[str, str] = {}
    for key, value in scope.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("scope keys and values must be strings")
        normalized[key] = value
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_records(records: Iterable[Record | Mapping[str, Any]]) -> tuple[Record, ...]:
    """Validate and freeze records as canonical ``Record`` values.

    Accepts canonical records or plain dicts (mapping-compatible) so adapters
    do not need to import host-specific types.  Rejects duplicate IDs so a
    backend cannot silently drop state.
    """
    if isinstance(records, (str, bytes)) or not isinstance(records, Iterable):
        raise TypeError("records must be an iterable of canonical records")
    normalized: list[Record] = []
    for item in records:
        if isinstance(item, Record):
            record = item
        elif isinstance(item, Mapping):
            record = Record.from_dict(dict(item))
        else:
            raise TypeError(
                f"records must be Record values or dicts, got {type(item).__name__}"
            )
        if not record.claims:
            raise ValueError(f"record {record.id!r} must have non-empty claims")
        normalized.append(record)
    ids = [record.id for record in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("records must have unique IDs")
    return tuple(normalized)


@dataclass(frozen=True)
class PriorState:
    """An immutable snapshot of backend state taken before a proposed write."""

    scope: tuple[tuple[str, str], ...]
    records: tuple[Record, ...]
    backend_name: str = ""

    def claims_by_id(self) -> dict[str, frozenset[str]]:
        return {record.id: frozenset(record.claims) for record in self.records}

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return {
            "scope": dict(self.scope),
            "backend_name": self.backend_name,
            "records": [asdict(record) for record in self.records],
        }


def capture_prior_state(
    adapter: MemoryAdapter, scope: Mapping[str, str]
) -> PriorState:
    """Snapshot the scoped prior state and validate it as canonical records.

    This is the explicit prior-state capture required before a guarded write;
    the returned value is compared again immediately before persisting so a
    stale proposal cannot overwrite concurrently changed backend state.
    """
    if not isinstance(adapter, MemoryAdapter):  # defensive, structural check
        raise AdapterContractError("adapter does not implement MemoryAdapter")
    records = canonical_records(adapter.snapshot(scope))
    return PriorState(
        scope=tuple(sorted((str(key), str(value)) for key, value in scope.items())),
        records=records,
        backend_name=str(getattr(adapter, "backend_name", "")),
    )


def prior_state_matches(adapter: MemoryAdapter, scope: Mapping[str, str], prior: PriorState) -> bool:
    """Return True when a fresh backend snapshot equals ``prior`` exactly."""
    current = capture_prior_state(adapter, scope)
    return current.records == prior.records
