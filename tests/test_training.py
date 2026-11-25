"""Opt-in CPU integration test for real TRL tool turns, LoRA updates and resume."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


@unittest.skipUnless(os.getenv("GRAPHICS_TRAINING_TEST") == "1", "Set GRAPHICS_TRAINING_TEST=1 with training deps")
class TrainingIntegration(unittest.TestCase):
    def test_multiturn_checkpoint(self):
        import torch
        from transformers import AutoConfig, AutoProcessor, Qwen2_5_VLForConditionalGeneration
        from graphics_agent.common.io import ROOT, config
        from graphics_agent.training.grpo import train

        cfg = config()
        server = cfg["server"]
        source = os.getenv("GRAPHICS_TEST_TOKENIZER", server["model"])
        processor = AutoProcessor.from_pretrained(source, revision=server["revision"], local_files_only=True)
        architecture = AutoConfig.from_pretrained(source, revision=server["revision"], local_files_only=True)
        text = architecture.text_config
        text.num_hidden_layers, text.layer_types = 1, ["full_attention"]
        text.num_attention_heads, text.num_key_value_heads = 2, 1
        text.hidden_size, text.head_dim, text.intermediate_size = 64, 32, 96
        text.rope_parameters["mrope_section"] = [4, 6, 6]
        vision = architecture.vision_config
        vision.depth, vision.hidden_size, vision.num_heads = 1, 64, 2
        vision.intermediate_size, vision.out_hidden_size = 96, 64
        vision.fullatt_block_indexes = [0]

        tokenizer = processor.tokenizer
        edits = [
            ("execute_command", {"command": "comp.new", "params": {"width": 512, "height": 512, "duration": 2, "frameRate": 30}}),
            ("execute_command", {"command": "layer.newShape", "params": {"kind": "rect", "size": [80, 80], "fill": "#ff0000"}}),
            ("add_keyframe", {"layer": "#1", "path": "transform/position", "keys": [
                {"time": 0, "value": [80, 256]}, {"time": 1.8, "value": [432, 256]}]}),
        ]
        generate = Qwen2_5_VLForConditionalGeneration.generate

        def constrained(model, input_ids, **kwargs):
            # One group member edits and one stops early, exercising nonzero advantages.
            length = input_ids.shape[1]
            sequences = []
            for i, ids in enumerate(input_ids):
                turn = tokenizer.decode(ids).count("<tool_response>")
                response = "Done."
                if turn < len(edits) and (turn > 0 or i == 0):
                    name, args = edits[turn]
                    call = {"name": "effectcraft", "arguments": {"name": name, "arguments": json.dumps(args)}}
                    response = "<tool_call>\n" + json.dumps(call) + "\n</tool_call>"
                sequences.append(tokenizer.encode(response, add_special_tokens=False) + [tokenizer.eos_token_id])

            def allowed(i, ids):
                seq = sequences[i]
                return [seq[min(len(ids) - length, len(seq) - 1)]]

            return generate(model, input_ids=input_ids, prefix_allowed_tokens_fn=allowed, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / "tiny-qwen"
            torch.manual_seed(42)
            Qwen2_5_VLForConditionalGeneration(architecture).save_pretrained(base)
            processor.save_pretrained(base)
            server.update(model=str(base), dtype="float32")
            model_yaml = root / "model.yaml"
            model_yaml.write_text(yaml.safe_dump(cfg))
            settings = config(ROOT / "configs/grpo.yaml")
            output = root / "training"
            settings["output_dir"] = str(output)
            settings["trainer"].update(use_vllm=False, use_cpu=True, bf16=False, max_completion_length=640)
            train_yaml = root / "grpo.yaml"
            train_yaml.write_text(yaml.safe_dump(settings))
            runs = root / "runs"
            with patch.dict(os.environ, GRAPHICS_MODEL_CONFIG=str(model_yaml), GRAPHICS_RUNS=str(runs)), \
                 patch.object(Qwen2_5_VLForConditionalGeneration, "generate", constrained):
                train(train_yaml)
                first = output / "checkpoint-1"
                self.assertTrue((first / "optimizer.pt").is_file())
                grades = [json.loads(p.read_text()) for p in runs.glob("*/grade.json")]
                self.assertEqual(sorted(g["valid"] for g in grades), [False, True])
                state = json.loads((first / "trainer_state.json").read_text())
                self.assertTrue(any(float(r.get("grad_norm", 0)) > 0 for r in state["log_history"]))
                settings["trainer"]["max_steps"] = 2
                train_yaml.write_text(yaml.safe_dump(settings))
                train(train_yaml, str(first))
                state = json.loads((output / "checkpoint-2/trainer_state.json").read_text())
                self.assertEqual(state["global_step"], 2)
