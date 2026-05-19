# TCAD Process Simulator

单文件半导体工艺模拟平台，包含 PyQt5 桌面界面、内置多用户 WebUI、工艺 recipe 编辑器、掩膜工具、3D/2D 可视化、导出工具，以及可选的 LLM 辅助工艺设计能力。

Single-file semiconductor process simulation platform with a PyQt5 desktop interface, built-in multi-user WebUI, process recipe editor, mask tools, 3D/2D visualization, export utilities, and optional LLM-assisted process design.

主入口是 [`tcad_simulator.py`](tcad_simulator.py)。`tcad_simulator_split/` 和 `tcad_simulator_split.zip` 是从单文件拆分出来的生成产物，普通 GitHub 源码发布不需要上传。

The canonical entry point is [`tcad_simulator.py`](tcad_simulator.py). `tcad_simulator_split/` and `tcad_simulator_split.zip` are generated split-package artifacts and are not required for a normal GitHub source release.

## 功能亮点 / Highlights

- 基于体素网格的晶圆/工艺模型，可配置网格尺寸和 voxel size。  
  Voxel-based wafer/process model with configurable grid dimensions and voxel size.
- 支持初始化晶圆、光刻胶旋涂、曝光、PEB、显影、沉积、选择性外延、刻蚀、CMP、离子注入、退火、氧化/氮化和表面反应等步骤。  
  Process steps include wafer initialization, resist spin/exposure/PEB/develop, deposition, selective epitaxy, etch, CMP, implantation, anneal, oxidation/nitridation, and surface reactions.
- 内置材料数据库，包含工艺参数、颜色、成分和 Admin 覆盖配置。  
  Built-in material database with process parameters, colors, composition data, and Admin overrides.
- 桌面 GUI 支持 recipe 编辑、参数检查、3D stack、截面图、掺杂/曝光热图、数据导出和 WebUI 启动。  
  Desktop GUI supports recipe editing, parameter inspection, 3D stack view, cross-sections, doping/exposure heatmaps, data export, and WebUI launch.
- 多用户 WebUI 支持隔离 worker session、WebGL/host-assisted preview、recipe history、library 管理、mask designer 和可选 AI Agent mode。  
  Multi-user WebUI supports isolated worker sessions, WebGL/host-assisted preview, recipe history, library management, mask designer, and optional AI Agent mode.
- 支持图片、NumPy mask 和可选 GDSII 的掩膜导入/导出。  
  Mask import/export supports images, NumPy masks, and optional GDSII workflows.
- 支持导出 recipe、CSV metrics/cross-section、STL/geometry、PNG frame sequence，以及可选 MP4 工艺视频。  
  Export support includes recipes, CSV metrics/cross-sections, STL/geometry assets, PNG frame sequences, and optional MP4 process videos.
- 可选 PDF 文献摄取和本地检索，用于辅助将工艺描述映射为 simulator recipe。  
  Optional literature/PDF ingestion and local retrieval help map process descriptions into simulator recipes.

## 状态和适用范围 / Status And Scope

这是面向研究和教学的 TCAD-like simulator。它实现的是 physics-inspired 数值启发式模型和工艺模型，不是经过工业标定的商业 TCAD sign-off 工具。除非你已经用真实工艺数据验证，否则生成的 recipe、几何结构、掺杂场和 metrology 数值都应视为探索性结果。

This is a research and education-oriented TCAD-like simulator. It uses physics-inspired numerical heuristics and process models, but it is not a calibrated replacement for commercial TCAD sign-off tools. Treat generated recipes, geometry, doping fields, and metrology values as exploratory unless validated against real process data.

## 仓库结构 / Repository Layout

