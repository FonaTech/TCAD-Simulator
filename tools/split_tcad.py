#!/usr/bin/env python3
"""Split the TCAD simulator monolith into a documented Python package.

The splitter is intentionally conservative. It creates readable module files,
extracts large embedded web assets, preserves the original runtime behavior via
small bootstrap/shared-namespace helpers, and emits reports that make remaining
manual refactors explicit.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from docsite import build_docsite, verify_docsite


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tools" / "split_manifest.json"


@dataclass
class TopLevelItem:
    name: str
    names: List[str]
    kind: str
    lineno: int
    end_lineno: int
    source: str
    digest: str

    @property
    def line_count(self) -> int:
        return max(0, int(self.end_lineno) - int(self.lineno) + 1)


@dataclass
class ModuleBucket:
    name: str
    symbols: List[str] = field(default_factory=list)
    line_count: int = 0


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return str(path)


def node_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target_nodes: List[ast.AST]
        if isinstance(node, ast.Assign):
            target_nodes = list(node.targets)
        else:
            target_nodes = [node.target]
        names: List[str] = []
        for target in target_nodes:
            names.extend(target_names(target))
        if len(names) == 1:
            return names[0]
        if names:
            return ",".join(names)
    if isinstance(node, ast.If):
        try:
            if ast.unparse(node.test) in {"__name__ == '__main__'", "__name__ == \"__main__\""}:
                return "__main_guard__"
        except Exception:
            pass
    return None


def defined_names(node: ast.AST) -> List[str]:
    names: List[str] = []

    def visit(child: ast.AST) -> None:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(child.name)
            return
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            target_nodes = child.targets if isinstance(child, ast.Assign) else [child.target]
            for target in target_nodes:
                names.extend(target_names(target))
            return
        if isinstance(child, ast.Import):
            for alias in child.names:
                names.append(alias.asname or alias.name.split(".")[0])
            return
        if isinstance(child, ast.ImportFrom):
            for alias in child.names:
                if alias.name == "*":
                    continue
                names.append(alias.asname or alias.name)
            return
        if isinstance(child, (ast.If, ast.Try, ast.With)):
            for sub in getattr(child, "body", []):
                visit(sub)
            for sub in getattr(child, "orelse", []):
                visit(sub)
            for sub in getattr(child, "finalbody", []):
                visit(sub)
            for handler in getattr(child, "handlers", []):
                for sub in getattr(handler, "body", []):
                    visit(sub)

    visit(node)
    out: List[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def target_names(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: List[str] = []
        for child in node.elts:
            names.extend(target_names(child))
        return names
    return []


def node_kind(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "function"
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        return "constant"
    if isinstance(node, ast.If):
        return "if"
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return "import"
    if isinstance(node, ast.Try):
        return "try"
    return type(node).__name__


def slice_lines(lines: Sequence[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end]) + "\n"


def parse_items(source_path: Path) -> Tuple[str, ast.Module, List[str], List[TopLevelItem], str]:
    source = source_path.read_text(encoding="utf-8", errors="surrogateescape")
    module = ast.parse(source)
    lines = source.splitlines()
    doc = ast.get_docstring(module) or ""
    items: List[TopLevelItem] = []
    for node in module.body:
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue
        lineno = int(getattr(node, "lineno", 0) or 0)
        decorators = getattr(node, "decorator_list", None)
        if decorators:
            lineno = min([lineno] + [int(getattr(dec, "lineno", lineno) or lineno) for dec in decorators])
        end_lineno = int(getattr(node, "end_lineno", lineno) or lineno)
        if lineno <= 0:
            continue
        if end_lineno <= 93:
            continue
        if isinstance(node, ast.If):
            try:
                if ast.unparse(node.test) in {"__name__ == '__main__'", "__name__ == \"__main__\""}:
                    continue
            except Exception:
                pass
        names = defined_names(node)
        name = ",".join(names) if names else f"__block_{lineno}"
        src = slice_lines(lines, lineno, end_lineno)
        items.append(
            TopLevelItem(
                name=name,
                names=names,
                kind=node_kind(node),
                lineno=lineno,
                end_lineno=end_lineno,
                source=src,
                digest=sha256_text(src),
            )
        )
    return source, module, lines, items, doc


def imports_preamble(lines: Sequence[str], module: ast.Module) -> str:
    parts: List[str] = []
    for node in module.body:
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Try)):
            lineno = int(getattr(node, "lineno", 0) or 0)
            end_lineno = int(getattr(node, "end_lineno", lineno) or lineno)
            if lineno and end_lineno and end_lineno <= 93:
                parts.append(slice_lines(lines, lineno, end_lineno).rstrip())
            continue
        if int(getattr(node, "lineno", 10**9) or 10**9) > 93:
            break
    return "\n\n".join(parts).strip() + "\n"


def runtime_preamble(lines: Sequence[str], module: ast.Module) -> str:
    preamble = imports_preamble(lines, module)
    if "Union" not in preamble:
        preamble = preamble.replace(
            "from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Set",
            "from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Set, Union",
        )
    return preamble.rstrip() + "\n\n"


def build_assignment(manifest: Dict[str, Any], items: List[TopLevelItem]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    symbol_to_module: Dict[str, str] = {}
    prefix_rules: List[Tuple[str, str]] = []
    for mod in manifest.get("modules", []):
        mod_name = str(mod["name"])
        for sym in mod.get("symbols", []):
            symbol_to_module[str(sym)] = mod_name
        for prefix in mod.get("prefixes", []):
            prefix_rules.append((str(prefix), mod_name))

    line_fallbacks: List[Tuple[int, int, str]] = [
        (94, 2700, "knowledge.documents"),
        (2701, 4617, "core.numeric"),
        (4618, 4718, "compat"),
        (4719, 5570, "core.geometry"),
        (5571, 6349, "materials.database"),
        (6350, 7373, "core.snapshot"),
        (7374, 18022, "process.model"),
        (18023, 19088, "process.steps"),
        (19089, 19467, "process.headless"),
        (19468, 21283, "mask.analysis"),
        (21284, 23326, "ui.qt_components"),
        (23327, 25526, "ui.controller"),
        (25527, 25850, "webui.shared"),
        (25851, 27867, "knowledge.skills"),
        (27868, 32059, "library.recipe"),
        (32060, 35118, "mask.layout"),
        (35119, 70491, "webui.worker"),
        (70492, 75989, "webui.server"),
        (75990, 99249, "webui.assets"),
        (99250, 101363, "selftests.runner"),
    ]

    assigned: Dict[str, str] = {}
    unassigned: List[Dict[str, Any]] = []
    for item in items:
        names = item.names or item.name.split(",")
        mod_name = symbol_to_module.get(item.name)
        if not mod_name:
            for name in names:
                if name in symbol_to_module:
                    mod_name = symbol_to_module[name]
                    break
        if not mod_name:
            for name in names:
                for prefix, candidate in prefix_rules:
                    if name.startswith(prefix):
                        mod_name = candidate
                        break
                if mod_name:
                    break
        if not mod_name:
            for start, end, candidate in line_fallbacks:
                if start <= item.lineno <= end:
                    mod_name = candidate
                    break
        if mod_name:
            assigned[item.name] = mod_name
        elif item.kind not in {"import", "try"}:
            unassigned.append(
                {
                    "name": item.name,
                    "kind": item.kind,
                    "line": item.lineno,
                    "end_line": item.end_lineno,
                    "line_count": item.line_count,
                    "sha256": item.digest,
                }
            )
    return assigned, unassigned


def dedupe_items(items: List[TopLevelItem]) -> Tuple[List[TopLevelItem], List[Dict[str, Any]]]:
    seen: Dict[Tuple[str, str], TopLevelItem] = {}
    out: List[TopLevelItem] = []
    removed: List[Dict[str, Any]] = []
    for item in items:
        key = (item.name, item.digest)
        if key in seen:
            first = seen[key]
            removed.append(
                {
                    "name": item.name,
                    "kind": item.kind,
                    "removed_line": item.lineno,
                    "kept_line": first.lineno,
                    "reason": "identical top-level definition",
                    "sha256": item.digest,
                }
            )
            continue
        seen[key] = item
        out.append(item)
    return out, removed


def module_path(src_root: Path, module_name: str) -> Path:
    return src_root.joinpath(*module_name.split(".")).with_suffix(".py")


def make_package_dirs(src_root: Path, modules: Iterable[str]) -> None:
    src_root.mkdir(parents=True, exist_ok=True)
    write_text(src_root / "__init__.py", "# Generated TCAD simulator package.\n")
    for module_name in modules:
        parts = module_name.split(".")[:-1]
        cur = src_root
        for part in parts:
            cur = cur / part
            init = cur / "__init__.py"
            if not init.exists():
                write_text(init, "# Package marker.\n")


def make_real_module(module_name: str, symbols: Sequence[str], body: str) -> str:
    public = [s for s in symbols if s and not s.startswith("_")]
    lines = [
        f'"""Generated TCAD simulator module: {module_name}.',
        "",
        "This file contains source extracted from tcad_simulator.py. The small",
        "prepare/finalize calls keep cross-module globals compatible with the",
        "original single-file execution model.",
        '"""',
        "from __future__ import annotations",
        "",
        "from tcad_simulator import _bootstrap as _b",
        "from tcad_simulator._shared import finalize_module as _tcad_finalize_module",
        "from tcad_simulator._shared import prepare_module as _tcad_prepare_module",
        "",
        "_b.ensure_before(__name__)",
        "_tcad_prepare_module(globals(), __name__)",
        "",
        body.rstrip(),
        "",
        f"_tcad_finalize_module(globals(), __name__, {list(symbols)!r})",
    ]
    if public:
        lines.append("")
        lines.append(f"__all__ = {public!r}")
    return "\n".join(lines) + "\n"


def make_asset_loader_module() -> str:
    return textwrap.dedent(
        '''\
        """Package resource loader for extracted TCAD WebUI assets."""
        from __future__ import annotations

        from importlib import resources


        def read_asset(name: str) -> str:
            return resources.files("tcad_simulator.assets.webui").joinpath(name).read_text(encoding="utf-8")
        '''
    )


def make_shared_module() -> str:
    return textwrap.dedent(
        '''\
        """Shared namespace support for generated TCAD modules."""
        from __future__ import annotations

        import runpy
        from typing import Any, Dict, Iterable, List, MutableMapping

        NS: Dict[str, Any] = {}
        MODULE_GLOBALS: List[MutableMapping[str, Any]] = []
        _RUNTIME_LOADED = False


        def _copy_runtime() -> None:
            global _RUNTIME_LOADED
            if _RUNTIME_LOADED:
                return
            runtime = runpy.run_module("tcad_simulator._runtime")
            for key, value in runtime.items():
                if key.startswith("__"):
                    continue
                NS[key] = value
            _RUNTIME_LOADED = True


        def prepare_module(target: MutableMapping[str, Any], module_name: str) -> None:
            _copy_runtime()
            if target not in MODULE_GLOBALS:
                MODULE_GLOBALS.append(target)
            for key, value in NS.items():
                if key.startswith("__"):
                    continue
                target.setdefault(key, value)


        def finalize_module(target: MutableMapping[str, Any], module_name: str, exports: Iterable[str]) -> None:
            for key, value in list(target.items()):
                if key.startswith("__"):
                    continue
                if key in {"_b", "_tcad_prepare_module", "_tcad_finalize_module"}:
                    continue
                NS[key] = value
            for module_globals in MODULE_GLOBALS:
                for key, value in NS.items():
                    if key.startswith("__"):
                        continue
                    module_globals.setdefault(key, value)
        '''
    )


def make_bootstrap(module_names: Sequence[str]) -> str:
    return textwrap.dedent(
        f'''\
        """Bootstrap real split TCAD modules in original source order."""
        from __future__ import annotations

        import importlib
        import sys
        from types import MappingProxyType
        from typing import Any, Iterable, MutableMapping

        from tcad_simulator import _shared

        _MODULES = {list(module_names)!r}
        _LOADED: set[str] = set()
        _LOADING: set[str] = set()


        def mark_loaded(name: str) -> None:
            _LOADED.add(name)


        def ensure_before(module_name: str) -> None:
            if module_name not in _MODULES:
                return
            for name in _MODULES:
                if name == module_name:
                    return
                if name in _LOADED or name in _LOADING:
                    continue
                _LOADING.add(name)
                try:
                    importlib.import_module(name)
                finally:
                    _LOADING.discard(name)
                _LOADED.add(name)


        def ensure_loaded() -> None:
            for name in _MODULES:
                if name in _LOADED or name in _LOADING:
                    continue
                _LOADING.add(name)
                try:
                    importlib.import_module(name)
                finally:
                    _LOADING.discard(name)
                _LOADED.add(name)


        def get(name: str) -> Any:
            ensure_loaded()
            return _shared.NS[name]


        def export(target: MutableMapping[str, Any], names: Iterable[str]) -> None:
            ensure_loaded()
            for name in names:
                if name in _shared.NS:
                    target[name] = _shared.NS[name]


        def namespace() -> MappingProxyType:
            ensure_loaded()
            return MappingProxyType(_shared.NS)
        '''
    )


def make_bridge() -> str:
    return textwrap.dedent(
        f'''\
        """Compatibility notes for generated split TCAD source.

        There is intentionally no generated _monolith.py. Runtime compatibility
        is provided by _bootstrap.py and _shared.py, which load the real split
        package modules in original source order and share legacy globals.
        """
        from __future__ import annotations
        '''
    )


def make_cli(manifest: Dict[str, Any]) -> str:
    flags = manifest.get("cli_selftests", [])
    flag_doc = ", ".join(str(x) for x in flags)
    return textwrap.dedent(
        f'''\
        """Command line entry point for the split TCAD simulator.

        Supported selftest flags are preserved from the monolith: {flag_doc}.
        """
        from __future__ import annotations

        import multiprocessing
        from tcad_simulator import _bootstrap as _b


        def main() -> None:
            try:
                multiprocessing.freeze_support()
            except Exception:
                pass
            _b.get("main")()


        if __name__ == "__main__":
            main()
        '''
    )


