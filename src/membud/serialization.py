"""Lossless serialization of canonical transitions."""
import json
from dataclasses import asdict
from typing import Any

from .schemas import Transition


def textual_representation(t: Transition) -> str:
    """Serialize all verifier inputs as deterministic, compact JSON text."""
    return json.dumps(transition_dict(t), ensure_ascii=False, sort_keys=True)


def transition_dict(t: Transition) -> dict[str, Any]:
    """Convert a transition to its JSON-compatible canonical dictionary form."""
    return {
        "case_id": t.case_id,
        "before": [asdict(r) for r in t.before],
        "candidate": [asdict(r) for r in t.candidate],
        "evidence": [asdict(e) for e in t.evidence],
        "required_claims": list(t.required_claims),
        "operations": [asdict(operation) for operation in t.operations],
    }
