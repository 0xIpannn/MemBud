"""run_halumem_live.process_user: official eval_memzero procedure and layout, checked against the official evaluation.py."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("run_halumem_live", ROOT / "scripts" / "run_halumem_live.py")
live = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_halumem_live", live)
_spec.loader.exec_module(live)
REPO = Path(os.environ.get("HALUMEM_REPO", Path.home() / "Repositories" / "HaluMem-repo"))


def _mp(content, *, update="False", original=(), source="dialogue", mtype="Persona Memory"):
    return {"memory_content": content, "memory_source": source, "importance": 1.0, "memory_type": mtype,
            "is_update": update, "original_memories": list(original)}


def _turn(role, content):
    return {"role": role, "content": content, "timestamp": "Jan 02, 2025, 10:00:00"}


USER = {
    "uuid": "u-1", "persona_info": "Name: Alex Doe; Gender: other; Age: 30",
    "sessions": [
        {"start_time": "Jan 02, 2025, 10:00:00", "dialogue": [_turn("user", "I like tea."), _turn("assistant", "Nice.")],
         # all three memory types: the official aggregation divides by each type's count
         "memory_points": [_mp("Alex likes tea"), _mp("Alex owns a boat", source="interference", mtype="Event Memory"),
                           _mp("Alex has a sister", mtype="Relationship Memory")]},
        {"start_time": "Feb 03, 2025, 11:30:00", "dialogue": [_turn("user", "I now prefer coffee.")],
         "memory_points": [_mp("Alex prefers coffee", update="True", original=["Alex likes tea"]),
                           _mp("Alex has a cat", update="True", original=[])],
         "questions": [{"question": "What does Alex drink?", "answer": "coffee", "evidence": [{"memory_content": "Alex prefers coffee"}]}]},
        {"start_time": "Mar 01, 2025, 09:00:00", "dialogue": [_turn("user", "Hi")], "memory_points": [],
         "is_generated_qa_session": True},
        {"start_time": "Apr 01, 2025, 09:00:00", "dialogue": [_turn("user", "beyond the cap")], "memory_points": []},
    ],
}


class FakeSystem:
    def __init__(self):
        self.calls = []

    def reset(self, user_id):
        self.calls.append(("reset", user_id))

    def add(self, messages, user_id, timestamp, case_id):
        self.calls.append(("add", user_id, timestamp, case_id, len(messages)))
        return [f"{user_id} wrote {case_id}"]

    def search(self, query, user_id, top_k):
        self.calls.append(("search", query, top_k))
        return [{"memory": f"hit for {query}", "created_at": "2025-02-03T11:30:00+00:00", "score": 0.9}]


def _run(cap=3):
    sys_, prompts = FakeSystem(), []
    out = live.process_user(USER, sys_, lambda p: prompts.append(p) or "coffee", template="Memories for user {user_id}:\n{memories}",
                            prompt="CTX={context} Q={question}", sessions_cap=cap)
    return out, sys_, prompts


def test_procedure_and_layout():
    out, sys_, prompts = _run()
    assert out["user_name"] == "Alex Doe" and out["uuid"] == "u-1"
    assert len(out["sessions"]) == 3  # sessions beyond the cap are not added
    s1, s2, s3 = out["sessions"]
    assert s1["extracted_memories"] == ["Alex Doe wrote u-1__s1"] and "questions" not in s1
    assert ("add", "Alex Doe", 1735812000, "u-1__s1", 2) in sys_.calls  # session start time as UTC epoch
    # update search only for is_update == "True" with original memories, top 10
    upd = s2["memory_points"]
    assert upd[0]["memories_from_system"] == ["2025-02-03T11:30:00+00:00: hit for Alex prefers coffee"]
    assert "memories_from_system" not in upd[1]
    assert ("search", "Alex prefers coffee", 10) in sys_.calls and ("search", "What does Alex drink?", 20) in sys_.calls
    q = s2["questions"][0]
    assert q["system_response"] == "coffee" and "Memories for user Alex Doe" in q["context"]
    assert prompts == [f"CTX={q['context']} Q=What does Alex drink?"]
    assert s3 == {"add_dialogue_duration_ms": s3["add_dialogue_duration_ms"], "is_generated_qa_session": True}
    assert "memories_from_system" not in USER["sessions"][1]["memory_points"][0]  # input not mutated


@pytest.mark.skipif(not (REPO / "eval" / "evaluation.py").exists(), reason="HaluMem reference clone not available")
def test_official_evaluation_accepts_the_output(tmp_path, monkeypatch):
    for mod in ("openai", "tenacity", "tqdm", "dotenv"):
        pytest.importorskip(mod)
    out, _, _ = _run()
    base = tmp_path / "results" / "memzero-test"
    base.mkdir(parents=True)
    (base / "memzero_eval_results.jsonl").write_text(json.dumps(out) + "\n")
    shim = live.shim
    meter = shim.Meter(0.0, 0.0, cap_usd=1.0)
    mods = shim.load_official(REPO, base_url="http://127.0.0.1:9", api_key="offline", model="fake", meter=meter)
    ev = mods["evaluation"]
    calls = []
    monkeypatch.setattr(ev, "evaluation_for_memory_integrity", lambda ext, m: calls.append("I") or {"score": 2})
    monkeypatch.setattr(ev, "evaluation_for_memory_accuracy", lambda d, g, m: calls.append("A") or
                        {"accuracy_score": 2, "is_included_in_golden_memories": "true"})
    monkeypatch.setattr(ev, "evaluation_for_update_memory", lambda s, m, o: calls.append("U") or {"evaluation_result": "Correct"})
    monkeypatch.setattr(ev, "evaluation_for_question", lambda q, a, e, r: calls.append("Q") or {"evaluation_result": "Correct"})
    monkeypatch.chdir(tmp_path)
    ev.main("memzero", "test", user_num=1, max_workers=2)
    stat = json.loads((base / "memzero_eval_stat_result.json").read_text())["overall_score"]
    assert sorted(calls) == ["A", "A", "I", "I", "I", "I", "Q", "U"]  # 2 extracted; 4 integrity (incl. update w/o originals); 1 update; 1 QA
    assert stat["memory_update"]["correct_update_memory_ratio(all)"] == 1.0
    assert stat["question_answering"]["correct_qa_ratio(all)"] == 1.0
    assert meter.calls == 0  # fakes only: no request reached the client


def test_retrying_llm_retries_then_succeeds_and_gives_up():
    waits = []

    class Flaky:
        call_records = ["kept"]

        def __init__(self, fails):
            self.fails = fails

        def generate_response(self, messages, response_format=None, **kw):
            if self.fails:
                self.fails -= 1
                raise RuntimeError("HTTP 429")
            return "ok"

    r = live.RetryingLLM(Flaky(2), attempts=4, wait_s=1, sleep=waits.append)
    assert r.generate_response([]) == "ok" and r.retries == 2 and waits == [1, 2]
    assert r.call_records == ["kept"]  # attributes pass through to the wrapped adapter
    with pytest.raises(RuntimeError):
        live.RetryingLLM(Flaky(9), attempts=3, wait_s=0, sleep=lambda s: None).generate_response([])