def make_compat_wrapper() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Compatibility entry point for split TCAD simulator builds."""
        from __future__ import annotations

        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent
        src = root / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        from tcad_simulator.cli import main

        if __name__ == "__main__":
            main()
        '''
    )


def extract_constant_string(item: TopLevelItem) -> Optional[str]:
    try:
        mod = ast.parse(item.source)
        node = mod.body[0]
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            return None
        value = node.value if isinstance(node, ast.AnnAssign) else node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    except Exception:
        return None
    return None


def replacement_asset_assignment(symbol: str, rel_path: str) -> str:
    return f"{symbol} = read_asset({Path(rel_path).name!r})\n"


def make_impl_source(
    module_name: str,
    items: Sequence[TopLevelItem],
    *,
    asset_map: Dict[str, str],
) -> str:
    parts = [
        f"# Generated implementation chunk: {module_name}",
        "# This file contains source extracted from tcad_simulator.py.",
        "",
    ]
    for item in sorted(items, key=lambda x: x.lineno):
        if item.name in asset_map:
            parts.append(replacement_asset_assignment(item.name, asset_map[item.name]).rstrip())
        else:
            parts.append(item.source.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


PROCESS_MODEL_MIXINS: List[Tuple[str, str, Set[str]]] = [
    (
        "process.model_state",
        "ProcessModelStateMixin",
        {
            "set_log_sink",
            "_sync_log_sink_from_history",
            "reset_state",
            "configure_domain",
            "configure_parallelism",
            "build_substrate",
            "material_at",
            "_log",
            "_rebuild_height_map",
            "_update_height_map_only",
            "_refresh_open_mask",
            "_invalidate_mesh_cache",
            "_ensure_material_z_cache",
            "_fractional_map",
            "_stable_order",
            "_hash_coords",
            "_select_weighted",
            "_apply_voxels",
            "_raise_height_map_from_mask",
            "_ensure_defect_fields",
            "_ensure_doping_field",
            "_dopant_element_symbol",
            "_ensure_dopant_species_field",
            "_clear_dopant_species_at_coords",
            "_clear_dopant_species_mask",
            "_clear_dopant_species_top_layers",
            "_laplacian",
            "_gradients",
            "_allocate_column_layers",
            "column_material_sequence",
            "_ensure_height_cache",
            "snapshot_state",
            "restore_state",
        },
    ),
    (
        "process.model_lithography",
        "ProcessModelLithographyMixin",
        {
            "spin_resist",
            "_generate_mask_density",
            "_generate_source_samples",
            "_get_tcc_modes",
            "_hopkins_aerial_image",
            "_compute_intensity_profile",
            "_simulate_dill_exposure",
            "post_exposure_bake",
            "_erode_resist_layers",
            "expose_resist",
            "develop_resist",
            "_apply_opc_bias",
            "_binary_dilate",
            "_binary_erode",
            "_estimate_cd_pitch",
        },
    ),
    (
        "process.model_deposition",
        "ProcessModelDepositionMixin",
        {
            "deposit_material",
            "_apply_deposition_dopant_top_layers",
            "_apply_deposition_dopant_flowable_volume",
            "_accessible_void_from_top",
            "_compute_accessible_volume",
            "_boundary_from_accessible",
            "_compute_neighbor_height_penalty",
            "_effective_density",
            "_smooth_sparse_values_2d",
            "_top_material_map",
            "_void_surface_normal",
            "_surface_neighbor_count",
            "_surface_relax_coordinate",
            "_estimate_feature_diameter",
            "_knudsen_diffusion_coefficient",
            "_solve_knudsen_column",
            "_group_boundary_by_column",
            "_compute_reaction_diffusion_flux",
            "_epitaxy_thickness_map",
            "_epitaxy_seed_map",
            "_critical_thickness_nm",
            "_deposit_ald",
            "_deposit_cvd",
            "_deposit_pvd",
            "_resputter_voxel",
            "_deposit_electroplate",
            "_diffusion_allowed_layers_lut",
            "_deposit_epitaxy_conformal",
            "_deposit_epitaxy",
            "_deposit_columnar_layers",
            "_deposition_uniformity_fraction",
            "_smooth_layer_map_weighted",
            "_postprocess_deposition_layers",
            "_deposit_generic",
        },
    ),
    (
        "process.model_geometry",
        "ProcessModelGeometryMixin",
        {
            "_levelset_from_binary",
            "compute_levelset",
            "compute_group_mesh",
            "_compute_material_mesh",
            "_smooth_mesh",
            "_decimate_mesh",
            "_adaptive_decimate_mesh",
            "_get_advanced_reconstructor",
            "_surface_options_key",
            "advanced_surface_ready",
            "brep_ready",
            "reconstruct_material_components",
            "get_smooth_surfaces",
            "get_brep_solids",
            "_estimate_surface_normals",
            "_collect_surface_columns",
            "get_voxel_rendering",
            "get_surface_render",
            "get_material_boxes",
            "get_material_layer_summary",
            "_compute_material_components",
            "_compute_component_mesh",
            "_smooth_patch_values",
            "_build_axis_surface_patch",
            "_component_surface_patches",
            "get_material_components_summary",
            "iter_material_surface_components",
            "get_material_surfaces",
        },
    ),
    (
        "process.model_etch_cmp",
        "ProcessModelEtchCmpMixin",
        {
            "etch_material",
            "_sputter_yield",
            "_consume_column_layers",
            "_apply_dry_etch_profile",
            "_wet_etch_controller",
            "_orientation_rate",
            "_wet_anisotropic_levelset",
            "_wet_anisotropic_faceted_etch",
            "_wet_isotropic_diffusion",
            "_dilate_mask",
            "_perform_directional_etch",
            "_perform_wet_etch",
            "_wet_micro_step",
            "_remove_resist_layers",
            "_perform_isotropic_overetch",
            "_remove_from_column",
            "_taper_neighbors",
            "_manhattan_distance",
            "cmp",
        },
    ),
    (
        "process.model_implant_thermal",
        "ProcessModelImplantThermalMixin",
        {
            "_implant_profile_distribution",
            "implant",
            "anneal",
            "_anneal_glass_reflow_densify",
            "surface_reaction",
            "_grow_oxide_from_silicon",
        },
    ),
    (
        "process.model_metrology_export",
        "ProcessModelMetrologyExportMixin",
        {
            "get_cross_section",
            "get_doping_slice",
            "measure_cd",
            "measure_feature_metrics_2d",
            "measure_column_stack",
            "measure_material_inventory",
            "measure_material_interfaces",
            "measure_component_diameters_2d",
            "measure_metrology_bundle",
            "compute_metrics",
            "export_3d_structure",
            "_sanitize_export_name",
            "_write_ascii_stl",
            "_write_tcad_geom",
            "export_cross_section_csv",
            "export_chart_data",
        },
    ),
]


def method_source_from_class_lines(lines: Sequence[str], node: ast.FunctionDef) -> str:
    start = int(getattr(node, "lineno", 0) or 0)
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        start = min([start] + [int(getattr(dec, "lineno", start) or start) for dec in decorators])
    end = int(getattr(node, "end_lineno", start) or start)
    return "\n".join(lines[start - 1 : end]).rstrip()


def split_process_model_item(item: TopLevelItem) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    tree = ast.parse(item.source)
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ProcessModel"), None)
    if cls is None:
        return {}, []
    lines = item.source.splitlines()
    method_map: Dict[str, str] = {}
    extras: List[str] = []
    init_source = ""
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            src = method_source_from_class_lines(lines, node)
            if node.name == "__init__":
                init_source = src
            else:
                method_map[node.name] = src
        else:
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", start) or start)
            if start and end:
                extras.append("\n".join(lines[start - 1 : end]).rstrip())

    assigned_methods: Set[str] = set()
    special: Dict[str, Dict[str, Any]] = {}
    mixin_imports: List[Tuple[str, str]] = []
    for module_name, mixin_class, method_names in PROCESS_MODEL_MIXINS:
        body_parts = [f"class {mixin_class}:"]
        selected = [name for name in method_map if name in method_names]
        assigned_methods.update(selected)
        if selected:
            for name in selected:
                body_parts.append(method_map[name])
                body_parts.append("")
        else:
            body_parts.append("    pass")
        special[module_name] = {
            "symbols": [mixin_class],
            "body": "\n".join(body_parts).rstrip() + "\n",
            "line_count": sum(method_map[name].count("\n") + 1 for name in selected),
        }
        mixin_imports.append((module_name, mixin_class))

    leftover = [name for name in method_map if name not in assigned_methods]
    if leftover:
        module_name = "process.model_misc"
        mixin_class = "ProcessModelMiscMixin"
        body_parts = [f"class {mixin_class}:"]
        for name in leftover:
            body_parts.append(method_map[name])
            body_parts.append("")
        special[module_name] = {
            "symbols": [mixin_class],
            "body": "\n".join(body_parts).rstrip() + "\n",
            "line_count": sum(method_map[name].count("\n") + 1 for name in leftover),
        }
        mixin_imports.append((module_name, mixin_class))

    imports = []
    for module_name, mixin_class in mixin_imports:
        imports.append(f"from tcad_simulator.{module_name} import {mixin_class}")
    bases = ", ".join(mixin for _module_name, mixin in mixin_imports) or "object"
    model_body = "\n".join(imports) + "\n\n"
    model_body += f"class ProcessModel({bases}):\n"
    if extras:
        for extra in extras:
            model_body += extra + "\n"
    model_body += init_source.rstrip() if init_source else "    pass"
    model_body += "\n"
    special["process.model"] = {
        "symbols": ["ProcessModel"],
        "body": model_body,
        "line_count": (init_source.count("\n") + 1 if init_source else 1),
    }
    return special, [module_name for module_name, _mixin in mixin_imports] + ["process.model"]


WORKER_RUNTIME_ANCHORS: List[Tuple[str, str]] = [
    ("agent_llm_test_config", "_agent_load_llm_test_config"),
    ("agent_config", "_agent_get_state"),
    ("agent_state_progress", "_agent_task_intent_heuristic"),
    ("agent_planning_graph", "_agent_llm_trace_append"),
    ("agent_llm_schema", "_agent_validate_step_blobs"),
    ("agent_step_autofix", "_agent_nkb_paths"),
    ("agent_learning", "_agent_autogen_step_meta"),
    ("agent_recipe_generation", "_webui_mask_designer_default_spec_from_exposure"),
    ("mask_tools", "PeerAgentRole"),
    ("peer_agents", "_agent_generate_chat_proposal_multi"),
    ("agent_multi_proposal", "_agent_tokenize"),
    ("retrieval_analysis", "_snapshot_blob"),
    ("recipe_state", "_is_large_domain"),
    ("cache_run", "_prepare_preview"),
    ("render_preview", "_export_timestamp_from_name"),
]


def make_worker_wrapper_source() -> str:
    return textwrap.dedent(
        '''\
        """Thin WebUI worker entry point.

        The large worker body is split by feature into webui.worker_runtime. The
        wrapper keeps the original public symbol while avoiding a hidden
        monolithic worker.py.
        """

        _TCAD_THREAD_CONN_EOF = object()


        def _webui_worker_main(
            conn: Any,
            storage_dir_str: str,
            ephemeral: bool = False,
            preview_quality: str = "high",
            default_domain: Optional[Dict[str, Any]] = None,
        ) -> None:
            from tcad_simulator.webui.worker_runtime.loader import run_worker_main

            return run_worker_main(
                conn,
                storage_dir_str,
                ephemeral=ephemeral,
                preview_quality=preview_quality,
                default_domain=default_domain,
            )
        '''
    )


def collect_nonlocal_names(source: str) -> List[str]:
    names: List[str] = []
    for match in re.finditer(r"^\s*nonlocal\s+([A-Za-z_][A-Za-z0-9_,\s]*)$", source, flags=re.MULTILINE):
        for raw in match.group(1).split(","):
            name = raw.strip()
            if name.isidentifier() and name not in names:
                names.append(name)
    return names


def make_worker_runtime_fragment_source(fragment_name: str, source: str, fragment_kind: str) -> str:
    source = source.rstrip() + "\n"
    nonlocal_names = collect_nonlocal_names(source)
    dummy_bindings = "".join(f"    {name} = None  # syntax binding for extracted nonlocal helper\n" for name in nonlocal_names)
    dummy_line_count = len(nonlocal_names)
    drop_tail = 0
    if fragment_kind == "command":
        body = "def _worker_fragment():\n" + dummy_bindings + "    while True:\n        try:\n" + source.rstrip() + "\n        except Exception:\n            pass\n"
        trim = 3 + dummy_line_count
        drop_tail = 2
    elif fragment_kind == "loop_prelude":
        body = "def _worker_fragment():\n" + dummy_bindings + source.rstrip() + "\n"
        if source.rstrip().endswith("try:"):
            body += "            pass\n        except Exception:\n            pass\n"
            drop_tail = 3
        trim = 1 + dummy_line_count
    else:
        body = "def _worker_fragment():\n" + dummy_bindings + (source.rstrip() if source.strip() else "    pass") + "\n"
        trim = 1 + dummy_line_count
    body = body.rstrip()
    return (
        f'"""Generated WebUI worker runtime fragment: {fragment_name}."""\n'
        "from __future__ import annotations\n\n"
        "import inspect\n\n"
        f"FRAGMENT_NAME = {fragment_name!r}\n"
        f"FRAGMENT_KIND = {fragment_kind!r}\n\n\n"
        f"{body}\n\n\n"
        "def get_source() -> str:\n"
        f"    lines = inspect.getsource(_worker_fragment).splitlines()[{trim}:]\n"
        f"    drop_tail = {drop_tail}\n"
        "    if drop_tail:\n"
        "        lines = lines[:-drop_tail]\n"
        '    return "\\n".join(lines) + "\\n"\n'
    )


def make_worker_runtime_loader(part_modules: Sequence[str]) -> str:
    return textwrap.dedent(
        f'''\
        """Load and execute generated WebUI worker runtime fragments."""
        from __future__ import annotations

        import __future__
        import importlib
        from typing import Any, Dict, Optional

        from tcad_simulator import _shared

        PART_MODULES = {list(part_modules)!r}
        _COMPILED = None


        def _build_worker_main():
            global _COMPILED
            if _COMPILED is not None:
                return _COMPILED
            body = "".join(str(importlib.import_module(name).get_source()) for name in PART_MODULES)
            header = (
                "def _generated_webui_worker_main(conn, storage_dir_str, "
                "ephemeral=False, preview_quality='high', default_domain=None):\\n"
            )
            ns = dict(_shared.NS)
            ns["__name__"] = "tcad_simulator.webui.worker_runtime.generated"
            ns["__package__"] = "tcad_simulator.webui"
            code = compile(header + body, "tcad_simulator/webui/worker_runtime/<generated>", "exec", flags=__future__.annotations.compiler_flag, dont_inherit=False)
            exec(code, ns)
            _COMPILED = ns["_generated_webui_worker_main"]
            return _COMPILED


        def run_worker_main(
            conn: Any,
            storage_dir_str: str,
            *,
            ephemeral: bool = False,
            preview_quality: str = "high",
            default_domain: Optional[Dict[str, Any]] = None,
        ) -> None:
            fn = _build_worker_main()
            return fn(
                conn,
                storage_dir_str,
                ephemeral=ephemeral,
                preview_quality=preview_quality,
                default_domain=default_domain,
            )
        '''
    )


def make_worker_runtime_init(part_modules: Sequence[str]) -> str:
    return textwrap.dedent(
        f'''\
        """Feature-split WebUI worker runtime fragments."""
        from __future__ import annotations

        PART_MODULES = {list(part_modules)!r}
        '''
    )


def sanitize_module_token(value: str, *, fallback: str = "part") -> str:
    token = re.sub(r"[^0-9A-Za-z_]+", "_", str(value or "").strip().lower()).strip("_")
    if not token:
        token = fallback
    if token[0].isdigit():
        token = "_" + token
    return token


def worker_command_name(test: ast.AST) -> Optional[str]:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return None
    left = test.left
    if not isinstance(left, ast.Name) or left.id != "cmd":
        return None
    op = test.ops[0]
    comp = test.comparators[0]
    if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and isinstance(comp.value, str):
        return "cmd_" + sanitize_module_token(comp.value, fallback="handler")
    if isinstance(op, ast.In) and isinstance(comp, (ast.Set, ast.Tuple, ast.List)):
        values: List[str] = []
        for elt in comp.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(sanitize_module_token(elt.value, fallback="handler"))
        if values:
            return "cmd_" + "_".join(values[:4])
    return None


def first_worker_command_try(fn: ast.FunctionDef) -> Optional[ast.Try]:
    for node in ast.walk(fn):
        if not isinstance(node, ast.While):
            continue
        try:
            is_forever = ast.unparse(node.test) == "True"
        except Exception:
            is_forever = False
        if not is_forever:
            continue
        for child in node.body:
            if not isinstance(child, ast.Try):
                continue
            if any(isinstance(sub, ast.If) and worker_command_name(sub.test) for sub in child.body):
                return child
    return None


def add_worker_part(
    parts: Dict[str, str],
    module_names: List[str],
    module_base: str,
    part_name: str,
    chunk_lines: Sequence[str],
    part_kinds: Dict[str, str],
    *,
    kind: str,
) -> None:
    if not chunk_lines:
        return
    safe_name = sanitize_module_token(part_name)
    module_name = f"{module_base}.{safe_name}"
    dedupe = 2
    unique_name = module_name
    while unique_name in parts:
        unique_name = f"{module_name}_{dedupe}"
        dedupe += 1
    parts[unique_name] = "\n".join(chunk_lines) + "\n"
    part_kinds[unique_name] = kind
    module_names.append(unique_name)


def split_worker_runtime_parts(item: TopLevelItem, *, package: str = "tcad_simulator") -> Tuple[Dict[str, str], List[str], Dict[str, str]]:
    tree = ast.parse(item.source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_webui_worker_main"), None)
    if fn is None:
        return {}, [], {}
    lines = item.source.splitlines()
    if not fn.body:
        return {}, [], {}
    body_start = int(fn.body[0].lineno)
    body_end = int(fn.end_lineno or len(lines))
    nested_lines: Dict[str, int] = {}
    nested_end_lines: Dict[str, int] = {}
    for node in ast.walk(fn):
        if node is fn or not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            continue
        nested_lines.setdefault(node.name, int(node.lineno))
        nested_end_lines.setdefault(node.name, int(getattr(node, "end_lineno", node.lineno)))

    boundaries: List[Tuple[str, int]] = [("session_setup", body_start)]
    for part_name, anchor in WORKER_RUNTIME_ANCHORS:
        line = nested_lines.get(anchor)
        if line is not None:
            boundaries.append((part_name, line))

    command_try = first_worker_command_try(fn)
    if command_try is not None:
        while_node = next((node for node in ast.walk(fn) if isinstance(node, ast.While) and command_try in node.body), None)
        if while_node is not None:
            boundaries.append(("rpc_loop_prelude", int(while_node.lineno)))
        for sub in command_try.body:
            if isinstance(sub, ast.If):
                cmd_name = worker_command_name(sub.test)
                if cmd_name:
                    boundaries.append((cmd_name, int(sub.lineno)))

    # Normalize, sort and remove duplicate line starts.
    dedup: List[Tuple[str, int]] = []
    seen_starts: Set[int] = set()
    for name, start in sorted(boundaries, key=lambda x: x[1]):
        if start in seen_starts:
            continue
        seen_starts.add(start)
        dedup.append((name, start))
    boundaries = dedup

    parts: Dict[str, str] = {}
    module_names: List[str] = []
    part_kinds: Dict[str, str] = {}
    helper_base = f"{package}.webui.worker_runtime"
    command_base = f"{package}.webui.worker_runtime.commands"
    for idx, (name, start) in enumerate(boundaries):
        end = (boundaries[idx + 1][1] - 1) if idx + 1 < len(boundaries) else body_end
        if end < start:
            continue
        chunk_lines = lines[start - 1 : end]
        if not chunk_lines:
            continue
        part_name = str(name)
        if name.startswith("cmd_"):
            add_worker_part(parts, module_names, command_base, part_name.removeprefix("cmd_"), chunk_lines, part_kinds, kind="command")
        elif name == "rpc_loop_prelude":
            add_worker_part(parts, module_names, helper_base, part_name, chunk_lines, part_kinds, kind="loop_prelude")
        else:
            add_worker_part(parts, module_names, helper_base, part_name, chunk_lines, part_kinds, kind="helper")
    return parts, module_names, part_kinds


def split_source(
    source_path: Path,
    out_dir: Path,
    manifest_path: Path,
    *,
    clean: bool,
    dedupe: str,
) -> Dict[str, Any]:
    manifest = read_json(manifest_path)
    package = str(manifest.get("package") or "tcad_simulator")
    source, module, lines, items, doc = parse_items(source_path)
    if dedupe == "conservative":
        items, removed = dedupe_items(items)
    else:
        removed = []
    assigned, unassigned = build_assignment(manifest, items)

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src_root = out_dir / "src" / package
    module_names = [str(m["name"]) for m in manifest.get("modules", [])]
    for module_name, _mixin, _methods in PROCESS_MODEL_MIXINS:
        if module_name not in module_names:
            module_names.append(module_name)
    if "process.model_misc" not in module_names:
        module_names.append("process.model_misc")
    make_package_dirs(src_root, module_names)

    write_text(src_root / "_runtime.py", runtime_preamble(lines, module) + "from tcad_simulator._asset_loader import read_asset\n")
    write_text(src_root / "_shared.py", make_shared_module())
    write_text(src_root / "_bridge_notes.py", make_bridge())
    write_text(src_root / "_asset_loader.py", make_asset_loader_module())
    write_text(out_dir / "tcad_simulator.py", make_compat_wrapper())

    by_module: Dict[str, List[TopLevelItem]] = {name: [] for name in module_names}
    item_by_name = {item.name: item for item in items}
    for item in items:
        mod_name = assigned.get(item.name)
        if mod_name:
            by_module.setdefault(mod_name, []).append(item)

    asset_map: Dict[str, str] = dict(manifest.get("assets") or {})
    asset_hashes: Dict[str, str] = {}
    for symbol, rel_path in asset_map.items():
        item = item_by_name.get(symbol)
        if not item:
            continue
        value = extract_constant_string(item)
        if value is None:
            continue
        out_path = src_root / rel_path
        write_text(out_path, value)
        asset_hashes[symbol] = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
    write_text(src_root / "assets" / "__init__.py", "# Asset package.\n")
    write_text(src_root / "assets" / "webui" / "__init__.py", "# WebUI asset package.\n")

    module_order = sorted(
        by_module.items(),
        key=lambda kv: min((item.lineno for item in kv[1]), default=10**12),
    )
    special_modules: Dict[str, Dict[str, Any]] = {}
    special_order: List[str] = []
    process_item = next((item for item in by_module.get("process.model", []) if "ProcessModel" in item.names), None)
    if process_item is not None:
        process_special, order = split_process_model_item(process_item)
        special_modules.update(process_special)
        special_order.extend(order)
        by_module["process.model"] = [item for item in by_module.get("process.model", []) if item is not process_item]

    worker_item = next((item for item in by_module.get("webui.worker", []) if "_webui_worker_main" in item.names), None)
    worker_runtime_modules: List[str] = []
    if worker_item is not None:
        worker_fragments, worker_runtime_modules, worker_fragment_kinds = split_worker_runtime_parts(worker_item, package=package)
        if worker_fragments:
            by_module["webui.worker"] = [item for item in by_module.get("webui.worker", []) if item is not worker_item]
            special_modules["webui.worker"] = {
                "symbols": ["_webui_worker_main", "_TCAD_THREAD_CONN_EOF"],
                "body": make_worker_wrapper_source(),
                "line_count": make_worker_wrapper_source().count("\n") + 1,
            }
            if "webui.worker" not in special_order:
                special_order.append("webui.worker")
            write_text(src_root / "webui" / "worker_runtime" / "__init__.py", make_worker_runtime_init(worker_runtime_modules))
            write_text(src_root / "webui" / "worker_runtime" / "commands" / "__init__.py", make_worker_runtime_init([m for m in worker_runtime_modules if ".commands." in m]))
            write_text(src_root / "webui" / "worker_runtime" / "loader.py", make_worker_runtime_loader(worker_runtime_modules))
            for module_name in worker_runtime_modules:
                rel_module = module_name.removeprefix(f"{package}.")
                fragment_name = rel_module.rsplit(".", 1)[-1]
                write_text(
                    module_path(src_root, rel_module),
                    make_worker_runtime_fragment_source(fragment_name, worker_fragments[module_name], worker_fragment_kinds[module_name]),
                )

    ordered_imports: List[str] = []
    for mod_name, bucket_items in module_order:
        if mod_name in special_modules and not bucket_items:
            continue
        symbols: List[str] = []
        for item in sorted(bucket_items, key=lambda x: x.lineno):
            for name in item.names or item.name.split(","):
                if name and name not in symbols:
                    symbols.append(name)
        if not bucket_items and mod_name not in special_modules:
            continue
        if mod_name in special_modules:
            symbols = list(dict.fromkeys(symbols + list(special_modules[mod_name].get("symbols", []))))
            body = str(special_modules[mod_name].get("body", "") or "")
        else:
            body = make_impl_source(mod_name, bucket_items, asset_map=asset_map)
        write_text(module_path(src_root, mod_name), make_real_module(f"{package}.{mod_name}", symbols, body))
        ordered_imports.append(f"{package}.{mod_name}")

    for mod_name in special_order:
        if mod_name in {imp.removeprefix(f"{package}.") for imp in ordered_imports}:
            continue
        spec = special_modules.get(mod_name)
        if not spec:
            continue
        symbols = list(spec.get("symbols", []))
        body = str(spec.get("body", "") or "")
        write_text(module_path(src_root, mod_name), make_real_module(f"{package}.{mod_name}", symbols, body))
        ordered_imports.append(f"{package}.{mod_name}")

    write_text(src_root / "_bootstrap.py", make_bootstrap(ordered_imports))
    write_text(src_root / "cli.py", make_cli(manifest))
    write_text(src_root / "__main__.py", "from tcad_simulator.cli import main\n\nif __name__ == '__main__':\n    main()\n")

    public_api = list(manifest.get("public_api") or [])
    init_lines = [
        '"""Split TCAD simulator package generated from tcad_simulator.py."""',
        "from __future__ import annotations",
        "",
        "from tcad_simulator import _bootstrap as _b",
        "",
    ]
    for sym in public_api:
        init_lines.append(f"{sym} = _b.get({sym!r})")
    init_lines.extend(["", f"__all__ = {public_api!r}", ""])
    write_text(src_root / "__init__.py", "\n".join(init_lines))

    report = build_report(
        source_path=source_path,
        out_dir=out_dir,
        manifest=manifest,
        doc=doc,
        items=items,
        assigned=assigned,
        unassigned=unassigned,
        removed=removed,
        by_module=by_module,
        special_modules=special_modules,
        asset_hashes=asset_hashes,
        worker_runtime_modules=worker_runtime_modules,
    )
    write_text(out_dir / "SPLIT_REPORT.json", json.dumps(report, ensure_ascii=False, indent=2))
    generate_docs(out_dir / "docs", report, manifest)
    build_docsite(out_dir / "docs", out_dir / "docs_html")
    write_text(out_dir / "split_tcad.sh", make_generated_shell_script())
    os.chmod(out_dir / "split_tcad.sh", 0o755)
    return report


def build_report(
    *,
    source_path: Path,
    out_dir: Path,
    manifest: Dict[str, Any],
    doc: str,
    items: List[TopLevelItem],
    assigned: Dict[str, str],
    unassigned: List[Dict[str, Any]],
    removed: List[Dict[str, Any]],
    by_module: Dict[str, List[TopLevelItem]],
    asset_hashes: Dict[str, str],
    special_modules: Optional[Dict[str, Dict[str, Any]]] = None,
    worker_runtime_modules: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    modules: Dict[str, Any] = {}
    for name, bucket in sorted(by_module.items()):
        modules[name] = {
            "symbols": [item.name for item in sorted(bucket, key=lambda x: x.lineno)],
            "symbol_count": len(bucket),
            "line_count": sum(item.line_count for item in bucket),
        }
    for name, spec in sorted((special_modules or {}).items()):
        modules[name] = {
            "symbols": list(spec.get("symbols", [])),
            "symbol_count": len(spec.get("symbols", [])),
            "line_count": int(spec.get("line_count", 0) or 0),
        }
    return {
        "schema_version": 1,
        "source": display_path(source_path),
        "output": display_path(out_dir),
        "source_sha256": sha256_text(source_path.read_text(encoding="utf-8", errors="surrogateescape")),
        "source_doc": doc[:2000],
        "top_level_item_count": len(items),
        "assigned_item_count": len(assigned),
        "unassigned_item_count": len(unassigned),
        "unassigned_items": unassigned,
        "dedupe_removed": removed,
        "asset_hashes": asset_hashes,
        "modules": modules,
        "public_api": list(manifest.get("public_api") or []),
        "cli_selftests": list(manifest.get("cli_selftests") or []),
        "worker_runtime_modules": list(worker_runtime_modules or []),
        "lost_nodes": 0,
        "notes": [
            "Generated package modules contain the extracted implementation source directly.",
            "There is intentionally no generated _monolith.py.",
            "webui.worker is a thin entry point; the worker runtime is split under webui.worker_runtime and webui.worker_runtime.commands.",
            "Large WebUI assets are extracted to package resources and exposed through webui.assets.",
            "tcad_simulator._bootstrap and _shared preserve the original single-file global namespace semantics.",
        ],
    }


def md_table(rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    sep = ["---"] * len(header)
    body = [header, sep, *rows[1:]]
    return "\n".join("| " + " | ".join(str(c) for c in row) + " |" for row in body) + "\n"


MODULE_ARCHITECTURE_NOTES: Dict[str, Tuple[str, str, str, str]] = {
    "compat": (
        "兼容层与可选依赖探测",
        "集中保存 Qt、matplotlib、cryptography、scikit-image、SciPy、numba 等可选依赖的导入结果。",
        "其他模块通过共享命名空间读取 `_HAS_QT`、`QtCore`、`QtWidgets`、`_skimage_marching_cubes` 等符号，避免在业务模块重复做导入兼容。",
        "该模块不承载业务状态；它决定功能是否启用和 fallback 路径是否可用。",
    ),
    "knowledge.engine": (
        "知识引擎门面",
        "`KnowledgeEngine` 管理存储根目录、文档摄取、PDF 摄取、检索、LLM rerank/refine 配置和 recipe 抽取。",
        "调用 `knowledge.documents` 的分块、向量索引、工艺映射与物理审计能力；WebUI/AKE 命令通过该类完成文献到 recipe 的映射。",
        "公开给包根导出，是知识增强工作流的主入口。",
    ),
    "knowledge.documents": (
        "文档处理与物理审计",
        "包含 `SemanticDocumentProcessor`、`LocalVectorIndex`、`ProcessMapper`、`PhysicsAuditor`。",
        "负责文本分块、局部向量检索、自然语言动作到 TCAD step 的映射，以及 recipe 的物理一致性审计。",
        "不直接依赖 WebUI；可由 headless 或 worker 命令调用。",
    ),
    "knowledge.skills": (
        "TCAD skills 注入",
        "解析 skill Markdown/frontmatter，构建默认技能库，按请求文本选择 skills 并注入 LLM messages。",
        "公开 `tcad_skills_get_library`、`tcad_skills_reload`、`tcad_skills_validate_dir`、`tcad_skills_maybe_inject_messages`。",
        "运行时只读取/维护 skills 目录和缓存，不应把学习结果写回源码。",
    ),
    "core.numeric": (
        "数值内核",
        "提供并行执行、距离变换、二值/加权传播、level-set、梯度、FFT blur、logistic、半球采样等底层算法。",
        "ProcessModel 的沉积、刻蚀、几何重建、掩膜代理仿真依赖该模块。",
        "numba/SciPy 可选加速由 compat 决定；缺失时使用 Python/NumPy fallback。",
    ),
    "core.physics_data": (
        "物理常数与材料工艺数据库",
        "保存光刻胶模型、TCC 模式、离子注入物种/靶材参数、湿法/干法刻蚀与化学参数。",
        "ProcessModel 的曝光、注入、退火、刻蚀、表面反应会读取这些常量和 helper。",
        "该模块是静态数据层，后续扩展材料或物理模型应优先在这里集中管理。",
    ),
    "core.geometry": (
        "几何重建",
        "包含 marching cubes 表、表面 patch、`AdvancedSurfaceReconstructor` 和 mesh/triangle 工具。",
        "ProcessModel 的 `compute_levelset`、`compute_group_mesh` 和 WebUI 预览/导出依赖这里的几何结果。",
        "输出以网格、三角片、组件 surface patch 为主，不处理 recipe 状态。",
    ),
    "core.snapshot": (
        "快照、缓存与体素压缩辅助",
        "管理 WebUI session/cache TTL、磁盘 spill、快照引用、zlib ndarray 压缩和缓存清理。",
        "用于大型模型状态、preview/cache、recipe run report 的跨进程传输与磁盘落盘。",
        "核心约束是原子写入、可恢复引用和避免 WebUI 数据目录无限增长。",
    ),
    "core.grid_io": (
        "体素网格、掩膜与 GDS IO",
        "提供 voxel grid 压缩/解压、selectivity 解析、mask 重采样、图片/GDS 载入与多边形栅格化。",
        "Process steps、mask 分析和 WebUI 导入路径都通过这里进入统一网格格式。",
        "输入输出以 NumPy 数组、mask bool/float map 和材料 id 网格为主。",
    ),
    "materials.database": (
        "材料模型与参数描述",
        "定义 `Material`、`ParameterSpec`、`MaterialDatabase`，提供材料 id、名称、颜色、启用状态和 palette。",
        "ProcessStep 参数、ProcessModel 网格材料 id、WebUI 材料列表和 Admin 材料配置都依赖该模块。",
        "这是公开 API 层之一，外部调用应通过 `MaterialDatabase` 查询材料而不是直接硬编码 id。",
    ),
    "process.model": (
        "ProcessModel 聚合类",
        "最终 `ProcessModel` 继承多个工艺域 mixin，并保留原始 `__init__`。",
        "对外表现仍是一个完整模型对象，内部实现已按 state、lithography、deposition、geometry、etch/CMP、implant/thermal、metrology/export 拆分。",
        "这是 TCAD 建模核心入口，负责网格状态、工艺执行、测量、导出和日志。",
    ),
    "process.steps": (
        "工艺步骤对象",
        "定义 `ProcessStep` 基类、Initialize/Spin/Exposure/Develop/Deposition/Epitaxy/Etch/CMP/Implant/Anneal/Oxidation 等步骤。",
        "`PROCESS_STEP_FACTORIES` 将 UI/WebUI recipe 中的步骤名映射到可执行 step 类。",
        "每个 step 暴露 `parameter_specs()` 和 `execute(model)`，由 UI、WebUI、headless 统一调用。",
    ),
    "process.headless": (
        "无界面仿真接口",
        "定义 `SimulateContext` 和 `simulate_headless`，把 recipe JSON/dict 转为步骤并运行到指定位置。",
        "用于 WebUI worker、测试、批处理和非 Qt 环境的自动仿真。",
        "通过快照缓存避免重复运行相同材料库/domain/step 签名。",
    ),
    "mask.analysis": (
        "掩膜分析与 DRC",
        "提供 1D/2D feature metrics、连通性、net rules、光刻代理显影、process window probe、tech node DRC。",
        "Mask Designer、Agent mask 生成、WebUI 预览和文档抽取后的物理审计都会使用这些函数。",
        "输出以指标 dict、DRC violation、SVG/GDS rects 和 NPROBE 几何分析结果为主。",
    ),
    "mask.layout": (
        "LayoutGraph 与 prompt fusion",
        "负责 mask spec 清洗、LayoutGraph 清洗/求解、几何存在性判断、net rules 自动生成和 prompt 到 mask spec 融合。",
        "连接自然语言/Agent 输出与可执行 exposure mask 参数。",
        "输入是 mask spec/layout graph dict，输出是可被 WebUI/ProcessStep 消费的规范化 mask spec。",
    ),
    "ui.qt_components": (
        "Qt 桌面 UI 组件",
        "包含 wafer/cross-section/heatmap canvas、MaskDesignerDialog、参数编辑器、AI Lab widget 和 `MainWindow`。",
        "只在 Qt 可用时启用；控制器通过这些组件刷新模型视图、步骤列表、参数和日志。",
        "不应承担仿真业务逻辑，业务操作通过 `SimulatorController` 和 ProcessModel 完成。",
    ),
    "ui.controller": (
        "桌面应用控制器",
        "`SimulatorController` 连接 UI 信号、recipe 操作、domain 应用、运行、导出、WebUI 开关和 Admin 管理。",
        "持有 `MaterialDatabase`、`ProcessModel`、step 列表、MainWindow 和 server manager。",
        "桌面入口启动后主要由该控制器协调所有用户动作。",
    ),
    "webui.shared": (
        "WebUI 通用工具",
        "提供时间戳、安全文件名、pickle、STL 转换、下载、LAN IP、存储根目录、ffmpeg 编码、文件锁等工具。",
        "被 WebUI server、worker、recipe 库和导出路径复用。",
        "该模块只承载通用函数和常量，不保存 session 运行状态。",
    ),
    "library.admin_config": (
        "管理员配置与加密",
        "管理 master key、部门 key、管理员密码 hash、配置 schema 迁移、工艺默认值和 process variants。",
        "Admin server 和 WebUI worker 读取该配置来应用材料、步骤默认值、权限与变体。",
        "敏感数据通过 Fernet 加密；缺少 cryptography 时相关功能会受限。",
    ),
    "library.storage": (
        "用户库与备份存储",
        "管理用户 profile、部门访问、library index、blob 文件、custom mask 缩略图、定期备份与恢复。",
        "WebUI 的 library_* 命令通过该模块上传、列出、读取、删除 step/recipe/blob。",
        "核心约束是部门隔离、原子写入和备份保留。",
    ),
    "library.literature": (
        "文献库与 RAG 文本缓存",
        "管理 PDF 文献索引、文本抽取、chunk 构建、相关文本选择、hard extract flow 和 action 到 TCAD step 映射。",
        "Agent AKE/literature import 命令与 KnowledgeEngine 共同使用。",
        "该模块处理文件系统缓存，不直接运行 TCAD 模型。",
    ),
    "library.recipe": (
        "Recipe 序列化与迁移",
        "负责 step/parameter spec 序列化、反序列化、默认 recipe、材料列表、模型 summary、session path、JSON version 迁移和材料参数规范化。",
        "WebUI、headless、library upload/import/export 都依赖这些兼容函数。",
        "旧 recipe 的材料名和 resist/develop 参数会在这里迁移到当前 schema。",
    ),
    "agent.worker_agent": (
        "Agent 责任域占位",
        "manifest 中保留 `_agent_`、NKB/NRL/RL 等 agent 规则；当前大量 agent helper 仍位于 WebUI worker runtime 闭包中。",
        "后续若抽出 `WorkerContext`，可把无闭包依赖的 agent 算法迁移到该模块。",
        "文档中应明确它是责任域，不把它误写为已完整独立实现的模块。",
    ),
    "webui.worker": (
        "WebUI worker 薄入口",
        "只导出 `_webui_worker_main` 和 `_TCAD_THREAD_CONN_EOF`，实际大执行体由 `webui.worker_runtime.loader` 按功能片段重组。",
        "保持原单文件 worker 的闭包语义，同时避免保留 1.8MB monolith。",
        "不应在这里继续增加业务逻辑，新增命令应进入 `worker_runtime/commands/`。",
    ),
    "webui.server": (
        "WebUI HTTP/session server",
        "包含 in-process/thread worker fallback、session 管理、HTTP request handler、静态资源服务和 `WebUIServerManager`。",
        "负责浏览器请求与 worker RPC 之间的桥接，以及多用户 session 生命周期。",
        "启动后 URL 通常由 `WebUIServerManager.url()` 返回。",
    ),
    "webui.admin": (
        "Admin HTTP server",
        "包含 Admin HTTP server/request handler、内置表面反应/退火扩散率表和 `AdminServerManager`。",
        "负责管理员认证、配置视图、材料/工艺 schema、preset/variant 管理。",
        "默认与 WebUI 分端口运行。",
    ),
    "webui.assets": (
        "WebUI 前端资源引用",
        "原单文件中的 HTML/CSS/JS 大字符串被提取到 `assets/webui/`，该模块通过 asset loader 暴露资源内容。",
        "server/admin 渲染页面时读取这些资源。",
        "这是资源桥接层，不是业务执行层。",
    ),
    "selftests.runner": (
        "自检入口",
        "保留 mask prompt、WebUI agent、SAQP、recipe IO 自检函数。",
        "`cli.py` 通过原 `main` 入口继续支持 selftest flags。",
        "用于验证拆分后行为和原单文件兼容。",
    ),
    "app.entrypoint": (
        "应用主入口",
        "保留原 `main()`，负责解析 CLI、自检分支、Qt 桌面启动和 WebUI/Admin 相关入口。",
        "split 包的 `cli.py` 和 `__main__.py` 最终都会调用该入口。",
        "这是兼容原 `python tcad_simulator.py` 的关键模块。",
    ),
}


PUBLIC_API_DOCS: Dict[str, Tuple[str, str, str, str]] = {
    "ProcessModel": ("process.model", "`ProcessModel(material_db, grid_shape=(192, 192, 160), voxel_size_nm=5.0, max_workers=None)`", "TCAD 工艺状态模型，管理体素网格、材料、掩膜、掺杂、缺陷、日志、测量和导出。", "典型调用：创建 `MaterialDatabase`，构造模型，执行 `build_substrate()` 和工艺方法，最后调用测量/导出方法。"),
    "MaterialDatabase": ("materials.database", "`MaterialDatabase()`", "材料库，提供材料 id、名称、颜色、启用状态、palette 和材料查询。", "所有 ProcessStep、ProcessModel、WebUI 和 Admin 材料配置都以它为材料来源。"),
    "Material": ("materials.database", "`Material` dataclass", "单个材料记录，包含名称、颜色、密度/介电/成分等材料属性。", "通常通过 `MaterialDatabase.material(id)` 获取。"),
    "ParameterSpec": ("materials.database", "`ParameterSpec` dataclass", "工艺步骤参数的 UI/schema 描述，记录名称、类型、范围、默认值和选项。", "ProcessStep.parameter_specs() 返回该对象列表。"),
    "ProcessStep": ("process.steps", "`ProcessStep(material_db)`", "工艺步骤基类，统一 `parameter_specs()`、`describe()`、`execute(model)` 协议。", "自定义步骤应保持该协议，以便 UI/WebUI/headless 统一运行。"),
    "InitializeWaferStep": ("process.steps", "`InitializeWaferStep`", "初始化晶圆/衬底步骤。", "执行时调用模型衬底构建能力。"),
    "SpinResistStep": ("process.steps", "`SpinResistStep`", "旋涂光刻胶步骤。", "封装 resist 厚度、类型、soft bake 等参数。"),
    "ExposureStep": ("process.steps", "`ExposureStep(material_db)`", "曝光步骤，支持 procedural mask、custom mask 和 image mask。", "可通过 `set_custom_mask()` 或 `set_image_mask()` 绑定掩膜。"),
    "DevelopStep": ("process.steps", "`DevelopStep`", "显影步骤。", "与曝光/PEB 结果配合改变 resist open mask。"),
    "PostExposureBakeStep": ("process.steps", "`PostExposureBakeStep`", "曝光后烘烤步骤。", "封装 PEB 温度、时间和扩散相关参数。"),
    "DepositionStep": ("process.steps", "`DepositionStep`", "沉积步骤。", "覆盖 ALD/CVD/PVD/electroplate/generic 等沉积路径。"),
    "SelectiveEpitaxyStep": ("process.steps", "`SelectiveEpitaxyStep`", "选择性外延步骤。", "使用 seed/material/选择性参数驱动外延沉积。"),
    "EtchStep": ("process.steps", "`EtchStep`", "刻蚀步骤。", "封装 dry/wet、方向性、选择比、overetch 等参数。"),
    "CMPProcessStep": ("process.steps", "`CMPProcessStep(material_db)`", "化学机械平坦化步骤。", "依赖材料选择性和目标高度/厚度控制。"),
    "ImplantationStep": ("process.steps", "`ImplantationStep`", "离子注入步骤。", "使用物种、能量、剂量、倾角等参数改变掺杂/损伤场。"),
    "AnnealStep": ("process.steps", "`AnnealStep`", "退火步骤。", "用于扩散、激活、损伤恢复和热处理。"),
    "OxidationNitridationStep": ("process.steps", "`OxidationNitridationStep`", "氧化/氮化/表面反应步骤。", "通过热预算和反应参数改变表面材料。"),
    "PROCESS_STEP_FACTORIES": ("process.steps", "`Dict[str, Callable[[MaterialDatabase], ProcessStep]]`", "步骤名称到步骤类工厂的注册表。", "recipe 反序列化、UI 添加步骤和 WebUI 导入都通过该表创建步骤。"),
    "SimulateContext": ("process.headless", "`SimulateContext(...)`", "headless 运行上下文，保存材料库、domain、缓存、并行度和存储根目录。", "重复批处理时复用上下文可减少初始化和快照开销。"),
    "simulate_headless": ("process.headless", "`simulate_headless(recipe, *, ctx, upto_step_index=None, snapshot_compression='dense-zlib')`", "无界面运行 recipe，并返回模型 summary、日志、快照/测量等结果。", "适合自动测试、WebUI worker、批处理和脚本调用。"),
    "KnowledgeEngine": ("knowledge.engine", "`KnowledgeEngine(storage_root=None, *, dim=2048)`", "知识增强入口，负责文档摄取、PDF 摄取、检索和从文献/查询抽取 recipe。", "可配置 LLM adapter，也可使用本地检索和物理审计路径。"),
    "tcad_skills_get_library": ("knowledge.skills", "`tcad_skills_get_library(skills_dir, *, create_default=True)`", "读取或创建 TCAD skills 库。", "用于给 Agent/LLM 注入领域技能上下文。"),
    "tcad_skills_reload": ("knowledge.skills", "`tcad_skills_reload(skills_dir)`", "清理并重新载入 skills 缓存。", "修改 skill 文件后调用。"),
    "tcad_skills_validate_dir": ("knowledge.skills", "`tcad_skills_validate_dir(skills_dir, *, create_default=True)`", "验证 skills 目录结构和 Markdown/frontmatter。", "用于启动前或 Admin 配置检查。"),
    "tcad_skills_maybe_inject_messages": ("knowledge.skills", "`tcad_skills_maybe_inject_messages(messages, cfg, *, role=None)`", "按配置和用户请求选择 skills，并把注入消息加入 LLM messages。", "WebUI Agent 调用 LLM 前使用。"),
    "SimulatorController": ("ui.controller", "`SimulatorController()`", "桌面 UI 控制器，协调 MainWindow、ProcessModel、recipe 步骤、导出和 WebUI 开关。", "Qt 桌面应用启动后的主要控制对象。"),
    "MainWindow": ("ui.qt_components", "`MainWindow(material_db)`", "Qt 主窗口，负责步骤列表、参数编辑、视图刷新、日志和用户操作控件。", "通常由 `SimulatorController` 创建并绑定。"),
    "WebUIServerManager": ("webui.server", "`WebUIServerManager(host='0.0.0.0', port=8765, max_users=10, ...)`", "WebUI server 管理器，管理 HTTP server、session、worker、静态资源和客户端性能参数。", "调用 `start()` 后用 `url()` 获取访问地址，调用 `stop()` 关闭。"),
    "AdminServerManager": ("webui.admin", "`AdminServerManager(host='0.0.0.0', port=8766, storage_root=None, token_ttl_s=28800)`", "Admin server 管理器，提供管理员认证、配置视图、schema 和材料/工艺管理。", "与 WebUI 分端口运行，通常用于后台配置。"),
}


def module_source_path(module_name: str) -> str:
    return "src/tcad_simulator/" + "/".join(module_name.split(".")) + ".py"


def module_domain(module_name: str) -> str:
    return module_name.split(".", 1)[0]


def module_symbols_preview(info: Dict[str, Any], limit: int = 10) -> str:
    symbols: List[str] = []
    for raw in info.get("symbols", []):
        for part in str(raw).split(","):
            symbol = part.strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return ", ".join(symbols[:limit]) if symbols else "无顶层符号或仅作为责任域占位"


def manifest_symbol_index(manifest: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for mod in manifest.get("modules", []):
        mod_name = str(mod.get("name") or "")
        for sym in mod.get("symbols", []):
            out[str(sym)] = mod_name
    for module_name, mixin_class, _methods in PROCESS_MODEL_MIXINS:
        out[mixin_class] = module_name
    out["ProcessModelMiscMixin"] = "process.model_misc"
    return out


def module_doc_tuple(module_name: str) -> Tuple[str, str, str, str]:
    if module_name in MODULE_ARCHITECTURE_NOTES:
        return MODULE_ARCHITECTURE_NOTES[module_name]
    if module_name.startswith("process.model_"):
        title = "ProcessModel 工艺域 mixin"
        return (
            title,
            "从原 `ProcessModel` 按方法集合抽取出的 mixin 文件。",
            "由 `process.model.ProcessModel` 继承并组合，对外仍表现为一个模型类。",
            "该模块的边界由 `PROCESS_MODEL_MIXINS` 定义，未匹配方法进入 `process.model_misc`。",
        )
    return (
        "生成模块",
        "该模块由 manifest 或 splitter 规则从单文件源码抽取。",
        "通过 `_bootstrap.py` 与 `_shared.py` 共享 legacy 全局符号。",
        "维护时应先查看 `SPLIT_REPORT.json` 的符号和行数，再调整 manifest。",
    )


PROCESS_PHYSICS_DETAILS: List[Dict[str, Any]] = [
    {
        "name": "Initialize Wafer",
        "module": "process.model_state",
        "step_class": "InitializeWaferStep",
        "method": "build_substrate",
        "kernel": "衬底/BOX/晶向初始化",
        "parameters": "material, thickness_nm, substrate_type, orientation, temperature, doping_type, doping_concentration, box_material, box_thickness_nm",
        "state": "grid, height_map, open_mask, wafer_orientation, temperature hint, dopant fields",
        "physics": [
            "把纳米厚度按 `ceil(thickness_nm / voxel_size_nm)` 离散为体素层。",
            "Bulk wafer 直接填充衬底；SOI 根据 BOX 厚度插入 buried oxide 并保留上层硅。",
            "orientation 写入模型状态，供湿法各向异性刻蚀和表面反应选择晶向相关路径。",
            "初始化后重建 height map 和 open mask，保证后续曝光、沉积和刻蚀只作用于可见表面。",
        ],
        "formula": r"$N_z = \lceil t_{nm} / \Delta x_{nm} \rceil$；height map 取每个 $(x,y)$ 柱中最高非 void 体素。",
        "paths": "process/steps.py:InitializeWaferStep -> process/model_state.py:build_substrate",
    },
    {
        "name": "Spin Resist",
        "module": "process.model_lithography",
        "step_class": "SpinResistStep",
        "method": "spin_resist",
        "kernel": "光刻胶旋涂、软烘收缩和化学状态初始化",
        "parameters": "material, resist_type, thickness_nm, softbake_temp_c, softbake_time_s, Dill A/B/C, Mack n/k, diffusion_length_nm",
        "state": "resist_material_id, resist_type, resist_diffusion_length_nm, _resist_state, grid, open_mask",
        "physics": [
            "对 wafer 顶面做柱状厚度分配，必要时使用高斯平滑模拟旋涂流平。",
            "软烘参数进入厚度收缩和酸扩散长度的经验近似。",
            "positive resist 初始化 PAC/polymer/PAG；negative resist 初始化交联相关 polymer 状态。",
            "写入光刻胶体素后刷新 open mask，使曝光只在光刻胶区域计算剂量。",
        ],
        "formula": r"$t_{vox}=\mathrm{round}(t_{resist}/\Delta x)$，PEB 默认扩散长度会被 clamp 到稳定范围。",
        "paths": "process/steps.py:SpinResistStep -> process/model_lithography.py:spin_resist",
    },
    {
        "name": "Exposure",
        "module": "process.model_lithography",
        "step_class": "ExposureStep",
        "method": "expose_resist",
        "kernel": "OPC bias、Hopkins/TCC aerial image、Dill 曝光化学",
        "parameters": "pattern, critical_dimension, pitch, wavelength, NA, sigma, focus, dose, mask_override, opc_config",
        "state": "resist_exposure, _resist_state.dose_map_mj_cm2, acid/PAC/polymer fields, logs",
        "physics": [
            "procedural mask 通过 oversampling 生成密度图；custom/image mask 先 resample 到模型 XY 尺寸。",
            "OPC 通过 binary erode/dilate 进行轻量 bias，auto bias 约随 wavelength/NA 标度变化。",
            "Hopkins 路径用 TCC modes 对 mask FFT 做传递函数卷积并归一化 aerial image。",
            "自定义 mask 使用稳定的高斯 aerial 近似，避免大型 mask 下 TCC 计算成本过高。",
            "Dill A/B/C、resist n/k、standing-wave/attenuation 信息进入光强剖面和 PAC/PAG 转换。",
        ],
        "formula": r"$I(x,y)=\sum_m |\mathcal{F}^{-1}\{\mathcal{F}(M)\,H_m\}|^2 p_m$；$E=I\cdot t$；Dill 近似用 $A,B,C$ 更新 PAC/acid。",
        "paths": "process/steps.py:ExposureStep -> process/model_lithography.py:expose_resist",
    },
    {
        "name": "Post Exposure Bake",
        "module": "process.model_lithography",
        "step_class": "PostExposureBakeStep",
        "method": "post_exposure_bake",
        "kernel": "Arrhenius 酸扩散、deprotection 和潜像平滑",
        "parameters": "temperature_c, time_s, diffusion_prefactor, activation_energy, deprotect_rate",
        "state": "_resist_state acid/PAC/polymer, resist_diffusion_length_nm, logs",
        "physics": [
            "扩散常数按 Arrhenius 形式从温度、D0、Ea 计算。",
            "SciPy 可用时使用 gaussian_filter 做加权扩散；否则使用稳定 fallback。",
            "positive/negative resist 在酸扩散后分别更新 inhibitor 或 polymer 状态。",
            "PEB 输出日志给出 D、sigma vox、acid mean/max，便于调试曝光和显影窗口。",
        ],
        "formula": r"$D=D_0\exp(-E_a/k_BT)$，$\sigma_{vox}\approx\sqrt{2Dt}/\Delta x$。",
        "paths": "process/steps.py:PostExposureBakeStep -> process/model_lithography.py:post_exposure_bake",
    },
    {
        "name": "Develop",
        "module": "process.model_lithography",
        "step_class": "DevelopStep",
        "method": "develop_resist",
        "kernel": "Mack 显影速率、2.5D 前沿传播和 undercut 平滑",
        "parameters": "develop_time_s, rate_nm_min, threshold_mj_cm2",
        "state": "grid, current_heights, open_mask, _resist_state, mesh cache",
        "physics": [
            "根据剂量/阈值和 resist chemistry 计算局部显影速率。",
            "沿每个柱自上而下累计 time_per_voxel，得到可移除深度。",
            "使用横向 Gaussian 平滑作为 undercut/contrast-limited lateral erosion 代理。",
            "移除 resist 后同步清理 PAC/PAG/polymer，并刷新 open mask 与 mesh cache。",
        ],
        "formula": r"$t_{voxel}=\Delta x/R(x,y,z)$，移除满足 $\sum t_{voxel}\le t_{dev}$ 的顶层 resist。",
        "paths": "process/steps.py:DevelopStep -> process/model_lithography.py:develop_resist",
    },
    {
        "name": "Deposition",
        "module": "process.model_deposition",
        "step_class": "DepositionStep",
        "method": "deposit_material",
        "kernel": "ALD/CVD/PVD/电镀/通用沉积、可达 void 和反应扩散 flux",
        "parameters": "material, thickness_nm, method, temperature_c, coverage, directionality, sticking_coeff, surface_diff_nm, gap_fill_bias_nm, resputter_prob, dopant",
        "state": "grid, height_map, dopant_species, mesh cache, diffusion LUT cache",
        "physics": [
            "coverage mask 决定 full wafer 或 open mask 区域沉积。",
            "accessible void 从顶部入口做 binary propagation，避免在封闭空洞内非物理填充。",
            "ALD 高 conformal fraction，CVD 结合 reaction-diffusion depth attenuation，PVD 结合 line-of-sight、surface diffusion 和 resputter。",
            "Knudsen diffusion column solver 估计深沟槽 precursor 供给；gap_fill_bias 控制 super/sub-conformal 行为。",
            "ALD dopant 和 flowable dopant reservoir 会写入 dopant species/active fields 供后续 anneal drive-in。",
        ],
        "formula": r"$D_K \propto r\sqrt{T/M}$；local flux 约为 $R_s C(z)$，并按 surface normal、depth 和 microloading 加权。",
        "paths": "process/steps.py:DepositionStep -> process/model_deposition.py:deposit_material",
    },
    {
        "name": "Selective Epitaxy",
        "module": "process.model_deposition",
        "step_class": "SelectiveEpitaxyStep",
        "method": "_deposit_epitaxy",
        "kernel": "半导体 seed surface 选择性外延、临界厚度和 3D conformal component",
        "parameters": "material, thickness_nm, seed_materials, seed_surface_groups, directionality, temperature_c, selectivity, doping",
        "state": "grid, height_map, dopant fields, last_implant_species, mesh cache",
        "physics": [
            "只在 Semiconductor surface group 或指定 seed material/group 上生长。",
            "3D conformal epitaxy 在可达 trench/sidewall 内做 seed-front propagation。",
            "top-growth component 用 normals、valley-fill、surface diffusion smoothing 近似外延台阶流/成面。",
            "critical thickness 和 material conformality 控制可沉积厚度与 sidewall component 比例。",
            "原位 doping 字符串写入对应 dopant species/concentration。",
        ],
        "formula": r"$t_{epi}=t_{conformal}+t_{top}$；可达深度由 geodesic distance 和 diffusion-length LUT 限制。",
        "paths": "process/steps.py:SelectiveEpitaxyStep -> process/model_deposition.py:_deposit_epitaxy",
    },
    {
        "name": "Etch",
        "module": "process.model_etch_cmp",
        "step_class": "EtchStep",
        "method": "etch_material",
        "kernel": "dry directional etch、wet isotropic/anisotropic etch、multi-material selectivity",
        "parameters": "target_material, chemistry, time_s, rate, selectivity_to_resist, sidewall_angle, bias_voltage, pressure, neutral_flux, passivation_rate, stop_material",
        "state": "grid, height_map, open_mask, damage/dopant cleanup, resist chemistry cleanup",
        "physics": [
            "dry etch 根据 bias voltage 估计 ion flux，并把 total time 分成多个稳定 micro-step。",
            "multi-material selectivity 通过 material id -> relative rate map 处理，stop layer 从 targets 中移除。",
            "directional etch 按 column top-down removal 和 sidewall taper 扩展邻域。",
            "wet etch controller 对 KOH/TMAH/VHF/hot phosphoric 等走湿法路径，使用 BFS/attenuation 处理可达目标。",
            "刻蚀移除材料时同步清理 dopants、damage、resist chemistry 并局部重建 height map。",
        ],
        "formula": r"$\Phi_i\approx1.2\times10^{16}(V_b/100)^{0.6}$；wet attenuation 使用 $\exp(-d/\lambda)$。",
        "paths": "process/steps.py:EtchStep -> process/model_etch_cmp.py:etch_material",
    },
    {
        "name": "CMP",
        "module": "process.model_etch_cmp",
        "step_class": "CMPProcessStep",
        "method": "cmp",
        "kernel": "Preston polishing、pattern density、pad contact 和 material selectivity",
        "parameters": "target_height_nm, time_s, pressure_psi, pad_speed, preston_coeff, selectivity_spec, target_material",
        "state": "grid, height_map, damage/dopant cleanup, logs",
        "physics": [
            "target height 转为 target_layer，只有高于目标且 pad 接触区域可被抛光。",
            "planarization_length 随 pressure 变化，决定 Gaussian smoothing 和 pad contact clearance。",
            "selectivity map 决定可移除材料；未列入材料可作为 stop layer。",
            "pattern density 让稀疏区域获得更高有效压力，同时 protrusion term 强化凸起去除。",
            "nm_budget 累计到体素层移除，残余纳米预算保留到下一步迭代。",
        ],
        "formula": r"Preston proxy: $RR=K_pPV\cdot S_m\cdot f_{density}\cdot f_{contact}\cdot f_{topo}$。",
        "paths": "process/steps.py:CMPProcessStep -> process/model_etch_cmp.py:cmp",
    },
    {
        "name": "Ion Implant",
        "module": "process.model_implant_thermal",
        "step_class": "ImplantationStep",
        "method": "implant",
        "kernel": "fast 2.5D LSS/SRIM-inspired profile 或 Monte Carlo BCA fallback",
        "parameters": "species, dose_cm2, energy_kev, tilt_deg, rotation_deg, plasma_density_cm3",
        "state": "dopant_concentration, active_dopants, dopant_species, damage_concentration, interstitials, vacancies, last_implant_species",
        "physics": [
            "fast profile 根据能量、物种质量和 top material density 估算 projected range Rp 与 straggle。",
            "tilt 和 plasma density 通过 lateral Gaussian halo 扩展剂量图。",
            "multi-material stopping 使用等效 stopping depth u(z)=∫dz/range_scale(material)。",
            "dopant species 写入总掺杂和 species field，非 dopant implant 主要增加 damage/defects。",
            "BCA fallback 使用 mean free path、Kinchin-Pease defects 和 damage-dependent dechanneling 进行采样。",
        ],
        "formula": r"$C(x,y,z)=Dose\cdot L(x,y)\cdot p(z)$，$p(z)$ 为 Rp/straggle 控制的归一化深度分布。",
        "paths": "process/steps.py:ImplantationStep -> process/model_implant_thermal.py:implant",
    },
    {
        "name": "Anneal",
        "module": "process.model_implant_thermal",
        "step_class": "AnnealStep",
        "method": "anneal",
        "kernel": "Arrhenius diffusion、defect recombination、dopant activation/reactivation、glass reflow",
        "parameters": "temperature_c, time_s, ambient",
        "state": "dopant_concentration, active_dopants, damage/interstitial/vacancy, dopant species, grid for reflow/oxidation",
        "physics": [
            "用 built-in 或 user table 取得 dopant diffusivity，并按材料 scaling 做 material-aware diffusion。",
            "ROI 限定在 dopants/defects 附近，避免大 domain 长退火导致 UI 卡顿。",
            "defect recombination 处理 I/V 场，damage 会增强 I/V 初值。",
            "BIC/precipitation/reactivation 只在 semiconductor 中生效，避免 oxide 中虚假消耗掺杂。",
            "高温下 flowable dielectric 做 viscous reflow + densification，平滑 topography 并按 shrink 比例更新体素。",
        ],
        "formula": r"$D=D_0\exp(-E_a/k_BT)$；Gaussian proxy 使用 $\sigma=\sqrt{2D\Delta t}/\Delta x$。",
        "paths": "process/steps.py:AnnealStep -> process/model_implant_thermal.py:anneal",
    },
    {
        "name": "Oxidation/Nitridation",
        "module": "process.model_implant_thermal",
        "step_class": "OxidationNitridationStep",
        "method": "surface_reaction",
        "kernel": "Deal-Grove-like proxy、stoichiometry/density volume expansion、product transport gate",
        "parameters": "reaction, target, temperature_c, time_s, ambient, pressure_atm, target_thickness_nm",
        "state": "grid material conversion, height_map, dopant segregation/cleanup, damage cleanup, surface_reaction history",
        "physics": [
            "支持 oxidation、nitridation、oxynitride，product 映射到 SiO2、Si3N4 或 SiON 类材料。",
            "消耗厚度由 reactant/product stoichiometry、density 和 molar mass 计算，保持质量/体积近似守恒。",
            "kinetics 使用 Deal-Grove-like proxy，wet oxidation、dry oxidation、NO/N2O/NH3 等 ambient 使用不同速率分支。",
            "transport gate 禁止沿已有 oxide/nitride 横向长距离爬行，避免深孔中非物理 oxidation path。",
            "新生成 product 区域清理 defects/damage，并做 dopant segregation/推挤近似。",
        ],
        "formula": r"Deal-Grove proxy: $x^2+Ax=B(t+\tau)$；Si 到 SiO2 体积膨胀约 $2.27\times$。",
        "paths": "process/steps.py:OxidationNitridationStep -> process/model_implant_thermal.py:surface_reaction",
    },
]


PROCESS_ARCHITECTURE_FLOWS: Dict[str, Dict[str, Any]] = {
    "Initialize Wafer": {
        "nodes": [
            ("Schema", "InitializeWaferStep schema: material/thickness/substrate/BOX/orientation/doping"),
            ("Material", "MaterialDatabase resolve wafer/BOX material ids and groups"),
            ("Quantize", "thickness_nm and voxel_size_nm -> substrate/BOX layer counts"),
            ("Stack", "bulk or SOI column stack allocation in material grid"),
            ("Doping", "initial dopant concentration and species fields"),
            ("Orientation", "wafer orientation stored for wet etch and surface reaction"),
            ("Refresh", "rebuild height_map/open_mask/material_z_cache"),
            ("Output", "wafer grid ready for lithography/deposition/etch"),
        ],
        "edges": [
            ("Schema", "Material", "material names"),
            ("Material", "Quantize", "ids and density hints"),
            ("Quantize", "Stack", "voxel layers"),
            ("Schema", "Doping", "dopant params"),
            ("Schema", "Orientation", "crystal orientation"),
            ("Stack", "Doping", "occupied substrate"),
            ("Doping", "Refresh", "state fields"),
            ("Orientation", "Refresh", "model hints"),
            ("Refresh", "Output", "consistent surface state"),
        ],
    },
    "Spin Resist": {
        "nodes": [
            ("Schema", "SpinResistStep schema: material/type/thickness/softbake/Dill/Mack"),
            ("Material", "resolve resist material id and chemistry parameters"),
            ("Thickness", "softbake shrink and thickness quantization"),
            ("Topography", "top surface columns with optional gaussian flow leveling"),
            ("Chemistry", "ResistChemistryState PAC/PAG/polymer/acid initialization"),
            ("Apply", "allocate resist voxels above current surface"),
            ("Refresh", "refresh open_mask and invalidate mesh cache"),
            ("Output", "resist film ready for exposure"),
        ],
        "edges": [
            ("Schema", "Material", "resist material"),
            ("Schema", "Thickness", "film and bake params"),
            ("Material", "Chemistry", "Dill/Mack defaults"),
            ("Thickness", "Topography", "voxel layer budget"),
            ("Topography", "Apply", "surface columns"),
            ("Chemistry", "Apply", "chemistry arrays"),
            ("Apply", "Refresh", "grid changed"),
            ("Refresh", "Output", "exposable resist"),
        ],
    },
    "Exposure": {
        "nodes": [
            ("Schema", "ExposureStep schema: pattern/CD/pitch/lambda/NA/sigma/focus/dose/OPC"),
            ("Mask", "procedural/custom/image mask density and resampling"),
            ("OPC", "binary erode/dilate OPC bias and auto bias"),
            ("Source", "source samples, partial coherence and TCC modes"),
            ("Aerial", "Hopkins FFT aerial image or gaussian fallback"),
            ("Intensity", "standing-wave/attenuation intensity profile through resist"),
            ("Dill", "Dill A/B/C update of PAC/PAG/acid/polymer"),
            ("Fields", "dose_map, resist_exposure and exposure logs"),
            ("Output", "latent image for PEB and develop"),
        ],
        "edges": [
            ("Schema", "Mask", "pattern inputs"),
            ("Mask", "OPC", "mask density"),
            ("Schema", "Source", "optics params"),
            ("OPC", "Aerial", "biased mask"),
            ("Source", "Aerial", "transfer functions"),
            ("Aerial", "Intensity", "normalized image"),
            ("Intensity", "Dill", "local dose"),
            ("Dill", "Fields", "chemistry fields"),
            ("Fields", "Output", "latent image"),
        ],
    },
    "Post Exposure Bake": {
        "nodes": [
            ("Schema", "PEB schema: temperature/time/D0/Ea/deprotect rate"),
            ("Diffusivity", "Arrhenius acid diffusivity and sigma_vox"),
            ("Filter", "gaussian_filter or stable fallback spatial smoothing"),
            ("Chemistry", "positive deprotection or negative polymer update"),
            ("State", "write acid/PAC/polymer and diffusion length"),
            ("Logs", "record D, sigma, acid mean/max"),
            ("Output", "conditioned latent image for develop"),
        ],
        "edges": [
            ("Schema", "Diffusivity", "thermal budget"),
            ("Diffusivity", "Filter", "diffusion length"),
            ("Filter", "Chemistry", "smoothed acid"),
            ("Schema", "Chemistry", "resist model"),
            ("Chemistry", "State", "updated arrays"),
            ("State", "Logs", "diagnostics"),
            ("Logs", "Output", "develop input"),
        ],
    },
    "Develop": {
        "nodes": [
            ("Schema", "DevelopStep schema: time/rate/threshold"),
            ("Rate", "Mack or threshold develop-rate map from dose and chemistry"),
            ("Front", "top-down time_per_voxel accumulation per column"),
            ("Undercut", "lateral gaussian smoothing for undercut and contrast limit"),
            ("Remove", "remove resist voxels and clear chemistry arrays"),
            ("Mask", "recompute current_heights and open_mask"),
            ("Cache", "invalidate mesh cache and append logs"),
            ("Output", "transferred open mask for etch/deposition"),
        ],
        "edges": [
            ("Schema", "Rate", "developer params"),
            ("Rate", "Front", "local removal speed"),
            ("Front", "Undercut", "develop depth"),
            ("Undercut", "Remove", "removal mask"),
            ("Remove", "Mask", "grid changed"),
            ("Mask", "Cache", "surface changed"),
            ("Cache", "Output", "pattern transferred"),
        ],
    },
    "Deposition": {
        "nodes": [
            ("Schema", "DepositionStep schema: material/thickness/method/temp/coverage/dopant"),
            ("Material", "resolve target material id and optional dopant species"),
            ("Coverage", "full wafer or open_mask coverage selection"),
            ("Accessible", "top-accessible void map by binary propagation"),
            ("Branch", "method branch: ALD/CVD/PVD/electroplate/generic"),
            ("Flux", "surface normals, Knudsen diffusion, reaction-diffusion flux, microloading"),
            ("Dopant", "ALD/flowable/in-situ dopant reservoir fields"),
            ("Apply", "conformal, columnar, sidewall or gap-fill voxel allocation"),
            ("Refresh", "height_map/open_mask/mesh cache and logs"),
            ("Output", "deposited material for next thermal/etch/metrology step"),
        ],
        "edges": [
            ("Schema", "Material", "material inputs"),
            ("Schema", "Coverage", "coverage mode"),
            ("Coverage", "Accessible", "allowed columns"),
            ("Material", "Branch", "method material pair"),
            ("Accessible", "Flux", "reachable boundary"),
            ("Branch", "Flux", "method physics"),
            ("Flux", "Apply", "voxel candidates"),
            ("Material", "Dopant", "dopant spec"),
            ("Dopant", "Apply", "species concentration"),
            ("Apply", "Refresh", "grid and fields changed"),
            ("Refresh", "Output", "stable surface"),
        ],
    },
    "Selective Epitaxy": {
        "nodes": [
            ("Schema", "SelectiveEpitaxyStep schema: material/seeds/temp/selectivity/doping"),
            ("Seed", "semiconductor group and explicit seed material map"),
            ("Reach", "geodesic reachable trench/sidewall seed-front propagation"),
            ("Critical", "lattice mismatch critical thickness and conformality limit"),
            ("Growth", "conformal sidewall plus top-growth and surface diffusion smoothing"),
            ("Doping", "in-situ dopant species and concentration fields"),
            ("Apply", "write epitaxial material voxels only on valid seeds"),
            ("Refresh", "height_map/mesh cache/logs"),
            ("Output", "selective epi geometry and doped regions"),
        ],
        "edges": [
            ("Schema", "Seed", "seed rules"),
            ("Seed", "Reach", "valid surfaces"),
            ("Schema", "Critical", "material/temperature"),
            ("Reach", "Growth", "reachable front"),
            ("Critical", "Growth", "thickness cap"),
            ("Schema", "Doping", "dopant string"),
            ("Growth", "Apply", "growth map"),
            ("Doping", "Apply", "in-situ doping"),
            ("Apply", "Refresh", "state changed"),
            ("Refresh", "Output", "epi complete"),
        ],
    },
    "Etch": {
        "nodes": [
            ("Schema", "EtchStep schema: target/chemistry/time/rate/selectivity/stop/bias/passivation"),
            ("Resolve", "target material ids, stop materials and selectivity map"),
            ("Mask", "open_mask dilation and accessible exposed surface"),
            ("Branch", "dry directional, wet isotropic or wet anisotropic controller"),
            ("Dry", "ion flux, sputter yield, sidewall taper and micro-steps"),
            ("Wet", "BFS attenuation, orientation rate and faceted level-set path"),
            ("Remove", "column consumption, isotropic overetch and sidewall taper"),
            ("Cleanup", "clear dopants, damage and resist chemistry in removed voxels"),
            ("Refresh", "height_map/open_mask/mesh cache/history"),
            ("Output", "etched profile for CMP/metrology/deposition"),
        ],
        "edges": [
            ("Schema", "Resolve", "materials"),
            ("Resolve", "Mask", "targets and stops"),
            ("Mask", "Branch", "exposed surface"),
            ("Branch", "Dry", "plasma chemistries"),
            ("Branch", "Wet", "wet chemistries"),
            ("Dry", "Remove", "directional rates"),
            ("Wet", "Remove", "diffusion/orientation rates"),
            ("Remove", "Cleanup", "removed cells"),
            ("Cleanup", "Refresh", "fields and grid"),
            ("Refresh", "Output", "profile updated"),
        ],
    },
    "CMP": {
        "nodes": [
            ("Schema", "CMPProcessStep schema: target_height/time/pressure/pad/preston/selectivity"),
            ("Selectivity", "material selectivity map and stop-layer interpretation"),
            ("Topography", "height_map, pattern density and pad contact clearance"),
            ("Rate", "Preston KpPV with material, density, contact and protrusion factors"),
            ("Budget", "nm removal budget converted to voxel layers and residuals"),
            ("Remove", "top column material removal above target height"),
            ("Cleanup", "clear damage/dopants in polished voxels"),
            ("Refresh", "height_map/open_mask/mesh cache/logs"),
            ("Output", "planarized surface for next lithography/deposition"),
        ],
        "edges": [
            ("Schema", "Selectivity", "material rules"),
            ("Schema", "Topography", "process settings"),
            ("Selectivity", "Rate", "relative rates"),
            ("Topography", "Rate", "contact/density"),
            ("Rate", "Budget", "nm per iteration"),
            ("Budget", "Remove", "voxel layers"),
            ("Remove", "Cleanup", "changed cells"),
            ("Cleanup", "Refresh", "state fields"),
            ("Refresh", "Output", "planar result"),
        ],
    },
    "Ion Implant": {
        "nodes": [
            ("Schema", "ImplantationStep schema: species/dose/energy/tilt/rotation/plasma"),
            ("Species", "species and target database: mass, charge, diffusivity, stopping"),
            ("Stopping", "multi-material effective stopping depth u(z)"),
            ("Profile", "Rp/straggle normalized projected-range distribution"),
            ("Lateral", "tilt, rotation and plasma halo lateral gaussian dose map"),
            ("Dopant", "dopant_concentration, active_dopants and species fields"),
            ("Damage", "Kinchin-Pease defects, interstitials, vacancies and amorphization"),
            ("Fallback", "Monte Carlo BCA fallback for sampled collision cascades"),
            ("Output", "implanted dopant and damage fields for anneal"),
        ],
        "edges": [
            ("Schema", "Species", "ion params"),
            ("Species", "Stopping", "target params"),
            ("Stopping", "Profile", "depth coordinate"),
            ("Schema", "Lateral", "tilt/plasma"),
            ("Profile", "Dopant", "depth dose"),
            ("Lateral", "Dopant", "xy dose"),
            ("Species", "Damage", "nuclear stopping"),
            ("Profile", "Damage", "damage depth"),
            ("Schema", "Fallback", "BCA path"),
            ("Fallback", "Damage", "sampled cascades"),
            ("Dopant", "Output", "species fields"),
            ("Damage", "Output", "defect fields"),
        ],
    },
    "Anneal": {
        "nodes": [
            ("Schema", "AnnealStep schema: temperature/time/ambient and optional user tables"),
            ("Diffusivity", "built-in or user D0/Ea tables with material scaling"),
            ("ROI", "dopant/defect ROI selection to bound long thermal runs"),
            ("Diffusion", "species-aware gaussian/ROI dopant diffusion"),
            ("Defects", "interstitial/vacancy recombination and damage recovery"),
            ("Activation", "activation/reactivation, BIC and precipitation in semiconductors"),
            ("Reflow", "flowable dielectric viscous reflow and densification"),
            ("Fields", "write dopant, active dopant, defect and material fields"),
            ("Refresh", "height_map/mesh cache/history/logs"),
            ("Output", "thermally updated device for reaction/metrology/next step"),
        ],
        "edges": [
            ("Schema", "Diffusivity", "thermal budget"),
            ("Diffusivity", "ROI", "diffusion length"),
            ("ROI", "Diffusion", "bounded field window"),
            ("ROI", "Defects", "defect window"),
            ("Diffusion", "Activation", "new concentration"),
            ("Defects", "Activation", "damage coupling"),
            ("Schema", "Reflow", "high temperature glass"),
            ("Activation", "Fields", "electrical dopants"),
            ("Reflow", "Fields", "grid/topography"),
            ("Fields", "Refresh", "state changed"),
            ("Refresh", "Output", "anneal complete"),
        ],
    },
    "Oxidation/Nitridation": {
        "nodes": [
            ("Schema", "OxidationNitridationStep schema: reaction/target/temp/time/ambient/pressure/thickness"),
            ("Tables", "surface reaction tables and product mapping to SiO2/Si3N4/SiON"),
            ("Surface", "top/sidewall accessible reactant surface selection"),
            ("Kinetics", "Deal-Grove-like growth x^2 + A x = B(t + tau) with ambient scaling"),
            ("Stoich", "stoichiometry, density and molar-mass volume expansion"),
            ("Transport", "product transport gate prevents oxide/nitride crawling into closed paths"),
            ("Convert", "reactant consumption and product voxel material conversion"),
            ("Segregation", "dopant segregation or push-out and damage cleanup"),
            ("Refresh", "height_map/open_mask/mesh cache/history/logs"),
            ("Output", "oxide/nitride/surface-reaction layer for next process"),
        ],
        "edges": [
            ("Schema", "Tables", "reaction params"),
            ("Tables", "Surface", "target/product ids"),
            ("Surface", "Kinetics", "available interface"),
            ("Schema", "Kinetics", "thermal budget"),
            ("Kinetics", "Stoich", "growth thickness"),
            ("Stoich", "Transport", "volume expansion"),
            ("Transport", "Convert", "allowed paths"),
            ("Convert", "Segregation", "new product cells"),
            ("Segregation", "Refresh", "fields changed"),
            ("Refresh", "Output", "reaction complete"),
        ],
    },
}


def mermaid_label(value: Any) -> str:
    text = str(value)
    text = text.replace("`", "").replace('"', "'").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def process_mermaid_diagram(detail: Dict[str, Any]) -> str:
    name = str(detail["name"])
    spec = PROCESS_ARCHITECTURE_FLOWS.get(name)
    if not spec:
        spec = {
            "nodes": [
                ("Schema", f"{detail['step_class']} parameters and MaterialDatabase"),
                ("Method", f"ProcessModel.{detail['method']}"),
                ("Physics", f"physical kernel: {detail['kernel']}"),
                ("State", f"state fields: {detail['state']}"),
                ("Refresh", "height map, mesh cache, history and logs"),
                ("Output", "metrology, preview or next process"),
            ],
            "edges": [
                ("Schema", "Method", "execute"),
                ("Method", "Physics", "compute"),
                ("Physics", "State", "write"),
                ("State", "Refresh", "invalidate"),
                ("Refresh", "Output", "ready"),
            ],
        }
    token = sanitize_module_token(name, fallback="process")
    lines = ["```mermaid", "flowchart TD"]
    for node_id, label in spec["nodes"]:
        lines.append(f'    {token}_{node_id}["{mermaid_label(label)}"]')
    for edge in spec["edges"]:
        src = f"{token}_{edge[0]}"
        dst = f"{token}_{edge[1]}"
        if len(edge) >= 3 and str(edge[2]).strip():
            lines.append(f"    {src} -->|{mermaid_label(edge[2])}| {dst}")
        else:
            lines.append(f"    {src} --> {dst}")
    lines.append("```")
    return "\n".join(lines)


def process_physics_detail_markdown(detail: Dict[str, Any], *, heading_level: int = 3, include_diagram: bool = True) -> str:
    heading = "#" * max(1, heading_level)
    physics_lines = "\n".join(f"- {line}" for line in detail["physics"])
    diagram = "\n\n" + process_mermaid_diagram(detail) if include_diagram else ""
    return (
        f"{heading} {detail['name']}\n\n"
        f"- 所属模块：`{detail['module']}`\n"
        f"- Step 类：`{detail['step_class']}`\n"
        f"- ProcessModel 方法：`{detail['method']}`\n"
        f"- 参数入口：{detail['parameters']}\n"
        f"- 状态更新：{detail['state']}\n"
        f"- 源码路径：`{detail['paths']}`\n"
        f"- 近似公式：{detail['formula']}\n\n"
        f"{physics_lines}"
        f"{diagram}\n"
    )


def process_detail_by_module() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for detail in PROCESS_PHYSICS_DETAILS:
        out.setdefault(str(detail["module"]), []).append(detail)
    return out


def process_physics_table() -> str:
    rows = [["工艺", "模块", "Step", "模型方法", "物理内核", "主要状态"]]
    for detail in PROCESS_PHYSICS_DETAILS:
        rows.append(
            [
                detail["name"],
                detail["module"],
                detail["step_class"],
                detail["method"],
                detail["kernel"],
                detail["state"],
            ]
        )
    return md_table(rows)


def process_physics_sections(*, compact: bool = False) -> str:
    parts = [process_physics_detail_markdown(detail, heading_level=3, include_diagram=True).strip() for detail in PROCESS_PHYSICS_DETAILS]
    return "\n\n".join(parts) + "\n"


def build_module_architecture_doc(
    *,
    module_rows: Sequence[Tuple[str, Dict[str, Any]]],
    manifest: Dict[str, Any],
) -> str:
    process_by_module = process_detail_by_module()
    module_sections: List[str] = ["# 每一个模块的架构细节\n"]
    module_sections.append(
        textwrap.dedent(
            """\
            本文档由 `tools/split_tcad.py` 生成，描述拆分后每个模块的职责边界、调用关系和维护约束。`process.*` 模块额外加入从源码实现审查得到的物理内核说明，避免只停留在文件清单层面。

            ## Process 物理内核总表

            """
        ).rstrip()
        + "\n\n"
        + process_physics_table()
    )
    module_sections.append(
        textwrap.dedent(
            """\

            ## Process 数据流总览

            ```mermaid
            flowchart TD
                Recipe[Recipe / UI Step] --> Step[ProcessStep.execute]
                Step --> Model[ProcessModel]
                Model --> Grid[material grid]
                Model --> Height[height_map / current_heights]
                Model --> Mask[open_mask / coverage mask]
                Model --> Resist[resist chemistry fields]
                Model --> Dopant[dopant / active dopant / species]
                Model --> Defects[damage / interstitials / vacancies]
                Grid --> Geometry[level-set / mesh / gbuffer]
                Height --> EtchCMP[etch / CMP front propagation]
                Mask --> LithoDep[lithography / deposition selectivity]
                Dopant --> Anneal[anneal diffusion activation]
                Defects --> Anneal
            ```
            """
        ).rstrip()
    )
    for name, info in module_rows:
        name_s = str(name)
        title, summary, flow, note = module_doc_tuple(name_s)
        manifest_mod = next((m for m in manifest.get("modules", []) if str(m.get("name")) == name_s), {})
        prefixes = ", ".join(str(x) for x in manifest_mod.get("prefixes", [])) or "无"
        section = [
            f"## `{name_s}`",
            "",
            f"- 路径：`{module_source_path(name_s)}`",
            f"- 职责：{title}",
            f"- 真实符号数：{info.get('symbol_count', 0)}",
            f"- 估算源行数：{info.get('line_count', 0)}",
            f"- 归类前缀：{prefixes}",
            f"- 主要符号：{module_symbols_preview(info, limit=20)}",
            f"- 架构说明：{summary}",
            f"- 数据/调用关系：{flow}",
            f"- 维护备注：{note}",
        ]
        if name_s in process_by_module:
            section.append("")
            section.append("### 嵌入代码实现中的物理细节")
            for detail in process_by_module[name_s]:
                section.extend(
                    [
                        "",
                        f"#### {detail['name']}",
                        "",
                        f"- Step/方法：`{detail['step_class']}` -> `{detail['method']}`",
                        f"- 物理内核：{detail['kernel']}",
                        f"- 参数入口：{detail['parameters']}",
                        f"- 状态更新：{detail['state']}",
                        f"- 近似公式：{detail['formula']}",
                        f"- 源码路径：`{detail['paths']}`",
                    ]
                )
                section.extend(f"- {line}" for line in detail["physics"])
                section.extend(["", process_mermaid_diagram(detail)])
        if name_s == "process.model":
            section.extend(
                [
                    "",
                    "### ProcessModel 组合关系",
                    "",
                    "`ProcessModel` 保留对外单一类形态，但内部继承 state、lithography、deposition、geometry、etch/CMP、implant/thermal、metrology/export 七个 mixin。外部仍通过 `ProcessModel(...)` 调用，不直接依赖 mixin 类。",
                    "",
                    "```mermaid",
                    "classDiagram",
                    "    class ProcessModel",
                    "    class ProcessModelStateMixin",
                    "    class ProcessModelLithographyMixin",
                    "    class ProcessModelDepositionMixin",
                    "    class ProcessModelGeometryMixin",
                    "    class ProcessModelEtchCmpMixin",
                    "    class ProcessModelImplantThermalMixin",
                    "    class ProcessModelMetrologyExportMixin",
                    "    ProcessModelStateMixin <|-- ProcessModel",
                    "    ProcessModelLithographyMixin <|-- ProcessModel",
                    "    ProcessModelDepositionMixin <|-- ProcessModel",
                    "    ProcessModelGeometryMixin <|-- ProcessModel",
                    "    ProcessModelEtchCmpMixin <|-- ProcessModel",
                    "    ProcessModelImplantThermalMixin <|-- ProcessModel",
                    "    ProcessModelMetrologyExportMixin <|-- ProcessModel",
                    "```",
                ]
            )
        module_sections.append("\n".join(section).rstrip() + "\n")
    return "\n\n".join(module_sections).rstrip() + "\n"


def build_module_contents_doc(*, module_rows: Sequence[Tuple[str, Dict[str, Any]]]) -> str:
    process_by_module = process_detail_by_module()
    sections: List[str] = [
        "# 项目内容与模块职责\n\n"
        "本文件按模块列出真实生成内容，并对 `process.*` 模块补充更细的物理实现说明、参数接口和状态影响。"
    ]
    for name, info in module_rows:
        name_s = str(name)
        section = [
            f"## `{name_s}`",
            "",
            f"- 路径：`{module_source_path(name_s)}`",
            f"- 符号数：{info.get('symbol_count', 0)}",
            f"- 估算源行数：{info.get('line_count', 0)}",
            f"- 主要符号：{module_symbols_preview(info, limit=40)}",
        ]
        if name_s in process_by_module:
            section.extend(["", "### Process 物理内容清单"])
            for detail in process_by_module[name_s]:
                section.extend(
                    [
                        "",
                        f"#### `{detail['step_class']}` / `{detail['method']}`",
                        "",
                        f"- 工艺名称：{detail['name']}",
                        f"- 物理内核：{detail['kernel']}",
                        f"- 输入参数：{detail['parameters']}",
                        f"- 写入状态：{detail['state']}",
                        f"- 数值/物理近似：{detail['formula']}",
                        f"- 源码定位：`{detail['paths']}`",
                        "- 实现要点：",
                    ]
                )
                section.extend(f"  - {line}" for line in detail["physics"])
                section.extend(["", process_mermaid_diagram(detail)])
        elif name_s.startswith("process."):
            title, summary, flow, note = module_doc_tuple(name_s)
            section.extend(
                [
                    "",
                    "### Process 支撑职责",
                    "",
                    f"- 支撑角色：{title}",
                    f"- 内容说明：{summary}",
                    f"- 调用关系：{flow}",
                    f"- 维护边界：{note}",
                ]
            )
        sections.append("\n".join(section).rstrip())
    sections.append(
        textwrap.dedent(
            """\

            ## Process 状态字段速查

            | 状态字段 | 类型/形态 | 主要写入者 | 主要用途 |
            | --- | --- | --- | --- |
            | `grid` | 3D uint16 material id voxel grid | initialize/deposition/etch/CMP/surface reaction | 几何、渲染、材料库存、所有工艺的主状态 |
            | `height_map` | 2D top index map | state/deposition/etch/CMP/reaction | 快速定位表面、open mask、column operation |
            | `open_mask` | 2D bool mask | lithography/state | 曝光、沉积、刻蚀的覆盖区域 |
            | `_resist_state` | PAC/PAG/polymer/acid arrays | spin/exposure/PEB/develop | 光刻胶化学状态和显影速率 |
            | `dopant_concentration` | 3D float field | implant/deposition/anneal/reaction | 总掺杂浓度 |
            | `active_dopants` | 3D float field | implant/anneal | 电活性掺杂估计 |
            | `dopant_species` | species keyed fields | implant/epi/ALD/anneal | 多物种掺杂追踪 |
            | `damage_concentration` | 3D float field | implant/etch/anneal/reaction | 注入损伤、非晶化和恢复 |
            | `interstitials` / `vacancies` | 3D float fields | implant/anneal | 点缺陷复合和扩散增强 |
            | `_mesh_cache` | material/group mesh cache | geometry/export/render | 避免重复 marching cubes 和 surface patch 重建 |
            """
        ).strip()
    )
    return "\n\n".join(sections).rstrip() + "\n"


def build_process_model_doc() -> str:
    process_rows = [["模块", "Mixin", "方法数", "代表方法", "职责"]]
    process_notes = {
        "process.model_state": "模型状态、domain、材料网格、掺杂/缺陷场、快照和恢复。",
        "process.model_lithography": "旋涂、曝光、Dill 模型、Hopkins/TCC、PEB、显影和 OPC bias。",
        "process.model_deposition": "ALD/CVD/PVD、电镀、外延、反应扩散、void 可达性和沉积后处理。",
        "process.model_geometry": "level-set、mesh、surface patch、B-Rep readiness、组件 surface 输出。",
        "process.model_etch_cmp": "干法/湿法刻蚀、方向性刻蚀、各向异性湿法、CMP 和材料移除。",
        "process.model_implant_thermal": "离子注入、退火、表面反应、氧化和玻璃回流致密化。",
        "process.model_metrology_export": "截面、CD、材料库存、接口、metrology bundle、STL/TCAD 几何/CSV/chart 导出。",
    }
    for module_name, mixin_class, methods in PROCESS_MODEL_MIXINS:
        process_rows.append([module_name, mixin_class, len(methods), ", ".join(sorted(methods)[:12]), process_notes.get(module_name, "")])
    return (
        "# ProcessModel 工艺模型架构\n\n"
        "`ProcessModel` 是 TCAD 核心状态对象。拆分后 `process.model.ProcessModel` 保留原始构造函数，并继承多个工艺域 mixin；因此外部调用方式不变，但源码维护边界更清晰。\n\n"
        + md_table(process_rows)
        + "\n## 工艺物理内核索引\n\n"
        + process_physics_table()
        + "\n## 工艺物理内核详解\n\n"
        + process_physics_sections()
        + textwrap.dedent(
            """\

            ## 调用生命周期

            1. 创建 `MaterialDatabase`。
            2. 创建 `ProcessModel(material_db, grid_shape, voxel_size_nm, max_workers)`。
            3. 调用 `build_substrate()` 或执行 `InitializeWaferStep` 建立初始晶圆。
            4. 依次执行 ProcessStep，或直接调用 `spin_resist()`、`expose_resist()`、`deposit_material()`、`etch_material()` 等模型方法。
            5. 使用 `measure_*()`、`compute_metrics()`、`get_cross_section()`、`compute_group_mesh()` 做测量和可视化。
            6. 使用 `snapshot_state()`、`restore_state()`、`export_3d_structure()` 等完成缓存、恢复和导出。

            ## 数值稳定性和边界策略

            - 所有厚度最终都会量化到 `voxel_size_nm`，低于体素分辨率的目标厚度会通过 ceil、残余预算或日志警告处理。
            - 大多数 3D 操作只在 ROI 内运行，减少 WebUI session 中的交互延迟。
            - SciPy/numba/skimage 可用时走加速路径，缺失时保持 NumPy/Python fallback。
            - 移除或材料转换时同步清理 dopant/damage/resist chemistry，避免后续测量读取到悬空物理场。
            - mesh、preview、snapshot 使用 cache/spill 机制控制大数组重复计算和跨进程传输开销。

            ## 维护规则

            新增 ProcessModel 方法时，应优先放入匹配工艺域 mixin；只有无法归类的方法才进入 `process.model_misc`。公开行为仍通过 `ProcessModel` 暴露，避免外部直接依赖 mixin 类。
            """
        )
    )


def worker_runtime_doc(report: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    modules = [str(m) for m in report.get("worker_runtime_modules", [])]
    helpers = [m.rsplit(".", 1)[-1] for m in modules if ".commands." not in m and not m.endswith(".loader")]
    commands = [m.rsplit(".", 1)[-1] for m in modules if ".commands." in m]
    return helpers, commands


def worker_command_category(command: str) -> str:
    if command in {"init", "shutdown_quit_exit", "ui_state", "log_tail", "llm_quota_status"}:
        return "会话与状态"
    if command.startswith("agent_"):
        return "AI Agent / AKE / Mask Designer"
    if command.startswith("library_") or command.startswith("profile_"):
        return "用户库、部门与 profile"
    if command.startswith("recipe_") or command in {"get_recipe", "set_step", "save", "load_autosave", "history_list", "history_load", "load_recipe_ephemeral"}:
        return "Recipe 编辑与持久化"
    if command.startswith("video_") or command.startswith("export") or command in {"exports_list", "slice", "render_gbuffer", "preview_manifest", "preview_elements", "mask_preview_step"}:
        return "预览、渲染与导出"
    if command in {"run_step", "run_all", "run_to_run_until", "reset", "undo", "apply_domain"}:
        return "仿真执行"
    return "其他"


def build_extended_readme(
    *,
    report: Dict[str, Any],
    manifest: Dict[str, Any],
    modules: Dict[str, Any],
    public_api: Sequence[str],
    module_rows: Sequence[Tuple[str, Dict[str, Any]]],
    domain_summary: Dict[str, Dict[str, int]],
    worker_helpers: Sequence[str],
    worker_commands: Sequence[str],
    nav: str,
) -> str:
    package = str(manifest.get("package") or "tcad_simulator")
    source = str(report.get("source") or "tcad_simulator.py")
    output = str(report.get("output") or "tcad_simulator_split")
    symbol_to_module = manifest_symbol_index(manifest)

    domain_role = {
        "agent": "AI agent 责任域和后续状态对象重构落点。",
        "app": "原单文件 CLI/GUI/WebUI 主入口。",
        "compat": "可选依赖和跨平台兼容探测。",
        "core": "数值计算、几何重建、快照和网格 IO。",
        "knowledge": "文档摄取、RAG、物理审计和 skills。",
        "library": "Admin 配置、用户库、文献库和 recipe 迁移。",
        "mask": "掩膜分析、DRC、LayoutGraph 和 prompt fusion。",
        "materials": "材料数据模型和材料数据库。",
        "process": "TCAD 工艺模型、工艺步骤和 headless 仿真。",
        "selftests": "原自检入口。",
        "ui": "Qt 桌面 UI。",
        "webui": "Web server、Admin server、worker runtime 和前端资源。",
    }
    domain_rows = [["领域", "模块数", "符号数", "估算源码行数", "架构角色"]]
    for domain, info in sorted(domain_summary.items()):
        domain_rows.append([domain, info["modules"], info["symbols"], info["lines"], domain_role.get(domain, "拆分生成领域。")])

    module_table = [["模块", "源码路径", "职责", "主要符号"]]
    for name, info in module_rows:
        title, _summary, _flow, _note = module_doc_tuple(str(name))
        module_table.append([name, module_source_path(str(name)), title, module_symbols_preview(info, limit=8)])

    api_rows = [["API", "模块", "调用形式", "核心用途"]]
    for sym in public_api:
        mod_name, call, purpose, _note = PUBLIC_API_DOCS.get(
            str(sym),
            (symbol_to_module.get(str(sym), "未知"), f"`{sym}`", "从包根导出的兼容 API。", ""),
        )
        api_rows.append([sym, mod_name, call, purpose])

    mixin_note = {
        "process.model_state": "模型状态、domain、材料网格、掺杂/缺陷场、快照和恢复。",
        "process.model_lithography": "旋涂、曝光、PEB、显影、OPC 和光刻成像。",
        "process.model_deposition": "ALD/CVD/PVD/电镀/外延/通用沉积和反应扩散。",
        "process.model_geometry": "level-set、mesh、surface patch、组件和 B-Rep readiness。",
        "process.model_etch_cmp": "干法/湿法刻蚀、方向性刻蚀、各向异性湿法和 CMP。",
        "process.model_implant_thermal": "注入、退火、表面反应、氧化和致密化。",
        "process.model_metrology_export": "截面、CD、材料接口、metrology bundle 和导出。",
    }
    mixin_rows = [["Mixin 模块", "类", "方法数", "职责"]]
    for module_name, mixin_class, methods in PROCESS_MODEL_MIXINS:
        mixin_rows.append([module_name, mixin_class, len(methods), mixin_note.get(module_name, "")])

    process_rows = [["工艺", "物理内核", "状态影响", "源码路径"]]
    for detail in PROCESS_PHYSICS_DETAILS:
        process_rows.append([detail["name"], detail["kernel"], detail["state"], detail["paths"]])

    helper_role = {
        "session_setup": "初始化 session 路径、缓存目录、MaterialDatabase、ProcessModel、Admin 配置和默认 recipe。",
        "agent_llm_test_config": "加载 LLM 测试配置和 provider/model 连接参数。",
        "agent_config": "维护当前 UI state 内的 agent 配置、quota 和可用性。",
        "agent_state_progress": "维护 agent progress、task intent、事件流和 UI 可观察状态。",
        "agent_planning_graph": "管理 multi-agent planning graph、LLM trace 和 proposal merge。",
        "agent_llm_schema": "定义/校验 step schema、JSON 输出、LLM repair 和安全清洗。",
        "agent_step_autofix": "对 agent 生成的 step/meta 做保守修复、材料归一化和 loop 结构对齐。",
        "agent_learning": "承载 NKB/NRL/RL、fix memory、规则库和自动练习经验检索。",
        "agent_recipe_generation": "根据用户目标、mask、知识片段和物理规则生成 recipe proposal。",
        "mask_tools": "Mask Designer、SmartMaskAgent、mask spec 默认值、DRC 与 prompt fusion。",
        "peer_agents": "PEER-C multi-agent orchestration，协调 planner/engineer/critic/executor 等角色。",
        "agent_multi_proposal": "tokenize、retrieval、multi proposal 合成与 assistant 输出整理。",
        "retrieval_analysis": "snapshot blob、run report、metrology 和 retrieved context 分析。",
        "recipe_state": "session/history/autosave/current recipe 的读写、迁移和持久化。",
        "cache_run": "step cache、undo、incremental run、recipe station 和 run report。",
        "render_preview": "preview cache、gbuffer、camera、mesh bbox、元素分布和渲染输出。",
        "rpc_loop_prelude": "worker RPC 主循环的 poll/recv/cmd/payload/rid 前置逻辑。",
    }
    helper_rows = [["Worker runtime helper", "角色"]]
    for helper in worker_helpers:
        helper_rows.append([helper, helper_role.get(helper, "Worker 闭包内自然功能片段，由 loader 按顺序重组。")])
    command_rows = [["RPC 命令", "类别", "文件"]]
    for command in worker_commands:
        command_rows.append([command, worker_command_category(command), f"webui/worker_runtime/commands/{command}.py"])

    file_tree = """tcad_simulator_split/
