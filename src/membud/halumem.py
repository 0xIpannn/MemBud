"""HaluMem dataset loader, transition builder, seeded fault injector, and silver labeler.

This module implements the Phase 1 (controlled, offline) and Phase 2 (natural
Mem0 proposal evaluation & silver-labeling) data pipeline for MemBud's RQ1
evaluation on HaluMem-shaped multi-session dialogues:

1. **Strict streaming loader** (:func:`iter_halumem_users`,
   :func:`load_halumem_dataset`): streams ``HaluMem-Medium.jsonl`` or
   ``HaluMem-Long.jsonl`` from a local path, validates every required field
   (``uuid``, ``persona_info``, ``sessions`` with ``dialogue``,
   ``memory_points`` carrying ``memory_content``, ``memory_type``,
   ``memory_source``, ``is_update``, ``original_memories``, ``timestamp``, and
   ``questions``), fails closed on malformed data, and records only the file
   SHA-256 and dataset revision in metadata (never raw content).
2. **Transition builder** (:func:`build_halumem_transitions`): converts each
   user's chronological sessions into schema-version-2 :class:`Transition`
   objects (``before`` = accumulated gold state, ``candidate`` = gold state
   after the session, ``evidence`` = verbatim user-turn quotes linked by
   explicit turn reference or documented lexical matching, ``required_claims``
   = prior claims that must survive, ``operations`` = ``WRITE`` / ``REVISE`` or
   ``SUPERSEDE`` / ``NOOP``). Sessions with memory points that cannot be
   grounded in any user turn (or whose ``original_memories`` are absent from
   prior state) are reported as ungroundable rather than given invented
   evidence.
3. **Seeded fault injector** (:func:`inject_halumem_faults`): deterministically
   generates labeled faulty variants of three types — ``omission`` of a
   required prior claim/record, ``corruption`` of a valid prior record, and
   ``unsupported_addition`` — recording the RNG seed and explicitly documenting
   label circularity as a limitation.
4. **Provisional silver labeler & human-audit sampler** (:func:`derive_silver_label`,
   :func:`select_stratified_audit_sample`, :func:`build_phase2_blind_quiz`):
   evaluates recorded real-Mem0 proposals offline ("propose once, judge many"),
   derives provisional silver labels (deterministic rules first, optional
   cached LLM claim-matching judge), attributes paraphrase grounding false
   holds, and builds a blind human-review quiz using ``scripts/review_quiz.py``.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .conversation import (
    ConversationError,
    ConversationTranscript,
    ConversationTurn,
    _claim_grounded_in_quote,
    _content_tokens,
    _normalize_role,
    _normalize_ws,
)
from .grounding import (
    CoverageGrounder,
    GroundingReport,
    GroundingSource,
    EvidenceGrounder,
    _numbers_in_text,
)
from .schemas import SCHEMA_VERSION, Evidence, Operation, Record, Transition
from .serialization import transition_dict

FAULT_TYPES: tuple[str, ...] = ("omission", "corruption", "unsupported_addition")
UPDATE_OPERATIONS: tuple[str, ...] = ("REVISE", "SUPERSEDE")
SYNTHETIC_FIXTURE_REVISION = "synthetic-halumem-fixture-v1"

LEXICAL_EVIDENCE_ASSUMPTION = (
    "HaluMem session memory_points are annotated at the session level and do not "
    "consistently carry direct turn_id pointers. When memory_source does not "
    "resolve directly to a user-role turn ID, evidence is derived by matching "
    "each memory_content claim against user-role dialogue turns in the same "
    "session via content-token lexical overlap (using verbatim turn text as the "
    "quote). Memory points with zero lexical overlap against all user-role turns "
    "in their session are reported as ungroundable and excluded rather than "
    "assigned fabricated evidence."
)

INJECTOR_CIRCULARITY_LIMITATION = (
    "Fault labels (omission, corruption, unsupported_addition) in Phase 1 are "
    "assigned by construction from the deterministic fault injector that created "
    "each variant. This entails circularity between fault construction and "
    "verifier criteria and measures controlled mechanism sensitivity, not "
    "natural error prevalence."
)

OMISSION_SCOPES: tuple[str, ...] = ("session_and_prior", "prior_only")
NORMALIZED_EVIDENCE_THETAS: tuple[float, ...] = (1.0, 0.8, 0.6)

SILVER_LABEL_LIMITATION = (
    "Phase 2 labels for natural Mem0 proposals are provisional 'silver' labels "
    "derived by comparing proposed claim diffs against HaluMem gold session "
    "memory_points (deterministic lexical matching first, optional cached LLM "
    "claim matcher). Silver labels are not ground truth and remain unvalidated "
    "until human audit agreement is graded on the blind review quiz."
)

_HALLUCINATION_POOL: tuple[str, ...] = (
    "Owns a private Gulfstream jet moored in Zurich",
    "Holds a diplomatic passport from the Grand Duchy of Luxembourg",
    "Won an Olympic gold medal in synchronized diving in 2016",
    "Serves as Chief Astronaut at the Antarctic Space Agency",
    "Collects seventeenth-century Venetian glass astrolabes",
    "Operates a deep-sea titanium submarine charter in Reykjavik",
    "Authored a trilogy of Icelandic opera librettos on quantum gravity",
    "Breeds champion Himalayan snow leopards on a private island",
)

_CORRUPTION_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Bandung", "Reykjavik"),
    ("Surabaya", "Anchorage"),
    ("Jakarta", "Vladivostok"),
    ("Yogyakarta", "Casablanca"),
    ("Malang", "Tashkent"),
    ("Semarang", "Montevideo"),
    ("Seattle", "Ulaanbaatar"),
    ("Austin", "Timbuktu"),
    ("Berlin", "Valparaiso"),
    ("Gojek", " Weyland-Yutani Corp"),
    ("Tokopedia", "Tyrell Corporation"),
    ("Traveloka", "Initech MacroSystems"),
    ("Python", "COBOL-68"),
    ("Rust", "FORTRAN-II"),
    ("vegetarian", "strictly carnivorous"),
    ("vegan", "allergic to all vegetables"),
    ("peanut", "pure oxygen"),
    ("shrimp", "distilled water"),
    ("morning", "midnight"),
    ("guitar", "theremin"),
    ("piano", "bagpipes"),
    ("running", "skydiving"),
    ("cycling", "bungee jumping"),
)

_ALLOWED_TOP_KEYS = frozenset(
    {
        "uuid",
        "persona_info",
        "sessions",
        "metadata",
        "scope",
        # Fields observed in the real HaluMem-Medium/Long files (revision cb04336).
        "token_cost",
        "total_dialogue_token_length",
        "total_question_count",
    }
)
_ALLOWED_SESSION_KEYS = frozenset(
    {
        "session_id",
        "id",
        "dialogue",
        "memory_points",
        "timestamp",
        "start_time",
        "end_time",
        "questions",
        "metadata",
        # Observed in the real files; informational only.
        "memory_points_count",
        "dialogue_turn_num",
        "dialogue_token_length",
        "question_count",
        "title",
        "generation_source",
        "is_generated_qa_session",
    }
)
_ALLOWED_MEMORY_POINT_KEYS = frozenset(
    {
        "memory_content",
        "memory_type",
        "memory_source",
        "is_update",
        "original_memories",
        "timestamp",
        "importance",
        "index",
        "id",
        "memory_id",
        "memories_from_system",
        # Observed in the real files. ``reference_*``/``reason`` appear only on
        # ``interference`` points, which are deliberate false distractors.
        "event_source",
        "role",
        "reason",
        "reference_memory_content",
        "reference_memory_index",
        "reference_memory_type",
        "source_memory_index",
    }
)
_ALLOWED_QUESTION_KEYS = frozenset(
    {
        "question_id",
        "id",
        "question",
        "answer",
        "gold_answer",
        "reference_answer",
        "question_type",
        "type",
        "timestamp",
        "evidence",
        "memory_points",
        "difficulty",
    }
)


class HaluMemValidationError(ValueError):
    """Raised when a HaluMem JSONL file or entry violates the strict schema."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


INTERFERENCE_SOURCE = "interference"


@dataclass(frozen=True)
class HaluMemMemoryPoint:
    """One validated reference memory point inside a HaluMem session."""

    point_id: str
    memory_content: str
    memory_type: str
    memory_source: str
    is_update: bool
    original_memories: tuple[str, ...]
    timestamp: str
    importance: str | int | float | None = None
    reference_memory_content: str = ""

    @property
    def is_interference(self) -> bool:
        """True for HaluMem's deliberate false-distractor points (not gold)."""
        return self.memory_source == INTERFERENCE_SOURCE


@dataclass(frozen=True)
class HaluMemQuestion:
    """One validated QA pair attached to a HaluMem session."""

    question_id: str
    question: str
    answer: str
    question_type: str = ""
    timestamp: str = ""


@dataclass(frozen=True)
class HaluMemSession:
    """One validated multi-turn session for a HaluMem user."""

    session_id: str
    dialogue: tuple[ConversationTurn, ...]
    memory_points: tuple[HaluMemMemoryPoint, ...]
    timestamp: str = ""
    questions: tuple[HaluMemQuestion, ...] = ()

    @property
    def gold_points(self) -> tuple[HaluMemMemoryPoint, ...]:
        """Reference memory points excluding ``interference`` distractors."""
        return tuple(p for p in self.memory_points if not p.is_interference)

    @property
    def interference_points(self) -> tuple[HaluMemMemoryPoint, ...]:
        """HaluMem's deliberately false, plausible-looking distractor points."""
        return tuple(p for p in self.memory_points if p.is_interference)

    def to_transcript(self, *, user_uuid: str) -> ConversationTranscript:
        return ConversationTranscript(
            conversation_id=f"{user_uuid}_{self.session_id}",
            turns=self.dialogue,
            scope={"user_id": user_uuid},
            metadata={
                "source_format": "halumem",
                "user_uuid": user_uuid,
                "session_id": self.session_id,
                "timestamp": self.timestamp,
            },
        )


@dataclass(frozen=True)
class HaluMemUser:
    """One validated user record from a HaluMem JSONL dataset."""

    uuid: str
    persona_info: str
    sessions: tuple[HaluMemSession, ...]


@dataclass(frozen=True)
class HaluMemDatasetMeta:
    """Content-free provenance summary of a loaded HaluMem JSONL file."""

    file_sha256: str
    dataset_revision: str
    user_count: int
    session_count: int
    memory_point_count: int
    update_point_count: int
    question_count: int
    is_synthetic_fixture: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Strict validation helpers & streaming loader
# ---------------------------------------------------------------------------


def _require_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HaluMemValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise HaluMemValidationError(f"{field_name} must be a string")
    return value.strip()


