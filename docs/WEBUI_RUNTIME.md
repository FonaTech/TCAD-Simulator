# WebUI Runtime

The WebUI is built into `tcad_simulator.py`. It provides browser access to recipe editing, simulation, preview, history, library storage, export, mask design, Admin configuration, and optional Agent workflows.

## Main Components

- `WebUIServerManager`: starts/stops the user WebUI HTTP server.
- `_WebUIRequestHandler`: serves static assets and API endpoints.
- `WebUISession`: maps browser sessions to worker state, storage, and history.
- `_webui_worker_main()`: owns per-session model state and handles RPC commands.
- `AdminServerManager`: starts/stops the Admin server for material/process/library configuration.
- `_AdminRequestHandler`: handles Admin UI and configuration routes.

## Request Flow

```text
Browser
    -> HTTP request/API command
    -> _WebUIRequestHandler
    -> WebUISession
    -> worker message {cmd, payload, rid}
    -> ProcessModel/recipe/library/export/agent action
    -> JSON or binary response
```

Where supported, sessions use isolated worker processes. Fallback modes keep the same command contract while reducing isolation.

## Worker State

A worker typically owns:

- `MaterialDatabase` and `ProcessModel`.
- current recipe and serialized recipe history.
- autosave and undo/snapshot cache.
- preview cache, render settings, and exported assets.
- library profile and encrypted library access.
- optional literature/Agent state and LLM provider config.

Large snapshots can be spilled to disk through the `_tcad_snapshot_*` helpers so WebUI sessions do not keep every array in process memory.

## Storage

By default, runtime state is stored under `TCAD_Web_Data/` next to the launch root. This can be overridden:

```bash
TCAD_WEBUI_STORAGE_ROOT=/path/to/storage
TCAD_STORAGE_ROOT=/path/to/storage
TCAD_LAUNCH_ROOT=/path/to/app/root
```

Do not commit `TCAD_Web_Data/`. It can contain encrypted libraries, master keys, Admin config, private recipes, exports, and literature databases.

## Assets And Downloaded JavaScript

The WebUI may need browser-side JavaScript assets such as Three.js helpers. Runtime-downloaded copies like `three.js`, `three.min.js`, `STLLoader.js`, and `OrbitControls.js` are ignored by `.gitignore`.

`tools/html_vendor/` is an offline documentation-site vendor cache for generated HTML docs. It is also ignored by default because it is large and reproducible.

## Rendering And Export

The WebUI supports both browser-side WebGL-style preview and host-assisted rendering paths. The host side can produce:

- preview manifests and gbuffer-like render data.
- cross-section and doping slices.
- STL/TCAD geometry exports.
- image frame sequences.
- optional MP4 through `imageio-ffmpeg`, system `ffmpeg`, or `TCAD_FFMPEG`.

Rendering code should consume `ProcessModel` surfaces, meshes, and metrology outputs rather than duplicating process-state logic.
