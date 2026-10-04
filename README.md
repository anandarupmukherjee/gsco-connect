# gsco-connect

Read-only access to the GSCO critical-minerals platform's data, from your own machine and your
own assistant (Claude Code and other MCP clients).

You keep your own Tailscale account. The platform owner shares one machine with it and sends you
an invite code. This tool exchanges the code for your personal key and connects your assistant.
No password, key file or VPN profile is passed around.

## What you need

- Python 3.9 or later (nothing to install with pip).
- [Tailscale](https://tailscale.com/download) on this machine, signed in. A free personal
  account is enough.

## Start

```bash
git clone https://github.com/anandarupmukherjee/gsco-connect && cd gsco-connect
python3 gsco_connect.py
```

A page opens in your browser and walks you through three steps. It runs on your own computer
only; leave the terminal window open while you use it.

**1. Ask for access.** Type your name and click **Make my request**. The page shows a short text
with your name and your Tailscale login. Copy it, or click **Open in email**, and send it to the
platform owner. It holds nothing secret.

**2. Accept the share.** The owner shares one machine with your Tailscale account. Tailscale
sends you an invitation; accept it. Nothing else of theirs becomes visible to you, and nothing of
yours becomes visible to them.

**3. Enter your invite code.** The owner sends you a code that starts with `gsco1-`. Paste it and
click **Connect**. The code works once and expires after a few days. The page then shows the
tools you were given, and a **Connect Claude Code** button if Claude Code is installed.

Come back to the page at any time by running `python3 gsco_connect.py` again: it shows where you
are, lets you check the connection, and can remove your access from the computer.

Prefer the terminal? Every step is also a command (see below); `python3 gsco_connect.py cli`
walks through them as text.

## Using it

In Claude Code, `/mcp` shows a server called `gsco`. Ask in plain words, for example:

- "Use the gsco tools to find evidence on lithium refining capacity in Chile."
- "Which companies operate the solvent-extraction stage for rare earths?"

Every result says which platform service it came from. An empty result means "not known to the
platform", not "does not exist". You can read; you cannot change anything.

For another MCP client, open **Use another assistant** on the page (or run
`python3 gsco_connect.py setup print`): it shows the address and header to use, and, for a client
that can only start a local program such as Claude Desktop, a ready entry that uses this tool as
the bridge.

## Commands

| Command | What it does |
|---|---|
| *(none)* or `ui` | Opens the page |
| `cli` | The same steps as text |
| `request` | Prints what to send the owner |
| `join CODE` | Exchanges the invite code for your key, then tests and sets up |
| `setup [claude-code\|print]` | Connects Claude Code, or prints settings for another client |
| `test` | Checks the connection and lists the tools you may use |
| `status` | Shows what this machine holds |
| `forget` | Removes the key from this machine |

## If something does not work

| You see | Do this |
|---|---|
| "The platform cannot be reached from this machine yet" | Start Tailscale, and accept the owner's share invitation. Your code is not used up |
| "this invite code is not valid, or was already used" | Each code works once. Ask the owner for a new one |
| "this invite code has expired" | Ask the owner for a new one |
| "The platform no longer accepts this key" | Your access was suspended, ended or replaced. Ask the owner |
| A tool is refused | It is not in your grant. `test` shows what is |

## What is kept, and where

- Your key is in `~/.config/gsco-connect/config.json`, readable only by you. It identifies you:
  every call is counted against it. Do not share it; `forget` removes it.
- The tool talks only to the address inside your invite code, over Tailscale.
- The page is served to your own computer only (127.0.0.1), and your key is never sent to the
  browser.
- Prices with restricted reuse terms are withheld unless the owner gave you the internal level.

## For the platform owner

Invitations are made on the platform dashboard's Access page ("Invite someone"), or with
`gsco-mcp/scripts/mcp-keys.sh invite`. The server side lives in the platform repository
(`gsco-mcp/app/access.py`, `POST /claim`).

Tests: `python3 tests/test_gsco_connect.py`.
