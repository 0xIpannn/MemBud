#!/usr/bin/env python3
"""Build a blind human-review quiz and grade the returned answers.

``--build`` writes one self-contained offline HTML file containing every case's
transition inputs (prior state, candidate state, evidence, required claims, and
declared operations) plus ACCEPT / HOLD / INVALID_INPUT controls. The quiz never
contains labels, expected decisions, categories, or notes from the label file.
Cases are displayed in hash order, not manifest order, so the manifest's
category-grouped ordering cannot hint at the decision.

``--grade`` validates a downloaded answers JSON file, compares it with the
frozen labels, and writes an agreement report with per-case rows, overall and
per-split counts, coverage, and a disagreement list for adjudication.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/review_quiz.py \
        --build --output data/review/quiz-v2.html
    PYTHONPATH=src .venv/bin/python scripts/review_quiz.py \
        --grade data/review/answers-alice.json --reviewer "Alice" \
        --output data/review/agreement-alice.json
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data/manifests/evaluation-v2.json"
DEFAULT_QUIZ = ROOT / "data/review/quiz-v2.html"

DECISIONS = ("ACCEPT", "HOLD", "INVALID_INPUT")
DECISION_SET = set(DECISIONS)
TRANSITION_FIELDS = (
    "case_id",
    "schema_version",
    "before",
    "candidate",
    "evidence",
    "required_claims",
    "operations",
)
# Keys that only belong in the label/answer-key file. A case file carrying any of
# these would leak labels into the quiz, so the builder refuses it outright.
FORBIDDEN_CASE_FIELDS = {
    "expected_decision",
    "expected_failed_checks",
    "expected_invalid",
    "category",
    "fault_types",
    "label_status",
    "corrupted_prior_record_ids",
    "valid_prior_records_expected_to_persist",
    "notes",
    "split",
}


class QuizError(ValueError):
    """Raised for invalid manifests, case packages, or answer files."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise QuizError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise QuizError(f"invalid JSON in {path}: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuizError(f"could not read {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QuizError(f"invalid JSON on line {line_number} of {path}: {exc}") from exc
        if not isinstance(row, dict):
            raise QuizError(f"line {line_number} of {path} is not a JSON object")
        rows.append(row)
    return rows


def _load_bundle(
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, Any], str, Path, list[dict[str, Any]]]:
    """Load and hash-check the manifest and its case file (never the labels)."""
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise QuizError(f"manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise QuizError("manifest must be a JSON object")
    for key in ("manifest_id", "cases_file", "cases_sha256", "case_ids", "case_count"):
        if key not in manifest:
            raise QuizError(f"manifest is missing required key {key!r}")

    manifest_sha = _sha256_file(manifest_path)
    cases_path = (manifest_path.parent / manifest["cases_file"]).resolve()
    if not cases_path.is_file():
        raise QuizError(f"case file not found: {cases_path}")
    actual_cases_sha = _sha256_file(cases_path)
    if actual_cases_sha != manifest["cases_sha256"]:
        raise QuizError(
            "case file hash does not match the manifest; refusing to build or grade "
            f"against a changed package ({cases_path})"
        )

    cases = _read_jsonl(cases_path)
    case_ids = [str(case.get("case_id", "")) for case in cases]
    declared_ids = [str(case_id) for case_id in manifest["case_ids"]]
    if case_ids != declared_ids or len(case_ids) != manifest["case_count"]:
        raise QuizError("case IDs/count do not match the manifest")
    if len(case_ids) != len(set(case_ids)):
        raise QuizError("case IDs must be unique")

    for case in cases:
        leaked = FORBIDDEN_CASE_FIELDS.intersection(case)
        if leaked:
            raise QuizError(
                f"case {case.get('case_id')} contains label-only fields "
                f"({', '.join(sorted(leaked))}); refusing to leak the answer key"
            )
        missing = [field for field in TRANSITION_FIELDS if field not in case]
        if missing:
            raise QuizError(
                f"case {case.get('case_id')} is missing transition fields: "
                f"{', '.join(missing)}"
            )
    return manifest, manifest_sha, cases_path, cases


def _load_labels(
    manifest: dict[str, Any], manifest_path: Path
) -> dict[str, dict[str, Any]]:
    """Load labels for grading only, after verifying the pinned label hash."""
    if "labels_file" not in manifest or "labels_sha256" not in manifest:
        raise QuizError("manifest has no pinned labels file")
    labels_path = (manifest_path.parent / manifest["labels_file"]).resolve()
    if not labels_path.is_file():
        raise QuizError(f"label file not found: {labels_path}")
    if _sha256_file(labels_path) != manifest["labels_sha256"]:
        raise QuizError("label file hash does not match the manifest; refusing to grade")
    rows = _read_jsonl(labels_path)
    if [row.get("case_id") for row in rows] != list(manifest["case_ids"]):
        raise QuizError("label rows do not match the manifest case IDs")
    labels: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row["case_id"])
        decision = row.get("expected_decision")
        if decision not in DECISION_SET:
            raise QuizError(f"label {case_id} has an unsupported expected_decision")
        labels[case_id] = row
    return labels


