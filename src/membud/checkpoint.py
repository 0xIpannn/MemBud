"""Append-only JSONL checkpoints for row-based model-evaluation runs.

A checkpoint lets an interrupted paid run resume without repeating completed
rows.  It stores only a *run configuration fingerprint* (used to refuse an
incompatible resume), the identity of each attempted row, and the row's
already-computed result/accounting payload.  It never stores prompts, API keys,
or raw provider request/response bodies beyond the result summary the runner
already writes to ``summary.json``.

Format
------
Line 1 is a header::

    {"config": {...}, "fingerprint": "<sha256>", "kind": "header", "schema_version": 1}

Each subsequent line is a row or the terminal completion marker::

    {"kind": "row", "row": {...}}
    {"kind": "complete", "schema_version": 1}

Every line is canonical JSON (sorted keys, compact separators) so the same
state always serializes to the same bytes.  Appends are flushed and
``os.fsync``-ed so a crash loses at most the current line.

Tolerance policy
----------------
* A torn or corrupt **final** line (a crash mid-write, or a final line without
  a trailing newline) is reported and ignored; the checkpoint can still be
  loaded.
* A corrupt **interior** line raises :class:`CheckpointCorruptError`; silently
  dropping completed rows in the middle of a run would corrupt the budget
  ledger.
* A row whose identity appears more than once is de-duplicated last-write-wins
  and counted in ``duplicate_row_count``; totals are always recomputed from the
  de-duplicated rows.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_FILENAME = "checkpoint.jsonl"
DEFAULT_IDENTITY_FIELDS: tuple[str, ...] = ("condition", "case_id")

_HEADER_KIND = "header"
_ROW_KIND = "row"
_COMPLETE_KIND = "complete"
_MAX_VALUE_REPR = 160


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CheckpointError(RuntimeError):
    """Base class for checkpoint failures."""


class CheckpointConfigMismatch(CheckpointError, ValueError):
    """A resume was attempted with a different run configuration.

    The checkpoint is append-only and its budget/accounting totals are reused,
    so resuming under a different manifest/model/prompt/settings would mix
    incomparable results.
    """


class CheckpointCorruptError(CheckpointError, ValueError):
    """A checkpoint line is corrupt where recovery is not safe."""


class CheckpointFormatError(CheckpointError, ValueError):
    """A row or configuration passed to the checkpoint API is malformed."""


# ---------------------------------------------------------------------------
# Configuration fingerprinting
# ---------------------------------------------------------------------------


def canonical_json(payload: Any) -> str:
    """Serialize deterministically (sorted keys, compact separators)."""
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-normalized deep copy of a run configuration."""
    if not isinstance(config, Mapping):
        raise CheckpointFormatError("checkpoint config must be a mapping")
    normalized = json.loads(json.dumps(dict(config), ensure_ascii=False, sort_keys=True))
    if not isinstance(normalized, dict):  # pragma: no cover - defensive
        raise CheckpointFormatError("checkpoint config must serialize to an object")
    return normalized


