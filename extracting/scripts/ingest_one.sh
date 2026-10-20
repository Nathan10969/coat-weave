#!/usr/bin/env bash
# Ingest a single PDF end-to-end via the CLI.
# Usage: scripts/ingest_one.sh <pdf_path>
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <pdf_path>" >&2
  exit 1
fi

PDF_PATH="$1"

if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

python -m coating_kg ingest "$PDF_PATH"
