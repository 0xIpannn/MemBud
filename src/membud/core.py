"""Backward-compatible public API for the modular MemBud MVP.

New code should import from the focused modules directly. Existing imports from
``membud.core`` continue to work.
"""

from .graph import build_graph, to_mermaid
from .migrations import (
    MIGRATION_POLICY,
    migrate_transition_to_v2,
    store_needs_migration,
    transition_needs_migration,
)
from .schemas import Check, Evidence, Operation, Record, Result, Transition
from .serialization import textual_representation, transition_dict
from .store import commit, run_case
from .verification import verify, verify_with_llm

__all__ = [
    "Check",
    "Evidence",
    "Operation",
    "Record",
    "MIGRATION_POLICY",
    "Result",
    "Transition",
    "build_graph",
    "commit",
    "migrate_transition_to_v2",
    "run_case",
    "store_needs_migration",
    "textual_representation",
    "to_mermaid",
    "transition_dict",
    "transition_needs_migration",
    "verify",
    "verify_with_llm",
]
