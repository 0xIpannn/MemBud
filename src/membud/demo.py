"""Run the bundled 11-case Indonesian-themed MemBud demonstration."""
from pathlib import Path
from uuid import uuid4

from .graph import to_mermaid
from .schemas import Transition
from .store import run_case


def cases():
    old = {"id": "r1", "claims": ["tinggal di Bandung"]}
    employment_evidence = [{
        "id": "e1",
        "source": "demo:employment",
        "text": "Ia bekerja di Tokopedia.",
        "supports": ["bekerja di Tokopedia"],
    }]
    relocation_evidence = [{
        "id": "e2",
        "source": "demo:relocation",
        "text": "Sekarang ia tinggal di Surabaya.",
        "supports": ["tinggal di Surabaya"],
    }]
    deletion_evidence = [{
        "id": "e3",
        "source": "demo:user-request",
        "text": "Pengguna meminta data lokasi dihapus.",
        "supports": [],
    }]
    deletion_authorization = {
        "source": "user_request",
        "evidence_id": "e3",
        "target_id": "r1",
        "rationale": "Pengguna meminta data lokasi dihapus.",
    }
    base = {
        "schema_version": 2,
        "before": [old],
        "evidence": employment_evidence,
        "required_claims": ["tinggal di Bandung"],
    }
    return [
        {
            **base,
            "case_id": "valid",
            "candidate": [old, {"id": "r2", "claims": ["bekerja di Tokopedia"]}],
            "operations": [{"type": "WRITE", "record_id": "r2"}],
        },
        {
            **base,
            "case_id": "omission",
            "candidate": [old],
            "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"],
            "operations": [{"type": "NOOP"}],
        },
        {
            **base,
            "case_id": "corruption",
            "candidate": [
                {"id": "r1", "claims": ["tinggal di Surabaya"]},
                {"id": "r2", "claims": ["bekerja di Tokopedia"]},
            ],
            "operations": [
                {"type": "REVISE", "record_id": "r1", "target_id": "r1"},
                {"type": "WRITE", "record_id": "r2"},
            ],
        },
        {
            **base,
            "case_id": "unsupported",
            "candidate": [old, {"id": "r2", "claims": ["memiliki kapal pesiar"]}],
            "evidence": [],
            "operations": [{"type": "WRITE", "record_id": "r2"}],
        },
        {
            **base,
            "case_id": "valid-noop",
            "candidate": [old],
            "evidence": [],
            "operations": [{"type": "NOOP"}],
        },
        {
            **base,
            "case_id": "preserve-added-claim",
            "candidate": [{"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]}],
            "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
        },
        {
            **base,
            "case_id": "remove-record",
            "candidate": [],
            "evidence": deletion_evidence,
            "required_claims": [],
            "operations": [{
                "type": "DELETE",
                "target_id": "r1",
                "reason": "permintaan pengguna",
                "authorization": deletion_authorization,
            }],
        },
        {
            **base,
            "case_id": "required-claim-deletion",
            "candidate": [],
            "evidence": deletion_evidence,
            "required_claims": ["tinggal di Bandung"],
            "operations": [{
                "type": "DELETE",
                "target_id": "r1",
                "reason": "permintaan pengguna",
                "authorization": deletion_authorization,
            }],
        },
        {
            **base,
            "case_id": "supported-revision",
            "candidate": [{"id": "r1", "claims": ["tinggal di Surabaya"]}],
            "evidence": relocation_evidence,
            "required_claims": ["tinggal di Surabaya"],
            "operations": [{"type": "REVISE", "record_id": "r1", "target_id": "r1"}],
        },
        {
            **base,
            "case_id": "explicit-supersession",
            "candidate": [{"id": "r2", "claims": ["tinggal di Surabaya"]}],
            "evidence": relocation_evidence,
            "required_claims": ["tinggal di Surabaya"],
            "operations": [{
                "type": "SUPERSEDE",
                "record_id": "r2",
                "target_id": "r1",
                "reason": "lokasi terbaru",
            }],
        },
        {
            **base,
            "case_id": "unsupported-plus-valid",
            "candidate": [old, {"id": "r2", "claims": ["bekerja di Tokopedia", "adalah pilot"]}],
            "operations": [{"type": "WRITE", "record_id": "r2"}],
        },
    ]


def main():
    output = Path("membud-output")
    output.mkdir(exist_ok=True)
    run_id = str(uuid4())
    # Each case gets isolated state; both verifier paths are run and logged.
    for raw in cases():
        t = Transition.from_dict(raw)
        diagram = to_mermaid(t)
        (output / f"{t.case_id}.mmd").write_text(diagram, encoding="utf-8")
        if raw["case_id"] == "valid":
            # Documentation alias, not an additional evaluation fixture.
            (output / "example-graph.mmd").write_text(diagram, encoding="utf-8")
            (output / "example-graph.md").write_text(
                "# Example transition graph\n\n```mermaid\n" + diagram + "```\n",
                encoding="utf-8",
            )
        for mode in ("graph", "text"):
            # Stores are current state, not a log: reset before replaying a case.
            store_file = output / f"{t.case_id}-{mode}.json"
            store_file.unlink(missing_ok=True)
            run_case(
                t,
                store_file,
                output / "audit.jsonl",
                mode,
                run_id=run_id,
            )
    print(f"Ran {len(cases())} cases through graph and text paths; audit: {output / 'audit.jsonl'}")


if __name__ == "__main__":
    main()
