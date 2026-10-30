"""Mock of the two Anthropic endpoints the e2e test touches.

GET  /api/oauth/usage  answers from a per-token usage table.
POST /v1/messages      records which credential and session served the request.
POST /_control/usage   replaces the usage table; GET /_control/hits lists requests.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

usage_by_token = {}
hits = []
lock = threading.Lock()

MESSAGE = {
    "id": "msg_mock",
    "type": "message",
    "role": "assistant",
    "model": "claude-mock",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def credential(self):
        auth = self.headers.get("Authorization", "")
        return self.headers.get("X-Api-Key") or auth.removeprefix("Bearer ").strip()

    def do_GET(self):
        if self.path == "/api/oauth/usage":
            with lock:
                usage = usage_by_token.get(self.credential())
            return self.reply(200, usage) if usage else self.reply(401, {"error": "unknown token"})
        if self.path == "/_control/hits":
            with lock:
                return self.reply(200, list(hits))
        self.reply(404, {"error": self.path})

    def do_POST(self):
        if self.path == "/_control/usage":
            table = self.body()
            with lock:
                usage_by_token.clear()
                usage_by_token.update(table)
            return self.reply(200, {"ok": True})
        if self.path.startswith("/v1/messages"):
            self.body()
            with lock:
                hits.append({"credential": self.credential(), "session": self.headers.get("X-Claude-Code-Session-Id")})
            return self.reply(200, MESSAGE)
        self.reply(404, {"error": self.path})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    print("ready", flush=True)
    server.serve_forever()
