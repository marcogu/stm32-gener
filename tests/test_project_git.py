import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config_model import ConfigError
from project_git import initialize_repository


@unittest.skipUnless(shutil.which("git"), "requires Git")
class ProjectGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stm32 git ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.out = self.root / "中文 project"
        self.out.mkdir()
        self.file = self.out / "main.c"
        self.file.write_text("void app_main(void) {}\n", encoding="utf-8")
        self.home = self.root / "home"
        self.home.mkdir()
        hooks = self.home / "empty hooks"
        hooks.mkdir()
        self.gitconfig = self.home / ".gitconfig"
        self.gitconfig.write_text(
            '[user]\n name = Fixture\n email = fixture@example.invalid\n'
            '[commit]\n gpgSign = false\n'
            f'[core]\n hooksPath = "{hooks.as_posix()}"\n', encoding="utf-8",
        )
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), XDG_CONFIG_HOME=str(self.home), GIT_CONFIG_NOSYSTEM="1")
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", *args], cwd=cwd or self.out, capture_output=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def test_new_repository_commits_files_and_respects_ignores(self):
        (self.out / ".gitignore").write_text("*.o\n", encoding="utf-8")
        (self.out / "main.o").write_text("ignored", encoding="utf-8")
        self.assertTrue(initialize_repository(self.out))
        self.assertEqual(self.git("log", "-1", "--format=%s"), "created.")
        self.assertEqual(self.git("ls-files").splitlines(), [".gitignore", "main.c"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_clean_rerun_skips_commit_and_changed_rerun_commits(self):
        self.assertTrue(initialize_repository(self.out))
        head = self.git("rev-parse", "HEAD")
        self.assertFalse(initialize_repository(self.out))
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.file.write_text("void app_main(void) { /* changed */ }\n", encoding="utf-8")
        self.assertTrue(initialize_repository(self.out))
        self.assertEqual(self.git("rev-list", "--count", "HEAD"), "2")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_nested_output_is_isolated_from_parent_repository(self):
        (self.root / "parent.txt").write_text("parent", encoding="utf-8")
        self.git("init", cwd=self.root)
        self.git("add", "parent.txt", cwd=self.root)
        self.git("commit", "-m", "parent", cwd=self.root)
        parent_head = self.git("rev-parse", "HEAD", cwd=self.root)
        self.assertTrue(initialize_repository(self.out))
        self.assertEqual(Path(self.git("rev-parse", "--show-toplevel")), self.out)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=self.root), parent_head)
        self.assertEqual(self.git("ls-files", cwd=self.root), "parent.txt")

    def test_environment_cannot_redirect_repository_or_index(self):
        other = self.root / "other repository"
        other.mkdir()
        self.git("init", cwd=other)
        index = self.root / "redirected-index"
        env = {
            "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other),
            "GIT_INDEX_FILE": str(index), "GIT_COMMON_DIR": str(other / ".git"),
            "GIT_OBJECT_DIRECTORY": str(other / ".git/objects"),
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(other),
        }
        with patch.dict(os.environ, env):
            self.assertTrue(initialize_repository(self.out))
        self.assertEqual(self.git("ls-files"), "main.c")
        self.assertEqual(self.git("ls-files", cwd=other), "")
        self.assertFalse(index.exists())

    def test_missing_identity_leaves_staged_files_and_can_be_retried(self):
        self.gitconfig.write_text("[user]\n useConfigOnly = true\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "git commit -am created") as caught:
            initialize_repository(self.out)
        self.assertIn("user.name", str(caught.exception))
        self.assertIn("user.email", str(caught.exception))
        self.assertIn("Generated files remain", str(caught.exception))
        self.assertTrue(self.file.exists())
        self.assertEqual(self.git("ls-files"), "main.c")
        self.git("config", "user.name", "Local Fixture")
        self.git("config", "user.email", "local@example.invalid")
        self.assertTrue(initialize_repository(self.out))
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>"), "Local Fixture <local@example.invalid>")

    def test_missing_git_stops_before_staging(self):
        with patch("project_git.subprocess.run", side_effect=FileNotFoundError) as run:
            with self.assertRaisesRegex(ConfigError, "Git was not found"):
                initialize_repository(self.out)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["git", "init"])
        self.assertTrue(self.file.exists())

    def test_init_failure_and_timeout_stop_following_steps(self):
        failures = (
            subprocess.CompletedProcess(["git", "init"], 128, "", "cannot initialize repository"),
            subprocess.TimeoutExpired(["git", "init"], 60),
        )
        for failure in failures:
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with self.subTest(failure=failure), patch("project_git.subprocess.run", **kwargs) as run:
                with self.assertRaisesRegex(ConfigError, "git init"):
                    initialize_repository(self.out)
                self.assertEqual(run.call_count, 1)
                self.assertTrue(self.file.exists())

    def test_add_failure_prevents_commit(self):
        run_git = subprocess.run
        seen = []

        def run(command, **kwargs):
            seen.append(command)
            if command == ["git", "add", "."]:
                return subprocess.CompletedProcess(command, 128, "", "index.lock already exists")
            return run_git(command, **kwargs)

        with patch("project_git.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(ConfigError, "git add"):
                initialize_repository(self.out)
        self.assertFalse(any(command[1] == "commit" for command in seen))
        self.assertTrue(self.file.exists())

    def test_repository_with_external_worktree_is_rejected_before_staging(self):
        self.git("init")
        external = self.root / "external"
        external.mkdir()
        self.git("config", "core.worktree", str(external))
        with self.assertRaisesRegex(ConfigError, "must belong to the output"):
            initialize_repository(self.out)
        self.assertFalse((self.out / ".git/index").exists())

    @unittest.skipIf(os.name == "nt", "symlink permissions differ on Windows")
    def test_git_symlink_is_rejected_without_changing_other_repository(self):
        other = self.root / "other"
        other.mkdir()
        self.git("init", cwd=other)
        (self.out / ".git").symlink_to(other / ".git", target_is_directory=True)
        with self.assertRaisesRegex(ConfigError, "repository validation"):
            initialize_repository(self.out)
        self.assertFalse((other / ".git/index").exists())

    def test_linked_worktree_commits_only_its_branch(self):
        self.assertTrue(initialize_repository(self.out))
        head = self.git("rev-parse", "HEAD")
        worktree = self.root / "linked worktree"
        self.git("worktree", "add", "-b", "linked", str(worktree))
        (worktree / "main.c").write_text("void app_main(void) { /* worktree */ }\n", encoding="utf-8")
        self.assertTrue(initialize_repository(worktree))
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertNotEqual(self.git("rev-parse", "HEAD", cwd=worktree), head)
        self.assertFalse(initialize_repository(worktree))

    def test_forged_git_file_cannot_stage_another_repository(self):
        other = self.root / "other"
        other.mkdir()
        self.git("init", cwd=other)
        (self.out / ".git").write_text(f"gitdir: {other / '.git'}\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "registered linked worktree"):
            initialize_repository(self.out)
        self.assertFalse((other / ".git/index").exists())

    def test_bare_repository_is_rejected(self):
        self.git("init", "--bare")
        with self.assertRaisesRegex(ConfigError, "bare Git repository"):
            initialize_repository(self.out)


if __name__ == "__main__":
    unittest.main()
