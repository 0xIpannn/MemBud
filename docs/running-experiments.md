# Running the experiments

Commands and provider setup for the paired evaluation, conversation pipeline and optional LLM verifier. Everything here is research tooling; for adopting the guard see the [quickstart](quickstart.md).

Install the test extra and run the suite: `python -m pip install -e ".[test]"` then `python -m pytest`.

Long model runs write `<output>/checkpoint.jsonl`; re-running the held-out runner with `--resume` validates the config fingerprint and skips completed rows (zero client calls for skipped rows). Cost figures are estimates from `membud.cost` (placeholder range, or explicit `--price-input-per-mtok`/`--price-output-per-mtok`/`--price-cached-per-mtok` flags) and are never presented as a bill. Format guarantees are in [`docs/format-policy.md`](format-policy.md); paired statistics are in [`docs/statistical-analysis.md`](statistical-analysis.md); conversation ingestion and transcript grounding are in [`docs/conversation-pipeline.md`](conversation-pipeline.md).

Run the multi-turn conversation pipeline demo or the frozen conversation evaluation:

```bash
PYTHONPATH=src .venv/bin/python -m membud.conversation --output membud-output/conversation
PYTHONPATH=src .venv/bin/python scripts/run_conversation_evaluation.py --output data/results/conversations-v1 --overwrite
```

Run the three-condition plumbing pilot on the hand-authored development fixtures:

```bash
python -m membud.experiment --output pilot-output --code-revision <git-revision>
```

This writes isolated stores, append-only condition audit logs, and `pilot-summary.json`; use a fresh `--output` directory for each run, because the runner refuses to reuse one; the CLI records a source-package working-tree SHA-256 unless `--code-revision` overrides it. The default input is the development fixture set; use `--manifest data/manifests/evaluation-v1.json` for the hash-pinned synthetic exploratory set. The manifest has one reviewer and is not a representative benchmark. The default `--verifier rules` path now runs two distinct strategies on identical inputs: structural graph verification for the graph condition and the exact-claim-set baseline for text. For the reproducible 36-case rules-only comparison, run `PYTHONPATH=src .venv/bin/python scripts/run_evaluation_v2.py --output data/results/evaluation-v2`; the v2 manifest is `data/manifests/evaluation-v2.json` (18 dev / 18 held-out, single-reviewed, second human review pending), while the frozen `data/manifests/evaluation-v1.json` remains available for archival reproduction. To invoke the optional LLM verifier, choose `--provider claude` (default) or `--provider gemini` with `--verifier llm`:

- **Claude (Anthropic)**: export `ANTHROPIC_API_KEY` (or `CLAUDE_API_KEY`) and run `--verifier llm --provider claude`. `CLAUDE_MODEL` defaults to `claude-haiku-4-5-20251001` (`claude-sonnet-4-5-20250929` is also supported via `CLAUDE_MODEL`), `CLAUDE_BASE_URL` defaults to `https://api.anthropic.com/v1`, and validated responses are cached in SQLite at `~/.cache/membud/claude-cache.sqlite3` (override with `CLAUDE_CACHE_PATH`).
- **Gemini (Google)**: export `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) and run `--verifier llm --provider gemini`. `GEMINI_MODEL` defaults to `gemini-2.5-flash` (`gemini-2.5-pro` is also supported via `GEMINI_MODEL`), `GEMINI_BASE_URL` defaults to `https://generativelanguage.googleapis.com/v1beta/openai`, and validated responses are cached in SQLite at `~/.cache/membud/gemini-cache.sqlite3` (override with `GEMINI_CACHE_PATH`).
- **Legacy archival providers**: `--provider deepseek` (`DEEPSEEK_API_KEY`, `DEEPSEEK_CACHE_PATH=~/.cache/membud/deepseek-cache.sqlite3`) and `--provider commandcode` (`CMD_API_KEY`, `CMD_CACHE_PATH=~/.cache/membud/commandcode-cache.sqlite3`) remain selectable for reproducing historical artifacts.

Never commit or paste API keys. MemBud reads credentials from the process environment; it does not load `.env` automatically (see `.env.example`). The cache and audit logs contain prompt inputs and model outputs, so keep them private and use only authorized data. Cache keys include model, request settings, and full prompt/input; cache hits make no API call and report zero new token usage. LLM results record requested/returned model, prompt version, non-secret request settings, raw structured response, token usage, retries, cache-hit status, and latency in the audit log. Completion usage responses provide token counts but not billed monetary cost; check the provider billing dashboard rather than inferring dollars from tokens. Pilot metrics and limitations are recorded in `data/pilot-report.md`.
