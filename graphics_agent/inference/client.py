import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from graphics_agent.common.io import ROOT, dump, run_root

TOOL = {"type": "function", "function": {"name": "effectcraft",
    "description": "Inspect or edit the live EffectCraft project.",
    "parameters": {"type": "object", "properties": {
        "name": {"type": "string", "description": "Action from catalog, or catalog, schema, or reference."},
        "arguments": {"type": "string", "description": "JSON-encoded object matching the action schema."}},
        "required": ["name", "arguments"]}}}


def remote(server):
    """True when the policy is a hosted OpenAI-compatible API (e.g. OpenRouter) rather than local vLLM."""
    return bool(server.get("base_url"))


def request(server, endpoint, payload=None, timeout=60):
    base = server["base_url"].rstrip("/") if remote(server) else f"http://{server['host']}:{server['port']}/v1"
    headers = {"Content-Type": "application/json"}
    if server.get("api_key_env"):
        key = os.getenv(server["api_key_env"])
        if not key:
            raise RuntimeError(f"Set {server['api_key_env']} to call {base}")
        headers["Authorization"] = f"Bearer {key}"
    if payload and server.get("extra_body"):
        payload = {**server["extra_body"], **payload}
    data = json.dumps(payload).encode() if payload else None
    label = "API" if remote(server) else "vLLM"
    # Hosted free tiers rate-limit; retry 429/5xx a few times with backoff.
    for attempt in range(4 if remote(server) else 1):
        req = urllib.request.Request(f"{base}/{endpoint}", data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = json.loads(response.read(4*1024*1024))
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode(errors="replace")
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3 and remote(server):
                time.sleep(min(20, 2 ** (attempt + 2)))
                continue
            raise RuntimeError(f"{label} {exc.code}: {detail}") from exc
        if isinstance(body, dict) and body.get("error"):  # OpenRouter reports some provider failures with HTTP 200
            if attempt < 3 and remote(server):
                time.sleep(min(20, 2 ** (attempt + 2)))
                continue
            raise RuntimeError(f"{label} error: {json.dumps(body['error'])[:1000]}")
        return body


def probe(server):
    if remote(server):
        model = {"id": server["model"], "base_url": server["base_url"]}
    else:
        models = request(server, "models", timeout=5)["data"]
        model = next((m for m in models if m["id"] == server["model"]), None)
        if model is None:
            raise ValueError("Configured model absent from /v1/models")
    probe_messages = [{"role": "system", "content": "Use the provided tool when asked."},
                      {"role": "user", "content": "Call effectcraft with name catalog and arguments equal to the string {}."}]
    result = request(server, "chat/completions", {"model": server["model"], "messages": probe_messages,
                     "tools": [TOOL], "temperature": 0, "max_tokens": server.get("probe_max_tokens", 128)},
                     timeout=120 if remote(server) else 60)
    calls = result["choices"][0]["message"].get("tool_calls") or []
    if not calls or calls[0]["function"]["name"] != "effectcraft":
        raise ValueError("Native automatic tool-call probe failed: " + json.dumps(result["choices"][0]["message"])[:500])
    parsed = json.loads(calls[0]["function"]["arguments"])
    if parsed.get("name") != "catalog" or not isinstance(parsed.get("arguments"), str) or json.loads(parsed["arguments"]) != {}:
        raise ValueError("Native tool-call probe returned unexpected arguments: " + calls[0]["function"]["arguments"][:300])
    return model


def serve(cfg, adapter=None):
    server = cfg["server"]
    if remote(server):
        raise ValueError(f"{server['base_url']} is a hosted API; nothing to serve. Run probe/generate directly.")
    with socket.socket() as s:
        s.bind((server["host"], server["port"]))
    args = [str(Path(sys.executable).with_name("vllm")), "serve", server["model"], "--revision", server["revision"],
            "--tokenizer-revision", server["revision"], "--enable-auto-tool-choice",
            "--chat-template", str(ROOT / "configs/tools.jinja")]
    for key in ("host", "port", "dtype", "max_model_len", "gpu_memory_utilization", "tool_call_parser"):
        args.extend(["--" + key.replace("_", "-"), str(server[key])])
    if adapter:
        args.extend(["--enable-lora", "--lora-modules", "trained=" + str(Path(adapter).resolve(strict=True))])
    proc = subprocess.Popen(args)
    try:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"vLLM exited with {proc.returncode}")
            try:
                request(server, "models", timeout=2)
                break
            except (OSError, RuntimeError):
                time.sleep(2)
        model = probe(server)
        run_root().mkdir(parents=True, exist_ok=True)
        dump(run_root() / "server.json", {"model": model, "config": server, "args": args})
        return proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
