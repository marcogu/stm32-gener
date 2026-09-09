# STM CMake Forge

Cross-platform desktop wizard for generating STM32 CMake application projects.

The UI collects the project, environment, library source and output settings. It calls the validated Python core through a small JSON bridge. Local static archives, local CMake projects and Git/FetchContent dependencies are supported.

## Development

```sh
npm install
npm run tauri dev
```

The host must provide Python 3.10+, CMake and the selected ARM GNU toolchain. The release bundle includes the Python bridge modules; it still expects Python to be available on the host until an embedded Python runtime is added.

## Verification

```sh
npm run build
npm run tauri build -- --debug
```
