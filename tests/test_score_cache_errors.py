import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("score_official", ROOT / "scripts" / "score_halumem_official.py")
so = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(so)


def test_load_cache_drops_transport_failures(tmp_path):
    p = tmp_path / "judge-results.jsonl"
    rows = [
        {"key": "A|c|0", "kind": "accuracy", "score": 2, "included": True},
        {"key": "A|c|0", "kind": "accuracy", "score": None, "included": None, "error": "LLMRequestError"},
        {"key": "I|c|1", "kind": "integrity", "score": None, "included": None, "error": "LLMRequestError"},
        {"key": "I|c|2", "kind": "integrity", "score": None, "included": None, "error": "LLMRequestError"},
        {"key": "I|c|2", "kind": "integrity", "score": 1, "included": None},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    cache = so.load_cache(p)
    assert cache["A|c|0"]["score"] == 2  # a later failure does not erase a good judgment
    assert "I|c|1" not in cache  # failure only -> missing, so --run retries it
    assert cache["I|c|2"]["score"] == 1  # retry after failure is kept
