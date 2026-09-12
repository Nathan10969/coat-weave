#!/usr/bin/env bash
# Ingest every PDF under $PDF_INPUT_DIR.
# Honours --max N to cap the count (useful during W2.4 5-patent golden set).
set -euo pipefail

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

PDF_INPUT_DIR="${PDF_INPUT_DIR:-./data/pdf}"
MAX="${1:-0}"

i=0
shopt -s nullglob
for pdf in "$PDF_INPUT_DIR"/*.pdf "$PDF_INPUT_DIR"/*.PDF; do
  i=$((i+1))
  if [[ "$MAX" != "0" && "$i" -gt "$MAX" ]]; then
    echo "stopping after $MAX files."
    break
  fi
  echo "==> [$i] $pdf"
  python -m coating_kg ingest "$pdf" || echo "WARN: failed on $pdf"
done

echo "done — processed $i file(s)."