├── tcad_simulator.py
├── split_tcad.sh
├── SPLIT_REPORT.json
├── VERIFY_REPORT.json
├── docs/
└── src/tcad_simulator/
    ├── _runtime.py / _shared.py / _bootstrap.py
    ├── assets/webui/
    ├── core/
    ├── process/
    ├── webui/worker_runtime/
    └── library/knowledge/mask/ui/"""

    sections: List[str] = []
    sections.append(f"""# TCAD Simulator 技术总览

本 README 面向 `tcad_simulator.py` 主程序，目标是让读者只阅读一个文件就能理解系统的物理工艺内核、软件架构、WebUI 操作逻辑、3D 渲染算法、文件系统布局、公开接口和维护边界。内容来自当前源码、可选开发报告、manifest 和文档生成规则。

## 关键事实

- 原始文件：`{source}`
- 输出目录：`{output}`
- 包名：`{package}`
- 顶层对象数：{report.get('top_level_item_count')}
- 已归类对象数：{report.get('assigned_item_count')}
- 未归类对象数：{report.get('unassigned_item_count')}
- 模块数：{len(modules)}
- 公开 API 数：{len(public_api)}
- WebUI worker runtime 分片数：{len(report.get('worker_runtime_modules', []))}
- WebUI worker helper 分片数：{len(worker_helpers)}
- WebUI worker RPC 命令数：{len(worker_commands)}

