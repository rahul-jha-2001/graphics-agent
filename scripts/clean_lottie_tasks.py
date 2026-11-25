#!/usr/bin/env python3
"""Clean a Lottie task set so the reward compares like with like.

For every task with a reference_lottie:
  1. Canonicalize: import the reference into EffectCraft and export it again. The agent's output
     always comes from EffectCraft's exporter, so the "truth" must too; otherwise fields only one
     exporter writes (After Effects' `ix`, `l`, ...) cap the similarity far below 1.
  2. Stability: round-trip the canonical file once more; it must score ~1.0 against itself.
  3. Validity: the canonical reference must pass the same grader gate and rendered-motion check
     the agent faces under the task's constraints, or the task is impossible.
  4. Losses: EffectCraft import warnings (unsupported effects, dropped glyphs, ...) mean the
     canonical file is not the animation the caption describes; such tasks are excluded unless
     --keep-lossy is given.

  python scripts/clean_lottie_tasks.py tasks/lottie660k/tasks.jsonl

Writes <dir>/references_canonical/<name>.json, <dir>/tasks.clean.jsonl (kept tasks, reference
pointing at the canonical file, original kept in metadata) and <dir>/cleaning_report.json.
"""
import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graphics_agent.common.io import dump, read_json  # noqa: E402
from graphics_agent.environments.effectcraft.rollout import cli, render_samples  # noqa: E402
from graphics_agent.tasks.lottie.grade import grade, similarity  # noqa: E402


def roundtrip(src, dst, work):
    project = work / (dst.stem + ".ecproj")
    imported = cli("--empty", "exec", "file.importLottie", "--params", json.dumps({"path": str(src)}),
                   "--save-as", str(project), cwd=work)
    cli("--project", str(project), "exec", "file.exportLottie", "--params",
        json.dumps({"comp": imported["result"]["comp"], "path": str(dst)}), cwd=work)
    return imported["result"].get("warnings", [])


def clean(row, base, out_dir, min_stability):
    ref = (base / row["reference_lottie"]).resolve()
    canonical = out_dir / ref.name
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        try:
            warnings = roundtrip(ref, canonical, work)
            again = work / "again.json"
            roundtrip(canonical, again, work)
            doc = read_json(canonical)
            result = {
                "task_id": row["task_id"],
                "raw_similarity": round(similarity(read_json(ref), doc), 4),
                "stability": round(similarity(doc, read_json(again)), 4),
                "import_warnings": warnings,
            }
            gate = grade(doc, row.get("constraints", {}))
            result["gate_errors"] = gate["errors"]
            if gate["valid"]:
                result["changing_pixels"] = render_samples(canonical, work, doc)["changing_pixels"]
        except Exception as exc:  # engine failures make the task unusable, not the whole run
            return {"task_id": row["task_id"], "error": str(exc)[:500], "keep": False, "reasons": ["engine_error"]}
    reasons = []
    if result["gate_errors"]:
        reasons.append("fails_gate")
    elif not result.get("changing_pixels"):
        reasons.append("no_visible_motion")
    if result["stability"] < min_stability:
        reasons.append("unstable_roundtrip")
    if warnings:
        reasons.append("lossy_import")
    result.update(reasons=reasons, canonical=str(canonical.relative_to(base)))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", type=Path)
    ap.add_argument("--min-stability", type=float, default=0.99)
    ap.add_argument("--keep-lossy", action="store_true", help="keep tasks whose import dropped features")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()

    base = a.tasks.resolve().parent
    out_dir = base / "references_canonical"
    out_dir.mkdir(exist_ok=True)
    rows = [json.loads(l) for l in a.tasks.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("reference_lottie")]
    with ThreadPoolExecutor(a.workers) as pool:
        results = list(pool.map(lambda r: clean(r, base, out_dir, a.min_stability), rows))

    kept = []
    for row, res in zip(rows, results):
        blocking = [x for x in res.get("reasons", []) if not (a.keep_lossy and x == "lossy_import")]
        res["keep"] = not blocking and "error" not in res
        if res["keep"]:
            meta = {**row.get("metadata", {}), "source_reference": row["reference_lottie"],
                    "raw_roundtrip_similarity": res["raw_similarity"], "import_warnings": res["import_warnings"]}
            kept.append({**row, "reference_lottie": res["canonical"], "metadata": meta})
    with (base / "tasks.clean.jsonl").open("w") as f:
        for row in kept:
            f.write(json.dumps(row) + "\n")

    counts = {}
    for res in results:
        for reason in res.get("reasons", []):
            counts[reason] = counts.get(reason, 0) + 1
    ok = [r for r in results if "error" not in r]
    summary = {
        "tasks": len(rows), "kept": len(kept), "excluded": len(rows) - len(kept), "exclusion_reasons": counts,
        "raw_similarity_mean": round(sum(r["raw_similarity"] for r in ok) / max(1, len(ok)), 4),
        "canonical_stability_mean": round(sum(r["stability"] for r in ok) / max(1, len(ok)), 4),
        "canonical_stability_min": min((r["stability"] for r in ok), default=None),
    }
    dump(base / "cleaning_report.json", {"summary": summary, "tasks": results})
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
