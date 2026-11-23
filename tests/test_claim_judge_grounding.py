from types import SimpleNamespace

from membud.adapters.claim_judge_grounding import ClaimJudgeGrounding, judge_messages, parse_verdicts


def _judge(payload):
    seen = []

    def judge(messages):
        seen.append(messages)
        return SimpleNamespace(json=payload, input_tokens=10, output_tokens=5)
    return judge, seen


DIALOGUE = [{"role": "user", "content": "I like tea."}, {"role": "assistant", "content": "Nice."},
            {"role": "user", "content": "I live in Oslo."}]
SOURCES = [("messages[0]:user", "I like tea."), ("messages[2]:user", "I live in Oslo.")]


def test_supported_claims_get_evidence_citing_user_turns():
    judge, seen = _judge({"claims": [{"i": 0, "verdict": "SUPPORTED", "turns": [0]},
                                     {"i": 1, "verdict": "UNSUPPORTED", "turns": []},
                                     {"i": 2, "verdict": "SUPPORTED", "turns": [2, 1, "x"]}]})
    g = ClaimJudgeGrounding(judge=judge, system_prompt="SYS")
    g.set_context(DIALOGUE, ["User is an engineer"])
    ev = g(["User likes tea", "User lives on Mars", "User lives in Oslo"], SOURCES)
    assert [e["supports"] for e in ev] == [["User likes tea"], ["User lives in Oslo"]]
    assert ev[0]["source"] == "messages[0]:user"
    assert ev[1]["source"] == "messages[2]:user"  # turn 1 is the assistant, not a trusted source; "x" ignored
    assert g.last_verdicts == {"User likes tea": True, "User lives on Mars": False, "User lives in Oslo": True}
    user_msg = seen[0][1]["content"]
    assert seen[0][0] == {"role": "system", "content": "SYS"}
    assert "[1] [assistant] Nice." in user_msg and "User is an engineer" in user_msg and "1. User lives on Mars" in user_msg


def test_malformed_or_missing_verdicts_fail_closed():
    for payload in ({"claims": "oops"}, {}, None, {"claims": [{"i": 7, "verdict": "SUPPORTED"}, {"verdict": "SUPPORTED"}]}):
        judge, _ = _judge(payload)
        g = ClaimJudgeGrounding(judge=judge, system_prompt="SYS")
        g.set_context(DIALOGUE, [])
        assert g(["User likes tea"], SOURCES) == []
        assert g.last_verdicts == {"User likes tea": False}


def test_no_claims_makes_no_call_and_supported_without_user_turn_is_marked():
    judge, seen = _judge({"claims": [{"i": 0, "verdict": "SUPPORTED", "turns": []}]})
    g = ClaimJudgeGrounding(judge=judge, system_prompt="SYS")
    assert g([], SOURCES) == [] and not seen
    ev = g(["User likes tea"], SOURCES)
    assert ev[0]["source"] == "claim-judge:no-user-turn-cited"


def test_message_layout_matches_batch_claim_judge():
    m = judge_messages("S", DIALOGUE, [], ["a", "b"])
    assert m[1]["content"].endswith("Earlier memory:\n(empty)\n\nClaims:\n0. a\n1. b")
    assert parse_verdicts({"claims": [{"i": "1", "verdict": "SUPPORTED", "turns": ["2"]}]}, 2) == ({1: True}, {1: [2]})
