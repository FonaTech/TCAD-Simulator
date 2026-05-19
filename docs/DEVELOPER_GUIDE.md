# Developer Guide

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Cross-Platform Launchers

Desktop app:

```bash
./run_tcad_macos.sh
./run_tcad_linux.sh
```

Windows:

```powershell
.\run_tcad.ps1
```

```bat
run_tcad.bat
```

All launchers forward arguments to `tcad_simulator.py`, so selftests can be run through them as well.

## Verification

Fast checks before publishing:

```bash
python3 -m py_compile tcad_simulator.py tools/docsite.py tools/split_tcad.py
TCAD_SKIP_QT=1 MPLBACKEND=Agg python3 tcad_simulator.py --mask-prompt-selftest --n 3 --res 128
bash -n run_tcad_macos.sh run_tcad_linux.sh split_tcad.sh split_tcad_linux.sh
git ls-files --others --exclude-standard
```

Some selftests require optional local fixtures such as LLM configs, previous-version files, or recipe examples. Missing fixtures are not automatically a packaging failure.

## Optional Split Tooling

`tools/split_tcad.py` can generate `tcad_simulator_split/` for code review, package experiments, API reports, and generated documentation:

```bash
./split_tcad.sh
./split_tcad_linux.sh
.\split_tcad.ps1
split_tcad.bat
```

Generated outputs are ignored:

- `tcad_simulator_split/`
- `tcad_simulator_split.zip`
- generated `docs_html/`

Do not treat the split output as the source of truth unless the project explicitly decides to migrate away from the single-file application.

## Documentation Site

`tools/docsite.py` can turn Markdown docs into an offline HTML site:

```bash
python3 tools/docsite.py --docs-dir docs --out-dir docs_html
```

If the local vendor cache under `tools/html_vendor/` is missing, the script automatically downloads Mermaid, Marked, MathJax, and Highlight.js through `tools/vendor_docsite_libs.py`. The vendor cache and generated `docs_html/` directory are ignored because they are reproducible and large.

Prefer writing source-facing documentation in root `docs/`. Generated split-package docs are for developer inspection only.

## GitHub Readiness

Recommended upload set:

- `tcad_simulator.py`
- `README.md`
- `docs/`
- `requirements.txt`
- `LICENSE`
- `THIRD_PARTY_NOTICES.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `.gitignore`
- `.github/`
- `tools/`
- `run_tcad_*`, `run_tcad.ps1`, `run_tcad.bat`
- `split_tcad*` scripts only if publishing the optional split tooling

Do not upload:

- `TCAD_Web_Data/`
- `tools/html_vendor/`
- `tcad_simulator_split/`
- `tcad_simulator_split.zip`
- downloaded JavaScript runtime assets
- `.DS_Store`
- API keys, local configs, private recipes, private papers, or generated exports

## License Notes

MIT is a reasonable license for this project's own source code. It is permissive and common for research/education tooling.

Third-party licenses still matter:

- PyQt5 is GPL/commercial licensed.
- PyMuPDF/MuPDF, if used, is AGPL/commercial licensed.
- Optional packages may have their own redistribution requirements.

Publishing source under MIT is generally coherent, but binary distribution, closed-source redistribution, or commercial packaging should be reviewed separately.
