"""Read and save editable schema-v1 configurations without touching project files."""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any

from config_model import ConfigError, object_fields


def _text(value: Any, where: str) -> str:
    # Empty strings are intentional: users can save a partially completed form.
    if not isinstance(value, str) or any(c in value for c in "\x00\r\n"):
        raise ConfigError(f"{where}: expected a single-line string")
    return value


def _fields(value: Any, where: str, *, text: str = "", arrays: str = "",
            flags: str = "", objects: str = "", required: str = "") -> dict:
    value = object_fields(value, " ".join((text, arrays, flags, objects)), where)
    for key in required.split():
        if key not in value:
            raise ConfigError(f"{where}.{key}: required field is missing")
    for key in text.split():
        if key in value:
            _text(value[key], f"{where}.{key}")
    for key in arrays.split():
        if key in value:
            if not isinstance(value[key], list):
                raise ConfigError(f"{where}.{key}: expected an array")
            for index, item in enumerate(value[key]):
                _text(item, f"{where}.{key}[{index}]")
    for key in flags.split():
        if key in value and not isinstance(value[key], bool):
            raise ConfigError(f"{where}.{key}: expected true or false")
    return value


def validate_config_shape(value: Any) -> dict:
    """Validate serializable structure, leaving generation-time checks for later.

    In particular, referenced paths need not exist and required text may be empty.
    This keeps saved drafts and configurations from another machine editable.
    """
    data = object_fields(value, "schemaVersion workspace environment project libraries generation", "configuration")
    if type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise ConfigError("schemaVersion: expected 1; use the current config.example.json format")
    if "workspace" in data:
        _fields(data["workspace"], "workspace", text="rootDir configFile")
    env = _fields(data.get("environment"), "environment", text="id displayName rootDir cubemxDir",
                  objects="toolchain device profiles", required="id rootDir toolchain")
    _fields(env["toolchain"], "environment.toolchain",
            text="file binDir compilerPrefix cmakeGenerator cmakeMinimumVersion", required="file")
    if "device" in env:
        _fields(env["device"], "environment.device", text="part family core fpu floatAbi", arrays="defines")
    if "profiles" in env:
        profiles = object_fields(env["profiles"], "Debug Release", "environment.profiles")
        for name, profile in profiles.items():
            _fields(profile, f"environment.profiles.{name}", arrays="compileOptions compileDefinitions linkOptions")
    _fields(data.get("project"), "project", text="name target version rootDir entryFile compileStandard",
            arrays="sourceDirs includeDirs sourceFiles compileOptions compileDefinitions linkLibraries",
            flags="scanSources generateExampleMain", required="name")
    if "generation" in data:
        generation = _fields(data["generation"], "generation", text="buildDir generator conflictPolicy",
                             arrays="outputFormats", flags="buildScript sizeReport runConfigure runBuild",
                             objects="postBuildTools")
        if "postBuildTools" in generation:
            _fields(generation["postBuildTools"], "generation.postBuildTools", text="objcopy size")
    libraries = data.get("libraries", [])
    if not isinstance(libraries, list):
        raise ConfigError("libraries: expected an array")
    for index, library in enumerate(libraries):
        where = f"libraries[{index}]"
        _fields(library, where, text="name displayName target",
                arrays="includeDirs compileDefinitions compileOptions linkLibraries", objects="source", required="name source")
        source = library["source"]
        if not isinstance(source, dict) or source.get("type") not in ("local-cmake", "local-static", "git"):
            raise ConfigError(f"{where}.source.type: expected local-cmake, local-static or git")
        if source["type"] == "local-cmake":
            _fields(source, where + ".source", text="type path", required="path")
        elif source["type"] == "local-static":
            _fields(source, where + ".source", text="type artifact", arrays="includeDirs", objects="artifactByEnvironment")
            if "artifact" not in source and "artifactByEnvironment" not in source:
                raise ConfigError(f"{where}.source.artifact: required field is missing")
            if "artifactByEnvironment" in source:
                variants = source["artifactByEnvironment"]
                if not isinstance(variants, dict):
                    raise ConfigError(f"{where}.source.artifactByEnvironment: expected an object")
                for key, artifact in variants.items():
                    _text(artifact, f"{where}.source.artifactByEnvironment.{key}")
        else:
            _fields(source, where + ".source", text="type repository ref checkoutDir cmakeSubdir fetchContentName",
                    flags="updateDisconnection", required="repository ref")
    return data


def _absolute(value: str, base: Path) -> str:
    if not value:
        return value
    # Imported configurations may come from Windows. Keep those absolute paths
    # editable on POSIX instead of prefixing them with an unrelated directory.
    if os.name != "nt" and PureWindowsPath(value).is_absolute():
        return value
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else base / path).resolve())


def _resolve_config_paths(data: dict, base: Path) -> dict:
    """Only rebase paths whose base is the configuration file's directory."""
    data = copy.deepcopy(data)

    def paths(section: dict, *keys: str) -> None:
        for key in keys:
            if key in section:
                section[key] = _absolute(section[key], base)

    def path_array(section: dict, key: str) -> None:
        if key in section:
            section[key] = [_absolute(value, base) for value in section[key]]

    paths(data.get("workspace", {}), "rootDir")
    paths(data["environment"], "rootDir", "cubemxDir")
    paths(data["environment"]["toolchain"], "file", "binDir")
    paths(data["project"], "rootDir")
    for library in data.get("libraries", []):
        source = library["source"]
        if source["type"] == "git":
            repository = source["repository"]
            if repository and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*://|[^/\s]+@[^:]+:", repository):
                source["repository"] = _absolute(repository, base)
        else:
            paths(source, "path", "artifact")
            path_array(source, "includeDirs")
            path_array(library, "includeDirs")
            for key, value in source.get("artifactByEnvironment", {}).items():
                source["artifactByEnvironment"][key] = _absolute(value, base)
    post = data.get("generation", {}).get("postBuildTools", {})
    for key, value in post.items():
        if "/" in value or "\\" in value:
            post[key] = _absolute(value, base)
    return data


def import_config(path: Path) -> dict:
    path = path.expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"configuration: {exc}") from exc
    data = _resolve_config_paths(validate_config_shape(data), path.parent)
    project = data["project"]
    output = project.get("rootDir", _absolute(project["name"], path.parent))
    return {"config": data, "output": output}


def export_config(path: Path, data: Any, config_dir: Path, output: str) -> dict:
    path = path.expanduser().resolve()
    data = _resolve_config_paths(validate_config_shape(data), config_dir.expanduser().resolve())
    if "rootDir" not in data["project"]:
        data["project"]["rootDir"] = _absolute(_text(output, "output"), Path.cwd())
    temporary_path = None
    try:
        # Keep the prior configuration intact until the complete UTF-8 document
        # is flushed and closed. The same directory makes replacement atomic.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"configuration: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not hide the original write or replace failure.
                pass
    return {"path": str(path)}
