import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scanner import ScanError, discover_sources, scan_environment, scan_project, scan_workspace


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_sources_are_sorted_deduplicated_and_pruned(self):
        for name in ("src/main.c", "src/sub/handler.c", "src/reset.S", "src/startup.s", "extra.c"):
            self.write(name)
        self.write("src/ignored.cpp")
        for directory in ("build", "cmake-build-debug", "cmake-build-release", ".git", "vendor", "Lib", "libs", "third_party", "output", "_deps"):
            self.write(f"{directory}/ignored.c")
            self.write(f"src/{directory}/ignored.c")
        self.assertEqual(discover_sources(self.root, ["src", "src/sub"]),
                         ["src/main.c", "src/reset.S", "src/startup.s", "src/sub/handler.c"])
        self.assertEqual(discover_sources(self.root, ["."]),
                         ["extra.c", "src/main.c", "src/reset.S", "src/startup.s", "src/sub/handler.c"])
        self.assertEqual(discover_sources(self.root, ["build", "src/vendor"]), [])
        self.assertEqual(discover_sources(self.root, []), [])

    def test_symlink_directories_and_files_are_not_followed(self):
        self.write("src/main.c")
        target = self.write("actual/source.c").parent
        try:
            (self.root / "src" / "linked").symlink_to(target, target_is_directory=True)
            (self.root / "src" / "linked.c").symlink_to(target / "source.c")
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable on this platform")
        self.assertEqual(discover_sources(self.root, ["src"]), ["src/main.c"])
        self.assertEqual(discover_sources(self.root, ["src/linked"]), [])

    def test_source_directories_must_exist_inside_project(self):
        with self.assertRaisesRegex(ScanError, "Source directory does not exist"):
            discover_sources(self.root, ["missing"])
        with self.assertRaisesRegex(ScanError, "inside project root"):
            discover_sources(self.root, [".."])
        with self.assertRaisesRegex(ScanError, "non-empty"):
            discover_sources(self.root, [""])

    def test_project_names_ignore_comments_strings_and_bracket_comments(self):
        self.write("CMakeLists.txt", '''
# project(wrong_line)
#[=[ project(wrong_block) ]=]
message("project(wrong_string)")
message([=[project(wrong_bracket)]=])
project(
    "sensor-app" VERSION 1.0.0 LANGUAGES C ASM
)
''')
        self.write("env_cfg.cmake", '''
# set(TARGET_ENV "wrong_line")
#[[ set(TARGET_ENV wrong_block) ]]
message("set(TARGET_ENV wrong_string)")
set(TARGET_ENV "stm32f407vet6" CACHE STRING "Target # name")
''')
        self.write("src/app_main.c")
        self.write("inc/app.h")
        self.write("include/device/device.h")
        self.write("src/private.h")
        self.write("Lib/noise/noise.h")
        result = scan_project(self.root, source_dirs=["src"])
        self.assertEqual(result["name"], "sensor-app")
        self.assertEqual(result["targetEnv"], "stm32f407vet6")
        self.assertEqual(result["sourceFiles"], ["src/app_main.c"])
        self.assertEqual(result["headerFiles"], ["inc/app.h", "include/device/device.h", "src/private.h"])
        self.assertEqual(result["includeDirs"], ["inc", "include", "include/device", "src"])
        self.assertTrue(result["hasCMake"])

    def test_dynamic_or_single_quoted_cmake_names_are_not_claimed(self):
        for project_name, target in (("${APP_NAME}", "${SELECTED_ENV}"), ("'name'", "'chip'")):
            self.write("CMakeLists.txt", f"project({project_name})")
            self.write("env_cfg.cmake", f"set(TARGET_ENV {target})")
            result = scan_project(self.root)
            self.assertEqual(result["name"], self.root.name)
            self.assertIsNone(result["targetEnv"])

    def test_bracket_literals_and_cmake_fallback(self):
        self.write("CMakeLists.txt", 'PROJECT([=[sensor_app]=] LANGUAGES C ASM)\nset(TARGET_ENV chip-a)')
        result = scan_project(self.root)
        self.assertEqual(result["name"], "sensor_app")
        self.assertEqual(result["targetEnv"], "chip-a")

    def test_environment_recognizes_toolchain_content_and_nested_files(self):
        custom = self.write("cmake/custom-name.cmake", 'set(CMAKE_C_COMPILER "arm-none-eabi-gcc")')
        hinted = self.write("config/board.cmake", 'set(CMAKE_SYSTEM_NAME Generic)\nset(CMAKE_SYSTEM_PROCESSOR arm)')
        self.write("cmake/flags.cmake", 'set(CMAKE_C_FLAGS "-Wall")')
        self.write("cmake/comment.cmake", '# set(CMAKE_C_COMPILER gcc)\nmessage("set(CMAKE_C_COMPILER gcc)")')
        self.write("cmake/stm32cubemx/CMakeLists.txt", "add_library(stm32cubemx INTERFACE)")
        startup = self.write("Startup/startup_stm32f407.S")
        linker = self.write("ld/STM32F407_FLASH.ld")
        self.write("Startup/unrelated.S")
        self.write("Drivers/CMSIS/Templates/startup_other.s")
        self.write("Drivers/CMSIS/Templates/other.ld")
        self.write("build/toolchain.cmake", "set(CMAKE_C_COMPILER gcc)")
        result = scan_environment(self.root)
        self.assertEqual(result["toolchainFiles"], sorted([str(custom), str(hinted)]))
        self.assertEqual(result["cubemxDir"], str(self.root / "cmake/stm32cubemx"))
        self.assertEqual(result["startupFiles"], [str(startup)])
        self.assertEqual(result["linkerScripts"], [str(linker)])

    def test_workspace_uses_direct_conventional_children(self):
        self.write("apps/sensor/CMakeLists.txt", "project(sensor)")
        self.write("apps/sensor/src/main.c")
        self.write("apps/build/main.c")
        self.write("envs/chip/cmake/cross.cmake", "set(CMAKE_C_COMPILER arm-none-eabi-gcc)")
        result = scan_workspace(self.root)
        self.assertEqual([item["name"] for item in result["projects"]], ["sensor"])
        self.assertEqual([item["id"] for item in result["environments"]], ["chip"])

    def test_invalid_paths_have_clear_errors(self):
        for scan in (scan_project, scan_environment, scan_workspace):
            with self.assertRaisesRegex(ScanError, "Directory does not exist"):
                scan(self.root / "missing")
            with self.assertRaisesRegex(ScanError, "Expected a directory"):
                scan(self.write("file.txt"))

    def test_cli_produces_json_and_reports_invalid_path_without_traceback(self):
        scanner = Path(__file__).resolve().parents[1] / "scanner.py"
        ok = subprocess.run([sys.executable, str(scanner), str(self.root)], text=True, capture_output=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(json.loads(ok.stdout)["rootDir"], str(self.root))
        failed = subprocess.run([sys.executable, str(scanner), str(self.root / "missing")], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertIn("Directory does not exist", failed.stderr)
        self.assertNotIn("Traceback", failed.stderr)


if __name__ == "__main__":
    unittest.main()
