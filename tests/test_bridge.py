import contextlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stm32 bridge ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "output"
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
