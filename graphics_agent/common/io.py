import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODEL_CONFIG = Path(os.getenv("GRAPHICS_MODEL_CONFIG", ROOT / "configs/model.yaml"))
MAX_BYTES = 16 * 1024 * 1024


def config(path=MODEL_CONFIG):
    return yaml.safe_load(Path(path).read_text())


def binary():
    default = Path(os.getenv("GRAPHICS_HOME", "/workspace/graphics-agent")) / "bin/effectcraft-cli"
    value = os.getenv("EFFECTCRAFT_CLI") or (str(default) if default.exists() else "effectcraft-cli")
    resolved = shutil.which(value)
    if not resolved:
        raise FileNotFoundError(f"EffectCraft CLI unavailable: {value}. Run bash install.sh.")
    return str(Path(resolved).resolve())


def run_root():
    return Path(os.getenv("GRAPHICS_RUNS", "runs")).resolve()


def dump(path, value):
    path = Path(path)
    tmp = path.with_name("." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def read_json(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"File exceeds {MAX_BYTES} bytes")
    return json.loads(path.read_bytes())


def digest(path):
    with open(path, "rb") as stream:
        h = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
        return h.hexdigest()