可选开发报告会把主程序中的职责域映射到模块视图，便于检查 `ProcessModel`、WebUI worker、materials、mask、knowledge 和 UI 的边界；实际发布和维护仍以 `tcad_simulator.py` 为准。

## 文档导航

{nav}
""")
    sections.append(f"""## 一分钟启动和验证

```bash
python3 -m py_compile "{source}"
TCAD_SKIP_QT=1 MPLBACKEND=Agg python3 "{source}" --mask-prompt-selftest --n 3 --res 128
```

```python
import runpy
ns = runpy.run_path("{source}", run_name="tcad_docs_probe")
print("MaterialDatabase" in ns, "ProcessModel" in ns)
```
""")
    sections.append("""## 系统总架构

```mermaid
flowchart TD
    User[用户/Python/浏览器] --> Entry[tcad_simulator.py 或 python -m tcad_simulator]
    Entry --> Package[tcad_simulator 包根]
    Package --> Bootstrap[_bootstrap 原顺序导入]
    Bootstrap --> Shared[_shared.NS legacy 命名空间]
    Bootstrap --> Runtime[_runtime imports 与可选依赖]
    Shared --> Knowledge[knowledge RAG/AKE/skills]
    Shared --> Core[core numeric/geometry/snapshot/grid_io]
    Shared --> Materials[materials MaterialDatabase]
    Shared --> Process[process ProcessModel/steps/headless]
    Shared --> Mask[mask DRC/LayoutGraph/prompt fusion]
    Shared --> Library[library admin/storage/literature/recipe]
    Shared --> UI[ui Qt/controller]
    Shared --> WebUI[webui server/admin/worker_runtime/assets]
    Process --> Core
    Process --> Materials
    Process --> Mask
    WebUI --> Process
    WebUI --> Library
    WebUI --> Knowledge
```

