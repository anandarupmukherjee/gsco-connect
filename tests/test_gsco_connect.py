"""Run: python3 tests/test_gsco_connect.py   (standard library only; starts a stand-in server)."""
import base64
import io
import json
import os
import stat
import sys
import tempfile
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["GSCO_CONNECT_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gsco_connect as g  # noqa: E402

CLAIMED = []


class Platform(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/claim":
            if body.get("code") in CLAIMED or not body.get("code", "").endswith("-goodsecret"):
                return self._send(400, {"error": "this invite code is not valid, or was already used"})
            CLAIMED.append(body["code"])
            return self._send(200, {"name": "alice", "key": "gsco_testkey", "url": URL, "tools": ["geo_*"], "reuse": "public", "expires": None})
        if self.path == "/mcp":
            if self.headers.get("X-API-Key") != "gsco_testkey":
                return self._send(401, {"error": "missing, wrong or revoked API key"})
            if body.get("method") == "tools/list":
                return self._send(200, {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "geo_zone"}, {"name": "geo_ore"}, {"name": "platform_catalogue"}]}})
            return self._send(200, {"jsonrpc": "2.0", "id": body.get("id"), "result": {"ok": True}})
        self._send(404, {})

    def _send(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


srv = HTTPServer(("127.0.0.1", 0), Platform)
URL = f"http://127.0.0.1:{srv.server_port}"
threading.Thread(target=srv.serve_forever, daemon=True).start()


def code_for(url, secret):
    return "gsco1-" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=") + "-" + secret


def run(*argv):
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            g.main(list(argv))
        return 0, out.getvalue()
    except SystemExit as e:
        return e.code, out.getvalue()


# the code carries the address; damaged and foreign codes are refused before any network call
assert g.parse_code(code_for(URL, "s-with-dashes"))[0] == URL
assert g.parse_code("  " + code_for(URL, "abc")[:20] + "\n" + code_for(URL, "abc")[20:])[1] == code_for(URL, "abc")
for bad in ("", "hello", "gsco1-!!!-x", "gsco2-" + "x" * 10 + "-y", code_for("ftp://x", "y"), "gsco1-" + "aHR0cA" + "-"):
    try:
        g.parse_code(bad)
        raise AssertionError(f"accepted {bad!r}")
    except ValueError:
        pass

# no access yet: test and setup say so
assert run("test")[0] == 1 and run("setup", "print")[0] == 1

# a machine that is not shared yet: nothing is used up, and the message says what to do
dead = code_for("http://127.0.0.1:9", "goodsecret")
assert run("join", dead, "--no-setup")[0] == 2 and not g.load()

# a wrong code is refused by the platform
assert run("join", code_for(URL, "wrong"), "--no-setup")[0] == 1 and not g.load()

# the right code: key stored privately, tools listed
rc, out = run("join", code_for(URL, "goodsecret"), "--no-setup")
assert rc == 0 and "Connected as alice" in out and "geo (2)" in out, out
assert g.load()["key"] == "gsco_testkey" and "gsco_testkey" not in out
assert stat.S_IMODE(g.CONFIG.stat().st_mode) == 0o600

# the code cannot be used twice
assert run("join", code_for(URL, "goodsecret"), "--no-setup")[0] == 1

rc, out = run("test")
assert rc == 0 and "3 tools" in out
rc, out = run("setup", "print")
assert rc == 0 and URL + "/mcp" in out and "gsco_testkey" not in out and '"bridge"' in out
rc, out = run("status")
assert "alice" in out and "gsco_testkey" not in out
assert run("key")[1] == "gsco_testkey"

# the bridge relays a request with the key and stays silent for a notification
sys.stdin = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"tools/list"}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\nnot json\n')
rc, out = run("bridge")
lines = [json.loads(x) for x in out.splitlines()]
assert len(lines) == 1 and lines[0]["id"] == 7 and lines[0]["result"]["tools"][0]["name"] == "geo_zone"

# a revoked key is reported plainly
cfg = g.load(); cfg["key"] = "gsco_revoked"; g.save(cfg)
assert run("test")[0] == 1

rc, out = run("forget")
assert "removed" in out and not g.CONFIG.exists()
print("gsco-connect: all checks pass")
