#!/usr/bin/env python3
"""Read-only STM32 project discovery; CMake values are literal hints only."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path


SOURCE_SUFFIXES = {".c", ".s", ".S"}
HEADER_SUFFIXES = {".h", ".H", ".hpp", ".hxx", ".hh", ".inc"}
EXCLUDED_DIRECTORIES = {
    ".git", ".svn", ".hg", "__pycache__", "build", "cmakefiles", "_deps",
    "vendor", "vendors", "lib", "libs", "third_party", "third-party",
    "external", "node_modules", "output", "out", "dist",
}
BRACKET_START = re.compile(r"\[(=*)\[")
COMMAND_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.+-]*\Z")


class ScanError(ValueError):
    """A requested scan path cannot be inspected."""


def _directory(root: Path) -> Path:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        raise ScanError(f"Directory does not exist: {root}")
    if not root.is_dir():
        raise ScanError(f"Expected a directory: {root}")
    return root


def _excluded(name: str) -> bool:
    lower = name.lower()
    return lower in EXCLUDED_DIRECTORIES or lower.startswith("cmake-build-")


def _walk_files(root: Path) -> Iterator[Path]:
    def onerror(error: OSError) -> None:
        raise ScanError(f"Cannot scan directory: {error.filename}: {error.strerror}") from error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        current = Path(directory)
        dirs[:] = sorted(name for name in dirs
                         if not _excluded(name) and not (current / name).is_symlink())
        for name in sorted(files):
            path = current / name
            if not path.is_symlink() and path.is_file():
                yield path


def _source_roots(root: Path, source_dirs: list[str]) -> list[Path]:
    if not isinstance(source_dirs, list) or any(not isinstance(value, str) or not value for value in source_dirs):
        raise ScanError("source_dirs must be a list of non-empty directory paths")
    result = []
    for value in source_dirs:
        candidate = Path(os.path.abspath(root / Path(value).expanduser()))
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise ScanError(f"Source directory must be inside project root: {value}") from error
        current = root
        skip = False
        for part in relative.parts:
            current /= part
            if _excluded(part) or current.is_symlink():
                skip = True
                break
        if skip:
            continue
        if not candidate.is_dir():
            raise ScanError(f"Source directory does not exist or is not a directory: {candidate}")
        result.append(candidate)
    return result


def discover_sources(root: Path, source_dirs: list[str]) -> list[str]:
    """Find C/ASM files below explicit project directories, without following links.

    Paths are sorted, unique, POSIX-style paths relative to root. An empty list
    selects no directories; use ["."] for the entire project. Build and dependency
    directories are always excluded, even when explicitly selected.
    """
    root = _directory(root)
    sources = {
        path.relative_to(root).as_posix()
        for directory in _source_roots(root, source_dirs)
        for path in _walk_files(directory)
        if path.suffix in SOURCE_SUFFIXES
    }
    return sorted(sources)


def _read(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ScanError(f"Cannot read {path}: {error.strerror}") from error


def _tokens(text: str) -> Iterator[tuple[str, str]]:
    """Tokenize only enough CMake syntax to ignore comments and opaque arguments."""
    position = 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
            continue
        if char == "#":
            bracket = BRACKET_START.match(text, position + 1)
            if bracket:
                end = text.find("]" + bracket[1] + "]", bracket.end())
                position = len(text) if end < 0 else end + len(bracket[1]) + 2
            else:
                end = text.find("\n", position)
                position = len(text) if end < 0 else end + 1
            continue
        bracket = BRACKET_START.match(text, position)
        if bracket:
            end = text.find("]" + bracket[1] + "]", bracket.end())
            if end < 0:
                return
            value = text[bracket.end():end]
            yield "argument", value.removeprefix("\n")
            position = end + len(bracket[1]) + 2
            continue
        if char == '"':
            position += 1
            value = []
            while position < len(text) and text[position] != '"':
                if text[position] == "\\" and position + 1 < len(text):
                    # Escaped tokens are not interpreted as literal names.
                    value.extend(text[position:position + 2])
                    position += 2
                else:
                    value.append(text[position])
                    position += 1
            if position == len(text):
                return
            position += 1
            yield "argument", "".join(value)
            continue
        if char in "()":
            yield char, char
            position += 1
            continue
        start = position
        while position < len(text) and not text[position].isspace() and text[position] not in '()#"':
            if text[position] == "\\" and position + 1 < len(text):
                position += 2
            else:
                position += 1
        yield "bare", text[start:position]


def _commands(text: str) -> Iterator[tuple[str, list[str]]]:
    tokens = iter(_tokens(text))
    for kind, name in tokens:
        if kind != "bare" or not COMMAND_NAME.fullmatch(name):
            continue
        if next(tokens, None) != ("(", "("):
            continue
        depth = 1
        arguments = []
        for argument_kind, value in tokens:
            if argument_kind == "(":
                depth += 1
            elif argument_kind == ")":
                depth -= 1
                if depth == 0:
                    yield name.lower(), arguments
                    break
            arguments.append(value)


def _literal_identifier(value: str) -> str | None:
    return value if IDENTIFIER.fullmatch(value) else None


def _project_hints(cmake_text: str, env_text: str) -> tuple[str | None, str | None]:
    name = next((_literal_identifier(args[0]) for command, args in _commands(cmake_text)
                 if command == "project" and args), None)
    target_env = None
    for text in (env_text, cmake_text):
        for command, args in _commands(text):
            if command == "set" and len(args) >= 2 and args[0] == "TARGET_ENV":
                # CACHE/PARENT_SCOPE suffixes are supported; lists and expressions are not.
                if len(args) == 2 or args[2] in {"CACHE", "PARENT_SCOPE"}:
                    target_env = _literal_identifier(args[1])
                if target_env:
                    break
        if target_env:
            break
    return name, target_env


def _is_toolchain(path: Path) -> bool:
    assignments = {args[0]: args[1:] for command, args in _commands(_read(path))
                   if command == "set" and len(args) >= 2}
    if any(key in assignments for key in ("CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_ASM_COMPILER")):
        return True
    return "CMAKE_SYSTEM_NAME" in assignments and any(key in assignments for key in (
        "CMAKE_SYSTEM_PROCESSOR", "CMAKE_TRY_COMPILE_TARGET_TYPE", "CMAKE_FIND_ROOT_PATH"))


def scan_environment(root: Path) -> dict:
    """Find environment file candidates without running or evaluating CMake."""
    root = _directory(root)
    files = list(_walk_files(root))
    toolchains = [path for path in files if path.suffix.lower() == ".cmake" and _is_toolchain(path)]
    cubemx = root / "cmake" / "stm32cubemx"
    cubemx_valid = not (root / "cmake").is_symlink() and not cubemx.is_symlink() and (cubemx / "CMakeLists.txt") in files
    # Bundled CMSIS templates contain startup files for many unrelated devices.
    environment_files = [path for path in files if not any(
        part.lower() in {"drivers", "middlewares"} for part in path.relative_to(root).parts[:-1])]
    return {
        "id": root.name,
        "rootDir": str(root),
        "toolchainFiles": sorted(str(path) for path in toolchains),
        "cubemxDir": str(cubemx) if cubemx_valid else None,
        "startupFiles": sorted(str(path) for path in environment_files
                               if path.suffix in {".s", ".S"} and path.name.lower().startswith("startup")),
        "linkerScripts": sorted(str(path) for path in environment_files if path.suffix.lower() == ".ld"),
    }


def scan_project(root: Path, source_dirs: list[str] | None = None) -> dict:
    """Return filesystem candidates and literal CMake hints, not an imported build.

    source_dirs defaults to ["."]. Headers are discovered across the project even
    when source directories are narrowed, since they often live in a sibling inc/.
    """
    root = _directory(root)
    cmake = root / "CMakeLists.txt"
    name, target_env = _project_hints(_read(cmake), _read(root / "env_cfg.cmake"))
    files = list(_walk_files(root))
    headers = [path for path in files if path.suffix in HEADER_SUFFIXES]
    include_dirs = {path.parent.relative_to(root).as_posix() for path in headers}
    include_dirs.update(path.relative_to(root).as_posix() for path in root.iterdir()
                        if path.is_dir() and not path.is_symlink() and path.name.lower() in {"inc", "include"})
    return {
        "rootDir": str(root),
        "name": name or root.name,
        "targetEnv": target_env,
        "sourceFiles": discover_sources(root, ["."] if source_dirs is None else source_dirs),
        "headerFiles": sorted(path.relative_to(root).as_posix() for path in headers),
        "includeDirs": sorted(include_dirs),
        "hasCMake": cmake in files,
    }


def scan_workspace(root: Path) -> dict:
    """Inspect direct apps/ and envs/ children using the workspace convention."""
    root = _directory(root)

    def children(name: str) -> list[Path]:
        parent = root / name
        if parent.is_symlink() or not parent.is_dir():
            return []
        return sorted(path for path in parent.iterdir()
                      if path.is_dir() and not path.is_symlink() and not _excluded(path.name))

    return {
        "rootDir": str(root),
        "projects": [scan_project(path) for path in children("apps")],
        "environments": [scan_environment(path) for path in children("envs")],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--kind", choices=["environment", "project", "workspace"], default="project")
    parser.add_argument("--source-dir", action="append", help="Project source directory; repeat to select several (default: entire project)")
    args = parser.parse_args()
    if args.source_dir is not None and args.kind != "project":
        parser.error("--source-dir can only be used with --kind project")
    try:
        if args.kind == "project":
            result = scan_project(args.path, source_dirs=args.source_dir)
        elif args.kind == "environment":
            result = scan_environment(args.path)
        else:
            result = scan_workspace(args.path)
    except (ScanError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
