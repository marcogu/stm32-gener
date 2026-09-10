# Configuration format (schemaVersion 1)

The CLI and future desktop UI use one JSON configuration. `config_model.py`
validates the fields below, resolves paths, and supplies the defaults shown here.
The model describes references to existing files; it does not copy an environment
or a library into the generated application. For a new project whose root is the
output directory, missing `sourceDirs` and `includeDirs` are created during
generation.

## Path rules

- The configuration file's directory is the base for relative paths in
  `workspace.rootDir`, `environment.rootDir`, `environment.cubemxDir`, toolchain
  fields, and local library source fields. The `workspace.rootDir` value itself
  is metadata and does not change this base directory.
- `project.rootDir` is relative to the configuration file. If omitted, it is the
  output directory (`--output`, or the value selected by the CLI). Project source,
  include, and entry paths are relative to `project.rootDir`.
- When `project.rootDir` points to an existing application and a relative
  `sourceDirs` or `includeDirs` entry is missing there, the missing directory is
  created under the output directory instead. This supports generating a new
  source tree while reusing an existing application root.
- A Git library's `source.checkoutDir` is relative to the generated output
  directory. Its `includeDirs` are relative to that checkout directory.
- `generation.buildDir` and all generated paths are relative to the output
  directory. `generation.buildDir` must be a subdirectory and cannot be `.`.
- Absolute paths are accepted where a normal path field is used. Paths containing
  `..` are rejected for `project.entryFile`, `source.cmakeSubdir`, and
  `generation.buildDir`. Paths containing `;` are rejected.

## Top-level fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schemaVersion` | integer `1` | yes | Configuration format version. |
| `workspace` | object | no | Optional metadata: `rootDir`, `configFile`. |
| `environment` | object | yes | Existing STM32 environment and toolchain. |
| `project` | object | yes | Application name, source files, and compile settings. |
| `libraries` | array | no | Local CMake, local static, or Git libraries; default `[]`. |
| `generation` | object | no | Output and conflict settings; all fields have defaults. |

Unknown fields are rejected, so a saved configuration cannot silently claim
support for a feature that the generator does not implement.

## `workspace`

| Field | Type | Default |
| --- | --- | --- |
| `rootDir` | path string | none |
| `configFile` | string | none |

These values are metadata only. Path resolution always uses the directory
containing the JSON file.

## `environment`

| Field | Type | Default |
| --- | --- | --- |
| `id` | identifier | required |
| `displayName` | string | none |
| `rootDir` | existing directory path | required |
| `cubemxDir` | existing directory path | `rootDir/cmake/stm32cubemx` |
| `toolchain` | object | required |
| `device` | object | `{}` |
| `profiles` | object | Debug/Release defaults |

`toolchain` fields:

| Field | Type | Default |
| --- | --- | --- |
| `file` | existing file path | required |
| `binDir` | existing directory path | none |
| `compilerPrefix` | string | `arm-none-eabi-` |
| `cmakeGenerator` | string | `Ninja` |
| `cmakeMinimumVersion` | `major.minor[.patch]`, at least 3.22 | `3.22` |

`device` accepts `part`, `family`, `core`, `fpu`, `floatAbi` strings and
`defines` (string array, default `[]`). `profiles` may contain `Debug` and/or
`Release`; each profile has `compileOptions`, `compileDefinitions`, and
`linkOptions` arrays. Defaults are `-Og -g3` for Debug compile options and
`-Os -g0` for Release compile options; other profile arrays default to `[]`.

The environment's `cubemxDir/CMakeLists.txt` and toolchain file must exist. The
scanner can suggest startup and linker candidates, but those suggestions are not
configuration fields in this version.

## `project`

| Field | Type | Default |
| --- | --- | --- |
| `name` | identifier | required |
| `target` | identifier | `name`; must equal `name` |
| `version` | numeric version | `1.0.0` |
| `rootDir` | existing directory path, or output when omitted | output |
| `sourceDirs` | string array | `["src"]` |
| `includeDirs` | directory paths resolved relative to project root | `["inc"]` |
| `sourceFiles` | file paths resolved relative to project root | `[]` |
| `scanSources` | boolean | `true` |
| `entryFile` | relative `.c` path | `<first sourceDir>/app_main.c` (normally `src/app_main.c`) |
| `generateExampleMain` | boolean | `true` |
| `compileStandard` | `c11`, `gnu11`, `c17`, or `gnu17` | `gnu11` |
| `compileOptions` | string array | `[-fstack-usage, -MMD, -MP]` |
| `compileDefinitions` | string array | `[]` |
| `linkLibraries` | string array | `[]` |