def fingerprint_config(config: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical configuration JSON."""
    return hashlib.sha256(canonical_json(normalize_config(config)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _non_negative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _identity(key: tuple[str, ...] | Mapping[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    if isinstance(key, Mapping):
        values: list[str] = []
        for field in fields:
            value = key.get(field)
            if not isinstance(value, str) or not value:
                raise CheckpointFormatError(
                    f"checkpoint row needs a non-empty string {field!r} identity field"
                )
            values.append(value)
        return tuple(values)
    if isinstance(key, tuple):
        if len(key) != len(fields):
            raise CheckpointFormatError(
                f"identity tuple has {len(key)} parts; expected {len(fields)}"
            )
        if any(not isinstance(part, str) or not part for part in key):
            raise CheckpointFormatError("identity tuple parts must be non-empty strings")
        return key
    raise CheckpointFormatError("identity must be a row mapping or a string tuple")


def _short_repr(value: Any) -> str:
    text = repr(value)
    if len(text) > _MAX_VALUE_REPR:
        return text[:_MAX_VALUE_REPR] + "..."
    return text


# ---------------------------------------------------------------------------
# RunCheckpoint
# ---------------------------------------------------------------------------


class RunCheckpoint:
    """Append-only JSONL checkpoint for a single row-based run.

    Typical use::

        checkpoint = RunCheckpoint.create(path, config=config)          # new run
        checkpoint = RunCheckpoint.load(path)                            # resume
        checkpoint.validate(config)                                      # refuse mismatch
        checkpoint.append(row)                                           # after each row
        checkpoint.mark_complete()                                       # when all planned rows exist
        totals = checkpoint.totals()                                     # budget carry-over
    """

    def __init__(
        self,
        path: str | Path,
        *,
        identity_fields: Sequence[str] = DEFAULT_IDENTITY_FIELDS,
    ) -> None:
        fields = tuple(identity_fields)
        if not fields or any(not isinstance(field, str) or not field for field in fields):
            raise ValueError("identity_fields must be non-empty strings")
        self.path = Path(path)
        self.identity_fields = fields
        self.config: dict[str, Any] | None = None
        self.fingerprint: str | None = None
        self.is_complete = False
        self.torn_final_line = False
        self.load_notes: list[str] = []
        self.duplicate_row_count = 0
        self._rows: dict[tuple[str, ...], dict[str, Any]] = {}
        # Byte offset of the last durable record; set when a torn/corrupt final
        # line was ignored so the next append repairs the file before extending.
        self._repair_size: int | None = None

    # -- construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        config: Mapping[str, Any],
        identity_fields: Sequence[str] = DEFAULT_IDENTITY_FIELDS,
    ) -> "RunCheckpoint":
        """Create a new checkpoint; refuses to overwrite recorded rows."""
        checkpoint = cls(path, identity_fields=identity_fields)
        checkpoint._initialize(config)
        return checkpoint

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        identity_fields: Sequence[str] = DEFAULT_IDENTITY_FIELDS,
    ) -> "RunCheckpoint":
        """Load a checkpoint tolerantly (torn final line ignored and reported)."""
        checkpoint = cls(path, identity_fields=identity_fields)
        checkpoint._load()
        return checkpoint

    def _initialize(self, config: Mapping[str, Any]) -> None:
        normalized = normalize_config(config)
        if self.path.exists() and self.path.stat().st_size:
            raise CheckpointError(
                f"refusing to overwrite non-empty checkpoint {self.path}; "
                "resume it or choose a fresh output directory"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fingerprint = fingerprint_config(normalized)
        header = {
            "kind": _HEADER_KIND,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "config": normalized,
        }
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write(canonical_json(header) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.config = normalized
        self.fingerprint = fingerprint

    # -- loading ------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            raise CheckpointError(f"checkpoint not found: {self.path}")
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            raise CheckpointError(f"checkpoint is empty: {self.path}")
        lines = text.splitlines()
        trailing_newline = text.endswith("\n")
        torn: str | None = None
        if lines and not trailing_newline:
            # A crash can leave the final write without its terminator even when
            # the JSON happens to parse; treat it as not durably recorded.
            torn = lines[-1]
            lines = lines[:-1]
        header_seen = False
        ignored_final_index: int | None = None
        # Treat the last non-blank line as the "final" line so trailing blank
        # lines do not make a genuinely final corrupt line look interior.
        content_indices = [
            index for index, line in enumerate(lines, start=1) if line.strip()
        ]
        last_content_index = content_indices[-1] if content_indices else 0
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            is_final_line = index == last_content_index
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                if is_final_line:
                    self.torn_final_line = True
                    ignored_final_index = index
                    self.load_notes.append(
                        f"ignored corrupt final checkpoint line {index}: {exc.msg}"
                    )
                    break
                raise CheckpointCorruptError(
                    f"corrupt checkpoint line {index} of {self.path}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                if is_final_line:
                    self.torn_final_line = True
                    ignored_final_index = index
                    self.load_notes.append(
                        f"ignored non-object final checkpoint line {index}"
                    )
                    break
                raise CheckpointCorruptError(
                    f"checkpoint line {index} of {self.path} is not a JSON object"
                )
            kind = record.get("kind")
            if kind == _HEADER_KIND:
                self._load_header(record, index, header_seen)
                header_seen = True
            elif kind == _ROW_KIND:
                if not header_seen:
                    raise CheckpointCorruptError(
                        f"checkpoint row on line {index} appears before the header"
                    )
                row = record.get("row")
                if not isinstance(row, dict):
                    raise CheckpointCorruptError(
                        f"checkpoint row on line {index} is not a JSON object"
                    )
                try:
                    identity = _identity(row, self.identity_fields)
                except CheckpointFormatError as exc:
                    raise CheckpointCorruptError(
                        f"checkpoint row on line {index} is malformed: {exc}"
                    ) from exc
                if identity in self._rows:
                    self.duplicate_row_count += 1
                self._rows[identity] = row
            elif kind == _COMPLETE_KIND:
                if not header_seen:
                    raise CheckpointCorruptError(
                        f"checkpoint completion marker on line {index} appears before the header"
                    )
                self.is_complete = True
            else:
                raise CheckpointCorruptError(
                    f"unknown checkpoint record kind {kind!r} on line {index}"
                )
        if not header_seen:
            detail = " (only a torn final line is present)" if torn is not None else ""
            raise CheckpointCorruptError(f"checkpoint has no valid header{detail}: {self.path}")
        if torn is not None:
            self.torn_final_line = True
            self.load_notes.append("ignored torn final checkpoint line (no trailing newline)")
            last_newline = text.rfind("\n")
            repair_prefix = text[: last_newline + 1] if last_newline >= 0 else ""
            self._repair_size = len(repair_prefix.encode("utf-8"))
        elif ignored_final_index is not None:
            repair_prefix = "".join(
                line + "\n" for line in lines[: ignored_final_index - 1]
            )
            self._repair_size = len(repair_prefix.encode("utf-8"))

    def _load_header(self, record: Mapping[str, Any], index: int, already_seen: bool) -> None:
        if already_seen:
            raise CheckpointCorruptError(
                f"duplicate checkpoint header on line {index}"
            )
        schema_version = record.get("schema_version")
        if schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointCorruptError(
                f"unsupported checkpoint schema_version {schema_version!r} on line {index}; "
                f"expected {CHECKPOINT_SCHEMA_VERSION}"
            )
        config = record.get("config")
        fingerprint = record.get("fingerprint")
        if not isinstance(config, dict) or not isinstance(fingerprint, str):
            raise CheckpointCorruptError(f"checkpoint header on line {index} is malformed")
        if fingerprint_config(config) != fingerprint:
            raise CheckpointCorruptError(
                "checkpoint header fingerprint does not match its stored config"
            )
        self.config = normalize_config(config)
        self.fingerprint = fingerprint

    # -- validation ---------------------------------------------------------

    def validate(self, config: Mapping[str, Any]) -> None:
        """Raise :class:`CheckpointConfigMismatch` unless the config matches."""
        if self.config is None or self.fingerprint is None:
            raise CheckpointError("checkpoint is not initialized; load or create it first")
        requested = normalize_config(config)
        stored = self.config
        if canonical_json(stored) == canonical_json(requested):
            return
        mismatches: list[str] = []
        for key in sorted(set(stored) | set(requested)):
            if key not in stored:
                mismatches.append(f"{key}: absent in checkpoint, requested {_short_repr(requested[key])}")
            elif key not in requested:
                mismatches.append(f"{key}: checkpoint {_short_repr(stored[key])}, absent in requested config")
            elif canonical_json(stored[key]) != canonical_json(requested[key]):
                mismatches.append(
                    f"{key}: checkpoint {_short_repr(stored[key])} != "
                    f"requested {_short_repr(requested[key])}"
                )
        raise CheckpointConfigMismatch(
            "checkpoint config fingerprint mismatch; refusing to resume "
            f"({len(mismatches)} differing field(s)): " + "; ".join(mismatches)
        )

    # -- appending ----------------------------------------------------------

    def append(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Durably append one completed row; returns a shallow copy."""
        if self.config is None or self.fingerprint is None:
            raise CheckpointError("checkpoint is not initialized; load or create it first")
        payload = dict(row)
        identity = _identity(payload, self.identity_fields)
        if identity in self._rows:
            raise CheckpointFormatError(
                f"row already recorded in checkpoint: {identity!r}"
            )
        self._append_record({"kind": _ROW_KIND, "row": payload})
        self._rows[identity] = payload
        return payload

    def mark_complete(self) -> None:
        """Append the terminal completion marker once."""
        if self.is_complete:
            return
        self._append_record({"kind": _COMPLETE_KIND, "schema_version": CHECKPOINT_SCHEMA_VERSION})
        self.is_complete = True

    def _append_record(self, record: Mapping[str, Any]) -> None:
        if self.config is None:  # pragma: no cover - checked by callers
            raise CheckpointError("checkpoint is not initialized")
        # A previously ignored torn/corrupt final line must be removed before the
        # next append, otherwise it would become a corrupt *interior* line.
        if self._repair_size is not None:
            if self.path.exists() and self.path.stat().st_size > self._repair_size:
                with self.path.open("r+b") as handle:
                    handle.truncate(self._repair_size)
                    handle.flush()
                    os.fsync(handle.fileno())
            self._repair_size = None
        line = canonical_json(record) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    # -- inspection ---------------------------------------------------------

    def completed_identities(self) -> list[tuple[str, ...]]:
        """Identities of all recorded rows, in file order."""
        return list(self._rows)

    def has_row(self, key: Mapping[str, Any] | tuple[str, ...]) -> bool:
        return _identity(key, self.identity_fields) in self._rows

    def get_row(self, key: Mapping[str, Any] | tuple[str, ...]) -> dict[str, Any] | None:
        return self._rows.get(_identity(key, self.identity_fields))

    def rows(self) -> list[dict[str, Any]]:
        """Recorded row payloads (de-duplicated, file order)."""
        return list(self._rows.values())

    def totals(self) -> dict[str, int]:
        """Recompute the run's accounting totals from recorded rows.

        This is what makes a resumed run's budget correct: the runner seeds its
        running counter from these values instead of trusting an in-memory
        counter lost by the crash.
        """
        accounted = reported = estimated = attempts = cache_hits = 0
        for row in self._rows.values():
            accounted_tokens = _non_negative_int(row.get("accounted_tokens"))
            accounted += accounted_tokens
            usage = row.get("usage")
            if isinstance(usage, dict):
                reported += _non_negative_int(usage.get("total_tokens"))
            if row.get("usage_estimated"):
                estimated += accounted_tokens
            attempts += _non_negative_int(row.get("attempts"))
            cache_hits += _non_negative_int(row.get("cache_hit"))
        return {
            "row_count": len(self._rows),
            "accounted_tokens": accounted,
            "reported_tokens": reported,
            "estimated_tokens": estimated,
            "attempts": attempts,
            "cache_hits": cache_hits,
        }


__all__ = [
    "CHECKPOINT_FILENAME",
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointConfigMismatch",
    "CheckpointCorruptError",
    "CheckpointError",
    "CheckpointFormatError",
    "DEFAULT_IDENTITY_FIELDS",
    "RunCheckpoint",
    "canonical_json",
    "fingerprint_config",
    "normalize_config",
]
