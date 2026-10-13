#!/usr/bin/env python3
"""Register a workspace-mcp instance in ~/.claude.json.

Writes atomically, keeps the file at mode 600, and never touches unrelated keys.
(Windows has no file modes; there the file stays protected by the user profile's
own permissions.)
Run with --dry-run first to show what would change.

Re-running for an existing instance keeps its port, default email, tool tier and
token directory unless the matching flag is passed. --fresh starts from defaults.

Examples
--------
Full-access instance for one account:

    register_server.py --name google-workspace \\
        --client-json ~/Downloads/client_secret_xxx.json \\
        --services gmail,drive,calendar,docs,sheets,slides,tasks,contacts \\
        --default-email me@company.com

Restricted instance for client accounts (read-only, but Drive up/download):

    register_server.py --name google-workspace-clients \\
        --client-json ~/Downloads/client_secret_xxx.json \\
        --permissions "gmail:readonly drive:full calendar:readonly docs:readonly" \\
        --port 8002
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime

CONFIG = os.path.expanduser("~/.claude.json")
DEFAULT_CREDS_ROOT = os.path.expanduser("~/.google_workspace_mcp")
DEFAULT_PORT = 8000
KEEP_BACKUPS = 3

NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
BACKUP_SUFFIX_RE = re.compile(r"\.bak-\d{8}-\d{6}")

# Mirrors workspace-mcp's own tables. The server exits at startup on any other
# value, which surfaces only as an MCP server that fails to connect.
SERVICES = ("gmail", "drive", "calendar", "docs", "sheets", "chat", "forms",
            "slides", "tasks", "contacts", "search", "appscript")
LEVELS = {
    "gmail": ("readonly", "organize", "drafts", "send", "full"),
    "tasks": ("readonly", "manage", "full"),
}
DEFAULT_LEVELS = ("readonly", "full")


def find_uvx() -> str:
    """Absolute path to uvx. MCP servers are spawned without the login shell's
    PATH, so a bare 'uvx' command fails on many machines."""
    found = shutil.which("uvx")
    if found:
        return os.path.abspath(found)
    home = os.path.expanduser("~")
    local_app_data = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    for candidate in (
        os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "uvx.exe"),  # Windows, winget
        os.path.join(home, ".local", "bin", "uvx.exe"),   # Windows, uv installer
        os.path.join(home, ".cargo", "bin", "uvx.exe"),   # Windows, older uv installer
        os.path.join(home, ".local", "bin", "uvx"),       # Linux / manual install
        os.path.join(home, ".cargo", "bin", "uvx"),       # Linux, older uv installer
        "/opt/homebrew/bin/uvx",                          # macOS, Apple Silicon
        "/usr/local/bin/uvx",                             # macOS, Intel
        "/home/linuxbrew/.linuxbrew/bin/uvx",             # Linux, Homebrew
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    if os.name == "nt":
        hint = ('winget install --id=astral-sh.uv -e   (or: powershell '
                '-ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex")')
    elif sys.platform == "darwin":
        hint = "brew install uv"
    else:
        hint = "curl -LsSf https://astral.sh/uv/install.sh | sh"
    sys.exit(f"uvx not found. Install it with: {hint}")


def quit_hint(platform: str = sys.platform) -> str:
    if platform == "darwin":
        return " (Cmd+Q)"
    if platform == "win32":
        return " (Alt+F4, or Quit from the tray icon)"
    return ""


def read_client(path: str) -> tuple[str, str]:
    path = os.path.expanduser(path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        sys.exit(f"Cannot read the OAuth client JSON: {exc}")
    except ValueError as exc:
        sys.exit(f"{path} is not valid JSON ({exc}).")
    if isinstance(data, dict):
        for key in ("installed", "web"):
            block = data.get(key)
            if not isinstance(block, dict):
                continue
            if not block.get("client_id"):
                sys.exit(f"{path} has no client_id under '{key}'. Download the "
                         "OAuth client JSON again from Google Cloud.")
            if key == "web":
                print("WARNING: this is a Web application client. A Desktop app "
                      "client is recommended — see the skill's step 3.", file=sys.stderr)
            return block["client_id"], block.get("client_secret", "")
    sys.exit(f"{path} does not look like a Google OAuth client JSON "
             "(no 'installed' or 'web' key).")


def parse_services(raw: str) -> str:
    services = [s.strip().lower() for s in raw.split(",")]
    bad = [s for s in services if s not in SERVICES]
    if bad:
        sys.exit(f"Unknown service(s) in --services: {', '.join(repr(s) for s in bad)}. "
                 f"Valid: {', '.join(SERVICES)}.")
    return ",".join(dict.fromkeys(services))


def parse_permissions(raw: str) -> list[str]:
    entries = raw.split()
    seen = set()
    for entry in entries:
        service, sep, level = entry.partition(":")
        if not sep:
            sys.exit(f"Invalid --permissions entry '{entry}'. Expected service:level, "
                     "e.g. gmail:readonly.")
        if service not in SERVICES:
            sys.exit(f"Unknown service '{service}' in --permissions. "
                     f"Valid: {', '.join(SERVICES)}.")
        if service in seen:
            sys.exit(f"Service '{service}' is listed twice in --permissions.")
        seen.add(service)
        valid = LEVELS.get(service, DEFAULT_LEVELS)
        if level not in valid:
            sys.exit(f"Unknown level '{level}' for '{service}'. Valid: {', '.join(valid)}.")
    if not entries:
        sys.exit("--permissions is empty.")
    return entries


def is_workspace_mcp(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return any("workspace-mcp" in str(a) for a in entry.get("args") or [])


def instance_port(entry: object) -> str | None:
    """Callback port of an existing workspace-mcp entry, or None if it is some other
    server. An entry with no WORKSPACE_MCP_PORT still occupies the server's default
    port, so it must be reported as 8000 rather than as 'no port'."""
    if not is_workspace_mcp(entry):
        return None
    return str((entry.get("env") or {}).get("WORKSPACE_MCP_PORT", DEFAULT_PORT))


def load_config() -> dict:
    if not os.path.exists(CONFIG):
        return {}
    # Claude Code writes UTF-8. Without an explicit encoding, Windows decodes with
    # the ANSI code page and every non-ASCII character in the file is mangled.
    with open(CONFIG, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            sys.exit(f"{CONFIG} is not valid JSON ({exc}). Fix or restore it first — "
                     "refusing to overwrite.")
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        sys.exit(f"{CONFIG} has an unexpected structure — refusing to overwrite.")
    return data


def prune_backups() -> None:
    folder, base = os.path.split(CONFIG)
    backups = sorted(f for f in os.listdir(folder)
                     if f.startswith(base) and BACKUP_SUFFIX_RE.fullmatch(f[len(base):]))
    for name in backups[:-KEEP_BACKUPS]:
        os.unlink(os.path.join(folder, name))


def write_config(data: dict) -> None:
    # Write through a symlinked config (dotfile managers) instead of replacing the link.
    target = os.path.realpath(CONFIG)
    backup = None
    if os.path.exists(target):
        backup = f"{CONFIG}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(target, backup)
        os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".claude.json.tmp")
    try:
        # ensure_ascii stays on: the config can hold lone surrogates from truncated
        # strings, which only survive a round trip as \uXXXX escapes.
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if backup:
        prune_backups()
        print(f"Wrote {CONFIG} (backup: {backup}, keeping the newest {KEEP_BACKUPS})")
    else:
        print(f"Wrote {CONFIG}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True,
                    help="MCP server name, e.g. google-workspace")
    ap.add_argument("--client-json", required=True,
                    help="Path to the OAuth client JSON downloaded from Google Cloud")
    ap.add_argument("--services",
                    help="Comma-separated services (gmail,drive,calendar,...). "
                         "Mutually exclusive with --permissions.")
    ap.add_argument("--permissions",
                    help='Space-separated per-service levels, e.g. '
                         '"gmail:readonly drive:full calendar:readonly"')
    ap.add_argument("--read-only", action="store_true",
                    help="Restrict every selected service to read-only scopes")
    ap.add_argument("--tool-tier", choices=["core", "extended", "complete"],
                    help="Reduce the number of exposed tools")
    ap.add_argument("--default-email",
                    help="Default account. Omit on shared instances so the model "
                         "must name the account on every call.")
    ap.add_argument("--port", type=int,
                    help="OAuth callback port. Must be unique per instance "
                         f"(default {DEFAULT_PORT}).")
    ap.add_argument("--creds-dir",
                    help="Token directory. Defaults to a per-instance directory so "
                         "instances never share credentials.")
    ap.add_argument("--fresh", action="store_true",
                    help="When replacing an instance, do not carry over its port, "
                         "default email, tool tier and token directory.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not NAME_RE.fullmatch(args.name):
        sys.exit("--name may only contain letters, digits, '-' and '_'.")
    if args.port is not None and not 1024 <= args.port <= 65535:
        sys.exit("--port must be between 1024 and 65535.")
    if args.permissions and (args.services or args.read_only):
        sys.exit("--permissions cannot be combined with --services or --read-only.")
    if not args.permissions and not args.services:
        sys.exit("Pass either --services or --permissions.")

    services = parse_services(args.services) if args.services else None
    permissions = parse_permissions(args.permissions) if args.permissions else None

    client_id, client_secret = read_client(args.client_json)

    config = load_config()
    servers = config.setdefault("mcpServers", {})

    previous = servers.get(args.name)
    inherit = is_workspace_mcp(previous) and not args.fresh
    old_env = (previous.get("env") or {}) if inherit else {}
    kept = []

    if args.port is not None:
        port = args.port
    else:
        try:
            port = int(old_env.get("WORKSPACE_MCP_PORT", DEFAULT_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        if "WORKSPACE_MCP_PORT" in old_env:
            kept.append(f"port {port}")

    if args.creds_dir:
        creds_dir = os.path.expanduser(args.creds_dir)
    elif inherit:
        # No directory on the old entry means the server's own default. Leave it
        # that way — moving it would orphan every stored token.
        creds_dir = old_env.get("WORKSPACE_MCP_CREDENTIALS_DIR")
        kept.append(f"token directory {creds_dir or '(server default)'}")
    else:
        creds_dir = os.path.join(DEFAULT_CREDS_ROOT, args.name)

    tool_tier = args.tool_tier or old_env.get("WORKSPACE_MCP_TOOL_TIER")
    if tool_tier and not args.tool_tier:
        kept.append(f"tool tier {tool_tier}")
    default_email = args.default_email or old_env.get("USER_GOOGLE_EMAIL")
    if default_email and not args.default_email:
        kept.append(f"default email {default_email}")

    server_args = ["workspace-mcp"]
    if permissions:
        server_args += ["--permissions", *permissions]
    if args.read_only:
        server_args.append("--read-only")

    env = {
        "OAUTHLIB_INSECURE_TRANSPORT": "1",
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "WORKSPACE_MCP_PORT": str(port),
    }
    if creds_dir:
        env["WORKSPACE_MCP_CREDENTIALS_DIR"] = creds_dir
    if client_secret:
        env["GOOGLE_OAUTH_CLIENT_SECRET"] = client_secret
    if services:
        env["WORKSPACE_MCP_TOOLS"] = services
    if tool_tier:
        env["WORKSPACE_MCP_TOOL_TIER"] = tool_tier
    if default_email:
        env["USER_GOOGLE_EMAIL"] = default_email

    entry = {"command": find_uvx(), "args": server_args, "env": env}

    for name, existing in servers.items():
        if name == args.name:
            continue
        if instance_port(existing) == str(port):
            sys.exit(f"Port {port} is already used by the '{name}' server. "
                     "Each instance needs its own port — try --port "
                     f"{port + 2}.")

    action = "Replacing" if args.name in servers else "Adding"
    servers[args.name] = entry

    redacted = dict(env)
    if "GOOGLE_OAUTH_CLIENT_SECRET" in redacted:
        redacted["GOOGLE_OAUTH_CLIENT_SECRET"] = "***"
    redacted["GOOGLE_OAUTH_CLIENT_ID"] = client_id[:20] + "…"
    print(f"{action} MCP server '{args.name}':")
    print(json.dumps({"command": entry["command"], "args": server_args,
                      "env": redacted}, indent=2))
    if kept:
        print("Kept from the existing entry (pass the flag to change, or --fresh "
              "to reset): " + "; ".join(kept))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    if creds_dir:
        os.makedirs(creds_dir, mode=0o700, exist_ok=True)
    write_config(config)
    print(f"\nQuit Claude Code completely{quit_hint()} and reopen it, then authenticate.")


if __name__ == "__main__":
    main()
