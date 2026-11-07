"""Host-memory adapter contracts and the guarded reference integration."""
from .base import (
    AdapterContractError,
    MemoryAdapter,
    PriorState,
    canonical_records,
    capture_prior_state,
    prior_state_matches,
    scope_key,
)
from .reference import GuardedDecision, GuardedMemory, InMemoryBackend, SQLiteBackend

__all__ = [
    "AdapterContractError",
    "GuardedDecision",
    "GuardedMemory",
    "InMemoryBackend",
    "MemoryAdapter",
    "PriorState",
    "SQLiteBackend",
    "canonical_records",
    "capture_prior_state",
    "prior_state_matches",
    "scope_key",
]
