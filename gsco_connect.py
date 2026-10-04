#!/usr/bin/env python3
"""gsco-connect: get read-only access to the GSCO platform's data from your own machine.

You use your own Tailscale account. The platform owner shares one machine with it and sends you a
one-time invite code; this tool exchanges the code for your personal key, keeps it on this
machine, and connects your assistant (Claude Code and others) to the platform's MCP server.

    python3 gsco_connect.py            opens a page in your browser with the three steps
    python3 gsco_connect.py cli        the same, as text in the terminal
    python3 gsco_connect.py request    what to send the owner so they can add you
    python3 gsco_connect.py join CODE  use the invite code they send back
    python3 gsco_connect.py setup      connect Claude Code, or show the settings for another client
    python3 gsco_connect.py test       check the connection and list what you may use
    python3 gsco_connect.py status     what this machine holds
    python3 gsco_connect.py forget     remove the key from this machine

Standard library only; Python 3.9 or later. Nothing is sent anywhere except to the platform's
address inside your invite code.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "1.1.0"
CONFIG = Path(os.environ.get("GSCO_CONNECT_HOME") or (Path.home() / ".config" / "gsco-connect")) / "config.json"
SERVER_NAME = "gsco"
SHARED_PAGE = "https://login.tailscale.com/admin/machines"


# ----------------------------------------------------------------------------- small helpers
def say(text: str = "") -> None:
    print(text)


def fail(text: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"\n{text}", file=sys.stderr)
    sys.exit(code)


def load() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG.parent, 0o700)
    except OSError:
        pass
    tmp = CONFIG.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, CONFIG)


# ----------------------------------------------------------------------------- Tailscale
def tailscale_bin() -> str | None:
    for c in (shutil.which("tailscale"), "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
              r"C:\Program Files\Tailscale\tailscale.exe"):
        if c and Path(c).exists():
            return c
    return None


def tailscale_me() -> dict:
    """{'login', 'device', 'running'} from this machine's Tailscale, as far as it can be read."""
    exe = tailscale_bin()
    if not exe:
        return {"installed": False, "running": False}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15).stdout
        d = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"installed": True, "running": False}
    me = d.get("Self") or {}
    user = (d.get("User") or {}).get(str(me.get("UserID"))) or {}
    return {"installed": True, "running": d.get("BackendState") == "Running", "login": user.get("LoginName") or "",
            "device": me.get("HostName") or socket.gethostname()}


# ----------------------------------------------------------------------------- the invite code
def parse_code(code: str) -> tuple[str, str]:
    """(server address, the code itself). The code is gsco1-<address>-<secret>."""
    code = "".join(code.split())
    parts = code.split("-", 2)
    if len(parts) != 3 or parts[0] != "gsco1" or not parts[2]:
        raise ValueError("That does not look like an invite code. It starts with gsco1- and is one long line.")
    try:
        url = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That invite code is damaged; copy it again, whole.") from None
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("That invite code does not name a server.")
    return url.rstrip("/"), code


def reachable(url: str, timeout: float = 6) -> bool:
    u = urlsplit(url)
    try:
        with socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(method: str, url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 30):
    """(status, parsed body). Never raises for an HTTP error status."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": f"gsco-connect/{VERSION}",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"error": raw[:200].decode("utf-8", "replace")}


NOT_SHARED = """The platform cannot be reached from this machine yet.

  1. Tailscale must be running here, signed in as the account you gave the owner{login}.
  2. The owner shares one machine with that account. Tailscale then sends you an invitation:
     accept it (it also appears at {page}).
  3. Run this again.

