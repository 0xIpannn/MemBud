"""Tests for the append-only JSONL run checkpoint.

No network, no model calls: the checkpoint is pure file state.
"""
from __future__ import annotations

import json

import pytest

from membud.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointConfigMismatch,
    CheckpointCorruptError,
    CheckpointError,
    CheckpointFormatError,
    RunCheckpoint,
    canonical_json,
    fingerprint_config,
    normalize_config,
)

CONFIG = {
    "manifest_sha256": "a" * 64,
    "split": "heldout",
    "model": "fake-deepseek",
    "provider": "fake",
    "prompt_versions": {"llm": "checks-v2", "semantic": "compare-v1"},
    "conditions": ["graph", "text"],
    "max_attempts": 3,
    "code_revision": "working-tree-sha256:test",
}


def _row(case_id: str, condition: str = "graph", accounted: int = 150) -> dict:
    return {
        "case_id": case_id,
        "condition": condition,
        "status": "completed",
        "decision": "ACCEPT",
        "accounted_tokens": accounted,
        "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": accounted},
        "usage_estimated": False,
        "attempts": 1,
        "cache_hit": 0,
    }


def _create(tmp_path, config=CONFIG, name="checkpoint.jsonl") -> RunCheckpoint:
    return RunCheckpoint.create(tmp_path / name, config=config)


# ---------------------------------------------------------------------------
# Round trip and deterministic serialization
# ---------------------------------------------------------------------------


def test_create_append_load_round_trip(tmp_path):
    checkpoint = _create(tmp_path)
    assert checkpoint.fingerprint == fingerprint_config(CONFIG)
    checkpoint.append(_row("HELD-01"))
    checkpoint.append(_row("HELD-02"))
    checkpoint.mark_complete()

    loaded = RunCheckpoint.load(tmp_path / "checkpoint.jsonl")
    assert loaded.config == normalize_config(CONFIG)
    assert loaded.fingerprint == fingerprint_config(CONFIG)
    assert loaded.is_complete is True
    assert loaded.torn_final_line is False
    assert loaded.load_notes == []
    assert loaded.get_row({"condition": "graph", "case_id": "HELD-01"}) == _row("HELD-01")
    assert loaded.has_row({"condition": "graph", "case_id": "HELD-02"})
    assert loaded.has_row(("graph", "HELD-01"))
    assert loaded.get_row(("graph", "missing")) is None
    assert loaded.completed_identities() == [("graph", "HELD-01"), ("graph", "HELD-02")]
    loaded.validate(CONFIG)  # no raise


