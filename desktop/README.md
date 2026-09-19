# STM CMake Forge

Cross-platform desktop wizard for generating STM32 CMake application projects.

The UI collects the project, environment, library source and output settings. It calls the validated Python core through a small JSON bridge. Local static archives, local CMake projects and Git/FetchContent dependencies are supported.

Use **Import config** and **Export config** in the header to load or save a
schema-v1 JSON configuration. Import restores the form and retains supported
advanced settings, including compile flags and toolchain options. Paths based on
the configuration file are resolved against its directory; project paths and Git
checkout paths retain their documented bases. Importing a configuration does not
generate project files.

Git / FetchContent entries include a **SOURCE_SUBDIR** field for repositories
whose `CMakeLists.txt` is in a subdirectory. Enter a repository-relative path such
as `components/driver`; leave it blank or use `.` for the repository root.

**Generate project** also initializes Git, stages all nonignored project files,
and commits with message `created.` before reporting success. Repeating generation
with no changes skips the commit. Install Git and configure your own `user.name`
and `user.email` first; Git failures leave the generated files available for retry.
Preview and configuration import/export do not initialize or commit Git repositories.

## Development

```sh
npm install
npm run tauri dev
```

The host must provide Python 3.10+, Git, CMake and the selected ARM GNU toolchain. The release bundle includes the Python bridge modules; it still expects Python to be available on the host until an embedded Python runtime is added.

## Verification

```sh
npm run build
npm run tauri build -- --debug
```
