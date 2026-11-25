import json
import time

from graphics_agent.common.io import ROOT
from graphics_agent.inference.client import TOOL, request


TRUNCATED = ("Your reply hit the length limit before any tool call. Keep planning short: "
             "call effectcraft now with the next action.")


def skill_text(path):
    """Skill body without YAML frontmatter, which is metadata for skill loaders, not the policy."""
    text = path.read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:].lstrip("\n")
    return text


def system_prompt(cfg):
    return skill_text(ROOT / cfg["harness"]["skill"])


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
        self.last_finish = None

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
                           timeout=max(1, min(server.get("request_timeout", 90), options["timeout"] - (start-self.run.started))))
        msg = response["choices"][0]["message"]
        self.last_finish = response["choices"][0].get("finish_reason")
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
        truncations = 0

        def execute_actions(self, message):
            calls = message.get("tool_calls") or []
            if not calls:
                # A reply cut off by the token limit is not a decision to stop; nudge it once or twice.
                if self.model.last_finish == "length" and self.truncations < 2:
                    self.truncations += 1
                    run.record("nudge", reason="length")
                    return self.add_messages({"role": "user", "content": TRUNCATED})
                return self.add_messages({"role": "exit", "content": "", "extra": {"exit_status": "model_finished"}})
            for call in calls:
                try:
                    fn = call["function"]
                    if fn["name"] != "effectcraft":
                        run.record("tool", name=fn["name"], arguments=fn.get("arguments"),
                                   observation={"ok": False, "error": "wrong function"}, state_sha256=None, elapsed=0)
                        raise ValueError(f"You called a function named '{fn['name']}', but the only function is "
                                         f"'effectcraft'. Call function effectcraft and put \"{fn['name']}\" in its name "
                                         "field, with the JSON object as a string in its arguments field.")
                    fields = json.loads(fn["arguments"])
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
