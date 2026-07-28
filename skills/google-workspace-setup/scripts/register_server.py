#!/usr/bin/env python3
"""Register a workspace-mcp instance in ~/.claude.json.

Writes atomically, keeps the file at mode 600, and never touches unrelated keys.
Run with --dry-run first to show what would change.

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

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime

CONFIG = os.path.expanduser("~/.claude.json")
DEFAULT_CREDS_ROOT = os.path.expanduser("~/.google_workspace_mcp")


def find_uvx() -> str:
    """Absolute path to uvx. MCP servers are spawned without the login shell's
    PATH, so a bare 'uvx' command fails on many machines."""
    found = shutil.which("uvx")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/uvx", "/usr/local/bin/uvx",
                      os.path.expanduser("~/.local/bin/uvx")):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    sys.exit("uvx not found. Install it with: brew install uv")


def read_client(path: str) -> tuple[str, str]:
    with open(os.path.expanduser(path)) as fh:
        data = json.load(fh)
    for key in ("installed", "web"):
        if key in data:
            block = data[key]
            if key == "web":
                print("WARNING: this is a Web application client. A Desktop app "
                      "client is recommended — see the skill's step 3.", file=sys.stderr)
            return block["client_id"], block.get("client_secret", "")
    sys.exit(f"{path} does not look like a Google OAuth client JSON "
             "(no 'installed' or 'web' key).")


def instance_port(entry: dict) -> str | None:
    """Callback port of an existing workspace-mcp entry, or None if it is some other
    server. An entry with no WORKSPACE_MCP_PORT still occupies the server's default
    port, so it must be reported as 8000 rather than as 'no port'."""
    args = entry.get("args") or []
    if not any("workspace-mcp" in str(a) for a in args):
        return None
    return entry.get("env", {}).get("WORKSPACE_MCP_PORT", "8000")


def load_config() -> dict:
    if not os.path.exists(CONFIG):
        return {}
    with open(CONFIG) as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError as exc:
            sys.exit(f"{CONFIG} is not valid JSON ({exc}). Fix or restore it first — "
                     "refusing to overwrite.")


def write_config(data: dict) -> None:
    backup = f"{CONFIG}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    if os.path.exists(CONFIG):
        shutil.copy2(CONFIG, backup)
        os.chmod(backup, stat.S_IRUSR | stat.S_IWUSR)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CONFIG), prefix=".claude.json.tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, CONFIG)
    print(f"Wrote {CONFIG} (backup: {backup})")


def main() -> None:
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
    ap.add_argument("--port", type=int, default=8000,
                    help="OAuth callback port. Must be unique per instance (default 8000).")
    ap.add_argument("--creds-dir",
                    help="Token directory. Defaults to a per-instance directory so "
                         "instances never share credentials.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.permissions and (args.services or args.read_only):
        sys.exit("--permissions cannot be combined with --services or --read-only.")
    if not args.permissions and not args.services:
        sys.exit("Pass either --services or --permissions.")

    client_id, client_secret = read_client(args.client_json)

    server_args = ["workspace-mcp"]
    if args.permissions:
        server_args += ["--permissions", *args.permissions.split()]
    if args.read_only:
        server_args.append("--read-only")

    creds_dir = args.creds_dir or os.path.join(DEFAULT_CREDS_ROOT, args.name)

    env = {
        "OAUTHLIB_INSECURE_TRANSPORT": "1",
        "GOOGLE_OAUTH_CLIENT_ID": client_id,
        "WORKSPACE_MCP_PORT": str(args.port),
        "WORKSPACE_MCP_CREDENTIALS_DIR": os.path.expanduser(creds_dir),
    }
    if client_secret:
        env["GOOGLE_OAUTH_CLIENT_SECRET"] = client_secret
    if args.services:
        env["WORKSPACE_MCP_TOOLS"] = args.services
    if args.tool_tier:
        env["WORKSPACE_MCP_TOOL_TIER"] = args.tool_tier
    if args.default_email:
        env["USER_GOOGLE_EMAIL"] = args.default_email

    entry = {"command": find_uvx(), "args": server_args, "env": env}

    config = load_config()
    servers = config.setdefault("mcpServers", {})

    for name, existing in servers.items():
        if name == args.name:
            continue
        if instance_port(existing) == str(args.port):
            sys.exit(f"Port {args.port} is already used by the '{name}' server. "
                     "Each instance needs its own port — try --port "
                     f"{args.port + 2}.")

    action = "Replacing" if args.name in servers else "Adding"
    servers[args.name] = entry

    redacted = dict(env)
    if "GOOGLE_OAUTH_CLIENT_SECRET" in redacted:
        redacted["GOOGLE_OAUTH_CLIENT_SECRET"] = "***"
    redacted["GOOGLE_OAUTH_CLIENT_ID"] = client_id[:20] + "…"
    print(f"{action} MCP server '{args.name}':")
    print(json.dumps({"command": entry["command"], "args": server_args,
                      "env": redacted}, indent=2))

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    os.makedirs(os.path.expanduser(creds_dir), mode=0o700, exist_ok=True)
    write_config(config)
    print("\nQuit Claude Code completely (Cmd+Q) and reopen it, then authenticate.")


if __name__ == "__main__":
    main()
