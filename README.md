# coating_kg

**Coating Patent Knowledge Graph — V1.2.2 implementation**

Vertical: 建筑外墙涂料 (architectural exterior coating).
Demo dataset: 348 BASF PCT patents under `G:\coating_1\20260502062406317\pdf\`.

This repo implements the V1.2.2 design — an evidence-grounded hyperedge schema where every fact extracted from a coating patent's Examples section is stored as a multi-slot `FactHyperedge` (9 required + 10 optional + 1 marker fields), connected to canonical `Material / Application / Substrate / Property / Process / TestMethod / Evidence / Patent` nodes through a typed alias subgraph.

Design source of truth: `G:\coating_1\v1.2_design\` (markdown 01-08), summarised in `G:\coating_1\Coating项目V1.2.2_完整设计稿.docx`.

---

## V1.2.2 highlights

- **8 node types**: `Material / Application / Substrate / Property / Process / TestMethod / Evidence / Patent`
- **`source_section_type`** is restricted to 4 values: `TABLE` (1.00) / `FIGURE` (0.85) / `TABLE_CAPTION_FOOTNOTE` (0.85) / `FIGURE_CAPTION_FOOTNOTE` (0.75) — no free paragraphs in V1
- **Alias subgraph** carries 5 edge types (`canonical_of` / `synonym_of` / `chemical_subtype_of` / `forbidden_merge` / `must_merge`) with conflict priority `forbidden_merge > must_merge > synonym_of`
- **`figure_table_units`** is the V1.2.2 spine for figure/table evidence — carries `tagged_entities` JSONB + `figure_subtype` + `caption_footnote_text` + `vlm_description` + `description_embedding` + `extracted_table_html` + `image_path`
- **Polarity + comparison_group** auto-resolved per V1.2.2 §修订 4 (single negative in same table → bind; otherwise pending review)

## Model stack

| Role | Model | Where |
|---|---|---|
| PDF layout | MinerU (`magic-pdf`) | local A100 |
| Figure / table description | Qwen3-VL-Plus / Max | DashScope API |
| Fact extraction (text) | Qwen-Plus | DashScope API |
| Text embedding | BGE-M3 (1024-dim) | local |
| Query / answer / verifier | Qwen3.6 | local or API |

## Quick start

```bash
# 1. start postgres + pgvector
docker compose up -d

# 2. set credentials
cp .env.example .env
# edit .env, fill DASHSCOPE_API_KEY

# 3. apply schema + seeds
bash scripts/setup_db.sh

# 4. ingest one patent end-to-end
python -m coating_kg ingest "G:/coating_1/20260502062406317/pdf/WO2026077939A1.pdf"
```

For a smoke test that needs **no** DB and **no** API key, run the deterministic-logic unit tests:

```bash
pip install -r requirements.txt
pytest tests/test_section_split.py tests/test_polarity.py tests/test_comparison_group.py -v
```

See [`SAMPLE_RUN.md`](SAMPLE_RUN.md) for a walk-through of the offline-runnable parts.

## Project structure

```
coating_kg/
├── db/                          # schema.sql + 4 seed files (config tables)
├── src/coating_kg/
│   ├── config.py                # typed settings, .env loader
│   ├── cli.py                   # `python -m coating_kg ingest <pdf>`
│   ├── db/                      # connection pool, pydantic models, insert/query
│   ├── pipeline/                # PDF → layout → unit → VLM → fact
│   └── ontology/                # canonical resolver + consistency check
├── prompts/                     # vlm_figure / vlm_table / fact_extract
├── tests/                       # pytest — section_split / polarity / comparison_group
└── scripts/                     # setup_db.sh / ingest_one.sh / ingest_batch.sh
```

## W2 schedule (V1.2.2 §修订 6)

| Sub-week | Goal | Files involved |
|---|---|---|
| **W2.1** MinerU integration | parse 1 native-text patent, write `Examples` section splitter, slice figures/tables | `pipeline/pdf_layout.py`, `pipeline/section_split.py`, `pipeline/unit_extractor.py` |
| **W2.2** Qwen-VL API | DashScope client, `figure_table_units` insert path, prompt v1 | `pipeline/vlm_describe.py`, `db/insert.py`, `prompts/vlm_*.txt` |
| **W2.3** Entity tag + polarity | `identified_entities` → `tagged_entities`, polarity classifier, `comparison_group` resolver | `pipeline/entity_tagger.py`, `pipeline/polarity.py`, `pipeline/comparison_group.py` |
| **W2.4** Golden set (5 patents) | full pipeline on 5 patents, manual review of 50 units | (everything) |

## Implementation status

| Module | Status |
|---|---|
| `db/schema.sql` | **complete & executable** (PostgreSQL 16 + pgvector) |
| `db/seed_*.sql` | **complete** (4 seed files, ~50/63/22/15 entries) |
| `pipeline/section_split.py` | **real implementation** (English / Chinese / German) |
| `pipeline/polarity.py` | **real implementation** (per V1.2.2 §修订 4) |
| `pipeline/comparison_group.py` | **real implementation** (per V1.2.2 §修订 4) |
| `pipeline/vlm_describe.py` | **real DashScope client** (tenacity retry, JSON-only output) |
| `ontology/canonical_resolver.py` | **real** (queries aliases + nodes) |
| `ontology/consistency_check.py` | **real** (forbidden_merge bidirectional) |
| `pipeline/pdf_layout.py` | skeleton — calls `magic-pdf` if available |
| `pipeline/unit_extractor.py` | skeleton with TODOs, works on MinerU JSON |
| `pipeline/fact_extractor.py` | skeleton — Qwen-Plus prompt wired, parsing TODO |

## References

- `G:\coating_1\v1.2_design\06_entity_relation_hyperedge.md` — node + edge + hyperedge spec
- `G:\coating_1\v1.2_design\08_v122_amendments.md` — V1.2.1 → V1.2.2 deltas
- `G:\coating_1\v1.2_design\03_config_tables.md` — `forbidden_merge` / `must_merge` / `property_directionality` source
- `G:\coating_1\Coating项目V1.2.2_完整设计稿.docx` — final consolidated design

## License

Internal R&D project. Not for distribution.