```text
.
├── tcad_simulator.py          # 主程序 / Main single-file application
├── README.md                  # 项目说明 / Project overview and usage
├── docs/                      # 主程序架构文档 / Source-focused architecture docs
├── requirements.txt           # 推荐依赖 / Recommended Python dependencies
├── run_tcad_macos.sh          # macOS 启动脚本 / macOS launcher
├── run_tcad_linux.sh          # Linux 启动脚本 / Linux launcher
├── run_tcad.ps1               # Windows PowerShell 启动脚本 / Windows PowerShell launcher
├── run_tcad.bat               # Windows CMD 启动脚本 / Windows CMD launcher
├── split_tcad.sh              # macOS/Unix 开发拆分脚本 / macOS/Unix developer split script
├── split_tcad_linux.sh        # Linux 开发拆分脚本 / Linux developer split script
├── split_tcad.ps1             # Windows PowerShell 开发拆分脚本 / Windows PowerShell split script
├── split_tcad.bat             # Windows CMD 开发拆分脚本 / Windows CMD split script
├── LICENSE                    # 本项目代码 MIT 协议 / MIT license for this project's own code
├── THIRD_PARTY_NOTICES.md     # 第三方依赖许可说明 / Third-party dependency license notes
├── CONTRIBUTING.md            # 贡献流程 / Contribution workflow
├── SECURITY.md                # 安全策略 / Vulnerability reporting policy
├── CODE_OF_CONDUCT.md         # 社区行为准则 / Community conduct policy
├── tools/                     # 开发工具，包括拆分脚本 / Developer tooling, including split helpers
└── tcad_simulator_split/      # 生成产物，默认不上传 / Generated split archive; ignored for release
```

## 环境要求 / Requirements

- 推荐 Python 3.10 或更新版本。  
  Python 3.10 or newer is recommended.
- 主桌面 GUI 需要可运行 Qt 的桌面环境。  
  A desktop environment capable of running Qt is required for the main GUI.
- WebUI 首次启动时，如果脚本旁边还没有 Three.js 静态资源，需要网络下载。  
  First WebUI launch may need network access to download Three.js static assets if they are not already present beside the script.
- GPU/WebGL 可提升浏览器 3D 预览体验；WebUI 也包含 host-assisted rendering 模式。  
  GPU/WebGL support improves browser-side 3D preview, while the WebUI also includes host-assisted rendering modes.

## 安装 / Installation

建议使用独立虚拟环境安装依赖：

Create an isolated environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果只想安装较小的核心运行栈：

For a smaller installation, install only the core runtime stack:

```bash
python -m pip install numpy matplotlib PyQt5 scipy scikit-image cryptography
```

可选功能对应的可选依赖：

Optional feature dependencies:

- GDSII 导入/导出 / GDSII import/export: `gdstk` or `gdspy`
- PDF 文献摄取 / PDF literature ingestion: `pdfminer.six`, `PyPDF2`, or `PyMuPDF`
- 掩膜 raster 辅助 / Mask raster helpers: `Pillow`
- MP4 导出 / MP4 export: `imageio-ffmpeg` or system `ffmpeg`
- 数值加速 / Numeric acceleration: `numba`

## 运行桌面应用 / Run The Desktop App

```bash
python tcad_simulator.py
```

也可以使用仓库内的跨平台启动脚本：

You can also use the cross-platform launchers in the repository:

```bash
# macOS
./run_tcad_macos.sh

# Linux
./run_tcad_linux.sh
```

```powershell
# Windows PowerShell
.\run_tcad.ps1
```

```bat
:: Windows CMD
run_tcad.bat
```

这些脚本会把参数原样转发给 `tcad_simulator.py`，例如：

These scripts forward arguments to `tcad_simulator.py`, for example:

```bash
./run_tcad_linux.sh --mask-prompt-selftest --n 3 --res 128
```

典型流程：

Typical workflow:

1. 调整 domain settings，例如 `NX`、`NY`、`NZ`、voxel size 和线程数。  
   Adjust domain settings such as `NX`, `NY`, `NZ`, voxel size, and thread count.
2. 在左侧面板编辑 process recipe。  
   Edit the process recipe in the left panel.
3. 单步运行、运行到指定步骤，或运行完整流程。  
   Run one step, run to a selected step, or run all steps.
4. 检查 3D stack、截面、热图、metrology 和日志。  
   Inspect the 3D stack, cross-sections, heatmaps, metrology, and logs.
5. 导出几何、截面、metrics、recipe、图片或视频。  
   Export geometry, cross-section data, metrics, recipes, images, or video assets.

