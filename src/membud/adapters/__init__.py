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
from .mem0_bridge import (
    MEM0_RELEASE,
    MEM0_SHAPE_CONTRACT,
    MEM0_WHEEL,
    MEM0_WHEEL_SHA256,
    Mem0BridgeError,
    Mem0ProposalBridge,
    StagedProposal,
    apply_staged,
    mem0_precommit_contract,
)
from .reference import GuardedDecision, GuardedMemory, InMemoryBackend, SQLiteBackend

__all__ = [
    "AdapterContractError",
    "GuardedDecision",
    "GuardedMemory",
    "InMemoryBackend",
    "MEM0_RELEASE",
    "MEM0_SHAPE_CONTRACT",
    "MEM0_WHEEL",
    "MEM0_WHEEL_SHA256",
    "Mem0BridgeError",
    "Mem0ProposalBridge",
    "MemoryAdapter",
    "PriorState",
    "SQLiteBackend",
    "StagedProposal",
    "apply_staged",
    "canonical_records",
    "capture_prior_state",
    "mem0_precommit_contract",
    "prior_state_matches",
    "scope_key",
]
