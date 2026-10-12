# Troubleshooting

Error messages mapped to causes. Each of these was hit during a real setup.

## "OAuth client credentials not found"

Full text mentions `GOOGLE_OAUTH_CLIENT_ID` and a `client_secret.json` path deep inside the
uv cache.

The server process did not receive the credentials. Putting them in an `env` block in
`~/.claude/settings.local.json` is **not** reliable — that block is not always inherited by
MCP server processes despite what upstream docs suggest.

Fix: the credentials belong in the server's own `env` block in `~/.claude.json` under
`mcpServers.<name>.env`. `register_server.py` does this. Then quit Claude Code (⌘Q) and
reopen — the running server still holds the old environment.

Verify without restarting:

```bash
python3 - <<'PY'
import json, os, subprocess
env = json.load(open(os.path.expanduser('~/.claude.json')))['mcpServers']['google-workspace']['env']
code = "from auth.google_auth import check_client_secrets; r=check_client_secrets(); print(r or 'OK')"
print(subprocess.run(['uvx','--from','workspace-mcp','python','-c',code],
                     env={**os.environ, **env}, capture_output=True, text=True).stdout)
PY
```

## "This app is blocked" / "Ez az alkalmazás le van tiltva"

*"This app tried to access sensitive info in your Google Account."* No Advanced link, no way
through.

The app is in **Production** but unverified, and the account is a consumer `@gmail.com`.
Google hard-blocks those for restricted Gmail/Drive scopes. Workspace accounts are not
affected — they get the bypassable warning instead, even across different tenants.

Fix: switch the project to **Testing** and add the address as a test user. Accept that all
refresh tokens then expire every 7 days. If indefinite tokens matter more, keep Production
and leave the consumer account out, or give it a second Cloud project in Testing.

## "Access blocked: … has not completed the Google verification process"

Wording that references the organization or an administrator, on a **Workspace** account.
The domain admin restricts unverified third-party apps. Publishing status will not help.

Fix (needs super admin on that domain): Admin console → Security → Access and data control
→ API controls → Manage Third-Party App Access → Configure new app → OAuth App Name Or
Client ID → paste the client ID → **Trusted: Can access all Google services**.

## "Missing OAuth state parameter"

The callback arrived without the `state` parameter, almost always because a hand-copied
authorization URL was truncated. The URL ends with
`&state=…&code_challenge=…&code_challenge_method=S256&access_type=offline&prompt=consent&login_hint=…`
and every one of those is required.

Fix: re-run `start_google_auth` and use the browser window it opens. Each run mints a fresh
`state`, so an older URL is dead even if complete.

## "redirect_uri_mismatch"

A **Web application** client whose registered redirect URI does not match. The server calls
back to `http://localhost:<PORT>/oauth2callback` — `http`, no trailing slash, port matching
`WORKSPACE_MCP_PORT`.

Fix: register that exact URI, or switch to a **Desktop app** client, which needs no
registration. Changing client type invalidates every stored token, so do it before
authenticating many accounts.

## "command not found: uvx" in the MCP server log

The server was registered with a bare `uvx`. GUI-launched apps do not inherit the login
shell's PATH, so the directory holding `uvx` is missing.

Fix: use the absolute path — `/opt/homebrew/bin/uvx` (macOS, Apple Silicon),
`/usr/local/bin/uvx` (macOS Intel), `%USERPROFILE%\.local\bin\uvx.exe` (Windows),
`~/.local/bin/uvx` (Linux). `register_server.py` fills this in automatically; the error
means the entry was written by hand.

## Tokens die after about a week

The project is in **Testing** status. Google revokes refresh tokens from test users after 7
days. There is no setting to extend it — only publishing to Production, which in turn
excludes consumer `@gmail.com` accounts.

## Tools call the wrong account

`USER_GOOGLE_EMAIL` is set on an instance shared by several accounts, so it silently fills
in as the default. Remove it from that instance's `env` — `user_google_email` then becomes a
required parameter on every tool.

## A port is already in use

Two instances share a `WORKSPACE_MCP_PORT`, so their OAuth callbacks collide. Give each its
own (8000, 8002, 8004…). `register_server.py` refuses a collision.

## An account authenticated with the wrong permissions

Tokens carry the scopes granted at consent time, so re-authenticating on a different
instance is what changes access. Delete the stale token and redo the flow on the right
instance:

```bash
ls ~/.google_workspace_mcp/*/
rm ~/.google_workspace_mcp/<instance>/<email>.json
```

## Changing services or permission levels later

Adding a service or raising a level changes the scope set, so every affected account must
re-authenticate. Re-run `register_server.py` with the same `--name` and the new `--services`
or `--permissions`, restart, then `start_google_auth` per account.

The re-run keeps the instance's port, default email, tool tier and token directory, so
accounts whose scopes did not change stay signed in. To drop one of those, pass `--fresh`
and state everything again.
