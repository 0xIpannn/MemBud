#!/usr/bin/env python3
"""Claim-level blind audit of Phase 2 silver labels (offline; no network, $0).

Audit v1 asked reviewers for the verifier's HOLD/ACCEPT from the recorded (mostly
empty) strict evidence, so it could not test the silver labels. This audit asks the
question the silver rule actually answers: is each added claim supported by what
the user said in the session? It shows the session dialogue and the prior memory,
hides the silver verdict, and grades agreement per claim.

  build: scripts/halumem_claim_audit.py build --run-dir DIR --halumem-path FILE
  grade: scripts/halumem_claim_audit.py grade --run-dir DIR --answers FILE

Silver rule used: prior_only scope with numeral normalization (the pre-registered
numnorm rule). A claim's silver verdict is "unsupported" iff it is in
`unsupported_added_claims`. Output (HaluMem text!) goes to the gitignored run dir.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.halumem import derive_silver_label, load_halumem_dataset  # noqa: E402
from membud.paired_stats import wilson  # noqa: E402
from membud.schemas import Transition  # noqa: E402

MANIFEST_ID = "membud-halumem-claim-audit-v1"
OUT = "claim-audit"
DECISIONS = ("SUPPORTED", "UNSUPPORTED", "UNSURE")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(run: Path, halumem: str, revision: str, proposals_n: int, per_proposal: int, seed: int) -> None:
    _, users = load_halumem_dataset(halumem, dataset_revision=revision or "local")
    lookup = {f"{u.uuid}__{s.session_id}": (u, s) for u in users for s in u.sessions}
    rng = random.Random(seed)
    flagged_pool, clean_pool = [], []
    for f in sorted((run / "proposals").glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        u, s = lookup[rec["case_id"]]
        t = Transition.from_dict(rec["transition"])
        lab = derive_silver_label(t, s, user_uuid=u.uuid, omission_scope="prior_only", normalize_numerals=True)
        before = {c for r in t.before for c in r.claims}
        added = sorted({c for r in t.candidate for c in r.claims if c not in before})
        bad = [c for c in added if c in set(lab.unsupported_added_claims)]
        good = [c for c in added if c not in set(lab.unsupported_added_claims)]
        entry = (rec["case_id"], t, s, bad, good)
        if bad and len(added) >= per_proposal:
            flagged_pool.append(entry)
        elif not bad and len(good) >= per_proposal:
            clean_pool.append(entry)
    half = proposals_n // 2
    picked = rng.sample(flagged_pool, min(half, len(flagged_pool))) + rng.sample(
        clean_pool, min(proposals_n - half, len(clean_pool))
    )
    items, keys = [], []
    for case_id, t, s, bad, good in picked:
        n_bad = min(len(bad), max(1, per_proposal - 1)) if bad else 0
        chosen = rng.sample(bad, n_bad) + rng.sample(good, min(len(good), per_proposal - n_bad))
        for claim in chosen:
            item_id = hashlib.sha256(f"{MANIFEST_ID}|{case_id}|{claim}".encode()).hexdigest()[:12]
            items.append({
                "item_id": item_id,
                "case_id": case_id,
                "claim": claim,
                "prior_memory": [c for r in t.before for c in r.claims],
                "dialogue": [{"role": x.role, "content": x.content} for x in s.dialogue],
            })
            keys.append({"item_id": item_id, "case_id": case_id, "silver": "UNSUPPORTED" if claim in bad else "SUPPORTED"})
    rng.shuffle(items)
    out = run / OUT
    out.mkdir(parents=True, exist_ok=True)
    (out / "claim-items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    (out / "claim-silver-key.jsonl").write_text("".join(json.dumps(k) + "\n" for k in sorted(keys, key=lambda k: k["item_id"])), encoding="utf-8")
    manifest = {"manifest_id": MANIFEST_ID, "seed": seed, "items": len(items), "proposals": len(picked),
                "items_sha256": _sha(out / "claim-items.jsonl"), "key_sha256": _sha(out / "claim-silver-key.jsonl"),
                "silver_rule": "prior_only+numeral_normalization", "item_ids": [i["item_id"] for i in items]}
    (out / "claim-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "claim-quiz.html").write_text(_html(items, manifest), encoding="utf-8")
    nb = sum(k["silver"] == "UNSUPPORTED" for k in keys)
    print(f"built {len(items)} claims from {len(picked)} proposals ({nb} silver-unsupported, {len(keys) - nb} silver-supported)")
    print(f"quiz: {out / 'claim-quiz.html'}")


def _html(items: list[dict], manifest: dict) -> str:
    data = json.dumps({"manifest_id": manifest["manifest_id"], "manifest_sha256": manifest["items_sha256"], "items": items}, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claim audit</title><style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--bd:#d0d0d0;--card:#f7f7f7;--u:#e8f0fe;--a:#fff4e0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e8e8e8;--mut:#9aa;--bd:#3a3d44;--card:#1f2228;--u:#1d2b45;--a:#33291a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:860px;padding:16px}}
.case{{border:1px solid var(--bd);border-radius:8px;background:var(--card);padding:12px;margin:16px 0}}
.claim{{font-size:17px;font-weight:600;margin:6px 0 10px}}.turn{{padding:6px 8px;margin:4px 0;border-radius:6px;white-space:pre-wrap}}
.user{{background:var(--u)}}.assistant{{background:var(--a);color:var(--mut)}}details{{margin:6px 0}}summary{{cursor:pointer;color:var(--mut)}}
label{{margin-right:14px}}input[type=text]{{width:100%;box-sizing:border-box}}button{{padding:8px 14px}}
</style></head><body>
<h1>Claim audit</h1>
<p>Each card has <b>one claim</b> that a memory system added after reading one session. Decide whether <b>the user said it</b> in the session dialogue (blue turns).</p>
<ul><li><b>SUPPORTED</b>: the user's words state it or clearly imply it, even in a different format (e.g. "1983-05-06" and "May 6, 1983"; "75000" and "$75,000"), or it combines facts the user did state.</li>
<li><b>UNSUPPORTED</b>: it adds a fact, number, name or detail the user did not give. Something said only by the assistant (orange) does not count unless the user confirms it.</li>
<li><b>Ignore a run-date phrase</b> such as "as of November 2026" or "around November 2026" (added by the memory system, not the user). Judge the rest of the claim.</li>
<li><b>UNSURE</b>: you cannot tell. The prior memory (collapsed) is context only.</li></ul>
<p>Reviewer name: <input id="rev" type="text" placeholder="your name" style="width:240px"> <span id="prog"></span></p>
<div id="cases"></div>
<p><button id="dl">Download answers JSON</button></p>
<script>
const D={data};const KEY="claim-audit-"+D.manifest_sha256.slice(0,12);
let S={{}};try{{S=JSON.parse(localStorage.getItem(KEY)||"{{}}")}}catch(e){{}}
function save(){{try{{localStorage.setItem(KEY,JSON.stringify(S))}}catch(e){{}}}}
function esc(s){{return s.replace(/[&<>]/g,c=>({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c]))}}
const root=document.getElementById("cases");
D.items.forEach((it,i)=>{{const d=document.createElement("div");d.className="case";
d.innerHTML=`<div>#${{i+1}} of ${{D.items.length}}</div><div class="claim">${{esc(it.claim)}}</div>
<details><summary>Session dialogue (${{it.dialogue.length}} turns)</summary>${{it.dialogue.map(t=>`<div class="turn ${{t.role}}"><b>${{t.role}}:</b> ${{esc(t.content)}}</div>`).join("")}}</details>
<details><summary>Prior memory (${{it.prior_memory.length}} claims)</summary>${{it.prior_memory.map(c=>`<div class="turn">${{esc(c)}}</div>`).join("")||"<i>empty</i>"}}</details>
<div>${{["SUPPORTED","UNSUPPORTED","UNSURE"].map(v=>`<label><input type="radio" name="r${{i}}" value="${{v}}"> ${{v}}</label>`).join("")}}</div>
<input type="text" placeholder="optional note" data-n="${{i}}">`;
root.appendChild(d);const cur=S[it.item_id]||{{}};
d.querySelectorAll("input[type=radio]").forEach(r=>{{if(cur.decision===r.value)r.checked=true;r.onchange=()=>{{S[it.item_id]={{decision:r.value,note:(S[it.item_id]||{{}}).note||""}};save();prog()}}}});
const n=d.querySelector("[data-n]");n.value=cur.note||"";n.oninput=()=>{{S[it.item_id]=Object.assign({{decision:null}},S[it.item_id],{{note:n.value}});save()}}}});
function prog(){{const a=D.items.filter(it=>(S[it.item_id]||{{}}).decision).length;document.getElementById("prog").textContent=a+" / "+D.items.length+" answered"}}prog();
document.getElementById("dl").onclick=()=>{{const ans={{}};D.items.forEach(it=>ans[it.item_id]={{decision:(S[it.item_id]||{{}}).decision||null,note:(S[it.item_id]||{{}}).note||""}});
const b=new Blob([JSON.stringify({{reviewer:document.getElementById("rev").value,manifest_id:D.manifest_id,manifest_sha256:D.manifest_sha256,answers:ans}},null,2)],{{type:"application/json"}});
const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download=D.manifest_id+"-answers-"+(document.getElementById("rev").value||"reviewer")+".json";a.click()}};
</script></body></html>"""


