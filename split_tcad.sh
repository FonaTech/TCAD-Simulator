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
