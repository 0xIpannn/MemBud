#!/usr/bin/env python3
"""Offline reanalysis of recorded Phase 2 Mem0 proposals (no network, $0).

Reads the proposals recorded by ``run_halumem_mem0.py`` and re-evaluates all four
conditions on identical transitions under (a) the recorded (strict StagedMem0)
evidence and (b) the pre-registered normalized evidence policy at each theta, with
silver labels under both omission scopes (see docs/halumem-evaluation.md). The
verifier is unchanged; only host-side evidence derivation and silver scope vary.
Outputs counts, rates and ids only (no HaluMem text) into the gitignored run dir.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from membud.conversation import verify_with_transcript  # noqa: E402
from membud.fairness import verify_text_fair  # noqa: E402
from membud.halumem import (  # noqa: E402
    NORMALIZED_EVIDENCE_THETAS,
    OMISSION_SCOPES,
    derive_silver_label,
    load_halumem_dataset,
    ground_transition,
    persona_name_from_info,
)
from membud.grounding import CoverageGrounder  # noqa: E402
from membud.paired_stats import mcnemar_exact, wilson  # noqa: E402
from membud.schemas import Transition  # noqa: E402
from membud.verification import verify  # noqa: E402

CONDITIONS = ("unguarded", "graph", "text", "text_fair")


def decide(cond: str, transition: Transition, transcript, grounded: bool) -> str:
    if cond == "unguarded":
        return "ACCEPT"
    if not grounded:
        return (verify_text_fair(transition) if cond == "text_fair" else verify(transition, cond)).decision
    if cond == "text_fair":
        return verify_with_transcript(transition, transcript, "text", fair_text=True).decision
    return verify_with_transcript(transition, transcript, cond, fair_text=False).decision


def rate(n: int, d: int) -> dict:
    return wilson(n, d)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--halumem-path", required=True)
    ap.add_argument("--dataset-revision", default="")
    args = ap.parse_args()
    run = Path(args.run_dir)

    _, users = load_halumem_dataset(args.halumem_path, dataset_revision=args.dataset_revision or "local")
    lookup = {f"{u.uuid}__{s.session_id}": (u, s) for u in users for s in u.sessions}
    cases = []
    for f in sorted((run / "proposals").glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        u, s = lookup[rec["case_id"]]
        cases.append((rec["case_id"], Transition.from_dict(rec["transition"]), s.to_transcript(user_uuid=u.uuid), u, s))

    out: dict = {"schema": "membud-halumem-phase2-reanalysis-v1", "n_proposals": len(cases),
                 "validated": False, "silver_is_provisional": True,
                 "silver_scopes": {}, "tracks": {}, "sanity": {}}

    labels = {}
    for scope in OMISSION_SCOPES:
      for nn in (False, True):
        scope_key = scope + ("+numnorm" if nn else "")
        per = [derive_silver_label(t, s, user_uuid=u.uuid, omission_scope=scope, normalize_numerals=nn) for _, t, _, u, s in cases]
        labels[scope_key] = per
        cats: dict[str, int] = {}
        for lab in per:
            cats[lab.primary_label] = cats.get(lab.primary_label, 0) + 1
        out["silver_scopes"][scope_key] = {
            "primary_category_counts": cats,
            "valid": rate(cats.get("valid", 0), len(per)),
            "recall_gap_proposals(any gold point unmatched)": rate(sum(bool(l.omitted_gold_points) for l in per), len(per)),
        }

    tracks = {
        "recorded_strict": None,
        **{f"normalized_theta_{th}": (th, False) for th in NORMALIZED_EVIDENCE_THETAS},
        **{f"normalizedN_theta_{th}": (th, True) for th in NORMALIZED_EVIDENCE_THETAS},
        **{f"normalizedN_numnorm_theta_{th}": (th, "numnorm") for th in NORMALIZED_EVIDENCE_THETAS},
    }
    decisions: dict = {}
    for tname, theta in tracks.items():
        for grounded in (False, True):
            key = f"{tname}|{'with' if grounded else 'without'}_transcript_grounding"
            decisions[key] = {c: [] for c in CONDITIONS}
            ev_per_claim = []
            reasons: dict[str, int] = {}
            reasons_valid: dict[str, int] = {}
            for cid, t, tr, u, s in cases:
                if theta is None:
                    tt = t
                else:
                    th, use_n = theta
                    use_nn = use_n == "numnorm"
                    tt, report = ground_transition(t, tr, CoverageGrounder(
                        min_coverage=th,
                        persona_name=persona_name_from_info(u.persona_info) if use_n else None,
                        fold_inflection=bool(use_n),
                        normalize_numerals=use_nn,
                    ))
                    for cg in report.claims:
                        reasons[cg.reason] = reasons.get(cg.reason, 0) + 1
                        if labels["prior_only"][len(ev_per_claim)].primary_label == "valid":
                            reasons_valid[cg.reason] = reasons_valid.get(cg.reason, 0) + 1
                before = {c for r in t.before for c in r.claims}
                added = [c for r in t.candidate for c in r.claims if c not in before]
                sup = {c for ev in tt.evidence for c in ev.supports}
                ev_per_claim.append((sum(c in sup for c in added), len(added)))
                for c in CONDITIONS:
                    decisions[key][c].append(decide(c, tt, tr, grounded))
            out["tracks"][key] = {"claims_with_evidence": [sum(a for a, _ in ev_per_claim), sum(b for _, b in ev_per_claim)]}
            if theta is not None:
                # Why added claims got no evidence (claim counts; `already_supported` = kept recorded evidence).
                out["tracks"][key]["claim_grounding_reasons"] = dict(sorted(reasons.items()))
                out["tracks"][key]["claim_grounding_reasons_in_silver_valid_proposals"] = dict(sorted(reasons_valid.items()))

    for key, dec in decisions.items():
        for scope in labels:
            lab = labels[scope]
            valid = [i for i, l in enumerate(lab) if l.primary_label == "valid"]
            faulty = [i for i, l in enumerate(lab) if l.primary_label != "valid"]
            block = {}
            for c in CONDITIONS:
                holds = [d == "HOLD" for d in dec[c]]
                block[c] = {
                    "hold_rate_overall": rate(sum(holds), len(holds)),
                    "catch_on_silver_faulty": rate(sum(holds[i] for i in faulty), len(faulty)),
                    "false_hold_on_silver_valid": rate(sum(holds[i] for i in valid), len(valid)),
                }
            g, tf = dec["graph"], dec["text_fair"]
            b = sum(1 for x, y in zip(g, tf) if x == "HOLD" and y != "HOLD")
            cc = sum(1 for x, y in zip(g, tf) if x != "HOLD" and y == "HOLD")
            block["graph_vs_text_fair_discordant"] = {"graph_only_hold": b, "text_fair_only_hold": cc, "mcnemar_p": mcnemar_exact(b, cc)}
            out["tracks"][key].setdefault("by_silver_scope", {})[scope] = block

    # Sanity: normalized theta=1.0 should reproduce recorded strict decisions.
    for grounded in ("without", "with"):
        a = decisions[f"recorded_strict|{grounded}_transcript_grounding"]
        b_ = decisions[f"normalized_theta_1.0|{grounded}_transcript_grounding"]
        out["sanity"][f"theta_1.0_matches_recorded_{grounded}_grounding"] = {
            c: sum(x == y for x, y in zip(a[c], b_[c])) for c in CONDITIONS} | {"of": len(cases)}

    (run / "reanalysis.json").write_text(json.dumps(out, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    print(f"proposals={len(cases)}")
    for scope, v in out["silver_scopes"].items():
        print(f"silver[{scope}] categories={v['primary_category_counts']}")
    for key, v in out["tracks"].items():
        n, d = v["claims_with_evidence"]
        print(f"\n{key}  (evidence for {n}/{d} added claims)")
        for scope in v["by_silver_scope"]:
            blk = v["by_silver_scope"][scope]
            for c in ("graph", "text_fair", "text"):
                x = blk[c]
                print(f"  [{scope:17s}] {c:9s} holds={x['hold_rate_overall']['numerator']}/{x['hold_rate_overall']['denominator']} "
                      f"catch={x['catch_on_silver_faulty']['numerator']}/{x['catch_on_silver_faulty']['denominator']} "
                      f"false_hold_valid={x['false_hold_on_silver_valid']['numerator']}/{x['false_hold_on_silver_valid']['denominator']}")
    print("\nsanity", json.dumps(out["sanity"]))
    print(f"wrote {run / 'reanalysis.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
