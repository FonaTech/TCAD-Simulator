# Algorithms

The simulator uses physics-inspired numerical heuristics on a voxel domain. It is suitable for qualitative process exploration, education, and recipe prototyping, not calibrated process sign-off.

## Voxel Domain

`ProcessModel` stores materials in a 3D integer grid. `voxel_size_nm` maps voxel units to physical dimensions. The height map summarizes the top material position per `(x, y)` column, while auxiliary fields carry doping, defects, stress, resist chemistry, and exposure information.

Important invariants:

- material ids must resolve through `MaterialDatabase`.
- grid edits must keep height map and open-mask state consistent.
- top-layer consuming operations must also clear or update doping/species fields.
- mesh and surface caches must be invalidated after geometry changes.

## Distance, Level Set, And Geometry

The numeric layer provides:

- binary and weighted propagation distances for etch/deposition accessibility.
- Euclidean distance transforms for signed-distance and morphology.
- `evolve_levelset()` and `surface_normals()` for interface motion and orientation-aware effects.
- `marching_cubes()` plus smoothing/decimation for material meshes.
- height-map surface patches and component summaries for faster preview and metrology.

The geometry path is deliberately mixed: fast height-map patches are used where topography is column-like, while marching cubes handles general volumetric material components.

## Lithography And Resist

The lithography chain includes:

1. `spin_resist()`: allocates resist layers over current topography.
2. `_generate_mask_density()` and mask import helpers: build or resample a binary/density mask.
3. `_hopkins_aerial_image()` and `_compute_intensity_profile()`: approximate optical image formation.
4. `_simulate_dill_exposure()`: evolves resist chemistry from exposure dose and material parameters.
5. `post_exposure_bake()`: diffuses/relaxes exposure chemistry.
6. `develop_resist()`: removes exposed or unexposed resist depending on tone, threshold, contrast, and process parameters.

This is a compact process model, not a full lithography solver. It prioritizes predictable mask-to-geometry transfer and useful recipe feedback.

## Deposition

`deposit_material()` dispatches to method-specific models:

- ALD: cycle-limited surface growth with conformality behavior.
- CVD: reaction/diffusion-style flux with accessible-volume and feature effects.
- PVD: directional line-of-sight growth and shadowing heuristics.
- Electroplating: topography-aware fill behavior.
- Epitaxy: seed/material constrained growth, critical-thickness checks, and optional in-situ doping.
- Generic deposition: columnar or conformal fallback based on method/material defaults.

Deposition methods update the material grid, height map, dopant fields when requested, and geometry caches.

## Etch

`etch_material()` supports dry, wet, directional, isotropic, anisotropic, and overetch-like behavior. Key helpers include:

- surface normal estimation and sputter-yield approximations.
- top-layer consumption with selectivity.
- directional profiles for plasma-like etch.
- wet isotropic diffusion and anisotropic/faceted etch approximations.
- taper and local loading heuristics.

The etch model treats selectivity as a material-id rate map and uses mask/open-area state to constrain where material can be removed.

## CMP

`cmp()` models planarization using removal rates, selectivity, slurry/pressure-like parameters, and effective density. It modifies topography toward a planar target while honoring material-specific removal behavior.

## Implant And Anneal

`implant()` uses species parameters, energy, dose, tilt/rotation/spread, stopping approximations, and defect generation to update doping and defect fields. Species-specific dopant fields are tracked when available.

`anneal()` diffuses dopants, repairs defects, and models temperature/time dependent redistribution. It also supports glass reflow/densification behavior for relevant material systems.

## Oxidation, Nitridation, And Surface Reactions

`surface_reaction()` and oxide growth helpers use simplified Deal-Grove/Massoud-style coefficient tables, ambient/material conditions, and diffusion limits to convert or grow surface materials. The model preserves a voxel-grid view of material conversion rather than solving full continuum transport.

## Metrology And Export

Metrology routines derive observable quantities from the grid and fields:

- cross-section material maps and doping slices.
- CD and feature metrics from masks or sections.
- material inventory and interface areas.
- component diameters and metrology bundles.
- STL, TCAD geometry, CSV, chart data, PNG frames, and optional MP4 exports.

Export routines should remain downstream of `ProcessModel` state and should not own simulation behavior.
