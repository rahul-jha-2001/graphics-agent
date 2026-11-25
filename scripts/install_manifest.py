"""Called by install.sh with the selected profile interpreter."""
import importlib.metadata
import os
import sys
from pathlib import Path

import yaml

from graphics_agent.common.io import MODEL_CONFIG, ROOT, config, digest, dump
from graphics_agent.operations import doctor

profile, override = sys.argv[1:]
home = Path(os.environ["GRAPHICS_HOME"])
settings = config(MODEL_CONFIG)
server = settings["server"]
settings_path = home / f"model-{profile}.yaml"
if profile != "engine":
    from huggingface_hub import model_info, snapshot_download
    if override and override != server["model"]:
        existing = config(settings_path)["server"] if settings_path.exists() else {}
        server["revision"] = existing["revision"] if existing.get("model") == override else model_info(override).sha
        server["model"] = override
    snapshot_download(server["model"], revision=server["revision"],
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "*.tiktoken"])
settings_path.write_text(yaml.safe_dump(settings))
status = doctor(settings)
if status["version"] != "0.21.1" or status["commands"] != 744:
    raise RuntimeError("Unexpected EffectCraft build or command registry")
manifest = {**status, "profile": profile, "model": server,
            "lock_sha256": digest(ROOT / "constraints/linux-x86_64-py311.txt"),
            "template_sha256": digest(ROOT / "configs/tools.jinja"),
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()}}
dump(home / f"manifest-{profile}.json", manifest)
