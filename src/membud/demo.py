"""Run the bundled 10-case Indonesian-themed local MVP demonstration."""
from pathlib import Path

from .core import Transition, run_case, to_mermaid


def cases():
    old = {"id": "r1", "claims": ["tinggal di Bandung"]}
    evidence = [{"id": "e1", "supports": ["bekerja di Tokopedia"]}]
    base = {"before": [old], "evidence": evidence, "required_claims": ["tinggal di Bandung"]}
    return [
        {**base, "case_id": "valid", "candidate": [old, {"id": "r2", "claims": ["bekerja di Tokopedia"]}]},
        {**base, "case_id": "omission", "candidate": [old], "required_claims": ["tinggal di Bandung", "bekerja di Tokopedia"]},
        {**base, "case_id": "corruption", "candidate": [{"id": "r1", "claims": ["tinggal di Surabaya"]}, {"id": "r2", "claims": ["bekerja di Tokopedia"]}]},
        {**base, "case_id": "unsupported", "candidate": [old, {"id": "r2", "claims": ["memiliki kapal pesiar"]}]},
        {**base, "case_id": "valid-noop", "candidate": [old]},
        {**base, "case_id": "preserve-added-claim", "candidate": [{"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]}]},
        {**base, "case_id": "remove-record", "candidate": [{"id": "r2", "claims": ["bekerja di Tokopedia"]}]},
        {**base, "case_id": "supported-revision", "candidate": [{"id": "r1", "claims": ["tinggal di Bandung", "bekerja di Tokopedia"]}]},
        {**base, "case_id": "explicit-supersession", "candidate": [{"id": "r2", "claims": ["bekerja di Tokopedia"]}], "supersedes": ["r1"], "required_claims": ["bekerja di Tokopedia"]},
        {**base, "case_id": "unsupported-plus-valid", "candidate": [old, {"id": "r2", "claims": ["bekerja di Tokopedia", "adalah pilot"]}]},
    ]


def main():
    output = Path("mvp-output")
    output.mkdir(exist_ok=True)
    # Each case gets isolated state; both verifier paths are run and logged.
    for raw in cases():
        t = Transition.from_dict(raw)
        diagram = to_mermaid(t)
        (output / f"{t.case_id}.mmd").write_text(diagram)
        if raw["case_id"] == "valid":
            (output / "example-graph.mmd").write_text(diagram)
            (output / "example-graph.md").write_text(
                "# Example transition graph\n\n```mermaid\n" + diagram + "```\n"
            )
        for mode in ("graph", "text"):
            run_case(t, output / f"{t.case_id}-{mode}.json", output / "audit.jsonl", mode)
    print(f"Ran {len(cases())} cases through graph and text paths; audit: {output / 'audit.jsonl'}")


if __name__ == "__main__":
    main()
