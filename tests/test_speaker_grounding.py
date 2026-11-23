from membud.adapters.mem0_staged import LexicalGrounding, SpeakerNormalizedGrounding

SRC = [("messages[0]:user", "I adopted a dog named Biscuit. I work at Acme Corp as an engineer. My favorite food is ramen.")]
G = SpeakerNormalizedGrounding()


def supported(claim, sources=SRC, policy=G):
    return bool(policy([claim], sources))


def test_third_person_user_rewrite_is_grounded():
    assert supported("User adopted a dog named Biscuit.")
    assert supported("User's favorite food is ramen.")


def test_plain_policy_still_holds_the_rewrite():
    assert not supported("User adopted a dog named Biscuit.", policy=LexicalGrounding())


def test_invented_details_are_still_held():
    assert not supported("User adopted a dog named Biscuit in Berlin.")
    assert not supported("User has a peanut allergy (p ~ 0.99).")
    assert not supported("User adopted a cat named Biscuit.")


def test_synonyms_are_not_invented():
    assert not supported("User is employed at Acme Corp as an engineer.")


def test_a_claim_with_only_the_subject_is_not_support():
    assert not supported("User.")


def test_no_user_message_no_support():
    assert not supported("User adopted a dog named Biscuit.", sources=[("messages[0]:user", "Hello there")])
