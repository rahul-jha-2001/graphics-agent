import json
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


def request(server, endpoint, payload=None, timeout=60):
    url = f"http://{server['host']}:{server['port']}/v1/{endpoint}"
    req = urllib.request.Request(url, json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read(4*1024*1024))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"vLLM {exc.code}: {exc.read(1000).decode(errors='replace')}") from exc


def probe(server):
    models = request(server, "models", timeout=5)["data"]
    model = next((m for m in models if m["id"] == server["model"]), None)
    if model is None:
        raise ValueError("Configured model absent from /v1/models")
    probe_messages = [{"role": "system", "content": "Use the provided tool when asked."},
                      {"role": "user", "content": "Call effectcraft with name catalog and arguments equal to the string {}."}]
    result = request(server, "chat/completions", {"model": server["model"], "messages": probe_messages,
                     "tools": [TOOL], "temperature": 0, "max_tokens": 128})
    calls = result["choices"][0]["message"].get("tool_calls", [])
    if not calls or calls[0]["function"]["name"] != "effectcraft":
        raise ValueError("Native automatic tool-call probe failed")
    parsed = json.loads(calls[0]["function"]["arguments"])
    if parsed.get("name") != "catalog" or json.loads(parsed["arguments"]) != {}:
        raise ValueError("Native tool-call probe returned unexpected arguments")
    return model


def serve(cfg, adapter=None):
    server = cfg["server"]
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