```mermaid
sequenceDiagram
    participant Importer as import tcad_simulator
    participant Init as __init__.py
    participant Boot as _bootstrap
    participant Shared as _shared.NS
    participant Mod as split modules
    Importer->>Init: request public API
    Init->>Boot: get(symbol)
    Boot->>Mod: import in original source order
    Mod->>Shared: prepare_module(globals)
    Mod->>Mod: execute extracted source
    Mod->>Shared: finalize_module(exports)
    Shared-->>Boot: symbol available
    Boot-->>Init: return API object
```
""")
    sections.append("## 领域和模块汇总\n\n" + md_table(domain_rows) + "\n## 模块架构索引\n\n" + md_table(module_table))

    module_detail_lines = ["## 每个模块的架构细节\n"]
    for name, info in module_rows:
        title, summary, flow, note = module_doc_tuple(str(name))
        module_detail_lines.append(f"""### `{name}`

- 路径：`{module_source_path(str(name))}`
- 领域：`{module_domain(str(name))}`
- 职责：{title}
- 符号数：{info.get('symbol_count', 0)}
- 估算行数：{info.get('line_count', 0)}
- 代表符号：{module_symbols_preview(info, limit=18)}
- 架构说明：{summary}
- 调用关系：{flow}
- 维护边界：{note}
""")
    sections.append("\n".join(module_detail_lines))

    sections.append("""## Process 工艺总流程

