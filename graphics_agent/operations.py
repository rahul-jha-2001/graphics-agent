import csv
import errno
import json
import os
import platform
import socket
import sysconfig
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from graphics_agent.common.io import binary, digest, dump, read_json, run_root
from graphics_agent.environments.effectcraft.rollout import Rollout, cli
from graphics_agent.harness.agent import generate
from graphics_agent.harness.scripted import create_square
from graphics_agent.tasks.lottie.grade import similarity


def doctor(cfg=None):
    path = Path(binary())
    info = cli("info", "--empty", cwd=path.parent)
    catalog = cli("commands", "--empty", cwd=path.parent)
    gpu = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=15) if shutil.which("nvidia-smi") else None
    gpu_metrics = None
    if gpu and not gpu.returncode:
        query = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free",
                                "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        gpu_metrics = query.stdout.strip()
    port_available = None
    if cfg:
        try:
            with socket.socket() as port:
                port.bind((cfg["server"]["host"], cfg["server"]["port"]))
                port_available = True
        except OSError as exc:
            port_available = False if exc.errno == errno.EADDRINUSE else None
    return {"python": sys.version, "abi": sysconfig.get_config_var("SOABI"), "cc": shutil.which("cc"),
            "gpu_driver_and_memory": gpu_metrics, "port_available": port_available,
            "model_cache": os.getenv("HF_HOME"), "platform": platform.platform(), "engine": str(path), "sha256": digest(path),
            "version": info["version"], "commands": len(catalog), "gpu": gpu.stdout.strip() if gpu and not gpu.returncode else None,
            "free_bytes": shutil.disk_usage(Path.cwd()).free}


def rollout(task, cfg, parent=None, scripted=False):
    run = Rollout(task, cfg, parent)
    try:
        run.start()
        if scripted:
            create_square(run)
            reason = "scripted"
        else:
            reason = generate(run)
        outcome = run.finish(reason)
    except Exception as exc:
        outcome = run.finish("failed", exc)
    return {"task_id": task.task_id, "path": str(run.path), **outcome}


def evaluate(tasks, cfg, workers, parent=None):
    if workers < 1:
        raise ValueError("workers must be positive")
    parent = parent or run_root() / uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda t: rollout(t, cfg, parent), tasks))
    summary = {"cases": len(rows), "valid": sum(r["valid"] for r in rows),
               "mean_reward": sum(r["reward"] for r in rows)/len(rows)}
    dump(parent / "summary.json", summary)
    with (parent / "cases.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    with (parent / "cases.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, ["task_id", "valid", "reward", "similarity", "path"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return {"path": str(parent), **summary}


def calibrate(tasks):
    parent = run_root() / uuid.uuid4().hex
    rows = []
    for task in tasks:
        if not task.reference:
            continue
        work = parent / uuid.uuid4().hex
        work.mkdir(parents=True)
        try:
            imported = cli("--empty", "exec", "file.importLottie", "--params",
                           json.dumps({"path": str(task.reference)}), "--save-as", str(work / "roundtrip.ecproj"), cwd=work)
            cli("--project", str(work / "roundtrip.ecproj"), "exec", "file.exportLottie",
                "--params", json.dumps({"path": str(work / "roundtrip.json")}), cwd=work)
            score = similarity(read_json(task.reference), read_json(work / "roundtrip.json"))
            rows.append({"task_id": task.task_id, "similarity": score, "import": imported})
        except Exception as exc:
            rows.append({"task_id": task.task_id, "error": str(exc)})
    if not rows:
        raise ValueError("Calibration requires reference animations")
    dump(parent / "calibration.json", rows)
    return {"path": str(parent), "cases": rows}
