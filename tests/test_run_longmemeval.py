import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("run_lme", ROOT / "scripts" / "run_longmemeval.py")
lme = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lme)

FAKE_EVAL = '''
def get_anscheck_prompt(task, question, answer, response, abstention=False):
    return "JUDGE|{}|{}".format(answer, response)
'''


class FakeClient:
    """Deterministic stand-in: extractor invents one claim, verifier flags it, repair deletes it."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, messages, validator=None):
        self.calls += 1
        sys_, user = messages[0]["content"], messages[-1]["content"]
        if sys_.startswith("You write the memories"):
            j = {"claims": ["User has a dog named Rex", "User owns a spaceship"]}
        elif sys_.startswith("You verify claims"):
            body = user.split("Claims:\n")[1]
            j = {"claims": [{"i": int(l.split(".")[0]), "verdict": "UNSUPPORTED" if "spaceship" in l else "SUPPORTED", "turns": [0]}
                            for l in body.splitlines()]}
        elif sys_.startswith("You correct"):
            cur = user.split("Current claims:\n")[1].split("\n\nFlagged")[0]
            j = {"claims": [l.split(". ", 1)[1] for l in cur.splitlines() if "spaceship" not in l]}
        elif user.startswith("JUDGE"):
            response = user.split("\n")[0].split("|")[2]
            j = {"label": "yes" if "Rex" in response else "no"}
        else:
            j = {"answer": "Rex" if "dog named Rex" in user else "unknown"}
        if validator:
            validator(j)
        return SimpleNamespace(json=j, input_tokens=10, output_tokens=5)


def test_conditions_differ_exactly_as_designed(tmp_path):
    (tmp_path / "src" / "evaluation").mkdir(parents=True)
    (tmp_path / "src" / "evaluation" / "evaluate_qa.py").write_text(FAKE_EVAL)
    anscheck = lme.load_anscheck(tmp_path)
    q = {"question_id": "q1", "question_type": "single-session-user", "question": "What is the dog's name?", "answer": "Rex",
         "question_date": "2023/05/01 (Mon) 10:00", "haystack_dates": ["2023/01/01 (Sun) 10:00"],
         "haystack_sessions": [[{"role": "user", "content": "I have a dog named Rex"}, {"role": "assistant", "content": "nice"}]]}
    client = FakeClient()
    st = lme.Stages(tmp_path / "stages.jsonl", client, (1e-6, 1e-6), 1.0)
    stores, stats = lme.ingest_question(q, st)
    assert len(stores["unguarded"]) == 2                       # everything stored
    assert stores["guard"] == []                               # session with an unsupported claim is held
    assert stores["guard_repair"] == ["[2023/01/01 (Sun) 10:00] User has a dog named Rex"]  # repaired, spaceship dropped
    assert stats["held"] == 1 and stats["recovered"] == 1
    res = lme.answer_and_judge(q, stores, st, anscheck, 10)
    assert res["unguarded"]["correct"] and res["guard_repair"]["correct"] and not res["guard"]["correct"]
    # checkpoint: a second pass makes no further calls
    n = client.calls
    lme.ingest_question(q, lme.Stages(tmp_path / "stages.jsonl", client, (1e-6, 1e-6), 1.0))
    assert client.calls == n


def test_spend_cap_stops_before_the_next_call(tmp_path):
    st = lme.Stages(tmp_path / "s.jsonl", FakeClient(), (1e-3, 1e-3), 0.01)  # 15 tokens * 1e-3 = 0.015 > cap
    st.call("k1", [{"role": "user", "content": "x"}], None)
    try:
        st.call("k2", [{"role": "user", "content": "y"}], None)
    except RuntimeError as exc:
        assert "cap" in str(exc)
    else:
        raise AssertionError("cap not enforced")


def test_pilot_sample_is_stratified_and_seeded():
    data = [{"question_id": f"{t}{i}", "question_type": t} for t in lme.QUESTION_TYPES for i in range(10)]
    s = lme.pilot_sample(data, 12, 1)
    assert len(s) == 12 and {q["question_type"] for q in s} == set(lme.QUESTION_TYPES)
    assert [q["question_id"] for q in s] == [q["question_id"] for q in lme.pilot_sample(data, 12, 1)]
