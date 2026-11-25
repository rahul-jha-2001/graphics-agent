import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graphics_agent.common.io import ROOT, binary, config
from graphics_agent.environments.effectcraft.rollout import Rollout
from graphics_agent.operations import calibrate, evaluate, rollout
from graphics_agent.tasks.lottie.data import Task, dataset
from graphics_agent.tasks.lottie.grade import grade, similarity


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / "tasks/references/square.json").read_text())

    def test_known_motion(self):
        self.assertEqual(grade(self.doc, {}, self.doc)["reward"], 1)

    def test_invalid_artifacts(self):
        for change in ("static", "hidden", "empty", "null", "off_time"):
            with self.subTest(change=change):
                data = copy.deepcopy(self.doc)
                layer = data["layers"][0]
                if change == "static":
                    layer["ks"]["p"] = {"a": 0, "k": [80, 256, 0]}
                elif change == "hidden":
                    layer["hd"] = True
                elif change == "empty":
                    layer["shapes"] = []
                elif change == "null":
                    layer["ty"] = 3
                else:
                    layer["ip"] = data["op"]
                self.assertEqual(grade(data, {}, self.doc)["reward"], 0)

    def test_similarity_and_constraints(self):
        self.assertLess(similarity(self.doc, {}), 1)
        self.assertFalse(grade(self.doc, {"width": 800})["valid"])

    def test_reference_is_private(self):
        task = dataset(ROOT / "tasks/eval.jsonl")[0]
        self.assertNotIn(str(task.reference), json.dumps(task.public()))
        self.assertNotIn("references", task.instruction())


def engine_available():
    try:
        binary()
        return True
    except FileNotFoundError:
        return False


@unittest.skipUnless(engine_available(), "Set EFFECTCRAFT_CLI for live engine integration")
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"GRAPHICS_RUNS": self.tmp.name, "MSWEA_SILENT_STARTUP": "1",
                                           "MSWEA_GLOBAL_CONFIG_DIR": self.tmp.name + "/mini"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cfg = config()

    def test_smoke(self):
        result = rollout(dataset(ROOT / "tasks/train.jsonl")[0], self.cfg, scripted=True)
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["similarity"], 1)
        self.assertTrue(result["changing_pixels"])

    def test_empty_and_command_restrictions(self):
        r = Rollout(Task("t", "square"), self.cfg)
        self.addCleanup(r.engine.close)
        r.start()
        for name, args in [("run_script", {}), ("reference", {"name": "../target"}),
                           ("execute_command", {"command": "file.open", "params": {"path": "/tmp/file"}})]:
            for i in range(3):
                result = json.loads(r.action(name, json.dumps(args)))
                self.assertFalse(result["ok"])
            self.assertIn("Repeated", result["error"])
        self.assertFalse(r.finish("model_finished")["valid"])

    def test_flat_layer_and_skill_references(self):
        r = Rollout(Task("t", "square"), self.cfg)
        self.addCleanup(r.engine.close)
        r.start()
        act = lambda name, args: json.loads(r.action(name, json.dumps(args)))
        act("execute_command", {"command": "comp.new", "params": {"width": 64, "height": 64}})
        act("execute_command", {"command": "layer.newShape", "params": {"name": "Dot", "kind": "ellipse"}})
        for flat in (True, False):
            result = act("get_layer", {"layer": "Dot", "flat": flat})
            self.assertTrue(result["ok"], result)
            self.assertIn("transform/position", json.dumps(result["result"]))
        names = act("catalog", {})["result"]["references"]
        self.assertIn("workflow", names)
        for name in names:
            self.assertTrue(act("reference", {"name": name})["ok"], name)

    def test_calibration(self):
        result = calibrate(dataset(ROOT / "tasks/eval.jsonl"))
        self.assertEqual(result["cases"][0]["similarity"], 1)

    def test_four_worker_native_tool_loop(self):
        try:
            import minisweagent
        except ImportError:
            self.skipTest("Install inference dependencies for the mini-SWE integration")
        def scripted_api(server, endpoint, payload, **kwargs):
            messages = payload["messages"]
            outputs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            if not outputs:
                action = ("execute_command", {"command": "comp.new", "params": {"width": 512, "height": 512}})
            elif len(outputs) == 1:
                action = ("execute_command", {"command": "layer.newShape", "params": {"kind": "ellipse", "fill": "#0000ff"}})
            elif len(outputs) == 2:
                layer = outputs[-1]["result"]["layer"]
                action = ("add_keyframe", {"layer": layer, "path": "transform/position",
                          "keys": [{"time": 0, "value": [100, 100]}, {"time": 2, "value": [400, 400]}]})
            else:
                action = None
            msg = {"role": "assistant", "content": "Finished"}
            if action:
                msg.update(content="", tool_calls=[{"id": f"call_{len(outputs)}", "type": "function",
                    "function": {"name": "effectcraft", "arguments": json.dumps({
                        "name": action[0], "arguments": json.dumps(action[1])})}}])
            return {"model": server["model"], "choices": [{"message": msg, "finish_reason": "tool_calls" if action else "stop"}],
                    "usage": {"completion_tokens": 20}}
        with patch("graphics_agent.harness.agent.request", side_effect=scripted_api):
            result = evaluate([Task(str(i), "Animate a blue circle") for i in range(4)], self.cfg, 4)
        self.assertEqual(result["valid"], 4)
        manifests = list(Path(result["path"]).glob("*/manifest.json"))
        self.assertEqual(len({json.loads(f.read_text())["engine_pid"] for f in manifests}), 4)
