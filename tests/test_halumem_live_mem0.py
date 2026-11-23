"""The three live arms on a REAL mem0ai==2.2.1 (offline: scripted LLM, hash embedder, no network).

Run with the pinned environment:  PYTHONPATH=src .venv-mem0/bin/python -m pytest tests/test_halumem_live_mem0.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["MEM0_TELEMETRY"] = "False"
os.environ["MEM0_DIR"] = os.environ.get("MEM0_DIR") or tempfile.mkdtemp(prefix="membud-mem0-dir-")
pytest.importorskip("mem0")
pytest.importorskip("spacy")

from membud.adapters.claim_judge_grounding import ClaimJudgeGrounding  # noqa: E402
from membud.adapters.mem0_offline import DIMS, HashEmbedder, ScriptedLLM  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("run_halumem_live", ROOT / "scripts" / "run_halumem_live.py")
live = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_halumem_live", live)
_spec.loader.exec_module(live)
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")
EMB = {"provider": "openai", "config": {"api_key": "offline-fake", "embedding_dims": DIMS}}
MSGS = [{"role": "user", "content": "I like green tea. My sister lives in Oslo."}, {"role": "assistant", "content": "Lovely."}]


def judge_flagging(word):
    """Fake claim judge: every claim containing ``word`` is UNSUPPORTED."""
    calls = []

    def judge(messages):
        claims = [ln.split(". ", 1)[1] for ln in messages[1]["content"].split("Claims:\n", 1)[1].splitlines()]
        calls.append(claims)
        return SimpleNamespace(json={"claims": [{"i": i, "verdict": "UNSUPPORTED" if word in c else "SUPPORTED", "turns": [0]}
                                                for i, c in enumerate(claims)]}, input_tokens=1, output_tokens=1)
    return judge, calls


def make(tmp_path, arm, judge=None):
    llm = ScriptedLLM()
    g = ClaimJudgeGrounding(judge=judge, system_prompt="SYS") if judge else None
    s = live.Mem0System(tmp_path / arm, arm, llm, "Use the person's name.", g, embedder=EMB, embedding_model=HashEmbedder())
    s.reset("Alex")
    return s, llm


def stored(system):
    return sorted(system._live("Alex"))


def test_pure_writes_everything(tmp_path):
    s, llm = make(tmp_path, "pure")
    llm.push("Alex likes green tea", "Alex's sister lives on Mars")
    assert sorted(s.add(MSGS, "Alex", 1735812000, "c1")) == ["Alex likes green tea", "Alex's sister lives on Mars"]
    assert stored(s) == ["Alex likes green tea", "Alex's sister lives on Mars"]


def test_hold_writes_nothing_when_any_claim_is_unsupported(tmp_path):
    judge, calls = judge_flagging("Mars")
    s, llm = make(tmp_path, "membud-hold", judge)
    llm.push("Alex likes green tea", "Alex's sister lives on Mars")
    assert s.add(MSGS, "Alex", 1735812000, "c1") == []
    assert stored(s) == []
    assert s.log[-1]["decision"] == "HOLD" and s.log[-1]["verdicts"]["Alex's sister lives on Mars"] is False
    llm.push("Alex likes green tea")
    assert s.add(MSGS, "Alex", 1735812000, "c2") == ["Alex likes green tea"]  # all supported -> committed
    assert stored(s) == ["Alex likes green tea"]


def test_claim_drop_commits_only_supported_claims(tmp_path):
    judge, calls = judge_flagging("Mars")
    s, llm = make(tmp_path, "membud-drop", judge)
    llm.push("Alex likes green tea", "Alex's sister lives on Mars")
    assert s.add(MSGS, "Alex", 1735812000, "c1") == ["Alex likes green tea"]
    assert stored(s) == ["Alex likes green tea"]
    e = s.log[-1]
    assert e["decision"] == "HOLD" and e["drop_applied"] is True and e["dropped"] == ["Alex's sister lives on Mars"]
    assert len(llm.calls) == 1  # the claim-drop commit uses infer=False: no second extraction call
    assert len(calls) == 1  # explicit evidence for the kept claims: no second judge call


def test_claim_drop_with_nothing_supported_writes_nothing(tmp_path):
    judge, _ = judge_flagging("Alex")
    s, llm = make(tmp_path, "membud-drop", judge)
    llm.push("Alex likes green tea")
    assert s.add(MSGS, "Alex", 1735812000, "c1") == []
    assert stored(s) == [] and "drop_applied" not in s.log[-1]


def test_session_time_becomes_created_at_in_search_results(tmp_path):
    for arm in ("pure", "membud-drop"):
        judge, _ = judge_flagging("Mars")
        s, llm = make(tmp_path, arm, judge if arm != "pure" else None)
        llm.push("Alex likes green tea", "Alex's sister lives on Mars")
        s.add(MSGS, "Alex", 1735812000, "c1")
        hits = s.search("green tea", "Alex", 20)
        assert hits and all(h["created_at"] == "2025-01-02T10:00:00+00:00" for h in hits), arm


def test_reset_gives_a_fresh_store(tmp_path):
    s, llm = make(tmp_path, "pure")
    llm.push("Alex likes green tea")
    s.add(MSGS, "Alex", 1735812000, "c1")
    s.reset("Alex")
    assert stored(s) == []
