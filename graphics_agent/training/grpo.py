import os
from pathlib import Path

from graphics_agent.common.io import ROOT, config
from graphics_agent.environments.effectcraft.rollout import Rollout
from graphics_agent.harness.agent import system_prompt
from graphics_agent.tasks.lottie.data import dataset


def train(path, resume=None):
    from datasets import Dataset
    from huggingface_hub import snapshot_download
    from peft import LoraConfig
    from transformers import AutoProcessor
    from trl import GRPOConfig, GRPOTrainer
    from trl.chat_template_utils import qwen3_template

    settings = config(path)
    cfg = config(os.getenv("GRAPHICS_MODEL_CONFIG", ROOT / settings["model_config"]))
    server = cfg["server"]
    model_path = server["model"]
    if not Path(model_path).is_dir():
        # TRL's colocated worker reads model.name_or_path without forwarding revision.
        model_path = snapshot_download(model_path, revision=server["revision"], local_files_only=True)
    tasks = {t.task_id: t for t in dataset(ROOT / settings["dataset"], "train")}
    data = Dataset.from_list([{"task_id": t.task_id, "prompt": [
        {"role": "system", "content": system_prompt(cfg)}, {"role": "user", "content": t.instruction()}]}
        for t in tasks.values()])
    live = set()

    class Environment:
        def reset(self, task_id, **kwargs):
            self._run = Rollout(tasks[task_id], cfg)
            self._run.manifest.update(training=settings["trainer"], lora_rank=settings["lora_rank"])
            live.add(self._run)
            self._run.start()

        def _score(self) -> float:
            score = self._run.finish("training_reward")["reward"]
            live.discard(self._run)
            return score

        def effectcraft(self, name: str, arguments: str) -> str:
            """Inspect or edit the live EffectCraft project.

            Args:
                name: Action from catalog, or catalog, schema, or reference.
                arguments: JSON-encoded object matching the action schema.

            Returns:
                A compact JSON observation.
            """
            return self._run.effectcraft(name, arguments)

    def artifact_reward(prompts, completions, completion_ids, environments, trainer_state, **kwargs):
        rewards = []
        for prompt, turns, tokens, env in zip(prompts, completions, completion_ids, environments, strict=True):
            env._run.record("training_completion", prompt=prompt, messages=turns,
                            completion_token_ids=tokens, policy_step=trainer_state.global_step)
            rewards.append(env._score())
        return rewards

    processor = AutoProcessor.from_pretrained(model_path, padding_side="left")
    processor.chat_template = (ROOT / "configs/tools.jinja").read_text()
    processor.tokenizer.chat_template = processor.chat_template
    processor.tokenizer.response_template = qwen3_template
    args = GRPOConfig(output_dir=settings["output_dir"], model_init_kwargs={
        "revision": server["revision"], "dtype": server["dtype"]}, **settings["trainer"])
    trainer = GRPOTrainer(model=model_path, processing_class=processor, args=args, train_dataset=data,
                          peft_config=LoraConfig(r=settings["lora_rank"], lora_alpha=2*settings["lora_rank"],
                              target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"),
                          reward_funcs=artifact_reward, environment_factory=Environment)
    try:
        trainer.train(resume_from_checkpoint=resume)
        trainer.save_model(str(Path(settings["output_dir"]) / "adapter"))
        trainer.save_state()
    finally:
        for rollout in live:
            rollout.finish("training_interrupted", "Training interrupted")
