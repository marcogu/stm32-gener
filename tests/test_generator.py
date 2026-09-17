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

    def test_environment_paths_fold_only_within_environment_root(self):
        env_root = self.root / "env"
        cases = (
            ("descendants", env_root, "${ENV_ROOT}", False),
            ("similar prefix", self.root / "env-other", (self.root / "env-other").as_posix(), False),
            ("outside root within output", self.out / "tools", "${CMAKE_CURRENT_SOURCE_DIR}/tools", False),
            ("root itself", env_root, "${ENV_ROOT}", True),
        )
        for name, base, prefix, at_root in cases:
            with self.subTest(name=name):
                cubemx = base if at_root else base / "cmake/stm32cubemx"
                bin_dir = base if at_root else base / "bin"
                put(base / "toolchain.cmake", "# toolchain\n")
                put(cubemx / "CMakeLists.txt", "# CubeMX\n")
                bin_dir.mkdir(parents=True, exist_ok=True)
                raw = copy.deepcopy(self.raw)
                raw["environment"].update(cubemxDir=str(cubemx), toolchain={
                    "file": str(base / "toolchain.cmake"), "binDir": str(bin_dir)})
                _, files = self.plan(raw)
                text = files[Path("env_cfg.cmake")]
                self.assertIn(f'set(ENV_ROOT "{env_root.as_posix()}")', text)
                self.assertIn('set(TARGET_ENV "test_env" CACHE STRING "Selected environment")', text)
                expected = {
                    "ENV_TOOLCHAIN_FILE": prefix + "/toolchain.cmake",
                    "ENV_CUBEMX_DIR": prefix if at_root else prefix + "/cmake/stm32cubemx",
                    "_TOOL_BIN": prefix if at_root else prefix + "/bin",
                }
                for variable, value in expected.items():
                    self.assertIn(f'set({variable} "{value}")', text)

    @unittest.skipUnless(shutil.which("cmake"), "requires CMake for path expansion")
    def test_folded_environment_paths_expand_literally_and_preserve_cached_target(self):
        env_root = self.root / "test_env $literal ${TARGET_ENV}"
        env_root.mkdir()
        for external in (False, True):
            base = self.out / "external $literal ${TARGET_ENV}" if external else env_root
            toolchain = base / "tool chain ${TARGET_ENV}.cmake"
            cubemx = base / "Cube MX $literal ${TARGET_ENV}"
            bin_dir = base / "tool bin $literal ${TARGET_ENV}"
            put(toolchain, "# toolchain\n")
            put(cubemx / "CMakeLists.txt", "# CubeMX\n")
            bin_dir.mkdir(parents=True, exist_ok=True)
            raw = copy.deepcopy(self.raw)
            raw["environment"].update(rootDir=str(env_root), cubemxDir=str(cubemx), toolchain={
                "file": str(toolchain), "binDir": str(bin_dir)})
            _, files = self.plan(raw)
            put(self.out / "env_cfg.cmake", files[Path("env_cfg.cmake")])
            variables = ["TARGET_ENV", "_GENERATED_TARGET_ENV", "ENV_ROOT", "ENV_TOOLCHAIN_FILE", "ENV_CUBEMX_DIR", "_TOOL_BIN"]
            put(self.out / "inspect.cmake", 'cmake_minimum_required(VERSION 3.22)\n'
                'include("${CMAKE_CURRENT_LIST_DIR}/env_cfg.cmake")\n'
                'file(WRITE "${CMAKE_CURRENT_LIST_DIR}/expanded.txt" "'
                + "\\n".join("${" + variable + "}" for variable in variables) + '\\n$ENV{PATH}")\n')
            for cached_target in (None, "previous_environment"):
                with self.subTest(external=external, cached_target=cached_target):
                    args = [shutil.which("cmake")]
                    if cached_target:
                        args.append(f"-DTARGET_ENV:STRING={cached_target}")
                    result = subprocess.run(args + ["-P", "inspect.cmake"], cwd=self.out, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual((self.out / "expanded.txt").read_text().splitlines(), [
                        cached_target or "test_env", "test_env", env_root.as_posix(), toolchain.as_posix(),
                        cubemx.as_posix(), bin_dir.as_posix(), bin_dir.as_posix() + os.pathsep + os.environ.get("PATH", ""),
                    ])

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

    def test_library_include_paths_are_unique_and_inherited(self):
        put(self.root / "local/CMakeLists.txt", "add_library(driver INTERFACE)\n")
        put(self.root / "libdriver.a", "archive fixture")
        for name in ("headers-a", "headers-b", "headers-c"):
            (self.root / name).mkdir()
        for source in (
            {"type": "local-static", "artifact": "libdriver.a", "includeDirs": ["headers-b", "./headers-a", "headers-c"]},
            {"type": "local-cmake", "path": "local"},
            {"type": "git", "repository": "https://example.invalid/driver.git", "ref": "main", "checkoutDir": str(self.root / "checkout")},
        ):
            with self.subTest(source=source["type"]):
                base = self.root / "checkout" if source["type"] == "git" else self.root
                self.raw["libraries"] = [{"name": "driver", "source": source,
                    "includeDirs": [str(base / "headers-a"), "headers-a/../headers-a", "headers-b", "headers-b"]}]
                config, files = self.plan()
                expected = [base / "headers-a", base / "headers-b"]
                if source["type"] == "local-static":
                    expected.append(base / "headers-c")
                self.assertEqual(config["libraries"][0]["includeDirs"], expected)
                text = files[Path("CMakeLists.txt")]
                block = 'target_include_directories(driver INTERFACE\n' + ''.join(f'    "{path.as_posix()}"\n' for path in expected) + ')'
                self.assertIn(block, text)
                for path in expected:
                    self.assertEqual(text.count(f'"{path.as_posix()}"'), 1)

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
        put(local / "include/local.h", "int local_value(void);\n")
        repo = self.root / "git repo"
        put(repo / "lib def/CMakeLists.txt", "add_library(git_target STATIC value.c)\n")
        put(repo / "lib def/value.c", "int git_value(void) { return 4; }\n")
        put(repo / "include/remote.h", "int git_value(void);\n")
        run(["git", "init", "--quiet", str(repo)])
        run(["git", "add", "."], repo)
        run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture"], repo)
        ref = run(["git", "rev-parse", "HEAD"], repo).stdout.strip()
        self.raw["libraries"] = [
            {"name": "prebuilt", "includeDirs": [str(static / "include")], "source": {"type": "local-static", "artifact": str(static / "libvalue.a"), "includeDirs": [str(static / "include")]}},
            {"name": "local", "target": "local_target", "includeDirs": [str(local / "include")], "source": {"type": "local-cmake", "path": str(local)}},
            {"name": "remote", "target": "git_target", "includeDirs": ["include"], "source": {"type": "git", "repository": str(repo), "ref": ref, "cmakeSubdir": "lib def", "fetchContentName": "DriverSource", "checkoutDir": "third party/git lib"}},
        ]
        put(self.root / "env/toolchain.cmake", 'set(CMAKE_C_COMPILER "cc")\nset(CMAKE_C_STANDARD 17)\n')
        self.raw["project"].update(compileDefinitions=["PROJECT_FLAG=1"])
        self.raw["environment"]["device"] = {"defines": ["ENV_FLAG=1"]}
        self.raw["libraries"][0]["compileDefinitions"] = ["LIB_FLAG=1"]
        self.raw["generation"]["buildDir"] = "artifact build"
        put(self.out / "src/app_main.c", '#include "value.h"\n#include "local.h"\n#include "remote.h"\n#if __STDC_VERSION__ != 201710L || !PROJECT_FLAG || !ENV_FLAG || !LIB_FLAG\n#error configuration did not apply\n#endif\nvoid app_main(void) { if (static_value()+local_value()+git_value()!=9) __builtin_trap(); }\n')
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
