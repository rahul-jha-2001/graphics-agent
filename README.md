# Graphics Agent

Generate an editable EffectCraft project from a prompt, export Lottie through the engine, and grade the exported animation. Inference and GRPO training use the same per-rollout editing environment. The repository includes a moving-square training example and a separate moving-circle evaluation example.

## Run the full pipeline on RunPod

For your first AI-generated animation, complete steps 1–3. Steps 4–6 evaluate the base model, train a LoRA adapter, and compare the two policies.

### 1. Install on the GPU host

Copy this project to the pod and open **Terminal 1** in the directory containing `install.sh`. Use Linux x86_64 with CPython 3.11, venv or uv, curl, GNU tar, flock, and writable persistent storage at `/workspace`. The first install needs access to GitHub, a Python package index, and Hugging Face. The GPU profiles require a working NVIDIA driver compatible with the pinned PyTorch 2.14.0 and CUDA 13 stack. GPU memory requirements depend on context length and concurrency.

```bash
python3.11 --version
nvidia-smi -L
bash install.sh --profile all
source /workspace/graphics-agent/activate-all
graphics-agent doctor
```

Running `bash install.sh` without arguments also selects `all`.

| Profile | What you can run |
|---|---|
| `engine` | EffectCraft CLI 0.21.1, scripted generation, and export validation on CPU |
| `inference` | Engine tools plus prompt-driven generation and evaluation with mini-SWE-agent, vLLM, PyTorch, Transformers, and the cached Qwen2.5-VL-3B-Instruct weights |
| `training` | Engine tools plus GRPO training with Qwen, PyTorch, Transformers, vLLM, TRL, and PEFT |
| `all` | Inference and training in one virtual environment |

Every profile finishes by running a **scripted moving-square test**. A passing test confirms that EffectCraft can create, save, export, and render an animation. Prompt-driven generation starts in the next two steps.

### 2. Start the model server in Terminal 1

```bash
graphics-agent serve
```

Leave this command running while generating or evaluating. It loads Qwen and serves it at `127.0.0.1:8000` on the pod. Both the server and client read the installed model YAML named by `$GRAPHICS_MODEL_CONFIG`.

### 3. Generate an animation in Terminal 2

Open a **second terminal on the same pod** in the project directory. Activate the environment in this shell too. Once the server has loaded the model, run:

```bash
source /workspace/graphics-agent/activate-all
graphics-agent probe
graphics-agent generate --prompt "A red ball bounces twice and settles"
```

A successful `probe` prints the model ID after checking a native tool call. During `generate`, Qwen chooses the EffectCraft edits from your prompt. Look for `"valid": true` in the result and open the directory printed in `path`. It contains **`generated.json`** for a Lottie player and **`generated.ecproj`** for editing in EffectCraft. If validation fails, read `grade.json` for the failed checks and `trajectory.jsonl` for the model and tool calls. If the probe reports a refused connection, check the server output in Terminal 1.

### 4. Evaluate the base model in Terminal 2

```bash
graphics-agent evaluate --dataset tasks/eval.jsonl --workers 4
graphics-agent calibrate --dataset tasks/eval.jsonl
```

Evaluation runs the prompts and writes `summary.json`, `cases.csv`, and `cases.jsonl` in the printed evaluation directory. Calibration imports and re-exports each reference through EffectCraft and records how closely it survives the round trip in `calibration.json`.

### 5. Train on the same GPU

In **Terminal 1**, press **Ctrl+C** and wait for the server to exit. In **Terminal 2**, run:

```bash
graphics-agent train --config configs/grpo.yaml
```

The default config uses `tasks/train.jsonl`, samples two rollouts per prompt, and runs one optimizer step. TRL starts its own colocated vLLM worker. After training, `runs/checkpoints/checkpoint-1/` contains the resumable training state and `runs/checkpoints/adapter/` contains the LoRA adapter for serving. These paths are relative to the project directory. Generation and evaluation outputs use `$GRAPHICS_RUNS`, which defaults to `/workspace/graphics-agent-runs`.

