---
name: google-workspace-setup
description: >
  Guides a user through connecting their Google Workspace accounts (Gmail, Drive, Calendar,
  Docs, Sheets, Slides, Tasks, Contacts) to Claude Code via the workspace-mcp server —
  Google Cloud project, OAuth client, permission levels, MCP registration, and per-account
  authentication. Use for "connect my Gmail", "set up Google Drive access", "add my work
  Google account", "google workspace mcp", or when a Google Workspace tool call fails
  because no server is configured.
user-invocable: true
---

# Google Workspace → Claude Code setup

Walk the user through a working setup in one sitting. Reply in the user's language.

Two rules that prevent most failures:

- **Never paste an authorization URL into chat as the primary path.** The auto-opened
  browser always gets the complete URL. A hand-copied URL that loses `&state=…` fails with
  *"Missing OAuth state parameter"*. Offer the URL only as a fallback, and then copy it
  whole.
- **Credentials belong in the MCP server's own `env` block** in `~/.claude.json`. An `env`
  block in `settings.local.json` is not reliably inherited by MCP server processes.

## Step 0 — Preflight

`uvx` must exist. Check first, and only install if it is missing — pick the command for the
user's OS:

| OS | Check | Install |
|---|---|---|
| macOS | `command -v uvx` | `brew install uv` |
| Windows | `where uvx` | `winget install --id=astral-sh.uv -e` |
| Linux | `command -v uvx` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Windows without winget:
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

The uv installer puts the binaries in `~/.local/bin` (`uvx.exe` on Windows); winget links
them into `%LOCALAPPDATA%\Microsoft\WinGet\Links`. A newly installed `uvx` may not be on
PATH until the terminal is reopened — that does not block you, because `register_server.py`
checks those locations and resolves the absolute path itself, which is required anyway: MCP
servers are spawned without the login shell's PATH.

## Step 1 — Gather requirements

Use `AskUserQuestion`. Do not assume — the answers determine the whole layout.

1. **Which accounts?** Collect every email address. Note which are Google Workspace
   (custom domain) and which are consumer `@gmail.com` — this decides Step 4.
2. **Which services?** Default to `gmail,drive,calendar,docs,sheets,slides,tasks,contacts`.
   Offer a smaller set (`gmail,drive,calendar`) for a faster consent screen. Skip `chat`
   unless asked — it needs extra Chat API configuration and a Workspace account.
3. **What access level?** Offer:
   - **Full** read/write — Claude can send mail, edit files, create events.
   - **Read-only** — nothing is sent, changed, or deleted.
   - **Mixed** — e.g. own account full, client accounts read-only with Drive up/download.

**Permission level is a property of the server instance, not the account.** Every account
authenticated against one instance gets the same scopes. Different levels ⇒ one instance
per level (Step 5).

Per-service levels for `--permissions`:

| Service | Levels (cumulative) |
|---|---|
| `gmail` | `readonly`, `organize`, `drafts`, `send`, `full` |
| `tasks` | `readonly`, `manage`, `full` |
| everything else | `readonly`, `full` |

Drive has no middle level: **downloading works with `drive:readonly`, uploading requires
`drive:full`**. The server ships no Drive delete tool, so `drive:full` cannot destroy files —
its extra reach is overwriting and changing sharing.

## Step 2 — Google Cloud project and APIs

