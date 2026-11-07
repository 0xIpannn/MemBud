"""Tests for the blind review quiz builder and the agreement grader."""
from __future__ import annotations

import hashlib
import html
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "data/manifests/evaluation-v2.json"
CASES_PATH = ROOT / "data/cases/evaluation-v2.jsonl"
LABELS_PATH = ROOT / "data/labels/evaluation-v2.jsonl"
DECISIONS = ("ACCEPT", "HOLD", "INVALID_INPUT")
WRONG_DECISION = {
    "ACCEPT": "HOLD",
    "HOLD": "INVALID_INPUT",
    "INVALID_INPUT": "ACCEPT",
}
LEAK_TOKENS = (
    "expected_decision",
    "expected_failed_checks",
    "expected_invalid",
    "fault_types",
    "label_status",
    "corrupted_prior_record_ids",
    "valid_prior_records_expected_to_persist",
)


def load_quiz_module():
    spec = importlib.util.spec_from_file_location(
        "review_quiz_under_test", ROOT / "scripts/review_quiz.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def quiz():
    return load_quiz_module()


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def labels_by_id() -> dict[str, dict]:
    return {row["case_id"]: row for row in load_jsonl(LABELS_PATH)}


def cases_by_id() -> dict[str, dict]:
    return {row["case_id"]: row for row in load_jsonl(CASES_PATH)}


def manifest_sha() -> str:
    return hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()


def case_section(quiz_html: str, case_id: str) -> str:
    match = re.search(
        rf'<article class="case" id="case-{re.escape(case_id)}">.*?</article>',
        quiz_html,
        re.DOTALL,
    )
    assert match is not None, f"case section for {case_id} not found"
    return match.group(0)


def write_answers(tmp_path: Path, payload: dict, name: str = "answers.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def answers_payload(decisions: dict[str, str], reviewer: str = "Alice") -> dict:
    data = manifest()
    return {
        "reviewer": reviewer,
        "manifest_id": data["manifest_id"],
        "manifest_sha256": manifest_sha(),
        "answers": {
            case_id: {"decision": decision, "note": f"note for {case_id}"}
            for case_id, decision in decisions.items()
        },
    }


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def test_build_writes_self_contained_offline_quiz(quiz, tmp_path):
    output = tmp_path / "quiz-v2.html"
    report = quiz.build_quiz(MANIFEST_PATH, output)
    assert output.is_file()
    quiz_html = output.read_text(encoding="utf-8")

    assert report["case_count"] == 36
    assert report["manifest_id"] == "membud-evaluation-v2"
    assert report["manifest_sha256"] == manifest_sha()
    assert quiz_html.startswith("<!DOCTYPE html>")
    assert "<style>" in quiz_html and "</style>" in quiz_html
    assert "<script>" in quiz_html and "</script>" in quiz_html
    assert "localStorage" in quiz_html
    assert "Download answers JSON" in quiz_html
    assert "progress" in quiz_html
    assert "ACCEPT" in quiz_html and "HOLD" in quiz_html and "INVALID_INPUT" in quiz_html
    assert "__CASES_HTML__" not in quiz_html
    assert "__MANIFEST_ID__" not in quiz_html
    assert "__CASE_COUNT__" not in quiz_html
    # No external assets or network dependencies.
    assert not re.search(r"<script[^>]+src=", quiz_html)
    assert not re.search(r"<link[^>]+href=", quiz_html)
    assert "http://" not in quiz_html and "https://" not in quiz_html


def test_build_does_not_leak_labels(quiz, tmp_path):
    output = tmp_path / "quiz-v2.html"
    quiz.build_quiz(MANIFEST_PATH, output)
    quiz_html = output.read_text(encoding="utf-8")
    labels = labels_by_id()

    for token in LEAK_TOKENS:
        assert token not in quiz_html, f"label-only token leaked: {token}"

    # No label category, fault type, or reviewer note may appear in the quiz.
    for row in labels.values():
        assert row["category"] not in quiz_html
        for fault_type in row["fault_types"]:
            assert fault_type not in quiz_html
        if row["notes"]:
            assert row["notes"] not in quiz_html
        # No serialized label row (the answer key) may be embedded either.
        assert json.dumps(row) not in quiz_html
        assert json.dumps(row, ensure_ascii=False) not in quiz_html

    # A known expected decision (DEV-14 -> INVALID_INPUT) is not singled out:
    # all three options are present and nothing is pre-selected.
    section = case_section(quiz_html, "DEV-14")
    for decision in DECISIONS:
        assert f'value="{decision}"' in section
    assert "checked" not in section
    assert not re.search(r'data-(?:expected|decision)="', quiz_html)
    # Values are only generic rubric words/options, never a case -> decision mapping.
    assert "expected_decision" not in quiz_html


def test_build_includes_all_cases_and_transition_fields(quiz, tmp_path):
    output = tmp_path / "quiz-v2.html"
    quiz.build_quiz(MANIFEST_PATH, output)
    quiz_html = output.read_text(encoding="utf-8")
    data = manifest()
    cases = cases_by_id()

    for case_id in data["case_ids"]:
        assert f'id="case-{case_id}"' in quiz_html

    # Display order must not mirror the manifest's category-grouped ordering.
    positions = [quiz_html.index(f'id="case-{case_id}"') for case_id in data["case_ids"]]
    assert positions != sorted(positions), "quiz must shuffle cases to avoid order leakage"

    for case_id in data["case_ids"]:
        case = cases[case_id]
        section = case_section(quiz_html, case_id)
        assert f"<code>{case_id}</code>" in section
        assert "Prior state (before)" in section
        assert "Candidate state" in section
        assert "Evidence" in section
        if case["evidence"]:
            assert "Supports" in section
            assert "Contradicts" in section
        else:
            assert "(none)" in section
        assert "Required claims" in section
        assert "Declared operations" in section
        assert "Full transition JSON" in section
        transition = {field: case[field] for field in quiz.TRANSITION_FIELDS}
        escaped_raw = html.escape(json.dumps(transition, ensure_ascii=False, indent=2))
        assert escaped_raw in section

        for record in case["before"] + case["candidate"]:
            assert f'<code class="rid">{record["id"]}</code>' in section
            for claim in record["claims"]:
                assert html.escape(claim) in section
        for evidence in case["evidence"]:
            assert html.escape(evidence["text"]) in section
            for claim in evidence.get("supports", []):
                assert html.escape(claim) in section
            for claim in evidence.get("contradicts", []):
                assert html.escape(claim) in section
        for operation in case["operations"]:
            assert operation["type"] in section
            authorization = operation.get("authorization")
            if authorization:
                for value in authorization.values():
                    assert html.escape(str(value)) in section
        for claim in case["required_claims"]:
            assert html.escape(claim) in section


# --------------------------------------------------------------------------- #
# Grade
# --------------------------------------------------------------------------- #


def test_grade_perfect_answers(quiz, tmp_path):
    labels = labels_by_id()
    decisions = {case_id: row["expected_decision"] for case_id, row in labels.items()}
    answers_path = write_answers(tmp_path, answers_payload(decisions))
    output_path = tmp_path / "agreement-alice.json"

    report = quiz.grade_review(answers_path, "Alice", MANIFEST_PATH, output_path)
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert written == report
    assert report["reviewer"] == "Alice"
    assert report["manifest_id"] == "membud-evaluation-v2"
    assert report["manifest_sha256"] == manifest_sha()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", report["timestamp_utc"])
    assert report["coverage"] == {
        "total": 36,
        "answered": 36,
        "blank": 0,
        "rate": 1.0,
        "blank_case_ids": [],
    }
    expected_counts = {"ACCEPT": 14, "HOLD": 12, "INVALID_INPUT": 10}
    assert report["overall"] == {
        "cases": 36,
        "answered": 36,
        "blank": 0,
        "agreements": 36,
        "disagreements": 0,
        "agreement_rate": 1.0,
        "reviewer_decision_counts": expected_counts,
        "expected_decision_counts": expected_counts,
    }
    assert report["disagreements"] == []
    assert set(report["splits"]) == {"dev", "heldout"}
    for split in ("dev", "heldout"):
        metrics = report["splits"][split]
        assert metrics["cases"] == metrics["answered"] == 18
        assert metrics["agreements"] == 18
        assert metrics["disagreements"] == 0
        assert metrics["agreement_rate"] == 1.0
    assert len(report["cases"]) == 36
    for row in report["cases"]:
        assert set(row) == {
            "case_id",
            "split",
            "reviewer_decision",
            "expected_decision",
            "agree",
        }
        assert row["agree"] is True


@pytest.mark.parametrize("wrong_count", [5, 36])
def test_grade_partial_and_wrong_answers(quiz, tmp_path, wrong_count):
    labels = labels_by_id()
    data = manifest()
    decisions = {
        case_id: row["expected_decision"] for case_id, row in labels.items()
    }
    for case_id in data["case_ids"][:wrong_count]:
        decisions[case_id] = WRONG_DECISION[decisions[case_id]]

    answers_path = write_answers(tmp_path, answers_payload(decisions), "partial.json")
    report = quiz.grade_review(answers_path, "Alice", MANIFEST_PATH, tmp_path / "out.json")
    compared = 36
    expected_agreements = compared - wrong_count

    assert report["coverage"]["answered"] == compared
    assert report["overall"]["agreements"] == expected_agreements
    assert report["overall"]["disagreements"] == wrong_count
    assert report["overall"]["agreement_rate"] == round(expected_agreements / compared, 4)
    assert len(report["disagreements"]) == wrong_count
    for disagreement in report["disagreements"]:
        assert disagreement["reviewer_decision"] == WRONG_DECISION[
            disagreement["expected_decision"]
        ]
        assert disagreement["reviewer_note"]
        assert set(disagreement["case_summary"]) == {
            "schema_version",
            "before",
            "candidate",
            "evidence",
            "required_claims",
            "operations",
        }
    # The first five manifest cases are all in the dev split.
    if wrong_count == 5:
        assert report["splits"]["dev"]["agreements"] == 13
        assert report["splits"]["dev"]["disagreements"] == 5
        assert report["splits"]["dev"]["agreement_rate"] == round(13 / 18, 4)
        assert report["splits"]["heldout"]["agreements"] == 18
        assert report["splits"]["heldout"]["agreement_rate"] == 1.0


def test_grade_allows_blanks_and_reports_coverage(quiz, tmp_path):
    labels = labels_by_id()
    data = manifest()
    answered_ids = data["case_ids"][:30]
    decisions = {
        case_id: labels[case_id]["expected_decision"] for case_id in answered_ids
    }
    payload = answers_payload(decisions)
    # One explicit blank plus five omitted entries.
    payload["answers"][data["case_ids"][30]] = {"decision": None, "note": "uncertain"}

    answers_path = write_answers(tmp_path, payload, "blank.json")
    report = quiz.grade_review(answers_path, "Alice", MANIFEST_PATH, tmp_path / "out.json")

    assert report["coverage"]["total"] == 36
    assert report["coverage"]["answered"] == 30
    assert report["coverage"]["blank"] == 6
    assert report["coverage"]["rate"] == round(30 / 36, 4)
    assert report["coverage"]["blank_case_ids"] == data["case_ids"][30:]
    assert report["overall"]["answered"] == 30
    assert report["overall"]["blank"] == 6
    assert report["overall"]["agreements"] == 30
    assert report["overall"]["agreement_rate"] == 1.0
    blank_rows = [row for row in report["cases"] if row["reviewer_decision"] is None]
    assert len(blank_rows) == 6
    assert all(row["agree"] is None for row in blank_rows)


def test_agreement_output_schema(quiz, tmp_path):
    labels = labels_by_id()
    decisions = {
        case_id: row["expected_decision"] for case_id, row in labels.items()
    }
    decisions["DEV-01"] = WRONG_DECISION[decisions["DEV-01"]]
    answers_path = write_answers(tmp_path, answers_payload(decisions), "schema.json")
    output_path = tmp_path / "agreement-schema.json"
    report = quiz.grade_review(answers_path, "Alice", MANIFEST_PATH, output_path)

    assert set(report) == {
        "schema_version",
        "kind",
        "reviewer",
        "timestamp_utc",
        "manifest_id",
        "manifest_sha256",
        "manifest_path",
        "cases_sha256",
        "labels_sha256",
        "answers_file",
        "coverage",
        "overall",
        "splits",
        "cases",
        "disagreements",
    }
    assert report["kind"] == "membud-review-agreement"
    assert report["schema_version"] == 1
    assert set(report["coverage"]) == {
        "total",
        "answered",
        "blank",
        "rate",
        "blank_case_ids",
    }
    assert set(report["overall"]) == {
        "cases",
        "answered",
        "blank",
        "agreements",
        "disagreements",
        "agreement_rate",
        "reviewer_decision_counts",
        "expected_decision_counts",
    }
    assert len(report["disagreements"]) == 1
    assert report["disagreements"][0]["case_id"] == "DEV-01"
    assert set(report["disagreements"][0]) == {
        "case_id",
        "split",
        "reviewer_decision",
        "expected_decision",
        "reviewer_note",
        "case_summary",
    }
    assert output_path.read_text(encoding="utf-8").endswith("\n")


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "content",
    [
        "{not valid json",
        "[]",
        '"just a string"',
        '{"foo": 1}',
        '{"answers": []}',
        '{"answers": {"UNKNOWN-99": {"decision": "ACCEPT"}}}',
        '{"answers": {"DEV-01": {"decision": "MAYBE"}}}',
        '{"answers": {"DEV-01": "ACCEPT"}}',
        '{"answers": {"DEV-01": {"decision": "ACCEPT", "note": 5}}}',
    ],
)
def test_malformed_answers_are_rejected(quiz, tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(quiz.QuizError):
        quiz.build_agreement(path, "Alice", MANIFEST_PATH)


def test_answers_with_wrong_manifest_identity_are_rejected(quiz, tmp_path):
    labels = labels_by_id()
    decisions = {
        case_id: row["expected_decision"] for case_id, row in labels.items()
    }
    payload = answers_payload(decisions)
    payload["manifest_sha256"] = "0" * 64
    path = write_answers(tmp_path, payload, "wrong-sha.json")
    with pytest.raises(quiz.QuizError, match="manifest_sha256"):
        quiz.build_agreement(path, "Alice", MANIFEST_PATH)

    payload = answers_payload(decisions, reviewer="Bob")
    path = write_answers(tmp_path, payload, "wrong-reviewer.json")
    with pytest.raises(quiz.QuizError, match="reviewer"):
        quiz.build_agreement(path, "Alice", MANIFEST_PATH)


def test_tampered_case_hash_is_rejected(quiz, tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    data = manifest()
    cases_path = (MANIFEST_PATH.parent / data["cases_file"]).resolve()
    labels_path = (MANIFEST_PATH.parent / data["labels_file"]).resolve()
    copied_cases = package / "cases.jsonl"
    copied_labels = package / "labels.jsonl"
    copied_cases.write_bytes(cases_path.read_bytes())
    copied_labels.write_bytes(labels_path.read_bytes())
    data["cases_file"] = "cases.jsonl"
    data["labels_file"] = "labels.jsonl"
    (package / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    # Sanity: the copied package builds while the hash matches.
    quiz.build_quiz(package / "manifest.json", tmp_path / "quiz.html")

    copied_cases.write_bytes(copied_cases.read_bytes() + b"\n")
    with pytest.raises(quiz.QuizError, match="case file hash"):
        quiz.build_quiz(package / "manifest.json", tmp_path / "quiz2.html")


def _copied_package(tmp_path: Path, mutate_cases) -> Path:
    package = tmp_path / "package"
    package.mkdir(parents=True)
    data = manifest()
    cases = load_jsonl(CASES_PATH)
    mutate_cases(cases)
    cases_path = package / "cases.jsonl"
    cases_path.write_text(
        "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
    )
    data["cases_file"] = "cases.jsonl"
    data["cases_sha256"] = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    manifest_path = package / "manifest.json"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    return manifest_path


def test_build_drops_unknown_fields_and_rejects_label_fields(quiz, tmp_path):
    def add_unknown(cases):
        cases[0]["future_metadata"] = "SHOULD_NOT_LEAK"

    quiz.build_quiz(
        _copied_package(tmp_path / "unknown", add_unknown), tmp_path / "quiz.html"
    )
    assert "SHOULD_NOT_LEAK" not in (tmp_path / "quiz.html").read_text(encoding="utf-8")

    def add_label(cases):
        cases[0]["expected_decision"] = "ACCEPT"

    with pytest.raises(quiz.QuizError, match="label-only"):
        quiz.build_quiz(
            _copied_package(tmp_path / "label", add_label), tmp_path / "quiz2.html"
        )


def test_quiz_explains_concepts_bilingually(quiz, tmp_path):
    output = tmp_path / "quiz-v2.html"
    quiz.build_quiz(MANIFEST_PATH, output)
    quiz_html = output.read_text(encoding="utf-8")

    for term in (
        "How to read a case",
        "Prior state",
        "Candidate state",
        "Claim",
        "Evidence",
        "Required claims",
        "Declared operations",
        "Delete authorization",
        "What changes",
        "How to decide",
        "Klaim wajib",
        "Operasi",
        "otorisasi",
    ):
        assert term in quiz_html, f"missing explained concept: {term}"

    # Plain-language decision hints appear with the options.
    assert "safe" in quiz_html
    assert "unsafe or unsure" in quiz_html
    assert "malformed" in quiz_html


def test_case_sections_are_numbered_and_show_a_derived_diff(quiz, tmp_path):
    output = tmp_path / "quiz-v2.html"
    quiz.build_quiz(MANIFEST_PATH, output)
    quiz_html = output.read_text(encoding="utf-8")
    cases = cases_by_id()

    def signature(records):
        return [(record["id"], tuple(record["claims"])) for record in records]

    added_id = next(
        case_id
        for case_id, case in cases.items()
        if set(record["id"] for record in case["candidate"])
        - set(record["id"] for record in case["before"])
    )
    noop_id = next(
        case_id
        for case_id, case in cases.items()
        if signature(case["before"]) == signature(case["candidate"])
    )
    modified_id = next(
        case_id
        for case_id, case in cases.items()
        if any(
            before["id"] == candidate["id"] and tuple(before["claims"]) != tuple(candidate["claims"])
            for before in case["before"]
            for candidate in case["candidate"]
        )
    )

    added_section = case_section(quiz_html, added_id)
    assert 'class="step"' in added_section
    assert "What changes (derived)" in added_section
    assert "added" in added_section
    assert "not whether the change is authorized" in added_section
    assert "Keadaan memori saat ini" in added_section
    added_record_id = (
        set(record["id"] for record in cases[added_id]["candidate"])
        - set(record["id"] for record in cases[added_id]["before"])
    ).pop()
    assert f'<code class="rid">{added_record_id}</code>' in added_section

    noop_section = case_section(quiz_html, noop_id)
    assert "No state change" in noop_section

    modified_section = case_section(quiz_html, modified_id)
    assert "modified" in modified_section
