# Mask And Lithography

Mask and lithography features connect layout intent to resist geometry and downstream pattern transfer.

## Mask Inputs

Supported workflows include:

- built-in mask designer for simple pixel masks.
- image import through Pillow-backed helpers where available.
- NumPy mask arrays for programmatic or WebUI workflows.
- optional GDSII import/export through `gdstk` or `gdspy`.

Masks are normalized to 2D arrays aligned to the simulation domain. `resample_mask()` and rasterization helpers convert external resolution to the simulator grid.

## Mask Analysis

Mask helpers compute:

- line/space and feature metrics.
- connectivity and net-like relationships.
- bounding boxes, area, density, and line orientation descriptors.
- DRC constraints inferred from target node and voxel size.
- process-window probes and transfer-context estimates.

These metrics feed the mask designer, WebUI previews, Agent prompts, and `PhysicsAuditor`.

## Exposure Step

`ExposureStep` carries parameters such as mask mode, tone, dose, focus-like and optical parameters, custom/image mask data, and advanced exposure options. It delegates to `ProcessModel.expose_resist()`.

The model may apply OPC-like bias or morphology before exposure. The final mask density is used to update resist chemistry fields.

## Optical Approximation

The file includes compact optical helpers:

- `_generate_source_samples()` for illumination samples.
- `_get_tcc_modes()` for transmission cross coefficient modes.
- `_hopkins_aerial_image()` for aerial image approximation.
- `_compute_intensity_profile()` for local intensity.
- `_simulate_dill_exposure()` for resist chemistry conversion.

These functions are intentionally lightweight. They provide recipe-level feedback and consistent UI behavior, not a replacement for a full lithography simulator.

## PEB And Develop

`post_exposure_bake()` diffuses/relaxes exposure chemistry. `develop_resist()` then removes resist according to tone, exposure threshold, contrast, rate, and developer time. The result is a patterned resist volume used by later etch/deposition steps.

## Pattern Transfer

Pattern transfer happens through subsequent steps:

- etch uses the open mask and selectivity to remove materials.
- deposition can use masks or accessible-volume constraints.
- metrology compares mask intent with resulting material geometry.

Changes in mask or lithography code should be checked with a small headless selftest and at least one visual WebUI/desktop preview when PyQt5 is available.
