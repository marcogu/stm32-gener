# STM32 CMake 工程生成器需求说明（草案）

## 1. 产品目标

构建一个运行于 macOS、Windows、Linux 的桌面应用，通过向导式 UI 收集 STM32 应用工程所需的信息，生成可直接使用的 CMake 工程文件和配套脚手架。

生成结果应延续 `embed-workspace/apps/*` 中已有的模板化约定，同时把环境工程、库工程和当前应用工程之间的路径与依赖关系配置化。

## 2. 用户输入

### 2.1 环境工程

- 环境工程根目录，例如 `envs/stm32f407vet6`。
- 芯片型号、系列、CPU/FPU/浮点 ABI 等目标信息。
- CMake toolchain 文件、编译器前缀或工具链目录。
- CubeMX 生成模块、启动文件、链接脚本及 HAL/CMSIS 目录。
- CMake 生成器（首版优先 Ninja）和 Debug/Release 构建选项。
- 可选的 OpenOCD/ST-Link/J-Link 烧录与调试配置。

应用应能扫描环境目录并从现有 `cmake` 文件、CubeMX 模块、启动文件和链接脚本中读取可用信息，不应强制假设 toolchain 文件固定命名。

### 2.2 库工程

每个库至少包含名称、来源方式和链接信息。来源方式包括：

- 本地 CMake 子工程（`add_subdirectory`）。
- 本地预编译静态库及头文件目录。
- Git/FetchContent 依赖（仓库地址、版本/tag、本地落盘目录）。

可配置 CMake target 名、源码/头文件目录、编译定义、额外链接库、按环境区分的产物路径，以及是否仅作为 IDE 浏览源码暴露。

### 2.3 当前应用工程

- 新建或导入的工程目录。
- 项目名、target 名、版本号和输出目录。
- `src`、`inc` 等源码/头文件目录，以及源码扫描或显式文件清单。
- 应用入口文件和可选的示例 `app_main.c`。
- 输出格式：`.elf`、`.hex`、`.bin`，以及 `arm-none-eabi-size/objcopy` 等后处理工具。
- C 标准、编译选项、宏定义和其他高级 CMake 变量。

## 3. 生成结果

首版生成以下文件或目录：

- 根 `CMakeLists.txt`。
- `env_cfg.cmake`，用于选择目标环境。
- `build.sh`，用于在 macOS/Linux 中直接执行 configure 和 build，并支持选择 Debug/Release。
- `src/`、`inc/` 和示例入口文件。
- 可选的 `openocd/` 配置。
- `README.md`、`.gitignore` 和构建说明。

工程文件写入成功后，在输出目录依次执行 `git init`、`git add .`、
`git commit -am "created."`。无变更时不创建空提交；Git 失败时保留工程文件并显示错误。
预览不初始化仓库或创建提交。`.gitignore` 应包含系统和编辑器文件、编译产物、
配置的构建目录以及生成工程内的 Git 依赖检出目录。

首版不生成 `cmake/` 扩展目录、`CMakePresets.json` 或 manifest；这些内容在模板稳定后再评估。

模板应抽象出环境加载、应用源码、库依赖、编译参数和 ELF/HEX/BIN 后处理等片段，避免把 `../../envs`、`../../libs` 或具体库名硬编码到生成器中。输出路径优先使用可移植的相对路径变量，必要时保留规范化绝对路径。

对于现有 CubeMX 环境，生成的 `project()` 名称必须与应用 target 保持一致，因为环境模块可能通过 `CMAKE_PROJECT_NAME` 注入 CubeMX 源码和链接设置；生成器不能在同一目录中再嵌套或覆盖环境工程的 `project()`。

## 4. UI 与工作流

应用采用分步向导：

1. 选择模板或导入已有工作区。
2. 扫描并选择环境工程，验证工具链和目标芯片。
3. 扫描、添加和配置库依赖。
4. 配置应用目录、源码和输出选项。
5. 预览待生成文件树、关键 CMake 片段和 diff。
6. 选择冲突策略并生成。
7. 可选执行 `cmake configure`/构建，显示日志和错误位置。

生成前必须校验路径存在性、工具链可执行性、target 名称冲突、依赖闭合和模板变量完整性。已有文件应支持跳过、覆盖、备份或逐项处理；生成前在预览中展示已有文件与待写入内容的差异。首版不依赖 manifest 判断历史修改，用户可在预览阶段决定处理方式。

配置应可保存为版本化 JSON，支持最近项目和重新打开。

## 5. 首版范围（MVP）

- 支持 macOS、Windows、Linux。
- 支持 GCC `arm-none-eabi`、CMake 和 Ninja。
- 支持现有本地环境工程和本地库工程扫描。
- 库依赖首版支持本地方式和 Git 在线方式：本地 CMake 子工程、预编译静态库，以及 Git/FetchContent（仓库地址、版本/tag、本地落盘目录）。用户在每个库条目中选择来源方式。
- 按现有 `apps` 模板生成一个 STM32 C/ASM 应用工程。
- 生成 `build.sh`、预览、冲突保护、配置保存和基本 configure 验证。

首版暂不包含在线模板市场、复杂包管理、完整 IDE 调试器、CubeMX `.ioc` 深度编辑和多目标批量工程。Git 在线方式只负责在生成工程中写入 FetchContent 配置，不负责实现独立的包管理界面。

## 6. 验收标准

- 在空目录中选择现有环境工程后，可生成完整目录和 CMake 文件。
- 生成的工程能够成功执行 CMake configure，并在工具链可用时构建出 `.elf`、`.hex`、`.bin`。
- 导入 `ad_pwm_tests` 等样例时，能识别源码、头文件目录、`TARGET_ENV` 和 `cj131` 等依赖。
- 环境路径、库路径或工具链无效时，阻止生成并指出具体字段和修复建议。
- 重复生成时不静默覆盖用户已修改文件，并在预览中显示冲突。
- 生成的 `build.sh` 在 macOS/Linux 中可直接完成 configure 和 build；Windows 用户可使用 README 中的等价 CMake 命令。
- 生成结果在 macOS、Windows、Linux 上使用对应路径分隔符和可用工具命令。

## 7. 分阶段实现建议

1. 固化配置 JSON 数据模型和首个 CMake 模板（包含本地/Git 两种库来源），使用样例工程建立快照验收。数据契约见 [`CONFIG_SCHEMA.md`](CONFIG_SCHEMA.md)，当前已提供命令行生成器原型 [`generator.py`](generator.py)。
2. 实现独立的扫描、校验和模板渲染核心。（已完成：`config_model.py`、`scanner.py`、`generator.py`、`bridge.py`。）
3. 实现桌面 UI 向导、预览和冲突处理。（已完成 Tauri 2 + React 首版界面。）
4. 接入 configure/build、日志和 `build.sh` 生成。
5. 完善打包、CubeMX 导入、CMakePresets、manifest 和模板扩展机制。
