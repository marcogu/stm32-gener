# STM CMake Forge

STM CMake Forge is a cross-platform desktop wizard for generating STM32 CMake application projects. It scans existing STM32 environment projects and applications, lets each library use a local CMake/static source or Git/FetchContent, previews the generated files, and writes a `CMakeLists.txt` plus `env_cfg.cmake`, `build.sh`, README and source scaffolding.

The validated core is available from the command line:

```sh
python3 generator.py config.example.json --output ./generated-app --dry-run
python3 generator.py config.example.json --output ./generated-app --force
cd generated-app && ./build.sh Debug
```

The scanner can inspect a project, environment or conventional workspace:

```sh
python3 scanner.py /path/to/apps/demo --kind project
python3 scanner.py /path/to/envs/stm32f407vet6 --kind environment
python3 scanner.py /path/to/embed-workspace --kind workspace
```

The desktop UI is in [`desktop/`](/Users/penpenqie/Documents/workspace/codeing/ai/stm32-gener/desktop):

```sh
cd desktop
npm install
npm run tauri dev
```

The host needs Python 3.10+, Git, CMake, the selected generator, and ARM GNU tools. Git dependencies are fetched by CMake during configure. The first desktop release bundles the Python bridge modules, while Python remains a host prerequisite.

After writing project files, both the desktop generator and CLI run `git init`,
`git add .`, and `git commit -am "created."` in the output directory. Git must
have `user.name` and `user.email` configured with your identity. Existing project
repositories are reused; unchanged regeneration does not create an empty commit.
All nonignored files in the output directory are included. The generated
`.gitignore` excludes OS/IDE files, build artifacts, the configured build directory,
and fetched Git dependencies. Preview (`--dry-run`) does not modify Git.
If a Git step fails, generated files are retained and the error identifies the
failed step; fix the Git configuration and generate again.

Use **Export config** to save the current settings as a schema-v1 JSON file and
**Import config** to restore them later. Imported configurations retain supported
settings that are not exposed by the form, and config-relative paths are resolved
against the selected JSON file. The same configuration format works with the CLI;
see [`CONFIG_SCHEMA.md`](CONFIG_SCHEMA.md) for all fields and path rules.

For Git dependencies whose `CMakeLists.txt` is below the repository root, set
**SOURCE_SUBDIR** to that relative directory, such as `components/driver`.
Leaving it blank uses the repository root (`.`).

Verification:

```sh
python3 -m unittest discover -s tests -v
cd desktop && npm run build
cd desktop && npm run tauri build -- --debug --bundles app
```

## Windows Installers With GitHub Actions

The workflow in [`.github/workflows/windows-build.yml`](.github/workflows/windows-build.yml)
builds x64 EXE (NSIS) and MSI installers on a GitHub-hosted Windows runner.

1. Commit and push the workflow and accompanying changes to the repository's default branch (`main`).
2. Open the repository on GitHub, select **Actions**, then **Build Windows Installers**.
3. Select **Run workflow**, choose `main`, and confirm **Run workflow**.
4. Open the new run and its **Windows x64** job to watch each step's logs.
5. After a successful run, return to its summary and download an installer under **Artifacts**.
   Extract the downloaded ZIP on Windows, then run the EXE or MSI. Either format installs the same app.

`workflow_dispatch` enables the manual button; a push alone does not start this workflow.
`runs-on` selects the build machine. `uses` calls a reusable Action, while `run`
executes a command. `npm ci` and Cargo's `--locked` use the committed dependency
lockfiles. The two upload steps retain the installers for 14 days and fail if
their expected files are missing. A failed run's first failing step contains the
relevant error; after fixing and pushing code, start a new run to build the new commit.

Checkout uses GitHub's automatic token with read-only repository access. No SSH
private key or custom secret is needed. Builds run on GitHub independently of
the Mac's VPN, Docker, or local compiler setup. Private-repository Actions usage
is subject to the account's plan and billing limits.

These installers are unsigned and do not embed Python. The installed app still
requires Python 3.10+ available as `python` on Windows. Building the generated
STM32 projects additionally requires CMake and the selected ARM toolchain.
Installing Python in the CI job only equips the build machine.

References: [Tauri GitHub pipeline](https://v2.tauri.app/distribute/pipelines/github/),
[GitHub manual workflow runs](https://docs.github.com/en/actions/how-tos/managing-workflow-runs/manually-running-a-workflow).
