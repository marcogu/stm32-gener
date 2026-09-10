import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from config_model import ConfigError, normalize_config
from generator import preview, render, write_files


ROOT = Path(__file__).resolve().parents[1]


def put(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stm32 generator ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "generated app"
        put(self.root / "env/toolchain.cmake", 'set(CMAKE_C_COMPILER "cc")\n')
        put(self.root / "env/cmake/stm32cubemx/CMakeLists.txt", 'target_sources(${CMAKE_PROJECT_NAME} PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../main.c")\nadd_library(stm32cubemx INTERFACE)\ntarget_link_libraries(${CMAKE_PROJECT_NAME} stm32cubemx)\n')
        put(self.root / "env/main.c", "void app_main(void);\nint main(void) { app_main(); return 0; }\n")
        self.raw = {
            "schemaVersion": 1,
            "environment": {"id": "test_env", "rootDir": "env", "toolchain": {"file": "env/toolchain.cmake"}},
            "project": {"name": "demo", "sourceDirs": ["src"], "includeDirs": ["inc"], "compileOptions": []},
            "libraries": [],
            "generation": {"outputFormats": ["elf"], "sizeReport": False},
        }

    def plan(self, raw=None):
        config = normalize_config(raw or self.raw, self.root, self.out)
        return config, render(config, self.root, self.out)

    def test_new_project_and_dry_run_do_not_create_files(self):
        config, files = self.plan()
        self.assertIn(Path("src/app_main.c"), files)
        self.assertNotIn("find_program", files[Path("CMakeLists.txt")])
        self.assertNotIn("set(CMAKE_C_STANDARD ", files[Path("CMakeLists.txt")])
        self.assertIn("+++ CMakeLists.txt", preview(files, self.out))
        self.assertFalse(self.out.exists())
        write_files(files, self.out, directories=config["project"]["createDirs"])
        self.assertTrue((self.out / "inc").is_dir())
        self.assertTrue(os.access(self.out / "build.sh", os.X_OK))
        self.assertFalse((self.out / "cmake").exists())
        self.assertFalse((self.out / "CMakePresets.json").exists())
        put(self.out / "src/app_main.c", "void app_main(void) { /* user's code */ }\n")
        _, second = self.plan()
        self.assertNotIn(Path("src/app_main.c"), second)
        write_files(second, self.out)
        self.assertIn("user's code", (self.out / "src/app_main.c").read_text())

    def test_declared_source_and_include_dirs_are_created_in_new_project(self):
        self.raw["project"].update(sourceDirs=["application/src", "drivers"], includeDirs=["application/inc", "generated/include"])
        config, files = self.plan()
        self.assertIn(Path("application/src/app_main.c"), files)
        self.assertEqual(set(config["project"]["createDirs"]), {
            Path("application/src"), Path("drivers"), Path("application/inc"), Path("generated/include")
        })
        self.assertFalse(self.out.exists())
        write_files(files, self.out, directories=config["project"]["createDirs"])
        for directory in config["project"]["createDirs"]:
            self.assertTrue((self.out / directory).is_dir())

    def test_missing_dirs_use_output_when_application_root_is_separate(self):
        (self.root / "application").mkdir()
        self.raw["project"].update(rootDir="application", sourceDirs=["src"], includeDirs=["inc"])
        config, files = self.plan()
        self.assertIn(Path("src/app_main.c"), files)
        self.assertEqual(set(config["project"]["createDirs"]), {Path("src"), Path("inc")})
        write_files(files, self.out, directories=config["project"]["createDirs"])
        self.assertTrue((self.out / "src").is_dir())
        self.assertTrue((self.out / "inc").is_dir())

    def test_scan_existing_project_and_exclude_build_sources(self):
        put(self.root / "app/src/a.c", "void a(void) {}\n")
        put(self.root / "app/src/nested/b.S", "")
        put(self.root / "app/build/CompilerIdC.c", "")
        (self.root / "app/inc").mkdir()
        self.raw["project"].update(rootDir="app", sourceDirs=["."], sourceFiles=["src/a.c"])
        config, files = self.plan()
        self.assertEqual(len(config["project"]["sourceFiles"]), 2)
        self.assertIn((self.root / "app/src/a.c").as_posix(), files[Path("CMakeLists.txt")])
        self.assertNotIn("CompilerIdC", files[Path("CMakeLists.txt")])
        self.assertNotIn(Path("src/app_main.c"), files)

    def test_invalid_values_fail_before_writing(self):
        for mutate in (
            lambda d: d.update(schemaVersion=2),
            lambda d: d.update(libraries={}),
            lambda d: d["project"].update(target="wrong"),
            lambda d: d["project"].update(compileStandard="c99"),
            lambda d: d["project"].update(typo=True),
            lambda d: d["project"].update(entryFile="../main.c"),
            lambda d: d["generation"].update(outputFormats=["hex"]),
            lambda d: d["generation"].update(buildDir="../build"),
            lambda d: d["generation"].update(buildScript="yes"),
        ):
            raw = copy.deepcopy(self.raw)
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                self.plan(raw)
        self.assertFalse(self.out.exists())

    def test_conflict_is_preflighted_and_skip_and_backup_preserve_data(self):
        put(self.out / "z.txt", "original")
        files = {Path("a.txt"): "new", Path("z.txt"): "replacement"}
        with self.assertRaises(ConfigError):
            write_files(files, self.out)
        self.assertFalse((self.out / "a.txt").exists())
        write_files(files, self.out, policy="skip")
        self.assertEqual((self.out / "z.txt").read_text(), "original")
        write_files(files, self.out, policy="backup")
        self.assertEqual((self.out / "z.txt.bak").read_text(), "original")
        self.assertEqual((self.out / "z.txt").read_text(), "replacement")
        with self.assertRaises(ConfigError):
            write_files({Path("z.txt"): "third"}, self.out, policy="backup")

    @unittest.skipIf(os.name == "nt", "symlink permissions differ on Windows")
    def test_symlink_parent_is_rejected_before_any_write(self):
        self.out.mkdir()
        external = self.root / "external"
        external.mkdir()
        (self.out / "src").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ConfigError):
            write_files({Path("a.txt"): "new", Path("src/a.c"): "unsafe"}, self.out, force=True)
        self.assertFalse((self.out / "a.txt").exists())
        self.assertFalse((external / "a.c").exists())

    def test_build_options_and_disabled_script(self):
        self.raw["generation"].update(generator="Unix Makefiles", buildDir="artifact build", buildScript=False)
        _, files = self.plan()
        self.assertNotIn(Path("build.sh"), files)
        self.assertNotIn("./build.sh", files[Path("README.md")])
        self.assertIn('artifact build/Debug', files[Path("README.md")])

    def test_git_validation_and_rendering_without_network(self):
        self.raw["libraries"] = [{"name": "driver", "target": "vendor::driver", "source": {
            "type": "git", "repository": "ssh://git@example.invalid/repo.git", "ref": "v1.0.0",
            "checkoutDir": str(self.root / "checkout with spaces"), "cmakeSubdir": "lib def", "fetchContentName": "DriverSource",
            "updateDisconnection": True}, "includeDirs": ["include"], "compileDefinitions": ["DRIVER_ENABLED"], "compileOptions": ["-Wall"], "linkLibraries": ["m"]}]
        _, files = self.plan()
        text = files[Path("CMakeLists.txt")]
        self.assertIn("FetchContent_MakeAvailable(DriverSource)", text)
        self.assertIn('SOURCE_SUBDIR "lib def"', text)
        self.assertIn("UPDATE_DISCONNECTED TRUE", text)
        self.assertIn((self.root / "checkout with spaces/include").as_posix(), text)
        self.assertFalse((self.root / "checkout with spaces").exists())
        self.raw["libraries"].append(copy.deepcopy(self.raw["libraries"][0]))
        with self.assertRaises(ConfigError):
            self.plan()

    def test_cli_dry_run_and_malformed_json(self):
        path = self.root / "config.json"
        put(path, json.dumps(self.raw))
        result = subprocess.run([sys.executable, str(ROOT / "generator.py"), str(path), "--output", str(self.out), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("+++ CMakeLists.txt", result.stdout)
        self.assertFalse(self.out.exists())
        put(path, '{"project":')
        result = subprocess.run([sys.executable, str(ROOT / "generator.py"), str(path), "--output", str(self.out)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    @unittest.skipUnless(all(shutil.which(v) for v in ("cmake", "ninja", "git", "cc", "ar", "bash")), "requires CMake/Ninja/Git/C compiler/ar/bash")
    @unittest.skipIf(os.name == "nt", "host executable and bash integration runs on POSIX")
    def test_full_local_static_cmake_and_git_build(self):
        def run(args, cwd=None):
            result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

        static = self.root / "static lib"
        put(static / "value.c", "int static_value(void) { return 2; }\n")
        put(static / "include/value.h", "int static_value(void);\n")
        run(["cc", "-c", str(static / "value.c"), "-o", str(static / "value.o")])
        run(["ar", "rcs", str(static / "libvalue.a"), str(static / "value.o")])
        local = self.root / "local lib"
        put(local / "CMakeLists.txt", "add_library(local_target STATIC value.c)\n")
        put(local / "value.c", "int local_value(void) { return 3; }\n")
        repo = self.root / "git repo"
        put(repo / "lib def/CMakeLists.txt", "add_library(git_target STATIC value.c)\n")
        put(repo / "lib def/value.c", "int git_value(void) { return 4; }\n")
        run(["git", "init", "--quiet", str(repo)])
        run(["git", "add", "."], repo)
        run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture"], repo)
        ref = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
        self.raw["libraries"] = [
            {"name": "prebuilt", "source": {"type": "local-static", "artifact": str(static / "libvalue.a"), "includeDirs": [str(static / "include")]}},
            {"name": "local", "target": "local_target", "source": {"type": "local-cmake", "path": str(local)}},
            {"name": "remote", "target": "git_target", "source": {"type": "git", "repository": str(repo), "ref": ref, "cmakeSubdir": "lib def", "fetchContentName": "DriverSource", "checkoutDir": "third party/git lib"}},
        ]
        put(self.root / "env/toolchain.cmake", 'set(CMAKE_C_COMPILER "cc")\nset(CMAKE_C_STANDARD 17)\n')
        self.raw["project"].update(compileDefinitions=["PROJECT_FLAG=1"])
        self.raw["environment"]["device"] = {"defines": ["ENV_FLAG=1"]}
        self.raw["libraries"][0]["compileDefinitions"] = ["LIB_FLAG=1"]
        self.raw["generation"]["buildDir"] = "artifact build"
        put(self.out / "src/app_main.c", '#include "value.h"\n#if __STDC_VERSION__ != 201710L || !PROJECT_FLAG || !ENV_FLAG || !LIB_FLAG\n#error configuration did not apply\n#endif\nint local_value(void); int git_value(void);\nvoid app_main(void) { if (static_value()+local_value()+git_value()!=9) __builtin_trap(); }\n')
        config, files = self.plan()
        write_files(files, self.out, directories=config["project"]["createDirs"])
        run(["bash", str(self.out / "build.sh"), "Debug"], self.root)
        run([str(self.out / "artifact build/Debug/demo.elf")])
        self.assertTrue((self.out / "third party/git lib/lib def/value.c").is_file())
        run(["bash", str(self.out / "build.sh"), "Release"], self.root)
        invalid = subprocess.run(["bash", str(self.out / "build.sh"), "RelWithDebInfo"], capture_output=True)
        self.assertEqual(invalid.returncode, 2)
        failed = subprocess.run(["cmake", "-S", str(self.out), "-B", str(self.out / "artifact build/Debug"), "-DTARGET_ENV=other"], capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Select the environment", failed.stderr)


if __name__ == "__main__":
    unittest.main()