def _parse_is_update(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise HaluMemValidationError(
        f"{field_name} must be a boolean or 'True'/'False' string, got {value!r}"
    )


def _parse_memory_source(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (list, tuple)) and value:
        parts: list[str] = []
        for idx, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise HaluMemValidationError(
                    f"{field_name}[{idx}] must be a non-empty string"
                )
            parts.append(item.strip())
        return "; ".join(parts)
    raise HaluMemValidationError(
        f"{field_name} must be a non-empty string or list of strings"
    )


def _parse_original_memories(
    value: Any, *, is_update: bool, field_name: str
) -> tuple[str, ...]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise HaluMemValidationError(f"{field_name} must be a list")

    parsed: list[str] = []
    for idx, item in enumerate(items):
        if isinstance(item, str):
            text = _require_non_empty_str(item, f"{field_name}[{idx}]")
            parsed.append(text)
        elif isinstance(item, Mapping) and "memory_content" in item:
            text = _require_non_empty_str(
                item["memory_content"], f"{field_name}[{idx}].memory_content"
            )
            parsed.append(text)
        else:
            raise HaluMemValidationError(
                f"{field_name}[{idx}] must be a non-empty string or object with memory_content"
            )

    if is_update and not parsed:
        raise HaluMemValidationError(
            f"{field_name} must be non-empty when is_update is True"
        )
    if not is_update and parsed:
        raise HaluMemValidationError(
            f"{field_name} must be empty when is_update is False"
        )
    return tuple(dict.fromkeys(parsed))


def _parse_persona_info(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, Mapping) and value:
        try:
            return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise HaluMemValidationError(
                f"{field_name} mapping is not JSON-serializable: {exc}"
            ) from exc
    raise HaluMemValidationError(
        f"{field_name} must be a non-empty string or non-empty JSON object"
    )


def _parse_memory_point(
    raw: Any, *, session_id: str, index: int
) -> HaluMemMemoryPoint:
    label = f"session {session_id!r} memory_points[{index}]"
    if not isinstance(raw, Mapping):
        raise HaluMemValidationError(f"{label} must be a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_MEMORY_POINT_KEYS)
    if unknown:
        raise HaluMemValidationError(
            f"{label} has unexpected fields: {', '.join(unknown)}"
        )
    for req in (
        "memory_content",
        "memory_type",
        "memory_source",
        "is_update",
        "original_memories",
        "timestamp",
    ):
        if req not in raw:
            raise HaluMemValidationError(f"{label} is missing required field {req!r}")

    content = _require_non_empty_str(raw["memory_content"], f"{label}.memory_content")
    mem_type = _require_non_empty_str(raw["memory_type"], f"{label}.memory_type")
    mem_source = _parse_memory_source(raw["memory_source"], f"{label}.memory_source")
    is_update = _parse_is_update(raw["is_update"], f"{label}.is_update")
    orig = _parse_original_memories(
        raw["original_memories"],
        is_update=is_update,
        field_name=f"{label}.original_memories",
    )
    timestamp = _require_str(raw["timestamp"], f"{label}.timestamp")
    raw_id = raw.get("point_id") or raw.get("memory_id") or raw.get("id") or raw.get("index")
    point_id = (
        str(raw_id).strip()
        if raw_id is not None and str(raw_id).strip()
        else f"{session_id}_m{index}"
    )
    reference_content = raw.get("reference_memory_content", "")
    if reference_content is not None and not isinstance(reference_content, str):
        raise HaluMemValidationError(
            f"{label}.reference_memory_content must be a string"
        )
    if mem_source == INTERFERENCE_SOURCE and (is_update or orig):
        raise HaluMemValidationError(
            f"{label}: interference points must not be updates"
        )
    importance = raw.get("importance")
    if importance is not None and (
        isinstance(importance, bool) or not isinstance(importance, (str, int, float))
    ):
        raise HaluMemValidationError(
            f"{label}.importance must be a string, number, or null"
        )
    return HaluMemMemoryPoint(
        point_id=point_id,
        memory_content=content,
        memory_type=mem_type,
        memory_source=mem_source,
        is_update=is_update,
        original_memories=orig,
        timestamp=timestamp,
        importance=importance,
        reference_memory_content=reference_content or "",
    )


def _parse_question(raw: Any, *, session_id: str, index: int) -> HaluMemQuestion:
    label = f"session {session_id!r} questions[{index}]"
    if not isinstance(raw, Mapping):
        raise HaluMemValidationError(f"{label} must be a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_QUESTION_KEYS)
    if unknown:
        raise HaluMemValidationError(
            f"{label} has unexpected fields: {', '.join(unknown)}"
        )
    if "question" not in raw:
        raise HaluMemValidationError(f"{label} is missing required field 'question'")
    question_text = _require_non_empty_str(raw["question"], f"{label}.question")
    ans_raw = (
        raw.get("answer")
        if "answer" in raw
        else raw.get("gold_answer", raw.get("reference_answer"))
    )
    if ans_raw is None:
        raise HaluMemValidationError(f"{label} is missing required field 'answer'")
    answer_text = _require_non_empty_str(ans_raw, f"{label}.answer")
    q_id_raw = raw.get("question_id") or raw.get("id")
    q_id = (
        str(q_id_raw).strip()
        if q_id_raw is not None and str(q_id_raw).strip()
        else f"{session_id}_q{index}"
    )
    q_type = str(raw.get("question_type") or raw.get("type") or "").strip()
    ts = _require_str(raw.get("timestamp", ""), f"{label}.timestamp")
    return HaluMemQuestion(
        question_id=q_id,
        question=question_text,
        answer=answer_text,
        question_type=q_type,
        timestamp=ts,
    )


def _parse_session(raw: Any, *, user_uuid: str, index: int) -> HaluMemSession:
    label = f"user {user_uuid!r} sessions[{index}]"
    if not isinstance(raw, Mapping):
        raise HaluMemValidationError(f"{label} must be a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_SESSION_KEYS)
    if unknown:
        raise HaluMemValidationError(
            f"{label} has unexpected fields: {', '.join(unknown)}"
        )
    for req in ("dialogue", "memory_points"):
        if req not in raw:
            raise HaluMemValidationError(f"{label} is missing required field {req!r}")
    if "timestamp" not in raw and "start_time" not in raw:
        raise HaluMemValidationError(
            f"{label} is missing required field 'timestamp' (or 'start_time')"
        )

    raw_sid = raw.get("session_id") or raw.get("id") or f"s{index}"
    session_id = _require_non_empty_str(str(raw_sid), f"{label}.session_id")
    timestamp = _require_str(
        raw["timestamp"] if "timestamp" in raw else raw["start_time"],
        f"{label}.timestamp",
    )

    raw_dialogue = raw["dialogue"]
    if not isinstance(raw_dialogue, list) or not raw_dialogue:
        raise HaluMemValidationError(f"{label}.dialogue must be a non-empty list")

    turns: list[ConversationTurn] = []
    seen_turn_ids: set[str] = set()
    for t_idx, raw_turn in enumerate(raw_dialogue, start=1):
        t_label = f"{label}.dialogue[{t_idx}]"
        if not isinstance(raw_turn, Mapping):
            raise HaluMemValidationError(f"{t_label} must be a JSON object")
        if "role" not in raw_turn or "content" not in raw_turn:
            raise HaluMemValidationError(
                f"{t_label} must contain 'role' and 'content'"
            )
        content = _require_non_empty_str(raw_turn["content"], f"{t_label}.content")
        try:
            role = _normalize_role(raw_turn["role"])
        except ConversationError as exc:
            raise HaluMemValidationError(f"{t_label}: {exc}") from exc
        raw_tid = raw_turn.get("turn_id") or raw_turn.get("id")
        turn_id = (
            _require_non_empty_str(str(raw_tid), f"{t_label}.turn_id")
            if raw_tid is not None
            else f"{session_id}_t{t_idx}"
        )
        if turn_id in seen_turn_ids:
            raise HaluMemValidationError(
                f"{label} has duplicate turn_id {turn_id!r}"
            )
        seen_turn_ids.add(turn_id)
        turn_ts = _require_str(
            raw_turn.get("timestamp", timestamp), f"{t_label}.timestamp"
        )
        speaker = _require_str(raw_turn.get("speaker", ""), f"{t_label}.speaker")
        turns.append(
            ConversationTurn(
                turn_id=turn_id,
                role=role,
                content=content,
                speaker=speaker,
                timestamp=turn_ts,
                session_id=session_id,
            )
        )

    if not any(turn.role == "user" for turn in turns):
        raise HaluMemValidationError(
            f"{label}.dialogue must include at least one user-role turn"
        )

    raw_points = raw["memory_points"]
    if not isinstance(raw_points, list):
        raise HaluMemValidationError(f"{label}.memory_points must be a list")
    points = tuple(
        _parse_memory_point(item, session_id=session_id, index=m_idx)
        for m_idx, item in enumerate(raw_points, start=1)
    )

    raw_questions = raw.get("questions", [])
    if not isinstance(raw_questions, list):
        raise HaluMemValidationError(f"{label}.questions must be a list")
    questions = tuple(
        _parse_question(item, session_id=session_id, index=q_idx)
        for q_idx, item in enumerate(raw_questions, start=1)
    )
    declared = raw.get("memory_points_count")
    if declared is not None and declared != len(points):
        raise HaluMemValidationError(
            f"{label}.memory_points_count={declared!r} does not match "
            f"{len(points)} parsed memory points"
        )

    return HaluMemSession(
        session_id=session_id,
        dialogue=tuple(turns),
        memory_points=points,
        timestamp=timestamp,
        questions=questions,
    )


def parse_halumem_user(raw: Any, *, line_number: int = 1) -> HaluMemUser:
    """Validate and parse one HaluMem user JSON object."""
    label = f"line {line_number}"
    if not isinstance(raw, Mapping):
        raise HaluMemValidationError(f"{label}: user entry must be a JSON object")
    unknown = sorted(set(raw) - _ALLOWED_TOP_KEYS)
    if unknown:
        raise HaluMemValidationError(
            f"{label}: unexpected top-level fields: {', '.join(unknown)}"
        )
    for req in ("uuid", "persona_info", "sessions"):
        if req not in raw:
            raise HaluMemValidationError(
                f"{label}: missing required top-level field {req!r}"
            )
    user_uuid = _require_non_empty_str(raw["uuid"], f"{label}.uuid")
    persona_info = _parse_persona_info(raw["persona_info"], f"{label}.persona_info")
    raw_sessions = raw["sessions"]
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise HaluMemValidationError(
            f"{label} (uuid={user_uuid!r}): 'sessions' must be a non-empty list"
        )
    sessions: list[HaluMemSession] = []
    seen_sids: set[str] = set()
    for s_idx, raw_session in enumerate(raw_sessions, start=1):
        session = _parse_session(raw_session, user_uuid=user_uuid, index=s_idx)
        if session.session_id in seen_sids:
            raise HaluMemValidationError(
                f"{label} (uuid={user_uuid!r}): duplicate session_id {session.session_id!r}"
            )
        seen_sids.add(session.session_id)
        sessions.append(session)
    return HaluMemUser(
        uuid=user_uuid,
        persona_info=persona_info,
        sessions=tuple(sessions),
    )


def compute_file_sha256(path: str | Path) -> str:
    """Stream-hash a file with SHA-256."""
    file_path = Path(path)
    if not file_path.is_file():
        raise HaluMemValidationError(f"HaluMem file not found: {file_path}")
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def iter_halumem_users(path: str | Path) -> Iterator[HaluMemUser]:
    """Stream and strictly validate ``HaluMem-Medium/Long.jsonl`` one user per line."""
    file_path = Path(path)
    if not file_path.is_file():
        raise HaluMemValidationError(f"HaluMem file not found: {file_path}")
    seen_uuids: set[str] = set()
    yielded = 0
    with file_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                raise HaluMemValidationError(
                    f"line {line_number} of {file_path} is blank; strict JSONL requires one JSON object per line"
                )
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise HaluMemValidationError(
                    f"line {line_number} of {file_path} is not valid JSON: {exc.msg}"
                ) from exc
            user = parse_halumem_user(payload, line_number=line_number)
            if user.uuid in seen_uuids:
                raise HaluMemValidationError(
                    f"line {line_number} of {file_path}: duplicate user uuid {user.uuid!r}"
                )
            seen_uuids.add(user.uuid)
            yielded += 1
            yield user
    if yielded == 0:
        raise HaluMemValidationError(f"HaluMem file is empty: {file_path}")


def load_halumem_dataset(
    path: str | Path,
    *,
    dataset_revision: str = "unspecified",
    is_synthetic_fixture: bool = False,
) -> tuple[HaluMemDatasetMeta, tuple[HaluMemUser, ...]]:
    """Stream-load a HaluMem JSONL file and return ``(content_free_meta, users)``."""
    revision = _require_non_empty_str(dataset_revision, "dataset_revision")
    file_sha256 = compute_file_sha256(path)
    users = tuple(iter_halumem_users(path))
    session_count = sum(len(u.sessions) for u in users)
    point_count = sum(len(s.memory_points) for u in users for s in u.sessions)
    update_count = sum(
        1 for u in users for s in u.sessions for p in s.memory_points if p.is_update
    )
    question_count = sum(len(s.questions) for u in users for s in u.sessions)
    meta = HaluMemDatasetMeta(
        file_sha256=file_sha256,
        dataset_revision=revision,
        user_count=len(users),
        session_count=session_count,
        memory_point_count=point_count,
        update_point_count=update_count,
        question_count=question_count,
        is_synthetic_fixture=bool(is_synthetic_fixture),
    )
    return meta, users


# ---------------------------------------------------------------------------
# Transition builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UngroundablePointReport:
    """Content-free report for a session or memory point that could not be grounded."""

    case_id: str
    user_uuid: str
    session_id: str
    point_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HaluMemTransitionCase:
    """One valid groundable transition built from a user session, plus its transcript."""

    case_id: str
    user_uuid: str
    session_id: str
    session_index: int
    transition: Transition
    transcript: ConversationTranscript
    grounding_method: str  # "explicit_turn_ref" | "lexical_match" | "noop"
    update_operation_mode: str


@dataclass(frozen=True)
class HaluMemBuildReport:
    """Result of building canonical transitions across a HaluMem dataset."""

    dataset_meta: HaluMemDatasetMeta
    update_operation_mode: str
    cases: tuple[HaluMemTransitionCase, ...]
    ungroundable: tuple[UngroundablePointReport, ...]
    assumptions: tuple[str, ...]

    def summary_dict(self) -> dict[str, Any]:
        """Content-free summary suitable for run reports."""
        return {
            "dataset": self.dataset_meta.to_dict(),
            "update_operation_mode": self.update_operation_mode,
            "groundable_transition_count": len(self.cases),
            "ungroundable_count": len(self.ungroundable),
            "ungroundable_cases": [u.to_dict() for u in self.ungroundable],
            "assumptions": list(self.assumptions),
        }


def _match_user_turn_for_claim(
    claim: str,
    memory_source: str,
    transcript: ConversationTranscript,
) -> tuple[ConversationTurn | None, str, str]:
    """Return ``(matched_user_turn, verbatim_quote, method)`` or ``(None, '', '')``."""
    user_turns = transcript.user_turns()
    if not user_turns:
        return None, "", ""

    # 1. Check if memory_source directly references a user turn ID or source_ref.
    direct_turn = transcript.turn_by_id(memory_source)
    if direct_turn is not None and direct_turn.role == "user":
        q_norm = _normalize_ws(direct_turn.content).lower()
        q_tokens = _content_tokens(direct_turn.content)
        if _claim_grounded_in_quote(claim, q_norm, q_tokens):
            return direct_turn, direct_turn.content, "explicit_turn_ref"

    # 2. Check if memory_source is itself a verbatim quote inside a user turn.
    src_norm = _normalize_ws(memory_source).lower()
    if src_norm and len(src_norm) >= 4:
        for turn in user_turns:
            turn_norm = _normalize_ws(turn.content).lower()
            if src_norm in turn_norm:
                q_tokens = _content_tokens(memory_source)
                if _claim_grounded_in_quote(claim, src_norm, q_tokens):
                    return turn, memory_source, "explicit_turn_quote"

    # 3. Lexical matching assumption: pick the user turn with highest content-token overlap.
    claim_norm = _normalize_ws(claim).lower()
    claim_tokens = _content_tokens(claim)
    if not claim_tokens and not claim_norm:
        return None, "", ""

    best_turn: ConversationTurn | None = None
    best_score = 0
    for turn in user_turns:
        turn_norm = _normalize_ws(turn.content).lower()
        turn_tokens = _content_tokens(turn.content)
        overlap = len(claim_tokens & turn_tokens)
        if claim_norm and claim_norm in turn_norm:
            overlap += len(claim_tokens) + 1
        if overlap > best_score:
            best_score = overlap
            best_turn = turn

    if best_turn is None or best_score == 0:
        return None, "", ""
    return best_turn, best_turn.content, "lexical_match"


ORIGINAL_MATCH_MIN_JACCARD = 0.6

ORIGINAL_RESOLUTION_ASSUMPTION = (
    "HaluMem 'original_memories' strings are not exact pointers into earlier "
    "memory_content (only ~24% match a prior point verbatim in HaluMem-Medium "
    "revision cb04336; most are earlier-version paraphrases). The builder resolves "
    "an original to a prior claim by exact string first, otherwise by the unique "
    "best content-token Jaccard >= 0.6 among that user's prior claims with "
    "identical numerals (ties and misses are reported as ungroundable, never "
    "guessed). This is a builder-side, deterministic normalization; the verifier's "
    "exact-string claim semantics are unchanged."
)


def _resolve_original_claim(
    original: str,
    records: Iterable[Record],
    excluded_ids: set[str],
) -> tuple[Record, str] | None:
    """Resolve an ``original_memories`` string to (record, prior claim) or None."""
    candidates = [r for r in records if r.id not in excluded_ids]
    for rec in candidates:
        if original in rec.claims:
            return rec, original
    o_tokens = _content_tokens(original)
    o_nums = _numbers_in_text(original)
    scored: list[tuple[float, str, Record]] = []
    for rec in candidates:
        for claim in rec.claims:
            c_tokens = _content_tokens(claim)
            union = o_tokens | c_tokens
            if not union or _numbers_in_text(claim) != o_nums:
                continue
            scored.append((len(o_tokens & c_tokens) / len(union), claim, rec))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    best = scored[0]
    if best[0] < ORIGINAL_MATCH_MIN_JACCARD:
        return None
    if len(scored) > 1 and scored[1][0] == best[0]:
        return None
    return best[2], best[1]


def build_halumem_transitions(
    users: Iterable[HaluMemUser],
    *,
    dataset_meta: HaluMemDatasetMeta,
    update_operation: str = "REVISE",
) -> HaluMemBuildReport:
    """Build per-user session transitions from validated HaluMem user objects.

    Each session's ``before`` state is the accumulated gold memory state from
    prior sessions for that user. If a session contains any memory point that
    cannot be lexically grounded in a user turn (or whose ``is_update=True``
    ``original_memories`` cannot be resolved in ``before``), the session is
    recorded in ``ungroundable`` and excluded from ``cases`` (while the gold
    memory state still advances to the reference post-session state so later
    sessions for that user remain synchronized with HaluMem's gold state).
    """
    op_mode = update_operation.strip().upper()
    if op_mode not in UPDATE_OPERATIONS:
        raise ValueError(
            f"update_operation must be one of {UPDATE_OPERATIONS}, got {update_operation!r}"
        )

    cases: list[HaluMemTransitionCase] = []
    ungroundable: list[UngroundablePointReport] = []

    for user in users:
        accumulated: dict[str, Record] = {}
        record_counter = 0

        def _alloc_record_id() -> str:
            nonlocal record_counter
            record_counter += 1
            return f"r_{user.uuid}_{record_counter}"

        for s_idx, session in enumerate(user.sessions, start=1):
            case_id = f"{user.uuid}__{session.session_id}"
            transcript = session.to_transcript(user_uuid=user.uuid)
            before_records = tuple(accumulated.values())

            if not session.gold_points:
                required = tuple(
                    dict.fromkeys(c for r in before_records for c in r.claims)
                )
                transition = Transition(
                    case_id=case_id,
                    before=before_records,
                    candidate=before_records,
                    evidence=(),
                    required_claims=required,
                    operations=(Operation(type="NOOP"),),
                    schema_version=SCHEMA_VERSION,
                )
                transition.validate()
                cases.append(
                    HaluMemTransitionCase(
                        case_id=case_id,
                        user_uuid=user.uuid,
                        session_id=session.session_id,
                        session_index=s_idx,
                        transition=transition,
                        transcript=transcript,
                        grounding_method="noop",
                        update_operation_mode=op_mode,
                    )
                )
                continue

            working = dict(accumulated)
            operations: list[Operation] = []
            evidence_list: list[Evidence] = []
            evidence_by_key: dict[tuple[str, str], int] = {}
            retired_claims: set[str] = set()
            targeted_prior_ids: set[str] = set()
            session_ungroundable = False
            methods_seen: set[str] = set()

            def _add_evidence(
                turn: ConversationTurn,
                quote: str,
                supports: Sequence[str],
                contradicts: Sequence[str],
            ) -> str:
                key = (turn.source_ref, _normalize_ws(quote))
                if key in evidence_by_key:
                    idx = evidence_by_key[key]
                    existing = evidence_list[idx]
                    evidence_list[idx] = Evidence(
                        id=existing.id,
                        supports=tuple(dict.fromkeys([*existing.supports, *supports])),
                        source=existing.source,
                        text=existing.text,
                        contradicts=tuple(
                            dict.fromkeys([*existing.contradicts, *contradicts])
                        ),
                    )
                    return existing.id
                ev_id = f"e{len(evidence_list) + 1}"
                evidence_by_key[key] = len(evidence_list)
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

            for point in session.gold_points:
                matched_turn, quote, method = _match_user_turn_for_claim(
                    point.memory_content, point.memory_source, transcript
                )
                if matched_turn is None:
                    ungroundable.append(
                        UngroundablePointReport(
                            case_id=case_id,
                            user_uuid=user.uuid,
                            session_id=session.session_id,
                            point_id=point.point_id,
                            reason="no_user_turn_lexical_match",
                        )
                    )
                    session_ungroundable = True
                    continue
                methods_seen.add(method)

                if not point.is_update:
                    already_stored = any(
                        point.memory_content in rec.claims for rec in working.values()
                    )
                    if already_stored:
                        continue
                    new_id = _alloc_record_id()
                    working[new_id] = Record(id=new_id, claims=(point.memory_content,))
                    _add_evidence(matched_turn, quote, (point.memory_content,), ())
                    operations.append(Operation(type="WRITE", record_id=new_id))
                else:
                    # Resolve original_memories in before_records.
                    matched_targets: list[Record] = []
                    matched_claims: list[str] = []
                    unresolved = False
                    for orig_claim in point.original_memories:
                        resolved = _resolve_original_claim(
                            orig_claim, before_records, targeted_prior_ids
                        )
                        found = resolved[0] if resolved is not None else None
                        if resolved is not None:
                            matched_claims.append(resolved[1])
                        if found is None:
                            ungroundable.append(
                                UngroundablePointReport(
                                    case_id=case_id,
                                    user_uuid=user.uuid,
                                    session_id=session.session_id,
                                    point_id=point.point_id,
                                    reason="missing_original_memory_in_prior_state",
                                )
                            )
                            session_ungroundable = True
                            unresolved = True
                            break
                        if found.id not in {r.id for r in matched_targets}:
                            matched_targets.append(found)
                    if unresolved or not matched_targets:
                        continue

                    if len(matched_targets) > 1:
                        # A single memory point consolidating multiple distinct prior
                        # records would require a DELETE on the secondary prior records,
                        # which requires an explicit user deletion directive.
                        ungroundable.append(
                            UngroundablePointReport(
                                case_id=case_id,
                                user_uuid=user.uuid,
                                session_id=session.session_id,
                                point_id=point.point_id,
                                reason="multi_record_consolidation_without_delete_authorization",
                            )
                        )
                        session_ungroundable = True
                        continue

                    target_rec = matched_targets[0]
                    retained = [
                        c
                        for c in target_rec.claims
                        if c not in set(matched_claims)
                    ]
                    updated_claims = tuple(
                        dict.fromkeys([*retained, point.memory_content])
                    )
                    if updated_claims == target_rec.claims:
                        # Real HaluMem quirk: an ``is_update`` point whose result
                        # equals the prior record. It is not a state change, so it
                        # yields no operation (the schema rejects a no-op REVISE).
                        ungroundable.append(
                            UngroundablePointReport(
                                case_id=case_id,
                                user_uuid=user.uuid,
                                session_id=session.session_id,
                                point_id=point.point_id,
                                reason="update_does_not_change_prior_record",
                            )
                        )
                        continue
                    targeted_prior_ids.add(target_rec.id)
                    retired_claims.update(matched_claims)

                    # Only attach contradicts for old claims that are also lexically
                    # grounded in the matched user turn quote so transcript grounding passes.
                    q_norm = _normalize_ws(quote).lower()
                    q_tokens = _content_tokens(quote)
                    grounded_contradicts = tuple(
                        old_c
                        for old_c in matched_claims
                        if old_c != point.memory_content
                        and _claim_grounded_in_quote(old_c, q_norm, q_tokens)
                    )
                    _add_evidence(
                        matched_turn,
                        quote,
                        (point.memory_content,),
                        grounded_contradicts,
                    )


                    if op_mode == "REVISE":
                        working[target_rec.id] = Record(
                            id=target_rec.id, claims=updated_claims
                        )
                        operations.append(
                            Operation(
                                type="REVISE",
                                record_id=target_rec.id,
                                target_id=target_rec.id,
                                reason=f"halumem_is_update:{point.point_id}",
                            )
                        )
                    else:  # SUPERSEDE
                        replacement_id = _alloc_record_id()
                        del working[target_rec.id]
                        working[replacement_id] = Record(
                            id=replacement_id, claims=updated_claims
                        )
                        operations.append(
                            Operation(
                                type="SUPERSEDE",
                                record_id=replacement_id,
                                target_id=target_rec.id,
                                reason=f"halumem_is_update:{point.point_id}",
                            )
                        )

            # Advance accumulated gold state even if one point in this session was
            # ungroundable, so later sessions see the true gold reference memories.
            if session_ungroundable:
                for point in session.gold_points:
                    if point.is_update:
                        for orig_claim in point.original_memories:
                            resolved = _resolve_original_claim(
                                orig_claim, accumulated.values(), set()
                            )
                            if resolved is not None:
                                accumulated[resolved[0].id] = Record(
                                    id=resolved[0].id, claims=(point.memory_content,)
                                )
                    else:
                        if not any(
                            point.memory_content in r.claims
                            for r in accumulated.values()
                        ):
                            nid = _alloc_record_id()
                            accumulated[nid] = Record(
                                id=nid, claims=(point.memory_content,)
                            )
                continue

            if not operations:
                operations.append(Operation(type="NOOP"))

            surviving_prior_claims = tuple(
                dict.fromkeys(
                    claim
                    for rec in before_records
                    for claim in rec.claims
                    if claim not in retired_claims
                )
            )
            transition = Transition(
                case_id=case_id,
                before=before_records,
                candidate=tuple(working.values()),
                evidence=tuple(evidence_list),
                required_claims=surviving_prior_claims,
                operations=tuple(operations),
                schema_version=SCHEMA_VERSION,
            )
            transition.validate()
            accumulated = working
            grounding_label = (
                "explicit_turn_ref"
                if methods_seen == {"explicit_turn_ref"}
                else "lexical_match"
            )
            cases.append(
                HaluMemTransitionCase(
                    case_id=case_id,
                    user_uuid=user.uuid,
                    session_id=session.session_id,
                    session_index=s_idx,
                    transition=transition,
                    transcript=transcript,
                    grounding_method=grounding_label,
                    update_operation_mode=op_mode,
                )
            )

    return HaluMemBuildReport(
        dataset_meta=dataset_meta,
        update_operation_mode=op_mode,
        cases=tuple(cases),
        ungroundable=tuple(ungroundable),
        assumptions=(LEXICAL_EVIDENCE_ASSUMPTION, ORIGINAL_RESOLUTION_ASSUMPTION),
    )


# ---------------------------------------------------------------------------
# Seeded fault injector (Phase 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InjectedEvalCase:
    """One Phase 1 evaluation transition (valid gold or seeded faulty variant)."""

    case_id: str
    base_case_id: str
    user_uuid: str
    session_id: str
    transition: Transition
    transcript: ConversationTranscript
    expected_decision: str  # "ACCEPT" | "HOLD"
    fault_type: str | None  # None for valid, or one of FAULT_TYPES
    fault_variant: str  # "valid" | "omission_prior_overwrite" | "corruption_unbacked" | "corruption_contradicted" | "unsupported_unbacked" | "unsupported_spoofed_evidence"
    valid_prior_records_expected_to_persist: tuple[str, ...]
    corrupted_prior_record_ids: tuple[str, ...]
    omitted_required_claims: tuple[str, ...]
    unsupported_added_claims: tuple[str, ...]
    seed: int
    label_source: str = "seeded_fault_injection"
    circularity_limitation: str = INJECTOR_CIRCULARITY_LIMITATION

    def label_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "base_case_id": self.base_case_id,
            "user_uuid": self.user_uuid,
            "session_id": self.session_id,
            "expected_decision": self.expected_decision,
            "expected_invalid": False,
            "category": self.fault_type or "valid",
            "fault_type": self.fault_type,
            "fault_types": [self.fault_type] if self.fault_type else [],
            "fault_variant": self.fault_variant,
            "valid_prior_records_expected_to_persist": list(
                self.valid_prior_records_expected_to_persist
            ),
            "corrupted_prior_record_ids": list(self.corrupted_prior_record_ids),
            "omitted_required_claims": list(self.omitted_required_claims),
            "unsupported_added_claims": list(self.unsupported_added_claims),
            "seed": self.seed,
            "label_source": self.label_source,
            "circularity_limitation": self.circularity_limitation,
        }


def _subseed(seed: int, key: str) -> int:
    digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _corrupt_claim_text(claim: str, rng: random.Random, forbidden_tokens: set[str]) -> str:
    """Deterministically perturb a prior claim into an unsupported corrupted claim."""
    for old_word, new_word in _CORRUPTION_REPLACEMENTS:
        pattern = re.compile(rf"\b{re.escape(old_word)}\b", re.IGNORECASE)
        if pattern.search(claim):
            candidate = pattern.sub(new_word.strip(), claim, count=1)
            if candidate != claim and not (_content_tokens(candidate) <= forbidden_tokens):
                return candidate
    suffixes = (
        "in Reykjavik since 1994",
        "at Tyrell Corporation headquarters",
        "with a fleet of seventeen helicopters",
        "exclusively on alternate leap years in Anchorage",
    )
    chosen = suffixes[rng.randrange(len(suffixes))]
    return f"{claim.rstrip('.')} ({chosen})"


def _pick_hallucinated_claim(
    rng: random.Random,
    existing_claims: set[str],
    transcript_tokens: set[str],
) -> str:
    indices = list(range(len(_HALLUCINATION_POOL)))
    rng.shuffle(indices)
    for idx in indices:
        candidate = _HALLUCINATION_POOL[idx]
        if candidate not in existing_claims and not (
            _content_tokens(candidate) & transcript_tokens
        ):
            return candidate
    return "Operates a fictional orbital observatory on Phobos"


def _surviving_prior_records(t: Transition) -> list[Record]:
    """Prior records in ``t.before`` that are not targeted by any operation in ``t``."""
    targeted = {op.target_id for op in t.operations if op.target_id}
    return [rec for rec in t.before if rec.id not in targeted]


def _inject_omission(
    base: HaluMemTransitionCase, seed: int
) -> InjectedEvalCase | None:
    """Construct a schema-v2 valid transition that omits a required prior claim/record.

    Victims are tried in a seeded order; a variant is kept only if its own
    post-condition holds (every claimed-omitted claim is truly absent from the
    candidate), so the injected label is true by construction.
    """
    t = base.transition
    surviving = [
        rec
        for rec in _surviving_prior_records(t)
        if any(c in set(t.required_claims) for c in rec.claims)
    ]
    if not surviving:
        return None
    rng = random.Random(_subseed(seed, f"{t.case_id}|omission"))
    order = list(surviving)
    rng.shuffle(order)
    for victim in order:
        injected = _inject_omission_for_victim(base, seed, victim)
        if injected is not None:
            return injected
    return None


def _inject_omission_for_victim(
    base: HaluMemTransitionCase, seed: int, victim: Record
) -> InjectedEvalCase | None:
    t = base.transition
    omitted_claims = tuple(c for c in victim.claims if c in set(t.required_claims))

    # To keep the transition schema-v2 valid AND pass preservation + faithfulness +
    # transcript grounding while failing ONLY coverage (REQUIRED_CLAIM_OMITTED),
    # we redirect an evidence-supported claim from this session onto victim (via REVISE)
    # so victim's prior claim disappears from candidate while all added claims are
    # evidence-backed.
    write_ops = [op for op in t.operations if op.type == "WRITE" and op.record_id]
    revise_ops = [
        op
        for op in t.operations
        if op.type in {"REVISE", "SUPERSEDE"} and op.record_id
    ]
    cand_by_id = {rec.id: rec for rec in t.candidate}

    if write_ops:
        donor_op = write_ops[0]
        donor_rec = cand_by_id[donor_op.record_id]  # type: ignore[index]
        new_candidate: list[Record] = []
        for rec in t.candidate:
            if rec.id == donor_rec.id:
                continue
            if rec.id == victim.id:
                new_candidate.append(Record(id=victim.id, claims=donor_rec.claims))
            else:
                new_candidate.append(rec)
        new_ops: list[Operation] = []
        for op in t.operations:
            if op == donor_op:
                new_ops.append(
                    Operation(
                        type="REVISE",
                        record_id=victim.id,
                        target_id=victim.id,
                        reason="injected_omission_overwrote_prior_record",
                    )
                )
            else:
                new_ops.append(op)
        faulty_t = Transition(
            case_id=f"{t.case_id}__fault_omission",
            before=t.before,
            candidate=tuple(new_candidate),
            evidence=t.evidence,
            required_claims=t.required_claims,
            operations=tuple(new_ops),
            schema_version=SCHEMA_VERSION,
        )
    elif revise_ops:
        donor_op = revise_ops[0]
        donor_rec = cand_by_id[donor_op.record_id]  # type: ignore[index]
        new_candidate = [
            Record(id=victim.id, claims=donor_rec.claims)
            if rec.id == victim.id
            else rec
            for rec in t.candidate
        ]
        new_ops = [
            *t.operations,
            Operation(
                type="REVISE",
                record_id=victim.id,
                target_id=victim.id,
                reason="injected_omission_overwrote_prior_record",
            ),
        ]
        faulty_t = Transition(
            case_id=f"{t.case_id}__fault_omission",
            before=t.before,
            candidate=tuple(new_candidate),
            evidence=t.evidence,
            required_claims=t.required_claims,
            operations=tuple(new_ops),
            schema_version=SCHEMA_VERSION,
        )
    else:
        return None

    faulty_t.validate()
    faulty_claims = {c for rec in faulty_t.candidate for c in rec.claims}
    omitted_claims = tuple(c for c in omitted_claims if c not in faulty_claims)
    if not omitted_claims:
        return None
    surviving_ids = tuple(r.id for r in _surviving_prior_records(t))
    return InjectedEvalCase(
        case_id=faulty_t.case_id,
        base_case_id=t.case_id,
        user_uuid=base.user_uuid,
        session_id=base.session_id,
        transition=faulty_t,
        transcript=base.transcript,
        expected_decision="HOLD",
        fault_type="omission",
        fault_variant="omission_prior_overwrite",
        valid_prior_records_expected_to_persist=surviving_ids,
        corrupted_prior_record_ids=(),
        omitted_required_claims=omitted_claims,
        unsupported_added_claims=(),
        seed=seed,
    )


def _inject_corruption(
    base: HaluMemTransitionCase, seed: int
) -> list[InjectedEvalCase]:
    """Construct schema-v2 valid transitions that corrupt a valid prior record."""
    t = base.transition
    results: list[InjectedEvalCase] = []
    surviving = _surviving_prior_records(t)
    surviving_ids = tuple(r.id for r in surviving)

    # Variant A: un-evidenced mutation of a surviving prior record.
    if surviving:
        rng = random.Random(_subseed(seed, f"{t.case_id}|corruption"))
        victim = surviving[rng.randrange(len(surviving))]
        transcript_tokens = {
            tok for turn in base.transcript.turns for tok in _content_tokens(turn.content)
        }
        corrupted_claim = _corrupt_claim_text(
            victim.claims[0], rng, transcript_tokens
        )
        new_candidate = tuple(
            Record(id=victim.id, claims=(corrupted_claim,))
            if rec.id == victim.id
            else rec
            for rec in t.candidate
        )
        base_ops = [op for op in t.operations if op.type != "NOOP"]
        new_ops = (
            *base_ops,
            Operation(
                type="REVISE",
                record_id=victim.id,
                target_id=victim.id,
                reason="injected_prior_record_corruption",
            ),
        )
        # Keep required_claims satisfied or focused on non-victim records so
        # preservation (PRIOR_RECORD_UNAUTHORIZED_CHANGE) is directly tested.
        faulty_t = Transition(
            case_id=f"{t.case_id}__fault_corruption",
            before=t.before,
            candidate=new_candidate,
            evidence=t.evidence,
            required_claims=t.required_claims,
            operations=new_ops,
            schema_version=SCHEMA_VERSION,
        )
        faulty_t.validate()
        results.append(
            InjectedEvalCase(
                case_id=faulty_t.case_id,
                base_case_id=t.case_id,
                user_uuid=base.user_uuid,
                session_id=base.session_id,
                transition=faulty_t,
                transcript=base.transcript,
                expected_decision="HOLD",
                fault_type="corruption",
                fault_variant="corruption_unbacked",
                valid_prior_records_expected_to_persist=surviving_ids,
                corrupted_prior_record_ids=(victim.id,),
                omitted_required_claims=tuple(
                    c for c in victim.claims if c in set(t.required_claims)
                ),
                unsupported_added_claims=(corrupted_claim,),
                seed=seed,
            )
        )

    # Variant B: when the transition carries grounded contradiction evidence
    # (from an is_update session where the turn quote contradicts the old prior claim),
    # corrupt the updated prior record by retaining the contradicted prior claim
    # alongside the new claim. Coverage and preservation pass; plain `text` false-accepts
    # because it ignores `evidence.contradicts`, while `graph` and `text_fair` both HOLD
    # with `CONTRADICTED_CLAIM`.
    contradicted_in_ev = {
        c for ev in t.evidence for c in ev.contradicts
    }
    if contradicted_in_ev:
        before_by_id = {r.id: r for r in t.before}
        for op in t.operations:
            if op.type in {"REVISE", "SUPERSEDE"} and op.target_id in before_by_id and op.record_id:
                prior_rec = before_by_id[op.target_id]
                matching_old = [c for c in prior_rec.claims if c in contradicted_in_ev]
                if not matching_old:
                    continue
                new_candidate_b = tuple(
                    Record(
                        id=rec.id,
                        claims=tuple(dict.fromkeys([*matching_old, *rec.claims])),
                    )
                    if rec.id == op.record_id
                    else rec
                    for rec in t.candidate
                )
                faulty_b = Transition(
                    case_id=f"{t.case_id}__fault_corruption_contradicted",
                    before=t.before,
                    candidate=new_candidate_b,
                    evidence=t.evidence,
                    required_claims=t.required_claims,
                    operations=t.operations,
                    schema_version=SCHEMA_VERSION,
                )
                faulty_b.validate()
                results.append(
                    InjectedEvalCase(
                        case_id=faulty_b.case_id,
                        base_case_id=t.case_id,
                        user_uuid=base.user_uuid,
                        session_id=base.session_id,
                        transition=faulty_b,
                        transcript=base.transcript,
                        expected_decision="HOLD",
                        fault_type="corruption",
                        fault_variant="corruption_contradicted",
                        valid_prior_records_expected_to_persist=(prior_rec.id, *surviving_ids),
                        corrupted_prior_record_ids=(prior_rec.id,),
                        omitted_required_claims=(),
                        unsupported_added_claims=tuple(matching_old),
                        seed=seed,
                    )
                )
                break

    return results


def _inject_unsupported_addition(
    base: HaluMemTransitionCase, seed: int, *, index: int
) -> InjectedEvalCase:
    """Construct a schema-v2 valid transition that adds an unsupported claim."""
    t = base.transition
    rng = random.Random(_subseed(seed, f"{t.case_id}|unsupported_addition"))
    existing_claims = {
        c for rec in (*t.before, *t.candidate) for c in rec.claims
    }
    transcript_tokens = {
        tok for turn in base.transcript.turns for tok in _content_tokens(turn.content)
    }
    hallucinated = _pick_hallucinated_claim(rng, existing_claims, transcript_tokens)
    halluc_id = f"r_halluc_{base.session_id}"
    used_ids = {r.id for r in (*t.before, *t.candidate)}
    suffix = 1
    while halluc_id in used_ids:
        suffix += 1
        halluc_id = f"r_halluc_{base.session_id}_{suffix}"

    new_candidate = (*t.candidate, Record(id=halluc_id, claims=(hallucinated,)))
    base_ops = [op for op in t.operations if op.type != "NOOP"]
    new_ops = (*base_ops, Operation(type="WRITE", record_id=halluc_id))

    # Alternate deterministically between unbacked addition (no evidence entry claims
    # support) and spoofed-evidence addition (caller-declared evidence falsely claims
    # a user turn supports the hallucinated claim, testing transcript_grounding).
    use_spoofed = (index % 2 == 1) and bool(base.transcript.user_turns())
    if use_spoofed:
        user_turn = base.transcript.user_turns()[0]
        spoofed_ev = Evidence(
            id=f"e_spoof_{ len(t.evidence) + 1 }",
            supports=(hallucinated,),
            source=user_turn.source_ref,
            text=user_turn.content,
            contradicts=(),
        )
        new_evidence = (*t.evidence, spoofed_ev)
        variant = "unsupported_spoofed_evidence"
    else:
        new_evidence = t.evidence
        variant = "unsupported_unbacked"

    faulty_t = Transition(
        case_id=f"{t.case_id}__fault_unsupported",
        before=t.before,
        candidate=new_candidate,
        evidence=new_evidence,
        required_claims=t.required_claims,
        operations=new_ops,
        schema_version=SCHEMA_VERSION,
    )
    faulty_t.validate()
    surviving_ids = tuple(r.id for r in _surviving_prior_records(t))
    return InjectedEvalCase(
        case_id=faulty_t.case_id,
        base_case_id=t.case_id,
        user_uuid=base.user_uuid,
        session_id=base.session_id,
        transition=faulty_t,
        transcript=base.transcript,
        expected_decision="HOLD",
        fault_type="unsupported_addition",
        fault_variant=variant,
        valid_prior_records_expected_to_persist=surviving_ids,
        corrupted_prior_record_ids=(),
        omitted_required_claims=(),
        unsupported_added_claims=(hallucinated,),
        seed=seed,
    )


def inject_halumem_faults(
    base_cases: Sequence[HaluMemTransitionCase],
    *,
    seed: int = 20261121,
) -> tuple[InjectedEvalCase, ...]:
    """Deterministically generate valid + 3-type faulty evaluation cases."""
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")

    suite: list[InjectedEvalCase] = []
    for idx, base in enumerate(base_cases):
        t = base.transition
        surviving_ids = tuple(r.id for r in _surviving_prior_records(t))
        # 1. Valid gold transition
        suite.append(
            InjectedEvalCase(
                case_id=t.case_id,
                base_case_id=t.case_id,
                user_uuid=base.user_uuid,
                session_id=base.session_id,
                transition=t,
                transcript=base.transcript,
                expected_decision="ACCEPT",
                fault_type=None,
                fault_variant="valid",
                valid_prior_records_expected_to_persist=surviving_ids,
                corrupted_prior_record_ids=(),
                omitted_required_claims=(),
                unsupported_added_claims=(),
                seed=seed,
            )
        )
        # 2. Omission of a required prior claim/record (when prior state exists)
        omission_case = _inject_omission(base, seed)
        if omission_case is not None:
            suite.append(omission_case)
        # 3. Corruption of a valid prior record (when prior state exists)
        for corruption_case in _inject_corruption(base, seed):
            suite.append(corruption_case)
        # 4. Unsupported addition
        suite.append(_inject_unsupported_addition(base, seed, index=idx))

    return tuple(suite)


# ---------------------------------------------------------------------------
# Synthetic HaluMem-shaped fixture (for offline Phase 1 and tests)
# ---------------------------------------------------------------------------


def synthetic_halumem_records() -> list[dict[str, Any]]:
    """Return a 6-user, 18-session synthetic HaluMem-shaped dataset.

    Contains zero real HaluMem text. Exercises:
    - Session 1: initial WRITE memory points;
    - Session 2: ``is_update="True"`` / ``True`` with ``original_memories`` (including
      explicit contrast turns that lexically ground ``contradicts``) plus new facts;
    - Session 3: further updates/additions, plus one intentionally ungroundable
      memory point on user ``syn-user-6`` session ``s3`` to exercise the
      transition builder's ungroundable-case detection and reporting.
    """
    specs = [
        {
            "uuid": "syn-user-1",
            "persona": "Synthetic software engineer persona in Bandung/Surabaya.",
            "s1_turns": [
                ("user", "Hi, my name is Nadia and I currently live in Bandung."),
                ("assistant", "Nice to meet you, Nadia! How is work going?"),
                ("user", "I work as a backend engineer at Tokopedia and I am allergic to shrimp."),
            ],
            "s1_points": [
                ("Lives in Bandung", "persona", "user", False, []),
                ("Works as a backend engineer at Tokopedia", "persona", "user", False, []),
                ("Allergic to shrimp", "health", "user", False, []),
            ],
            "s1_q": ("Where does Nadia live?", "Bandung"),
            "s2_turns": [
                ("user", "Quick update: I relocated last month, so I now live in Surabaya instead of Bandung."),
                ("assistant", "Congrats on the move to Surabaya!"),
                ("user", "I also started learning classical guitar on weekends."),
            ],
            "s2_points": [
                ("Lives in Surabaya", "persona", "user", True, ["Lives in Bandung"]),
                ("Learning classical guitar on weekends", "hobby", "user", False, []),
            ],
            "s2_q": ("Where does Nadia live now?", "Surabaya"),
            "s3_turns": [
                ("user", "I switched companies this week: I now work as a backend engineer at Gojek, not Tokopedia."),
                ("assistant", "Best of luck at Gojek!"),
                ("user", "I adopted a rescue cat named Mochi yesterday."),
            ],
            "s3_points": [
                (
                    "Works as a backend engineer at Gojek",
                    "persona",
                    "user",
                    True,
                    ["Works as a backend engineer at Tokopedia"],
                ),
                ("Adopted a rescue cat named Mochi", "event", "user", False, []),
            ],
            "s3_q": ("Which company does Nadia work for now?", "Gojek"),
        },
        {
            "uuid": "syn-user-2",
            "persona": "Synthetic data analyst persona in Jakarta/Yogyakarta.",
            "s1_turns": [
                ("user", "Hello! I live in Jakarta and I work as a data analyst at Traveloka."),
                ("assistant", "Welcome! Do you have any dietary preferences?"),
                ("user", "Yes, I follow a vegetarian diet."),
            ],
            "s1_points": [
                ("Lives in Jakarta", "persona", "user", "False", []),
                ("Works as a data analyst at Traveloka", "persona", "user", "False", []),
                ("Follows a vegetarian diet", "preference", "user", "False", []),
            ],
            "s1_q": ("What diet does the user follow?", "Vegetarian"),
            "s2_turns": [
                ("user", "I moved from Jakarta and now live in Yogyakarta."),
                ("assistant", "Yogyakarta is wonderful."),
                ("user", "I also joined a morning running club near Malioboro."),
            ],
            "s2_points": [
                ("Lives in Yogyakarta", "persona", "user", "True", ["Lives in Jakarta"]),
                ("Joined a morning running club", "hobby", "user", "False", []),
            ],
            "s2_q": ("Which city did the user move to?", "Yogyakarta"),
            "s3_turns": [
                ("user", "My doctor advised changes, so I now follow a vegan diet instead of a vegetarian diet."),
                ("assistant", "Got it, vegan diet noted."),
                ("user", "I am preparing for a half marathon in November."),
            ],
            "s3_points": [
                ("Follows a vegan diet", "preference", "user", "True", ["Follows a vegetarian diet"]),
                ("Preparing for a half marathon in November", "event", "user", "False", []),
            ],
            "s3_q": ("What diet does the user follow now?", "Vegan"),
        },
        {
            "uuid": "syn-user-3",
            "persona": "Synthetic teacher persona in Malang/Semarang.",
            "s1_turns": [
                ("user", "Good morning! I live in Malang and teach high school physics."),
                ("assistant", "Good morning! Do you commute by car?"),
                ("user", "I commute using a green commuter bicycle every day."),
            ],
            "s1_points": [
                ("Lives in Malang", "persona", "user", False, []),
                ("Teaches high school physics", "persona", "user", False, []),
                ("Commutes using a green commuter bicycle", "preference", "user", False, []),
            ],
            "s1_q": ("What subject does the user teach?", "High school physics"),
            "s2_turns": [
                ("user", "I transferred schools and now live in Semarang instead of Malang."),
                ("assistant", "Hope Semarang treats you well!"),
                ("user", "I started coaching the school robotics team after class."),
            ],
            "s2_points": [
                ("Lives in Semarang", "persona", "user", True, ["Lives in Malang"]),
                ("Coaches the school robotics team", "event", "user", False, []),
            ],
            "s2_q": ("Where does the user live after transferring?", "Semarang"),
            "s3_turns": [
                ("user", "I sold my green commuter bicycle and now commute using an electric scooter."),
                ("assistant", "An electric scooter sounds convenient."),
                ("user", "I also drink black coffee every morning."),
            ],
            "s3_points": [
                (
                    "Commutes using an electric scooter",
                    "preference",
                    "user",
                    True,
                    ["Commutes using a green commuter bicycle"],
                ),
                ("Drinks black coffee every morning", "preference", "user", False, []),
            ],
            "s3_q": ("How does the user commute now?", "Electric scooter"),
        },
        {
            "uuid": "syn-user-4",
            "persona": "Synthetic systems programmer persona in Seattle/Austin.",
            "s1_turns": [
                ("user", "Hey there, I live in Seattle and write distributed storage services in Python."),
                ("assistant", "Nice! Any hobbies outside coding?"),
                ("user", "I play jazz piano at a neighborhood cafe on Fridays."),
            ],
            "s1_points": [
                ("Lives in Seattle", "persona", "user", False, []),
                ("Writes distributed storage services in Python", "persona", "user", False, []),
                ("Plays jazz piano on Fridays", "hobby", "user", False, []),
            ],
            "s1_q": ("What instrument does the user play?", "Jazz piano"),
            "s2_turns": [
                ("user", "Our team migrated our codebase, so I now write distributed storage services in Rust instead of Python."),
                ("assistant", "Rust is great for storage engines."),
                ("user", "I also started bouldering twice a week."),
            ],
            "s2_points": [
                (
                    "Writes distributed storage services in Rust",
                    "persona",
                    "user",
                    True,
                    ["Writes distributed storage services in Python"],
                ),
                ("Started bouldering twice a week", "hobby", "user", False, []),
            ],
            "s2_q": ("Which programming language does the user use now?", "Rust"),
            "s3_turns": [
                ("user", "I relocated from Seattle and now live in Austin."),
                ("assistant", "Welcome to Austin!"),
                ("user", "I volunteered at a local bookshop on Sunday."),
            ],
            "s3_points": [
                ("Lives in Austin", "persona", "user", True, ["Lives in Seattle"]),
                ("Volunteered at a local bookshop", "event", "user", False, []),
            ],
            "s3_q": ("Where does the user live now?", "Austin"),
        },
        {
            "uuid": "syn-user-5",
            "persona": "Synthetic marine biologist persona in Berlin.",
            "s1_turns": [
                ("user", "Hello, I live in Berlin and research coral reef acoustics."),
                ("assistant", "Fascinating field! Any allergies or medical notes?"),
                ("user", "I have a severe peanut allergy."),
            ],
            "s1_points": [
                ("Lives in Berlin", "persona", "user", False, []),
                ("Researches coral reef acoustics", "persona", "user", False, []),
                ("Has a severe peanut allergy", "health", "user", False, []),
            ],
            "s1_q": ("What allergy does the user have?", "Severe peanut allergy"),
            "s2_turns": [
                ("user", "My lab focus shifted: I now research Arctic whale bioacoustics instead of coral reef acoustics."),
                ("assistant", "Arctic whale bioacoustics sounds incredible."),
                ("user", "I also practice road cycling on weekends."),
            ],
            "s2_points": [
                (
                    "Researches Arctic whale bioacoustics",
                    "persona",
                    "user",
                    True,
                    ["Researches coral reef acoustics"],
                ),
                ("Practices road cycling on weekends", "hobby", "user", False, []),
            ],
            "s2_q": ("What does the user research now?", "Arctic whale bioacoustics"),
            "s3_turns": [
                ("user", "I started learning Norwegian for our upcoming field expedition."),
                ("assistant", "Lykke til with Norwegian!"),
            ],
            "s3_points": [
                ("Learning Norwegian for field expedition", "hobby", "user", False, []),
            ],
            "s3_q": ("Which language is the user learning?", "Norwegian"),
        },
        {
            "uuid": "syn-user-6",
            "persona": "Synthetic architect persona with an ungroundable annotation in s3.",
            "s1_turns": [
                ("user", "Hi! I live in Bandung and design sustainable bamboo housing."),
                ("assistant", "That is great sustainable work."),
                ("user", "I drink matcha tea every afternoon."),
            ],
            "s1_points": [
                ("Lives in Bandung", "persona", "user", False, []),
                ("Designs sustainable bamboo housing", "persona", "user", False, []),
                ("Drinks matcha tea every afternoon", "preference", "user", False, []),
            ],
            "s1_q": ("What does the user design?", "Sustainable bamboo housing"),
            "s2_turns": [
                ("user", "I moved my studio and now live in Surabaya instead of Bandung."),
                ("assistant", "How is the new studio in Surabaya?"),
                ("user", "It has great natural light and I sketch urban parks on Saturdays."),
            ],
            "s2_points": [
                ("Lives in Surabaya", "persona", "user", True, ["Lives in Bandung"]),
                ("Sketches urban parks on Saturdays", "hobby", "user", False, []),
            ],
            "s2_q": ("Where is the user's new studio?", "Surabaya"),
            "s3_turns": [
                ("user", "Today we only talked about the weather and traffic."),
                ("assistant", "Indeed, heavy rain all afternoon."),
            ],
            # Intentionally ungroundable memory point: zero lexical overlap with s3 user turn.
            "s3_points": [
                ("Published a monograph on subterranean lunar habitats", "event", "user", False, []),
            ],
            "s3_q": ("What did they discuss in session 3?", "Weather and traffic"),
        },
    ]

    users: list[dict[str, Any]] = []
    for spec in specs:
        uid = spec["uuid"]
        sessions: list[dict[str, Any]] = []
        for s_num in (1, 2, 3):
            sid = f"s{s_num}"
            ts_base = f"2026-03-0{s_num}T10:00:00Z"
            turns_raw = spec[f"s{s_num}_turns"]
            dialogue = [
                {
                    "turn_id": f"{sid}_t{t_idx}",
                    "role": role,
                    "content": text,
                    "timestamp": ts_base,
                }
                for t_idx, (role, text) in enumerate(turns_raw, start=1)
            ]
            points_raw = spec[f"s{s_num}_points"]
            memory_points = [
                {
                    "id": f"{sid}_m{m_idx}",
                    "memory_content": content,
                    "memory_type": m_type,
                    "memory_source": m_source,
                    "is_update": is_upd,
                    "original_memories": orig,
                    "timestamp": ts_base,
                    "importance": 1,
                }
                for m_idx, (content, m_type, m_source, is_upd, orig) in enumerate(
                    points_raw, start=1
                )
            ]
            q_text, a_text = spec[f"s{s_num}_q"]
            questions = [
                {
                    "question_id": f"{sid}_q1",
                    "question": q_text,
                    "answer": a_text,
                    "question_type": "fact",
                    "timestamp": ts_base,
                }
            ]
            sessions.append(
                {
                    "session_id": sid,
                    "timestamp": ts_base,
                    "dialogue": dialogue,
                    "memory_points": memory_points,
                    "questions": questions,
                }
            )
        users.append(
            {
                "uuid": uid,
                "persona_info": spec["persona"],
                "sessions": sessions,
            }
        )
    return users


def write_synthetic_halumem_fixture(path: str | Path) -> Path:
    """Write the synthetic HaluMem-shaped JSONL fixture to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(item, ensure_ascii=False, sort_keys=True)
        for item in synthetic_halumem_records()
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Phase 2: Deterministic & LLM-assisted Silver Labeler for Natural Proposals
# ---------------------------------------------------------------------------

SILVER_CATEGORIES: tuple[str, ...] = (
    "valid",
    "omission",
    "corruption",
    "unsupported_addition",
)
CLAIM_MATCH_JUDGE_PROMPT_VERSION = "membud-halumem-claim-match-v1"


@dataclass(frozen=True)
class SilverLabelResult:
    """Provisional silver annotation for one recorded real-Mem0 session proposal."""

    case_id: str
    user_uuid: str
    session_id: str
    primary_label: str  # "valid" | "omission" | "corruption" | "unsupported_addition"
    fault_types: tuple[str, ...]  # subset of ("omission", "corruption", "unsupported_addition")
    expected_decision: str  # "ACCEPT" if primary_label == "valid" else "HOLD"
    matched_gold_points: tuple[str, ...]
    omitted_gold_points: tuple[str, ...]
    omitted_prior_claims: tuple[str, ...]
    corrupted_prior_record_ids: tuple[str, ...]
    unsupported_added_claims: tuple[str, ...]
    paraphrased_valid_claims_ungrounded_by_lexical: tuple[str, ...]
    judge_method: str  # "deterministic" | "deterministic+llm_claim_judge"
    judge_calls: int = 0
    label_status: str = "provisional_silver_unvalidated"
    is_ground_truth: bool = False
    limitation: str = SILVER_LABEL_LIMITATION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_quiz_label_row(self, *, split: str = "heldout") -> dict[str, Any]:
        """Format as a ``scripts/review_quiz.py``-compatible label JSONL row."""
        return {
            "case_id": self.case_id,
            "split": split,
            "category": self.primary_label,
            "expected_decision": self.expected_decision,
            "expected_invalid": False,
            "fault_types": list(self.fault_types),
            "valid_prior_records_expected_to_persist": [],
            "corrupted_prior_record_ids": list(self.corrupted_prior_record_ids),
            "label_status": self.label_status,
            "notes": (
                f"Provisional silver label ({self.judge_method}): "
                f"primary={self.primary_label}, faults={list(self.fault_types)}"
            ),
        }


def _claim_token_similarity(proposed: str, reference: str) -> tuple[float, float, float]:
    """Return ``(precision, recall, jaccard)`` over content tokens."""
    p_norm = _normalize_ws(proposed).lower()
    r_norm = _normalize_ws(reference).lower()
    if p_norm and r_norm and (p_norm == r_norm or p_norm in r_norm or r_norm in p_norm):
        return 1.0, 1.0, 1.0
    p_tokens = _content_tokens(proposed)
    r_tokens = _content_tokens(reference)
    if not p_tokens or not r_tokens:
        return 0.0, 0.0, 0.0
    inter = len(p_tokens & r_tokens)
    union = len(p_tokens | r_tokens)
    return inter / len(p_tokens), inter / len(r_tokens), inter / union


class LLMClaimMatchJudge:
    """Cached JSON-mode LLM judge used ONLY for ambiguous claim-to-gold matching."""

    def __init__(
        self,
        client: Any,
        *,
        log_path: str | Path | None = None,
        prompt_version: str = CLAIM_MATCH_JUDGE_PROMPT_VERSION,
    ) -> None:
        self.client = client
        self.log_path = Path(log_path) if log_path is not None else None
        self.prompt_version = prompt_version
        self.calls = 0
        self.cache_hits = 0
        self.total_tokens = 0

    def matches(self, proposed_claim: str, gold_claim: str) -> bool:
        messages = [
            {
                "role": "system",
                "content": (
                    "You compare a proposed memory claim against a reference gold memory point. "
                    "Return ONLY a JSON object with keys 'equivalent' (boolean) and 'reason' (non-empty string). "
                    "Set 'equivalent' to true if and only if the proposed claim states the same core fact "
                    "as the gold memory point without contradicting any entity, location, role, or number."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "prompt_version": self.prompt_version,
                        "proposed_claim": proposed_claim,
                        "gold_memory_point": gold_claim,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]

        def _validate(payload: dict[str, Any]) -> None:
            if not isinstance(payload.get("equivalent"), bool):
                raise ValueError("'equivalent' must be a boolean")
            if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
                raise ValueError("'reason' must be a non-empty string")

        completion = self.client.complete_json(messages, validator=_validate)
        self.calls += 1
        if completion.cached:
            self.cache_hits += 1
        if isinstance(completion.total_tokens, int):
            self.total_tokens += completion.total_tokens
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "prompt_version": self.prompt_version,
                "model": completion.model,
                "cached": completion.cached,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "total_tokens": completion.total_tokens,
                "equivalent": completion.json["equivalent"],
                "reason": completion.json["reason"],
            }
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return bool(completion.json["equivalent"])


_PERSONA_NAME_RE = re.compile(r"(?:^|[;\]]\s*)Name:\s*([^;]+);")


def persona_name_from_info(persona_info: str) -> str | None:
    """Extract the ``Name: ...;`` field of a HaluMem ``persona_info`` string, if any."""
    match = _PERSONA_NAME_RE.search(persona_info)
    return match.group(1).strip() if match and match.group(1).strip() else None


def ground_transition(
    transition: Transition,
    transcript: ConversationTranscript,
    grounder: EvidenceGrounder,
) -> tuple[Transition, GroundingReport]:
    """Attach evidence from ``grounder`` to a natural proposal (verifier unchanged).

    Only claims added by the transition and not already supported are grounded;
    existing evidence is kept. Returns the new transition and the report that
    says, per claim, why it was or was not grounded.
    """
    before_claims = {c for r in transition.before for c in r.claims}
    added = [c for r in transition.candidate for c in r.claims if c not in before_claims]
    supported = frozenset(c for ev in transition.evidence for c in ev.supports)
    sources = [GroundingSource(t.source_ref, t.content) for t in transcript.user_turns()]
    report = grounder.ground(added, sources, already_supported=supported)
    grounded = Transition(
        case_id=transition.case_id,
        before=transition.before,
        candidate=transition.candidate,
        evidence=(*transition.evidence, *report.evidence),
        required_claims=transition.required_claims,
        operations=transition.operations,
        schema_version=transition.schema_version,
    )
    return grounded, report


def rederive_evidence_normalized(
    transition: Transition,
    transcript: ConversationTranscript,
    *,
    min_coverage: float,
    persona_name: str | None = None,
    fold_inflection: bool = False,
    normalize_numerals: bool = False,
) -> Transition:
    """Host-side deterministic evidence policy for natural proposals (verifier unchanged).

    Thin wrapper over :class:`~membud.grounding.CoverageGrounder` (kept for the
    pre-registered analysis). For each added, not-yet-supported claim it picks the
    single user turn with the highest content-token coverage (earliest on ties) and
    attaches verbatim-quote evidence if coverage >= ``min_coverage`` and every
    numeral of the claim occurs in that turn. Normalization N is opt-in (see
    docs/halumem-evaluation.md). Unsupported claims are never invented.
    """
    grounder = CoverageGrounder(
        min_coverage=min_coverage,
        persona_name=persona_name,
        fold_inflection=fold_inflection,
        normalize_numerals=normalize_numerals,
    )
    return ground_transition(transition, transcript, grounder)[0]


def derive_silver_label(
    transition: Transition,
    session: HaluMemSession,
    *,
    user_uuid: str,
    llm_judge: LLMClaimMatchJudge | None = None,
    omission_scope: str = "session_and_prior",
    normalize_numerals: bool = False,
) -> SilverLabelResult:
    """Derive a provisional silver label for a natural Mem0 proposal transition.

    Deterministic rules run first:
    1. Match each candidate claim against the session's gold ``memory_points``
       by exact/substring match or content-token precision/recall >= 0.5 (with
       no conflicting numerals). If overlap is partial (0.2 <= jaccard < 0.5)
       and ``llm_judge`` is provided, consult the cached LLM claim matcher.
    2. Check whether any gold ``memory_point`` in ``session`` (or any surviving
       prior claim from ``transition.before`` that was not superseded by an
       ``is_update`` gold point) is missing from ``transition.candidate`` ->
       ``omission``.
    3. Check whether any surviving prior record in ``transition.before`` was
       improperly removed/altered to a claim not matching any gold update, OR
       whether an ``is_update=True`` gold point's ``original_memories`` was
       retained in ``transition.candidate`` alongside a conflicting update
       (stale/contradicted prior record) -> ``corruption``.
    4. Check whether any newly added/updated claim in ``transition.candidate``
       matches neither any gold ``memory_point`` nor the session's user-turn
       content tokens -> ``unsupported_addition``.
    """
    if omission_scope not in OMISSION_SCOPES:
        raise ValueError(f"omission_scope must be one of {OMISSION_SCOPES}")
    before_by_id = {r.id: r for r in transition.before}
    candidate_by_id = {r.id: r for r in transition.candidate}
    before_claims = {c for r in transition.before for c in r.claims}
    candidate_claims = {c for r in transition.candidate for c in r.claims}
    added_claims = sorted(candidate_claims - before_claims)
    evidence_supported = {c for ev in transition.evidence for c in ev.supports}

    user_text = " ".join(
        t.content for t in session.dialogue if t.role == "user"
    )
    user_tokens = _content_tokens(user_text)
    user_numbers = _numbers_in_text(user_text, normalize=normalize_numerals)

    gold_points = session.gold_points
    interference_points = session.interference_points
    gold_retired_claims = {
        orig for p in gold_points if p.is_update for orig in p.original_memories
    }
    judge_calls_before = llm_judge.calls if llm_judge is not None else 0
    used_llm_judge = False

    def _matches_gold(claim: str, gold_text: str) -> bool:
        nonlocal used_llm_judge
        claim_nums = _numbers_in_text(claim, normalize=normalize_numerals)
        gold_nums = _numbers_in_text(gold_text, normalize=normalize_numerals)
        if claim_nums and gold_nums and claim_nums != gold_nums:
            return False
        prec, rec, jac = _claim_token_similarity(claim, gold_text)
        if prec >= 0.5 and rec >= 0.5:
            return True
        if llm_judge is not None and 0.15 <= jac < 0.5:
            used_llm_judge = True
            return llm_judge.matches(claim, gold_text)
        return False

    # 1. Match gold memory points for this session against candidate claims.
    matched_gold: list[str] = []
    omitted_gold: list[str] = []
    claim_matched_to_gold: set[str] = set()

    for gp in gold_points:
        matching_c = [
            c for c in candidate_claims if _matches_gold(c, gp.memory_content)
        ]
        if matching_c:
            matched_gold.append(gp.point_id)
            claim_matched_to_gold.update(matching_c)
        else:
            omitted_gold.append(gp.point_id)

    # Check surviving prior claims from `before`.
    expected_surviving_prior = [
        c for c in before_claims if c not in gold_retired_claims
    ]
    omitted_prior: list[str] = []
    for prior_c in sorted(expected_surviving_prior):
        if prior_c not in candidate_claims and not any(
            _matches_gold(c, prior_c) for c in candidate_claims
        ):
            omitted_prior.append(prior_c)

    # 2. Check prior-record corruption:
    # (a) a prior record whose claims were NOT in gold_retired_claims was removed or modified
    #     to a claim that does not match its original claim;
    # (b) a prior record whose claim WAS in gold_retired_claims (is_update=True) was left
    #     un-updated/retained in candidate alongside or instead of the update.
    corrupted_record_ids: list[str] = []
    for rid, rec in before_by_id.items():
        rec_claims = set(rec.claims)
        is_retired_record = bool(rec_claims & gold_retired_claims)
        cand_rec = candidate_by_id.get(rid)
        if not is_retired_record:
            if cand_rec is None or set(cand_rec.claims) != rec_claims:
                corrupted_record_ids.append(rid)
        else:
            # This prior record was supposed to be updated/retired in this session.
            # If the old retired claim still survives unchanged in candidate, Mem0
            # failed to update/supersede the prior record (stale/corrupted prior record).
            if cand_rec is not None and (set(cand_rec.claims) & gold_retired_claims):
                corrupted_record_ids.append(rid)

    # 3. Check added claims for unsupported additions vs valid paraphrases.
    unsupported_added: list[str] = []
    paraphrased_ungrounded: list[str] = []
    for claim in added_claims:
        c_tokens = _content_tokens(claim)
        c_nums = _numbers_in_text(claim, normalize=normalize_numerals)
        num_conflict = bool(c_nums and not (c_nums <= user_numbers))
        token_coverage = (
            len(c_tokens & user_tokens) / len(c_tokens) if c_tokens else 0.0
        )
        is_gold_match = claim in claim_matched_to_gold
        is_dialogue_grounded = (not num_conflict) and (token_coverage >= 0.6)
        # HaluMem "interference" points are plausible-but-false variants of real
        # memories. A claim closer to a distractor than to any gold point is a
        # dataset-attested hallucination, even if its tokens overlap the dialogue.
        if interference_points:
            best_distractor = max(
                _claim_token_similarity(claim, ip.memory_content)[2]
                for ip in interference_points
            )
            best_gold = max(
                (_claim_token_similarity(claim, gp.memory_content)[2] for gp in gold_points),
                default=0.0,
            )
            if best_distractor >= 0.5 and best_distractor > best_gold:
                unsupported_added.append(claim)
                continue

        if not is_gold_match and not is_dialogue_grounded:
            unsupported_added.append(claim)
        elif claim not in evidence_supported:
            # The claim is valid/supported by gold or user dialogue, but StagedMem0's
            # strict LexicalGrounding did not emit evidence.supports for it!
            paraphrased_ungrounded.append(claim)

    faults: list[str] = []
    # "prior_only" (v2): a gold point of this session that was not extracted is a
    # recall gap, recorded in ``omitted_gold_points`` but not an omission fault.
    omission_trigger = (
        bool(omitted_prior)
        if omission_scope == "prior_only"
        else bool(omitted_gold or omitted_prior)
    )
    if omission_trigger:
        faults.append("omission")
    if corrupted_record_ids:
        faults.append("corruption")
    if unsupported_added:
        faults.append("unsupported_addition")

    if not faults:
        primary = "valid"
    elif "unsupported_addition" in faults:
        primary = "unsupported_addition"
    elif "corruption" in faults:
        primary = "corruption"
    else:
        primary = "omission"

    judge_calls_made = (
        (llm_judge.calls - judge_calls_before) if llm_judge is not None else 0
    )
    return SilverLabelResult(
        case_id=transition.case_id,
        user_uuid=user_uuid,
        session_id=session.session_id,
        primary_label=primary,
        fault_types=tuple(faults),
        expected_decision="ACCEPT" if primary == "valid" else "HOLD",
        matched_gold_points=tuple(matched_gold),
        omitted_gold_points=tuple(omitted_gold),
        omitted_prior_claims=tuple(omitted_prior),
        corrupted_prior_record_ids=tuple(sorted(set(corrupted_record_ids))),
        unsupported_added_claims=tuple(unsupported_added),
        paraphrased_valid_claims_ungrounded_by_lexical=tuple(paraphrased_ungrounded),
        judge_method=(
            "deterministic+llm_claim_judge" if used_llm_judge else "deterministic"
        ),
        judge_calls=judge_calls_made,
    )


def select_stratified_audit_sample(
    silver_labels: Sequence[SilverLabelResult],
    *,
    target_size: int = 100,
    seed: int = 20261121,
) -> list[SilverLabelResult]:
    """Select a seeded, stratified human-audit sample across silver label categories."""
    if target_size <= 0:
        raise ValueError("target_size must be positive")
    items = list(silver_labels)
    if len(items) <= target_size:
        return items

    rng = random.Random(_subseed(seed, "human_audit_stratified_sample"))
    by_cat: dict[str, list[SilverLabelResult]] = {cat: [] for cat in SILVER_CATEGORIES}
    for item in items:
        by_cat.setdefault(item.primary_label, []).append(item)
    for bucket in by_cat.values():
        rng.shuffle(bucket)

    non_empty_cats = [cat for cat, bucket in by_cat.items() if bucket]
    selected: list[SilverLabelResult] = []
    # Round-robin across non-empty strata so rare error classes are represented.
    while len(selected) < target_size and any(by_cat[cat] for cat in non_empty_cats):
        for cat in non_empty_cats:
            if by_cat[cat] and len(selected) < target_size:
                selected.append(by_cat[cat].pop(0))

    order_map = {item.case_id: idx for idx, item in enumerate(items)}
    return sorted(selected, key=lambda x: order_map[x.case_id])


def build_phase2_blind_quiz(
    transitions_by_id: Mapping[str, Transition],
    sampled_labels: Sequence[SilverLabelResult],
    output_dir: str | Path,
    *,
    manifest_id: str = "membud-halumem-phase2-audit-v1",
) -> dict[str, Any]:
    """Write a hash-pinned manifest + label-free cases + silver labels and build the blind HTML quiz."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cases_path = out / "audit-cases.jsonl"
    labels_path = out / "audit-labels.jsonl"
    manifest_path = out / "audit-manifest.json"
    quiz_path = out / "blind-review-quiz.html"

    case_lines: list[str] = []
    label_lines: list[str] = []
    case_ids: list[str] = []
    case_entries: list[dict[str, Any]] = []

    for sl in sampled_labels:
        t = transitions_by_id[sl.case_id]
        t_payload = transition_dict(t)
        # Ensure only the 7 public transition keys are written to audit-cases.jsonl.
        clean_case = {
            k: t_payload[k]
            for k in (
                "case_id",
                "schema_version",
                "before",
                "candidate",
                "evidence",
                "required_claims",
                "operations",
            )
        }
        case_lines.append(json.dumps(clean_case, ensure_ascii=False, sort_keys=True))
        label_lines.append(
            json.dumps(sl.to_quiz_label_row(split="heldout"), ensure_ascii=False, sort_keys=True)
        )
        case_ids.append(sl.case_id)
        case_entries.append(
            {"case_id": sl.case_id, "split": "heldout", "expected_invalid": False}
        )

    cases_bytes = ("\n".join(case_lines) + "\n").encode("utf-8") if case_lines else b""
    labels_bytes = ("\n".join(label_lines) + "\n").encode("utf-8") if label_lines else b""
    cases_path.write_bytes(cases_bytes)
    labels_path.write_bytes(labels_bytes)

    manifest = {
        "manifest_id": manifest_id,
        "schema_version": SCHEMA_VERSION,
        "status": "provisional_silver_pending_human_review",
        "cases_file": cases_path.name,
        "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
        "labels_file": labels_path.name,
        "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
        "case_count": len(case_ids),
        "case_ids": case_ids,
        "case_entries": case_entries,
        "review": {
            "reviewer_count": 0,
            "second_independent_reviewer": None,
            "second_review_status": "pending_human_audit_of_silver_labels",
            "limitation": SILVER_LABEL_LIMITATION,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Import scripts/review_quiz.py to render the self-contained blind HTML quiz.
    import importlib.util

    quiz_script = Path(__file__).resolve().parents[2] / "scripts" / "review_quiz.py"
    spec = importlib.util.spec_from_file_location("membud_review_quiz", quiz_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    quiz_report = module.build_quiz(manifest_path, quiz_path)
    return {
        "manifest_path": str(manifest_path),
        "cases_path": str(cases_path),
        "labels_path": str(labels_path),
        "quiz_html_path": str(quiz_path),
        "case_count": quiz_report["case_count"],
        "manifest_sha256": quiz_report["manifest_sha256"],
    }


__all__ = [
    "CLAIM_MATCH_JUDGE_PROMPT_VERSION",
    "FAULT_TYPES",
    "INJECTOR_CIRCULARITY_LIMITATION",
    "LEXICAL_EVIDENCE_ASSUMPTION",
    "SILVER_CATEGORIES",
    "SILVER_LABEL_LIMITATION",
    "SYNTHETIC_FIXTURE_REVISION",
    "UPDATE_OPERATIONS",
    "HaluMemBuildReport",
    "HaluMemDatasetMeta",
    "HaluMemMemoryPoint",
    "HaluMemQuestion",
    "HaluMemSession",
    "HaluMemTransitionCase",
    "HaluMemUser",
    "HaluMemValidationError",
    "InjectedEvalCase",
    "LLMClaimMatchJudge",
    "SilverLabelResult",
    "UngroundablePointReport",
    "build_halumem_transitions",
    "build_phase2_blind_quiz",
    "compute_file_sha256",
    "derive_silver_label",
    "rederive_evidence_normalized",
    "persona_name_from_info",
    "OMISSION_SCOPES",
    "NORMALIZED_EVIDENCE_THETAS",
    "inject_halumem_faults",
    "iter_halumem_users",
    "load_halumem_dataset",
    "parse_halumem_user",
    "select_stratified_audit_sample",
    "synthetic_halumem_records",
    "write_synthetic_halumem_fixture",
]
