# A100 Stages 0–3 Batch Runner

Run **stages 0 / 1 / 0.5 / 2 / 3** offline on A100 (or any Linux box with
local MinerU + internet for DashScope) for all 348 PDFs, producing the
inputs that V1.2.5 pipeline stages 4–9 will consume tomorrow.

## What runs here

| Stage | Script | Parallelism | Time (348 PDFs) | Cost |
|---|---|---|---|---|
| 1 — MinerU layout | `01_batch_mineru.py` | 6-way (process pool, GPU bound) | ~2-3 h | local GPU |
| 0.5 — patent metadata + IPC gate | `02_batch_metadata.py` | 32-way (thread pool, IO bound) | ~3-5 min | ~¥3.5 (DashScope) |
| 2+3 — unit extraction + materialize | `03_batch_units.py` | 1-way (CPU/IO, fast) | ~5 min | 0 |

**Stages 4–9 do NOT run here** (Qwen-VL / Qwen-plus fact extraction).
Tomorrow we run those via `cli.py ingest` — its inputs are the artifacts
this folder produces.

## What you need to change

**Only one place**: in `01_batch_mineru.py`, the function
`run_mineru_for_pdf()`. Replace its `subprocess.run([...])` call with the
exact command you tested for 1-PDF MinerU on your A100. Everything else
is data-driven — no other edits should be needed.

The other two scripts (`02_batch_metadata.py` / `03_batch_units.py`)
import directly from the existing `coating_kg` package — they do NOT
duplicate any pipeline logic.

## Prerequisites on A100

```bash
# 1. Clone repo + install
git clone <repo>
cd coating_kg
pip install -e .

# 2. Set env (drop into .env or export)
export OPENAI_API_KEY=<your-key>          # DashScope key for stage 0.5
export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export PDF_INPUT_DIR=/data/coating_1/pdfs   # adjust to your A100 path
export MINERU_OUTPUT_DIR=/data/coating_kg/data/mineru_output
export MINERU_N_WORKERS=6
export MINERU_MAX_FAILURES=10              # continue later stages if <=10 PDFs fail MinerU

# 3. Verify MinerU works on a single PDF first (your existing test command)
magic-pdf -p test.pdf -o /tmp/mineru_test -m auto    # <-- replace with yours
```

## How to run

```bash
cd coating_kg/scripts/a100_stages_0_3

# Sequential — recommended (each step's outputs feed the next)
python 01_batch_mineru.py
python 02_batch_metadata.py
python 03_batch_units.py

# Or one-shot (runs all three in order; MinerU tolerates up to MINERU_MAX_FAILURES)
python run_all.py
```

All three scripts are **idempotent**: rerunning skips already-completed
work. Safe to Ctrl+C and resume.
If Stage 1 has a small number of failed PDFs, it writes
`$MINERU_OUTPUT_DIR/_failed_pdfs.txt` and still lets stages 0.5/2/3 process
the successful MinerU outputs. Set `MINERU_MAX_FAILURES=0` for strict mode.

## Outputs (this is the "schema contract" with stages 4–9)

After all three steps run, you will have:

```
data/
├── mineru_output/
│   └── <doc_id>/
│       └── auto/
│           ├── <uuid>_content_list.json   ★ stage 2 input
│           ├── <uuid>.md
│           ├── images/*.jpg                ★ stage 4 (Qwen-VL) input
│           └── ...
├── patents/
│   └── <doc_id>__patent_meta.json          ★ stage 0.5 result
│      {
│        "patent_id": ...,
│        "title": ..., "ipc_codes": [...], "applicant": ...,
│        "abstract": ..., "filing_date": ...,
│        "is_coating_patent": true|false,   ★ early IPC gate signal
│        "is_coating_reason": "primary IPC: C09D 175/14",
│        "section_split_mode": "found"|"fallback",
│        "examples_page_range": [start, end]
│      }
└── units/
    └── U_<doc_id>_p<N>_b<M>/
        ├── meta.json                       ★ stage 4-7 input
        │   { unit_id, unit_type, doc_id, page, region_id, region_type,
        │     bbox, caption_footnote_text, image_path / table_html_path }
        ├── caption.txt
        └── image.png  OR  table.html
```

Tomorrow's `cli.py ingest` reads these files directly — no transform
needed. The `is_coating_patent: false` patents will be skipped at the
early gate (cli.py:64-108) without consuming any LLM budget.

## Sanity-check after running

```bash
# 1. How many PDFs got MinerU output?
ls data/mineru_output/ | wc -l                    # should be 348

# 2. How many got patent_meta?
ls data/patents/*.json | wc -l                    # should be 348

# 3. How many will be filtered as non-coating?
python -c "
import json, glob
non = sum(1 for f in glob.glob('data/patents/*.json')
          if not json.load(open(f))['is_coating_patent'])
print(f'non-coating: {non}/348 ({100*non/348:.0f}%)')
"

# 4. How many examples extracted into units?
ls data/units/ | wc -l                            # patent-dependent
```

If any number looks off (e.g., < 300 PDFs got MinerU output), check the
respective script's stderr log — failures are reported per-PDF.