def _quiz_order(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display order independent of the manifest's category grouping."""
    return sorted(
        cases,
        key=lambda case: hashlib.sha256(str(case["case_id"]).encode("utf-8")).hexdigest(),
    )


# --------------------------------------------------------------------------- #
# HTML rendering
# --------------------------------------------------------------------------- #


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _js_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return (
        text.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render_records(records: list[dict[str, Any]]) -> str:
    if not records:
        return '<p class="empty">(none)</p>'
    parts = ['<ul class="records">']
    for record in records:
        parts.append(f'<li><code class="rid">{_esc(record.get("id", ""))}</code>')
        claims = record.get("claims") or []
        if claims:
            parts.append('<ul class="claims">')
            for claim in claims:
                parts.append(f"<li>{_esc(claim)}</li>")
            parts.append("</ul>")
        else:
            parts.append('<span class="muted">(no claims)</span>')
        parts.append("</li>")
    parts.append("</ul>")
    return "".join(parts)


def _render_claim_list(claims: list[str]) -> str:
    if not claims:
        return '<span class="muted">(none)</span>'
    return "; ".join(f'<span class="claim">{_esc(claim)}</span>' for claim in claims)


def _render_evidence(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return '<p class="empty">(none)</p>'
    parts = ['<div class="evidence-list">']
    for item in evidence:
        parts.append('<div class="evidence-item">')
        parts.append(
            '<div class="ev-head">'
            f'<code class="rid">{_esc(item.get("id", ""))}</code>'
            f'<span class="source">{_esc(item.get("source", ""))}</span>'
            "</div>"
        )
        parts.append(f'<blockquote>{_esc(item.get("text", ""))}</blockquote>')
        parts.append('<div class="ev-tags">')
        parts.append(
            '<div><span class="tag tag-support">Supports</span> '
            f'{_render_claim_list(item.get("supports") or [])}</div>'
        )
        parts.append(
            '<div><span class="tag tag-contradict">Contradicts</span> '
            f'{_render_claim_list(item.get("contradicts") or [])}</div>'
        )
        parts.append("</div></div>")
    parts.append("</div>")
    return "".join(parts)


def _render_operations(operations: list[dict[str, Any]]) -> str:
    if not operations:
        return '<p class="empty">(none declared)</p>'
    parts = ['<ul class="operations">']
    for operation in operations:
        op_type = str(operation.get("type", ""))
        parts.append('<li class="operation">')
        parts.append('<div class="op-head">')
        parts.append(f'<span class="op-type">{_esc(op_type)}</span>')
        for key in ("record_id", "target_id"):
            if operation.get(key):
                parts.append(f'<span class="op-field">{key}=<code>{_esc(operation[key])}</code></span>')
        parts.append("</div>")
        if operation.get("reason"):
            parts.append(f'<div class="op-reason">reason: {_esc(operation["reason"])}</div>')
        authorization = operation.get("authorization")
        if authorization:
            parts.append('<div class="auth">')
            parts.append('<span class="tag">Authorization</span>')
            for key in ("source", "evidence_id", "target_id", "rationale"):
                parts.append(
                    f'<span class="op-field">{key}=<code>{_esc(authorization.get(key, ""))}</code></span>'
                )
            parts.append("</div>")
        elif op_type == "DELETE":
            parts.append(
                '<div class="auth auth-missing"><span class="tag">Authorization</span>'
                '<span class="muted">(none)</span></div>'
            )
        parts.append("</li>")
    parts.append("</ul>")
    return "".join(parts)


def _render_case(case: dict[str, Any], index: int, total: int) -> str:
    case_id = str(case["case_id"])
    # Whitelist the transition fields so an unexpected extra key (for example a
    # label field added by a future merge) can never be embedded in the quiz.
    transition = {field: case[field] for field in TRANSITION_FIELDS}
    raw = json.dumps(transition, ensure_ascii=False, indent=2)
    radio_name = f"decision-{case_id}"
    options = "".join(
        f'<label class="option"><input class="decision-input" type="radio" '
        f'name="{_esc(radio_name)}" value="{decision}" data-case="{_esc(case_id)}">'
        f"<span>{decision}</span></label>"
        for decision in DECISIONS
    )
    return f"""
<article class="case" id="case-{_esc(case_id)}">
  <header class="case-head">
    <div>
      <p class="case-number">Case {index} of {total}</p>
      <h2><code>{_esc(case_id)}</code></h2>
    </div>
    <p class="case-note">Transition inputs only — no label is present in this file.</p>
  </header>

  <div class="grid two">
    <section class="panel">
      <h3>Prior state (before)</h3>
      {_render_records(transition.get("before") or [])}
    </section>
    <section class="panel">
      <h3>Candidate state</h3>
      {_render_records(transition.get("candidate") or [])}
    </section>
  </div>

  <section class="panel">
    <h3>Evidence</h3>
    {_render_evidence(transition.get("evidence") or [])}
  </section>

  <section class="panel">
    <h3>Required claims <span class="sub">(verifier input)</span></h3>
    <p class="claims-line">{_render_claim_list(transition.get("required_claims") or [])}</p>
  </section>

  <section class="panel">
    <h3>Declared operations</h3>
    {_render_operations(transition.get("operations") or [])}
  </section>

  <details class="raw">
    <summary>Full transition JSON</summary>
    <pre>{html.escape(raw)}</pre>
  </details>

  <fieldset class="answer-block">
    <legend>Your decision for <code>{_esc(case_id)}</code></legend>
    <div class="options">{options}</div>
  </fieldset>

  <div class="note-wrap">
    <label for="note-{_esc(case_id)}">Optional note</label>
    <textarea id="note-{_esc(case_id)}" class="note-input" data-case="{_esc(case_id)}"
      rows="2" placeholder="Reason, evidence cited, or rule applied (optional)."></textarea>
  </div>
</article>
"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MemBud blind review quiz — __MANIFEST_ID__</title>
<style>
  :root {
    --bg: #f4f6fa;
    --card: #ffffff;
    --ink: #1b2430;
    --muted: #5c6a7b;
    --line: #d7dfe9;
    --accent: #2358a6;
    --accent-soft: #e8f0fb;
    --warn: #a3480b;
    --support: #1d6b45;
    --contradict: #9b2c2c;
    --shadow: 0 1px 2px rgba(21, 35, 55, 0.06), 0 8px 24px rgba(21, 35, 55, 0.05);
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 16px;
    line-height: 1.5;
  }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  code { font-size: 0.92em; }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 26px 18px 72px; }
  .hero {
    background: linear-gradient(135deg, #173a6b 0%, #2358a6 62%, #2c6fb5 100%);
    color: #fff;
    border-radius: 14px;
    padding: 30px 30px 24px;
    box-shadow: var(--shadow);
    margin-bottom: 20px;
  }
  .hero h1 { margin: 0 0 8px; font-size: 1.75rem; line-height: 1.2; }
  .hero p { margin: 0; max-width: 72ch; }
  .hero code { color: #dce9fb; }
  .meta { display: flex; flex-wrap: wrap; gap: 10px 26px; margin: 18px 0 0; }
  .meta div { min-width: 180px; }
  .meta dt { font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.72; }
  .meta dd { margin: 2px 0 0; font-size: 0.9rem; word-break: break-all; }
  .card {
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 12px;
    box-shadow: var(--shadow);
    padding: 22px 24px;
    margin-bottom: 18px;
  }
  h2 { font-size: 1.25rem; margin: 0 0 12px; }
  h3 { font-size: 1rem; margin: 0 0 8px; }
  .card > h2:not(:first-child) { margin-top: 22px; }
  .lede { color: var(--muted); margin-top: 0; }
  ol, ul { margin: 8px 0 0; padding-left: 22px; }
  li { margin: 4px 0; }
  .hint, .muted, .sub { color: var(--muted); }
  .sub { font-weight: 400; font-size: 0.86em; }
  .rubric { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
  .rubric-item { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; background: #fbfcfe; }
  .rubric-item h3 { display: flex; align-items: center; gap: 8px; }
  .rubric-item p { margin: 6px 0 0; color: var(--muted); font-size: 0.93rem; }
  .pill { display: inline-block; border-radius: 999px; padding: 2px 10px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.04em; }
  .pill.accept { background: #e2f2e8; color: var(--support); }
  .pill.hold { background: #fdf0dd; color: var(--warn); }
  .pill.invalid { background: #fbe5e5; color: var(--contradict); }
  .reviewer-row { display: flex; flex-wrap: wrap; gap: 14px; align-items: flex-end; }
  .reviewer-row label { display: block; font-weight: 600; margin-bottom: 4px; }
  input[type="text"], textarea {
    font: inherit;
    color: inherit;
    background: #fff;
    border: 1px solid #b9c5d3;
    border-radius: 8px;
    padding: 10px 12px;
    width: 100%;
  }
  input[type="text"] { max-width: 420px; }
  input[type="text"]:focus, textarea:focus, input[type="radio"]:focus-visible {
    outline: 3px solid #b9d0f0;
    outline-offset: 1px;
  }
  .progress-row { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  progress { width: min(100%, 560px); height: 16px; accent-color: var(--accent); }
  .progress-text { font-weight: 600; }
  .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
  button {
    font: inherit;
    font-weight: 600;
    border-radius: 8px;
    padding: 10px 16px;
    border: 1px solid var(--accent);
    background: var(--accent);
    color: #fff;
    cursor: pointer;
  }
  button.secondary { background: #fff; color: var(--accent); }
  button:hover { filter: brightness(1.05); }
  #save-status { min-height: 1.4em; margin: 10px 0 0; font-size: 0.88rem; color: var(--muted); }
  #save-status.error { color: var(--contradict); }
  .case {
    background: var(--card);
    border: 1px solid var(--line);
    border-left: 4px solid var(--accent);
    border-radius: 12px;
    box-shadow: var(--shadow);
    padding: 22px 24px;
    margin-bottom: 20px;
  }
  .case-head { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; flex-wrap: wrap; }
  .case-head h2 { margin: 0; font-size: 1.45rem; }
  .case-number { margin: 0 0 2px; color: var(--muted); font-size: 0.8rem; letter-spacing: 0.06em; text-transform: uppercase; }
  .case-note { margin: 4px 0 0; color: var(--muted); font-size: 0.84rem; max-width: 34ch; text-align: right; }
  .grid.two { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
  .panel { border: 1px solid var(--line); border-radius: 10px; background: #fbfcfe; padding: 14px 16px; margin-top: 14px; }
  .grid .panel { margin-top: 14px; }
  .empty { margin: 0; color: var(--muted); font-style: italic; }
  .records, .claims, .operations { list-style: none; padding-left: 0; margin: 0; }
  .records > li { padding: 6px 0; border-bottom: 1px dashed #e2e8f0; }
  .records > li:last-child { border-bottom: 0; }
  .claims { margin: 2px 0 0 18px; padding-left: 16px; list-style: disc; }
  .claims li { margin: 1px 0; }
  .rid { background: #edf2f8; border-radius: 5px; padding: 1px 6px; color: #24425f; font-weight: 600; }
  .claim { background: var(--accent-soft); border-radius: 5px; padding: 1px 6px; }
  .claims-line { margin: 0; line-height: 1.8; }
  .evidence-item { border-top: 1px solid #e5ebf2; padding: 10px 0 4px; }
  .evidence-item:first-child { border-top: 0; padding-top: 0; }
  .ev-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .source { font-size: 0.8rem; color: var(--muted); background: #eef2f7; border-radius: 999px; padding: 1px 9px; }
  blockquote { margin: 8px 0; padding: 8px 12px; border-left: 3px solid #b9cbe3; background: #f4f8fd; color: #22364c; }
  .ev-tags { display: grid; gap: 4px; font-size: 0.9rem; }
  .tag { display: inline-block; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; border-radius: 4px; padding: 1px 7px; margin-right: 6px; }
  .tag-support { background: #e2f2e8; color: var(--support); }
  .tag-contradict { background: #fbe5e5; color: var(--contradict); }
  .operation { padding: 8px 0; border-bottom: 1px dashed #e2e8f0; }
  .operation:last-child { border-bottom: 0; }
  .op-head { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .op-type { font-weight: 800; letter-spacing: 0.04em; background: #173a6b; color: #fff; border-radius: 5px; padding: 1px 8px; font-size: 0.8rem; }
  .op-field { font-size: 0.86rem; color: #304459; }
  .op-reason { margin-top: 4px; font-size: 0.9rem; color: var(--muted); }
  .auth { margin-top: 6px; padding: 8px 10px; border: 1px solid #d9e5f2; background: #f4f8fd; border-radius: 8px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 0.86rem; }
  .auth .tag { background: #e2ebf7; color: #24425f; margin-right: 0; }
  .auth-missing { border-color: #f0c9a8; background: #fff7ef; }
  .auth-missing .tag { background: #fbe3cf; color: var(--warn); }
  .raw { margin-top: 14px; border: 1px solid var(--line); border-radius: 10px; background: #f8fafc; }
  .raw summary { cursor: pointer; padding: 10px 14px; font-weight: 600; color: var(--accent); }
  .raw pre { margin: 0; padding: 0 14px 14px; overflow-x: auto; font-size: 0.82rem; line-height: 1.45; }
  .answer-block { border: 2px solid #c7d6ea; border-radius: 10px; padding: 12px 16px 14px; margin: 18px 0 0; background: #f8fbff; }
  .answer-block legend { font-weight: 700; padding: 0 6px; }
  .options { display: flex; flex-wrap: wrap; gap: 10px; }
  .option { display: flex; align-items: center; gap: 7px; border: 1px solid #b9c5d3; border-radius: 8px; background: #fff; padding: 9px 14px; cursor: pointer; font-weight: 700; }
  .option:hover { border-color: var(--accent); background: #f2f7ff; }
  .option input { width: 18px; height: 18px; accent-color: var(--accent); margin: 0; }
  .note-wrap { margin-top: 12px; }
  .note-wrap label { display: block; font-size: 0.88rem; font-weight: 600; margin-bottom: 4px; }
  textarea { resize: vertical; min-height: 58px; }
  .footer { color: var(--muted); font-size: 0.85rem; text-align: center; margin-top: 26px; }
  @media (max-width: 620px) {
    .wrap { padding: 14px 10px 48px; }
    .hero, .card, .case { padding: 18px 15px; }
    .case-note { text-align: left; }
  }
  @media print {
    body { background: #fff; }
    .hero { background: #173a6b !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .card, .case { box-shadow: none; break-inside: avoid; }
    .actions, #save-status { display: none; }
  }
</style>
</head>
<body>
<div class="wrap">

<header class="hero">
  <h1>MemBud blind human-review quiz</h1>
  <p class="lede">
    You are independently reviewing <strong>__CASE_COUNT__</strong> frozen memory-transition cases.
    Judge each transition from the inputs and the decision rubric alone. This file is fully
    offline and contains <strong>no answer key</strong>: no per-case labels, decision hints,
    or reviewer notes from the label file appear anywhere in it.
  </p>
  <dl class="meta">
    <div><dt>Manifest</dt><dd><code>__MANIFEST_ID__</code></dd></div>
    <div><dt>Manifest SHA-256</dt><dd><code>__MANIFEST_SHA__</code></dd></div>
    <div><dt>Cases</dt><dd>__CASE_COUNT__ (display order is hash-shuffled)</dd></div>
  </dl>
</header>

<section class="card" id="instructions">
  <h2>Before you start</h2>
  <ol>
    <li>Enter your reviewer name below. Use the same name when running the grader.</li>
    <li>Read each case's prior state, candidate state, evidence (note the
      <em>supports</em> and <em>contradicts</em> claims), required claims, and declared
      operations, including any structured DELETE authorization.</li>
    <li>Choose one decision and optionally add a short note. A blank decision is allowed,
      but it reduces coverage and cannot be counted as agreement.</li>
    <li>Answers autosave in this browser via <code>localStorage</code>. Keep this tab open
      until you download; clearing browser data or using private mode may lose drafts.</li>
    <li>When finished, click <strong>Download answers JSON</strong> and send that file to the
      review coordinator. Do not send a screenshot of the page.</li>
  </ol>
  <p class="hint">
    Full annotation rules are in <code>data/annotation-guidelines.md</code>. If a case is
    genuinely ambiguous under those rules, choose HOLD when it is schema-valid and note the
    ambiguity; choose INVALID_INPUT only when the schema contract is broken before
    verification.
  </p>

  <h2>Decision rubric</h2>
  <div class="rubric">
    <div class="rubric-item">
      <h3><span class="pill accept">ACCEPT</span></h3>
      <p>The transition is schema-valid, every required claim is present, every material
      candidate claim is supported by the supplied evidence or valid prior memory, declared
      operations match the state change, any DELETE has valid structured authorization, and
      no contradiction remains unresolved.</p>
    </div>
    <div class="rubric-item">
      <h3><span class="pill hold">HOLD</span></h3>
      <p>The transition is schema-valid but has a material verification problem: an omitted
      required claim, an unsupported or contradicted claim, an unjustified change/removal,
      unsupported provenance, or an unresolved contradiction against a retained claim.</p>
    </div>
    <div class="rubric-item">
      <h3><span class="pill invalid">INVALID_INPUT</span></h3>
      <p>The v2 schema contract is broken before verification: for example a DELETE without
      valid structured authorization, an authorization that references missing evidence or
      the wrong target, or a candidate state change not declared by a matching operation.
      Invalid input is never verified or written.</p>
    </div>
  </div>
</section>

<section class="card" id="reviewer-card">
  <h2>Reviewer</h2>
  <div class="reviewer-row">
    <div>
      <label for="reviewer">Your name</label>
      <input type="text" id="reviewer" name="reviewer" autocomplete="off" spellcheck="false"
        placeholder="e.g. Alice Rahman">
    </div>
  </div>
  <p class="hint">The name is stored in your browser and included in the downloaded answers JSON.</p>
</section>

<section class="card" id="progress-card">
  <h2>Progress</h2>
  <div class="progress-row">
    <progress id="progress-bar" value="0" max="__CASE_COUNT__"></progress>
    <span class="progress-text" id="progress-text">0 / __CASE_COUNT__ answered</span>
  </div>
  <div class="actions">
    <button type="button" class="js-download" id="download">Download answers JSON</button>
    <button type="button" class="secondary" id="reset">Clear saved answers</button>
  </div>
  <p id="save-status" role="status" aria-live="polite"></p>
</section>

<main id="cases">
__CASES_HTML__
</main>

<section class="card" id="finish">
  <h2>Finish</h2>
  <p>
    Check that the progress indicator shows <strong>__CASE_COUNT__ / __CASE_COUNT__</strong>
    answered, then download the answers JSON. The coordinator will grade it with
    <code>scripts/review_quiz.py --grade</code>; disagreements go to adjudication using
    <code>data/review/adjudication-template.md</code>.
  </p>
  <div class="actions">
    <button type="button" class="js-download">Download answers JSON</button>
  </div>
</section>

<p class="footer">
  Self-contained review artifact generated from manifest <code>__MANIFEST_ID__</code>.
  No network requests, no external assets, no labels.
</p>

</div>
<script>
(function () {
  "use strict";

  var MANIFEST_ID = __MANIFEST_ID_JSON__;
  var MANIFEST_SHA256 = __MANIFEST_SHA_JSON__;
  var CASE_IDS = __CASE_IDS_JSON__;
  var STORAGE_KEY = "membud-review-quiz:" + MANIFEST_ID + ":" + MANIFEST_SHA256;

  var reviewerInput = document.getElementById("reviewer");
  var progressBar = document.getElementById("progress-bar");
  var progressText = document.getElementById("progress-text");
  var saveStatus = document.getElementById("save-status");
  var resetButton = document.getElementById("reset");
  var downloadButtons = document.querySelectorAll(".js-download");

  var state = { reviewer: "", answers: {} };

  function blankAnswers() {
    var answers = {};
    CASE_IDS.forEach(function (caseId) {
      answers[caseId] = { decision: null, note: "" };
    });
    return answers;
  }

  function normalizeDecision(value) {
    if (value === "ACCEPT" || value === "HOLD" || value === "INVALID_INPUT") {
      return value;
    }
    return null;
  }

  function setStatus(message, isError) {
    saveStatus.textContent = message;
    saveStatus.className = isError ? "error" : "";
  }

  function answeredCount() {
    var count = 0;
    CASE_IDS.forEach(function (caseId) {
      if (state.answers[caseId] && state.answers[caseId].decision) count += 1;
    });
    return count;
  }

  function updateProgress() {
    var count = answeredCount();
    progressBar.value = count;
    progressBar.max = CASE_IDS.length;
    progressText.textContent = count + " / " + CASE_IDS.length + " answered";
  }

  function saveState(message) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
        reviewer: state.reviewer,
        answers: state.answers,
        saved_at: new Date().toISOString()
      }));
      var stamp = new Date().toLocaleTimeString();
      setStatus(message || ("Draft saved in this browser at " + stamp + "."), false);
    } catch (error) {
      setStatus(
        "Could not save to localStorage. Keep this tab open and download before closing.",
        true
      );
    }
  }

  function loadState() {
    state.answers = blankAnswers();
    var raw = null;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch (error) {
      setStatus("localStorage is unavailable; answers will not autosave.", true);
      return;
    }
    if (!raw) return;
    try {
      var saved = JSON.parse(raw);
      if (saved && typeof saved.reviewer === "string") {
        state.reviewer = saved.reviewer;
      }
      if (saved && saved.answers && typeof saved.answers === "object") {
        CASE_IDS.forEach(function (caseId) {
          var entry = saved.answers[caseId];
          if (!entry || typeof entry !== "object") return;
          state.answers[caseId].decision = normalizeDecision(entry.decision);
          state.answers[caseId].note = typeof entry.note === "string" ? entry.note : "";
        });
      }
      setStatus("Restored your saved draft from this browser.", false);
    } catch (error) {
      setStatus("A saved draft could not be read; starting with blank answers.", true);
    }
  }

  function applyStateToForm() {
    reviewerInput.value = state.reviewer;
    CASE_IDS.forEach(function (caseId) {
      var entry = state.answers[caseId];
      if (entry.decision) {
        var selector = 'input[name="decision-' + caseId + '"][value="' + entry.decision + '"]';
        var radio = document.querySelector(selector);
        if (radio) radio.checked = true;
      }
      var note = document.getElementById("note-" + caseId);
      if (note) note.value = entry.note;
    });
    updateProgress();
  }

  Array.prototype.forEach.call(
    document.querySelectorAll(".decision-input"),
    function (radio) {
      radio.addEventListener("change", function () {
        var caseId = radio.getAttribute("data-case");
        if (!state.answers[caseId]) state.answers[caseId] = { decision: null, note: "" };
        state.answers[caseId].decision = normalizeDecision(radio.value);
        updateProgress();
        saveState();
      });
    }
  );

  Array.prototype.forEach.call(
    document.querySelectorAll(".note-input"),
    function (textarea) {
      textarea.addEventListener("input", function () {
        var caseId = textarea.getAttribute("data-case");
        if (!state.answers[caseId]) state.answers[caseId] = { decision: null, note: "" };
        state.answers[caseId].note = textarea.value;
        saveState("Draft saved in this browser.");
      });
    }
  );

  reviewerInput.addEventListener("input", function () {
    state.reviewer = reviewerInput.value.trim();
    saveState();
  });

  function slugify(value) {
    var slug = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    return slug || "reviewer";
  }

  function downloadAnswers() {
    var reviewer = reviewerInput.value.trim();
    if (!reviewer) {
      window.alert("Enter your reviewer name before downloading answers.");
      reviewerInput.focus();
      return;
    }
    state.reviewer = reviewer;
    saveState();
    var answers = {};
    CASE_IDS.forEach(function (caseId) {
      answers[caseId] = {
        decision: state.answers[caseId].decision,
        note: state.answers[caseId].note || ""
      };
    });
    var payload = {
      reviewer: reviewer,
      manifest_id: MANIFEST_ID,
      manifest_sha256: MANIFEST_SHA256,
      answers: answers
    };
    var blob = new Blob([JSON.stringify(payload, null, 2) + "\\n"], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = MANIFEST_ID + "-answers-" + slugify(reviewer) + ".json";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    var unanswered = CASE_IDS.length - answeredCount();
    var message = "Downloaded answers JSON for " + reviewer + ".";
    if (unanswered > 0) message += " " + unanswered + " case(s) are still blank.";
    setStatus(message, unanswered > 0);
  }

  Array.prototype.forEach.call(downloadButtons, function (button) {
    button.addEventListener("click", downloadAnswers);
  });

  resetButton.addEventListener("click", function () {
    if (!window.confirm("Clear all saved answers for this quiz? This cannot be undone.")) {
      return;
    }
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch (error) {
      /* localStorage may be unavailable; reset the form anyway. */
    }
    state.answers = blankAnswers();
    Array.prototype.forEach.call(
      document.querySelectorAll(".decision-input"),
      function (radio) { radio.checked = false; }
    );
    Array.prototype.forEach.call(
      document.querySelectorAll(".note-input"),
      function (textarea) { textarea.value = ""; }
    );
    updateProgress();
    saveState("Saved answers cleared from this browser.");
  });

  loadState();
  applyStateToForm();
})();
</script>
</body>
</html>
"""


def _render_quiz(
    manifest: dict[str, Any], manifest_sha: str, cases: list[dict[str, Any]]
) -> str:
    ordered = _quiz_order(cases)
    cases_html = "\n".join(
        _render_case(case, index, len(ordered))
        for index, case in enumerate(ordered, start=1)
    )
    manifest_id = str(manifest["manifest_id"])
    return (
        HTML_TEMPLATE.replace("__MANIFEST_ID_JSON__", _js_json(manifest_id))
        .replace("__MANIFEST_SHA_JSON__", _js_json(manifest_sha))
        .replace("__CASE_IDS_JSON__", _js_json([str(case["case_id"]) for case in ordered]))
        .replace("__MANIFEST_ID__", _esc(manifest_id))
        .replace("__MANIFEST_SHA__", _esc(manifest_sha))
        .replace("__CASE_COUNT__", str(len(ordered)))
        .replace("__CASES_HTML__", cases_html)
    )


def render_quiz(manifest_path: str | Path = DEFAULT_MANIFEST) -> str:
    """Render the self-contained quiz HTML for a hash-pinned manifest."""
    manifest, manifest_sha, _cases_path, cases = _load_bundle(manifest_path)
    return _render_quiz(manifest, manifest_sha, cases)


def build_quiz(
    manifest_path: str | Path = DEFAULT_MANIFEST,
    output_path: str | Path = DEFAULT_QUIZ,
) -> dict[str, Any]:
    """Write the quiz HTML and return a small build report."""
    output_path = Path(output_path)
    manifest, manifest_sha, _cases_path, cases = _load_bundle(manifest_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_quiz(manifest, manifest_sha, cases), encoding="utf-8")
    return {
        "output": str(output_path),
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha,
        "case_count": len(cases),
    }


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #


def _validate_answer_payload(
    payload: Any,
    manifest: dict[str, Any],
    manifest_sha: str,
    reviewer: str,
) -> dict[str, dict[str, Any]]:
    """Validate and normalize an answers payload; blank decisions become ``None``."""
    if not isinstance(payload, dict):
        raise QuizError("answers file must contain a JSON object")
    if "answers" not in payload:
        raise QuizError("answers file is missing the 'answers' object")
    answers = payload["answers"]
    if not isinstance(answers, dict):
        raise QuizError("'answers' must be a JSON object keyed by case ID")

    case_ids = [str(case_id) for case_id in manifest["case_ids"]]
    unknown = sorted(set(answers) - set(case_ids))
    if unknown:
        raise QuizError(f"answers contain unknown case IDs: {', '.join(unknown)}")

    if payload.get("manifest_id") not in (None, manifest["manifest_id"]):
        raise QuizError(
            "answers manifest_id does not match the manifest being graded: "
            f"{payload.get('manifest_id')!r} != {manifest['manifest_id']!r}"
        )
    if payload.get("manifest_sha256") not in (None, manifest_sha):
        raise QuizError(
            "answers manifest_sha256 does not match the manifest being graded; "
            "the case package may have changed"
        )
    file_reviewer = payload.get("reviewer")
    if file_reviewer is not None:
        if not isinstance(file_reviewer, str):
            raise QuizError("'reviewer' in the answers file must be a string")
        if file_reviewer.strip() and file_reviewer.strip() != reviewer:
            raise QuizError(
                "answers reviewer does not match --reviewer: "
                f"{file_reviewer.strip()!r} != {reviewer!r}"
            )

    normalized: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        entry = answers.get(case_id)
        if entry is None:
            normalized[case_id] = {"decision": None, "note": ""}
            continue
        if not isinstance(entry, dict):
            raise QuizError(f"answer for {case_id} must be a JSON object")
        decision = entry.get("decision")
        if decision is None or decision == "":
            decision = None
        elif not isinstance(decision, str) or decision not in DECISION_SET:
            raise QuizError(
                f"answer for {case_id} has unsupported decision {decision!r}; "
                f"expected one of {', '.join(DECISIONS)} or blank"
            )
        note = entry.get("note", "")
        if note is None:
            note = ""
        if not isinstance(note, str):
            raise QuizError(f"note for {case_id} must be a string")
        normalized[case_id] = {"decision": decision, "note": note}
    return normalized


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    compared = [row for row in rows if row["reviewer_decision"] is not None]
    agreements = sum(1 for row in compared if row["agree"] is True)
    reviewer_counts = {decision: 0 for decision in DECISIONS}
    expected_counts = {decision: 0 for decision in DECISIONS}
    for row in compared:
        reviewer_counts[row["reviewer_decision"]] += 1
    for row in rows:
        expected_counts[row["expected_decision"]] += 1
    return {
        "cases": len(rows),
        "answered": len(compared),
        "blank": len(rows) - len(compared),
        "agreements": agreements,
        "disagreements": len(compared) - agreements,
        "agreement_rate": round(agreements / len(compared), 4) if compared else None,
        "reviewer_decision_counts": reviewer_counts,
        "expected_decision_counts": expected_counts,
    }


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": case.get("schema_version"),
        "before": case.get("before") or [],
        "candidate": case.get("candidate") or [],
        "evidence": case.get("evidence") or [],
        "required_claims": case.get("required_claims") or [],
        "operations": case.get("operations") or [],
    }


def _pct(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def build_agreement(
    answers_path: str | Path,
    reviewer: str,
    manifest_path: str | Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """Validate answers, compare with labels, and return the agreement report."""
    reviewer = reviewer.strip()
    if not reviewer:
        raise QuizError("reviewer name must not be empty")
    manifest_path = Path(manifest_path)
    manifest, manifest_sha, cases_path, cases = _load_bundle(manifest_path)
    labels = _load_labels(manifest, manifest_path)

    payload = _read_json(Path(answers_path))
    normalized = _validate_answer_payload(payload, manifest, manifest_sha, reviewer)

    entries = {
        str(entry["case_id"]): entry for entry in manifest.get("case_entries", [])
    }
    cases_by_id = {str(case["case_id"]): case for case in cases}

    rows: list[dict[str, Any]] = []
    for case_id in [str(case_id) for case_id in manifest["case_ids"]]:
        reviewer_decision = normalized[case_id]["decision"]
        expected_decision = labels[case_id]["expected_decision"]
        split = entries.get(case_id, {}).get("split") or labels[case_id].get("split") or "unspecified"
        agree = None if reviewer_decision is None else reviewer_decision == expected_decision
        rows.append(
            {
                "case_id": case_id,
                "split": split,
                "reviewer_decision": reviewer_decision,
                "expected_decision": expected_decision,
                "agree": agree,
            }
        )

    answered = [row for row in rows if row["reviewer_decision"] is not None]
    blank_ids = [row["case_id"] for row in rows if row["reviewer_decision"] is None]
    coverage = {
        "total": len(rows),
        "answered": len(answered),
        "blank": len(blank_ids),
        "rate": round(len(answered) / len(rows), 4) if rows else None,
        "blank_case_ids": blank_ids,
    }

    split_names: list[str] = []
    for name in ("dev", "heldout"):
        if any(row["split"] == name for row in rows):
            split_names.append(name)
    for name in sorted({row["split"] for row in rows} - set(split_names)):
        split_names.append(name)
    splits = {
        name: _group_metrics([row for row in rows if row["split"] == name])
        for name in split_names
    }

    disagreements = []
    for row in answered:
        if row["agree"] is False:
            disagreements.append(
                {
                    "case_id": row["case_id"],
                    "split": row["split"],
                    "reviewer_decision": row["reviewer_decision"],
                    "expected_decision": row["expected_decision"],
                    "reviewer_note": normalized[row["case_id"]]["note"],
                    "case_summary": _case_summary(cases_by_id[row["case_id"]]),
                }
            )

    report = {
        "schema_version": 1,
        "kind": "membud-review-agreement",
        "reviewer": reviewer,
        "timestamp_utc": _utc_now(),
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest_sha,
        "manifest_path": str(manifest_path),
        "cases_sha256": manifest["cases_sha256"],
        "labels_sha256": manifest["labels_sha256"],
        "answers_file": str(answers_path),
        "coverage": coverage,
        "overall": _group_metrics(rows),
        "splits": splits,
        "cases": rows,
        "disagreements": disagreements,
    }
    return report


def grade_review(
    answers_path: str | Path,
    reviewer: str,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build the agreement report and optionally write it as JSON."""
    report = build_agreement(answers_path, reviewer, manifest_path)
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def default_agreement_path(reviewer: str, review_dir: Path = ROOT / "data/review") -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", reviewer.strip().lower()).strip("-") or "reviewer"
    return review_dir / f"agreement-{slug}.json"


def print_summary(report: dict[str, Any], output_path: Path | None = None) -> None:
    coverage = report["coverage"]
    overall = report["overall"]
    print(
        f"reviewer={report['reviewer']} manifest={report['manifest_id']} "
        f"sha256={report['manifest_sha256'][:12]}"
    )
    print(
        f"coverage: {coverage['answered']}/{coverage['total']} "
        f"({_pct(coverage['rate'])}); blank={coverage['blank']}"
    )
    print(
        f"agreement overall: {overall['agreements']}/{overall['answered']} "
        f"({_pct(overall['agreement_rate'])}); disagreements={overall['disagreements']}"
    )
    for name, metrics in report["splits"].items():
        print(
            f"  {name:8s} {metrics['agreements']}/{metrics['answered']} "
            f"({_pct(metrics['agreement_rate'])})"
        )
    if report["disagreements"]:
        print("disagreements:")
        for item in report["disagreements"]:
            print(
                f"  {item['case_id']} [{item['split']}]: "
                f"reviewer={item['reviewer_decision']} "
                f"expected={item['expected_decision']}"
            )
    else:
        print("disagreements: none")
    if output_path is not None:
        print(f"wrote {output_path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a blind offline review quiz for a frozen transition manifest, "
            "or grade a downloaded answers JSON file against the pinned labels."
        )
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="build the self-contained quiz HTML",
    )
    parser.add_argument(
        "--grade",
        metavar="ANSWERS_JSON",
        help="answers JSON file downloaded from the quiz",
    )
    parser.add_argument(
        "--reviewer",
        help="reviewer name (required with --grade)",
    )
    parser.add_argument(
        "--output",
        help=(
            "output path; defaults to data/review/quiz-v2.html for --build and "
            "data/review/agreement-<slug>.json for --grade"
        ),
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="path to the hash-pinned manifest",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.build and args.grade:
        parser.error("choose either --build or --grade, not both")
    if not args.build and not args.grade:
        parser.error("choose --build or --grade")

    try:
        if args.build:
            if args.reviewer:
                parser.error("--reviewer is only valid with --grade")
            output = Path(args.output) if args.output else DEFAULT_QUIZ
            report = build_quiz(args.manifest, output)
            print(
                f"wrote blind quiz for {report['case_count']} cases to {report['output']} "
                f"(manifest={report['manifest_id']} sha256={report['manifest_sha256'][:12]})"
            )
            print("no labels, decision hints, categories, or label notes are embedded")
            return 0

        if not args.reviewer:
            parser.error("--reviewer is required with --grade")
        output = Path(args.output) if args.output else default_agreement_path(args.reviewer)
        report = grade_review(args.grade, args.reviewer, args.manifest, output)
        print_summary(report, output)
        return 0
    except (QuizError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