When `scanSources` is true, the scanner finds `.c`, `.s`, and `.S` files below
`sourceDirs`, excluding build, dependency, output, and symlink directories.
Explicit `sourceFiles` are added to the scan result. If the project root equals
the output directory, `generateExampleMain` can create `entryFile` when no source
file exists there. Existing environment, project, and library files remain in
place; generation never copies them.

`compileStandard` controls GNU extensions through `CMAKE_C_EXTENSIONS`. The
generated template does not set `CMAKE_C_STANDARD`; the C standard version is
inherited from the environment/toolchain or the compiler default.

The application target must have the same name as `project.name` because the
CubeMX environment template relies on that project name.

## `libraries`

Each item has these common fields:

| Field | Type | Default |
| --- | --- | --- |
| `name` | identifier | required |
| `displayName` | string | none |
| `source` | source object | required |
| `target` | CMake target identifier | `name` |
| `includeDirs` | path string array | `[]` |
| `compileDefinitions` | string array | `[]` |
| `compileOptions` | string array | `[]` |
| `linkLibraries` | string array | `[]` |

Source variants:

| `source.type` | Required fields | Optional fields |
| --- | --- | --- |
| `local-cmake` | `path` (directory containing `CMakeLists.txt`) | none |
| `local-static` | `artifact` (file), unless the selected environment is supplied by `artifactByEnvironment` | `includeDirs` (directories), `artifactByEnvironment` map of environment id to artifact path |
| `git` | `repository`, `ref`, `checkoutDir` | `cmakeSubdir`, `fetchContentName`, `updateDisconnection` |

For Git sources, `cmakeSubdir` defaults to `.`, `fetchContentName` to the library
name, and `updateDisconnection` to `false`; `repository`, `ref`, and
`checkoutDir` must be supplied.
`repository` may be a URL, SCP-style Git address, or existing local directory.
Git entries are rendered with CMake `FetchContent`; the generator does not run
Git or access the network during generation. Fetching occurs later when CMake
configures the generated project.

Library include directories are added to the application as `PRIVATE` includes.
The same include directories and library compile flags are attached to the
library target with `INTERFACE` scope. This is the current model for imported
static targets and source libraries; explicit `PUBLIC`/`PRIVATE` flag scope is
planned for a later schema revision.

## `generation`

| Field | Type | Default |
| --- | --- | --- |
| `buildDir` | relative subdirectory | `build` |
| `generator` | `Ninja`, `Unix Makefiles`, or `MinGW Makefiles` | toolchain generator, otherwise `Ninja` |
| `buildScript` | boolean | `true` |
| `outputFormats` | unique array containing `elf`, optionally `hex`/`bin` | `["elf", "hex", "bin"]` |
| `sizeReport` | boolean | `true` |
| `postBuildTools` | object | compiler-prefix `size` and `objcopy` |
| `conflictPolicy` | `prompt`, `overwrite`, `skip`, or `backup` | `prompt` |
| `runConfigure` | boolean | `false`, but must remain false |
| `runBuild` | boolean | `false`, but must remain false |

Automatic configure/build is not implemented. `runConfigure` and `runBuild` are
accepted for forward compatibility but setting either to true is rejected. When
`buildScript` is enabled, the generated `build.sh` runs CMake configure and build
for Debug or Release; this is the supported command-line workflow.

## Minimal example

```json
{
  "schemaVersion": 1,
  "environment": {
    "id": "stm32f407vet6",
    "rootDir": "../../c/embed-workspace/envs/stm32f407vet6",
    "toolchain": {
      "file": "../../c/embed-workspace/envs/stm32f407vet6/cmake/arm-none-eabi.cmake"
    }
  },
  "project": {
    "name": "demo_app",
    "sourceDirs": ["src"],
    "includeDirs": ["inc"],
    "scanSources": true,
    "generateExampleMain": true
  },
  "libraries": [
    {
      "name": "cj131",
      "source": {
        "type": "local-static",
        "artifact": "../../c/embed-workspace/libs/cj131/libcj131.a",
        "includeDirs": ["../../c/embed-workspace/libs/cj131/include"]
      }
    },
    {
      "name": "remote_math",
      "source": {
        "type": "git",
        "repository": "https://example.com/remote_math.git",
        "ref": "v1.2.0",
        "checkoutDir": "libs/remote_math"
      }
    }
  ],
  "generation": {
    "generator": "Ninja",
    "buildScript": true
  }
}
```

Validate and generate with:

```sh
python3 generator.py config.json --output ./generated-app --dry-run
python3 generator.py config.json --output ./generated-app --force
cd generated-app && ./build.sh Debug
```
