"""Opt-in reply-context transcript grounding (prototype): default behaviour must not change."""
from __future__ import annotations

from membud.conversation import ConversationTranscript, ConversationTurn, verify_transcript_grounding, verify_with_transcript
from membud.schemas import Evidence, Operation, Record, Transition

CLAIM = "monthly income increased to 9500 dollars"


def _transcript(user_reply="That really helps, thank you."):
    turns = (
        ConversationTurn("t0", "user", "I got a promotion at work."),
        ConversationTurn("t1", "assistant", "Congratulations! Your monthly income increased to 9500 dollars, which is great."),
        ConversationTurn("t2", "user", user_reply),
        ConversationTurn("t3", "assistant", "Anything else?"),
    )
    return ConversationTranscript("c", turns)


def _transition(cited_turn="t2", claim=CLAIM, quote=None):
    t = _transcript()
    turn = t.turn_by_id(cited_turn)
    return Transition(
        case_id="rc",
        before=(),
        candidate=(Record("r1", (claim,)),),
        evidence=(Evidence("e1", (claim,), turn.source_ref, quote or turn.content, ()),),
        required_claims=(),
        operations=(Operation("WRITE", "r1"),),
    )


def test_default_holds_when_cited_user_reply_shares_no_words_with_claim():
    chk = verify_transcript_grounding(_transition(), _transcript())
    assert not chk.passed and chk.reason_code == "UNGROUNDED_EVIDENCE_CLAIM"


def test_reply_context_accepts_when_claim_is_in_the_assistant_turn_replied_to():
    chk = verify_transcript_grounding(_transition(), _transcript(), allow_reply_context=True)
    assert chk.passed and chk.reason_code == "TRANSCRIPT_GROUNDING_OK"


def test_reply_context_does_not_help_a_claim_absent_from_the_assistant_turn():
    t = _transition(claim="owns a yacht in Monaco")
    assert not verify_transcript_grounding(t, _transcript(), allow_reply_context=True).passed


def test_reply_context_needs_the_cited_turn_to_directly_follow_the_assistant_turn():
    # cited turn t0 has no preceding assistant turn; t3 is assistant (not a user reply)
    for cited in ("t0", "t3"):
        t = _transition(cited_turn=cited)
        assert not verify_transcript_grounding(t, _transcript(), allow_reply_context=True).passed


def test_quote_and_role_rules_are_unchanged_by_the_flag():
    bad_quote = _transition(quote="something the user never said")
    assert verify_transcript_grounding(bad_quote, _transcript(), allow_reply_context=True).reason_code == "UNGROUNDED_EVIDENCE_QUOTE"
    assistant_cited = _transition(cited_turn="t1")
    assert verify_transcript_grounding(assistant_cited, _transcript(), allow_reply_context=True).reason_code == "DISALLOWED_EVIDENCE_ROLE"


def test_verify_with_transcript_threads_the_flag_and_defaults_off():
    t, tr = _transition(), _transcript()
    assert verify_with_transcript(t, tr, "graph").decision == "HOLD"
    assert verify_with_transcript(t, tr, "graph", allow_reply_context=True).decision == "ACCEPT"
    assert verify_with_transcript(t, tr, "text", fair_text=True, allow_reply_context=True).decision == "ACCEPT"
