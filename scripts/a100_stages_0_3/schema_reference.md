# Output Schema Contract — Stages 0–3 → Stages 4–9

This file documents the artifacts produced by `01_batch_mineru.py` /
`02_batch_metadata.py` / `03_batch_units.py`, and the exact contract
each downstream stage relies on. Tomorrow's stages 4-9 read these
files **verbatim** — there is no transformation step in between.

## 1. `data/mineru_output/<doc_id>/`

Produced by stage 1 (MinerU local).

```
<doc_id>/
└── auto/                                     ← MinerU 2.x default subdir
    ├── <uuid>_content_list.json              ★ stage 2/0.5 input
    ├── <uuid>.md                             (full doc as markdown)
    ├── <uuid>_layout.json                    (page-nested, NOT used)
    ├── <uuid>_origin.pdf                     (cached source)
    ├── <uuid>_model.json                     (raw model output)
    └── images/                               ★ stage 4 (Qwen-VL) input
        ├── <hash1>.jpg
        ├── <hash2>.jpg
        └── ...
```

### `content_list.json` — the single most important file

A flat JSON array of blocks; each block is:

```json
{
  "type": "text" | "title" | "image" | "table" | "equation",
  "page_idx": 12,
  "text": "...",                 // for text/title
  "img_path": "images/abc.jpg",  // for image (relative to auto/)
  "img_caption": ["..."],
  "img_footnote": ["..."],
  "table_body": "<table>...</table>",  // for table
  "bbox": [x1, y1, x2, y2],      // ★ V1.2.5 stage 2 propagates this
  ...
}
```

`unit_extractor.iter_figure_table_units(layout, examples_only=True)`
walks this array, keeps blocks where `type ∈ {image, table}` AND the
block's `page_idx` falls inside the Examples section (detected via
`section_split.split_examples_section`).

## 2. `data/patents/<doc_id>__patent_meta.json`

Produced by stage 0.5 (DashScope qwen-plus + V1.2.5 4-signal classifier).

```json
{
  "patent_id": "WO2026052438A1",
  "title": "TWO-COMPONENT COATING COMPOSITION",
  "applicant": "BASF Coatings GmbH",
  "filing_date": "2025-09-15",
  "publication_date": "2026-04-12",
  "application_number": "PCT/EP2025/074321",
  "publication_number": "WO2026052438A1",
  "ipc_codes": ["C09D 175/14", "C08G 18/62", "C08K 5/10"],
  "abstract": "A two-component coating composition comprising ...",
  "inventors": ["Author A", "Author B"],

  "is_coating_patent": true,                    ★ early IPC gate signal
  "is_coating_reason": "primary IPC: C09D 175/14",
  "section_split_mode": "found",                ★ V1.2.5 task C
  "examples_page_range": [22, 31]               ★ V1.2.5 task C
}
```

### Fields the early gate (cli.py:64-108) reads

- `is_coating_patent: false` → cli.py exits before stage 4-7,
  saving ~¥0.5/imposter × ~30% × 348 ≈ ¥45 of LLM budget
- `is_coating_reason` → free-text debug hint, useful for grep later
- All other fields are passed through to stage 9 (build_coatings_csv)
  for the `applicant` / `filing_date` / `title` columns

## 3. `data/units/U_<doc_id>_p<N>_b<M>/`

Produced by stages 2+3 (rule-based + disk IO).

```
U_WO2026052438A1_p25_b330/
├── meta.json              ★ stage 4-7 input (unit metadata)
├── caption.txt            (caption + footnote text, plain)
└── image.png  OR  table.html   (one of the two)
```

### `meta.json`

```json
{
  "unit_id": "U_WO2026052438A1_p25_b330",
  "doc_id": "WO2026052438A1",
  "page": 25,
  "region_id": "Table 1",
  "region_type": "TABLE",                     // or "FIGURE"
  "unit_type": "table",                       // or "figure"
  "bbox": [x1, y1, x2, y2],                   ★ V1.2.5 task #2 propagates
  "caption_footnote_text": "Table 1: ...",
  "image_path": null,                         // set for figures
  "table_html_path": "table.html",            // set for tables
  "extracted_table_html": "<table>...</table>"
}
```

## 4. How stages 4–9 consume these

| Stage | Script (tomorrow) | Reads |
|---|---|---|
| Early gate | `cli.py:64-108` | `data/patents/*__patent_meta.json` (early-return on `is_coating_patent: false`) |
| 4 — VLM describe | `pipeline/vlm_describe.py` | `data/units/<unit>/image.png` or `table.html` + `meta.json` |
| 5 — Entity tag | `pipeline/entity_tagger.py` | VLM output + `meta.json` |
| 6 — Paragraph match | `pipeline/paragraph_matcher.py` | Examples paragraphs from `data/mineru_output/<doc_id>/auto/<uuid>_content_list.json` |
| 7 — Fact extract | `pipeline/fact_extractor.py` | `data/units/<unit>/meta.json` + matched paragraphs + VLM description |
| 8 — Polarity | `pipeline/polarity.py` | facts.json (in-memory) |
| 9 — CSV pivot | `scripts/build_coatings_csv.py` | All `data/units/*/facts.json` + `data/patents/*__patent_meta.json` |

## 5. What is **NOT** produced here

These files appear later, during stage 4-9:

- `data/units/<unit>/vlm_description.json` ← stage 4
- `data/units/<unit>/matched_paragraphs.json` ← stage 6
- `data/units/<unit>/facts.json` ← stage 7
- `data/units/<unit>/proposed_canonicals.json` ← stage 7
- `data/units/<unit>/coverage.json` ← stage 7
- `output/coatings_wide.csv` ← stage 9

Tomorrow's pipeline produces these from the artifacts in this folder.

## 6. Schema stability guarantee

The four files above (`content_list.json`, `images/*.jpg`,
`<doc_id>__patent_meta.json`, `meta.json`) are the **stable interface**
between this offline batch and the V1.2.5 LLM pipeline. They will not
change in V2.0 — V2.0's only change is swapping stage 4/6/7 from
DashScope cloud to local A100 vLLM (different `base_url`, same JSON
schemas in/out).

So the artifacts you produce tonight are usable for V1.2.5, V2.0, and
any later iteration without re-running MinerU.
