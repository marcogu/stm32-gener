#!/usr/bin/env python3
"""Generate an STM32 CMake application from a versioned JSON configuration."""

from __future__ import annotations

import argparse
import difflib
import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from config_model import ConfigError, normalize_config, read_config


def cmake_arg(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace(";", "\\;") + '"'


def path_arg(path: Path, output: Path) -> str:
    path = path.resolve()
    if path.is_relative_to(output):
        return '"${CMAKE_CURRENT_SOURCE_DIR}/' + cmake_arg(path.relative_to(output).as_posix())[1:]
    return cmake_arg(path.as_posix())


def env_path_arg(path: Path, root: Path, output: Path) -> str:
    """Reuse ENV_ROOT for its children, preserving paths outside the environment."""
    path, root = path.resolve(), root.resolve()
    if path == root:
        return '"${ENV_ROOT}"'
    if path.is_relative_to(root):
        return '"${ENV_ROOT}/' + cmake_arg(path.relative_to(root).as_posix())[1:]
    return path_arg(path, output)


def target_items(command: str, values: list[str], scope: str = "PRIVATE", target: str = "${PROJECT_NAME}") -> list[str]:
    if not values:
        return []
    return [f"{command}({target} {scope}".rstrip(), *[f"    {v}" for v in values], ")"]


def render(config: dict, config_dir: Path, output: Path) -> dict[Path, str]:
    """Render validated input without writing files or executing CMake."""
    output = output.resolve()
    project, env, gen = config["project"], config["environment"], config["generation"]
    tool, standard = env["toolchain"], project["compileStandard"]
    lines = [
        f"cmake_minimum_required(VERSION {tool['cmakeMinimumVersion']})",
        "include(env_cfg.cmake)",
        "if(NOT TARGET_ENV STREQUAL _GENERATED_TARGET_ENV)",
        '    message(FATAL_ERROR "Select the environment in the generator and regenerate into a fresh build directory.")',
        "endif()",
        'set(CMAKE_TOOLCHAIN_FILE "${ENV_TOOLCHAIN_FILE}")',
        "set(CMAKE_C_STANDARD_REQUIRED ON)",
        f"set(CMAKE_C_EXTENSIONS {'ON' if standard.startswith('gnu') else 'OFF'})",
        f"project({project['name']} VERSION {project['version']} LANGUAGES C ASM)",
        "add_executable(${PROJECT_NAME})",
        'add_subdirectory("${ENV_CUBEMX_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/stm32cubemx")', "",
    ]
    lines += target_items("target_sources", [path_arg(p, output) for p in project["sourceFiles"]])
    lines += target_items("target_include_directories", [path_arg(p, output) for p in project["includeDirs"]])
    lines += target_items("target_compile_definitions", [cmake_arg(v) for v in env["device"]["defines"] + project["compileDefinitions"]])
    lines += target_items("target_compile_options", [cmake_arg(v) for v in project["compileOptions"]])
    for name, profile in env["profiles"].items():
        for key, command in (("compileOptions", "target_compile_options"), ("compileDefinitions", "target_compile_definitions"), ("linkOptions", "target_link_options")):
            values = ['"$<$<CONFIG:' + name + '>:' + cmake_arg(v)[1:-1] + '>"' for v in profile[key]]
            lines += target_items(command, values)
    # CubeMX uses the plain link signature on the application target.
    links = []
    if any(lib["source"]["type"] == "git" for lib in config["libraries"]):
        lines.append("include(FetchContent)")
    for lib in config["libraries"]:
        src, name, target = lib["source"], lib["name"], lib["target"]
        lines += ["", f"# Library: {name}"]
        if src["type"] == "local-cmake":
            lines.append(f'add_subdirectory({path_arg(src["path"], output)} "${{CMAKE_CURRENT_BINARY_DIR}}/libraries/{name}")')
        elif src["type"] == "local-static":
            lines += [f"add_library({target} STATIC IMPORTED)", f"set_target_properties({target} PROPERTIES IMPORTED_LOCATION {path_arg(src['artifact'], output)})"]
        else:
            fetch = src["fetchContentName"]
            lines += [
                f"FetchContent_Declare({fetch}",
                f"    GIT_REPOSITORY {cmake_arg(src['repository'])}",
                f"    GIT_TAG {cmake_arg(src['ref'])}",
                f"    SOURCE_DIR {path_arg(src['checkoutDir'], output)}",
                f"    SOURCE_SUBDIR {cmake_arg(src['cmakeSubdir'].as_posix())}",
                f"    UPDATE_DISCONNECTED {'TRUE' if src['updateDisconnection'] else 'FALSE'}",
                ")", f"FetchContent_MakeAvailable({fetch})",
            ]
        lines += [f"if(NOT TARGET {target})", f'    message(FATAL_ERROR "Library {name} did not define target {target}.")', "endif()"]
        include_args = [path_arg(p, output) for p in lib["includeDirs"]]
        # Linked applications inherit the library's public include directories.
        lines += target_items("target_include_directories", include_args, scope="INTERFACE", target=target)
        for key, command in (("compileDefinitions", "target_compile_definitions"), ("compileOptions", "target_compile_options")):
            lines += target_items(command, [cmake_arg(v) for v in lib[key]], scope="INTERFACE", target=target)
        links += [cmake_arg(target), *[cmake_arg(v) for v in lib["linkLibraries"]]]
    links += [cmake_arg(v) for v in project["linkLibraries"]]
    lines += target_items("target_link_libraries", links, scope="")
    lines += ["", 'set_target_properties(${PROJECT_NAME} PROPERTIES SUFFIX ".elf")']
    commands = []
    if set(gen["outputFormats"]) & {"hex", "bin"}:
        lines.append(f"find_program(STM32_GENER_OBJCOPY NAMES {cmake_arg(gen['postBuildTools']['objcopy'])} REQUIRED)")
    for extension, fmt in (("hex", "ihex"), ("bin", "binary")):
        if extension in gen["outputFormats"]:
            commands.append(f'    COMMAND "${{STM32_GENER_OBJCOPY}}" -O {fmt} "$<TARGET_FILE:${{PROJECT_NAME}}>" "${{CMAKE_CURRENT_BINARY_DIR}}/${{PROJECT_NAME}}.{extension}"')
    if gen["sizeReport"]:
        lines.append(f"find_program(STM32_GENER_SIZE NAMES {cmake_arg(gen['postBuildTools']['size'])} REQUIRED)")
        commands.append('    COMMAND "${STM32_GENER_SIZE}" "$<TARGET_FILE:${PROJECT_NAME}>"')
    if commands:
        lines += ["add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD", *commands, '    COMMENT "Generating firmware outputs"', "    VERBATIM", ")"]
    env_lines = [
        f"set(_GENERATED_TARGET_ENV {cmake_arg(env['id'])})",
        f'set(TARGET_ENV {cmake_arg(env["id"])} CACHE STRING "Selected environment")',
        f"set(ENV_ROOT {path_arg(env['rootDir'], output)})",
        f"set(ENV_TOOLCHAIN_FILE {env_path_arg(tool['file'], env['rootDir'], output)})",
        f"set(ENV_CUBEMX_DIR {env_path_arg(env['cubemxDir'], env['rootDir'], output)})",
    ]
    if "binDir" in tool:
        env_lines += [
            "if(CMAKE_HOST_WIN32)", '    set(_PATH_SEPARATOR ";")', "else()", '    set(_PATH_SEPARATOR ":")', "endif()",
            f"set(_TOOL_BIN {env_path_arg(tool['binDir'], env['rootDir'], output)})",
            'set(ENV{PATH} "${_TOOL_BIN}${_PATH_SEPARATOR}$ENV{PATH}")',
        ]
    ignored = [gen["buildDir"].as_posix() + "/", "cmake-build-*/"]
    for lib in config["libraries"]:
        src = lib["source"]
        if src["type"] == "git" and src["checkoutDir"].is_relative_to(output):
            ignored.append(src["checkoutDir"].relative_to(output).as_posix() + "/")
    files = {
        Path("CMakeLists.txt"): "\n".join(lines) + "\n",
        Path("env_cfg.cmake"): "\n".join(env_lines) + "\n",
        Path(".gitignore"): "\n".join(ignored) + "\n",
    }
    if project["createEntry"]:
        files[project["entryFile"]] = "void app_main(void)\n{\n    /* The environment calls this application entry point. */\n}\n"
    if gen["buildScript"]:
        files[Path("build.sh")] = render_build_script(config)
    files[Path("README.md")] = render_readme(config)
    return files


def render_build_script(config: dict) -> str:
    gen, tool = config["generation"], config["environment"]["toolchain"]
    lines = [
        "#!/usr/bin/env bash", "set -euo pipefail",
        'ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)',
        'CONFIG=${1:-Debug}',
        'if [ "$#" -gt 1 ]; then echo "Usage: $0 [Debug|Release]" >&2; exit 2; fi',
        'case "$CONFIG" in Debug|Release) ;; *) echo "Expected Debug or Release" >&2; exit 2 ;; esac',
        f"BUILD_DIR={shlex.quote(gen['buildDir'].as_posix())}",
    ]
    if "binDir" in tool:
        lines += [f"TOOL_BIN={shlex.quote(str(tool['binDir']))}", 'export PATH="$TOOL_BIN:$PATH"']
    lines += [
        f'cmake -S "$ROOT" -B "$ROOT/$BUILD_DIR/$CONFIG" -G {shlex.quote(gen["generator"])} -DCMAKE_BUILD_TYPE="$CONFIG"',
        'cmake --build "$ROOT/$BUILD_DIR/$CONFIG" --parallel',
    ]
    return "\n".join(lines) + "\n"


def render_readme(config: dict) -> str:
    project, env, gen = config["project"], config["environment"], config["generation"]
    build_dir = gen["buildDir"].as_posix()
    lines = [
        f"# {project['name']}", "", f"Environment: {env['id']}.", "",
        "Requires CMake 3.22+, the selected CMake generator, and ARM GNU tools on PATH.",
        "Git dependencies require Git and repository access during configure.", "",
    ]
    if gen["buildScript"]:
        lines += ["On macOS/Linux:", "", "```sh", "./build.sh", "./build.sh Release", "```", ""]
    lines += [
        "Equivalent commands on Windows, macOS and Linux, from this directory:", "", "```sh",
        f'cmake -S . -B "{build_dir}/Debug" -G "{gen["generator"]}" -DCMAKE_BUILD_TYPE=Debug',
        f'cmake --build "{build_dir}/Debug" --parallel', "```", "",
        f"Outputs: {build_dir}/Debug/{project['name']} with " + ", ".join("." + ext for ext in gen["outputFormats"]) + ".", "",
        "External environment, library and application files are referenced in place; they are not copied.",
        "To change environments, select it in the configuration, regenerate and use a fresh build directory.",
        "The environment must provide main(), call app_main(), and configure startup and linking.", "",
    ]
    if config["libraries"]:
        lines += ["## Libraries", ""]
        for lib in config["libraries"]:
            src = lib["source"]
            detail = f"{src['repository']} @ {src['ref']}" if src["type"] == "git" else str(src.get("path", src.get("artifact")))
            lines.append(f"- {lib['name']} ({src['type']}): {detail}")
    return "\n".join(lines) + "\n"


def check_destinations(files: dict[Path, str], output: Path, directories: list[Path]) -> None:
    for relative in [*files, *directories]:
        if any(p != relative and p in relative.parents for p in files) or (relative in files and relative in directories):
            raise ConfigError(f"planned output paths collide: {relative}")
    for relative in [*files, *directories]:
        if relative.is_absolute() or ".." in relative.parts:
            raise ConfigError(f"invalid output path: {relative}")
        path = output
        for part in relative.parts:
            path /= part
            if path.is_symlink():
                raise ConfigError(f"refusing to write through symlink: {path}")
            if path.exists() and path != output / relative and not path.is_dir():
                raise ConfigError(f"output parent is not a directory: {path}")
        if relative in files and path.exists() and not path.is_file():
            raise ConfigError(f"output file is not a regular file: {path}")
        if relative in directories and path.exists() and not path.is_dir():
            raise ConfigError(f"output directory is not a directory: {path}")


def preview(files: dict[Path, str], output: Path) -> str:
    check_destinations(files, output, [])
    chunks = []
    for relative, content in files.items():
        path = output / relative
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old == content:
            chunks.append(f"unchanged: {relative}\n")
        else:
            chunks.append("".join(difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True), fromfile=str(relative) if path.exists() else "/dev/null", tofile=str(relative))))
    return "".join(chunks)


