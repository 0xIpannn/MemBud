#!/usr/bin/env python3
"""Dual-reviewer claim audit on a fresh sample, with inter-rater agreement.

build : draws claims not in audit v1/v3: every fresh judge-flagged (unsupported) claim plus a
        seeded random draw of judge-supported claims. Both reviewers label the SAME blind file.
        The rubric hash (docs/halumem-grounding-rubric.md) is recorded in the manifest.
grade : takes two answer files; reports inter-rater kappa and, per reviewer, judge flag
        precision and miss rate. States explicitly when the two reviewers disagree in direction.
Offline; no model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from membud.paired_stats import wilson  # noqa: E402

spec = importlib.util.spec_from_file_location("claim_audit", ROOT / "scripts" / "halumem_claim_audit.py")
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

OUT = "claim-audit-dual"
RUBRIC = ROOT / "docs" / "halumem-grounding-rubric.md"
MANIFEST_ID = "halumem-claim-audit-dual-v1"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a == "UNSUPPORTED" for a, _ in pairs) / n
    pb = sum(b == "UNSUPPORTED" for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def build(a) -> None:
    run = Path(a.run_dir)
    seen = set()
    for d in ("claim-audit", "claim-audit-v2"):
        for line in (run / d / "claim-items.jsonl").read_text().splitlines():
            i = json.loads(line)
            seen.add((i["case_id"], i["claim"]))
    verdicts = [json.loads(l) for l in (run / "llm-claim-verdicts-accepted-replyctx.jsonl").read_text().splitlines()]
    fresh = [v for v in verdicts if (v["case_id"], v["claim"]) not in seen]
    flagged = [v for v in fresh if not v["judge_supported"]]
    rng = random.Random(a.seed)
    supported = rng.sample([v for v in fresh if v["judge_supported"]], a.supported)
    chosen = flagged + supported
    users = {}
    for line in Path(a.halumem_path).read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        users[u["uuid"]] = u
    items, keys = [], []
    for v in chosen:
        pf = json.loads((run / "proposals" / f"{v['case_id']}.json").read_text())
        s = users[pf["user_uuid"]]["sessions"][pf["session_index"] - 1]
        before = [c for r in pf["transition"]["before"] for c in r["claims"]]
        iid = hashlib.sha256(f"{MANIFEST_ID}|{v['case_id']}|{v['claim']}".encode()).hexdigest()[:12]
        items.append({"item_id": iid, "case_id": v["case_id"], "claim": v["claim"], "prior_memory": before,
                      "dialogue": [{"role": t["role"], "content": t["content"]} for t in s["dialogue"]]})
        keys.append({"item_id": iid, "case_id": v["case_id"], "judge": "SUPPORTED" if v["judge_supported"] else "UNSUPPORTED"})
    rng.shuffle(items)
    out = run / OUT
    out.mkdir(parents=True, exist_ok=True)
    (out / "claim-items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    (out / "claim-judge-key.jsonl").write_text("".join(json.dumps(k) + "\n" for k in sorted(keys, key=lambda k: k["item_id"])), encoding="utf-8")
    man = {"manifest_id": MANIFEST_ID, "seed": a.seed, "items": len(items), "items_sha256": sha(out / "claim-items.jsonl"),
           "rubric_sha256": sha(RUBRIC), "judge_flagged": len(flagged), "judge_supported": len(supported),
           "excluded_already_labelled": len(seen)}
    (out / "claim-manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    html = ca._html(items, {"manifest_id": MANIFEST_ID, "items_sha256": man["items_sha256"]})
    new_rules = (
        "<ul><li><b>SUPPORTED</b>: (1) the user's words state or clearly imply it (paraphrase and number/date format changes are fine; "
        "combining facts the user stated is fine); (2) the assistant introduced it and a later user turn <b>adopts that specific fact</b> "
        "(restates/uses it, agrees in a way that refers to it, will act on it, builds on it); or (3) it follows directly from something "
        "the user said earlier or from the prior memory.</li>"
        "<li><b>UNSUPPORTED</b>: it adds a fact, number, name or detail not covered above. Generic thanks or acknowledgement "
        "(\"thank you\", \"great ideas\") is <b>not</b> adoption. A fact the user rejected is unsupported.</li>"
        "<li><b>Ignore a run-date phrase</b> such as \"as of November 2026\".</li>"
        "<li><b>UNSURE</b>: you cannot decide. Rubric: docs/halumem-grounding-rubric.md (hash in manifest). Please label alone, without discussing.</li></ul>")
    html = re.sub(r"<ul><li><b>SUPPORTED</b>.*?</ul>", lambda _m: new_rules, html, count=1, flags=re.S)
    (out / "claim-quiz.html").write_text(html, encoding="utf-8")
    print(f"built {len(items)} claims ({len(flagged)} judge-flagged, {len(supported)} judge-supported) -> {out / 'claim-quiz.html'}")


def grade(a) -> None:
    run = Path(a.run_dir)
    out = run / OUT
    man = json.loads((out / "claim-manifest.json").read_text())
    key = {k["item_id"]: k["judge"] for k in map(json.loads, (out / "claim-judge-key.jsonl").read_text().splitlines())}
    rev = {}
    for path in (a.reviewer1, a.reviewer2):
        ans = json.loads(Path(path).read_text())
        if ans.get("manifest_sha256") != man["items_sha256"]:
            raise SystemExit(f"{path} does not match this manifest")
        rev[ans.get("reviewer") or path] = {i: (v or {}).get("decision") for i, v in ans["answers"].items()}
    names = list(rev)
    if len(names) != 2:
        raise SystemExit("need two distinct reviewer names")
    r1, r2 = rev[names[0]], rev[names[1]]
    dec = ("SUPPORTED", "UNSUPPORTED")
    both = [(r1[i], r2[i]) for i in key if r1.get(i) in dec and r2.get(i) in dec]
    res = {"manifest": man["manifest_id"], "rubric_sha256": man["rubric_sha256"], "reviewers": names, "items": len(key),
           "both_decisive": len(both), "unsure_or_blank": len(key) - len(both),
           "inter_rater_agreement": wilson(sum(x == y for x, y in both), len(both)), "cohens_kappa": kappa(both), "per_reviewer": {}}
    for n_, r in rev.items():
        flagged = [i for i, j in key.items() if j == "UNSUPPORTED" and r.get(i) in dec]
        sup = [i for i, j in key.items() if j == "SUPPORTED" and r.get(i) in dec]
        res["per_reviewer"][n_] = {
            "judge_flag_precision": wilson(sum(r[i] == "UNSUPPORTED" for i in flagged), len(flagged)),
            "judge_miss_rate": wilson(sum(r[i] == "UNSUPPORTED" for i in sup), len(sup)),
            "agreement_with_judge": wilson(sum(r[i] == key[i] for i in flagged + sup), len(flagged + sup))}
    p = [v["judge_flag_precision"]["rate"] if isinstance(v["judge_flag_precision"], dict) and "rate" in v["judge_flag_precision"] else None
         for v in res["per_reviewer"].values()]
    res["flip_note"] = ("compare per_reviewer flag precision: if one reviewer's interval excludes the other's point estimate, "
                        "report that the judge's value flips between reviewers; do not headline one number")
    res["validated"] = False
    (out / "dual-agreement.json").write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--run-dir", required=True)
    b.add_argument("--halumem-path", required=True)
    b.add_argument("--supported", type=int, default=28)
    b.add_argument("--seed", type=int, default=20261122)
    g = sub.add_parser("grade")
    g.add_argument("--run-dir", required=True)
    g.add_argument("--reviewer1", required=True)
    g.add_argument("--reviewer2", required=True)
    a = ap.parse_args()
    build(a) if a.cmd == "build" else grade(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
