"""Load the official HaluMem evaluation modules from a local clone, with spend metering (no code copied).

The HaluMem repository (MemTensor/HaluMem) is licensed CC BY-NC-ND 4.0, so nothing from it is vendored or
modified on disk. This shim imports ``eval/llms.py``, ``eval/eval_tools.py``, ``eval/evaluation.py`` and
``eval/prompts.py`` from the clone at run time, and reads plain string constants from ``eval/eval_memzero.py``
with ``ast`` (that module creates a hosted ``MemoryClient`` at import, so it is never imported).

Run-time changes, all outside the official files and none touching prompts, parsing or aggregation:
* the OpenAI-compatible endpoint is configured through the environment variables ``llms.py`` reads
  (``OPENAI_BASE_URL``/``OPENAI_API_KEY``/``OPENAI_MODEL``), pointed at the configured judge;
* ``llms.client.chat.completions.create`` is wrapped to record token usage, stop the run once the estimated
  spend reaches a cap, and stop on HTTP 402 (insufficient balance). The stop is a ``BaseException`` so the
  official ``except Exception`` handlers cannot turn it into a ``None`` score;
* an optional provider field is added to every request (for DeepSeek ``{"thinking": {"type": "disabled"}}``,
  the setting used by this project's other judge calls); prompts and parsing are unchanged;
* ``evaluation.ProcessPoolExecutor`` is replaced by ``ThreadPoolExecutor`` so the meter sees every call
  (worker processes would not share it). Concurrency, not logic, changes.
"""
from __future__ import annotations

import ast
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path.home() / "Repositories" / "HaluMem-repo"


class BudgetExceeded(BaseException):
    """Raised by the metered endpoint; deliberately not an ``Exception`` subclass."""


def read_constants(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """Return top-level string constants from a Python file without executing it."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    out[t.id] = node.value.value
    missing = set(names) - set(out)
    if missing:
        raise KeyError(f"{path}: constants not found: {sorted(missing)}")
    return out


@dataclass
class Meter:
    prompt_per_token: float
    completion_per_token: float
    cap_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    errors: int = 0
    extra_usd: float = 0.0  # spend from calls made outside the official client (extractor, claim judge)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def spent(self) -> float:
        return self.input_tokens * self.prompt_per_token + self.output_tokens * self.completion_per_token + self.extra_usd

    def check(self) -> None:
        if self.spent >= self.cap_usd:
            raise BudgetExceeded(f"estimated spend ${self.spent:.4f} reached cap ${self.cap_usd}")

    def record(self, usage: Any) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

    def as_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "errors": self.errors, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "other_usd": round(self.extra_usd, 4), "estimated_spend_usd": round(self.spent, 4), "cap_usd": self.cap_usd}


def meter_create(create, meter: Meter, extra_body: dict | None = None):
    def wrapped(*args, **kwargs):
        meter.check()
        if extra_body:
            kwargs.setdefault("extra_body", dict(extra_body))
        try:
            resp = create(*args, **kwargs)
        except Exception as exc:
            with meter._lock:
                meter.errors += 1
            if getattr(exc, "status_code", None) == 402 or "Insufficient Balance" in str(exc):
                raise BudgetExceeded(f"provider reports insufficient balance: {exc}") from exc
            raise
        meter.record(getattr(resp, "usage", None))
        return resp
    return wrapped


def load_official(repo: Path, *, base_url: str, api_key: str, model: str, meter: Meter, retry_times: int = 3,
                  extra_body: dict | None = None):
    """Import the official modules with the given endpoint and a metered client. Returns a namespace dict."""
    env = {"OPENAI_BASE_URL": base_url, "OPENAI_API_KEY": api_key, "OPENAI_MODEL": model,
           "RETRY_TIMES": str(retry_times), "WAIT_TIME_LOWER": "2", "WAIT_TIME_UPPER": "20"}
    os.environ.update(env)
    eval_dir = str(Path(repo) / "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    import llms  # noqa: E402  (official)
    llms.client.chat.completions.create = meter_create(llms.client.chat.completions.create, meter, extra_body)
    import eval_tools  # noqa: E402,F401  (official; binds llms.llm_request_for_json, whose client is now metered)
    import evaluation  # noqa: E402  (official)
    import prompts  # noqa: E402  (official)
    evaluation.ProcessPoolExecutor = ThreadPoolExecutor
    return {"llms": llms, "eval_tools": eval_tools, "evaluation": evaluation, "prompts": prompts}
