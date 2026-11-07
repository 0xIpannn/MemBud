"""Backward-compatible public API for the modular MemBud MVP.

New code should import from the focused modules directly. Existing imports from
``membud.core`` continue to work.
"""

from .graph import build_graph, to_mermaid
from .schemas import Check, Evidence, Operation, Record, Result, Transition
from .serialization import textual_representation, transition_dict
from .store import commit, run_case
from .verification import verify

__all__ = [
    "Check",
    "Evidence",
    "Operation",
    "Record",
    "Result",
    "Transition",
    "build_graph",
    "commit",
    "run_case",
    "textual_representation",
    "to_mermaid",
    "transition_dict",
    "verify",
]
