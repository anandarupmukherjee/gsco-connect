#!/usr/bin/env python3
"""gsco-connect: get read-only access to the GSCO platform's data from your own machine.

You use your own Tailscale account. The platform owner shares one machine with it and sends you a
one-time invite code; this tool exchanges the code for your personal key, keeps it on this
machine, and connects your assistant (Claude Code and others) to the platform's MCP server.

    python3 gsco_connect.py            guided: does the next step that is missing
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

VERSION = "1.0.0"
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
    text = "\n".join(["GSCO access request", f"name: {name}", f"tailscale: {login}",
                      f"device: {me.get('device') or socket.gethostname()}"] + ([f"note: {org}"] if org else []))
    say("\nSend this to the platform owner (email or chat is fine; it holds nothing secret):\n")
    say("-" * 60)
    say(text)
    say("-" * 60)
    say("\nThey will share a machine with your Tailscale account and send you an invite code.")
    say("Then run:  python3 gsco_connect.py join <the code>")


def cmd_join(args) -> None:
    code = args.code or input("Paste the invite code: ").strip()
    try:
        url, code = parse_code(code)
    except ValueError as e:
        fail(str(e))
    if not reachable(url):
        login = tailscale_me().get("login")
        fail(NOT_SHARED.format(login=f" ({login})" if login else "", page=SHARED_PAGE), 2)
    status, r = http_json("POST", url + "/claim", {"code": code})
    if status != 200:
        fail(f"The platform refused the code: {r.get('error') or status}")
    save({"url": r.get("url") or url, "key": r["key"], "name": r.get("name"), "tools": r.get("tools"),
          "reuse": r.get("reuse"), "expires": r.get("expires")})
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
    cfg = need_config()
    if not reachable(cfg["url"]):
        msg = NOT_SHARED.format(login="", page=SHARED_PAGE).replace("Your invite code is still good; nothing was used up.", "").rstrip()
        (say if quiet_fail else fail)(msg)
        return False
    status, r = rpc(cfg, "tools/list")
    if status == 401:
        (say if quiet_fail else fail)(f"The platform no longer accepts this key: {r.get('error')}. Ask the owner for a new invite code.")
        return False
    tools = [t["name"] for t in (r.get("result") or {}).get("tools") or []]
    if status != 200 or not tools:
        (say if quiet_fail else fail)(f"Unexpected answer from the platform (HTTP {status}).")
        return False
    groups: dict[str, int] = {}
    for t in tools:
        groups[t.split("_")[0]] = groups.get(t.split("_")[0], 0) + 1
    say(f"The platform answers. You may use {len(tools)} tools:")
    say("  " + ", ".join(f"{g} ({n})" for g, n in sorted(groups.items())))
    if cfg.get("expires"):
        say(f"Your access runs until {cfg['expires']}.")
    return True


def cmd_setup(args) -> None:
    cfg = need_config()
    mcp_url = cfg["url"] + "/mcp"
    client = args.client
    if client is None:
        client = "claude-code" if shutil.which("claude") else "print"
    if client == "claude-code":
        exe = shutil.which("claude")
        if not exe:
            fail("Claude Code (the `claude` command) is not on this machine's PATH. Try:  python3 gsco_connect.py setup print")
        if cfg.get("client") == "claude-code":          # replace only an entry this tool made
            subprocess.run([exe, "mcp", "remove", SERVER_NAME, "-s", "user"], capture_output=True)
        p = subprocess.run([exe, "mcp", "add", "--transport", "http", "-s", "user", SERVER_NAME, mcp_url,
                            "--header", f"X-API-Key: {cfg['key']}"], capture_output=True, text=True)
        if p.returncode:
            fail(f"Claude Code did not accept the server:\n{p.stderr.strip() or p.stdout.strip()}\n"
                 f"If a server called '{SERVER_NAME}' already exists there, remove it first:  claude mcp remove {SERVER_NAME}")
        save({**cfg, "client": "claude-code"})
        say(f"Claude Code is connected: the server is called '{SERVER_NAME}'. In Claude Code, /mcp shows it;")
        say("try asking: \"use the gsco tools to find lithium refineries in Chile\".")
        return
    say("Settings for any MCP client that speaks streamable HTTP:\n")
    say(f"  URL     {mcp_url}")
    say("  Header  X-API-Key: <your key>      (the key is in " + str(CONFIG) + ")")
    say("\nClaude Code:")
    say(f"  claude mcp add --transport http -s user {SERVER_NAME} {mcp_url} --header \"X-API-Key: $(python3 gsco_connect.py key)\"")
    say("\nClients that can only start a local command (for example Claude Desktop) can use this tool as the bridge:")
    say(json.dumps({"mcpServers": {SERVER_NAME: {"command": sys.executable,
                                                 "args": [str(Path(__file__).resolve()), "bridge"]}}}, indent=2))


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
    exe = shutil.which("claude")
    if exe and load().get("client") == "claude-code":      # only an entry this tool made
        subprocess.run([exe, "mcp", "remove", SERVER_NAME, "-s", "user"], capture_output=True)
    try:
        CONFIG.unlink()
        say("The key is removed from this machine. Tell the owner if they should revoke it as well.")
    except FileNotFoundError:
        say("There was no key on this machine.")


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
    for name, text in (("test", "check the connection and list your tools"), ("status", "what this machine holds"),
                       ("forget", "remove the key from this machine"), ("key", "print the key (for scripts)"),
                       ("bridge", "stdio relay for clients that cannot send an HTTP header")):
        sub.add_parser(name, help=text)
    args = ap.parse_args(argv)
    try:
        {"request": cmd_request, "join": cmd_join, "setup": cmd_setup, "test": cmd_test, "status": cmd_status,
         "forget": cmd_forget, "key": cmd_key, "bridge": cmd_bridge, None: cmd_guided}[args.cmd](args)
    except (KeyboardInterrupt, EOFError):
        fail("\nStopped.", 130)


if __name__ == "__main__":
    main()
