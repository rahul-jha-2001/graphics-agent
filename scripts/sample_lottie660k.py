#!/usr/bin/env python3
"""Sample LottieGPT/LottieAnimation-660K into harness tasks (prompt = caption, reference = Lottie JSON).

The dataset is gated (accept its terms on Hugging Face) and licensed CC BY-NC-SA 4.0:
research and non-commercial use only. Authenticate with `hf auth login` or HF_TOKEN.

  python scripts/sample_lottie660k.py --shard 0 --count 50 --out tasks/lottie660k

Writes <out>/tasks.jsonl (harness dataset format), <out>/references/<id>.json and <out>/profile.json.
Only the ~44 MB metadata shard is downloaded; the MP4 preview shards are not needed.
"""
import argparse
import collections
import io
import json
import random
from pathlib import Path

REPO = "LottieGPT/LottieAnimation-660K"
SHARDS = 68
# Layer types the harness toolset can create today: solid, shape, text (plus null as a helper).
BUILDABLE_TYPES = {1, 3, 4, 5}
LAYER_NAMES = {0: "precomp", 1: "solid", 2: "image", 3: "null", 4: "shape", 5: "text", 6: "audio"}


def rows(path):
    import zstandard
    with open(path, "rb") as raw:
        stream = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(raw), encoding="utf-8")
        for line in stream:
            if line.strip():
                yield json.loads(line)


def all_layers(doc):
    yield from doc.get("layers", [])
    for asset in doc.get("assets", []):
        yield from asset.get("layers", []) or []


def animated_props(value):
    if isinstance(value, dict):
        hit = int(value.get("a") == 1 and isinstance(value.get("k"), list))
        return hit + sum(animated_props(v) for v in value.values())
    if isinstance(value, list):
        return sum(animated_props(v) for v in value)
    return 0


def profile(doc):
    layers = list(all_layers(doc))
    types = collections.Counter(LAYER_NAMES.get(l.get("ty"), str(l.get("ty"))) for l in layers)
    fr = doc.get("fr") or 0
    return {
        "layers": len(layers),
        "layer_types": dict(types),
        "animated_props": animated_props(doc.get("layers", [])) + animated_props(doc.get("assets", [])),
        "duration": round((doc.get("op", 0) - doc.get("ip", 0)) / fr, 3) if fr else None,
        "fps": fr,
        "size": [doc.get("w"), doc.get("h")],
        "has_images": any(l.get("ty") == 2 for l in layers) or any("p" in a and "u" in a for a in doc.get("assets", [])),
        "has_expressions": '"x":' in json.dumps(doc.get("layers", []))[:2_000_000],
        "buildable_layer_types": all(l.get("ty") in BUILDABLE_TYPES for l in doc.get("layers", [])),
    }


def within_rollout_bounds(doc):
    fr = doc.get("fr") or 0
    duration = (doc.get("op", 0) - doc.get("ip", 0)) / fr if fr else 0
    return (0 < (doc.get("w") or 0) <= 4096 and 0 < (doc.get("h") or 0) <= 4096
            and 0 < fr <= 120 and 0 < duration <= 120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--count", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("tasks/lottie660k"))
    ap.add_argument("--max-json-bytes", type=int, default=300_000, help="skip very large animations")
    ap.add_argument("--max-layers", type=int, default=None, help="optional: keep only simple animations")
    ap.add_argument("--buildable-only", action="store_true", help="only solid/shape/text/null top-level layers")
    ap.add_argument("--local", type=Path, help="use an already-downloaded metadata shard instead of the Hub")
    a = ap.parse_args()
    if not 0 <= a.shard < SHARDS:
        ap.error(f"--shard must be in 0..{SHARDS - 1}")

    if a.local:
        path = a.local
    else:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(REPO, f"data/metadata-{a.shard:05d}-of-{SHARDS:05d}.jsonl.zst", repo_type="dataset")

    rng = random.Random(a.seed)
    sample, seen, skipped = [], 0, collections.Counter()
    stats = collections.Counter()
    for row in rows(path):
        doc = row.get("lottie_json")
        if isinstance(doc, str):
            doc = json.loads(doc)
        size = len(json.dumps(doc, separators=(",", ":")))
        prof = profile(doc)
        stats["rows"] += 1
        stats["buildable_layer_types"] += prof["buildable_layer_types"]
        stats["has_images"] += prof["has_images"]
        stats["has_precomp"] += "precomp" in prof["layer_types"]
        reason = ("caption" if not (row.get("caption") or "").strip() else
                  "bounds" if not within_rollout_bounds(doc) else
                  "too_large" if size > a.max_json_bytes else
                  "too_many_layers" if a.max_layers and prof["layers"] > a.max_layers else
                  "not_buildable" if a.buildable_only and not prof["buildable_layer_types"] else None)
        if reason:
            skipped[reason] += 1
            continue
        seen += 1  # reservoir sampling over eligible rows
        item = (row, doc, size, prof)
        if len(sample) < a.count:
            sample.append(item)
        elif (j := rng.randrange(seen)) < a.count:
            sample[j] = item

    refs = a.out / "references"
    refs.mkdir(parents=True, exist_ok=True)
    with (a.out / "tasks.jsonl").open("w") as f:
        for row, doc, size, prof in sample:
            ref = refs / f"{row['id']}.json"
            ref.write_text(json.dumps(doc, separators=(",", ":")))
            f.write(json.dumps({
                "task_id": f"l660k-{row['id']}",
                "prompt": row["caption"].strip(),
                "constraints": {"width": doc["w"], "height": doc["h"], "fps": doc["fr"], "duration": prof["duration"]},
                "reference_lottie": f"references/{ref.name}",
                "split": "eval",
                "metadata": {"source": REPO, "source_id": row["id"], "name": row.get("name"), "tags": row.get("tag"),
                             "license": "CC-BY-NC-SA-4.0", "permitted_use": "Academic research and non-commercial use only",
                             "json_bytes": size, **prof},
            }) + "\n")
    summary = {"shard": a.shard, "scanned": stats["rows"], "eligible": seen, "sampled": len(sample),
               "skipped": dict(skipped),
               "shard_share": {k: round(stats[k] / max(1, stats["rows"]), 3)
                               for k in ("buildable_layer_types", "has_images", "has_precomp")},
               "sample_layers_median": sorted(p["layers"] for *_, p in sample)[len(sample) // 2] if sample else None}
    (a.out / "profile.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
