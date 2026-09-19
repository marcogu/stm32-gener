import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config_model import ConfigError


ROOT = Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stm32 bridge ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "output"
        home = self.root / "git home"
        home.mkdir()
        hooks = home / "hooks"
        hooks.mkdir()
        (home / ".gitconfig").write_text(
            '[user]\n name = Test\n email = test@example.invalid\n'
            '[commit]\n gpgsign = false\n'
            f'[core]\n hooksPath = "{hooks.as_posix()}"\n', encoding="utf-8")
        env_vars = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env_vars.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
        environment = patch.dict(os.environ, env_vars, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        env = self.root / "env"
        (env / "cmake/stm32cubemx").mkdir(parents=True)
        (env / "cmake/stm32cubemx/CMakeLists.txt").write_text("", encoding="utf-8")
        (env / "toolchain.cmake").write_text("", encoding="utf-8")
        self.config = {
            "schemaVersion": 1,
            "environment": {"id": "test_env", "rootDir": "env", "toolchain": {"file": "env/toolchain.cmake"}},
            "project": {"name": "demo"},
            "libraries": [],
        }

    def payload(self, action="generate"):
        return json.dumps({"action": action, "config": self.config, "configDir": str(self.root), "output": str(self.out)})

    def request(self, action="generate", input_text=None):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(ROOT / "bridge.py")],
            input=self.payload(action) if input_text is None else input_text,
            capture_output=True,
            encoding="utf-8",
        )
        self.assertNotIn("Traceback", result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2 if not response["ok"] else 0, result.stderr)
        return response

    def test_malformed_requests_return_nonempty_errors(self):
        for body in ("{bad", "[]", "{}"):
            with self.subTest(body=body):
                response = self.request(input_text=body)
                self.assertFalse(response["ok"])
                self.assertTrue(response["error"])
                self.assertFalse(self.out.exists())

    def test_library_scan_returns_declared_cmake_target(self):
        library = self.root / "library"
        library.mkdir()
        (library / "CMakeLists.txt").write_text("add_library(driver STATIC driver.c)\n", encoding="utf-8")
        request = json.dumps({"action": "scan", "kind": "library", "path": str(library)})
        response = self.request(input_text=request)
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["name"], "driver")
        self.assertEqual(response["data"]["target"], "driver")

    def test_unicode_paths_round_trip_through_utf8_bridge(self):
        if not shutil.which("git"):
            self.skipTest("requires Git")
        self.out = self.root / "\u4e2d\u6587\u5de5\u7a0b"
        payload = json.dumps({"action": "generate", "config": self.config,
                              "configDir": str(self.root), "output": str(self.out)}, ensure_ascii=False)
        response = self.request(input_text=payload)
        self.assertTrue(response["ok"])
        self.assertEqual(Path(response["data"]["output"]), self.out.resolve())
        self.assertTrue((self.out / "CMakeLists.txt").is_file())

    def test_required_field_is_reported_before_writing(self):
        self.config["project"]["name"] = ""
        response = self.request()
        self.assertFalse(response["ok"])
        self.assertIn("project.name", response["error"])
        self.assertFalse(self.out.exists())

    def test_incomplete_library_is_reported_before_writing(self):
        self.config["libraries"] = [{"name": "", "source": {"type": "local-static", "artifact": ""}}]
        response = self.request()
        self.assertFalse(response["ok"])
        self.assertIn("libraries[0].name", response["error"])
        self.assertFalse(self.out.exists())

    def test_conflict_returns_error_and_preserves_existing_file(self):
        self.out.mkdir()
        path = self.out / "CMakeLists.txt"
        path.write_text("existing project", encoding="utf-8")
        response = self.request()
        self.assertFalse(response["ok"])
        self.assertIn("existing files differ", response["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), "existing project")
        self.assertFalse((self.out / "env_cfg.cmake").exists())
        self.assertFalse((self.out / ".git").exists())

    def test_preview_does_not_initialize_git(self):
        response = self.request("preview")
        self.assertTrue(response["ok"])
        self.assertIsNone(response["data"]["gitCommitted"])
        self.assertFalse(self.out.exists())

    @unittest.skipUnless(shutil.which("git"), "requires Git")
    def test_generation_commits_only_nonignored_files_and_can_be_repeated(self):
        self.out.mkdir()
        ignored = [".DS_Store", "nested/DS_Store", "nested/.DS_Store", "Thumbs.db", ".project",
                   ".idea/settings", ".vscode/settings.json", ".codex/config", "build/cache", "cmake-build-debug/cache"]
        ignored += ["output" + suffix for suffix in (".o", ".d", ".bin", ".map", ".hex", ".lst", ".crf", ".swp", ".swo")]
        for name in ignored:
            path = self.out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("ignored", encoding="utf-8")

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.out, capture_output=True, text=True, check=True).stdout.strip()

        response = self.request()
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["data"]["gitCommitted"])
        self.assertEqual(git("log", "-1", "--format=%s"), "created.")
        self.assertEqual(git("status", "--porcelain"), "")
        tracked = git("ls-files").splitlines()
        self.assertTrue({"CMakeLists.txt", "env_cfg.cmake", ".gitignore", "build.sh", "README.md", "src/app_main.c"}.issubset(tracked))
        self.assertFalse(set(ignored) & set(tracked))
        head = git("rev-parse", "HEAD")
        response = self.request()
        self.assertTrue(response["ok"], response)
        self.assertFalse(response["data"]["gitCommitted"])
        self.assertEqual(git("rev-parse", "HEAD"), head)
        (self.out / "src/app_main.c").write_text("void app_main(void) { /* changed */ }\n", encoding="utf-8")
        response = self.request()
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["data"]["gitCommitted"])
        self.assertEqual(git("rev-list", "--count", "HEAD"), "2")

    @unittest.skipUnless(shutil.which("git"), "requires Git")
    def test_cli_generation_initializes_and_commits_but_dry_run_does_not(self):
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(self.config), encoding="utf-8")
        args = [sys.executable, str(ROOT / "generator.py"), str(config_path), "--output", str(self.out)]
        preview = subprocess.run([*args, "--dry-run"], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(self.out.exists())
        generated = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        commit = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=self.out, capture_output=True, text=True, check=True)
        self.assertEqual(commit.stdout.strip(), "created.")

    def test_git_failure_is_reported_after_files_are_written(self):
        error = ConfigError("Git step 'git commit -am created.' failed. Generated files remain; configure user.name and user.email.")
        stdout = io.StringIO()
        with patch("project_git.initialize_repository", side_effect=error), patch("sys.stdin", io.StringIO(self.payload())), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(ROOT / "bridge.py"), run_name="__main__")
        self.assertEqual(exited.exception.code, 2)
        self.assertEqual(json.loads(stdout.getvalue()), {"ok": False, "error": str(error)})
        self.assertTrue((self.out / "CMakeLists.txt").is_file())

    def test_unreadable_existing_text_always_returns_json_error(self):
        self.out.mkdir()
        path = self.out / "CMakeLists.txt"
        path.write_bytes(b"\xff")
        for action in ("preview", "generate"):
            with self.subTest(action=action):
                response = self.request(action)
                self.assertFalse(response["ok"])
                self.assertIn("codec", response["error"])
                self.assertEqual(path.read_bytes(), b"\xff")
                self.assertFalse((self.out / "env_cfg.cmake").exists())

    def test_write_and_unexpected_failures_preserve_error_protocol(self):
        for error in (PermissionError("Cannot write output directory"), RuntimeError("Generator failed"), RuntimeError()):
            with self.subTest(error=repr(error)):
                stdout = io.StringIO()
                with patch("generator.write_files", side_effect=error), patch("sys.stdin", io.StringIO(self.payload())), contextlib.redirect_stdout(stdout):
                    with self.assertRaises(SystemExit) as exited:
                        runpy.run_path(str(ROOT / "bridge.py"), run_name="__main__")
                self.assertEqual(exited.exception.code, 2)
                self.assertEqual(json.loads(stdout.getvalue()), {"ok": False, "error": str(error) or type(error).__name__})
                self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
