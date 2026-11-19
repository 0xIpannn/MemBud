"""Conversation ingestion, transcript-grounded evidence checks, and extractors.

This module connects MemBud's canonical transition verification to actual
multi-turn conversation transcripts rather than pre-packaged fixtures alone:

1. **Canonical transcript schema** (:class:`ConversationTurn`,
   :class:`ConversationTranscript`) with strict validation and lossless
   round-tripping.
2. **Multi-format parsers** (:func:`parse_conversation`, :func:`load_conversation`,
   :func:`load_conversations_jsonl`) for MemBud JSON, OpenAI-style chat messages,
   ``opencode run --format json`` event streams, HaluMem-shaped session objects,
   and plain-text / Markdown transcripts.
3. **Transcript-grounded evidence verification** (:func:`verify_transcript_grounding`,
   :func:`verify_with_transcript`), closing the caller-self-asserted evidence
   boundary when a real conversation transcript is supplied: evidence items must
   resolve to an allowed-role turn with a verbatim substring quote and lexical
   claim grounding, and ``DELETE`` authorizations must match a grounded deletion
   directive.
4. **Extractors and multi-turn runner** (:class:`RuleBasedConversationExtractor`,
   :class:`LLMConversationExtractor`, :func:`build_transition_from_conversation`,
   :func:`run_conversation_session`) plus a CLI (``python -m membud.conversation``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fairness import verify_text_fair
from .schemas import SCHEMA_VERSION, Check, DeletionAuthorization, Evidence, Operation, Record, Result, Transition
from .semantic import verify_with_semantics
from .serialization import transition_dict
from .store import _append_event, _atomic_write, _parse_store, _serialize_store, run_case
from .verification import verify

ALLOWED_ROLES = ("user", "assistant", "system", "tool")
ROLE_ALIASES = {
    "human": "user",
    "pengguna": "user",
    "ai": "assistant",
    "bot": "assistant",
    "model": "assistant",
    "asisten": "assistant",
}
DEFAULT_GROUNDING_ROLES = ("user",)
TRANSCRIPT_CHECK_NAME = "transcript_grounding"
TRANSCRIPT_REASON_CODES = frozenset(
    {
        "TRANSCRIPT_GROUNDING_OK",
        "UNGROUNDED_EVIDENCE_SOURCE",
        "UNGROUNDED_EVIDENCE_QUOTE",
        "DISALLOWED_EVIDENCE_ROLE",
        "UNGROUNDED_EVIDENCE_CLAIM",
        "UNGROUNDED_DELETE_AUTHORIZATION",
    }
)
CONVERSATION_EXTRACTOR_PROMPT_VERSION = "membud-conversation-extract-v1"
SUPPORTED_FORMATS = ("auto", "membud", "openai", "opencode", "halumem", "text")
SESSION_CONDITIONS = ("unguarded", "graph", "text", "text_fair", "semantic")

_STOPWORDS = frozenset(
    {
        # English function words
        "the", "and", "for", "with", "that", "this", "from", "have", "has", "had",
        "was", "were", "are", "been", "not", "but", "you", "your", "she", "her",
        "his", "they", "their", "our", "out", "into", "about", "now", "also",
        # Indonesian function words
        "dan", "yang", "untuk", "dengan", "dari", "pada", "dalam", "atau", "juga",
        "ini", "itu", "saya", "aku", "kamu", "anda", "dia", "mereka", "kita",
        "kami", "adalah", "akan", "sudah", "telah", "belum", "tidak", "bukan",
        "ada", "lagi", "bisa", "masih", "sekarang", "bahwa", "karena", "oleh",
        "sebagai",
    }
)

_DELETE_INTENT_RE = re.compile(
    r"(?:\b(?:hapus|menghapus|dihapus|lupakan|hilangkan|delete|deleted|deleting|"
    r"remove|removed|removing|forget|forgotten|drop|dropped)\b|"
    r"tidak\s+relevan|no\s+longer\s+relevant)",
    re.IGNORECASE,
)

_TEXT_TURN_RE = re.compile(
    r"^(?:\[(?P<id>[A-Za-z0-9_.:-]+)\]\s*)?"
    r"(?P<role>user|assistant|system|tool|human|ai|bot|model|pengguna|asisten)\s*:\s*"
    r"(?P<content>.*)$",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(
    r"(?<!\bPT)(?<!\bCV)(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\bNo)"
    r"(?<!\d)[.!?\n]+(?!\d)\s*"
)


class ConversationError(ValueError):
    """Raised when conversation data or transcript-grounded proposals are invalid."""


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _normalize_role(role: Any) -> str:
    if not isinstance(role, str) or not role.strip():
        raise ConversationError("turn role must be a non-empty string")
    normalized = role.strip().lower()
    normalized = ROLE_ALIASES.get(normalized, normalized)
    if normalized not in ALLOWED_ROLES:
        raise ConversationError(f"unsupported conversation role: {role!r}")
    return normalized


def _ensure_json_dict(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConversationError(f"{field_name} must be a mapping")
    copied = dict(value)
    try:
        json.dumps(copied, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ConversationError(f"{field_name} must be JSON-serializable: {exc}") from exc
    return copied


def _ensure_scope(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConversationError("scope must be a mapping")
    scope: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ConversationError("scope keys and values must be strings")
        scope[key] = item
    return scope


def _content_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[0-9a-zA-Z]+", text.lower())
    return {
        token
        for token in tokens
        if (len(token) >= 3 or token.isdigit()) and token not in _STOPWORDS
    }


@dataclass(frozen=True)
class ConversationTurn:
    """One normalized turn in a multi-turn conversation transcript."""

    turn_id: str
    role: str
    content: str
    speaker: str = ""
    timestamp: str = ""
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.turn_id, str) or not self.turn_id.strip():
            raise ConversationError("turn_id must be a non-empty string")
        object.__setattr__(self, "role", _normalize_role(self.role))
        if not isinstance(self.content, str):
            raise ConversationError("turn content must be a string")
        for attr in ("speaker", "timestamp", "session_id"):
            val = getattr(self, attr)
            if not isinstance(val, str):
                raise ConversationError(f"turn {attr} must be a string")
        object.__setattr__(self, "metadata", _ensure_json_dict(self.metadata, "turn metadata"))

    @property
    def source_ref(self) -> str:
        """Canonical source reference stored on ``Evidence.source``."""
        if self.session_id:
            return f"session:{self.session_id}:turn:{self.turn_id}"
        return f"turn:{self.turn_id}"

    def matches_source_ref(self, ref: str) -> bool:
        """Return True when ``ref`` identifies this turn."""
        if not isinstance(ref, str) or not ref.strip():
            return False
        candidate = ref.strip()
        accepted = {self.turn_id, f"turn:{self.turn_id}", f"chat:{self.turn_id}"}
        if self.session_id:
            accepted.add(f"session:{self.session_id}:turn:{self.turn_id}")
            accepted.add(f"{self.session_id}:{self.turn_id}")
        return candidate in accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "role": self.role,
            "content": self.content,
            "speaker": self.speaker,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(
        cls,
        d: Mapping[str, Any],
        *,
        default_turn_id: str = "",
        default_session_id: str = "",
    ) -> "ConversationTurn":
        if not isinstance(d, Mapping):
            raise ConversationError("conversation turn must be an object")
        if "turn_id" in d:
            turn_id = d["turn_id"]
        elif "id" in d:
            turn_id = d["id"]
        else:
            turn_id = default_turn_id
        if "role" not in d:
            raise ConversationError("turn must declare role")
        if "content" in d:
            content = d["content"]
        elif "text" in d:
            content = d["text"]
        else:
            raise ConversationError("turn must declare content")
        speaker = d.get("speaker", "")
        if "timestamp" in d:
            timestamp = d["timestamp"]
        elif "created_at" in d:
            timestamp = d["created_at"]
        else:
            timestamp = ""
        if "session_id" in d:
            session_id = d["session_id"]
        elif "sessionID" in d:
            session_id = d["sessionID"]
        else:
            session_id = default_session_id
        return cls(
            turn_id=turn_id,
            role=d["role"],
            content=content,
            speaker="" if speaker is None else speaker,
            timestamp="" if timestamp is None else timestamp,
            session_id="" if session_id is None else session_id,
            metadata=d.get("metadata", {}),
        )


@dataclass(frozen=True)
class ConversationTranscript:
    """Validated multi-turn conversation transcript."""

    conversation_id: str
    turns: tuple[ConversationTurn, ...]
    scope: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ConversationError("conversation_id must be a non-empty string")
        if isinstance(self.turns, (str, bytes)) or not isinstance(self.turns, Iterable):
            raise ConversationError("turns must be an iterable of ConversationTurn")
        turns_tuple = tuple(self.turns)
        if not turns_tuple:
            raise ConversationError("conversation transcript must contain at least one turn")
        for turn in turns_tuple:
            if not isinstance(turn, ConversationTurn):
                raise ConversationError("all turns must be ConversationTurn instances")
        turn_ids = [turn.turn_id for turn in turns_tuple]
        if len(turn_ids) != len(set(turn_ids)):
            raise ConversationError("turn_ids within a conversation must be unique")
        object.__setattr__(self, "turns", turns_tuple)
        object.__setattr__(self, "scope", _ensure_scope(self.scope))
        object.__setattr__(self, "metadata", _ensure_json_dict(self.metadata, "transcript metadata"))

    def turn_by_id(self, ref: str) -> ConversationTurn | None:
        """Resolve a turn by exact ``turn_id`` or canonical source reference."""
        if not isinstance(ref, str) or not ref.strip():
            return None
        needle = ref.strip()
        for turn in self.turns:
            if turn.turn_id == needle:
                return turn
        for turn in self.turns:
            if turn.matches_source_ref(needle):
                return turn
        return None

    def user_turns(self) -> tuple[ConversationTurn, ...]:
        """Return only user-authored turns in chronological order."""
        return tuple(turn for turn in self.turns if turn.role == "user")

    def slice_turns(
        self,
        *,
        up_to_turn_id: str | None = None,
        turn_ids: Iterable[str] | None = None,
    ) -> "ConversationTranscript":
        """Return a sub-transcript up to ``up_to_turn_id`` or restricted to ``turn_ids``."""
        if up_to_turn_id is not None and turn_ids is not None:
            raise ConversationError("specify at most one of up_to_turn_id or turn_ids")
        if up_to_turn_id is not None:
            cutoff = None
            for index, turn in enumerate(self.turns):
                if turn.turn_id == up_to_turn_id or turn.matches_source_ref(up_to_turn_id):
                    cutoff = index
                    break
            if cutoff is None:
                raise ConversationError(f"up_to_turn_id not found in transcript: {up_to_turn_id!r}")
            selected = self.turns[: cutoff + 1]
        elif turn_ids is not None:
            requested = list(turn_ids)
            if not requested:
                raise ConversationError("turn_ids must not be empty")
            resolved_ids: set[str] = set()
            for ref in requested:
                matched = self.turn_by_id(ref)
                if matched is None:
                    raise ConversationError(f"turn_id not found in transcript: {ref!r}")
                resolved_ids.add(matched.turn_id)
            selected = tuple(turn for turn in self.turns if turn.turn_id in resolved_ids)
        else:
            selected = self.turns
        return ConversationTranscript(
            conversation_id=self.conversation_id,
            turns=selected,
            scope=dict(self.scope),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turns": [turn.to_dict() for turn in self.turns],
            "scope": dict(self.scope),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(
        cls,
        d: Any,
        *,
        conversation_id: str = "conversation-1",
    ) -> "ConversationTranscript":
        if not isinstance(d, Mapping):
            raise ConversationError("conversation transcript must be a JSON object")
        raw_turns = d.get("turns")
        if not isinstance(raw_turns, list) or not raw_turns:
            raise ConversationError("conversation transcript must contain a non-empty turns list")
        conv_id = d.get("conversation_id", conversation_id)
        turns = tuple(
            ConversationTurn.from_dict(item, default_turn_id=f"t{index}")
            for index, item in enumerate(raw_turns, start=1)
        )
        return cls(
            conversation_id=conv_id,
            turns=turns,
            scope=d.get("scope", {}),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Multi-format conversation parsers
# ---------------------------------------------------------------------------


def _extract_openai_content(raw_content: Any) -> str:
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        parts: list[str] = []
        for block in raw_content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                block_type = block.get("type", "text")
                if block_type == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
                else:
                    raise ConversationError("unsupported content block in OpenAI message")
            else:
                raise ConversationError("OpenAI message content blocks must be strings or objects")
        return "\n".join(parts)
    raise ConversationError("OpenAI message content must be a string or list of content blocks")


def _timestamp_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ConversationError("timestamp must be a string or number, not bool")
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise ConversationError(f"invalid timestamp type: {type(value).__name__}")


def _parse_openai(raw: Any, *, conversation_id: str) -> ConversationTranscript:
    if isinstance(raw, Mapping):
        messages = raw.get("messages") if "messages" in raw else raw.get("dialogue")
        conv_id = raw.get("conversation_id") or raw.get("id") or conversation_id
        scope = raw.get("scope", {})
        metadata = _ensure_json_dict(raw.get("metadata", {}), "transcript metadata")
        if "model" in raw and "model" not in metadata:
            metadata["model"] = raw["model"]
    elif isinstance(raw, (list, tuple)):
        messages = list(raw)
        conv_id = conversation_id
        scope = {}
        metadata = {}
    else:
        raise ConversationError("OpenAI conversation must be a list of messages or an object")

    if not isinstance(messages, list) or not messages:
        raise ConversationError("OpenAI conversation must contain at least one message")

    turns: list[ConversationTurn] = []
    for index, msg in enumerate(messages, start=1):
        if not isinstance(msg, Mapping):
            raise ConversationError("each OpenAI message must be an object")
        if "role" not in msg:
            raise ConversationError("OpenAI message missing required 'role'")
        if "content" not in msg:
            raise ConversationError("OpenAI message missing required 'content'")
        content = _extract_openai_content(msg["content"])
        if "turn_id" in msg:
            turn_id = msg["turn_id"]
        elif "id" in msg:
            turn_id = msg["id"]
        else:
            turn_id = f"t{index}"
        speaker = msg.get("speaker", msg.get("name", ""))
        ts = _timestamp_str(msg.get("timestamp", msg.get("created_at", "")))
        session_id = msg.get("session_id", "")
        msg_meta = _ensure_json_dict(msg.get("metadata", {}), "turn metadata")
        turns.append(
            ConversationTurn(
                turn_id=turn_id,
                role=msg["role"],
                content=content,
                speaker="" if speaker is None else str(speaker),
                timestamp=ts,
                session_id="" if session_id is None else str(session_id),
                metadata=msg_meta,
            )
        )
    return ConversationTranscript(
        conversation_id=str(conv_id),
        turns=tuple(turns),
        scope=scope,
        metadata=metadata,
    )


def _is_opencode_event(item: Mapping[str, Any]) -> bool:
    event_type = item.get("type")
    if isinstance(event_type, str) and event_type in {
        "step_start", "step_finish", "tool_use", "text", "user", "message",
    }:
        return True
    return isinstance(item.get("part"), Mapping) or "sessionID" in item


def _parse_opencode(raw: Any, *, conversation_id: str) -> ConversationTranscript:
    if isinstance(raw, str):
        events = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ConversationError(f"invalid opencode JSONL line: {exc}") from exc
            if not isinstance(parsed, Mapping):
                raise ConversationError("opencode JSONL lines must be JSON objects")
            events.append(parsed)
    elif isinstance(raw, Mapping):
        if "events" in raw:
            events = raw["events"]
        elif "messages" in raw:
            events = raw["messages"]
        else:
            raise ConversationError("opencode object must contain 'events' or 'messages'")
    elif isinstance(raw, (list, tuple)):
        events = list(raw)
    else:
        raise ConversationError("opencode input must be JSONL text, event list, or mapping")

    if not isinstance(events, list) or not events:
        raise ConversationError("opencode event stream is empty")

    turns: list[ConversationTurn] = []
    seen_session_id = ""
    pending_assistant_idx: int | None = None
    pending_message_id: str | None = None

    for event in events:
        if not isinstance(event, Mapping):
            raise ConversationError("each opencode event must be an object")
        event_type = str(event.get("type", "")).strip()
        part = event.get("part")
        part_map = part if isinstance(part, Mapping) else {}
        session_id = str(
            event.get("sessionID")
            or part_map.get("sessionID")
            or event.get("session_id")
            or ""
        )
        if session_id and not seen_session_id:
            seen_session_id = session_id
        timestamp = _timestamp_str(event.get("timestamp", part_map.get("timestamp", "")))

        if event_type in {"step_start", "step_finish"}:
            pending_assistant_idx = None
            pending_message_id = None
            continue

        if event_type == "tool_use":
            pending_assistant_idx = None
            pending_message_id = None
            tool_name = str(part_map.get("tool") or event.get("tool") or "tool")
            state = part_map.get("state")
            state_map = state if isinstance(state, Mapping) else {}
            status = state_map.get("status", "")
            tool_summary = {
                "tool": tool_name,
                "status": status,
                "input": state_map.get("input"),
                "output": state_map.get("output"),
                "error": state_map.get("error"),
            }
            turn_id = str(part_map.get("callID") or part_map.get("id") or f"t{len(turns) + 1}")
            turns.append(
                ConversationTurn(
                    turn_id=turn_id,
                    role="tool",
                    content=json.dumps(tool_summary, ensure_ascii=False, sort_keys=True),
                    speaker=tool_name,
                    timestamp=timestamp,
                    session_id=session_id,
                    metadata={"event_type": "tool_use"},
                )
            )
            continue

        if event_type == "text":
            text = part_map.get("text") if "text" in part_map else event.get("text")
            if not isinstance(text, str):
                raise ConversationError("opencode 'text' event must contain string text")
            msg_id = str(part_map.get("messageID") or "")
            if pending_assistant_idx is not None and msg_id and msg_id == pending_message_id:
                prev = turns[pending_assistant_idx]
                turns[pending_assistant_idx] = ConversationTurn(
                    turn_id=prev.turn_id,
                    role=prev.role,
                    content=f"{prev.content}\n{text}" if prev.content else text,
                    speaker=prev.speaker,
                    timestamp=prev.timestamp or timestamp,
                    session_id=prev.session_id or session_id,
                    metadata=prev.metadata,
                )
                continue
            turn_id = str(part_map.get("id") or msg_id or f"t{len(turns) + 1}")
            turns.append(
                ConversationTurn(
                    turn_id=turn_id,
                    role="assistant",
                    content=text,
                    timestamp=timestamp,
                    session_id=session_id,
                    metadata={"event_type": "text"},
                )
            )
            pending_assistant_idx = len(turns) - 1
            pending_message_id = msg_id or None
            continue

        if event_type in {"user", "message"} or "role" in event:
            pending_assistant_idx = None
            pending_message_id = None
            info = event.get("info")
            info_map = info if isinstance(info, Mapping) else {}
            role = event.get("role") or info_map.get("role") or (
                "user" if event_type == "user" else ""
            )
            if not role:
                continue
            if "content" in event:
                content = _extract_openai_content(event["content"])
            elif isinstance(part_map.get("text"), str):
                content = part_map["text"]
            elif isinstance(event.get("text"), str):
                content = event["text"]
            elif isinstance(event.get("parts"), list):
                content = _extract_openai_content(event["parts"])
            else:
                raise ConversationError(f"opencode {event_type or role} event missing text content")
            turn_id = str(
                event.get("turn_id")
                or event.get("id")
                or info_map.get("id")
                or part_map.get("id")
                or f"t{len(turns) + 1}"
            )
            turns.append(
                ConversationTurn(
                    turn_id=turn_id,
                    role=role,
                    content=content,
                    timestamp=timestamp,
                    session_id=session_id,
                    metadata={"event_type": event_type or "message"},
                )
            )

    if not turns:
        raise ConversationError("no conversational turns found in opencode event stream")

    conv_id = seen_session_id or conversation_id
    return ConversationTranscript(
        conversation_id=conv_id,
        turns=tuple(turns),
        metadata={"source_format": "opencode"},
    )


def _parse_halumem(raw: Any, *, conversation_id: str) -> ConversationTranscript:
    if not isinstance(raw, Mapping):
        raise ConversationError("HaluMem conversation must be a JSON object")
    sessions = raw.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ConversationError("HaluMem conversation must contain a non-empty sessions list")

    conv_id = str(raw.get("uuid") or raw.get("conversation_id") or conversation_id)
    turns: list[ConversationTurn] = []
    for s_index, session in enumerate(sessions, start=1):
        if not isinstance(session, Mapping):
            raise ConversationError("each HaluMem session must be an object")
        session_id = str(session.get("session_id") or f"s{s_index}")
        dialogue = session.get("dialogue")
        if dialogue is None:
            dialogue = session.get("turns", session.get("messages", []))
        if not isinstance(dialogue, list):
            raise ConversationError("HaluMem session dialogue must be a list")
        for t_index, msg in enumerate(dialogue, start=1):
            if not isinstance(msg, Mapping):
                raise ConversationError("HaluMem dialogue entry must be an object")
            turn = ConversationTurn.from_dict(
                msg,
                default_turn_id=f"{session_id}_t{t_index}",
                default_session_id=session_id,
            )
            turns.append(turn)

    if not turns:
        raise ConversationError("HaluMem sessions contain no dialogue turns")

    metadata: dict[str, Any] = _ensure_json_dict(raw.get("metadata", {}), "transcript metadata")
    metadata.setdefault("source_format", "halumem")
    metadata["session_count"] = len(sessions)
    if "uuid" in raw:
        metadata["uuid"] = raw["uuid"]
    if "persona_info" in raw:
        metadata["persona_info"] = raw["persona_info"]
    return ConversationTranscript(
        conversation_id=conv_id,
        turns=tuple(turns),
        scope=raw.get("scope", {}),
        metadata=metadata,
    )


def _parse_text(raw: Any, *, conversation_id: str) -> ConversationTranscript:
    if not isinstance(raw, str):
        raise ConversationError("text transcript input must be a string")
    turns: list[ConversationTurn] = []
    current_id: str | None = None
    current_role: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_role is None:
            return
        turn_id = current_id or f"t{len(turns) + 1}"
        content = "\n".join(current_lines).strip()
        turns.append(ConversationTurn(turn_id=turn_id, role=current_role, content=content))

    for line in raw.splitlines():
        match = _TEXT_TURN_RE.match(line.strip())
        if match:
            _flush()
            current_id = match.group("id")
            current_role = match.group("role")
            current_lines = [match.group("content")]
        elif current_role is not None:
            current_lines.append(line.rstrip())
        elif line.strip():
            raise ConversationError(
                "text transcript must begin with a role prefix such as 'User:' or '[t1] User:'"
            )
    _flush()

    if not turns:
        raise ConversationError("text transcript contains no turns")
    return ConversationTranscript(
        conversation_id=conversation_id,
        turns=tuple(turns),
        metadata={"source_format": "text"},
    )


def _try_parse_json_or_jsonl(text: str) -> tuple[bool, Any]:
    stripped = text.strip()
    if not stripped:
        return False, None
    try:
        return True, json.loads(stripped)
    except json.JSONDecodeError:
        pass
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 1 and all(line.startswith("{") for line in lines):
        items: list[Any] = []
        for line in lines:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                return False, None
        return True, items
    return False, None


def parse_conversation(
    raw: Any,
    *,
    conversation_id: str = "conversation-1",
    format: str = "auto",
) -> ConversationTranscript:
    """Parse a conversation from MemBud, OpenAI, opencode, HaluMem, or text format."""
    if format not in SUPPORTED_FORMATS:
        raise ConversationError(
            f"unsupported conversation format {format!r}; expected one of {SUPPORTED_FORMATS}"
        )
    if isinstance(raw, ConversationTranscript):
        return raw

    selected = format
    payload = raw
    if selected == "auto":
        if isinstance(payload, str):
            is_json, decoded = _try_parse_json_or_jsonl(payload)
            if is_json:
                payload = decoded
            else:
                selected = "text"
        if selected == "auto":
            if isinstance(payload, Mapping):
                if "turns" in payload:
                    selected = "membud"
                elif "sessions" in payload:
                    selected = "halumem"
                elif "messages" in payload or "dialogue" in payload:
                    selected = "openai"
                elif "events" in payload:
                    selected = "opencode"
                else:
                    raise ConversationError(
                        "cannot detect conversation format from object keys: "
                        + ", ".join(sorted(map(str, payload.keys())))
                    )
            elif isinstance(payload, (list, tuple)):
                if not payload:
                    raise ConversationError("conversation list must not be empty")
                if any(isinstance(item, Mapping) and _is_opencode_event(item) for item in payload):
                    selected = "opencode"
                else:
                    selected = "openai"
            else:
                raise ConversationError(
                    f"cannot parse conversation from {type(payload).__name__}"
                )

    if selected == "membud":
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ConversationError(f"invalid membud JSON conversation: {exc}") from exc
        return ConversationTranscript.from_dict(payload, conversation_id=conversation_id)
    if selected == "openai":
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ConversationError(f"invalid OpenAI JSON conversation: {exc}") from exc
        return _parse_openai(payload, conversation_id=conversation_id)
    if selected == "opencode":
        return _parse_opencode(payload, conversation_id=conversation_id)
    if selected == "halumem":
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ConversationError(f"invalid HaluMem JSON conversation: {exc}") from exc
        return _parse_halumem(payload, conversation_id=conversation_id)
    return _parse_text(payload, conversation_id=conversation_id)


def load_conversation(
    path: str | Path,
    *,
    conversation_id: str | None = None,
    format: str = "auto",
) -> ConversationTranscript:
    """Load one conversation transcript from disk."""
    file_path = Path(path)
    raw = file_path.read_text(encoding="utf-8")
    default_id = conversation_id or file_path.stem or "conversation-1"
    return parse_conversation(raw, conversation_id=default_id, format=format)


def load_conversations_jsonl(
    path: str | Path,
    *,
    format: str = "auto",
) -> list[ConversationTranscript]:
    """Load one conversation transcript per line from a JSONL file."""
    file_path = Path(path)
    transcripts: list[ConversationTranscript] = []
    for index, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ConversationError(f"invalid JSON on line {index} of {file_path}: {exc}") from exc
        conv_id = f"conversation-{index}"
        target = item
        if isinstance(item, Mapping):
            conv_id = str(item.get("conversation_id") or conv_id)
            if "transcript" in item and "turns" not in item:
                target = item["transcript"]
        transcripts.append(
            parse_conversation(target, conversation_id=conv_id, format=format)
        )
    return transcripts


# ---------------------------------------------------------------------------
# Transcript-grounded evidence verification
# ---------------------------------------------------------------------------


def _claim_grounded_in_quote(claim: str, quote_norm: str, quote_tokens: set[str]) -> bool:
    claim_norm = _normalize_ws(claim).lower()
    if claim_norm and claim_norm in quote_norm:
        return True
    claim_tokens = _content_tokens(claim)
    if not claim_tokens:
        return True
    return bool(claim_tokens & quote_tokens)


def verify_transcript_grounding(
    transition: Transition,
    transcript: ConversationTranscript,
    *,
    allowed_roles: Iterable[str] = DEFAULT_GROUNDING_ROLES,
    require_claim_lexical_overlap: bool = True,
) -> Check:
    """Verify that transition evidence and DELETE authorizations are grounded in ``transcript``."""
    transition.validate()
    if not isinstance(transcript, ConversationTranscript):
        raise ConversationError("transcript must be a ConversationTranscript instance")
    allowed_roles_set = frozenset(_normalize_role(role) for role in allowed_roles)
    if not allowed_roles_set:
        raise ConversationError("allowed_roles must not be empty")

    ungrounded_sources: list[str] = []
    disallowed_roles: list[str] = []
    ungrounded_quotes: list[str] = []
    ungrounded_claims: list[str] = []
    ungrounded_deletes: list[str] = []

    grounded_turns: dict[str, ConversationTurn | None] = {}
    evidence_by_id: dict[str, Evidence] = {ev.id: ev for ev in transition.evidence}
    before_by_id: dict[str, Record] = {rec.id: rec for rec in transition.before}

    for ev in transition.evidence:
        source = ev.source.strip()
        quote_norm = _normalize_ws(ev.text).lower()
        turn: ConversationTurn | None = None
        if source:
            turn = transcript.turn_by_id(source)
            if turn is None:
                ungrounded_sources.append(ev.id)
                grounded_turns[ev.id] = None
                continue
        else:
            if not quote_norm:
                ungrounded_sources.append(ev.id)
                grounded_turns[ev.id] = None
                continue
            matching_allowed = [
                candidate
                for candidate in transcript.turns
                if candidate.role in allowed_roles_set
                and quote_norm in _normalize_ws(candidate.content).lower()
            ]
            matching_any = matching_allowed or [
                candidate
                for candidate in transcript.turns
                if quote_norm in _normalize_ws(candidate.content).lower()
            ]
            if not matching_any:
                ungrounded_sources.append(ev.id)
                grounded_turns[ev.id] = None
                continue
            turn = matching_any[0]

        grounded_turns[ev.id] = turn
        if turn.role not in allowed_roles_set:
            disallowed_roles.append(ev.id)
        if not quote_norm or quote_norm not in _normalize_ws(turn.content).lower():
            ungrounded_quotes.append(ev.id)
        elif require_claim_lexical_overlap:
            quote_tokens = _content_tokens(ev.text)
            for claim in (*ev.supports, *ev.contradicts):
                if not _claim_grounded_in_quote(claim, quote_norm, quote_tokens):
                    ungrounded_claims.append(claim)

    for operation in transition.operations:
        if operation.type != "DELETE":
            continue
        assert operation.authorization is not None  # guaranteed by Transition.validate()
        ev = evidence_by_id.get(operation.authorization.evidence_id)
        turn = grounded_turns.get(operation.authorization.evidence_id) if ev else None
        if ev is None or turn is None:
            continue  # source/evidence failure already captured above
        if turn.role not in allowed_roles_set:
            continue  # disallowed role already captured above
        quote_norm = _normalize_ws(ev.text).lower()
        if not quote_norm or quote_norm not in _normalize_ws(turn.content).lower():
            continue  # quote mismatch already captured above

        has_intent = bool(_DELETE_INTENT_RE.search(ev.text) or _DELETE_INTENT_RE.search(turn.content))
        target_record = before_by_id.get(operation.target_id or "")
        target_tokens = (
            _content_tokens(" ".join(target_record.claims)) if target_record else set()
        )
        evidence_tokens = _content_tokens(ev.text) | _content_tokens(turn.content)
        has_target_overlap = bool(target_tokens and (target_tokens & evidence_tokens))
        if not (has_intent or has_target_overlap):
            ungrounded_deletes.append(operation.target_id or operation.authorization.evidence_id)

    if ungrounded_sources:
        affected = sorted(set(ungrounded_sources))
        return Check(
            TRANSCRIPT_CHECK_NAME,
            False,
            "evidence items do not resolve to any transcript turn: " + ", ".join(affected),
            affected,
            status="FAIL",
            reason_code="UNGROUNDED_EVIDENCE_SOURCE",
        )
    if disallowed_roles:
        affected = sorted(set(disallowed_roles))
        return Check(
            TRANSCRIPT_CHECK_NAME,
            False,
            "evidence cites turn(s) with disallowed role: " + ", ".join(affected),
            affected,
            status="FAIL",
            reason_code="DISALLOWED_EVIDENCE_ROLE",
        )
    if ungrounded_quotes:
        affected = sorted(set(ungrounded_quotes))
        return Check(
            TRANSCRIPT_CHECK_NAME,
            False,
            "evidence text is not a verbatim substring of its cited turn: " + ", ".join(affected),
            affected,
            status="FAIL",
            reason_code="UNGROUNDED_EVIDENCE_QUOTE",
        )
    if ungrounded_deletes:
        affected = sorted(set(ungrounded_deletes))
        return Check(
            TRANSCRIPT_CHECK_NAME,
            False,
            "DELETE authorization is not supported by deletion intent or target overlap: "
            + ", ".join(affected),
            affected,
            status="FAIL",
            reason_code="UNGROUNDED_DELETE_AUTHORIZATION",
        )
    if ungrounded_claims:
        affected = sorted(set(ungrounded_claims))
        return Check(
            TRANSCRIPT_CHECK_NAME,
            False,
            "evidence claims lack lexical grounding in cited turn quote: " + "; ".join(affected),
            affected,
            status="FAIL",
            reason_code="UNGROUNDED_EVIDENCE_CLAIM",
        )
    return Check(
        TRANSCRIPT_CHECK_NAME,
        True,
        "all evidence items and authorizations are grounded in allowed transcript turns",
        [],
        status="PASS",
        reason_code="TRANSCRIPT_GROUNDING_OK",
    )


def verify_with_transcript(
    transition: Transition,
    transcript: ConversationTranscript,
    representation: str = "graph",
    *,
    semantic_comparator: Any | None = None,
    allowed_roles: Iterable[str] = DEFAULT_GROUNDING_ROLES,
    require_claim_lexical_overlap: bool = True,
    fair_text: bool = False,
) -> Result:
    """Verify a transition and enforce transcript grounding over ``transcript``."""
    if semantic_comparator is not None:
        base = verify_with_semantics(transition, semantic_comparator)
    elif fair_text:
        base = verify_text_fair(transition)
    else:
        base = verify(transition, representation)

    grounding_check = verify_transcript_grounding(
        transition,
        transcript,
        allowed_roles=allowed_roles,
        require_claim_lexical_overlap=require_claim_lexical_overlap,
    )
    checks = list(base.checks) + [grounding_check]
    decision = "ACCEPT" if all(c.status == "PASS" and c.passed for c in checks) else "HOLD"
    verifier_config = dict(base.verifier_config)
    verifier_config.update(
        {
            "transcript_grounded": True,
            "conversation_id": transcript.conversation_id,
        }
    )
    return Result(
        case_id=transition.case_id,
        decision=decision,
        checks=checks,
        verifier=base.verifier,
        prompt_version=base.prompt_version,
        verifier_config=verifier_config,
        usage=dict(base.usage),
        latency_ms=base.latency_ms,
        raw_response=base.raw_response,
        provider_request_id=base.provider_request_id,
    )


# ---------------------------------------------------------------------------
# Transition construction from conversation proposals
# ---------------------------------------------------------------------------


def _coerce_records(records: Iterable[Record | Mapping[str, Any]]) -> tuple[Record, ...]:
    result: list[Record] = []
    for item in records:
        if isinstance(item, Record):
            result.append(item)
        elif isinstance(item, Mapping):
            result.append(Record.from_dict(dict(item)))
        else:
            raise ConversationError(
                f"prior records must be Record or dict, got {type(item).__name__}"
            )
    return tuple(result)


def _next_record_id(used_ids: set[str]) -> str:
    index = 1
    while f"r{index}" in used_ids:
        index += 1
    return f"r{index}"


def _resolve_grounded_turn(
    transcript: ConversationTranscript,
    turn_ref: str,
    quote: str | None,
    allowed_roles_set: frozenset[str],
) -> tuple[ConversationTurn, str]:
    turn = transcript.turn_by_id(turn_ref)
    if turn is None:
        raise ConversationError(f"proposal references unknown turn_id: {turn_ref!r}")
    if turn.role not in allowed_roles_set:
        raise ConversationError(
            f"proposal cites turn {turn.turn_id!r} with disallowed role {turn.role!r}"
        )
    chosen_quote = turn.content if quote is None else str(quote)
    if not chosen_quote.strip():
        raise ConversationError(f"proposal quote for turn {turn.turn_id!r} must be non-empty")
    if _normalize_ws(chosen_quote).lower() not in _normalize_ws(turn.content).lower():
        raise ConversationError(
            f"proposal quote is not present in turn {turn.turn_id!r}: {chosen_quote!r}"
        )
    return turn, chosen_quote


def build_transition_from_conversation(
    transcript: ConversationTranscript,
    *,
    case_id: str,
    before: Iterable[Record | Mapping[str, Any]] = (),
    proposals: Sequence[Mapping[str, Any]] = (),
    in_scope_claims: Iterable[str] = (),
    required_claims: Iterable[str] | None = None,
    allowed_roles: Iterable[str] = DEFAULT_GROUNDING_ROLES,
    schema_version: int = SCHEMA_VERSION,
) -> Transition:
    """Build a validated schema-v2 ``Transition`` from conversation-grounded proposals."""
    if not isinstance(transcript, ConversationTranscript):
        raise ConversationError("transcript must be a ConversationTranscript")
    allowed_roles_set = frozenset(_normalize_role(r) for r in allowed_roles)
    before_records = _coerce_records(before)
    before_by_id = {record.id: record for record in before_records}
    candidate_map: dict[str, Record] = {record.id: record for record in before_records}
    used_record_ids = set(candidate_map)

    operations: list[Operation] = []
    evidence_list: list[Evidence] = []
    evidence_index: dict[tuple[str, str], int] = {}
    proposed_supported: list[str] = []
    evidenced_targets: set[str] = set()
    deleted_targets: set[str] = set()

    def _register_evidence(
        turn: ConversationTurn,
        quote: str,
        supports: Sequence[str],
        contradicts: Sequence[str],
    ) -> str:
        key = (turn.source_ref, _normalize_ws(quote))
        if key in evidence_index:
            idx = evidence_index[key]
            existing = evidence_list[idx]
            merged_supports = tuple(dict.fromkeys([*existing.supports, *supports]))
            merged_contradicts = tuple(dict.fromkeys([*existing.contradicts, *contradicts]))
            evidence_list[idx] = Evidence(
                id=existing.id,
                supports=merged_supports,
                source=existing.source,
                text=existing.text,
                contradicts=merged_contradicts,
            )
            return existing.id
        ev_id = f"e{len(evidence_list) + 1}"
        evidence_index[key] = len(evidence_list)
        evidence_list.append(
            Evidence(
                id=ev_id,
                supports=tuple(dict.fromkeys(supports)),
                source=turn.source_ref,
                text=quote,
                contradicts=tuple(dict.fromkeys(contradicts)),
            )
        )
        return ev_id

    if not proposals:
        operations.append(Operation(type="NOOP"))
    else:
        for raw_prop in proposals:
            if not isinstance(raw_prop, Mapping):
                raise ConversationError("each proposal must be a mapping")
            op_type = str(raw_prop.get("type") or raw_prop.get("operation") or "").strip().upper()
            if op_type not in {"WRITE", "REVISE", "DELETE", "SUPERSEDE", "NOOP"}:
                raise ConversationError(f"unsupported proposal operation type: {op_type!r}")

            if op_type == "NOOP":
                if len(proposals) != 1:
                    raise ConversationError("NOOP must be the sole proposal")
                operations.append(Operation(type="NOOP"))
                continue

            claims_raw = raw_prop.get("claims")
            claims: tuple[str, ...] = (
                tuple(str(c) for c in claims_raw) if claims_raw is not None else ()
            )
            contradicts_raw = raw_prop.get("contradicts", ())
            contradicts = tuple(str(c) for c in contradicts_raw)

            # Resolve attached evidence items (flat turn_id/quote or nested evidence list).
            prop_evidence_ids: list[str] = []
            prop_supports: list[str] = []
            nested_ev = raw_prop.get("evidence")
            if isinstance(nested_ev, Sequence) and not isinstance(nested_ev, (str, bytes)):
                for item in nested_ev:
                    if not isinstance(item, Mapping):
                        raise ConversationError("nested evidence entry must be a mapping")
                    t_ref = str(item.get("turn_id") or item.get("source") or "").strip()
                    if not t_ref:
                        raise ConversationError("nested evidence entry requires turn_id or source")
                    quote_arg = item.get("quote", item.get("text"))
                    turn, quote = _resolve_grounded_turn(
                        transcript, t_ref, quote_arg, allowed_roles_set
                    )
                    ev_supports = tuple(
                        str(c)
                        for c in item.get(
                            "supports",
                            () if op_type == "DELETE" else claims,
                        )
                    )
                    ev_contradicts = tuple(str(c) for c in item.get("contradicts", contradicts))
                    ev_id = _register_evidence(turn, quote, ev_supports, ev_contradicts)
                    prop_evidence_ids.append(ev_id)
                    prop_supports.extend(ev_supports)
            elif raw_prop.get("turn_id") or raw_prop.get("source"):
                t_ref = str(raw_prop.get("turn_id") or raw_prop.get("source") or "").strip()
                quote_arg = raw_prop.get("quote", raw_prop.get("evidence_text"))
                turn, quote = _resolve_grounded_turn(
                    transcript, t_ref, quote_arg, allowed_roles_set
                )
                if "supports" in raw_prop:
                    ev_supports = tuple(str(c) for c in raw_prop["supports"])
                elif op_type == "DELETE":
                    ev_supports = ()
                else:
                    ev_supports = claims
                ev_id = _register_evidence(turn, quote, ev_supports, contradicts)
                prop_evidence_ids.append(ev_id)
                prop_supports.extend(ev_supports)

            proposed_supported.extend(prop_supports)

            if op_type == "WRITE":
                if not claims:
                    raise ConversationError("WRITE proposal requires non-empty claims")
                record_id = raw_prop.get("record_id")
                if not record_id:
                    record_id = _next_record_id(used_record_ids)
                record_id = str(record_id)
                if record_id in candidate_map:
                    raise ConversationError(f"WRITE record_id already exists: {record_id!r}")
                used_record_ids.add(record_id)
                candidate_map[record_id] = Record(id=record_id, claims=claims)
                operations.append(Operation(type="WRITE", record_id=record_id))
                continue

            target_id = raw_prop.get("target_id")
            if not isinstance(target_id, str) or target_id not in before_by_id:
                raise ConversationError(f"{op_type} proposal requires a valid prior target_id")

            if op_type == "DELETE":
                if not prop_evidence_ids:
                    raise ConversationError("DELETE proposal requires grounded turn evidence")
                ev_id = prop_evidence_ids[0]
                ev_obj = next(item for item in evidence_list if item.id == ev_id)
                rationale = str(
                    raw_prop.get("rationale")
                    or raw_prop.get("reason")
                    or f"User requested deletion in {ev_obj.source}"
                ).strip()
                reason = str(raw_prop.get("reason") or rationale).strip()
                del candidate_map[target_id]
                deleted_targets.add(target_id)
                operations.append(
                    Operation(
                        type="DELETE",
                        target_id=target_id,
                        reason=reason,
                        authorization=DeletionAuthorization(
                            source=ev_obj.source,
                            evidence_id=ev_id,
                            target_id=target_id,
                            rationale=rationale,
                        ),
                    )
                )
                continue

            if not claims:
                raise ConversationError(f"{op_type} proposal requires non-empty claims")
            if op_type == "REVISE":
                record_id = str(raw_prop.get("record_id") or target_id)
                if record_id != target_id:
                    raise ConversationError("REVISE proposal must keep target_id as record_id")
                candidate_map[target_id] = Record(id=target_id, claims=claims)
                if prop_supports:
                    evidenced_targets.add(target_id)
                operations.append(
                    Operation(
                        type="REVISE",
                        record_id=record_id,
                        target_id=target_id,
                        reason=str(raw_prop.get("reason", "")),
                    )
                )
            else:  # SUPERSEDE
                record_id = raw_prop.get("record_id")
                if not record_id:
                    record_id = _next_record_id(used_record_ids)
                record_id = str(record_id)
                if record_id == target_id or record_id in before_by_id:
                    raise ConversationError("SUPERSEDE requires a fresh replacement record_id")
                used_record_ids.add(record_id)
                del candidate_map[target_id]
                candidate_map[record_id] = Record(id=record_id, claims=claims)
                if prop_supports:
                    evidenced_targets.add(target_id)
                operations.append(
                    Operation(
                        type="SUPERSEDE",
                        record_id=record_id,
                        target_id=target_id,
                        reason=str(raw_prop.get("reason", "")),
                    )
                )

    if required_claims is None:
        retired_ids = deleted_targets | evidenced_targets
        derived: list[str] = []
        for record in before_records:
            if record.id not in retired_ids:
                derived.extend(record.claims)
        derived.extend(str(c) for c in in_scope_claims)
        derived.extend(proposed_supported)
        final_required = tuple(dict.fromkeys(derived))
    else:
        final_required = tuple(dict.fromkeys(str(c) for c in required_claims))

    return Transition(
        case_id=case_id,
        before=before_records,
        candidate=tuple(candidate_map.values()),
        evidence=tuple(evidence_list),
        required_claims=final_required,
        operations=tuple(operations),
        schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# Conversation memory extractors (deterministic rules & optional JSON-mode LLM)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SlotRule:
    slot: str
    pattern: re.Pattern[str]
    template: str
    supersede_cue: bool = False


_SLOT_RULES: tuple[_SlotRule, ...] = (
    # Residence (ID & EN)
    _SlotRule(
        "residence",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:sudah\s+|telah\s+)?pindah(?:\s+domisili)?\s+ke\s+"
            r"(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "tinggal di {val}",
        supersede_cue=True,
    ),
    _SlotRule(
        "residence",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:sekarang\s+)?(?:tinggal|berdomisili|menetap)\s+di\s+"
            r"(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "tinggal di {val}",
    ),
    _SlotRule(
        "residence",
        re.compile(
            r"(?:i\s+)?(?:have\s+)?(?:moved|relocated)\s+to\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "lives in {val}",
        supersede_cue=True,
    ),
    _SlotRule(
        "residence",
        re.compile(
            r"(?:i\s+)?(?:currently\s+|now\s+)?(?:live|reside)\s+in\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "lives in {val}",
    ),
    # Employment / role (ID & EN)
    _SlotRule(
        "employment",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:sekarang\s+|mulai\s+)?(?:bekerja|kerja)\s+(?:di|pada)\s+"
            r"(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "bekerja di {val}",
    ),
    _SlotRule(
        "role",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:sekarang\s+)?(?:bekerja\s+sebagai|menjadi)\s+"
            r"(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "menjadi {val}",
    ),
    _SlotRule(
        "employment",
        re.compile(
            r"(?:i\s+)?(?:currently\s+|now\s+)?work(?:ing)?\s+(?:at|for)\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "works at {val}",
    ),
    _SlotRule(
        "role",
        re.compile(
            r"(?:i\s+am|i'm|my\s+role\s+is)\s+(?:a|an)\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "role is {val}",
    ),
    # Study / hobby / possession / allergy / preference (ID & EN)
    _SlotRule(
        "study",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:mulai\s+|sedang\s+)?belajar\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "belajar {val}",
    ),
    _SlotRule(
        "possession",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:menjual\s+(?P<old>[^,.;!?]+?)\s+dan\s+membeli|membeli)\s+"
            r"(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "memiliki {val}",
        supersede_cue=True,
    ),
    _SlotRule(
        "possession",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:memiliki|memelihara|punya)\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "memiliki {val}",
    ),
    _SlotRule(
        "possession",
        re.compile(
            r"(?:i\s+)?(?:sold\s+(?P<old>[^,.;!?]+?)\s+and\s+bought|bought)\s+"
            r"(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "has {val}",
        supersede_cue=True,
    ),
    _SlotRule(
        "possession",
        re.compile(r"(?:i\s+)?(?:have|own)\s+(?P<val>[^,.;!?]+)", re.IGNORECASE),
        "has {val}",
    ),
    _SlotRule(
        "allergy",
        re.compile(r"(?:saya\s+|aku\s+)?menderita\s+(?P<val>[^,.;!?]+)", re.IGNORECASE),
        "menderita {val}",
    ),
    _SlotRule(
        "allergy",
        re.compile(r"(?:i\s+am|i'm)\s+allergic\s+to\s+(?P<val>[^,.;!?]+)", re.IGNORECASE),
        "allergic to {val}",
    ),
    _SlotRule(
        "preference",
        re.compile(
            r"(?:saya\s+|aku\s+)?(?:suka|menyukai|gemar)\s+(?P<val>[^,.;!?]+)",
            re.IGNORECASE,
        ),
        "menyukai {val}",
    ),
    _SlotRule(
        "preference",
        re.compile(r"(?:i\s+)?(?:prefer|like|love)\s+(?P<val>[^,.;!?]+)", re.IGNORECASE),
        "prefers {val}",
    ),
    _SlotRule(
        "name",
        re.compile(r"(?:nama\s+saya|panggil\s+saya)\s+(?P<val>[^,.;!?]+)", re.IGNORECASE),
        "nama adalah {val}",
    ),
    _SlotRule(
        "name",
        re.compile(r"(?:my\s+name\s+is|i\s+am\s+called)\s+(?P<val>[^,.;!?]+)", re.IGNORECASE),
        "name is {val}",
    ),
)

_SLOT_PREFIXES: dict[str, tuple[str, ...]] = {
    "residence": ("tinggal di ", "lives in "),
    "employment": ("bekerja di ", "works at "),
    "role": ("menjadi ", "role is "),
    "study": ("belajar ",),
    "possession": ("memiliki ", "has "),
    "allergy": ("menderita ", "allergic to "),
    "preference": ("menyukai ", "prefers "),
    "name": ("nama adalah ", "name is "),
}

_DELETE_CLAUSE_RE = re.compile(
    r"^(?:tolong\s+|mohon\s+|please\s+)?"
    r"(?:hapus|lupakan|hilangkan|delete|remove|forget|drop)\s+"
    r"(?:catatan\s+|data\s+|memori\s+|informasi\s+|my\s+|the\s+)?"
    r"(?:record\s+(?:about|of|for)\s+|memory\s+(?:about|of|for)\s+|note\s+(?:about|of|for)\s+)?"
    r"(?P<target>.+)$",
    re.IGNORECASE,
)

_CONTRAST_TAIL_RE = re.compile(r"\b(?:bukan|instead\s+of|not)\s+(?P<old>[^,.;!?]+)$", re.IGNORECASE)
_CORRECTION_CUE_RE = re.compile(
    r"\b(?:koreksi|maaf|salah|correction|actually|update|sekarang|now)\b", re.IGNORECASE
)
_SUPERSESSION_CUE_RE = re.compile(
    r"\b(?:pindah|moved|relocated|berganti|ganti|replaced|menjual|sold)\b", re.IGNORECASE
)


_CLAUSE_CONJUNCTION_RE = re.compile(
    r"(?:,\s*(?:(?:dan|and)\s+)?|\s+(?:dan|and)\s+)"
    r"(?=(?:saya|aku|i|sekarang|mulai|currently|now|also|juga|"
    r"tinggal|berdomisili|menetap|bekerja|kerja|menjadi|belajar|"
    r"memiliki|memelihara|punya|suka|menyukai|gemar|menderita|pindah|"
    r"live|reside|work|working|have|own|prefer|like|love|moved|relocated|"
    r"allergic|my\s+name|nama\s+saya|panggil\s+saya|"
    r"tolong|mohon|please|hapus|lupakan|hilangkan|delete|remove|forget|drop)\b)",
    re.IGNORECASE,
)

_SINGULAR_SLOTS = frozenset({"residence", "employment", "role", "name"})


def _claim_slot(claim: str) -> str | None:
    lowered = claim.strip().lower()
    for slot, prefixes in _SLOT_PREFIXES.items():
        if any(lowered.startswith(prefix) for prefix in prefixes):
            return slot
    return None


def _split_clauses(content: str) -> list[str]:
    clauses: list[str] = []
    for piece in _SENTENCE_SPLIT_RE.split(content):
        cleaned = piece.strip()
        if not cleaned:
            continue
        # Do not split contrast tails ("..., bukan Bandung") or replacement idioms
        # ("menjual X dan membeli Y"); split only independent coordinated clauses.
        contrast_match = _CONTRAST_TAIL_RE.search(cleaned)
        if contrast_match:
            head = cleaned[: contrast_match.start()].rstrip(" ,")
            tail = cleaned[contrast_match.start() :].strip()
            parts = [p.strip() for p in _CLAUSE_CONJUNCTION_RE.split(head) if p.strip()]
            if parts:
                parts[-1] = f"{parts[-1]}, {tail}" if ", " in cleaned[contrast_match.start() - 2 :] else f"{parts[-1]} {tail}"
                # Ensure each split fragment remains a verbatim substring when possible.
                clauses.extend(
                    p if _normalize_ws(p).lower() in _normalize_ws(cleaned).lower() else cleaned
                    for p in parts
                )
            else:
                clauses.append(cleaned)
        else:
            clauses.extend(p.strip() for p in _CLAUSE_CONJUNCTION_RE.split(cleaned) if p.strip())
    return clauses


def _find_delete_target(target_phrase: str, state: Mapping[str, Record]) -> str | None:
    needle_tokens = _content_tokens(target_phrase)
    if not needle_tokens:
        return None
    best_id: str | None = None
    best_score = 0
    for rid, record in state.items():
        rec_tokens = _content_tokens(" ".join(record.claims))
        overlap = len(needle_tokens & rec_tokens)
        if overlap > best_score:
            best_score = overlap
            best_id = rid
    return best_id if best_score >= 1 else None


class RuleBasedConversationExtractor:
    """Deterministic stdlib extractor mapping user turns into a schema-v2 Transition."""

    name = "rules-conversation-extractor"

    def extract(
        self,
        transcript: ConversationTranscript,
        before: Iterable[Record | Mapping[str, Any]] = (),
        *,
        case_id: str | None = None,
        focus_turn_ids: Iterable[str] | None = None,
        only_latest_turn: bool | None = None,
        allowed_roles: Iterable[str] = DEFAULT_GROUNDING_ROLES,
    ) -> Transition:
        """Extract memory proposals from user turns and return a schema-v2 Transition."""
        if not isinstance(transcript, ConversationTranscript):
            raise ConversationError("transcript must be a ConversationTranscript")
        allowed_roles_set = frozenset(_normalize_role(r) for r in allowed_roles)
        before_records = _coerce_records(before)
        working: dict[str, Record] = {r.id: r for r in before_records}
        used_ids = set(working)

        if focus_turn_ids is not None:
            requested_ids = list(focus_turn_ids)
            turns: list[ConversationTurn] = []
            for ref in requested_ids:
                turn = transcript.turn_by_id(ref)
                if turn is None:
                    raise ConversationError(f"focus_turn_id not found: {ref!r}")
                if turn.role in allowed_roles_set:
                    turns.append(turn)
        else:
            candidate_turns = [t for t in transcript.turns if t.role in allowed_roles_set]
            use_latest = bool(before_records) if only_latest_turn is None else only_latest_turn
            turns = candidate_turns[-1:] if (use_latest and candidate_turns) else candidate_turns

        proposals_by_target: dict[str, dict[str, Any]] = {}
        write_proposals: list[dict[str, Any]] = []

        for turn in turns:
            for clause in _split_clauses(turn.content):
                # Skip questions unless they contain an explicit deletion verb.
                if clause.endswith("?") and not _DELETE_INTENT_RE.search(clause):
                    continue

                del_match = _DELETE_CLAUSE_RE.match(clause)
                if del_match:
                    target_phrase = del_match.group("target")
                    target_id = _find_delete_target(target_phrase, working)
                    if target_id is not None and target_id in {r.id for r in before_records}:
                        del working[target_id]
                        proposals_by_target[target_id] = {
                            "type": "DELETE",
                            "target_id": target_id,
                            "turn_id": turn.turn_id,
                            "quote": clause,
                            "reason": "user explicitly requested deletion",
                            "rationale": f"User requested deletion in {turn.turn_id}: {clause}",
                        }
                    continue

                # Split compound clauses joined by "dan sekarang " / "and now " / "dan membeli "?
                # Keep full clause as quote so verbatim substring check always holds.
                stripped_clause = re.sub(
                    r"^(?:koreksi|correction|maaf,\s*saya\s*salah|actually)\s*[:.,]?\s*",
                    "",
                    clause,
                    flags=re.IGNORECASE,
                )
                contrast_match = _CONTRAST_TAIL_RE.search(stripped_clause)
                old_contrast = contrast_match.group("old").strip() if contrast_match else ""
                match_text = (
                    stripped_clause[: contrast_match.start()].rstrip(" ,")
                    if contrast_match
                    else stripped_clause
                )

                for rule in _SLOT_RULES:
                    match = rule.pattern.search(match_text)
                    if not match:
                        continue
                    raw_val = match.group("val").strip(" .!?,;")
                    # Trim trailing "yang baru" / "sekarang" / contrast markers if any.
                    raw_val = re.sub(r"\s+(?:sekarang|now)$", "", raw_val, flags=re.IGNORECASE).strip()
                    if not raw_val:
                        continue
                    claim = rule.template.format(val=raw_val)
                    already_present = any(claim in rec.claims for rec in working.values())
                    if already_present:
                        break

                    # Resolve prior target: explicit old phrase ("bukan X", "menjual X",
                    # "sold X") first, then single-valued slot replacement.
                    matched_prior_id: str | None = None
                    matched_old_claims: list[str] = []
                    old_phrase = old_contrast or (match.groupdict().get("old") or "").strip()
                    if old_phrase:
                        old_tokens = _content_tokens(old_phrase)
                        for rid, rec in working.items():
                            for existing_claim in rec.claims:
                                if old_tokens and (old_tokens & _content_tokens(existing_claim)):
                                    matched_prior_id = rid
                                    matched_old_claims = [existing_claim]
                                    break
                            if matched_prior_id is not None:
                                break
                    if matched_prior_id is None and rule.slot in _SINGULAR_SLOTS:
                        for rid, rec in working.items():
                            slot_claims = [c for c in rec.claims if _claim_slot(c) == rule.slot]
                            if slot_claims:
                                matched_prior_id = rid
                                matched_old_claims = slot_claims
                                break

                    contradicts = (
                        list(matched_old_claims)
                        if matched_old_claims and (old_phrase or _CORRECTION_CUE_RE.search(clause))
                        else []
                    )
                    is_supersede = rule.supersede_cue or bool(_SUPERSESSION_CUE_RE.search(clause))

                    before_id_set = {r.id for r in before_records}
                    if matched_prior_id is not None and matched_prior_id in before_id_set:
                        prior_rec = working[matched_prior_id]
                        retained = [c for c in prior_rec.claims if c not in matched_old_claims]
                        new_claims = tuple(dict.fromkeys([*retained, claim]))
                        if is_supersede and not retained:
                            new_rid = _next_record_id(used_ids)
                            used_ids.add(new_rid)
                            del working[matched_prior_id]
                            working[new_rid] = Record(id=new_rid, claims=new_claims)
                            proposals_by_target[matched_prior_id] = {
                                "type": "SUPERSEDE",
                                "record_id": new_rid,
                                "target_id": matched_prior_id,
                                "claims": list(new_claims),
                                "turn_id": turn.turn_id,
                                "quote": clause,
                                "supports": [claim],
                                "contradicts": contradicts,
                                "reason": f"superseded by {turn.turn_id}",
                            }
                        else:
                            working[matched_prior_id] = Record(id=matched_prior_id, claims=new_claims)
                            proposals_by_target[matched_prior_id] = {
                                "type": "REVISE",
                                "record_id": matched_prior_id,
                                "target_id": matched_prior_id,
                                "claims": list(new_claims),
                                "turn_id": turn.turn_id,
                                "quote": clause,
                                "supports": [claim],
                                "contradicts": contradicts,
                                "reason": f"revised by {turn.turn_id}",
                            }
                    else:
                        new_rid = _next_record_id(used_ids)
                        used_ids.add(new_rid)
                        working[new_rid] = Record(id=new_rid, claims=(claim,))
                        write_proposals.append(
                            {
                                "type": "WRITE",
                                "record_id": new_rid,
                                "claims": [claim],
                                "turn_id": turn.turn_id,
                                "quote": clause,
                                "supports": [claim],
                                "contradicts": contradicts,
                            }
                        )
                    break

        all_proposals = [*proposals_by_target.values(), *write_proposals]
        resolved_case_id = case_id or f"{transcript.conversation_id}-extract"
        return build_transition_from_conversation(
            transcript,
            case_id=resolved_case_id,
            before=before_records,
            proposals=all_proposals,
            allowed_roles=allowed_roles_set,
        )


class LLMConversationExtractor:
    """JSON-mode LLM extractor turning conversation turns into a grounded Transition."""

    def __init__(
        self,
        client: Any,
        *,
        prompt_version: str = CONVERSATION_EXTRACTOR_PROMPT_VERSION,
        allowed_roles: Iterable[str] = DEFAULT_GROUNDING_ROLES,
    ) -> None:
        if client is None:
            raise ValueError("client must not be None")
        self.client = client
        self.prompt_version = prompt_version
        self.allowed_roles = tuple(_normalize_role(r) for r in allowed_roles)
        self.model = str(getattr(client, "model", client.__class__.__name__))
        self.last_extraction_metadata: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return f"llm:{self.model}"

    def _messages(
        self,
        transcript: ConversationTranscript,
        before_records: tuple[Record, ...],
        focus_turns: Sequence[ConversationTurn],
    ) -> list[dict[str, str]]:
        system_prompt = (
            "You extract candidate memory operations from actual conversation turns. "
            "Use ONLY facts explicitly stated in allowed-role turns. Return ONLY a JSON object "
            "with key 'operations' (a list). Each item must declare 'type' in "
            "{WRITE, REVISE, DELETE, SUPERSEDE, NOOP}. Every non-NOOP item must cite 'turn_id' "
            "and a verbatim 'quote' substring from that turn. WRITE/REVISE/SUPERSEDE require "
            "non-empty 'claims' (atomic strings), optional 'contradicts' (list of prior claims), "
            "and REVISE/SUPERSEDE require 'target_id'. DELETE requires 'target_id' and 'rationale'. "
            "Return {\"operations\": []} if no memory change is warranted."
        )
        payload = {
            "prompt_version": self.prompt_version,
            "allowed_roles": list(self.allowed_roles),
            "before": [asdict(r) for r in before_records],
            "focus_turn_ids": [t.turn_id for t in focus_turns],
            "turns": [
                {"turn_id": t.turn_id, "role": t.role, "content": t.content}
                for t in transcript.turns
            ],
        }
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def extract(
        self,
        transcript: ConversationTranscript,
        before: Iterable[Record | Mapping[str, Any]] = (),
        *,
        case_id: str | None = None,
        focus_turn_ids: Iterable[str] | None = None,
    ) -> Transition:
        if not isinstance(transcript, ConversationTranscript):
            raise ConversationError("transcript must be a ConversationTranscript")
        before_records = _coerce_records(before)
        if focus_turn_ids is not None:
            focus_turns = [
                t for ref in focus_turn_ids if (t := transcript.turn_by_id(ref)) is not None
            ]
        else:
            focus_turns = list(transcript.turns)

        allowed_set = frozenset(self.allowed_roles)

        def _validate(payload: dict[str, Any]) -> None:
            if not isinstance(payload, dict) or set(payload) != {"operations"}:
                raise ValueError("extractor output must contain exactly one 'operations' key")
            ops = payload["operations"]
            if not isinstance(ops, list):
                raise ValueError("'operations' must be a JSON array")
            for op in ops:
                if not isinstance(op, dict):
                    raise ValueError("each operation must be a JSON object")
                kind = str(op.get("type", "")).upper()
                if kind not in {"WRITE", "REVISE", "DELETE", "SUPERSEDE", "NOOP"}:
                    raise ValueError(f"invalid operation type: {kind!r}")
                if kind == "NOOP":
                    if len(ops) != 1:
                        raise ValueError("NOOP must be the sole operation")
                    continue
                turn_id = op.get("turn_id")
                quote = op.get("quote")
                if not isinstance(turn_id, str) or not turn_id.strip():
                    raise ValueError(f"{kind} requires turn_id")
                turn = transcript.turn_by_id(turn_id)
                if turn is None or turn.role not in allowed_set:
                    raise ValueError(f"ungrounded or disallowed turn_id: {turn_id!r}")
                if not isinstance(quote, str) or not quote.strip():
                    raise ValueError(f"{kind} requires non-empty verbatim quote")
                if _normalize_ws(quote).lower() not in _normalize_ws(turn.content).lower():
                    raise ValueError(f"quote not present in turn {turn.turn_id!r}")
                if kind == "DELETE":
                    if not isinstance(op.get("target_id"), str) or not op["target_id"].strip():
                        raise ValueError("DELETE requires target_id")
                    if not isinstance(op.get("rationale"), str) or not op["rationale"].strip():
                        raise ValueError("DELETE requires rationale")
                else:
                    claims = op.get("claims")
                    if (
                        not isinstance(claims, list)
                        or not claims
                        or any(not isinstance(c, str) or not c.strip() for c in claims)
                    ):
                        raise ValueError(f"{kind} requires non-empty string claims list")
                    if kind in {"REVISE", "SUPERSEDE"} and (
                        not isinstance(op.get("target_id"), str) or not op["target_id"].strip()
                    ):
                        raise ValueError(f"{kind} requires target_id")

        messages = self._messages(transcript, before_records, focus_turns)
        try:
            completion = self.client.complete_json(messages, validator=_validate)
        except Exception as exc:
            raise ConversationError(f"LLM conversation extraction failed: {exc}") from exc

        self.last_extraction_metadata = {
            "extractor": self.name,
            "prompt_version": self.prompt_version,
            "model": completion.model,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "total_tokens": completion.total_tokens,
            "latency_ms": completion.latency_ms,
            "cached": completion.cached,
        }
        resolved_case_id = case_id or f"{transcript.conversation_id}-llm-extract"
        return build_transition_from_conversation(
            transcript,
            case_id=resolved_case_id,
            before=before_records,
            proposals=completion.json["operations"],
            allowed_roles=allowed_set,
        )


# ---------------------------------------------------------------------------
# Multi-turn conversation session runner
# ---------------------------------------------------------------------------


def _apply_operations_unguarded(
    current: dict[str, Record],
    raw_transition: Mapping[str, Any],
) -> dict[str, Record]:
    """Apply a proposed step's candidate/operations to an unguarded store."""
    candidate_raw = raw_transition.get("candidate")
    before_raw = raw_transition.get("before")
    if isinstance(before_raw, list) and isinstance(candidate_raw, list):
        before_recs = [Record.from_dict(r) for r in before_raw]
        if {r.id: frozenset(r.claims) for r in current.values()} == {
            r.id: frozenset(r.claims) for r in before_recs
        }:
            return {r.id: r for r in (Record.from_dict(c) for c in candidate_raw)}

    # If earlier faulty steps already poisoned the unguarded store so `before`
    # diverged, apply declared operations directly onto the current unguarded state.
    updated = dict(current)
    cand_by_id: dict[str, Record] = {}
    if isinstance(candidate_raw, list):
        for item in candidate_raw:
            rec = Record.from_dict(item)
            cand_by_id[rec.id] = rec
    for op in raw_transition.get("operations", []):
        kind = str(op.get("type", "")).upper()
        if kind == "WRITE":
            rid = op.get("record_id")
            if rid in cand_by_id:
                updated[rid] = cand_by_id[rid]
        elif kind == "REVISE":
            tid = op.get("target_id")
            rid = op.get("record_id")
            if tid in updated and rid in cand_by_id:
                updated[tid] = cand_by_id[rid]
        elif kind == "DELETE":
            tid = op.get("target_id")
            updated.pop(tid, None)
        elif kind == "SUPERSEDE":
            tid = op.get("target_id")
            rid = op.get("record_id")
            updated.pop(tid, None)
            if rid in cand_by_id:
                updated[rid] = cand_by_id[rid]
    return updated