def write_files(files: dict[Path, str], output: Path, force: bool = False, *, policy: str = "prompt", directories: list[Path] | None = None) -> None:
    """Preflight every destination; stage content before replacing files."""
    output = output.resolve()
    directories = directories or []
    check_destinations(files, output, directories)
    policy = "overwrite" if force else policy
    changes = {p: text for p, text in files.items() if not (output / p).exists() or (output / p).read_text(encoding="utf-8") != text}
    conflicts = [p for p in changes if (output / p).exists()]
    if conflicts and policy == "prompt":
        raise ConfigError("existing files differ; preview with --dry-run and select --force, --conflict skip or backup: " + ", ".join(map(str, conflicts)))
    if policy == "skip":
        changes = {p: text for p, text in changes.items() if p not in conflicts}
    if policy == "backup":
        for p in conflicts:
            backup = output / (str(p) + ".bak")
            if backup.exists() or backup.is_symlink():
                raise ConfigError(f"backup already exists: {backup}")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stm32-gener-", dir=output) as staging:
        staged = Path(staging)
        for p, text in changes.items():
            path = staged / p
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            mode = stat.S_IMODE((output / p).stat().st_mode) if (output / p).exists() else 0o644
            path.chmod(mode | 0o111 if p == Path("build.sh") else mode)
        for p in directories:
            (output / p).mkdir(parents=True, exist_ok=True)
        for p in changes:
            destination = output / p
            destination.parent.mkdir(parents=True, exist_ok=True)
            if policy == "backup" and p in conflicts:
                shutil.copy2(destination, output / (str(p) + ".bak"))
            os.replace(staged / p, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, help="defaults to project.rootDir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--conflict", choices=["prompt", "skip", "overwrite", "backup"])
    parser.add_argument("--dry-run", action="store_true", help="show diffs without writing or fetching")
    args = parser.parse_args()
    try:
        config_path = args.config.expanduser().resolve()
        raw = read_config(config_path)
        project = raw.get("project", {})
        if args.output:
            output = args.output.expanduser().resolve()
        elif isinstance(project, dict) and isinstance(project.get("rootDir"), str):
            output = (config_path.parent / Path(project["rootDir"]).expanduser()).resolve()
        else:
            raise ConfigError("specify --output or project.rootDir")
        config = normalize_config(raw, config_path.parent, output)
        files = render(config, config_path.parent, output)
        if args.dry_run:
            check_destinations(files, output, config["project"]["createDirs"])
            print(preview(files, output), end="")
        else:
            write_files(files, output, args.force, policy=args.conflict or config["generation"]["conflictPolicy"], directories=config["project"]["createDirs"])
            print(f"Processed {len(files)} planned files in {output}")
        return 0
    except (ConfigError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
