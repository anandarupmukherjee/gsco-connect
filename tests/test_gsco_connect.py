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

# ---- the page: same steps over a local HTTP API, for this machine's browser only
import argparse, re, time, urllib.request, urllib.error
CLAIMED.clear()
threading.Thread(target=lambda: g.cmd_ui(argparse.Namespace(port=0, no_browser=True)), daemon=True).start()
import gc
for _ in range(50):
    time.sleep(0.1)
    servers = [o for o in gc.get_objects() if type(o).__name__ == "ThreadingHTTPServer"]
    if servers:
        break
UI = f"http://127.0.0.1:{servers[0].server_port}"


def ui(method, path, body=None, token=None, host=None):
    req = urllib.request.Request(UI + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"X-Token": token} if token else {}), **({"Host": host} if host else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


st, page = ui("GET", "/")
assert st == 200 and b"GSCO platform access" in page and b"gsco_testkey" not in page
TOKEN = re.search(rb'const T = "([^"]+)"', page).group(1).decode()
assert ui("GET", "/api/state")[0] == 403 and ui("GET", "/api/state", token="wrong")[0] == 403      # the page's token is needed
assert ui("GET", "/", host="evil.example")[0] == 403                                               # and the right Host
assert ui("POST", "/api/forget", {}, host="evil.example", token=TOKEN)[0] == 403
st, raw = ui("GET", "/api/state", token=TOKEN)
state = json.loads(raw)
assert st == 200 and state["access"] is None and "tailscale" in state and "key" not in raw.decode()
st, raw = ui("POST", "/api/request", {"name": "Alice Example", "login": "alice@example.org", "note": "Uni X"}, TOKEN)
assert st == 200 and "name: Alice Example" in json.loads(raw)["text"] and "note: Uni X" in json.loads(raw)["text"]
st, raw = ui("POST", "/api/join", {"code": "nonsense"}, TOKEN)
assert st == 400 and "invite code" in json.loads(raw)["error"]
st, raw = ui("POST", "/api/test", {}, TOKEN)
assert st == 400 and "no access yet" in json.loads(raw)["error"]
st, raw = ui("POST", "/api/join", {"code": code_for(URL, "goodsecret")}, TOKEN)
assert st == 200 and json.loads(raw)["name"] == "alice" and b"gsco_testkey" not in raw
st, raw = ui("GET", "/api/state", token=TOKEN)
assert json.loads(raw)["access"]["name"] == "alice" and b"gsco_testkey" not in raw                  # the key never reaches the page
st, raw = ui("POST", "/api/test", {}, TOKEN)
assert st == 200 and json.loads(raw) == {"tools": 3, "groups": {"geo": 2, "platform": 1}}
st, raw = ui("POST", "/api/forget", {}, TOKEN)
assert st == 200 and json.loads(raw)["removed"] and not g.CONFIG.exists()
assert ui("POST", "/api/nothing", {}, TOKEN)[0] == 404
print("gsco-connect: all checks pass")
