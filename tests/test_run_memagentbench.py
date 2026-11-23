import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("run_mab", ROOT / "scripts" / "run_memagentbench.py")
mab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mab)

CTX = "Here is a list of facts:\n0. Ann is married to Bob.\n1. Cy lives in Rome.\n2. Ann is married to Dee.\n"


class FakeClient:
    """Deterministic stand-in: conflicts = same subject (first word) with a different line; supersede the OLD side."""

    def __init__(self):
        self.calls = 0

    def complete_json(self, messages, validator=None):
        self.calls += 1
        sys_, user = messages[0]["content"], messages[-1]["content"]
        if sys_.startswith("You maintain"):
            new_part, _, ex_part = user.partition("\n\nEXISTING facts:\n")
            new = re.findall(r"\[(\d+)\] (\S+) .*", new_part)
            ex = re.findall(r"\[(\d+)\] (\S+) .*", ex_part)
            j = {"conflicts": [{"new": int(n), "old": int(o)} for n, sn in new for o, so in ex if sn == so and int(o) < int(n)]}
        elif sys_.startswith("You repair"):
            j = {"supersede": [int(x) for x in re.findall(r"OLD \[(\d+)\]", user)]}
        else:
            facts = dict(re.findall(r"^(\d+)\. (.*)$", user, re.M))
            j = {"answer": "Dee" if any("Dee" in t for t in facts.values()) else "Bob"}
        if validator:
            validator(j)
        return SimpleNamespace(json=j, input_tokens=100, output_tokens=10)


def test_pipeline_wiring_end_to_end_with_fake_client(tmp_path):
    st = mab.Stages(tmp_path / "stages.jsonl", FakeClient(), (3e-7, 1.2e-6), 1.0)
    fc, cs = mab.make_llm_fns(st, tmp_path / "conflicts.jsonl")
    stores, stats = mab.ingest(mab.parse_facts(CTX), fc, cs, chunk_size=2, max_retries=2)
    tpl = "Based on the Knowledge Pool, {question} \nAnswer:"
    ans = {c: mab.answer(st, tpl, stores[c], "who is Ann married to", 5) for c in mab.CONDITIONS}
    assert ans == {"unguarded": "Dee", "guard": "Bob", "guard_repair": "Dee"}  # fake reader picks Dee only if the new fact is stored
    assert stats["guard_repair"]["chunks_recovered"] == 1
    assert (tmp_path / "conflicts.jsonl").read_text().strip()
    n = st.client.calls
    st2 = mab.Stages(tmp_path / "stages.jsonl", None, (0, 0), 1.0)  # resume from checkpoint: no client, no new calls
    fc2, cs2 = mab.make_llm_fns(st2, None)
    mab.ingest(mab.parse_facts(CTX), fc2, cs2, chunk_size=2, max_retries=2)
    assert st.client.calls == n


def test_spend_cap_stops_before_the_next_call(tmp_path):
    st = mab.Stages(tmp_path / "s.jsonl", FakeClient(), (3e-7, 1.2e-6), 0.0)
    fc, cs = mab.make_llm_fns(st, None)
    import pytest
    with pytest.raises(RuntimeError):
        mab.ingest(mab.parse_facts(CTX), fc, cs, chunk_size=2)