To continue training, first increase `trainer.max_steps` in `configs/grpo.yaml` to the **total** number of steps you want, then run:

```bash
graphics-agent train --config configs/grpo.yaml --resume runs/checkpoints/checkpoint-1
```

### 6. Compare the base model and trained adapter

After training finishes, start the server again in **Terminal 1**, this time loading the adapter:

```bash
graphics-agent serve --adapter runs/checkpoints/adapter
```

In **Terminal 2**, run:

```bash
graphics-agent probe
graphics-agent compare --dataset tasks/eval.jsonl
```

The comparison evaluates both policies on the same held-out prompts with the same tools and budgets. The printed `base.path` and `trained.path` point to the individual reports. Their parent directory contains `comparison.json`. Press **Ctrl+C** in Terminal 1 when finished.

## Run the scripted engine check on CPU

```bash
export GRAPHICS_HOME="$PWD/.runtime"
export HF_HOME="$GRAPHICS_HOME/huggingface"
export GRAPHICS_RUNS="$PWD/runs"
bash install.sh --profile engine
source "$GRAPHICS_HOME/activate-engine"
python -m graphics_agent verify
python -m unittest discover -s tests -v
```

The engine profile installs the official checksum-verified CLI and small validation dependencies. The inference and training profiles also cache model weights. The all profile installs both sets of GPU dependencies in one virtual environment. An existing release archive can be supplied via EFFECTCRAFT_ARCHIVE. The bootstrap checks the binary checksum on rerun. A failed integrity check stops installation.

## Data and artifacts

Each JSONL record requires task_id and prompt. Optional fields include constraints (width, height, fps, duration in seconds), split, input_assets, reference_lottie, and metadata. Paths resolve relative to the dataset. Supplied PNG and JPEG assets can be imported by name. Record licensing and permitted use in metadata. The bundled synthetic examples are dedicated to the public domain under CC0-1.0.

A rollout writes task.json, manifest.json, trajectory.jsonl, grade.json, diagnostics.json, and engine.stderr. Successful exports also contain generated.ecproj and generated.json. Keep the rollout directory together when using external image assets. Batch evaluation writes per-case CSV and JSONL plus summary.json. Calibration uses a separate workspace to import and re-export the evaluator's reference and measure structural loss.

The policy discovers a small editing catalog and retrieves live schemas on demand. The harness owns saving and export. Validation checks the canvas, timing, graphic layer content, keyframes, and pixel changes in a bounded sample of frames rendered from a fresh import of the export. A failed gate scores zero. A valid animation receives 1 for a task without a reference, or 0.25 + 0.75 × normalized JSON-tree similarity when a reference exists. Structural similarity is sensitive to equivalent artwork encoded in different ways. Frame sampling can miss brief changes.

## Verification

Run the test suite with EFFECTCRAFT_CLI pointing to the pinned binary. The four-worker integration test additionally uses mini-SWE-agent and a deterministic substitute for the model endpoint. The live model acceptance path is serve → probe → generate. The GPU training acceptance path is train → resume. Local verification covered real EffectCraft exports, four isolated workers, and the CPU training test below. Full Qwen 3B generation and colocated GPU training on RunPod remain unverified.

An opt-in CPU training integration test uses the cached Qwen processor, a randomly initialized one-layer policy, and constrained decoding. One group member makes valid edits and the other ends early. The test checks graded rewards, a nonzero gradient, optimizer state, and continuation from checkpoint 1 to checkpoint 2.

```bash
GRAPHICS_TRAINING_TEST=1 OMP_NUM_THREADS=2 python -m unittest discover -s tests -p test_training.py -v
```

The graphics interface follows the pinned [EffectCraft agent guide](https://github.com/storytold/effectcraft/blob/v0.21.1/docs/agents.md). The training adapter targets the [TRL 1.18.2 GRPO environment API](https://huggingface.co/docs/trl/v1.18.2/grpo_trainer).
