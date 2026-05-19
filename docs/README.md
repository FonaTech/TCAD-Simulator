# TCAD Simulator Documentation

This documentation describes the architecture and algorithms of `tcad_simulator.py`, the canonical source file for the project. The generated `tcad_simulator_split/` package is a developer aid only; the source release should be understood and maintained from the single-file simulator first.

本文档解释主程序 `tcad_simulator.py` 的算法架构、运行时边界和维护方式。`tcad_simulator_split/` 是开发辅助生成物，不是开源发布时必须上传的主体。

## Document Map / 文档地图

- [ARCHITECTURE.md](ARCHITECTURE.md): source organization, state model, UI/WebUI boundaries, and runtime data flow.
- [ALGORITHMS.md](ALGORITHMS.md): voxel process model, lithography, deposition, etch, CMP, implant, anneal, oxidation, geometry, metrology, and export algorithms.
- [WEBUI_RUNTIME.md](WEBUI_RUNTIME.md): built-in WebUI, worker sessions, storage, Admin server, rendering, exports, and runtime asset handling.
- [MASK_LITHOGRAPHY.md](MASK_LITHOGRAPHY.md): mask import/rasterization, DRC, aerial image approximation, Dill exposure, PEB, and resist development.
- [AGENT_KNOWLEDGE.md](AGENT_KNOWLEDGE.md): optional literature ingestion, local retrieval, process mapping, physics audit, skills, and LLM-assisted recipe design.
- [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md): local setup, verification, cross-platform scripts, optional split tooling, GitHub upload checklist, and license notes.

## Main Entry Points / 主入口

- `python tcad_simulator.py`: launch the desktop PyQt5 application.
- `./run_tcad_macos.sh` or `./run_tcad_linux.sh`: Unix convenience launchers.
- `.\run_tcad.ps1` or `run_tcad.bat`: Windows launchers.
- `python tcad_simulator.py --mask-prompt-selftest --n 3 --res 128`: lightweight headless selftest.

## Architectural Summary / 架构摘要

`tcad_simulator.py` combines several layers in one distributable file:

1. Knowledge and recipe-assistance utilities.
2. Numeric kernels, distance transforms, voxel compression, snapshot spill/reload, and geometry reconstruction.
3. `MaterialDatabase`, material parameters, palettes, and Admin overrides.
4. `ProcessModel`, which owns the voxel grid, height map, doping/defect fields, masks, logs, caches, and export state.
5. `ProcessStep` subclasses and `PROCESS_STEP_FACTORIES`, which convert UI/JSON recipe steps into model mutations.
6. Desktop Qt widgets and `SimulatorController`.
7. Built-in WebUI/Admin HTTP servers, session workers, asset handling, recipe history, library storage, exports, and optional Agent mode.

The numerical model is physics-inspired and designed for research, teaching, and recipe exploration. It is not a calibrated commercial TCAD sign-off engine.
