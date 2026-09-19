import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge import handle
from config_io import export_config, import_config
from config_model import ConfigError


class ConfigIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stm32 config ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / "original"
        self.base.mkdir()
        self.saved = self.root / "配置.json"
        self.config = {
            "schemaVersion": 1,
            "environment": {"id": "board", "rootDir": "env", "toolchain": {"file": "env/toolchain.cmake"}},
            "project": {"name": "demo"},
        }

    def write(self, data=None):
        self.saved.write_text(json.dumps(self.config if data is None else data), encoding="utf-8")

    def test_advanced_fields_and_path_bases_survive_export_and_import(self):
        self.config["workspace"] = {"rootDir": "../workspace", "configFile": "workspace.json"}
        self.config["environment"].update({
            "displayName": "开发板", "cubemxDir": "env/cubemx",
            "toolchain": {"file": "env/toolchain.cmake", "binDir": "tools/bin", "compilerPrefix": "arm-custom-",
                          "cmakeGenerator": "Unix Makefiles", "cmakeMinimumVersion": "3.25.1"},
            "device": {"part": "STM32F407", "family": "F4", "core": "cortex-m4", "fpu": "fpv4-sp-d16",
                       "floatAbi": "hard", "defines": ["STM32F407xx"]},
            "profiles": {"Debug": {"compileOptions": ["-Og"], "compileDefinitions": ["DEBUG"], "linkOptions": ["-g"]},
                         "Release": {"compileOptions": ["-Os"]}},
        })
        self.config["project"].update({
            "target": "demo", "version": "1.2.3", "rootDir": "app",
            "sourceDirs": ["source"], "sourceFiles": ["source/main.c"], "includeDirs": ["headers"],
            "entryFile": "source/main.c", "scanSources": False, "generateExampleMain": False,
            "compileStandard": "c17", "compileOptions": ["-Wall"], "compileDefinitions": ["APP=1"], "linkLibraries": ["m"],
        })
        self.config["libraries"] = [
            {"name": "cmake", "displayName": "CMake 库", "target": "driver::driver", "source": {"type": "local-cmake", "path": "drivers"},
             "includeDirs": ["drivers/inc"], "compileDefinitions": ["DRIVER"], "compileOptions": ["-O2"], "linkLibraries": ["m"]},
            {"name": "archive", "source": {"type": "local-static", "artifact": "lib/default.a", "includeDirs": ["lib/inc"],
                                            "artifactByEnvironment": {"board": "lib/board.a", "other": "lib/other.a"}},
             "includeDirs": ["lib/public"]},
            {"name": "remote", "source": {"type": "git", "repository": "https://example.com/repo.git", "ref": "v1",
                                           "checkoutDir": "third_party/remote", "cmakeSubdir": "cmake/library",
                                           "fetchContentName": "remote_content", "updateDisconnection": True},
             "includeDirs": ["include", "src/public"]},
            {"name": "local_git", "source": {"type": "git", "repository": "../repository", "ref": "main"}},
            {"name": "ssh_git", "source": {"type": "git", "repository": "git@example.com:org/repository.git", "ref": "main"}},
        ]
        self.config["generation"] = {
            "buildDir": "output/build", "generator": "Unix Makefiles", "buildScript": False,
            "outputFormats": ["elf", "hex"], "sizeReport": False, "conflictPolicy": "backup",
            "runConfigure": False, "runBuild": False,
            "postBuildTools": {"objcopy": "tools/bin/custom-objcopy", "size": "custom-size"},
        }
        original = copy.deepcopy(self.config)
        expected = copy.deepcopy(original)
        expected["workspace"]["rootDir"] = str(self.root / "workspace")
        expected["environment"].update(rootDir=str(self.base / "env"), cubemxDir=str(self.base / "env/cubemx"))
        expected["environment"]["toolchain"].update(file=str(self.base / "env/toolchain.cmake"), binDir=str(self.base / "tools/bin"))
        expected["project"]["rootDir"] = str(self.base / "app")
        expected["libraries"][0]["source"]["path"] = str(self.base / "drivers")
        expected["libraries"][0]["includeDirs"] = [str(self.base / "drivers/inc")]
        expected["libraries"][1]["source"].update(
            artifact=str(self.base / "lib/default.a"), includeDirs=[str(self.base / "lib/inc")],
            artifactByEnvironment={"board": str(self.base / "lib/board.a"), "other": str(self.base / "lib/other.a")})
        expected["libraries"][1]["includeDirs"] = [str(self.base / "lib/public")]
        expected["libraries"][3]["source"]["repository"] = str(self.root / "repository")
        expected["generation"]["postBuildTools"]["objcopy"] = str(self.base / "tools/bin/custom-objcopy")

        result = export_config(self.saved, self.config, self.base, str(self.root / "unused-output"))
        self.assertEqual(result, {"path": str(self.saved)})
        saved_text = self.saved.read_text(encoding="utf-8")
        self.assertIn("开发板", saved_text)
        self.assertTrue(saved_text.endswith("\n"))
        self.assertEqual(json.loads(saved_text), expected)
        self.assertEqual(import_config(self.saved), {"config": expected, "output": str(self.base / "app")})
        self.assertEqual(self.config, original)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_import_uses_file_directory_for_config_paths_and_output_default(self):
        self.write()
        result = import_config(self.saved)
        self.assertEqual(result["config"]["environment"]["rootDir"], str(self.root / "env"))
        self.assertEqual(result["config"]["environment"]["toolchain"]["file"], str(self.root / "env/toolchain.cmake"))
        self.assertEqual(result["output"], str(self.root / "demo"))
        self.assertNotIn("rootDir", result["config"]["project"])
        self.assertNotIn("libraries", result["config"])
        self.assertNotIn("generation", result["config"])

    @unittest.skipIf(os.name == "nt", "foreign Windows path handling is only needed on POSIX")
    def test_windows_absolute_paths_remain_editable_on_posix(self):
        for windows_path in (r"C:\workspace\env", "C:/workspace/env", r"\\server\share\env"):
            with self.subTest(path=windows_path):
                self.config["environment"]["rootDir"] = windows_path
                self.config["project"]["rootDir"] = windows_path
                self.config["libraries"] = [{"name": "repo", "source": {"type": "git", "repository": windows_path, "ref": "main"}}]
                self.write()
                imported = import_config(self.saved)
                self.assertEqual(imported["config"]["environment"]["rootDir"], windows_path)
                self.assertEqual(imported["config"]["libraries"][0]["source"]["repository"], windows_path)
                self.assertEqual(imported["output"], windows_path)
                self.assertEqual(imported["config"]["environment"]["toolchain"]["file"], str(self.root / "env/toolchain.cmake"))
                export_config(self.saved, self.config, self.base, "")
                saved = json.loads(self.saved.read_text(encoding="utf-8"))
                self.assertEqual(saved["environment"]["rootDir"], windows_path)
                self.assertEqual(saved["project"]["rootDir"], windows_path)
                self.assertEqual(saved["libraries"][0]["source"]["repository"], windows_path)
                self.assertEqual(saved["environment"]["toolchain"]["file"], str(self.base / "env/toolchain.cmake"))

    def test_export_records_output_when_project_root_is_omitted(self):
        output = self.root / "chosen/output"
        export_config(self.saved, self.config, self.base, str(output))
        self.assertEqual(import_config(self.saved)["output"], str(output))
        self.assertFalse(output.exists())

    def test_failed_save_preserves_existing_file_and_cleans_temporary_file(self):
        self.write()
        original = self.saved.read_bytes()
        expected_files = set(self.root.iterdir())
        create_temporary = tempfile.NamedTemporaryFile

        def fail_during_write(*args, **kwargs):
            temporary = create_temporary(*args, **kwargs)
            write = temporary.write

            def partial_write(text):
                write(text[:10])
                raise OSError("disk is full")

            temporary.write = partial_write
            return temporary

        for target, side_effect, message in (
                ("config_io.tempfile.NamedTemporaryFile", fail_during_write, "disk is full"),
                ("config_io.os.replace", PermissionError("replacement denied"), "replacement denied")):
            with self.subTest(failure=message), patch(target, side_effect=side_effect):
                with self.assertRaisesRegex(ConfigError, message):
                    export_config(self.saved, self.config, self.base, str(self.root / "output"))
            self.assertEqual(self.saved.read_bytes(), original)
            self.assertEqual(set(self.root.iterdir()), expected_files)

    def test_drafts_keep_empty_paths_and_do_not_validate_or_generate_projects(self):
        self.config["environment"] = {"id": "", "rootDir": "", "cubemxDir": "", "toolchain": {"file": "", "binDir": ""}}
        self.config["project"] = {"name": "", "rootDir": ""}
        self.config["libraries"] = [{"name": "", "source": {"type": "git", "repository": "", "ref": "", "checkoutDir": "", "cmakeSubdir": ""}}]
        with patch("bridge.normalize_config", side_effect=AssertionError("must not validate filesystem")), \
                patch("bridge.render", side_effect=AssertionError("must not render")), \
                patch("bridge.write_files", side_effect=AssertionError("must not write project files")):
            saved = handle({"action": "export_config", "path": str(self.saved), "config": self.config, "configDir": str(self.base), "output": ""})
            imported = handle({"action": "import_config", "path": saved["path"]})
        self.assertEqual(imported, {"config": self.config, "output": ""})
        self.assertEqual(set(self.root.iterdir()), {self.saved, self.base})

    def test_import_rejects_invalid_json_encoding_and_missing_file(self):
        for value in (b"{bad", b"\xff", b"[]", b"null"):
            with self.subTest(value=value):
                self.saved.write_bytes(value)
                with self.assertRaises(ConfigError):
                    import_config(self.saved)
        with self.assertRaises(ConfigError):
            import_config(self.root / "missing.json")

    def test_import_and_export_reject_unsupported_shapes_before_overwriting(self):
        invalid = [
            {**self.config, "schemaVersion": 2},
            {**self.config, "schemaVersion": True},
            {**self.config, "unknown": "field"},
            {**self.config, "environment": {}},
            {**self.config, "workspace": []},
            {**self.config, "project": {"name": "demo", "sourceDirs": "src"}},
            {**self.config, "project": {"name": "demo", "sourceFiles": [False]}},
            {**self.config, "generation": {"sizeReport": 1}},
            {**self.config, "generation": {"postBuildTools": {"other": "tool"}}},
            {**self.config, "libraries": {}},
            {**self.config, "libraries": [{"name": "lib", "source": {"type": "http"}}]},
            {**self.config, "libraries": [{"name": "lib", "source": {"type": "git", "repository": "repo", "ref": "main", "path": "bad"}}]},
            {**self.config, "libraries": [{"name": "lib", "source": {"type": "local-static", "artifactByEnvironment": {"board": []}}}]},
        ]
        for data in invalid:
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ConfigError):
                    import_config(self.saved)
                existing = self.saved.read_bytes()
                with self.assertRaises(ConfigError):
                    export_config(self.saved, data, self.base, str(self.root / "output"))
                self.assertEqual(self.saved.read_bytes(), existing)


if __name__ == "__main__":
    unittest.main()
