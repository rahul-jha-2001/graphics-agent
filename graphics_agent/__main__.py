import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

from graphics_agent.common.io import MODEL_CONFIG, ROOT, config, dump, run_root
from graphics_agent.inference.client import probe, serve
from graphics_agent.operations import calibrate, doctor, evaluate, rollout
from graphics_agent.tasks.lottie.data import Task, dataset


def main():
    parser = argparse.ArgumentParser(prog="graphics-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "verify", "smoke", "serve", "probe", "generate", "evaluate", "calibrate", "compare", "train"):
        p = sub.add_parser(name)
        p.add_argument("--config", type=Path, default=ROOT / "configs/grpo.yaml" if name == "train" else MODEL_CONFIG)
        if name == "serve":
            p.add_argument("--adapter", type=Path)
        if name == "generate":
            p.add_argument("--prompt", required=True)
        if name in ("evaluate", "calibrate", "compare"):
            p.add_argument("--dataset", type=Path, default=ROOT / "tasks/eval.jsonl")
            p.add_argument("--limit", type=int)
            p.add_argument("--workers", type=int, default=4)
        if name == "train":
            p.add_argument("--resume", metavar="CHECKPOINT")
    a = parser.parse_args()
    cfg = config(a.config)
    cmd = a.command
    ok = True
    if cmd in ("doctor", "verify"):
        result = doctor(cfg)
        if cmd == "verify":
            result["smoke"] = rollout(Task("verify", "Animate a square"), cfg, scripted=True)
            ok = result["smoke"]["valid"]
    elif cmd in ("generate", "smoke"):
        scripted = cmd == "smoke"
        result = rollout(Task(cmd, "Move a red square left to right" if scripted else a.prompt), cfg, scripted=scripted)
        ok = result["valid"]
    elif cmd == "probe":
        result = probe(cfg["server"])
    elif cmd == "serve":
        return serve(cfg, a.adapter)
    elif cmd == "train":
        from graphics_agent.training.grpo import train
        train(a.config, a.resume)
        return 0
    else:
        if a.limit is not None and a.limit <= 0:
            parser.error("--limit must be positive")
        tasks = dataset(a.dataset, "eval" if cmd == "compare" else None)[:a.limit]
        if cmd == "calibrate":
            result = calibrate(tasks)
            ok = all("error" not in r for r in result["cases"])
        elif cmd == "evaluate":
            result = evaluate(tasks, cfg, a.workers)
            ok = result["valid"] == result["cases"]
        else:
            folder = run_root() / uuid.uuid4().hex
            result = {}
            for label in ("base", "trained"):
                version = copy.deepcopy(cfg)
                version["server"]["policy"] = cfg["server"]["model"] if label == "base" else "trained"
                result[label] = evaluate(tasks, version, a.workers, folder / label)
            dump(folder / "comparison.json", result)
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        sys.exit(str(exc))