def test_serialization_is_deterministic(tmp_path):
    first = _create(tmp_path, name="first.jsonl")
    second = _create(tmp_path, name="second.jsonl")
    for checkpoint in (first, second):
        checkpoint.append(_row("HELD-01"))
        checkpoint.mark_complete()
    assert (tmp_path / "first.jsonl").read_bytes() == (
        tmp_path / "second.jsonl"
    ).read_bytes()
    # Canonical JSON: sorted keys and compact separators.
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_checkpoint_contains_only_provided_row_payload(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    text = (tmp_path / "checkpoint.jsonl").read_text(encoding="utf-8")
    # The checkpoint stores identity/accounting/result summaries, never prompt
    # text, request messages, or credentials.  Only prompt *version labels* are
    # recorded (as part of the config fingerprint).
    assert "messages" not in text
    assert "api_key" not in text
    assert '"prompt":' not in text
    assert "system_prompt" not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert records[0]["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert records[1]["row"] == _row("HELD-01")


def test_create_refuses_to_overwrite_non_empty_checkpoint(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    with pytest.raises(CheckpointError, match="refusing to overwrite"):
        _create(tmp_path)


def test_complete_marker_is_idempotent(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.mark_complete()
    checkpoint.mark_complete()
    text = (tmp_path / "checkpoint.jsonl").read_text(encoding="utf-8")
    assert text.count('"kind":"complete"') == 1
    loaded = RunCheckpoint.load(tmp_path / "checkpoint.jsonl")
    assert loaded.is_complete is True


# ---------------------------------------------------------------------------
# Fingerprint validation
# ---------------------------------------------------------------------------


def test_validate_accepts_equivalent_config(tmp_path):
    checkpoint = _create(tmp_path)
    equivalent = dict(reversed(list(CONFIG.items())))
    checkpoint.validate(equivalent)  # order-insensitive canonical comparison


@pytest.mark.parametrize(
    "field, value",
    [
        ("max_attempts", 5),
        ("split", "dev"),
        ("model", "other-model"),
        ("conditions", ["graph"]),
        ("manifest_sha256", "b" * 64),
        ("prompt_versions", {"llm": "checks-v1", "semantic": "compare-v1"}),
        ("code_revision", "different-revision"),
    ],
)
def test_validate_refuses_field_mismatch(tmp_path, field, value):
    checkpoint = _create(tmp_path)
    changed = dict(CONFIG)
    changed[field] = value
    with pytest.raises(CheckpointConfigMismatch) as excinfo:
        checkpoint.validate(changed)
    message = str(excinfo.value)
    assert "refusing to resume" in message
    assert field in message


def test_validate_reports_added_and_missing_fields(tmp_path):
    checkpoint = _create(tmp_path)
    changed = {key: value for key, value in CONFIG.items() if key != "split"}
    changed["new_setting"] = True
    with pytest.raises(CheckpointConfigMismatch) as excinfo:
        checkpoint.validate(changed)
    message = str(excinfo.value)
    assert "split" in message
    assert "new_setting" in message


def test_validate_before_initialization_raises(tmp_path):
    checkpoint = RunCheckpoint(tmp_path / "checkpoint.jsonl")
    with pytest.raises(CheckpointError, match="not initialized"):
        checkpoint.validate(CONFIG)


# ---------------------------------------------------------------------------
# Crash tolerance: torn / corrupt lines
# ---------------------------------------------------------------------------


def _lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_torn_final_line_without_newline_is_reported_and_ignored(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    path = tmp_path / "checkpoint.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"row","row":{"case_id":"HELD-02","condit')  # crash mid-write

    loaded = RunCheckpoint.load(path)
    assert loaded.torn_final_line is True
    assert any("torn" in note or "ignored" in note for note in loaded.load_notes)
    assert loaded.completed_identities() == [("graph", "HELD-01")]


def test_complete_json_final_line_without_newline_is_still_torn(tmp_path):
    checkpoint = _create(tmp_path)
    path = tmp_path / "checkpoint.jsonl"
    # A complete-looking row that was never terminated by a newline is treated
    # as not durably recorded and re-runs on resume.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json({"kind": "row", "row": _row("HELD-02")}))
    loaded = RunCheckpoint.load(path)
    assert loaded.torn_final_line is True
    assert loaded.completed_identities() == []


def test_corrupt_final_line_with_newline_is_ignored(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    path = tmp_path / "checkpoint.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json}\n")
    loaded = RunCheckpoint.load(path)
    assert loaded.torn_final_line is True
    assert loaded.completed_identities() == [("graph", "HELD-01")]


def test_corrupt_interior_line_raises(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    checkpoint.append(_row("HELD-02"))
    path = tmp_path / "checkpoint.jsonl"
    lines = _lines(path)
    lines.insert(1, "{corrupt}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CheckpointCorruptError, match="line 2"):
        RunCheckpoint.load(path)


def test_corrupt_truncated_row_with_following_row_raises(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    checkpoint.append(_row("HELD-02"))
    path = tmp_path / "checkpoint.jsonl"
    lines = _lines(path)
    lines.insert(1, '{"kind":"row","row":')  # unterminated JSON in the middle
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CheckpointCorruptError):
        RunCheckpoint.load(path)


def test_unknown_record_kind_raises(tmp_path):
    checkpoint = _create(tmp_path)
    path = tmp_path / "checkpoint.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"mystery"}\n')
    with pytest.raises(CheckpointCorruptError, match="unknown checkpoint record kind"):
        RunCheckpoint.load(path)


def test_row_before_header_raises(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text(
        canonical_json({"kind": "row", "row": _row("HELD-01")}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CheckpointCorruptError, match="before the header"):
        RunCheckpoint.load(path)


def test_missing_header_raises(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(CheckpointError, match="empty"):
        RunCheckpoint.load(path)


def test_only_torn_header_raises(tmp_path):
    path = tmp_path / "checkpoint.jsonl"
    path.write_text('{"kind":"header","schema_version":1,"config"', encoding="utf-8")
    with pytest.raises(CheckpointCorruptError, match="no valid header"):
        RunCheckpoint.load(path)


def test_tampered_header_fingerprint_raises(tmp_path):
    checkpoint = _create(tmp_path)
    path = tmp_path / "checkpoint.jsonl"
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    header["config"]["max_attempts"] = 99  # fingerprint no longer matches config
    path.write_text(canonical_json(header) + "\n", encoding="utf-8")
    with pytest.raises(CheckpointCorruptError, match="fingerprint does not match"):
        RunCheckpoint.load(path)


def test_unsupported_schema_version_raises(tmp_path):
    checkpoint = _create(tmp_path)
    path = tmp_path / "checkpoint.jsonl"
    header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    header["schema_version"] = 99
    path.write_text(canonical_json(header) + "\n", encoding="utf-8")
    with pytest.raises(CheckpointCorruptError, match="unsupported checkpoint schema_version"):
        RunCheckpoint.load(path)


def test_append_after_torn_line_repairs_the_log(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    path = tmp_path / "checkpoint.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"row","row":{"case_id":"HELD-02","condit')  # torn

    loaded = RunCheckpoint.load(path)
    assert loaded.torn_final_line is True
    loaded.append(_row("HELD-02"))
    loaded.mark_complete()

    reloaded = RunCheckpoint.load(path)
    assert reloaded.torn_final_line is False
    assert reloaded.completed_identities() == [("graph", "HELD-01"), ("graph", "HELD-02")]
    assert reloaded.is_complete is True


def test_append_after_corrupt_final_line_repairs_the_log(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    path = tmp_path / "checkpoint.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")

    loaded = RunCheckpoint.load(path)
    assert loaded.torn_final_line is True
    loaded.append(_row("HELD-02"))

    reloaded = RunCheckpoint.load(path)
    assert reloaded.torn_final_line is False
    assert reloaded.completed_identities() == [("graph", "HELD-01"), ("graph", "HELD-02")]


def test_load_missing_checkpoint_raises(tmp_path):
    with pytest.raises(CheckpointError, match="not found"):
        RunCheckpoint.load(tmp_path / "missing.jsonl")


# ---------------------------------------------------------------------------
# Rows, duplicates, totals
# ---------------------------------------------------------------------------


def test_append_refuses_duplicate_identity(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    with pytest.raises(CheckpointFormatError, match="already recorded"):
        checkpoint.append(_row("HELD-01"))


def test_append_requires_identity_fields(tmp_path):
    checkpoint = _create(tmp_path)
    with pytest.raises(CheckpointFormatError, match="case_id"):
        checkpoint.append({"condition": "graph", "case_id": ""})
    with pytest.raises(CheckpointFormatError, match="case_id"):
        checkpoint.append({"condition": "graph", "accounted_tokens": 1})


def test_custom_identity_fields(tmp_path):
    checkpoint = RunCheckpoint.create(
        tmp_path / "checkpoint.jsonl",
        config={"variants": ["graph_cache"]},
        identity_fields=("variant", "case_id"),
    )
    checkpoint.append({"variant": "graph_cache", "case_id": "HELD-01"})
    loaded = RunCheckpoint.load(
        tmp_path / "checkpoint.jsonl", identity_fields=("variant", "case_id")
    )
    assert loaded.has_row(("graph_cache", "HELD-01"))
    assert loaded.totals()["row_count"] == 1


def test_duplicate_rows_last_write_wins(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01", accounted=100))
    path = tmp_path / "checkpoint.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            canonical_json({"kind": "row", "row": _row("HELD-01", accounted=250)}) + "\n"
        )
    loaded = RunCheckpoint.load(path)
    assert loaded.duplicate_row_count == 1
    assert loaded.get_row(("graph", "HELD-01"))["accounted_tokens"] == 250
    assert loaded.totals()["accounted_tokens"] == 250


def test_totals_are_recomputed_from_rows(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01", accounted=150))
    checkpoint.append(
        {
            "case_id": "HELD-02",
            "condition": "graph",
            "accounted_tokens": 7500,
            "usage": {"total_tokens": None},
            "usage_estimated": True,
            "attempts": 3,
            "cache_hit": 0,
        }
    )
    checkpoint.append(
        {
            "case_id": "HELD-03",
            "condition": "graph",
            "accounted_tokens": 0,
            "usage": {"total_tokens": None},
            "usage_estimated": False,
            "attempts": 0,
            "cache_hit": 1,
        }
    )
    loaded = RunCheckpoint.load(tmp_path / "checkpoint.jsonl")
    assert loaded.totals() == {
        "row_count": 3,
        "accounted_tokens": 7650,
        "reported_tokens": 150,
        "estimated_tokens": 7500,
        "attempts": 4,
        "cache_hits": 1,
    }


def test_append_after_load_continues_the_log(tmp_path):
    checkpoint = _create(tmp_path)
    checkpoint.append(_row("HELD-01"))
    del checkpoint

    resumed = RunCheckpoint.load(tmp_path / "checkpoint.jsonl")
    resumed.append(_row("HELD-02"))
    resumed.mark_complete()
    reloaded = RunCheckpoint.load(tmp_path / "checkpoint.jsonl")
    assert reloaded.completed_identities() == [("graph", "HELD-01"), ("graph", "HELD-02")]
    assert reloaded.is_complete is True
    assert reloaded.totals()["accounted_tokens"] == 300


def test_fingerprint_config_normalizes_nested_values():
    first = fingerprint_config({"b": [1, 2], "a": {"y": 1, "x": 2}})
    second = fingerprint_config({"a": {"x": 2, "y": 1}, "b": [1, 2]})
    assert first == second
    assert len(first) == 64