```mermaid
flowchart LR
    Recipe[Recipe JSON/UI Steps] --> Factory[PROCESS_STEP_FACTORIES]
    Factory --> Step[ProcessStep.execute]
    Step --> Model[ProcessModel]
    Model --> State[model_state]
    Model --> Litho[model_lithography]
    Model --> Dep[model_deposition]
    Model --> Etch[model_etch_cmp]
    Model --> Thermal[model_implant_thermal]
    Model --> Geom[model_geometry]
    Model --> Metro[model_metrology_export]
    Geom --> Render[3D preview/mesh/export]
    Metro --> Report[metrics/CSV/STL/TCAD geom]
```
""" + "\n" + md_table(mixin_rows) + "\n## 每个 Process 工艺的物理内核\n\n" + md_table(process_rows))

    for detail in PROCESS_PHYSICS_DETAILS:
        sections.append(
            process_physics_detail_markdown(detail, heading_level=3, include_diagram=True).rstrip()
            + "\n\n维护该工艺时必须同时考虑参数 schema、材料解析、体素层更新、日志/快照和 WebUI recipe 序列化。Qt UI、WebUI 和 headless recipe 最终都通过相同的 `ProcessStep.execute(model)` 协议触发该内核。\n"
        )

    sections.append("""## 3D 渲染、几何重建和导出算法