The user does this in the browser; you cannot. Send them to
[console.cloud.google.com](https://console.cloud.google.com/) → **create their own new
project**, then enable one API per chosen service:

[Gmail](https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com) ·
[Drive](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com) ·
[Calendar](https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com) ·
[Docs](https://console.cloud.google.com/flows/enableapi?apiid=docs.googleapis.com) ·
[Sheets](https://console.cloud.google.com/flows/enableapi?apiid=sheets.googleapis.com) ·
[Slides](https://console.cloud.google.com/flows/enableapi?apiid=slides.googleapis.com) ·
[Tasks](https://console.cloud.google.com/flows/enableapi?apiid=tasks.googleapis.com) ·
[Contacts](https://console.cloud.google.com/flows/enableapi?apiid=people.googleapis.com)

**Every person sets up their own Cloud project — never share one across colleagues.** An
unverified project is capped at 100 consenting users *for the lifetime of the project*; the
cap cannot be reset, and creating a new OAuth client inside the same project does not clear
it. A shared project also couples everyone to one publishing status (one person's personal
Gmail would force 7-day token expiry on the whole team) and to one client secret, where a
single leak or revocation breaks every colleague at once. If a company-wide app is genuinely
wanted later, that is a different setup: one project owned by IT, submitted for Google
verification.

## Step 3 — OAuth client: Desktop app

APIs & Services → Credentials → Create Credentials → OAuth client ID →
**Application type: Desktop app** → Create → **Download JSON**.

Desktop is the correct type: this server runs on the user's own machine and cannot keep a
secret confidential. It also needs no redirect URI registration — Google accepts the
loopback callback on any port and path.

Ask for the **file path** of the downloaded JSON. Never ask the user to paste the client
secret into chat.

## Step 4 — Audience and publishing status

Google Auth Platform → **Audience**. This is where setups usually break.

| Situation | Setting | Consequence |
|---|---|---|
| Only Workspace accounts (any domain, including several tenants) | **External + Production** | Unverified-app warning → *Advanced* → *Continue*. Tokens do not expire. |
| Any consumer `@gmail.com` account | **External + Testing**, all addresses added as **Test users** | Consumer accounts work, but Google revokes every refresh token after **7 days** — weekly re-authentication for all accounts. |
| All accounts in the same tenant that owns the project | **Internal** | No warning, no expiry, no verification. Cleanest, but single-tenant only. |

Publishing status is per project. A setup that needs both indefinite tokens for work
accounts *and* a personal Gmail needs a second Google Cloud project in Testing status,
plus a second server instance.

If a Workspace account hits *"Access blocked"*, its domain admin restricts unverified
third-party apps: Admin console → Security → Access and data control → API controls →
Manage Third-Party App Access → add the client ID as **Trusted**. This is uncommon —
verify before sending anyone to the admin console.

## Step 5 — Register the server(s)

One instance per permission level. Give each its own port and credential directory.

`register_server.py` lives in `scripts/` next to this file. Call it by absolute path — when
installed as a plugin the working directory is the user's project, not the plugin. Claude
Code fills in the skill directory below when it loads this file; the second line is a
fallback for versions that do not, and picks the most recently installed copy:

```bash
SETUP="${CLAUDE_SKILL_DIR}/scripts/register_server.py"
[ -f "$SETUP" ] || SETUP=$(find ~/.claude -name register_server.py -path '*google-workspace-setup*' -exec ls -t {} + 2>/dev/null | head -1)
```

The examples call `python3`, which is right for macOS and Linux. On Windows `python3` is
usually the Microsoft Store stub, not an interpreter — use `python` or `py -3` there. If the
machine has no Python at all, `uv run --no-project "$SETUP" …` works wherever `uv` is on
PATH; the script needs nothing beyond the standard library.

```bash
python3 "$SETUP" \
  --name google-workspace \
  --client-json ~/Downloads/client_secret_xxx.json \
  --services gmail,drive,calendar,docs,sheets,slides,tasks,contacts \
  --default-email me@company.com \
  --dry-run
```

Run with `--dry-run` first, show the user the output, then re-run without it. The script
backs up `~/.claude.json` (keeping the three newest backups), writes atomically, keeps mode
600 on macOS and Linux, validates service and level names, and refuses a port collision.

Re-running for an existing instance replaces its services and permission levels but keeps
its port, default email, tool tier and token directory unless you pass the matching flag —
the output lists what was kept. Pass `--fresh` to reset those to defaults instead.

A second, restricted instance:

```bash
python3 "$SETUP" \
  --name google-workspace-clients \
  --client-json ~/Downloads/client_secret_xxx.json \
  --permissions "gmail:readonly drive:full calendar:readonly docs:readonly sheets:readonly" \
  --port 8002
```

Omit `--default-email` on shared instances so every call must name the account — this
prevents an operation landing on the wrong mailbox.

Tool count is worth a word: eight services at full access is ~93 tools per instance. If the
user runs several instances, offer `--tool-tier extended` (~66) or `core` (~31). Note that
`start_google_auth` exists only in the untiered set; with a tier, authentication happens
automatically on the first failing call instead.

## Step 6 — Restart

The user must quit Claude Code **completely** and reopen it — closing the window is not
enough, the app keeps running. macOS: **⌘Q**. Windows: **Alt+F4** or quit from the tray
icon. MCP servers are spawned at session start; a config change is invisible until then.

## Step 7 — Authenticate and verify

One account at a time. Call `start_google_auth` with the account's address, and tell the
user to complete the flow in the browser window that opened. Warn them to pick the right
account in the account chooser — a wrong pick stores the token under the wrong name.

Then prove it works with a real call before moving on:

```
list_calendars(user_google_email="…")
```

Repeat for each account. Finish with a summary of what each account can and cannot do.

## Troubleshooting

Read [references/troubleshooting.md](references/troubleshooting.md) when anything fails —
it maps the exact error messages to causes.