## WebUI

在桌面应用里点击 **Start Web UI** 启动 WebUI。默认行为：

Start the WebUI from the desktop app with **Start Web UI**. Defaults:

- WebUI 端口从 `8765` 开始，被占用时自动递增。  
  WebUI port starts at `8765` and auto-increments if occupied.
- Admin UI 端口从 `8766` 开始，被占用时自动递增。  
  Admin UI port starts at `8766` and auto-increments if occupied.
- 支持时，每个 Web session 使用隔离 worker process。  
  Web sessions use isolated worker processes where supported.
- 运行时数据默认写入脚本旁边的 `TCAD_Web_Data/`，除非通过环境变量覆盖。  
  Runtime data is stored under `TCAD_Web_Data/` beside the script unless overridden.

常用环境变量：

Useful environment variables:

```bash
TCAD_WEBUI_STORAGE_ROOT=/path/to/storage
TCAD_STORAGE_ROOT=/path/to/storage
TCAD_LAUNCH_ROOT=/path/to/app/root
TCAD_FFMPEG=/path/to/ffmpeg
TCAD_SKIP_QT=1
MPLBACKEND=Agg
```

`TCAD_SKIP_QT=1` 只适合 headless/selftest；桌面 GUI 仍需要 PyQt5。

`TCAD_SKIP_QT=1` is useful only for headless/selftest workflows; the desktop GUI still needs PyQt5.

## 自测 / Selftests

脚本包含几个 headless selftest 入口：

The script includes several headless selftest entry points:

```bash
python tcad_simulator.py --mask-prompt-selftest
python tcad_simulator.py --webui-selftest --skip-video
python tcad_simulator.py --saqp-selftest --skip-ref
python tcad_simulator.py --recipe-io-selftest
```

部分 regression selftest 需要本地 fixture，例如 `SAQP_Thinking_Flow.json`、`tcad_simulator_2.19.py` 或 `LLM_Test_Config.json`。缺少这些 fixture 时测试失败，不一定表示 simulator 无法运行。

Some regression selftests expect local fixture files such as `SAQP_Thinking_Flow.json`, `tcad_simulator_2.19.py`, or `LLM_Test_Config.json`. Missing fixtures can make those tests fail even when the simulator starts correctly.

## 文档 / Documentation

正式文档位于 [`docs/`](docs/)，重点解释 `tcad_simulator.py` 本身的架构、算法和维护边界：

Formal documentation lives in [`docs/`](docs/) and focuses on the architecture, algorithms, and maintenance boundaries of `tcad_simulator.py`:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：源文件组织、状态模型和运行时边界。  
  Source organization, state model, and runtime boundaries.
- [`docs/ALGORITHMS.md`](docs/ALGORITHMS.md)：体素工艺模型、沉积、刻蚀、CMP、注入、退火、氧化、几何和测量算法。  
  Voxel process model, deposition, etch, CMP, implant, anneal, oxidation, geometry, and metrology algorithms.
- [`docs/WEBUI_RUNTIME.md`](docs/WEBUI_RUNTIME.md)：内置 WebUI、worker session、Admin server、存储、渲染和导出。  
  Built-in WebUI, worker sessions, Admin server, storage, rendering, and exports.
- [`docs/MASK_LITHOGRAPHY.md`](docs/MASK_LITHOGRAPHY.md)：掩膜导入、DRC、曝光、PEB 和显影链路。  
  Mask import, DRC, exposure, PEB, and development flow.
- [`docs/AGENT_KNOWLEDGE.md`](docs/AGENT_KNOWLEDGE.md)：可选文献摄取、RAG、Agent 和 recipe 审计。  
  Optional literature ingestion, RAG, Agent workflows, and recipe auditing.
- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md)：验证、跨平台脚本、可选拆分工具和 GitHub 上传检查。  
  Verification, cross-platform scripts, optional split tooling, and GitHub upload checks.

可生成离线 HTML 文档：

Offline HTML documentation can be generated with:

```bash
python3 tools/docsite.py --docs-dir docs --out-dir docs_html
```