TCAD 模型内部主状态是三维体素材料网格、掩膜、掺杂/缺陷场和 height map。渲染路径经过 mesh、surface patch、gbuffer、slice 或导出格式转换。

```mermaid
flowchart TD
    Grid[Material voxel grid] --> LevelSet[compute_levelset / signed distance]
    LevelSet --> MC[marching_cubes]
    MC --> Mesh[triangles / vertices / normals]
    Mesh --> Smooth[smoothing / decimation]
    Smooth --> Components[material components / surface patches]
    Components --> Preview[WebUI preview / gbuffer]
    Components --> Export[STL / TCAD geom / chart data]
    Grid --> Slice[cross-section slice]
    Grid --> Inventory[material inventory / interfaces]
    Doping[Doping field] --> DopingSlice[get_doping_slice]
```

```mermaid
flowchart LR
    Worker[worker_runtime.render_preview] --> Prepare[_prepare_preview]
    Prepare --> Camera[_camera / mesh bbox]
    Prepare --> ReadGeom[_read_tcad_geom]
    ReadGeom --> GBuffer[_render_gbuffer]
    GBuffer --> RGB[RGB / depth / normal / element maps]
    RGB --> Cache[preview cache]
    Cache --> Browser[WebGL / Canvas UI]
```
""")

    sections.append("""## WebUI 操作逻辑

WebUI 是多用户 session + worker RPC 架构。HTTP server 负责静态资源、session 和请求路由；worker 负责模型状态、recipe、仿真、preview、export、library 和 agent。

```mermaid
sequenceDiagram
    participant Browser as Browser
    participant HTTP as WebUIRequestHandler
    participant Session as WebUISession
    participant Worker as worker_runtime
    participant Model as ProcessModel
    participant Store as Session Storage
    Browser->>HTTP: POST /api cmd + payload
    HTTP->>Session: resolve cookie/session
    Session->>Worker: send message
    Worker->>Model: mutate/run/render/export
    Worker->>Store: autosave/cache/history/exports
    Worker-->>Session: conn.send result
    Session-->>HTTP: response
    HTTP-->>Browser: JSON/binary/asset
```

```mermaid
flowchart TD
    Browser[Web UI] --> Commands[RPC command map]
    Commands --> Recipe[recipe edit commands]
    Commands --> Run[run_step / run_all / run_to]
    Commands --> Preview[preview_manifest / render_gbuffer / slice]
    Commands --> Export[export_zip / video / images]
    Commands --> Agent[agent_chat / refine / iterate / apply]
    Commands --> Library[library step/recipe/blob]
    Recipe --> Autosave[autosave/history]
    Run --> StepCache[step cache and run reports]
    Preview --> PreviewCache[preview cache]
    Agent --> Knowledge[KnowledgeEngine / skills / literature]
    Export --> ExportsDir[exports dir]
```
""" + "\n## Worker runtime helper 分片\n\n" + md_table(helper_rows) + "\n## Worker RPC 命令清单\n\n" + md_table(command_rows))

    sections.append(f"""## 文件系统和运行时数据

```mermaid
flowchart TD
    Root[tcad_simulator_split] --> Src[src/tcad_simulator]
    Root --> Docs[docs]
    Root --> Report[SPLIT_REPORT.json]
    Src --> Assets[assets/webui]
    Src --> ProcessDir[process]
    Src --> WebDir[webui]
    WebDir --> Runtime[worker_runtime]
    Runtime --> Commands[commands]
    UserData[TCAD Web Data root] --> Sessions[sessions]
    Sessions --> History[history]
    Sessions --> PreviewCache[preview cache]
    Sessions --> Exports[exports]
    UserData --> Library[encrypted library]
    UserData --> Literature[literature db / rag chunks]
    UserData --> Admin[admin config / keys]
```

```text
{file_tree}
```

运行时文件系统分为生成包目录、package resource、用户数据根目录三层。生成包目录保存源码、文档、报告和入口脚本；package resource 保存 WebUI HTML/CSS/JS；用户数据根目录保存 session、autosave、history、cache、exports、library、literature 和 admin config。快照和大型 ndarray 会通过 `core.snapshot` spill 到磁盘，以避免 WebUI 多用户场景中把大对象长期放在进程内存里。
""")

    sections.append("""## Knowledge、Agent 和自动练习闭环

```mermaid
flowchart TD
    UserGoal[用户目标 / 文献 / 工艺意图] --> Skills[tcad skills injection]
    UserGoal --> KE[KnowledgeEngine]
    KE --> Docs[SemanticDocumentProcessor]
    KE --> Index[LocalVectorIndex]
    KE --> Mapper[ProcessMapper]
    Mapper --> Proposal[Recipe proposal]
    Proposal --> Auditor[PhysicsAuditor]
    Auditor --> WorkerAgent[worker_runtime agent helpers]
    WorkerAgent --> Run[simulate / run recipe]
    Run --> Metrics[metrology / run report]
    Metrics --> Learn[NKB / NRL / RL / fix memory]
    Learn --> Refine[agent_refine / agent_iterate]
    Refine --> Proposal
```

Agent 功能包含 quota、provider/model 配置、skills 注入、任务意图识别、step schema、JSON 修复、物理审计、mask designer、PEER-C 多角色协作、NKB/NRL/RL 和 run report 反馈。当前这些 helper 仍在 worker 闭包中运行，因为它们需要访问 session、model、steps、cache、history、current_ui_state、storage_root 等上下文。
""")

    sections.append("## 公开 API 总览\n\n" + md_table(api_rows))

    sections.append("""## 启动方式、调用路径和边界

```bash
python3 tcad_simulator_split/tcad_simulator.py
PYTHONPATH=tcad_simulator_split/src python3 -m tcad_simulator
python3 tcad_simulator_split/tcad_simulator.py --mask-prompt-selftest
python3 tcad_simulator_split/tcad_simulator.py --webui-selftest
python3 tcad_simulator_split/tcad_simulator.py --saqp-selftest
python3 tcad_simulator_split/tcad_simulator.py --recipe-io-selftest
```

