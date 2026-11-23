import re

import pytest

from membud.memagentbench import CONDITIONS, Store, chunked, ingest, load_rag_template, load_scorer, parse_facts, rank

CTX = "Here is a list of facts:\n0. Ann is married to Bob.\n1. Cy lives in Rome.\n2. Ann is married to Dee.\n3. Eve plays chess.\n"


def test_parse_and_chunk():
    f = parse_facts(CTX)
    assert f[0] == (0, "Ann is married to Bob.") and len(f) == 4
    assert chunked(f, 3) == [f[:3], f[3:]]
    with pytest.raises(ValueError):
        chunked(f, 0)


def _key(text):
    return re.sub(r" (is married to|lives in|plays) .*", "", text)


def find_conflicts(new, pool):
    return [(n, o) for n, nt in new for o, ot in pool if o < n and _key(nt) == _key(ot) and nt != ot]


def test_ingest_unguarded_guard_repair_differ_on_stale_fact():
    stores, stats = ingest(parse_facts(CTX), find_conflicts, lambda info: [o[0] for _, o in info], chunk_size=2)
    texts = {c: {t for _, t in stores[c].active()} for c in CONDITIONS}
    assert "Ann is married to Bob." in texts["unguarded"] and "Ann is married to Dee." in texts["unguarded"]
    # guard: the conflicting NEW fact (serial 2) is held, so the stale value stays and the new one is lost
    assert "Ann is married to Dee." not in texts["guard"] and "Ann is married to Bob." in texts["guard"]
    # guard + repair: stale record superseded, new fact committed
    assert texts["guard_repair"] == {"Cy lives in Rome.", "Ann is married to Dee.", "Eve plays chess."}
    assert stats["guard_repair"]["chunks_recovered"] == 1 and stats["guard_repair"]["superseded"] == 1
    assert stats["guard"]["dropped_adds"] == 1


def test_failed_repair_falls_back_to_guard_behaviour():
    stores, stats = ingest(parse_facts(CTX), find_conflicts, lambda info: [], chunk_size=2)  # chooser never supersedes
    assert {t for _, t in stores["guard_repair"].active()} == {t for _, t in stores["guard"].active()}
    assert stats["guard_repair"]["chunks_recovered"] == 0 and stats["guard_repair"]["repair_attempts"] == 2


def test_conflict_inside_one_chunk_is_found():
    stores, _ = ingest(parse_facts(CTX), find_conflicts, lambda info: [o[0] for _, o in info], chunk_size=4)
    assert {t for _, t in stores["guard_repair"].active()} == {"Cy lives in Rome.", "Ann is married to Dee.", "Eve plays chess."}


def test_rank_prefers_rare_token_overlap_and_store_supersede():
    facts = parse_facts(CTX)
    assert rank("who is Ann married to", facts, 2)[0][1].startswith("Ann")
    assert rank("x", [], 3) == []
    s = Store()
    s.add(1, "a"); s.add(2, "b"); s.supersede(1); s.supersede(99)
    assert s.active() == [(2, "b")]


def test_reference_loaders_read_clone_without_importing(tmp_path):
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "eval_other_utils.py").write_text(
        "import numpy_not_installed\n"
        "def normalize_answer(t):\n    t=t.lower()\n    t=''.join(c for c in t if c not in string.punctuation)\n"
        "    t=re.sub(r'\\b(a|an|the)\\b',' ',t)\n    return ' '.join(t.split())\n"
        "def substring_exact_match_score(p,g):\n    return normalize_answer(g) in normalize_answer(p)\n"
        "def drqa_metric_max_over_ground_truths(f,p,gs):\n    return max(f(p,g) for g in gs)\n")
    (tmp_path / "utils" / "templates.py").write_text(
        "S='x'\nBASE_TEMPLATES={'factconsolidation':{'system':S,'query':{'rag_agent':'Q: {question}'}}}\n")
    score = load_scorer(tmp_path)
    assert score("The answer is Rodez.", ["rodez"]) and not score("Paris", ["Rodez"])
    assert load_rag_template(tmp_path) == "Q: {question}"
