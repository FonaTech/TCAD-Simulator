# Architecture

`tcad_simulator.py` is intentionally published as the canonical application file. It contains the desktop GUI, the WebUI, the process simulator, the material database, optional knowledge/LLM features, and maintenance selftests in one Python source file.

## Source Regions

The file is organized roughly in this order:

1. Knowledge layer: `KnowledgeEngine`, `SemanticDocumentProcessor`, `LocalVectorIndex`, `ProcessMapper`, and `PhysicsAuditor`.
2. Numeric and geometry kernels: numba-aware helpers, distance transforms, level-set evolution, normals, marching cubes, grid compression, and snapshot utilities.
3. Materials: `Material`, `ParameterSpec`, and `MaterialDatabase`.
4. Core simulator: `ProcessModel` and its methods for wafer state, lithography, deposition, etch, CMP, implantation, anneal, oxidation/nitridation, metrology, and export.
5. Recipe model: `ProcessStep` subclasses plus `PROCESS_STEP_FACTORIES`.
6. Headless execution: `SimulateContext` and `simulate_headless`.
7. Mask and physics helpers: mask metrics, DRC, lithography proxy, `ProcessPhysicsDB`, and geometry analyzers.
8. Desktop UI: Qt canvases, parameter editors, mask designer, `MainWindow`, and `SimulatorController`.
9. WebUI runtime: storage helpers, recipe serialization, worker command loop, HTTP request handlers, `WebUIServerManager`, and `AdminServerManager`.
10. CLI/selftest entry points.

## Core State Model

`ProcessModel` owns the mutable simulation state:

- `grid`: a 3D material-id voxel array.
- `height_map`: per-column topography derived from the grid.
- `mask`: process mask state used by lithography and pattern transfer.
- `doping_field`, species-specific dopant fields, defect fields, and stress/thermal auxiliary fields.
- material and geometry caches used by rendering, metrology, and export.
- logs, snapshots, and optional cache files for headless/WebUI undo or replay.

Most process methods mutate the grid and then repair derived state: height map, open-mask state, mesh caches, dopant/species consistency, and logs. This is the main invariant to preserve when changing process algorithms.

## Recipe Execution

Recipes are lists of `ProcessStep` objects or serialized step blobs. Each concrete step owns UI-facing parameter specifications and calls a corresponding `ProcessModel` method in `execute()`.

```text
Recipe JSON/UI
    -> PROCESS_STEP_FACTORIES
    -> ProcessStep.execute(model)
    -> ProcessModel method
    -> grid/fields/height_map/log/cache updates
    -> UI/WebUI/metrology/export refresh
```

The same step protocol is used by the desktop GUI, WebUI, headless selftests, Agent proposals, and recipe import/export. This shared protocol is the main compatibility boundary.

## UI Boundaries

The desktop GUI is a local controller around the same model:

- `MainWindow` builds widgets, canvases, menus, recipe panels, and WebUI controls.
- `SimulatorController` owns `MaterialDatabase`, `ProcessModel`, recipe list, undo cache, WebUI/Admin managers, and import/export operations.
- Qt widgets should not implement process physics; they should collect parameters and delegate to `ProcessStep` or `ProcessModel`.

The WebUI uses HTTP plus a worker runtime:

- HTTP handlers resolve sessions, static assets, and API requests.
- each session uses isolated worker state where supported.
- workers own a `ProcessModel`, current recipe, caches, history, library access, and optional Agent state.

## Runtime Data

Runtime data is intentionally outside the source tree when possible. Key roots include:

- `TCAD_Web_Data/`: WebUI sessions, history, cache, exports, encrypted library, literature DB, Admin config, and local keys.
- `TCAD_Headless_Cache/`: headless snapshot cache under the system temp directory.
- `TCAD_GUI_Undo_*`: temporary GUI undo snapshot cache.

These directories can contain secrets, local data, large binary outputs, and user experiments. They are ignored by `.gitignore` and should not be uploaded.

## Generated Split Package

`tools/split_tcad.py` can generate `tcad_simulator_split/` for code navigation, API inventory, and mechanical validation. That package is not the authoritative project layout. Changes should be designed against `tcad_simulator.py`, then the split tool can be used as a developer report generator if needed.
