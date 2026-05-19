#!/usr/bin/env python3
"""Vendor the static browser libraries used by the generated documentation site."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_VENDOR_DIR = TOOLS_DIR / "html_vendor"

SPECS = {
    "mermaid": {
        "version": "11.14.0",
        "tarball": "https://registry.npmjs.org/mermaid/-/mermaid-11.14.0.tgz",
        "files": [("package/dist/mermaid.min.js", "mermaid/mermaid.min.js")],
    },
    "marked": {
        "version": "18.0.3",
        "tarball": "https://registry.npmjs.org/marked/-/marked-18.0.3.tgz",
        "files": [("package/lib/marked.umd.js", "marked/marked.umd.js")],
    },
    "highlight": {
        "version": "11.11.1",
        "tarball": "https://registry.npmjs.org/@highlightjs/cdn-assets/-/cdn-assets-11.11.1.tgz",
        "files": [
            ("package/highlight.min.js", "highlight/highlight.min.js"),
            ("package/styles/github.min.css", "highlight/styles/github.min.css"),
        ],
    },
    "mathjax": {
        "version": "4.1.2",
        "tarball": "https://registry.npmjs.org/mathjax/-/mathjax-4.1.2.tgz",
        "extract_all_to": "mathjax",
        "required": ["mathjax/tex-mml-svg.js", "mathjax/output/svg.js", "mathjax/input/tex.js"],
    },
    "mathjax-newcm-font": {
        "version": "4.1.2",
        "tarball": "https://registry.npmjs.org/@mathjax/mathjax-newcm-font/-/mathjax-newcm-font-4.1.2.tgz",
        "extract_all_to": "mathjax-newcm-font",
        "required": [
            "mathjax-newcm-font/svg.js",
            "mathjax-newcm-font/svg/dynamic/calligraphic.js",
        ],
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, *, retries: int = 4) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tcad-docsite-vendor/1.0"})
            with urllib.request.urlopen(req, timeout=90) as resp, dest.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
            return
        except Exception as exc:  # pragma: no cover - network failure path
            last_error = exc
            if dest.exists():
                dest.unlink()
    raise RuntimeError(f"failed to download {url}: {last_error}")


def safe_extract_member(tf: tarfile.TarFile, member_name: str, dest: Path) -> None:
    member = tf.getmember(member_name)
    if not member.isfile():
        raise RuntimeError(f"tar member is not a file: {member_name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = tf.extractfile(member)
    if src is None:
        raise RuntimeError(f"cannot read tar member: {member_name}")
    with src, dest.open("wb") as fh:
        shutil.copyfileobj(src, fh)


def safe_extract_tree(tf: tarfile.TarFile, src_prefix: str, dest_root: Path) -> None:
    dest_root.mkdir(parents=True, exist_ok=True)
    prefix = src_prefix.rstrip("/") + "/"
    for member in tf.getmembers():
        if not member.name.startswith(prefix):
            continue
        rel = member.name[len(prefix) :]
        if not rel or rel.startswith("../") or "/../" in rel:
            continue
        dest = dest_root / rel
        if member.isdir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = tf.extractfile(member)
        if src is None:
            continue
        with src, dest.open("wb") as fh:
            shutil.copyfileobj(src, fh)


def expected_files(vendor_dir: Path) -> List[Path]:
    paths: List[Path] = []
    for spec in SPECS.values():
        for _src, rel in spec.get("files", []):
            paths.append(vendor_dir / rel)
        for rel in spec.get("required", []):
            paths.append(vendor_dir / rel)
    return paths


def vendor_libs(vendor_dir: Path, *, clean: bool = False) -> Dict[str, object]:
    vendor_dir = vendor_dir.resolve()
    if clean and vendor_dir.exists():
        shutil.rmtree(vendor_dir)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    manifest: Dict[str, object] = {"libraries": {}, "files": {}}

    with tempfile.TemporaryDirectory(prefix="tcad_docsite_vendor_") as tmp_s:
        tmp = Path(tmp_s)
        for name, spec in SPECS.items():
            tar_path = tmp / f"{name}.tgz"
            download(str(spec["tarball"]), tar_path)
            with tarfile.open(tar_path, "r:gz") as tf:
                if "extract_all_to" in spec:
                    dest = vendor_dir / str(spec["extract_all_to"])
                    if dest.exists():
                        shutil.rmtree(dest)
                    safe_extract_tree(tf, "package", dest)
                for src, rel in spec.get("files", []):
                    safe_extract_member(tf, str(src), vendor_dir / str(rel))
            manifest["libraries"][name] = {
                "version": spec["version"],
                "tarball": spec["tarball"],
            }

    missing = [str(p.relative_to(vendor_dir)) for p in expected_files(vendor_dir) if not p.exists()]
    if missing:
        raise RuntimeError("missing vendored files: " + ", ".join(missing))
    for path in sorted(p for p in vendor_dir.rglob("*") if p.is_file()):
        manifest["files"][str(path.relative_to(vendor_dir))] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    manifest_path = vendor_dir / "VENDOR_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def check_vendor(vendor_dir: Path) -> None:
    missing = [str(p.relative_to(vendor_dir)) for p in expected_files(vendor_dir) if not p.exists()]
    if missing:
        raise RuntimeError("missing vendored files: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description="Download local JS/CSS assets for the TCAD documentation site.")
    parser.add_argument("--vendor-dir", default=str(DEFAULT_VENDOR_DIR), help="Destination vendor directory.")
    parser.add_argument("--clean", action="store_true", help="Delete the vendor directory before downloading.")
    parser.add_argument("--check", action="store_true", help="Only check that required files exist.")
    args = parser.parse_args()
    vendor_dir = Path(args.vendor_dir).expanduser().resolve()
    if args.check:
        check_vendor(vendor_dir)
        print(f"vendor ok: {vendor_dir}")
        return 0
    manifest = vendor_libs(vendor_dir, clean=bool(args.clean))
    print(f"vendored {len(manifest['files'])} files into {vendor_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
