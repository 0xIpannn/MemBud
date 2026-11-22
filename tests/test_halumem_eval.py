"""End-to-end offline tests for the HaluMem Phase 1 and Phase 2 runners.

Both runners are exercised on the synthetic HaluMem-shaped fixture only.  Phase 2
uses an injected proposal stager so no Mem0, OpenRouter, or network access is
required.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from membud.checkpoint import (
    CHECKPOINT_FILENAME,
    CheckpointConfigMismatch,
    RunCheckpoint,
)
from membud.halumem import (
    build_halumem_transitions,
    load_halumem_dataset,
    write_synthetic_halumem_fixture,
)
from membud.llm.client import OpenRouterPricingError
from membud.serialization import transition_dict


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase1 = _load_script("run_halumem_eval")
phase2 = _load_script("run_halumem_mem0")


def _fixture(tmp_path: Path) -> Path:
    return write_synthetic_halumem_fixture(tmp_path / "synthetic-halumem.jsonl")


def _gold_transitions(tmp_path: Path):
    path = _fixture(tmp_path)
    meta, users = load_halumem_dataset(path, dataset_revision="test")
    report = build_halumem_transitions(users, dataset_meta=meta)
    return path, {case.case_id: case.transition for case in report.cases}


def _fake_stager(transitions):
    calls = []

    def stage(user, session, session_index, case_id):
        calls.append(case_id)
        return {
            "case_id": case_id,
            "user_uuid": user.uuid,
            "session_id": session.session_id,
            "session_index": session_index,
            "staged_decision": "ACCEPT",
            "staged_applied": True,
            "staged_reason": "injected offline proposal",
            "mem0_raw_output": {},
            "mem0_proposals": [],
            "transition": transition_dict(transitions[case_id]),
            "usage": {"total_tokens": 0, "api_attempts": 0},
            "latency_ms": 0.0,
        }

    return stage, calls


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------


def test_phase1_runner_end_to_end_on_synthetic_fixture(tmp_path):
    report = phase1.run_halumem_phase1(
        output_dir=tmp_path / "out", resamples=100, seed=11
    )

    assert report["schema"] == "membud-halumem-phase1-report-v1"
    assert report["paid_api_calls"] == 0
    assert report["network_calls"] == 0
    assert report["config"]["is_synthetic_fixture"] is True
    assert report["config"]["dataset_revision"] == "synthetic-halumem-fixture-v1"
    assert report["case_counts"]["total"] == 66
    assert report["case_counts"]["valid"] == 17
    assert report["case_counts"]["by_fault_type"] == {
        "omission": 11,
        "corruption": 21,
        "unsupported_addition": 17,
    }
    assert report["build_summary"]["ungroundable_count"] == 1

    without = report["tracks"]["without_transcript_grounding"]
    with_tg = report["tracks"]["with_transcript_grounding"]
    for track in (without, with_tg):
        assert set(track["conditions"]) == {"unguarded", "graph", "text", "text_fair"}
        for condition in ("graph", "text", "text_fair"):
            metrics = track["conditions"][condition]
            assert metrics["faulty_overall"]["detection"]["numerator"] > 0
            assert metrics["valid_updates"]["false_holds"]["numerator"] == 0

    # Known finding: with equal information graph and text_fair tie.
    graph_vs_fair = without["paired_comparisons"]["graph_vs_text_fair"]["overall"]
    assert graph_vs_fair["paired_bootstrap_ci95"]["mean_diff"] == 0.0
    assert graph_vs_fair["left_only_correct"] == 0
    assert graph_vs_fair["right_only_correct"] == 0

    # Guarded verification detects faults that unguarded commits.
    graph_vs_unguarded = without["paired_comparisons"]["graph_vs_unguarded"][
        "faulty_only"
    ]
    assert graph_vs_unguarded["paired_bootstrap_ci95"]["mean_diff"] > 0.5
    assert graph_vs_unguarded["right_only_correct"] == 0
    assert graph_vs_unguarded["mcnemar_exact_p"] is not None

    # Transcript grounding closes the spoofed-evidence gap.
    assert (
        with_tg["conditions"]["graph"]["faulty_overall"]["detection"]["numerator"]
        == with_tg["conditions"]["graph"]["faulty_overall"]["detection"]["denominator"]
    )

    assert (tmp_path / "out" / "report.json").is_file()
    checkpoint = RunCheckpoint.load(
        tmp_path / "out" / CHECKPOINT_FILENAME,
        identity_fields=phase1.CHECKPOINT_IDENTITY_FIELDS,
    )
    assert checkpoint.is_complete is True


def test_phase1_report_contains_no_dialogue_or_claim_content(tmp_path):
    output = tmp_path / "out"
    phase1.run_halumem_phase1(output_dir=output, resamples=50, seed=5)
    text = (output / "report.json").read_text(encoding="utf-8")

    for raw in (
        "Hi, my name is Nadia",
        "Lives in Bandung",
        "Works as a backend engineer at Tokopedia",
        "Allergic to shrimp",
        "Owns a private Gulfstream jet",
    ):
        assert raw not in text

    assert "syn-user-1__s1" in text  # content-free identifiers are allowed


def test_phase1_runner_resume_reuses_checkpoint(tmp_path):
    output = tmp_path / "out"
    first = phase1.run_halumem_phase1(output_dir=output, resamples=40, seed=3)
    total_rows = first["checkpoint"]["totals"]["row_count"]
    assert first["checkpoint"]["executed_rows"] == total_rows
    assert first["checkpoint"]["resumed_rows"] == 0

    second = phase1.run_halumem_phase1(
        output_dir=output, resamples=40, seed=3, resume=True
    )
    assert second["checkpoint"]["resumed_rows"] == total_rows
    assert second["checkpoint"]["executed_rows"] == 0
    assert second["case_counts"] == first["case_counts"]


def test_phase1_runner_refuses_resume_with_changed_config(tmp_path):
    output = tmp_path / "out"
    phase1.run_halumem_phase1(output_dir=output, resamples=20, seed=1)
    with pytest.raises(CheckpointConfigMismatch):
        phase1.run_halumem_phase1(
            output_dir=output, resamples=20, seed=2, resume=True
        )


def test_phase1_runner_supports_supersede_operation(tmp_path):
    report = phase1.run_halumem_phase1(
        output_dir=tmp_path / "out",
        resamples=20,
        seed=1,
        update_operation="SUPERSEDE",
    )
    assert report["config"]["update_operation"] == "SUPERSEDE"
    assert report["case_counts"]["total"] > 0


# ---------------------------------------------------------------------------
# Phase 2: propose once, judge many
# ---------------------------------------------------------------------------


def test_phase2_propose_once_judge_many_is_paired_and_offline(tmp_path):
    path, transitions = _gold_transitions(tmp_path)
    stager, calls = _fake_stager(transitions)
    output = tmp_path / "out"

    report = phase2.run_halumem_phase2(
        halumem_path=path,
        output_dir=output,
        proposal_stager=stager,
        embedder_kind="hash",
        num_users=2,
        sessions_per_user=2,
        audit_sample_size=4,
        resamples=50,
        seed=21,
    )

    assert len(calls) == 4
    assert report["propose_once_judge_many"]["proposals_recorded"] == 4
    assert report["propose_once_judge_many"]["verifier_llm_calls"] == 0
    assert report["propose_once_judge_many"]["paired_on_identical_transitions"] is True
    assert report["budget"]["executed_sessions"] == 4
    assert report["budget"]["resumed_sessions"] == 0
    assert report["budget"]["aborted"] is False
    assert report["validation_status"] == "unvalidated_pending_human_audit"

    config = json.loads((output / "run-config.json").read_text(encoding="utf-8"))
    assert config["sampling"]["rule"] == "seeded_random_users_first_n_sessions_each"
    assert config["seed"] == 21
    assert config["sampling"]["sampled_session_count"] == 4
    assert config["budget_caps"]["concurrency"] == 1
    assert config["model"]["pricing"]["is_free"] is True
    assert config["embedder"]["kind"] == "hash"

    proposal_files = sorted((output / "proposals").glob("*.json"))
    assert len(proposal_files) == 4

    assert set(report["conditions"]) == {"unguarded", "graph", "text", "text_fair"}
    # Every condition is evaluated on the same recorded transitions.
    assert all(
        condition_metrics["proposals_evaluated"] == 4
        for condition_metrics in report["conditions"].values()
    )

    assert report["silver_vs_human_agreement"]["status"] == "pending_human_review"
    assert report["silver_vs_human_agreement"]["validated"] is False
    quiz = report["silver_vs_human_agreement"]["quiz_package"]
    assert Path(quiz["quiz_html_path"]).is_file()
    assert quiz["case_count"] == 4


def test_phase2_resume_does_not_call_proposal_stager_again(tmp_path):
    path, transitions = _gold_transitions(tmp_path)
    stager, calls = _fake_stager(transitions)
    output = tmp_path / "out"
    phase2.run_halumem_phase2(
        halumem_path=path,
        output_dir=output,
        proposal_stager=stager,
        embedder_kind="hash",
        num_users=1,
        sessions_per_user=2,
        audit_sample_size=2,
        resamples=20,
        seed=4,
    )
    assert len(calls) == 2

    def fail_if_called(*args, **kwargs):
        raise AssertionError("resume must replay recorded proposals, not re-propose")

    resumed = phase2.run_halumem_phase2(
        halumem_path=path,
        output_dir=output,
        proposal_stager=fail_if_called,
        embedder_kind="hash",
        num_users=1,
        sessions_per_user=2,
        audit_sample_size=2,
        resamples=20,
        seed=4,
        resume=True,
    )
    assert resumed["budget"]["resumed_sessions"] == 2
    assert resumed["budget"]["executed_sessions"] == 0


def test_phase2_hard_request_cap_stops_before_any_proposal(tmp_path):
    path, transitions = _gold_transitions(tmp_path)
    stager, calls = _fake_stager(transitions)
    report = phase2.run_halumem_phase2(
        halumem_path=path,
        output_dir=tmp_path / "out",
        proposal_stager=stager,
        embedder_kind="hash",
        num_users=2,
        sessions_per_user=2,
        max_requests=0,
        resamples=10,
        seed=8,
    )
    assert calls == []
    assert report["budget"]["aborted"] is True
    assert "max_requests" in report["budget"]["abort_reason"]
    assert report["natural_error_prevalence"]["total_proposals"] == 0


def test_phase2_zero_price_guard_blocks_paid_model_before_proposing(tmp_path):
    path, transitions = _gold_transitions(tmp_path)
    stager, calls = _fake_stager(transitions)

    class FakeClient:
        model = "deepseek/deepseek-v4.1-flash"
        base_url = "https://openrouter.ai/api/v1"
        provider_name = "openrouter-openai-compatible"

    catalog = [
        {
            "id": "deepseek/deepseek-v4.1-flash",
            "name": "DeepSeek V4.1 Flash",
            "pricing": {
                "prompt": "0.0000000498",
                "completion": "0.0000001992",
            },
        }
    ]
    with pytest.raises(OpenRouterPricingError, match="non-zero pricing"):
        phase2.run_halumem_phase2(
            halumem_path=path,
            output_dir=tmp_path / "out",
            llm_client=FakeClient(),
            openrouter_models_catalog=catalog,
            proposal_stager=stager,
            embedder_kind="hash",
            num_users=1,
            sessions_per_user=1,
            resamples=10,
        )
    assert calls == []


def test_phase2_data_policy_gate_requires_explicit_confirmation(capsys):
    assert phase2.require_data_policy_confirmation(
        confirmed_flag=False, is_synthetic_fixture=False, network_enabled=False
    )
    with pytest.raises(PermissionError, match="data-policy"):
        phase2.require_data_policy_confirmation(
            confirmed_flag=False, is_synthetic_fixture=False, network_enabled=True
        )
    stderr = capsys.readouterr().err
    assert "CC BY-NC-ND" in stderr
    assert "log" in stderr.lower()
    assert phase2.require_data_policy_confirmation(
        confirmed_flag=True, is_synthetic_fixture=False, network_enabled=True
    )


def test_phase2_sampling_rule_is_seeded_and_takes_first_sessions(tmp_path):
    path, _ = _gold_transitions(tmp_path)
    _, users = load_halumem_dataset(path, dataset_revision="test")
    first = phase2.sample_halumem_sessions(users, num_users=3, sessions_per_user=2, seed=9)
    second = phase2.sample_halumem_sessions(users, num_users=3, sessions_per_user=2, seed=9)

    assert [(u.uuid, s.session_id, i) for u, s, i in first] == [
        (u.uuid, s.session_id, i) for u, s, i in second
    ]
    assert len({u.uuid for u, _, _ in first}) == 3
    assert len(first) == 6
    for user, session, index in first:
        assert session is user.sessions[index - 1]


def test_phase2_fallback_transition_builder_uses_schema_version():
    from membud.schemas import SCHEMA_VERSION, Record

    before = (Record(id="r1", claims=("Prior claim",)),)
    transition = phase2._fallback_transition_from_outcome(
        "case-1",
        before,
        [
            {"event": "ADD", "memory": "Added claim", "id": "r2"},
            {"event": "UPDATE", "memory": "Revised claim", "id": "r1"},
        ],
        [],
    )
    assert transition.schema_version == SCHEMA_VERSION
    assert transition.case_id == "case-1"
    assert {op.type for op in transition.operations} == {"WRITE", "REVISE"}
    assert {r.id: r.claims for r in transition.candidate} == {
        "r1": ("Revised claim",),
        "r2": ("Added claim",),
    }


def test_phase2_grade_human_audit_roundtrip(tmp_path):
    path, transitions = _gold_transitions(tmp_path)
    stager, _ = _fake_stager(transitions)
    output = tmp_path / "out"
    phase2.run_halumem_phase2(
        halumem_path=path,
        output_dir=output,
        proposal_stager=stager,
        embedder_kind="hash",
        num_users=1,
        sessions_per_user=2,
        audit_sample_size=2,
        resamples=20,
        seed=4,
    )

    import hashlib

    manifest_path = output / "human-audit" / "audit-manifest.json"
    labels_path = output / "human-audit" / "audit-labels.jsonl"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    labels = {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in labels_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    answers = {
        "reviewer": "Test Reviewer",
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "answers": {
            case_id: {
                "decision": labels[case_id]["expected_decision"],
                "note": "matches the provisional silver label",
            }
            for case_id in manifest["case_ids"]
        },
    }
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8")

    agreement = phase2.grade_phase2_human_audit(
        output_dir=output, answers_path=answers_path
    )
    assert agreement["overall"]["answered"] == 2
    assert agreement["overall"]["agreements"] == 2
    assert agreement["coverage"]["blank"] == 0

    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["silver_vs_human_agreement"]["status"] == "graded"
    assert report["silver_vs_human_agreement"]["validated"] is True
    assert report["validation_status"] == "human_audit_graded"
