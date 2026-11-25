import json
import time

from graphics_agent.common.io import ROOT
from graphics_agent.inference.client import TOOL, request


def system_prompt(cfg):
    return (ROOT / cfg["harness"]["skill"]).read_text()


def bounded_history(messages, byte_budget):
    """Drop complete old exchanges together so tool responses keep their call."""
    def size(items):
        return len(json.dumps(items, ensure_ascii=False).encode())

    fixed = messages[:2]
    if size(fixed) > byte_budget:
        raise ValueError("Task and skill exceed the model context budget")
    turns = []
    for message in messages[2:]:
        if message["role"] == "assistant":
            turns.append([])
        turns[-1].append(message)
    while turns and size(fixed + [m for turn in turns for m in turn]) > byte_budget:
        turns.pop(0)
    return fixed + [m for turn in turns for m in turn]


class Policy:
    def __init__(self, run):
        self.run = run
        self.tokens = 0

    def query(self, messages):
        options = self.run.cfg["harness"]
        left = options["max_tokens"] - self.tokens
        if left <= 0:
            raise RuntimeError("Generation token budget exhausted")
        server = self.run.cfg["server"]
        context = server["max_model_len"] - min(left, options["max_completion_tokens"]) - 512
        history = bounded_history(messages, context)
        payload = {"model": server.get("policy", server["model"]), "tools": [TOOL],
                   "temperature": options["temperature"], "seed": options["seed"],
                   "max_tokens": min(left, options["max_completion_tokens"]),
                   "messages": history}
        start = time.monotonic()
        response = request(server, "chat/completions", payload,
                           timeout=max(1, min(90, options["timeout"] - (start-self.run.started))))
        msg = response["choices"][0]["message"]
        usage = response.get("usage", {})
        self.tokens += usage.get("completion_tokens", payload["max_tokens"])
        self.run.record("model", message=msg, usage=usage, finish_reason=response["choices"][0]["finish_reason"],
                        model=response.get("model"), retained_messages=len(history), elapsed=time.monotonic()-start)
        return {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls") and v is not None}

    @staticmethod
    def format_message(**kwargs):
        return kwargs

    @staticmethod
    def get_template_vars():
        return {}

    @staticmethod
    def serialize():
        return {}


def generate(run):
    from minisweagent.agents.default import DefaultAgent

    class GraphicsAgent(DefaultAgent):
        def execute_actions(self, message):
            calls = message.get("tool_calls") or []
            if not calls:
                return self.add_messages({"role": "exit", "content": "", "extra": {"exit_status": "model_finished"}})
            for call in calls:
                try:
                    fn = call["function"]
                    fields = json.loads(fn["arguments"])
                    if fn["name"] != "effectcraft":
                        raise ValueError("Use effectcraft")
                    result = run.effectcraft(**fields)
                except (KeyError, ValueError, TypeError) as exc:
                    result = json.dumps({"ok": False, "error": str(exc)[:500]})
                self.add_messages({"role": "tool", "tool_call_id": call["id"], "content": result})
            return self.messages[-len(calls):]

    policy = Policy(run)
    agent = GraphicsAgent(policy, run, system_template=system_prompt(run.cfg), instance_template="{{ task }}",
                          step_limit=run.cfg["harness"]["max_steps"], cost_limit=0,
                          wall_time_limit_seconds=run.cfg["harness"]["timeout"])
    run.record("prompt", system=system_prompt(run.cfg), task=run.task.instruction(), tools=[TOOL])
    return agent.run(run.task.instruction())["exit_status"]
