"""A minimal client for Fusion's local MCP server (streamable HTTP), enough to run Fusion API scripts.

usage as a module:  c = Client(); print(c.script("def run(_):\n    print(1)\n"))
usage from a shell: python tools/fusion_mcp.py file.py   (runs that script in Fusion and prints its output)
"""
import json
import os
import sys
import urllib.request

URL = os.environ.get("FUSION_MCP", "http://127.0.0.1:27182/mcp")


class ScriptError(RuntimeError):
    pass


class Client:
    def __init__(self, url=URL, timeout=900):
        self.url, self.timeout, self.sid, self.n = url, timeout, None, 0
        self._post({"jsonrpc": "2.0", "id": self._id(), "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "acid-harness", "version": "1"}}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _id(self):
        self.n += 1
        return self.n

    def _post(self, body, timeout=None):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        if self.sid:
            req.add_header("Mcp-Session-Id", self.sid)
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            self.sid = r.headers.get("Mcp-Session-Id") or self.sid
            if "event-stream" not in (r.headers.get("Content-Type") or ""):
                text = r.read().decode("utf-8", "replace")
                if "id" not in body:
                    return None
                return json.loads(text)
            # an SSE stream, which may stay open: read events until the one that answers this request
            seen = []
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    seen.append(line)
                    try:
                        msg = json.loads(line[5:])
                    except ValueError:
                        continue
                    if "id" in body and msg.get("id") == body["id"] and ("result" in msg or "error" in msg):
                        return msg
            if "id" not in body:
                return None
        raise ScriptError("no reply in: " + "\n".join(seen)[:500])

    def call(self, tool, args, timeout=None):
        msg = self._post({"jsonrpc": "2.0", "id": self._id(), "method": "tools/call",
                          "params": {"name": tool, "arguments": args}}, timeout)
        if "error" in msg:
            raise ScriptError(json.dumps(msg["error"])[:2000])
        res = msg["result"]
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        try:  # the adapter wraps a script's output as {"message": ..., "success": ...}
            wrapped = json.loads(text)
            if isinstance(wrapped, dict) and wrapped.get("success") is False:
                raise ScriptError(str(wrapped.get("error") or wrapped.get("message"))[:4000])
            if isinstance(wrapped, dict) and "message" in wrapped:
                if wrapped.get("success") is False or res.get("isError"):
                    raise ScriptError(str(wrapped["message"])[:4000])
                return wrapped["message"]
        except ValueError:
            pass
        if res.get("isError"):
            raise ScriptError(text[:4000])
        return text

    def script(self, source, read_only=False, timeout=None):
        obj = {"script": source}
        if read_only:
            obj["readOnly"] = True
        return self.call("fusion_mcp_execute", {"featureType": "script", "object": obj}, timeout)


if __name__ == "__main__":
    print(Client().script(open(sys.argv[1], encoding="utf-8").read()))