def run_conversation_session(
    transcript: ConversationTranscript,
    store_dir: str | Path,
    *,
    condition: str = "graph",
    steps: Sequence[Mapping[str, Any]] | None = None,
    extractor: Any | None = None,
    initial_records: Iterable[Record | Mapping[str, Any]] = (),
    semantic_comparator: Any | None = None,
    allowed_roles: Iterable[str] = DEFAULT_GROUNDING_ROLES,
    require_claim_lexical_overlap: bool = True,
    run_id: str | None = None,
    code_revision: str = "",
    manifest_id: str = "",
    manifest_sha256: str = "",
) -> dict[str, Any]:
    """Execute a multi-turn conversation against a guarded or unguarded store."""
    if condition not in SESSION_CONDITIONS:
        raise ConversationError(
            f"unsupported condition {condition!r}; expected one of {SESSION_CONDITIONS}"
        )
    if not isinstance(transcript, ConversationTranscript):
        raise ConversationError("transcript must be a ConversationTranscript")

    base_dir = Path(store_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    store_path = base_dir / "state.json"
    log_path = base_dir / "audit.jsonl"
    session_run_id = run_id or str(uuid.uuid4())
    seed_records = _coerce_records(initial_records)

    if store_path.exists():
        loaded_map, _, _ = _parse_store(store_path.read_text(encoding="utf-8"), store_path)
        current_map = dict(loaded_map)
    else:
        current_map = {r.id: r for r in seed_records}
        if seed_records and condition != "unguarded":
            _atomic_write(store_path, _serialize_store(current_map, 0))
        elif seed_records and condition == "unguarded":
            _atomic_write(
                store_path,
                json.dumps([asdict(r) for r in current_map.values()], ensure_ascii=False, indent=2)
                + "\n",
            )

    active_extractor = extractor
    if steps is None and active_extractor is None:
        active_extractor = RuleBasedConversationExtractor()

    if steps is None:
        step_items: list[dict[str, Any]] = [
            {
                "step_id": f"{transcript.conversation_id}-step-{idx}",
                "case_id": f"{transcript.conversation_id}-{turn.turn_id}",
                "up_to_turn_id": turn.turn_id,
                "focus_turn_ids": [turn.turn_id],
            }
            for idx, turn in enumerate(transcript.user_turns(), start=1)
        ]
    else:
        step_items = [dict(s) for s in steps]

    step_results: list[dict[str, Any]] = []
    for index, step in enumerate(step_items, start=1):
        step_id = str(step.get("step_id") or step.get("case_id") or f"step-{index}")
        up_to = step.get("up_to_turn_id")
        step_transcript = (
            transcript.slice_turns(up_to_turn_id=up_to) if up_to is not None else transcript
        )
        prior_tuple = tuple(current_map.values())

        raw_transition: dict[str, Any] | None = None
        transition: Transition | None = None
        invalid_reason = ""

        if "transition" in step or "candidate" in step:
            if "transition" in step:
                raw_transition = dict(step["transition"])
            else:
                transition_keys = {
                    "case_id",
                    "schema_version",
                    "before",
                    "candidate",
                    "evidence",
                    "required_claims",
                    "operations",
                    "supersedes",
                }
                raw_transition = {k: v for k, v in step.items() if k in transition_keys}
                raw_transition.setdefault("schema_version", SCHEMA_VERSION)
            raw_transition.setdefault("case_id", step.get("case_id", step_id))
            if "before" not in raw_transition:
                raw_transition["before"] = [asdict(r) for r in prior_tuple]
            try:
                transition = Transition.from_dict(raw_transition)
            except (KeyError, TypeError, ValueError) as exc:
                invalid_reason = str(exc)
        elif "proposals" in step:
            try:
                transition = build_transition_from_conversation(
                    step_transcript,
                    case_id=str(step.get("case_id", step_id)),
                    before=prior_tuple,
                    proposals=step["proposals"],
                    in_scope_claims=step.get("in_scope_claims", ()),
                    required_claims=step.get("required_claims"),
                    allowed_roles=allowed_roles,
                )
                raw_transition = transition_dict(transition)
            except (KeyError, TypeError, ValueError) as exc:
                invalid_reason = str(exc)
        else:
            assert active_extractor is not None
            focus_ids = step.get("focus_turn_ids")
            if focus_ids is None and up_to is not None:
                focus_ids = [up_to]
            try:
                transition = active_extractor.extract(
                    step_transcript,
                    before=prior_tuple,
                    case_id=str(step.get("case_id", step_id)),
                    focus_turn_ids=focus_ids,
                )
                raw_transition = transition_dict(transition)
            except (KeyError, TypeError, ValueError) as exc:
                invalid_reason = str(exc)

        if transition is None:
            started_at = datetime.now(timezone.utc).isoformat()
            event = {
                "run_id": session_run_id,
                "code_revision": code_revision,
                "manifest_id": manifest_id,
                "manifest_sha256": manifest_sha256,
                "started_at": started_at,
                "status": "invalid_input",
                "condition": condition,
                "decision": "INVALID_INPUT",
                "case_id": step.get("case_id", step_id),
                "input": raw_transition,
                "error_type": "ValueError",
                "error": invalid_reason,
            }
            _append_event(log_path, event)
            step_results.append(
                {
                    "step_id": step_id,
                    "case_id": str(step.get("case_id", step_id)),
                    "up_to_turn_id": up_to,
                    "condition": condition,
                    "decision": "INVALID_INPUT",
                    "status": "invalid_input",
                    "error": invalid_reason,
                    "checks": [],
                    "store_written": False,
                }
            )
            continue

        if condition == "unguarded":
            started = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            current_map = _apply_operations_unguarded(current_map, transition_dict(transition))
            _atomic_write(
                store_path,
                json.dumps([asdict(r) for r in current_map.values()], ensure_ascii=False, indent=2)
                + "\n",
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            event = {
                "run_id": session_run_id,
                "code_revision": code_revision,
                "manifest_id": manifest_id,
                "manifest_sha256": manifest_sha256,
                "started_at": started_at,
                "latency_ms": latency_ms,
                "status": "completed",
                "condition": "unguarded",
                "decision": "APPLIED",
                "case_id": transition.case_id,
                "input": transition_dict(transition),
                "store_after": [asdict(r) for r in current_map.values()],
            }
            _append_event(log_path, event)
            step_results.append(
                {
                    "step_id": step_id,
                    "case_id": transition.case_id,
                    "up_to_turn_id": up_to,
                    "condition": condition,
                    "decision": "APPLIED",
                    "status": "completed",
                    "latency_ms": latency_ms,
                    "checks": [],
                    "store_written": True,
                }
            )
            continue

        rep = "text" if condition in {"text", "text_fair", "semantic"} else "graph"

        def _step_verifier(t: Transition, representation: str, _prepared: Any) -> Result:
            return verify_with_transcript(
                t,
                step_transcript,
                representation,
                semantic_comparator=semantic_comparator if condition == "semantic" else None,
                allowed_roles=allowed_roles,
                require_claim_lexical_overlap=require_claim_lexical_overlap,
                fair_text=(condition == "text_fair"),
            )

        res = run_case(
            transition,
            store_path,
            log_path,
            representation=rep,
            run_id=session_run_id,
            code_revision=code_revision,
            manifest_id=manifest_id,
            manifest_sha256=manifest_sha256,
            verifier=_step_verifier,
        )
        if res.decision == "ACCEPT":
            current_map = {r.id: r for r in transition.candidate}
        last_line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        step_results.append(
            {
                "step_id": step_id,
                "case_id": transition.case_id,
                "up_to_turn_id": up_to,
                "condition": condition,
                "decision": res.decision,
                "status": last_line.get("status", "completed"),
                "latency_ms": last_line.get("latency_ms"),
                "checks": [asdict(c) for c in res.checks],
                "store_written": bool(last_line.get("store_written", False)),
            }
        )

    final_records = [asdict(r) for r in current_map.values()]
    return {
        "conversation_id": transcript.conversation_id,
        "condition": condition,
        "run_id": session_run_id,
        "step_count": len(step_results),
        "steps": step_results,
        "final_records": final_records,
        "store_path": str(store_path),
        "audit_path": str(log_path),
    }


def _demo_conversation() -> ConversationTranscript:
    return parse_conversation(
        {
            "conversation_id": "demo-conversation",
            "scope": {"user_id": "demo-user"},
            "turns": [
                {"turn_id": "t1", "role": "user", "content": "Halo! Saya tinggal di Bandung."},
                {"turn_id": "t2", "role": "assistant", "content": "Halo, senang bertemu!"},
                {
                    "turn_id": "t3",
                    "role": "user",
                    "content": "Saya sekarang bekerja di Tokopedia dan mulai belajar bahasa Jepang.",
                },
                {
                    "turn_id": "t4",
                    "role": "user",
                    "content": "Koreksi: saya pindah ke Surabaya, bukan Bandung.",
                },
            ],
        },
        format="membud",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m membud.conversation",
        description="Run MemBud extraction and transcript-grounded verification on conversation data.",
    )
    parser.add_argument("--input", help="path to conversation file (.json, .jsonl, or text)")
    parser.add_argument("--format", choices=SUPPORTED_FORMATS, default="auto")
    parser.add_argument("--condition", choices=SESSION_CONDITIONS, default="graph")
    parser.add_argument("--output", default="membud-output/conversation")
    args = parser.parse_args(argv)

    transcript = (
        load_conversation(args.input, format=args.format)
        if args.input
        else _demo_conversation()
    )
    out_dir = Path(args.output)
    summary = run_conversation_session(
        transcript,
        out_dir,
        condition=args.condition,
        extractor=RuleBasedConversationExtractor(),
    )
    summary_path = out_dir / "conversation-summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"conversation={transcript.conversation_id} condition={args.condition} "
        f"steps={summary['step_count']} records={len(summary['final_records'])} "
        f"summary={summary_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