Your invite code is still good; nothing was used up."""


# ----------------------------------------------------------------------------- the steps themselves
class Problem(Exception):
    """Something the user can act on; the text says what."""


def request_text(name: str, login: str, note: str = "") -> str:
    me = tailscale_me()
    return "\n".join(["GSCO access request", f"name: {name.strip()}", f"tailscale: {(login or me.get('login') or '').strip()}",
                      f"device: {me.get('device') or socket.gethostname()}"] + ([f"note: {note.strip()}"] if note.strip() else []))


def do_join(code: str) -> dict:
    """Exchange an invite code for the key and store it. Returns the stored settings without the key."""
    try:
        url, code = parse_code(code)
    except ValueError as e:
        raise Problem(str(e)) from None
    if not reachable(url):
        login = tailscale_me().get("login")
        raise Problem(NOT_SHARED.format(login=f" ({login})" if login else "", page=SHARED_PAGE))
    status, r = http_json("POST", url + "/claim", {"code": code})
    if status != 200:
        raise Problem(f"The platform refused the code: {r.get('error') or status}")
    save({"url": r.get("url") or url, "key": r["key"], "name": r.get("name"), "tools": r.get("tools"),
          "reuse": r.get("reuse"), "expires": r.get("expires")})
    return {"name": r.get("name"), "expires": r.get("expires")}


def do_test() -> dict:
    """{'tools': n, 'groups': {...}} or raises Problem."""
    cfg = load()
    if not cfg.get("key"):
        raise Problem("This machine has no access yet.")
    if not reachable(cfg["url"]):
        raise Problem("The platform cannot be reached. Is Tailscale running, and did you accept the owner's share invitation?")
    status, r = rpc(cfg, "tools/list")
    if status == 401:
        raise Problem(f"The platform no longer accepts this key ({r.get('error')}). Ask the owner for a new invite code.")
    tools = [t["name"] for t in (r.get("result") or {}).get("tools") or []]
    if status != 200 or not tools:
        raise Problem(f"Unexpected answer from the platform (HTTP {status}).")
    groups: dict[str, int] = {}
    for t in tools:
        groups[t.split("_")[0]] = groups.get(t.split("_")[0], 0) + 1
    return {"tools": len(tools), "groups": dict(sorted(groups.items()))}


def do_setup_claude_code() -> str:
    cfg = load()
    exe = shutil.which("claude")
    if not cfg.get("key"):
        raise Problem("This machine has no access yet.")
    if not exe:
        raise Problem("Claude Code (the `claude` command) is not installed on this machine.")
    if cfg.get("client") == "claude-code":          # replace only an entry this tool made
        subprocess.run([exe, "mcp", "remove", SERVER_NAME, "-s", "user"], capture_output=True)
    p = subprocess.run([exe, "mcp", "add", "--transport", "http", "-s", "user", SERVER_NAME, cfg["url"] + "/mcp",
                        "--header", f"X-API-Key: {cfg['key']}"], capture_output=True, text=True)
    if p.returncode:
        raise Problem(f"Claude Code did not accept the server: {p.stderr.strip() or p.stdout.strip()} "
                      f"If a server called '{SERVER_NAME}' already exists there, remove it first: claude mcp remove {SERVER_NAME}")
    save({**cfg, "client": "claude-code"})
    return f"Claude Code is connected. The server is called '{SERVER_NAME}'; /mcp in Claude Code shows it."


def bridge_entry() -> dict:
    return {"mcpServers": {SERVER_NAME: {"command": sys.executable, "args": [str(Path(__file__).resolve()), "bridge"]}}}


def do_forget() -> bool:
    exe = shutil.which("claude")
    if exe and load().get("client") == "claude-code":      # only an entry this tool made
        subprocess.run([exe, "mcp", "remove", SERVER_NAME, "-s", "user"], capture_output=True)
    try:
        CONFIG.unlink()
        return True
    except FileNotFoundError:
        return False


# ----------------------------------------------------------------------------- commands
def cmd_request(args) -> None:
    me = tailscale_me()
    if not me.get("installed"):
        say("Tailscale is not installed on this machine. Install it from https://tailscale.com/download,")
        say("sign in (a free personal account is enough), then run this again.\n")
    elif not me.get("running"):
        say("Tailscale is installed but not connected. Start it and sign in, then run this again.\n")
    name = args.name or input("Your name (as the owner should see it): ").strip()
    login = me.get("login") or input("Your Tailscale login (the email you sign in to Tailscale with): ").strip()
    org = args.note if args.note is not None else input("Organisation or project (optional): ").strip()
    text = request_text(name, login, org or "")
    say("\nSend this to the platform owner (email or chat is fine; it holds nothing secret):\n")
    say("-" * 60)
    say(text)
    say("-" * 60)
    say("\nThey will share a machine with your Tailscale account and send you an invite code.")
    say("Then run:  python3 gsco_connect.py join <the code>")


def cmd_join(args) -> None:
    code = args.code or input("Paste the invite code: ").strip()
    try:
        r = do_join(code)
    except Problem as e:
        fail(str(e), 2 if "cannot be reached" in str(e) else 1)
    say(f"Connected as {r.get('name')}. Your key is stored in {CONFIG} (readable only by you).")
    say("The invite code is now used up; you do not need it again.\n")
    cmd_test(args, quiet_fail=True)
    if not args.no_setup:
        say()
        cmd_setup(argparse.Namespace(client=None))


def rpc(cfg: dict, method: str, params: dict | None = None):
    return http_json("POST", cfg["url"] + "/mcp", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                     {"X-API-Key": cfg["key"], "Accept": "application/json, text/event-stream"})


def need_config() -> dict:
    cfg = load()
    if not cfg.get("key"):
        fail("This machine has no access yet. Run:  python3 gsco_connect.py request")
    return cfg


def cmd_test(args, quiet_fail: bool = False) -> bool:
    need_config()
    try:
        r = do_test()
    except Problem as e:
        (say if quiet_fail else fail)(str(e))
        return False
    say(f"The platform answers. You may use {r['tools']} tools:")
    say("  " + ", ".join(f"{g} ({n})" for g, n in r["groups"].items()))
    if load().get("expires"):
        say(f"Your access runs until {load()['expires']}.")
    return True


def cmd_setup(args) -> None:
    cfg = need_config()
    mcp_url = cfg["url"] + "/mcp"
    client = args.client
    if client is None:
        client = "claude-code" if shutil.which("claude") else "print"
    if client == "claude-code":
        try:
            say(do_setup_claude_code())
        except Problem as e:
            fail(f"{e}\nTry:  python3 gsco_connect.py setup print")
        say("Try asking: \"use the gsco tools to find lithium refineries in Chile\".")
        return
    say("Settings for any MCP client that speaks streamable HTTP:\n")
    say(f"  URL     {mcp_url}")
    say("  Header  X-API-Key: <your key>      (the key is in " + str(CONFIG) + ")")
    say("\nClaude Code:")
    say(f"  claude mcp add --transport http -s user {SERVER_NAME} {mcp_url} --header \"X-API-Key: $(python3 gsco_connect.py key)\"")
    say("\nClients that can only start a local command (for example Claude Desktop) can use this tool as the bridge:")
    say(json.dumps(bridge_entry(), indent=2))


def cmd_bridge(args) -> None:
    """A stdio MCP server that relays each message to the platform with the key added: for clients
    that cannot set an HTTP header themselves. One JSON-RPC message per line, in and out."""
    cfg = need_config()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        status, r = http_json("POST", cfg["url"] + "/mcp", msg,
                              {"X-API-Key": cfg["key"], "Accept": "application/json, text/event-stream"}, timeout=660)
        if isinstance(msg, dict) and "id" not in msg:
            continue                                           # a notification: nothing to send back
        if status != 200 and "jsonrpc" not in r:
            r = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                 "error": {"code": -32000, "message": r.get("error") or f"platform answered HTTP {status}"}}
        sys.stdout.write(json.dumps(r) + "\n")
        sys.stdout.flush()


def cmd_status(args) -> None:
    cfg, me = load(), tailscale_me()
    say(f"gsco-connect {VERSION} on {platform.system()}")
    say(f"Tailscale: {'running as ' + (me.get('login') or '?') if me.get('running') else 'installed, not connected' if me.get('installed') else 'not installed'}")
    if not cfg.get("key"):
        say("Access: none on this machine yet.")
        return
    say(f"Access: {cfg.get('name')} at {cfg['url']}" + (f", until {cfg['expires']}" if cfg.get("expires") else ""))
    say(f"Key: stored in {CONFIG}")
    say(f"Platform reachable: {'yes' if reachable(cfg['url']) else 'no'}")


def cmd_key(args) -> None:
    sys.stdout.write(need_config()["key"])


def cmd_forget(args) -> None:
    say("The key is removed from this machine. Tell the owner if they should revoke it as well."
        if do_forget() else "There was no key on this machine.")


def cmd_guided(args) -> None:
    """Do whichever step is missing."""
    cfg = load()
    if cfg.get("key"):
        cmd_status(args)
        say()
        if cmd_test(args, quiet_fail=True):
            say("\nTo connect an assistant:  python3 gsco_connect.py setup")
        return
    say("GSCO platform access\n")
    say("  1  I do not have an invite code yet")
    say("  2  I have an invite code")
    pick = input("\nChoose 1 or 2: ").strip()
    say()
    if pick == "2":
        cmd_join(argparse.Namespace(code=None, no_setup=False))
    else:
        cmd_request(argparse.Namespace(name=None, note=None))


# ----------------------------------------------------------------------------- the page
PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GSCO platform access</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#d9dde3;--fg:#1c2128;--dim:#5b6470;--accent:#1f6feb;--ok:#1a7f37;--bad:#cf222e;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--line:#2a323d;--fg:#e6edf3;--dim:#8b949e;--accent:#58a6ff;--ok:#3fb950;--bad:#f85149;--warn:#d29922}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:24px;margin:0 0 4px}.sub{color:var(--dim);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:14px 0}
.card.done{opacity:.72}.card h2{font-size:17px;margin:0 0 6px;display:flex;gap:10px;align-items:center}
.n{flex:none;width:26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;font-size:14px;display:grid;place-items:center;font-weight:600}
.done .n{background:var(--ok)}
p{margin:6px 0}.dim{color:var(--dim)}.small{font-size:14px}
label{display:block;font-size:14px;color:var(--dim);margin:10px 0 3px}
input,textarea{width:100%;font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:9px 11px}
textarea{min-height:84px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:14px}
button,.btn{font:inherit;font-weight:600;cursor:pointer;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:8px;padding:9px 16px;text-decoration:none;display:inline-block}
button.sec,.btn.sec{background:transparent;color:var(--accent)}button.danger{background:transparent;color:var(--bad);border-color:var(--bad)}
button:disabled{opacity:.5;cursor:default}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;margin:10px 0 0}
.msg{margin-top:10px;white-space:pre-wrap}.msg.bad{color:var(--bad)}.msg.ok{color:var(--ok)}
.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:1px 10px;font-size:13px;margin:3px 4px 0 0}
.status{display:flex;gap:8px;align-items:center;font-size:15px}.dot{width:10px;height:10px;border-radius:50%;background:var(--dim)}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.hide{display:none!important}
details{margin-top:12px}summary{cursor:pointer;color:var(--accent)}
</style></head><body><div class="wrap">
<h1>GSCO platform access</h1>
<p class="sub">Read-only access to the GSCO critical-minerals platform, for you and your assistant. Three steps; this page keeps your place.</p>

<div class="card"><div class="status"><span id="tsDot" class="dot"></span><span id="tsText">Checking Tailscale…</span></div>
  <p id="tsHelp" class="small dim hide"></p></div>

<div id="connected" class="card hide">
  <h2><span class="n">✓</span> <span id="cTitle">Connected</span></h2>
  <p id="cTools" class="small"></p><div id="cGroups"></div>
  <div class="row">
    <button id="btnClaude">Connect Claude Code</button>
    <button id="btnTest" class="sec">Check the connection</button>
  </div>
  <div id="cMsg" class="msg"></div>
  <details><summary>Use another assistant</summary>
    <p class="small">Any MCP client that can send an HTTP header:</p>
    <pre id="oUrl"></pre>
    <p class="small">A client that can only start a local program (Claude Desktop, for example): add this to its MCP settings and restart it.</p>
    <pre id="oBridge"></pre><div class="row"><button id="btnCopyBridge" class="sec">Copy</button><span id="oCopied" class="dim small"></span></div>
  </details>
  <details><summary>Remove access from this computer</summary>
    <p class="small dim">Deletes your key here. Tell the platform owner if they should cancel it as well.</p>
    <button id="btnForget" class="danger">Remove</button></details>
</div>

<div id="steps">
<div id="s1" class="card">
  <h2><span class="n">1</span> Ask for access</h2>
  <p class="small dim">Tell the platform owner who you are. Nothing secret is sent.</p>
  <label>Your name</label><input id="rName" autocomplete="name" placeholder="Alice Example">
  <div id="rLoginWrap" class="hide"><label>Your Tailscale login (the email you sign in to Tailscale with)</label><input id="rLogin" placeholder="alice@example.org"></div>
  <label>Organisation or project (optional)</label><input id="rNote" placeholder="University of X">
  <div class="row"><button id="btnRequest">Make my request</button></div>
  <div id="rOut" class="hide"><pre id="rText"></pre>
    <div class="row"><button id="btnCopyReq">Copy</button><a id="rMail" class="btn sec" href="#">Open in email</a><span id="rCopied" class="dim small"></span></div>
    <p class="small dim">Send this to the platform owner, by email or chat.</p></div>
</div>
<div id="s2" class="card">
  <h2><span class="n">2</span> Accept the share</h2>
  <p class="small">The owner shares one machine with your Tailscale account. Tailscale sends you an invitation: accept it.
    You see nothing else of theirs, and they see nothing of yours.</p>
  <div class="row"><a class="btn sec" target="_blank" rel="noopener" href="https://login.tailscale.com/admin/machines">Open Tailscale</a></div>
</div>
<div id="s3" class="card">
  <h2><span class="n">3</span> Enter your invite code</h2>
  <p class="small dim">The owner sends you a code that starts with <code>gsco1-</code>. It works once.</p>
  <textarea id="jCode" placeholder="gsco1-…" spellcheck="false"></textarea>
  <div class="row"><button id="btnJoin">Connect</button></div>
  <div id="jMsg" class="msg"></div>
</div>
</div>
<p class="small dim" id="foot"></p>
</div>
<script>
const T = "__TOKEN__", $ = id => document.getElementById(id);
async function api(method, path, body) {
  const r = await fetch(path, {method, headers: {"X-Token": T, "Content-Type": "application/json"}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json().catch(() => ({error: "HTTP " + r.status}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}
function msg(id, text, cls) { const e = $(id); e.textContent = text || ""; e.className = "msg " + (cls || ""); }
async function copy(text, note) { try { await navigator.clipboard.writeText(text); $(note).textContent = "copied"; } catch { $(note).textContent = "select the text and copy it"; } }
function showTools(r) {
  $("cTools").textContent = "You may use " + r.tools + " tools:";
  $("cGroups").replaceChildren(...Object.entries(r.groups).map(([g, n]) => { const s = document.createElement("span"); s.className = "pill"; s.textContent = g + " (" + n + ")"; return s; }));
}
async function refresh() {
  const s = await api("GET", "/api/state");
  const ts = s.tailscale;
  $("tsDot").className = "dot " + (ts.running ? "ok" : "bad");
  $("tsText").textContent = ts.running ? "Tailscale is running as " + (ts.login || "(unknown login)") : ts.installed ? "Tailscale is installed but not connected" : "Tailscale is not installed on this computer";
  $("tsHelp").classList.toggle("hide", ts.running);
  $("tsHelp").textContent = ts.installed ? "Start Tailscale and sign in, then reload this page." : "Install it from tailscale.com/download and sign in (a free personal account is enough), then reload this page.";
  $("rLoginWrap").classList.toggle("hide", !!ts.login);
  $("foot").textContent = "gsco-connect " + s.version + " · your key, once you have one, is kept in " + s.config_path;
  const on = !!s.access;
  $("connected").classList.toggle("hide", !on); $("steps").classList.toggle("hide", on);
  if (on) {
    $("cTitle").textContent = "Connected as " + s.access.name + (s.access.expires ? " · until " + s.access.expires : "");
    $("btnClaude").classList.toggle("hide", !s.claude);
    $("btnClaude").textContent = s.access.client === "claude-code" ? "Reconnect Claude Code" : "Connect Claude Code";
    $("oUrl").textContent = "URL     " + s.access.url + "/mcp\nHeader  X-API-Key: <your key, in " + s.config_path + ">";
    $("oBridge").textContent = JSON.stringify(s.bridge, null, 2);
    if (!s.claude) msg("cMsg", "Claude Code is not installed on this computer. Use the settings under “Use another assistant”.", "");
    try { showTools(await api("POST", "/api/test")); } catch (e) { msg("cMsg", e.message, "bad"); }
  }
}
$("btnRequest").onclick = async () => {
  if (!$("rName").value.trim()) { $("rName").focus(); return; }
  const r = await api("POST", "/api/request", {name: $("rName").value, login: $("rLogin").value, note: $("rNote").value});
  $("rText").textContent = r.text; $("rOut").classList.remove("hide"); $("s1").classList.add("done");
  $("rMail").href = "mailto:?subject=" + encodeURIComponent("GSCO access request") + "&body=" + encodeURIComponent(r.text);
};
$("btnCopyReq").onclick = () => copy($("rText").textContent, "rCopied");
$("btnJoin").onclick = async () => {
  $("btnJoin").disabled = true; msg("jMsg", "Connecting…");
  try { await api("POST", "/api/join", {code: $("jCode").value}); $("jCode").value = ""; msg("jMsg", ""); await refresh(); }
  catch (e) { msg("jMsg", e.message, "bad"); }
  $("btnJoin").disabled = false;
};
$("btnTest").onclick = async () => { try { showTools(await api("POST", "/api/test")); msg("cMsg", "The platform answers.", "ok"); } catch (e) { msg("cMsg", e.message, "bad"); } };
$("btnClaude").onclick = async () => { try { msg("cMsg", (await api("POST", "/api/setup")).message, "ok"); await refresh(); } catch (e) { msg("cMsg", e.message, "bad"); } };
$("btnCopyBridge").onclick = () => copy($("oBridge").textContent, "oCopied");
$("btnForget").onclick = async () => { if (confirm("Remove your access from this computer?")) { await api("POST", "/api/forget"); msg("cMsg", ""); await refresh(); } };
refresh().catch(e => { $("tsText").textContent = e.message; });
</script></body></html>
'''


def cmd_ui(args) -> None:
    """The same steps as a page in the browser, served to this machine only."""
    import secrets as _secrets
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = _secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: dict) -> None:
            self._send(status, json.dumps(obj).encode())

        def _local(self) -> bool:
            """Only this machine's own browser: the right Host, and for /api the page's token."""
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            if not self._local():
                return self._json(403, {"error": "this page is for this computer only"})
            if self.path in ("/", "/index.html"):
                return self._send(200, PAGE.replace("__TOKEN__", token).encode(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._api(lambda body: state())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local():
                return self._json(403, {"error": "this page is for this computer only"})
            routes = {"/api/request": lambda b: {"text": request_text(str(b.get("name") or ""), str(b.get("login") or ""), str(b.get("note") or ""))},
                      "/api/join": lambda b: do_join(str(b.get("code") or "")),
                      "/api/test": lambda b: do_test(),
                      "/api/setup": lambda b: {"message": do_setup_claude_code()},
                      "/api/forget": lambda b: {"removed": do_forget()}}
            if self.path not in routes:
                return self._json(404, {"error": "not found"})
            self._api(routes[self.path])

        def _api(self, fn):
            if self.headers.get("X-Token") != token:
                return self._json(403, {"error": "open this page from the address the tool printed"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                self._json(200, fn(body if isinstance(body, dict) else {}))
            except Problem as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def state() -> dict:
        cfg = load()
        return {"version": VERSION, "tailscale": tailscale_me(), "claude": bool(shutil.which("claude")),
                "config_path": str(CONFIG), "bridge": bridge_entry(),
                "access": ({"name": cfg.get("name"), "url": cfg.get("url"), "expires": cfg.get("expires"),
                            "client": cfg.get("client")} if cfg.get("key") else None)}

    srv = None
    for port in ([args.port] if getattr(args, "port", None) else [8765, 8766, 8767, 0]):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        fail("Could not open a local port for the page. Use the commands instead:  python3 gsco_connect.py --help")
    url = f"http://127.0.0.1:{srv.server_port}/"
    say(f"GSCO platform access is open at {url}")
    say("Leave this window open while you use the page. Press Ctrl+C here to close it.")
    if not getattr(args, "no_browser", False):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        say("\nClosed.")
    finally:
        srv.server_close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="gsco-connect", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("request", help="what to send the owner so they can add you")
    p.add_argument("--name")
    p.add_argument("--note")
    p = sub.add_parser("join", help="use the invite code the owner sent")
    p.add_argument("code", nargs="?")
    p.add_argument("--no-setup", action="store_true", help="store the key only; do not configure a client")
    p = sub.add_parser("setup", help="connect Claude Code, or print the settings")
    p.add_argument("client", nargs="?", choices=["claude-code", "print"])
    p = sub.add_parser("ui", help="the same steps as a page in your browser (the default)")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true", help="print the address instead of opening it")
    sub.add_parser("cli", help="the guided steps as text, without a browser")
    for name, text in (("test", "check the connection and list your tools"), ("status", "what this machine holds"),
                       ("forget", "remove the key from this machine"), ("key", "print the key (for scripts)"),
                       ("bridge", "stdio relay for clients that cannot send an HTTP header")):
        sub.add_parser(name, help=text)
    args = ap.parse_args(argv)
    try:
        {"request": cmd_request, "join": cmd_join, "setup": cmd_setup, "test": cmd_test, "status": cmd_status,
         "forget": cmd_forget, "key": cmd_key, "bridge": cmd_bridge, "ui": cmd_ui, "cli": cmd_guided,
         None: cmd_ui}[args.cmd](args)
    except (KeyboardInterrupt, EOFError):
        fail("\nStopped.", 130)


if __name__ == "__main__":
    main()
