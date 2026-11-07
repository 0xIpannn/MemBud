"""Guarded JSON persistence and append-only audit logging."""
import json
from dataclasses import asdict
from pathlib import Path

from .schemas import Record, Result, Transition
from .serialization import transition_dict
from .verification import verify


def commit(store: dict[str, Record], t: Transition, result: Result) -> dict[str, Record]:
    """Replace store only on ACCEPT; return a copy and never mutate input."""
    if result.case_id != t.case_id:
        raise ValueError("result does not belong to transition")
    return {r.id: r for r in t.candidate} if result.decision == "ACCEPT" else dict(store)


def run_case(
    t: Transition,
    store_path: str | Path,
    log_path: str | Path,
    representation: str = "graph",
) -> Result:
    """Load a JSON store, verify, conditionally commit, and append a JSONL audit event."""
    store_path, log_path = Path(store_path), Path(log_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if store_path.exists():
        store = {
            r.id: r
            for r in (
                Record.from_dict(x)
                for x in json.loads(store_path.read_text(encoding="utf-8"))
            )
        }
    else:
        store = {r.id: r for r in t.before}
    result = verify(t, representation)
    after = commit(store, t, result)
    store_path.write_text(
        json.dumps([asdict(r) for r in after.values()], indent=2) + "\n",
        encoding="utf-8",
    )
    event = {
        "input": transition_dict(t),
        "representation": representation,
        "result": result.to_dict(),
        "store_after": [asdict(r) for r in after.values()],
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return result
