"""One bounded newline-delimited MCP stdio transport per live project."""
import json
import os
import select
import subprocess
import time

import jsonschema

from graphics_agent.common.io import MAX_BYTES, binary


class Engine:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.sequence = 0
        self.tools = {}
        self.buffer = b""

    def start(self):
        path = str(self.root)
        with (self.root.parent / "engine.stderr").open("ab") as log:
            self.proc = subprocess.Popen(
                [binary(), "mcp", "--empty", "--automation-read-root", path, "--automation-write-root", path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, cwd=path, bufsize=0,
            )
        self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "graphics-agent", "version": "0.1"}})
        self.send({"method": "notifications/initialized"})
        page = self.rpc("tools/list", {})
        self.tools = {item["name"]: item for item in page["tools"]}

    def send(self, obj):
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", **obj}) + "\n").encode())

    def rpc(self, method, params):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + 20
        while True:
            if b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.proc.stdout], [], [], remaining)[0]:
                    raise TimeoutError(f"MCP timeout during {method}")
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("EffectCraft disconnected")
                self.buffer += chunk
                if len(self.buffer) > MAX_BYTES:
                    raise ValueError("Oversized MCP response")
                continue
            line, self.buffer = self.buffer.split(b"\n", 1)
            reply = json.loads(line)
            if reply.get("id") != self.sequence:
                continue
            if "error" in reply:
                raise RuntimeError(str(reply["error"]))
            return reply["result"]

    def call(self, name, args):
        jsonschema.validate(args, self.tools[name]["inputSchema"])
        reply = self.rpc("tools/call", {"name": name, "arguments": args})
        message = reply["content"][0]["text"]
        if reply.get("isError"):
            raise ValueError(message[:1200])
        try:
            return json.loads(message)
        except json.JSONDecodeError:
            return message

    def close(self):
        if self.proc is None:
            return
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
        self.proc = None