```python
from tcad_simulator import WebUIServerManager, AdminServerManager
web = WebUIServerManager(host="127.0.0.1", port=8765)
admin = AdminServerManager(host="127.0.0.1", port=8766)
web.start(open_browser=False)
admin.start()
print(web.url(), admin.url())
web.stop()
admin.stop()
```
""")

    sections.append("""## 架构维护规则

1. README 是总览，不替代源码；具体行为以生成后的模块源码和 `SPLIT_REPORT.json` 为准。
2. 新增顶层符号时优先更新 `tools/split_manifest.json`，让 splitter 能稳定归类。
3. 新增 ProcessModel 方法时优先更新 `PROCESS_MODEL_MIXINS`，避免落入 misc。
4. 新增 WebUI RPC 命令时应进入 `worker_runtime/commands/`，文件名使用自然命令名。
5. 不把大型 worker 主体塞回 `webui/worker.py`；`worker.py` 应保持薄入口。
6. 不恢复隐藏整包源码文件、代理实现目录或旧 worker 片段目录。
7. WebUI 大资源继续放在 `assets/webui/`，由 `_asset_loader.py` 读取。
8. 每次重新 split 后运行 py_compile、包导入、worker loader 和 README 大小检查。

## README 覆盖性检查

```bash
wc -c docs/README.md
rg -n "Initialize|Exposure|Deposition|Etch|CMP|Implant|Anneal|Oxidation" docs README.md
rg -n "worker_runtime|render_gbuffer|marching_cubes|snapshot|library|KnowledgeEngine" docs README.md
```
""")

    readme = "\n\n".join(section.rstrip() for section in sections).rstrip() + "\n"
    return readme


def generate_docs(docs_dir: Path, report: Dict[str, Any], manifest: Dict[str, Any]) -> None:
    docs_dir.mkdir(parents=True, exist_ok=True)
    modules = report.get("modules", {})
    public_api = report.get("public_api", [])
    package = str(manifest.get("package") or "tcad_simulator")
    source = str(report.get("source") or "tcad_simulator.py")
    output = str(report.get("output") or "tcad_simulator_split")
    worker_helpers, worker_commands = worker_runtime_doc(report)
    symbol_to_module = manifest_symbol_index(manifest)
    module_rows = sorted(modules.items())
    domain_summary: Dict[str, Dict[str, int]] = {}
    for name, info in module_rows:
        domain = module_domain(str(name))
        bucket = domain_summary.setdefault(domain, {"modules": 0, "symbols": 0, "lines": 0})
        bucket["modules"] += 1
        bucket["symbols"] += int(info.get("symbol_count", 0) or 0)
        bucket["lines"] += int(info.get("line_count", 0) or 0)

    domain_rows = [["领域", "模块数", "符号数", "估算源码行数"]]
    for domain, info in sorted(domain_summary.items()):
        domain_rows.append([domain, info["modules"], info["symbols"], info["lines"]])

    module_index_rows = [["模块视图", "源码路径", "职责", "代表符号"]]
    for name, info in module_rows:
        title, _summary, _flow, _note = module_doc_tuple(str(name))
        module_index_rows.append([name, module_source_path(str(name)), title, module_symbols_preview(info, limit=8)])

    process_rows = [["工艺", "物理内核", "状态影响", "主程序源码区域"]]
    for detail in PROCESS_PHYSICS_DETAILS:
        process_rows.append([detail["name"], detail["kernel"], detail["state"], detail["paths"]])

    api_rows = [["API/符号", "模块视图", "调用形式", "核心用途"]]
    for sym in public_api:
        mod_name, call, purpose, _note = PUBLIC_API_DOCS.get(
            str(sym),
            (symbol_to_module.get(str(sym), "主程序"), f"`{sym}`", "由 `tcad_simulator.py` 暴露或维护的兼容符号。", ""),
        )
        api_rows.append([sym, mod_name, call, purpose])

    helper_rows = [["Worker helper", "说明"]]
    for helper in worker_helpers:
        helper_rows.append([helper, "WebUI worker 内部功能片段，对应 `tcad_simulator.py` 中 session、recipe、cache、preview、agent 或 library 责任域。"])

    command_rows = [["RPC 命令", "类别"]]
    for command in worker_commands:
        command_rows.append([command, worker_command_category(command)])

    write_text(
        docs_dir / "README.md",
        textwrap.dedent(
            f"""\
            # TCAD Simulator 主程序文档

            这些文档由 `tools/split_tcad.py` 根据 `{source}` 的 AST 清单和内置架构知识生成。重点是解释主程序 `tcad_simulator.py` 的算法架构、运行时边界和维护方式；`{output}` 只是开发报告输出目录，不是开源发布主体。

            ## 文档导航

            - `ARCHITECTURE.md`：主程序结构、状态模型、recipe 执行和 UI/WebUI 边界。
            - `ALGORITHMS.md`：体素工艺、光刻、沉积、刻蚀、CMP、注入、退火、氧化、几何和测量算法。
            - `WEBUI_RUNTIME.md`：WebUI session、worker、Admin、存储、渲染和导出。
            - `MASK_LITHOGRAPHY.md`：掩膜导入、DRC、曝光、PEB、显影和 pattern transfer。
            - `AGENT_KNOWLEDGE.md`：文献摄取、检索、Agent recipe 生成和物理审计。
            - `DEVELOPER_GUIDE.md`：验证、跨平台脚本、可选拆分工具和维护规则。

            ## 关键事实

            - 主程序：`{source}`
            - 开发报告输出：`{output}`
            - 逻辑包名：`{package}`
            - 顶层对象数：{report.get('top_level_item_count')}
            - 已归类对象数：{report.get('assigned_item_count')}
            - 未归类对象数：{report.get('unassigned_item_count')}
            - 模块视图数量：{len(modules)}
            - 公开 API 数：{len(public_api)}
            - WebUI worker helper 数：{len(worker_helpers)}
            - WebUI worker RPC 命令数：{len(worker_commands)}

            ## 一分钟验证

            ```bash
            python3 -m py_compile {source}
            TCAD_SKIP_QT=1 MPLBACKEND=Agg python3 {source} --mask-prompt-selftest --n 3 --res 128
            ```

            正式源码发布应上传根目录 `README.md`、`docs/` 和 `tcad_simulator.py`。生成的 `{output}/` 与 `{output}.zip` 默认不上传。
            """
        ),
    )

    write_text(
        docs_dir / "ARCHITECTURE.md",
        textwrap.dedent(
            """\
            # 架构说明

            ## 主程序分层

            `tcad_simulator.py` 将以下层次放在一个可运行文件中：

            1. `KnowledgeEngine`、`SemanticDocumentProcessor`、`LocalVectorIndex`、`ProcessMapper`、`PhysicsAuditor`：文献、检索、recipe 映射和物理审计。
            2. 数值内核：传播距离、Euclidean distance transform、level-set、surface normals、marching cubes、voxel compression 和 snapshot spill/reload。
            3. `MaterialDatabase`：材料属性、颜色、成分、工艺参数和 Admin 覆盖。
            4. `ProcessModel`：材料体素网格、height map、掩膜、掺杂/缺陷场、geometry cache、日志和导出状态。
            5. `ProcessStep` 与 `PROCESS_STEP_FACTORIES`：把 GUI/JSON/WebUI recipe 转换为 `ProcessModel` 变更。
            6. Qt 桌面 UI：canvas、mask designer、参数编辑器、`MainWindow` 和 `SimulatorController`。
            7. WebUI/Admin：HTTP handler、session worker、runtime storage、library、exports 和可选 Agent mode。

            ## 模块视图规模

            下面的表是从主程序 AST 生成的责任域视图，用于导航和维护，不代表发布时必须采用拆分包结构。
            """
        )
        + "\n"
        + md_table(domain_rows)
        + "\n\n## 模块索引\n\n"
        + md_table(module_index_rows)
        + textwrap.dedent(
            """

            ## 关键数据流

            ```text
            Recipe JSON/UI/WebUI
                -> PROCESS_STEP_FACTORIES
                -> ProcessStep.execute(model)
                -> ProcessModel method
                -> grid / height_map / mask / doping / cache / log updates
                -> desktop view, WebUI preview, metrology, or export
            ```

            `ProcessModel` 是主状态边界。修改任何工艺算法时，要同时维护 material grid、height map、open mask、dopant/species fields、mesh caches 和日志/快照语义。
            """
        ),
    )

    write_text(
        docs_dir / "ALGORITHMS.md",
        "# 算法架构\n\n"
        "`tcad_simulator.py` 使用 physics-inspired 体素模型表达半导体工艺。它适合研究、教学和 recipe 探索，不是商业 TCAD sign-off 求解器。\n\n"
        "## Process 工艺内核\n\n"
        + md_table(process_rows)
        + "\n## 代表性算法链路\n\n"
        "- Lithography：`spin_resist()` -> mask density -> aerial image/TCC approximation -> Dill exposure -> PEB -> `develop_resist()`。\n"
        "- Deposition：ALD/CVD/PVD/electroplate/epitaxy/generic 路径根据材料、accessibility、conformality、feature size 和 dopant 设置更新体素。\n"
        "- Etch：dry/wet/directional/isotropic/anisotropic 路径结合 selectivity、surface normals、mask/open area 和 overetch 参数移除材料。\n"
        "- CMP：使用局部密度、selectivity 和去除率启发式逼近平坦化。\n"
        "- Implant/Anneal：用 species、energy、dose、tilt、diffusion 和 defect repair 更新掺杂/缺陷场。\n"
        "- Geometry：level-set、marching cubes、surface patches、smoothing 和 decimation 为 3D preview、STL/geom export 和 metrology 提供数据。\n"
        "\n## 维护原则\n\n"
        "工艺方法不应只改 `grid`。凡改变几何或材料的操作，都需要修复 height map、掺杂/物种字段、mesh cache、snapshot/cache 和 metrology 可见状态。\n",
    )

    write_text(
        docs_dir / "WEBUI_RUNTIME.md",
        "# WebUI Runtime\n\n"
        "WebUI 是 `tcad_simulator.py` 内置的多用户 session + worker runtime。HTTP server 负责静态资源、session 和 API 路由；worker 负责 `MaterialDatabase`、`ProcessModel`、recipe、history、cache、preview、library、export 和可选 Agent state。\n\n"
        "## Worker Helper\n\n"
        + md_table(helper_rows)
        + "\n## RPC 命令\n\n"
        + md_table(command_rows)
        + "\n## 运行时数据\n\n"
        "`TCAD_Web_Data/` 保存 session、history、cache、preview、exports、encrypted library、literature DB、Admin config 和本地 key。该目录默认忽略，不能作为开源源码上传。\n\n"
        "常用环境变量：`TCAD_WEBUI_STORAGE_ROOT`、`TCAD_STORAGE_ROOT`、`TCAD_LAUNCH_ROOT`、`TCAD_FFMPEG`、`TCAD_SKIP_QT`。\n",
    )

    write_text(
        docs_dir / "MASK_LITHOGRAPHY.md",
        textwrap.dedent(
            """\
            # 掩膜与光刻

            掩膜链路把用户 layout intent 转成 resist 和后续 pattern transfer 可用的二维/三维状态。

            ## 输入与栅格化

            支持内置 mask designer、图片 mask、NumPy mask 和可选 GDSII。外部 mask 会通过 resampling/rasterization 对齐到模拟域。

            ## DRC 和特征分析

            Mask helpers 计算 line/space、connectivity、density、bbox、orientation、node-based DRC、process-window probe 和 transfer context。这些结果供 WebUI、Agent、PhysicsAuditor 和 metrology 使用。

            ## 光刻流程

            `ExposureStep` 调用 `ProcessModel.expose_resist()`，内部串联 mask density、OPC-like bias、aerial image approximation、Dill exposure、PEB 和 develop。该链路是轻量级 recipe 反馈模型，不是完整光刻求解器。
            """
        ),
    )

    write_text(
        docs_dir / "AGENT_KNOWLEDGE.md",
        textwrap.dedent(
            """\
            # Agent 与知识系统

            LLM/文献功能是可选能力，普通仿真不依赖 API key。

            ## Pipeline

            ```text
            paper/user goal
                -> SemanticDocumentProcessor / LocalVectorIndex
                -> KnowledgeEngine search
                -> ProcessMapper
                -> candidate recipe
                -> schema cleanup and material normalization
                -> PhysicsAuditor
                -> optional trial simulation
            ```

            ## 边界

            Agent 只能生成 recipe proposal。最终执行仍必须通过 `PROCESS_STEP_FACTORIES` 和 `ProcessStep.execute(model)`，并应接受人工复核。不要提交 API key、LLM config、private papers 或 `TCAD_Web_Data/TCAD_Literature_DB`。
            """
        ),
    )

    write_text(
        docs_dir / "DEVELOPER_GUIDE.md",
        textwrap.dedent(
            f"""\
            # 开发指南

            ## 推荐验证

            ```bash
            python3 -m py_compile {source} tools/docsite.py tools/split_tcad.py
            TCAD_SKIP_QT=1 MPLBACKEND=Agg python3 {source} --mask-prompt-selftest --n 3 --res 128
            bash -n run_tcad_macos.sh run_tcad_linux.sh split_tcad.sh split_tcad_linux.sh
            ```

            ## 跨平台脚本

            - macOS：`./run_tcad_macos.sh`
            - Linux：`./run_tcad_linux.sh`
            - Windows PowerShell：`.\\run_tcad.ps1`
            - Windows CMD：`run_tcad.bat`

            ## 可选开发报告

            `tools/split_tcad.py`、`split_tcad.sh`、`split_tcad_linux.sh`、`split_tcad.ps1` 和 `split_tcad.bat` 用于生成 `{output}/` 报告、模块视图和校验结果。该输出默认 ignored，不作为 GitHub 发布主体。

            ## 公开 API 视图
            """
        )
        + "\n"
        + md_table(api_rows)
        + textwrap.dedent(
            f"""

            ## GitHub 上传边界

            上传 `tcad_simulator.py`、`README.md`、`docs/`、`requirements.txt`、license/community files、`.github/`、`.gitignore` 和需要公开的 `tools/`。不要上传 `TCAD_Web_Data/`、`tools/html_vendor/`、`{output}/`、`{output}.zip`、downloaded JS、API keys 或 generated exports。
            """
        ),
    )

    write_text(
        docs_dir / "DEDUP_REPORT.md",
        "# 去冗余报告\n\n"
        "该文件仅供开发者确认生成过程中的保守去冗余结果；它不是主程序架构文档的一部分。\n\n"
        + json.dumps(report.get("dedupe_removed", []), ensure_ascii=False, indent=2)
        + "\n",
    )
    return


def make_generated_shell_script() -> str:
    return textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail
        cd "$(dirname "$0")"
        export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
        python3 -m py_compile $(find src -name '*.py' -print)
        (cd src && python3 -c 'import tcad_simulator; print("import ok", len(getattr(tcad_simulator, "__all__", [])))')
        """
    )


def verify(out_dir: Path, *, run_selftests: bool, timeout_s: int) -> Dict[str, Any]:
    src = out_dir / "src"
    py_files = sorted(str(p) for p in src.rglob("*.py"))
    result: Dict[str, Any] = {"py_compile": False, "import": False, "docsite": False, "selftests": {}}
    for path in py_files:
        py_compile.compile(path, doraise=True)
    result["py_compile"] = True

    env = dict(os.environ)
    env["PYTHONPATH"] = str(src) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    code = "import tcad_simulator; print(len(tcad_simulator.__all__))"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(src),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )
    result["import"] = proc.returncode == 0
    result["import_stdout"] = proc.stdout.strip()
    result["import_stderr"] = proc.stderr.strip()
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "package import failed")
    verify_docsite(out_dir / "docs_html")
    result["docsite"] = True

    if run_selftests:
        for flag in ["--recipe-io-selftest", "--mask-prompt-selftest", "--webui-selftest", "--saqp-selftest"]:
            proc = subprocess.run(
                [sys.executable, str(out_dir / "tcad_simulator.py"), flag],
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout_s,
            )
            result["selftests"][flag] = {
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-4000:],
                "stderr_tail": proc.stderr[-4000:],
            }
    write_text(out_dir / "VERIFY_REPORT.json", json.dumps(result, ensure_ascii=False, indent=2))
    return result


def clean_generated_noise(out_dir: Path) -> None:
    for cache_dir in out_dir.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
    for ds in out_dir.rglob(".DS_Store"):
        try:
            ds.unlink()
        except OSError:
            pass


def make_root_shell() -> str:
    return textwrap.dedent(
        """\
        #!/usr/bin/env bash
        set -euo pipefail

        SRC="${1:-tcad_simulator.py}"
        OUT="${2:-tcad_simulator_split}"
        ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        PY="${PYTHON:-python3}"

        "$PY" "$ROOT/tools/split_tcad.py" --src "$SRC" --out "$OUT" --clean --dedupe conservative
        "$PY" "$ROOT/tools/split_tcad.py" --out "$OUT" --verify

        echo "Split complete: $OUT"
        echo "Docs: $OUT/docs"
        echo "Docs HTML: $OUT/docs_html/index.html"
        echo "Report: $OUT/SPLIT_REPORT.json"
        """
    )


def install_shell_script(path: Path) -> None:
    write_text(path, make_root_shell())
    os.chmod(path, 0o755)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Split tcad_simulator.py into a package and documentation.")
    parser.add_argument("--src", default="tcad_simulator.py", help="Source monolith path.")
    parser.add_argument("--out", default="tcad_simulator_split", help="Output directory.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Split manifest path.")
    parser.add_argument("--clean", action="store_true", help="Remove output directory first.")
    parser.add_argument("--dedupe", choices=["none", "conservative"], default="conservative")
    parser.add_argument("--verify", action="store_true", help="Verify an existing split output.")
    parser.add_argument("--run-selftests", action="store_true", help="Run long original selftests during verify.")
    parser.add_argument("--timeout", type=int, default=120, help="Verification timeout seconds.")
    parser.add_argument("--install-shell", action="store_true", help="Install root split_tcad.sh and exit.")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    if args.install_shell:
        install_shell_script(ROOT / "split_tcad.sh")
        return 0
    if args.verify:
        verify(out_dir, run_selftests=bool(args.run_selftests), timeout_s=int(args.timeout))
        clean_generated_noise(out_dir)
        return 0
    source_path = Path(args.src).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    split_source(source_path, out_dir, manifest_path, clean=bool(args.clean), dedupe=str(args.dedupe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
