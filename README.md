# MemBud

MemBud checks a **proposed memory update** before an agent commits it. You give it the memory state
before, the state you want to write, and the evidence for the change; it answers `ACCEPT` or `HOLD`.
On a `HOLD`, nothing is written. The default verifier is deterministic and needs no model, no network
and no third-party dependencies.

```text
agent proposes a memory write ──► MemBud verify ──► ACCEPT: write happens
                                        │
                                        └──► HOLD / error / timeout: write is denied, state unchanged
```

**Status: experimental prototype, not a production memory service.** It is a research codebase with a
working guard around it. Read [what ACCEPT does and does not mean](docs/quickstart.md#what-accept-does-and-does-not-mean)
before relying on it.

## Try it

```bash
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[test]"
membud-demo                 # 11 bundled cases through the graph and text verifiers
membud-verify --port 8787   # localhost /verify sidecar
python -m pytest -q
```

Then follow the **[quickstart](docs/quickstart.md)**: verify a transition over HTTP, use it as a
library, and guard an opencode memory tool. It takes about ten minutes, and its examples are covered by
the test suite.

## What is in the box

| Piece | What it does |
|---|---|
| `membud.verification`, `membud.graph_verification` | Deterministic checks: coverage, preservation, faithfulness. Structural graph verifier plus an exact-claim-set text baseline. |
| `membud.store` | Versioned JSON store: writes only on `ACCEPT`, atomic, locked, with an append-only audit log. |
| `membud.service` (`membud-verify`) | Localhost-only, stdlib `/verify` sidecar. Persists nothing; fails closed. |
| `integrations/opencode/` | opencode `tool.execute.before` plugin that denies a memory write unless the sidecar says `ACCEPT`. |
| `membud.adapters` | Host-owned staged-write wrappers, including one for real Mem0 `2.2.1` ([guarded Mem0 integration](docs/mem0-guarded-integration.md)). |
| `membud.conversation` | Conversation parsers, transcript-grounded evidence checks, extractors ([conversation pipeline](docs/conversation-pipeline.md)). |
| Optional LLM verifier | JSON-mode, OpenAI-compatible; never used unless you select it explicitly. See [running experiments](docs/running-experiments.md). |

## Known limits

- Claims are compared as **exact strings**; paraphrases are different claims.
- Evidence is **caller-declared** unless you also supply the conversation transcript.
- The sidecar has no authentication or TLS (loopback only) and has not been tested with concurrent
  writers. Only opencode 1.19.5 and Mem0 2.2.1 (with offline model fakes) have been exercised.
- All evaluation data is synthetic and single-reviewer. No result here shows that a graph representation
  beats a text representation. See [research status](docs/research-status.md).

## Documentation

- [Quickstart](docs/quickstart.md): the 10-minute path.
- [Reference](docs/reference.md): code layout, schema and compatibility, input semantics, operation rules.
- [Running experiments](docs/running-experiments.md): evaluation runners, conversation pipeline, LLM providers, cost estimates.
- [Research status](docs/research-status.md): what has been built, run and measured, with caveats.
- [Format policy](docs/format-policy.md), [statistical analysis](docs/statistical-analysis.md), [fairness audit](docs/fairness-audit.md), [software review](docs/software-review-2026-11-19.md).
- [`TODO.md`](TODO.md) and [`PLAN.md`](PLAN.md): remaining work and design history.

## License

[MIT](LICENSE).
