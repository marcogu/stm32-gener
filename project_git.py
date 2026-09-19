"""Create and commit the Git repository belonging to a generated project."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config_model import ConfigError


GIT_TIMEOUT_SECONDS = 60


def initialize_repository(output: Path) -> bool:
    """Initialize, stage and commit a generated project; return False if unchanged.

    Keep Git identity, hooks and signing under the user's normal Git configuration.
    Repository-routing environment variables must never redirect these operations
    to a repository from which the generator itself happened to be launched.
    """
    output = output.resolve()

    def fail(step: str, detail: str) -> ConfigError:
        return ConfigError(
            f"Git step '{step}' failed: {detail}\n"
            f"Generated files remain at {output}. Fix the Git problem and retry generation."
        )

    if not output.is_dir() or output == Path(output.anchor):
        raise fail("repository validation", "the output must be an existing project directory, not a filesystem root")

    git_dir = output / ".git"
    if git_dir.is_symlink() or (git_dir.exists() and not (git_dir.is_dir() or git_dir.is_file())):
        raise fail("repository validation", "output/.git must be a directory or a registered linked worktree file, not a symlink")
    if not git_dir.exists() and (output / "HEAD").is_file() and (output / "objects").is_dir():
        raise fail("repository validation", "the output appears to be a bare Git repository; select a project worktree directory")

    def verify_metadata(metadata: Path, linked: bool = False) -> None:
        if not linked and (metadata / "commondir").exists():
            raise fail("repository validation", "output/.git must not redirect to another repository's shared metadata")
        for name in ("config", "config.worktree", "HEAD", "index", "objects", "refs", "logs", "commondir"):
            if (metadata / name).is_symlink():
                raise fail("repository validation", f"Git metadata '{metadata / name}' must not be a symlink")

    if git_dir.is_dir():
        verify_metadata(git_dir)

    routing_variables = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        "GIT_IMPLICIT_WORK_TREE", "GIT_PREFIX",
    }
    env = {
        key: value for key, value in os.environ.items()
        if key not in routing_variables and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["GIT_TERMINAL_PROMPT"] = "0"

    def run(*args: str, allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        command = ["git", *args]
        step = " ".join(command)
        try:
            result = subprocess.run(
                command, cwd=output, env=env, capture_output=True,
                encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise fail(step, "Git was not found. Install Git and ensure it is available on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise fail(step, f"timed out after {GIT_TIMEOUT_SECONDS} seconds; check Git hooks and signing settings") from exc
        except OSError as exc:
            raise fail(step, str(exc)) from exc
        if result.returncode not in allowed:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
            identity_errors = (
                "identity unknown", "unable to auto-detect email", "empty ident name",
                "no email was given", "no name was given",
            )
            if any(message in detail.lower() for message in identity_errors):
                detail += "\nConfigure Git user.name and user.email with your own identity, then retry."
            raise fail(step, detail)
        return result

    def verify_root() -> None:
        root = run("rev-parse", "--show-toplevel").stdout.strip()
        metadata = Path(run("rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
        if Path(root).resolve() != output:
            raise fail("repository validation", "Git's worktree must belong to the output directory")
        if git_dir.is_file():
            # Linked worktrees have a backlink; merely checking --show-toplevel
            # would also accept a .git file pointing at an unrelated main index.
            try:
                backlink = Path((metadata / "gitdir").read_text(encoding="utf-8").strip())
            except (OSError, UnicodeError) as exc:
                raise fail("repository validation", "output/.git must reference a registered linked worktree; arbitrary external Git directories are not supported") from exc
            if backlink.resolve() != git_dir:
                raise fail("repository validation", "the linked worktree metadata belongs to another output directory")
            verify_metadata(metadata, linked=True)
        elif metadata != git_dir:
            raise fail("repository validation", "Git's metadata must belong to the output directory")
        else:
            verify_metadata(metadata)

    if git_dir.exists():
        verify_root()
    run("init")
    verify_root()
    run("add", ".")
    staged = run("diff", "--cached", "--quiet", "--exit-code", "--no-ext-diff", allowed=(0, 1))
    if staged.returncode == 0:
        return False
    run("commit", "-am", "created.")
    return True
