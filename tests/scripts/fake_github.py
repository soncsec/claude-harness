"""A local stand-in for api.github.com, so the harness scripts run offline with the real curl.

It answers the calls the scripts make, the way GitHub would, and records each
one (method, path, Authorization header, body) in `server.calls`.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INSTALLATION_ID = 12345


class FakeGitHub(BaseHTTPRequestHandler):
    def _reply(self, code: int, payload: object = None) -> None:
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> tuple[str, bytes]:
        auth = self.headers.get("Authorization", "")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.calls.append((self.command, self.path, auth, body))
        return auth, body

    @staticmethod
    def _has_jwt(auth: str) -> bool:
        return auth.startswith("Bearer ") and auth.count(".") == 2

    def do_POST(self) -> None:
        auth, _ = self._record()
        if self.path != f"/app/installations/{INSTALLATION_ID}/access_tokens":
            return self._reply(404, {"message": "Not Found"})
        if not self._has_jwt(auth):
            return self._reply(401, {"message": "A JSON web token could not be decoded"})
        self.server.minted += 1
        self._reply(
            201, {"token": f"ghs_FAKE{self.server.minted}", "expires_at": "2030-01-01T00:00:00Z"}
        )

    def do_GET(self) -> None:
        auth, _ = self._record()
        if self.path == "/users/test-agent%5Bbot%5D":
            return self._reply(200, {"id": 4242})
        if self.path == "/app/installations":
            if not self._has_jwt(auth):
                return self._reply(401, {"message": "Bad credentials"})
            account = {"login": "owner", "type": "User"}
            return self._reply(200, [{"id": INSTALLATION_ID, "account": account}])
        if self.path.startswith("/repos/") and self.path.endswith("/installation"):
            repo = self.path.removeprefix("/repos/").removesuffix("/installation")
            if not self._has_jwt(auth):
                return self._reply(401, {"message": "Bad credentials"})
            # Simulates the operator ticking the repo after a few checks.
            pending = self.server.reach_after.get(repo, 0)
            if pending > 0:
                self.server.reach_after[repo] = pending - 1
            elif repo in self.server.installed:
                return self._reply(200, {"id": INSTALLATION_ID})
        self._reply(404, {"message": "Not Found"})

    def do_DELETE(self) -> None:
        self._record()
        self._reply(204 if self.path == "/installation/token" else 404)

    def log_message(self, *args) -> None:
        pass


def start(installed=("owner/repo", "owner/other")) -> ThreadingHTTPServer:
    """Start the fake on a free local port; call .shutdown() when done."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    server.calls = []
    server.minted = 0
    server.installed = set(installed)
    server.reach_after = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
