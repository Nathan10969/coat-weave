# CoatWeave extraction package

This package extracts coating-patent evidence into structured facts and knowledge
graph (KG) records. Facts link materials, processes, test conditions and results to
their source pages. Provide your own PDFs, model credentials and output directories.

## Install and run offline checks

From the repository root, with Python 3.10 or newer:

```bash
cd extracting
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
python -m pytest tests/test_section_split.py tests/test_polarity.py tests/test_comparison_group.py -v
```

On Windows, activate `.venv/Scripts/Activate.ps1` in PowerShell. These tests cover
section boundaries, positive/comparative examples and comparison groups without a
database or external model calls. See [SAMPLE_RUN.md](SAMPLE_RUN.md) for additional
offline examples.

## Configure extraction

Edit `.env` using [.env.example](.env.example). Current settings are defined in
[`src/coating_kg/config.py`](src/coating_kg/config.py):

| Settings | Purpose |
| --- | --- |
| `MINERU_API_BASE`, `MINERU_TOKEN` | Online PDF layout extraction |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY` | OpenAI-compatible text and vision endpoint |
| `QWEN_TEXT_MODEL`, `QWEN_VL_MODEL`, `QWEN_TABLE_OCR_MODEL` | Provider model names |
| `PDF_INPUT_DIR`, `MINERU_OUTPUT_DIR` | Local PDF input and layout-cache paths |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL when database writes are enabled |

Use model names available to your account. Existing layout caches can be reused;
without a cache or MinerU credentials the parser returns a placeholder rather
than a completed extraction.

From `extracting/`, replace the PDF path with a file you have permission to use:

```bash
python -m coating_kg ingest /absolute/path/your-patent.pdf --skip-db
```

`--skip-db` skips database writes; it still uses configured layout and model
services. To run extraction followed by KG and CSV generation:

```bash
python scripts/run_pipeline.py /absolute/path/your-patent.pdf --with-kg
```

Generated files are written under `data/` and `output/`; they are not supplied with
this source snapshot. Inspect evidence pointers and values before using results.

## Optional PostgreSQL setup

The [Compose file](docker-compose.yml) provides PostgreSQL 16 with pgvector. Set
database credentials in `.env`, install PostgreSQL client tools `psql` and
`pg_isready`, then run from `extracting/`:

```bash
docker compose up -d
bash scripts/setup_db.sh
python -m coating_kg ingest /absolute/path/your-patent.pdf
```

The final command enables database writes. Starter SQL files contain ontology and
configuration entries rather than a patent corpus.

## Source map

| Path | Purpose |
| --- | --- |
| [`db/schema.sql`](db/schema.sql), `db/seed_*.sql` | Schema and starter ontology |
| [`src/coating_kg/pipeline/`](src/coating_kg/pipeline/) | Layout, units, extraction and projection |
| [`src/coating_kg/ontology/`](src/coating_kg/ontology/) | Canonical entity resolution and consistency checks |
| [`prompts/`](prompts/) | Text, figure and table extraction instructions |
| [`scripts/`](scripts/) | Batch execution, validation and export |
| [`tests/`](tests/) | Deterministic and mocked regression checks |

The [field contract](docs/KG_FIELD_CONTRACT.md) describes record formats;
the [vision reconstruction contract](docs/AGENT_NATIVE_VISION_KG_RECONSTRUCTION.md)
describes image-based reconstruction. Some contract sections describe target
fields that require corresponding model/export support. Use the implementation
and its tests to check which fields are currently handled.

## License

MIT; see the repository [LICENSE](../LICENSE).