def grade(run: Path, answers_path: str) -> None:
    out = run / OUT
    man = json.loads((out / "claim-manifest.json").read_text())
    ans = json.loads(Path(answers_path).read_text())
    if ans.get("manifest_sha256") != man["items_sha256"]:
        raise SystemExit("answers do not match this audit's manifest")
    key = {k["item_id"]: k for k in map(json.loads, (out / "claim-silver-key.jsonl").read_text().splitlines())}
    rows = []
    for iid, k in key.items():
        d = (ans["answers"].get(iid) or {}).get("decision")
        rows.append((k["silver"], d))
    answered = [(s, d) for s, d in rows if d in ("SUPPORTED", "UNSUPPORTED")]
    unsure = sum(d == "UNSURE" for _, d in rows)
    blank = sum(d is None for _, d in rows)
    agree = sum(s == d for s, d in answered)
    tp = sum(s == d == "UNSUPPORTED" for s, d in answered)
    tn = sum(s == d == "SUPPORTED" for s, d in answered)
    fp = sum(s == "UNSUPPORTED" and d == "SUPPORTED" for s, d in answered)  # silver says unsupported, human supported
    fn = sum(s == "SUPPORTED" and d == "UNSUPPORTED" for s, d in answered)
    n = len(answered)
    pe = ((tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)) / (n * n) if n else 0
    kappa = ((agree / n) - pe) / (1 - pe) if n and pe < 1 else None
    res = {"reviewer": ans.get("reviewer"), "items": len(rows), "answered_decisive": n, "unsure": unsure, "blank": blank,
           "agreement": wilson(agree, n), "cohens_kappa": kappa,
           "silver_unsupported_human_supported": fp, "silver_supported_human_unsupported": fn,
           "silver_unsupported_confirmed": {"numerator": tp, "denominator": tp + fp},
           "silver_supported_confirmed": {"numerator": tn, "denominator": tn + fn},
           "note": "claim-level; silver = prior_only + numeral normalization; unvalidated sample"}
    (out / "claim-agreement.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--run-dir", required=True)
    b.add_argument("--halumem-path", required=True)
    b.add_argument("--dataset-revision", default="")
    b.add_argument("--proposals", type=int, default=24)
    b.add_argument("--per-proposal", type=int, default=3)
    b.add_argument("--seed", type=int, default=20261121)
    g = sub.add_parser("grade")
    g.add_argument("--run-dir", required=True)
    g.add_argument("--answers", required=True)
    a = ap.parse_args()
    if a.cmd == "build":
        build(Path(a.run_dir), a.halumem_path, a.dataset_revision, a.proposals, a.per_proposal, a.seed)
    else:
        grade(Path(a.run_dir), a.answers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