如果 `tools/html_vendor/` 缺少 Mermaid、Marked、MathJax 或 Highlight.js，构建脚本会自动下载所需 vendor 资源。`docs_html/` 和 `tools/html_vendor/` 是可再生成产物，默认不上传 GitHub。

If `tools/html_vendor/` is missing Mermaid, Marked, MathJax, or Highlight.js, the build script downloads the required vendor assets automatically. `docs_html/` and `tools/html_vendor/` are reproducible generated outputs and are not uploaded by default.

## LLM 和文献功能 / LLM And Literature Features

LLM 功能是可选的，普通 WebUI 模式默认关闭。支持的 provider 风格包括：

LLM features are optional and disabled by default in normal WebUI mode. Supported provider styles include:

- Ollama 本地 HTTP API / Ollama local HTTP API
- OpenAI-compatible chat completions APIs
- SiliconFlow OpenAI-compatible API
- Custom HTTP payload/header templates

不要提交 API key 或本地 LLM 配置文件。本项目 `.gitignore` 已排除常见 runtime config/output 路径。

Do not commit API keys or local LLM config files. The project `.gitignore` excludes common runtime config/output paths.

## 开发拆分工具 / Developer Split Tooling

本仓库仍保留 `tools/split_tcad.py` 和对应脚本，用于代码审查、模块清单、API 盘点和生成开发文档。它们是开发辅助工具，不改变主入口仍然是 `tcad_simulator.py` 这一事实。

The repository still includes `tools/split_tcad.py` and companion scripts for code review, module inventory, API inspection, and generated developer docs. They are developer aids; the canonical entry point remains `tcad_simulator.py`.

```bash
# macOS / Unix
./split_tcad.sh

# Linux
./split_tcad_linux.sh
```

```powershell
# Windows PowerShell
.\split_tcad.ps1
```

```bat
:: Windows CMD
split_tcad.bat
```

生成的 `tcad_simulator_split/`、`tcad_simulator_split.zip` 和 `docs_html/` 默认不应上传 GitHub。

Generated `tcad_simulator_split/`, `tcad_simulator_split.zip`, and `docs_html/` should not be uploaded to GitHub by default.

## GitHub 上传清单 / GitHub Upload Checklist

建议上传：

Recommended files to upload:

- `tcad_simulator.py`
- `README.md`
- `docs/`
- `LICENSE`
- `requirements.txt`
- `THIRD_PARTY_NOTICES.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `.gitignore`
- `.github/`
- `run_tcad_macos.sh`、`run_tcad_linux.sh`、`run_tcad.ps1`、`run_tcad.bat`
- `tools/` 和 `split_tcad*`，仅当你想公开开发用拆分工具。  
  `tools/` and `split_tcad*`, only if you want to publish developer split tooling.

默认不要上传：

Do not upload by default:

- `tcad_simulator_split/`
- `tcad_simulator_split.zip`
- `docs_html/`
- `tools/html_vendor/`
- `.DS_Store`
- `TCAD_Web_Data/`
- `TCAD_Selftest_Output_*/`
- `LLM_Test_Config.json`
- API keys、private datasets、internal recipes 或 generated exports。  
  API keys, private datasets, internal recipes, or generated exports.

## 开源协议 / License

本项目你自己的源代码计划使用 MIT License 发布，见 [`LICENSE`](LICENSE)。

This project's own source code is intended to be released under the MIT License. See [`LICENSE`](LICENSE).

重要依赖说明：MIT 对你自己的代码是合理选择，但不会改变第三方依赖的许可证。尤其是 PyQt5 采用 GPL/commercial 双许可；如果安装可选的 PyMuPDF/MuPDF，其许可证是 AGPL/commercial。开源发布源码通常可以处理，但如果你要打包二进制、闭源再分发或商业分发，需要专门复核。详见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

Important dependency note: MIT is reasonable for your own code, but it does not change third-party licenses. PyQt5 is GPL/commercial licensed, and optional PyMuPDF/MuPDF is AGPL/commercial licensed. Public source release is usually manageable, but packaged binaries, closed-source redistribution, and commercial distribution need careful review. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
