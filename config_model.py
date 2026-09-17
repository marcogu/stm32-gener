"""Validated configuration shared by the CLI and future desktop UI."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from scanner import discover_sources


class ConfigError(ValueError):
    pass


def object_fields(value: Any, keys: str, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected an object")
    unknown = set(value) - set(keys.split())
    if unknown:
        raise ConfigError(f"{where}: unsupported fields: {', '.join(sorted(unknown))}")
    return value


def string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or any(c in value for c in "\x00\r\n"):
        raise ConfigError(f"{where}: expected a nonempty single-line string")
    return value


def strings(value: Any, where: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected an array")
    return [string(item, f"{where}[{i}]") for i, item in enumerate(value)]


def boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false")
    return value


def identifier(value: Any, where: str, target: bool = False) -> str:
    value = string(value, where)
    pattern = r"[A-Za-z_][A-Za-z0-9_.+-]*(?:::[A-Za-z_][A-Za-z0-9_.+-]*)*" if target else r"[A-Za-z_][A-Za-z0-9_-]*"
    if not re.fullmatch(pattern, value):
        raise ConfigError(f"{where}: invalid identifier: {value}")
    return value


def resolve(value: str, base: Path) -> Path:
    if ";" in value:
        raise ConfigError(f"paths containing ';' are not supported: {value}")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def local_path(value: Any, base: Path, where: str, kind: str | None = None) -> Path:
    path = resolve(string(value, where), base)
    if kind == "file" and not path.is_file():
        raise ConfigError(f"{where}: file does not exist: {path}")
    if kind == "dir" and not path.is_dir():
        raise ConfigError(f"{where}: directory does not exist: {path}")
    return path


def relative_path(value: Any, where: str) -> Path:
    value = string(value, where)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or ";" in value or re.match(r"^[A-Za-z]:", value):
        raise ConfigError(f"{where}: expected a relative path without '..'")
    return path


def read_config(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"configuration: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError("configuration: expected an object")
    return value


def normalize_config(data: dict, config_dir: Path, output: Path) -> dict:
    """Resolve paths and defaults without writing files or fetching dependencies."""
    data = copy.deepcopy(data)
    object_fields(data, "schemaVersion workspace environment project libraries generation", "configuration")
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise ConfigError("schemaVersion: expected 1; use the current config.example.json format")
    if "workspace" in data:
        workspace = object_fields(data["workspace"], "rootDir configFile", "workspace")
        for key in workspace:
            string(workspace[key], f"workspace.{key}")
    output = output.resolve()
    if output.exists() and not output.is_dir():
        raise ConfigError(f"output is not a directory: {output}")
    env = object_fields(data.get("environment"), "id displayName rootDir cubemxDir toolchain device profiles", "environment")
    env["id"] = identifier(env.get("id"), "environment.id")
    env["rootDir"] = local_path(env.get("rootDir"), config_dir, "environment.rootDir", "dir")
    env["cubemxDir"] = local_path(env.get("cubemxDir", str(env["rootDir"] / "cmake/stm32cubemx")), config_dir, "environment.cubemxDir", "dir")
    local_path("CMakeLists.txt", env["cubemxDir"], "environment.cubemxDir/CMakeLists.txt", "file")
    tool = object_fields(env.get("toolchain"), "file binDir compilerPrefix cmakeGenerator cmakeMinimumVersion", "environment.toolchain")
    tool["file"] = local_path(tool.get("file"), config_dir, "environment.toolchain.file", "file")
    if "binDir" in tool:
        tool["binDir"] = local_path(tool["binDir"], config_dir, "environment.toolchain.binDir", "dir")
    tool["compilerPrefix"] = string(tool.get("compilerPrefix", "arm-none-eabi-"), "environment.toolchain.compilerPrefix")
    minimum = string(tool.get("cmakeMinimumVersion", "3.22"), "environment.toolchain.cmakeMinimumVersion")
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", minimum) or tuple(map(int, minimum.split(".")[:2])) < (3, 22):
        raise ConfigError("environment.toolchain.cmakeMinimumVersion: expected 3.22 or newer")
    tool["cmakeMinimumVersion"] = minimum
    device = object_fields(env.get("device", {}), "part family core fpu floatAbi defines", "environment.device")
    for key in set(device) - {"defines"}:
        string(device[key], f"environment.device.{key}")
    device["defines"] = strings(device.get("defines", []), "environment.device.defines")
    env["device"] = device
    profiles = object_fields(env.get("profiles", {}), "Debug Release", "environment.profiles")
    for name, defaults in (("Debug", ["-Og", "-g3"]), ("Release", ["-Os", "-g0"])):
        profile = object_fields(profiles.get(name, {}), "compileOptions compileDefinitions linkOptions", f"environment.profiles.{name}")
        for key in ("compileOptions", "compileDefinitions", "linkOptions"):
            profile[key] = strings(profile.get(key, defaults if key == "compileOptions" else []), f"environment.profiles.{name}.{key}")
        profiles[name] = profile
    env["profiles"] = profiles

    gen = object_fields(data.get("generation", {}), "buildDir generator buildScript outputFormats sizeReport postBuildTools conflictPolicy runConfigure runBuild", "generation")
    gen["generator"] = gen.get("generator", tool.get("cmakeGenerator", "Ninja"))
    if gen["generator"] not in ("Ninja", "Unix Makefiles", "MinGW Makefiles"):
        raise ConfigError("generation.generator: expected Ninja, Unix Makefiles or MinGW Makefiles")
    gen["buildDir"] = relative_path(gen.get("buildDir", "build"), "generation.buildDir")
    if gen["buildDir"] == Path("."):
        raise ConfigError("generation.buildDir must be a subdirectory")
    for key, default in (("buildScript", True), ("sizeReport", True), ("runConfigure", False), ("runBuild", False)):
        gen[key] = boolean(gen.get(key, default), f"generation.{key}")
    if gen["runConfigure"] or gen["runBuild"]:
        raise ConfigError("generation: automatic build is not implemented; run the generated build.sh")
    gen["conflictPolicy"] = gen.get("conflictPolicy", "prompt")
    if gen["conflictPolicy"] not in ("prompt", "overwrite", "skip", "backup"):
        raise ConfigError("generation.conflictPolicy: expected prompt, overwrite, skip or backup")
    formats = strings(gen.get("outputFormats", ["elf", "hex", "bin"]), "generation.outputFormats")
    if "elf" not in formats or set(formats) - {"elf", "hex", "bin"} or len(formats) != len(set(formats)):
        raise ConfigError("generation.outputFormats: unique elf/hex/bin values, including elf, required")
    gen["outputFormats"] = formats
    post = object_fields(gen.get("postBuildTools", {}), "objcopy size", "generation.postBuildTools")
    for key in ("objcopy", "size"):
        value = string(post.get(key, tool["compilerPrefix"] + key), f"generation.postBuildTools.{key}")
        post[key] = str(resolve(value, config_dir)) if "/" in value or "\\" in value else value
    gen["postBuildTools"] = post
    data["generation"] = gen

    project = object_fields(data.get("project"), "name target version rootDir sourceDirs includeDirs sourceFiles scanSources entryFile generateExampleMain compileStandard compileOptions compileDefinitions linkLibraries", "project")
    project["name"] = identifier(project.get("name"), "project.name")
    project["target"] = identifier(project.get("target", project["name"]), "project.target")
    if project["name"] != project["target"]:
        raise ConfigError("project.target must equal project.name for the CubeMX environment template")
    project["version"] = string(project.get("version", "1.0.0"), "project.version")
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", project["version"]):
        raise ConfigError("project.version: expected a numeric CMake version")
    project_root = local_path(project.get("rootDir", str(output)), config_dir, "project.rootDir")
    if project_root != output and not project_root.is_dir():
        raise ConfigError(f"project.rootDir: directory does not exist: {project_root}")
    project["rootDir"] = project_root
    project["scanSources"] = boolean(project.get("scanSources", True), "project.scanSources")
    project["generateExampleMain"] = boolean(project.get("generateExampleMain", True), "project.generateExampleMain")
    source_dirs = strings(project.get("sourceDirs", ["src"]), "project.sourceDirs")
    def project_directory(value: str, where: str) -> tuple[Path, Path]:
        path = local_path(value, project_root, where)
        # A separate output directory is commonly used for a new project while
        # the application root points at an existing workspace. In that case,
        # missing relative source/include folders belong to the output tree.
        if path.exists() or project_root == output or Path(value).is_absolute() or ".." in Path(value).parts:
            return path, project_root
        output_path = local_path(value, output, where)
        return (output_path, output) if output_path.is_relative_to(output) else (path, project_root)

    source_info = [project_directory(value, "project.sourceDirs") for value in source_dirs]
    source_directories = [path for path, _ in source_info]
    sources = [local_path(value, project_root, "project.sourceFiles") for value in strings(project.get("sourceFiles", []), "project.sourceFiles")]
    if project["scanSources"]:
        for value, (directory, scan_root) in zip(source_dirs, source_info):
            if not directory.is_dir():
                if directory.is_relative_to(output):
                    continue
                raise ConfigError(f"project.sourceDirs: directory does not exist: {directory}")
            try:
                sources.extend(resolve(value, scan_root) for value in discover_sources(scan_root, [str(directory)]))
            except ValueError as exc:
                raise ConfigError(f"project.sourceDirs: {exc}") from exc
    default_entry = "src/app_main.c"
    first_source_dir = Path(source_dirs[0]) if source_dirs else None
    if first_source_dir and not first_source_dir.is_absolute() and ".." not in first_source_dir.parts and not re.match(r"^[A-Za-z]:", source_dirs[0]):
        default_entry = (first_source_dir / "app_main.c").as_posix()
    entry = relative_path(project.get("entryFile", default_entry), "project.entryFile")
    if entry.suffix != ".c":
        raise ConfigError("project.entryFile: expected a .c application source file")
    project["entryFile"] = entry
    entry_path = (output / entry).resolve()
    can_create_entry = project_root == output or any(directory.is_relative_to(output) for directory in source_directories)
    create_entry = can_create_entry and project["generateExampleMain"] and not entry_path.exists() and (not sources or entry_path in sources)
    if create_entry and not entry_path.is_relative_to(output):
        raise ConfigError("project.entryFile: resolves outside output")
    if not sources and create_entry:
        sources.append(entry_path)
    for path in sources:
        if not path.is_file() and not (create_entry and path == entry_path):
            raise ConfigError(f"project.sourceFiles: file does not exist: {path}")
        if path.suffix not in {".c", ".s", ".S"}:
            raise ConfigError(f"project.sourceFiles: unsupported source extension: {path}")
    if not sources:
        raise ConfigError("project.sourceFiles: no application sources found")
    project["sourceFiles"] = sorted(set(sources))
    project["createEntry"] = create_entry
    include_info = [project_directory(value, "project.includeDirs") for value in strings(project.get("includeDirs", ["inc"]), "project.includeDirs")]
    include_dirs = [path for path, _ in include_info]
    planned_files = [output / p for p in ("CMakeLists.txt", "env_cfg.cmake", "README.md", ".gitignore", "build.sh")]
    if create_entry:
        planned_files.append(entry_path)
    for directory in source_directories + include_dirs + [output / gen["buildDir"]]:
        if any(directory == p or directory.is_relative_to(p) for p in planned_files):
            raise ConfigError(f"project/generation directory conflicts with a generated file: {directory}")
    if (output / gen["buildDir"]).is_relative_to(entry_path) or entry_path.is_relative_to(output / gen["buildDir"]):
        raise ConfigError("project.entryFile overlaps generation.buildDir")
    create_dirs = []
    for path in source_directories + include_dirs:
        if not path.is_dir():
            if path.is_relative_to(output) and not path.exists():
                relative = path.relative_to(output)
                if relative not in create_dirs:
                    create_dirs.append(relative)
            else:
                field = "project.sourceDirs" if path in source_directories else "project.includeDirs"
                raise ConfigError(f"{field}: directory does not exist: {path}")
    project["includeDirs"] = include_dirs
    project["createDirs"] = create_dirs
    project["compileStandard"] = project.get("compileStandard", "gnu11")
    if project["compileStandard"] not in ("c11", "gnu11", "c17", "gnu17"):
        raise ConfigError("project.compileStandard: expected c11, gnu11, c17 or gnu17")
    for key in ("compileOptions", "compileDefinitions", "linkLibraries"):
        project[key] = strings(project.get(key, ["-fstack-usage", "-MMD", "-MP"] if key == "compileOptions" else []), f"project.{key}")

    libraries = data.get("libraries", [])
    if not isinstance(libraries, list):
        raise ConfigError("libraries: expected an array")
    targets = {project["name"], "stm32cubemx", "STM32_Drivers"}
    names: set[str] = set()
    fetch_names: set[str] = set()
    checkouts: list[Path] = []
    for i, lib in enumerate(libraries):
        where = f"libraries[{i}]"
        object_fields(lib, "name displayName source target includeDirs compileDefinitions compileOptions linkLibraries", where)
        lib["name"] = identifier(lib.get("name"), where + ".name")
        lib["target"] = identifier(lib.get("target", lib["name"]), where + ".target", target=True)
        if lib["name"] in names or lib["target"] in targets:
            raise ConfigError(f"{where}: duplicate or reserved library name/target")
        names.add(lib["name"])
        targets.add(lib["target"])
        src = lib.get("source")
        if not isinstance(src, dict) or src.get("type") not in ("local-cmake", "local-static", "git"):
            raise ConfigError(f"{where}.source.type: expected local-cmake, local-static or git")
        extra_includes = strings(lib.get("includeDirs", []), where + ".includeDirs")
        if src["type"] == "local-cmake":
            object_fields(src, "type path", where + ".source")
            src["path"] = local_path(src.get("path"), config_dir, where + ".source.path", "dir")
            local_path("CMakeLists.txt", src["path"], where + ".source.path/CMakeLists.txt", "file")
        elif src["type"] == "local-static":
            object_fields(src, "type artifact includeDirs artifactByEnvironment", where + ".source")
            variants = src.get("artifactByEnvironment", {})
            if not isinstance(variants, dict):
                raise ConfigError(f"{where}.source.artifactByEnvironment: expected an object")
            for key, value in variants.items():
                string(value, where + ".source.artifactByEnvironment." + key)
            src["artifact"] = local_path(variants.get(env["id"], src.get("artifact")), config_dir, where + ".source.artifact", "file")
            extra_includes += strings(src.get("includeDirs", []), where + ".source.includeDirs")
        else:
            object_fields(src, "type repository ref checkoutDir cmakeSubdir fetchContentName updateDisconnection", where + ".source")
            src["repository"] = string(src.get("repository"), where + ".source.repository")
            if src["repository"].startswith("-"):
                raise ConfigError(f"{where}.source.repository: invalid repository")
            if not re.match(r"[A-Za-z][A-Za-z0-9+.-]*://|[^/\s]+@[^:]+:", src["repository"]):
                src["repository"] = str(local_path(src["repository"], config_dir, where + ".source.repository", "dir"))
            src["ref"] = string(src.get("ref"), where + ".source.ref")
            src["checkoutDir"] = local_path(src.get("checkoutDir", f"libs/{lib['name']}"), output, where + ".source.checkoutDir")
            checkout = src["checkoutDir"]
            if any(checkout == p or checkout.is_relative_to(p) or p.is_relative_to(checkout) for p in checkouts):
                raise ConfigError(f"{where}.source.checkoutDir: overlapping library checkouts")
            if checkout == output or output.is_relative_to(checkout) or checkout == env["rootDir"] or checkout == project_root:
                raise ConfigError(f"{where}.source.checkoutDir: overlaps an input or output root")
            protected = [env["rootDir"], *sources, *include_dirs, *planned_files]
            if project_root != output:
                protected.append(project_root)
            for other in libraries:
                other_source = other.get("source", {}) if isinstance(other, dict) else {}
                for key in ("path", "artifact"):
                    value = other_source.get(key)
                    if value:
                        protected.append(resolve(str(value), config_dir))
            if any(checkout == p or checkout.is_relative_to(p) or p.is_relative_to(checkout) for p in protected):
                raise ConfigError(f"{where}.source.checkoutDir: overlaps application, environment or local library files")
            if checkout.exists() and (not checkout.is_dir() or (any(checkout.iterdir()) and not (checkout / ".git").exists())):
                raise ConfigError(f"{where}.source.checkoutDir: expected an empty directory or a Git checkout")
            checkouts.append(checkout)
            src["cmakeSubdir"] = relative_path(src.get("cmakeSubdir", "."), where + ".source.cmakeSubdir")
            src["fetchContentName"] = identifier(src.get("fetchContentName", lib["name"]), where + ".source.fetchContentName")
            fetch_key = src["fetchContentName"].lower()
            if fetch_key in fetch_names:
                raise ConfigError(f"{where}.source.fetchContentName: duplicate (case insensitive)")
            fetch_names.add(fetch_key)
            src["updateDisconnection"] = boolean(src.get("updateDisconnection", False), where + ".source.updateDisconnection")
        base = src["checkoutDir"] if src["type"] == "git" else config_dir
        # Both include fields are supported; resolve aliases before deduplicating
        # and preserve the declared header search order.
        lib["includeDirs"] = list(dict.fromkeys(
            local_path(value, base, where + ".includeDirs", None if src["type"] == "git" else "dir")
            for value in extra_includes
        ))
        for key in ("compileOptions", "compileDefinitions", "linkLibraries"):
            lib[key] = strings(lib.get(key, []), where + "." + key)
    data["libraries"] = libraries
    return data
