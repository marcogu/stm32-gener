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

The host needs Python 3.10+, CMake, the selected generator, and ARM GNU tools. Git dependencies are fetched by CMake during configure. The first desktop release bundles the Python bridge modules, while Python remains a host prerequisite.

Verification:

```sh
python3 -m unittest discover -s tests -v
cd desktop && npm run build
cd desktop && npm run tauri build -- --debug --bundles app
```
