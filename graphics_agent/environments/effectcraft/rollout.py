import hashlib
import json
import resource
import shutil
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path

from PIL import Image
import jsonschema

from graphics_agent.common.io import MAX_BYTES, ROOT, binary, digest, dump, read_json, run_root
from graphics_agent.environments.effectcraft.session import Engine
from graphics_agent.tasks.lottie.grade import grade

COMMANDS = {"comp.new", "layer.newText", "layer.newShape", "layer.newSolid", "shape.newPath",
            "shape.newShape", "shape.fillStroke", "layer.select", "time.set"}
TOOLS = {"describe_command", "execute_command", "get_comp", "get_layer", "get_property",
         "set_property", "add_keyframe", "undo", "redo"}


def cli(*args, cwd):
    result = subprocess.run([binary(), *args, "--json"], cwd=cwd, capture_output=True,
                            timeout=45, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).decode(errors="replace")[:1200])
    return json.loads(result.stdout)


class Rollout:
    def __init__(self, task, cfg, parent=None):
        self.task, self.cfg = task, cfg
        self.path = (parent or run_root()) / uuid.uuid4().hex
        self.path.mkdir(parents=True, mode=0o700)
        self.work = self.path / "workspace"
        self.work.mkdir()
        self.engine = Engine(self.work)
        self.started = time.monotonic()
        self.failures = Counter()
        self.asset_paths = {}
        self.steps = 0
        self.errors = 0
        self.outcome = None
        self.manifest = {"skill_sha256": digest(ROOT / cfg["harness"]["skill"]),
                         "template_sha256": digest(ROOT / "configs/tools.jinja"), "config": cfg, "split": task.split, "metadata": task.metadata}
        dump(self.path / "task.json", task.public())
        dump(self.path / "manifest.json", self.manifest)
        (self.path / "trajectory.jsonl").touch()

    def record(self, kind, **payload):
        with (self.path / "trajectory.jsonl").open("a") as f:
            f.write(json.dumps({"time": time.time(), "kind": kind, **payload}) + "\n")

    def start(self):
        for asset in self.task.assets:
            asset_dir = self.work / "assets"
            asset_dir.mkdir(exist_ok=True)
            dst = asset_dir / asset.name
            if dst.exists() or asset.stat().st_size > MAX_BYTES:
                raise ValueError(f"Invalid asset {asset.name}")
            shutil.copyfile(asset, dst)
            self.asset_paths[asset.name] = dst
        self.engine.start()
        self.manifest["mcp_startup_seconds"] = time.monotonic() - self.started
        info = cli("info", "--empty", cwd=self.work)
        self.manifest.update(engine_version=info["version"], command_count=info["commands"],
                             engine_pid=self.engine.proc.pid, schema_sha256=hashlib.sha256(
                                 json.dumps(self.engine.tools, sort_keys=True).encode()).hexdigest())
        dump(self.path / "manifest.json", self.manifest)

    def perform(self, name, args):
        available = TOOLS & self.engine.tools.keys()
        if name == "catalog":
            return {"tools": sorted(available), "commands": sorted(COMMANDS),
                    "helpers": ["schema", "reference"] + (["import_asset"] if self.asset_paths else [])}
        if name == "import_asset":
            asset = self.asset_paths.get(args.get("name"))
            if asset is None or asset.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                raise ValueError("Select a supplied PNG or JPEG asset by name")
            return self.engine.call("execute_command", {"command": "file.import",
                "params": {"paths": [str(asset)], "addToComp": True}})
        if name == "schema":
            if args.get("name") not in available:
                raise ValueError("Unknown policy tool")
            return self.engine.tools[args["name"]]["inputSchema"]
        if name == "reference":
            if args != {"name": "workflow"}:
                raise ValueError("Available reference: workflow")
            return (ROOT / self.cfg["harness"]["skill"]).read_text()[:4000]
        if name not in available:
            raise ValueError("Unknown policy tool. Use catalog.")
        if name in ("execute_command", "describe_command"):
            if "id" in args or args.get("command") not in COMMANDS:
                raise ValueError("Use an allowed command ID in command")
            if name == "execute_command":
                meta = self.engine.call("describe_command", {"command": args["command"]})
                jsonschema.validate(args.get("params", {}), meta["schema"])
                params = args.get("params", {})
                if args["command"] == "comp.new":
                    for key, limit in (("width", 4096), ("height", 4096), ("duration", 120), ("frameRate", 120)):
                        if key in params and not 0 < float(params[key]) <= limit:
                            raise ValueError(f"{key} outside rollout bounds")
        if "expression" in args:
            raise ValueError("Use explicit property values or keyframes")
        result = self.engine.call(name, args)
        if name == "get_layer" and isinstance(result, dict) and "properties" in result:
            paths = []
            pending = [result.pop("properties")]
            while pending:
                node = pending.pop()
                pending.extend(node.get("children", []))
                if "path" in node and "value" in node:
                    paths.append(node["path"])
            result["property_paths"] = sorted(paths, key=lambda p: (not p.startswith("transform/"), p))
        return result

    def action(self, name, arguments):
        self.steps += 1
        start = time.monotonic()
        if self.steps > self.cfg["harness"]["max_steps"] or start-self.started > self.cfg["harness"]["timeout"]:
            raise RuntimeError("Rollout budget exhausted")
        try:
            if len(arguments) > 16000:
                raise ValueError("Oversized arguments")
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("Expected a JSON object")
            key = (name, json.dumps(args, sort_keys=True))
            if self.failures[key] >= 2:
                raise ValueError("Repeated failing action blocked")
            try:
                result = self.perform(name, args)
            except (ValueError, KeyError, TypeError, jsonschema.ValidationError):
                self.failures[key] += 1
                raise
            text = json.dumps(result)
            observation = {"ok": True, "result": result if len(text) <= 5000 else text[:5000] + "…"}
        except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
            self.errors += 1
            observation = {"ok": False, "error": str(exc)[:1200]}
        state = self.engine.call("get_comp", {}) if observation["ok"] and name in {"execute_command", "add_keyframe", "set_property"} else None
        self.record("tool", state_sha256=hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest() if state else None, name=name, arguments=arguments, observation=observation,
                    elapsed=time.monotonic()-start)
        return json.dumps(observation)

    def finish(self, reason, error=None):
        if self.outcome is not None:
            return self.outcome
        result = {"valid": False, "reward": 0.0, "errors": [], "similarity": None}
        try:
            if self.engine.proc and self.engine.tools:
                comp = self.engine.call("get_comp", {})
                snapshot = self.work / "snapshot.ecproj"
                self.engine.call("save_project", {"path": str(snapshot)})
                project = self.path / "generated.ecproj"
                if snapshot.stat().st_size > MAX_BYTES:
                    raise ValueError("Oversized project snapshot")
                snapshot.replace(project)
                project.chmod(0o444)
                self.engine.close()
                if not comp.get("id") or not comp.get("layers"):
                    raise ValueError("Empty composition")
                tmp = self.work / "export.json"
                cli("--project", str(project), "exec", "file.exportLottie", "--params",
                    json.dumps({"comp": comp["id"], "path": str(tmp)}), cwd=self.work)
                doc = read_json(tmp)
                output = self.path / "generated.json"
                tmp.replace(output)
                ref = self.task.reference
                result = grade(doc, self.task.constraints, read_json(ref) if ref else None)
                if result["valid"]:
                    evidence = render_samples(output, self.work, doc)
                    result.update(evidence)
                    if not evidence["changing_pixels"]:
                        result.update(valid=False, reward=0.0)
                        result["errors"].append("Rendered samples contain no visible change")
                result.update(project_sha256=digest(project), export_sha256=digest(output),
                              reference_sha256=digest(ref) if ref else None)
            else:
                raise RuntimeError("Engine did not initialize")
        except Exception as exc:
            result.update(valid=False, reward=0.0)
            result["errors"].append(str(exc)[:1200])
        finally:
            self.engine.close()
        if error:
            result.update(valid=False, reward=0.0)
            result["errors"].append(str(error)[:1200])
        self.outcome = result
        dump(self.path / "grade.json", result)
        dump(self.path / "diagnostics.json", {"finish_reason": reason, "steps": self.steps,
             "tool_errors": self.errors, "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "elapsed": time.monotonic()-self.started, "errors": result["errors"]})
        return result

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {"rollout": str(self.path)}

    def effectcraft(self, name: str, arguments: str) -> str:
        """Inspect or edit the live EffectCraft project.

        Args:
            name: Action from catalog, or catalog, schema, or reference.
            arguments: JSON-encoded object matching the action schema.

        Returns:
            A compact JSON observation.
        """
        return self.action(name, arguments)


def render_samples(output, work, doc):
    """Verify exported artwork at bounded resolution after a fresh Lottie import."""
    project = work / "render.ecproj"
    cli("--empty", "exec", "file.importLottie", "--params", json.dumps({"path": str(output)}),
        "--save-as", str(project), cwd=work)
    frames = {0, max(0, int(doc["op"]-doc["ip"])-1)}
    last = max(frames)
    frames.update(round(last*i/8) for i in range(1, 8))
    first = None
    visible = False
    sampled = []
    for frame in sorted(frames):
        path = work / "sample.png"
        cli("--project", str(project), "render-frame", "--frame", str(frame), "--transparent",
            "--max-side", "192", "--out", str(path), cwd=work)
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            visible |= rgba.getchannel("A").getbbox() is not None
            pixels = rgba.convert("RGBa").tobytes()
        sampled.append(frame)
        if first is None:
            first = pixels
        elif visible and pixels != first:
            return {"changing_pixels": True, "sample_frames": sampled}
    return {"changing_pixels": False, "sample_frames": sampled}
