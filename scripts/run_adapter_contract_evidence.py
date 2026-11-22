#!/usr/bin/env python
"""Write data/results/adapter-contract-v1/<backend>/evidence.json from the parametrized contract suite.

Offline and deterministic (no timings, ids or paths). Run in the pinned mem0 venv:
    PYTHONPATH=src .venv-mem0/bin/python scripts/run_adapter_contract_evidence.py
"""
from __future__ import annotations
import importlib.metadata as md, json, subprocess, sys, xml.etree.ElementTree as ET, tempfile, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "tests"))
from adapter_harness import HARNESSES  # noqa: E402

VERSIONS = {
    "mem0-blackbox": {"mem0ai": "2.2.1", "spacy": "3.8.18"},
    "mem0-staged": {"mem0ai": "2.2.1", "spacy": "3.8.18"},
    "fake-blackbox": {"stdlib": "sqlite3 fake (tests/blackbox_fakes.py)"},
}
OUT = ROOT / "data/results/adapter-contract-v1"

for name, cls in sorted(HARNESSES.items()):
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "r.xml"
        subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_adapter_contract.py",
                        "-k", name, f"--junitxml={xml}", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True)
        cases = []
        for tc in ET.parse(xml).getroot().iter("testcase"):
            status = "failed" if tc.find("failure") is not None or tc.find("error") is not None else (
                "skipped" if tc.find("skipped") is not None else "passed")
            cases.append({"test": tc.get("name").replace(f"-{name}]", "]").replace(f"[{name}]", ""), "status": status})
    cases.sort(key=lambda c: c["test"])
    ev = {
        "backend": name,
        "adapter": "BlackBoxStaged (public API only)" if name.endswith("blackbox") else "StagedMem0 (shadow clone of Mem0 storage)",
        "pinned": VERSIONS[name],
        "contract_suite": "tests/test_adapter_contract.py",
        "passed": sum(c["status"] == "passed" for c in cases),
        "failed": sum(c["status"] == "failed" for c in cases),
        "cases": cases,
        "detects_unmodeled_side_effects": cls.detects,
        "not_protected_against": [k for k, v in cls.detects.items() if not v],
    }
    d = OUT / name; d.mkdir(parents=True, exist_ok=True)
    (d / "evidence.json").write_text(json.dumps(ev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(name, ev["passed"], "passed", ev["failed"], "failed")

findings = {
    "a-mem": {"pinned": "git ceffb860f0712bbae97b184d440df62bc910ca8d (agiresearch/A-mem; PyPI 'agentic-memory' is unrelated)",
              "status": "NOT SAFELY STAGEABLE", "executable_proof": "tests/test_amem_findings.py",
              "reasons": ["constructing a second in-process AgenticMemorySystem resets the shared chroma client and destroys the first instance's vector store, so no in-process scratch twin exists",
                          "add_note always runs the evolution LLM and may rewrite neighbour notes, so replaying a verified add on live is not the staged set"]},
    "graphiti": {"pinned": "graphiti-core 0.31.2 + kuzu 0.11.3", "status": "NOT RUNNABLE OFFLINE",
                 "reasons": ["add_episode(group_id=...) raises AttributeError on KuzuDriver (no _database)",
                             "with group_id=None the full-text index edge_name_and_fact is missing and Kuzu's FTS extension needs a network download",
                             "Neo4j/FalkorDB need a server"]},
    "letta": {"pinned": "letta==0.16.4 (Python server line; current PyPI letta 0.34.x is the Node CLI)", "status": "NOT RUNNABLE OFFLINE",
              "reasons": ["782 MB / 244 packages, asyncpg and pgvector needed manually", "startup requires Postgres on localhost:5432; no SQLite path"]},
    "zep": {"status": "NOT TRIED", "reasons": ["Zep Cloud is SaaS, not offline"]},
}
(OUT / "findings.json").write_text(json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
