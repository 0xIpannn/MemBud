"""LLM claim-judge grounding policy for :class:`membud.adapters.mem0_staged.StagedMem0`.

``StagedMem0`` asks a host *grounding policy* which trusted source supports each claim Mem0 proposes.
The default :class:`~membud.adapters.mem0_staged.LexicalGrounding` is a deterministic stand-in that
requires every content word of a claim to appear in one user message; on paraphrasing extractors it
grounds nothing. This policy instead asks an LLM judge, once per staged operation, to label every
proposed claim SUPPORTED or UNSUPPORTED against the whole session and the earlier memory, and turns
the SUPPORTED claims into evidence items. UNSUPPORTED or unlabelled claims get no evidence, so the
verifier HOLDs the operation (fail closed).

The judge prompt is supplied by the caller (for HaluMem: ``SYSTEM_ACCEPTED`` from
``scripts/halumem_llm_phase2_eval.py``), so the same judge drives the offline and the live runs.
The session dialogue and earlier memory are not visible through the policy signature
``(claims, sources)``; the host sets them with :meth:`set_context` before each guarded call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

JudgeFn = Callable[[list[dict[str, str]]], Any]  # messages -> object with .json, .input_tokens, .output_tokens


def format_dialogue(dialogue: Sequence[Mapping[str, str]]) -> str:
    return "\n".join(f"[{i}] [{t['role']}] {t['content']}" for i, t in enumerate(dialogue))


def judge_messages(system: str, dialogue: Sequence[Mapping[str, str]], prior: Sequence[str], claims: Sequence[str]):
    """The exact message layout used by ``scripts/run_halumem_claim_judge.py``."""
    listed = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Session:\n{format_dialogue(dialogue)}\n\nEarlier memory:\n"
                                    f"{chr(10).join(prior) or '(empty)'}\n\nClaims:\n{listed}"},
    ]


def parse_verdicts(payload: Any, n: int) -> tuple[dict[int, bool], dict[int, list[int]]]:
    """Map claim index -> supported, and -> cited turn numbers. Malformed entries are skipped (unsupported)."""
    supported: dict[int, bool] = {}
    turns: dict[int, list[int]] = {}
    entries = payload.get("claims") if isinstance(payload, Mapping) else None
    for e in entries if isinstance(entries, list) else []:
        try:
            i = int(e["i"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= i < n:
            continue
        supported[i] = e.get("verdict") == "SUPPORTED"
        cited = []
        for x in e.get("turns") or []:
            try:
                cited.append(int(x))
            except (TypeError, ValueError):
                continue
        turns[i] = cited
    return supported, turns


@dataclass
class ClaimJudgeGrounding:
    """Grounding policy backed by an LLM claim judge. Not thread-safe; one instance per host."""

    judge: JudgeFn
    system_prompt: str
    dialogue: list[dict[str, str]] = field(default_factory=list)
    prior: list[str] = field(default_factory=list)
    last_verdicts: dict[str, bool] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def set_context(self, dialogue: Sequence[Mapping[str, str]], prior: Sequence[str]) -> None:
        self.dialogue = [{"role": str(t["role"]), "content": str(t["content"])} for t in dialogue]
        self.prior = list(prior)
        self.last_verdicts = {}

    def __call__(self, claims: Sequence[str], sources: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
        claims = list(dict.fromkeys(c for c in claims if isinstance(c, str) and c.strip()))
        self.last_verdicts = {}
        if not claims:
            return []
        res = self.judge(judge_messages(self.system_prompt, self.dialogue, self.prior, claims))
        self.calls.append({"claims": len(claims), "input_tokens": getattr(res, "input_tokens", None),
                           "output_tokens": getattr(res, "output_tokens", None)})
        supported, turns = parse_verdicts(getattr(res, "json", None), len(claims))
        by_turn = {}
        for source_id, text in sources:  # StagedMem0 source ids look like "messages[<index>]:<role>"
            try:
                by_turn[int(source_id.split("[", 1)[1].split("]", 1)[0])] = (source_id, text)
            except (IndexError, ValueError):
                continue
        evidence: list[dict[str, Any]] = []
        for i, claim in enumerate(claims):
            ok = supported.get(i, False)
            self.last_verdicts[claim] = ok
            if not ok:
                continue
            cited = [by_turn[t] for t in turns.get(i, []) if t in by_turn]
            evidence.append({
                "id": f"judge-{i}",
                "supports": [claim],
                # Provenance: the trusted user turns the judge cited; a claim the judge grounded only in
                # earlier memory or an adopted assistant fact has no cited user turn and is marked as such.
                "source": ",".join(s for s, _ in cited) or "claim-judge:no-user-turn-cited",
                "text": "\n".join(t for _, t in cited) or "(judge: supported by earlier memory or adopted assistant fact)",
            })
        return evidence
