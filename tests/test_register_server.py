"""Run with: python3 -m unittest discover tests"""

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = (Path(__file__).resolve().parents[1]
          / "skills/google-workspace-setup/scripts/register_server.py")
spec = importlib.util.spec_from_file_location("register_server", SCRIPT)
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)

POSIX = os.name != "nt"


class RegisterServerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.config = self.home / ".claude.json"
        self.creds_root = self.home / ".google_workspace_mcp"
        self.client = self.home / "client.json"
        self.client.write_text(json.dumps(
            {"installed": {"client_id": "id.apps.googleusercontent.com",
                           "client_secret": "s3cret"}}), encoding="utf-8")
        for attr, value in (("CONFIG", str(self.config)),
                            ("DEFAULT_CREDS_ROOT", str(self.creds_root)),
                            ("find_uvx", lambda: "/fake/uvx")):
            patcher = mock.patch.object(rs, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_script(self, *argv, client=None):
        """Returns (exit code or None, combined output)."""
        out = io.StringIO()
        code = None
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                rs.main(["--client-json", str(client or self.client), *argv])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def write_config(self, data):
        self.config.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def entry(self, name="gw"):
        return json.loads(self.config.read_text(encoding="utf-8"))["mcpServers"][name]

    def backups(self):
        return sorted(p.name for p in self.home.iterdir() if ".bak-" in p.name)

    # --- writing -----------------------------------------------------------

    def test_adds_entry_without_existing_config(self):
        code, out = self.run_script("--name", "gw", "--services", "gmail,drive")
        self.assertIsNone(code, out)
        entry = self.entry()
        self.assertEqual(entry["command"], "/fake/uvx")
        self.assertEqual(entry["args"], ["workspace-mcp"])
        self.assertEqual(entry["env"]["WORKSPACE_MCP_TOOLS"], "gmail,drive")
        self.assertEqual(entry["env"]["WORKSPACE_MCP_PORT"], "8000")
        self.assertEqual(entry["env"]["WORKSPACE_MCP_CREDENTIALS_DIR"],
                         str(self.creds_root / "gw"))
        self.assertTrue((self.creds_root / "gw").is_dir())
        self.assertNotIn("backup", out)
        self.assertEqual(self.backups(), [])
        if POSIX:
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)

    def test_dry_run_writes_nothing(self):
        code, out = self.run_script("--name", "gw", "--services", "gmail", "--dry-run")
        self.assertIsNone(code, out)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.creds_root.exists())
        self.assertNotIn("s3cret", out)

    def test_unrelated_keys_and_non_ascii_survive(self):
        original = {"projects": {"C:/Users/Máté/Ügyfelek/Őrség": {"x": "🙂 „idézet”"}},
                    "mcpServers": {"other": {"type": "http", "url": "https://x.test"}}}
        self.write_config(original)
        code, out = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIsNone(code, out)
        result = json.loads(self.config.read_text(encoding="utf-8"))
        del result["mcpServers"]["gw"]
        self.assertEqual(result, original)

    def test_lone_surrogate_survives(self):
        self.config.write_text('{"cut": "abc\\ud83d"}', encoding="utf-8")
        code, out = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIsNone(code, out)
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8"))["cut"],
                         "abc\ud83d")

    @unittest.skipUnless(POSIX, "needs a fake uvx executable on PATH")
    def test_non_ascii_config_under_non_utf8_locale(self):
        # Same failure mode as Windows' ANSI code page: open() without encoding=.
        self.write_config({"projects": {"/home/Máté": {}}})
        bindir = self.home / "bin"
        bindir.mkdir()
        fake = bindir / "uvx"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        env = {"HOME": str(self.home), "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
               "LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--name", "gw", "--services", "gmail",
             "--client-json", str(self.client)],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIn("/home/Máté", data["projects"])
        self.assertIn("gw", data["mcpServers"])

    @unittest.skipUnless(POSIX, "symlinks need privileges on Windows")
    def test_symlinked_config_stays_a_symlink(self):
        real = self.home / "dotfiles" / "claude.json"
        real.parent.mkdir()
        real.write_text("{}", encoding="utf-8")
        self.config.symlink_to(real)
        code, out = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIsNone(code, out)
        self.assertTrue(self.config.is_symlink())
        self.assertIn("gw", json.loads(real.read_text(encoding="utf-8"))["mcpServers"])

    def test_backups_are_pruned_to_newest_three(self):
        self.write_config({})
        old = [f".claude.json.bak-2020010{i}-000000" for i in range(1, 5)]
        for name in old:
            (self.home / name).write_text("{}")
        foreign = self.home / ".claude.json.bak-mine"
        foreign.write_text("{}")
        code, out = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIsNone(code, out)
        kept = self.backups()
        self.assertIn(foreign.name, kept)
        kept.remove(foreign.name)
        self.assertEqual(len(kept), 3)
        self.assertEqual(kept[:2], old[2:])
        self.assertIn("backup", out)

    def test_invalid_config_is_not_overwritten(self):
        self.config.write_text("{not json", encoding="utf-8")
        code, _ = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIn("refusing to overwrite", code)
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{not json")

    # --- re-running --------------------------------------------------------

    def test_rerun_keeps_port_email_tier_and_token_directory(self):
        custom = self.home / "tokens"
        self.run_script("--name", "gw", "--services", "gmail", "--port", "8010",
                        "--default-email", "a@b.hu", "--tool-tier", "core",
                        "--creds-dir", str(custom))
        code, out = self.run_script("--name", "gw", "--services", "gmail,drive")
        self.assertIsNone(code, out)
        env = self.entry()["env"]
        self.assertEqual(env["WORKSPACE_MCP_TOOLS"], "gmail,drive")
        self.assertEqual(env["WORKSPACE_MCP_PORT"], "8010")
        self.assertEqual(env["USER_GOOGLE_EMAIL"], "a@b.hu")
        self.assertEqual(env["WORKSPACE_MCP_TOOL_TIER"], "core")
        self.assertEqual(env["WORKSPACE_MCP_CREDENTIALS_DIR"], str(custom))
        self.assertIn("Kept from the existing entry", out)

    def test_rerun_flags_override_and_fresh_resets(self):
        self.run_script("--name", "gw", "--services", "gmail", "--port", "8010",
                        "--default-email", "a@b.hu", "--tool-tier", "core")
        self.run_script("--name", "gw", "--services", "gmail", "--port", "8020")
        self.assertEqual(self.entry()["env"]["WORKSPACE_MCP_PORT"], "8020")
        self.assertEqual(self.entry()["env"]["USER_GOOGLE_EMAIL"], "a@b.hu")
        self.run_script("--name", "gw", "--services", "gmail", "--fresh")
        env = self.entry()["env"]
        self.assertEqual(env["WORKSPACE_MCP_PORT"], "8000")
        self.assertNotIn("USER_GOOGLE_EMAIL", env)
        self.assertNotIn("WORKSPACE_MCP_TOOL_TIER", env)

    def test_rerun_leaves_server_default_token_directory_alone(self):
        self.write_config({"mcpServers": {"gw": {
            "command": "uvx", "args": ["workspace-mcp"],
            "env": {"USER_GOOGLE_EMAIL": "a@b.hu"}}}})
        code, out = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIsNone(code, out)
        self.assertNotIn("WORKSPACE_MCP_CREDENTIALS_DIR", self.entry()["env"])
        self.assertFalse(self.creds_root.exists())

    def test_replacing_a_non_workspace_server_inherits_nothing(self):
        self.write_config({"mcpServers": {"gw": {
            "command": "npx", "args": ["something-else"],
            "env": {"USER_GOOGLE_EMAIL": "a@b.hu", "WORKSPACE_MCP_PORT": "9000"}}}})
        self.run_script("--name", "gw", "--services", "gmail")
        env = self.entry()["env"]
        self.assertEqual(env["WORKSPACE_MCP_PORT"], "8000")
        self.assertNotIn("USER_GOOGLE_EMAIL", env)

    # --- other servers -----------------------------------------------------

    def test_other_server_with_null_env_does_not_crash(self):
        self.write_config({"mcpServers": {
            "a": {"command": "x", "args": ["y"], "env": None},
            "b": {"type": "http", "url": "https://x.test"},
            "c": "unexpected"}})
        code, out = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIsNone(code, out)

    def test_port_collision_is_refused(self):
        self.write_config({"mcpServers": {"first": {
            "command": "uvx", "args": ["workspace-mcp"], "env": None}}})
        code, _ = self.run_script("--name", "gw", "--services", "gmail")
        self.assertIn("Port 8000 is already used by the 'first' server", code)
        self.assertNotIn("gw", json.loads(self.config.read_text())["mcpServers"])
        code, out = self.run_script("--name", "gw", "--services", "gmail", "--port", "8002")
        self.assertIsNone(code, out)

    # --- input validation --------------------------------------------------

    def test_invalid_input_is_rejected_before_writing(self):
        cases = [
            (["--name", "../../evil", "--services", "gmail"], "--name may only contain"),
            (["--name", "gw", "--services", "mail,drive"], "Unknown service(s)"),
            (["--name", "gw", "--services", "gmail,,drive"], "Unknown service(s)"),
            (["--name", "gw", "--services", "gmail", "--port", "99999"], "--port must be"),
            (["--name", "gw", "--permissions", "gmail"], "Expected service:level"),
            (["--name", "gw", "--permissions", "gmial:readonly"], "Unknown service"),
            (["--name", "gw", "--permissions", "drive:organize"], "Unknown level"),
            (["--name", "gw", "--permissions", "gmail:full gmail:readonly"], "listed twice"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv):
                code, _ = self.run_script(*argv)
                self.assertIn(message, str(code))
        self.assertFalse(self.config.exists())
        self.assertEqual(list(self.home.parent.glob("evil")), [])

    def test_services_are_normalised(self):
        self.run_script("--name", "gw", "--services", " Gmail, drive ,gmail")
        self.assertEqual(self.entry()["env"]["WORKSPACE_MCP_TOOLS"], "gmail,drive")

    def test_permissions_become_server_args(self):
        self.run_script("--name", "gw", "--permissions",
                        "gmail:organize tasks:manage drive:full")
        self.assertEqual(self.entry()["args"], ["workspace-mcp", "--permissions",
                                                "gmail:organize", "tasks:manage", "drive:full"])
        self.assertNotIn("WORKSPACE_MCP_TOOLS", self.entry()["env"])

    def test_bad_client_json_gives_a_message_not_a_traceback(self):
        missing = self.home / "nope.json"
        garbage = self.home / "garbage.json"
        garbage.write_text("{oops", encoding="utf-8")
        no_id = self.home / "no_id.json"
        no_id.write_text('{"installed": {}}', encoding="utf-8")
        wrong = self.home / "wrong.json"
        wrong.write_text('["a list"]', encoding="utf-8")
        for path, message in ((missing, "Cannot read the OAuth client JSON"),
                              (garbage, "is not valid JSON"),
                              (no_id, "has no client_id"),
                              (wrong, "does not look like a Google OAuth client JSON")):
            with self.subTest(path=path.name):
                code, _ = self.run_script("--name", "gw", "--services", "gmail", client=path)
                self.assertIn(message, str(code))
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
