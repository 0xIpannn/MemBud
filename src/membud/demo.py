"""Run the bundled 10-case local MVP demonstration."""
from pathlib import Path
import json
import tempfile

from .core import Transition, run_case


def cases():
    old = {"id": "r1", "claims": ["lives in Oslo"]}
    evidence = [{"id": "e1", "supports": ["works at Acme"]}]
    base = {"before": [old], "evidence": evidence, "required_claims": ["lives in Oslo"]}
    return [
        {**base, "case_id": "valid", "candidate": [old, {"id": "r2", "claims": ["works at Acme"]}]},
        {**base, "case_id": "omission", "candidate": [{"id": "r1", "claims": ["lives in Oslo"]}], "required_claims": ["lives in Oslo", "works at Acme"]},
        {**base, "case_id": "corruption", "candidate": [{"id": "r1", "claims": ["lives in Paris"]}, {"id": "r2", "claims": ["works at Acme"]}]},
        {**base, "case_id": "unsupported", "candidate": [old, {"id": "r2", "claims": ["owns a yacht"]}]},
        {**base, "case_id": "valid-noop", "candidate": [old]},
        {**base, "case_id": "preserve-added-claim", "candidate": [{"id": "r1", "claims": ["lives in Oslo", "works at Acme"]}]},
        {**base, "case_id": "remove-record", "candidate": [{"id": "r2", "claims": ["works at Acme"]}]},
        {**base, "case_id": "supported-revision", "candidate": [{"id": "r1", "claims": ["lives in Oslo", "works at Acme"]}]},
        {**base, "case_id": "explicit-supersession", "candidate": [{"id": "r2", "claims": ["works at Acme"]}], "supersedes": ["r1"], "required_claims": ["works at Acme"]},
        {**base, "case_id": "unsupported-plus-valid", "candidate": [old, {"id": "r2", "claims": ["works at Acme", "is a pilot"]}]},
    ]


def main():
    output = Path("mvp-output")
    output.mkdir(exist_ok=True)
    # Each case gets isolated state; both verifier paths are run and logged.
    for raw in cases():
        t = Transition.from_dict(raw)
        for mode in ("graph", "text"):
            run_case(t, output / f"{t.case_id}-{mode}.json", output / "audit.jsonl", mode)
    print(f"Ran {len(cases())} cases through graph and text paths; audit: {output / 'audit.jsonl'}")


if __name__ == "__main__":
    main()
