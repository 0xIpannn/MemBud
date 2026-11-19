"""Backward-compatible public API for the modular MemBud MVP.

New code should import from the focused modules directly. Existing imports from
``membud.core`` continue to work.
"""

from .conversation import (
    ConversationError,
    ConversationTranscript,
    ConversationTurn,
    LLMConversationExtractor,
    RuleBasedConversationExtractor,
    build_transition_from_conversation,
    load_conversation,
    parse_conversation,
    run_conversation_session,
    verify_transcript_grounding,
    verify_with_transcript,
)
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
    "ConversationError",
    "ConversationTranscript",
    "ConversationTurn",
    "Evidence",
    "LLMConversationExtractor",
    "Operation",
    "Record",
    "RuleBasedConversationExtractor",
    "MIGRATION_POLICY",
    "Result",
    "Transition",
    "build_graph",
    "build_transition_from_conversation",
    "commit",
    "load_conversation",
    "migrate_transition_to_v2",
    "parse_conversation",
    "run_case",
    "run_conversation_session",
    "store_needs_migration",
    "textual_representation",
    "to_mermaid",
    "transition_dict",
    "transition_needs_migration",
    "verify",
    "verify_transcript_grounding",
    "verify_with_llm",
    "verify_with_transcript",
]
