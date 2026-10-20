# Batch layout, metadata and units

These scripts prepare layout caches, patent metadata and evidence units for a
batch of PDFs. They reuse the extraction package and do not run the later
figure/table interpretation or fact-extraction stages.

| Script | Work | Dependencies |
| --- | --- | --- |
| `01_batch_mineru.py` | Batch PDF layout extraction | A working local MinerU command; accelerator requirements depend on that installation |
| `02_batch_metadata.py` | First-page metadata and IPC gate annotation | Configured OpenAI-compatible text model; network access |
| `03_batch_units.py` | Unit extraction and materialization | Existing layout files; local CPU and disk |

## Configure

Install the extraction package as described in [its README](../../README.md).
Set `PDF_INPUT_DIR` to your PDF directory and `MINERU_OUTPUT_DIR` to the layout
output directory. Configure `OPENAI_API_KEY`, `OPENAI_BASE_URL` and model names
for metadata extraction. Keep credentials in your environment or local `.env`.

Check `run_mineru_for_pdf()` in `01_batch_mineru.py`: its subprocess command must
match the MinerU CLI installed on your machine. Test that command with one PDF
before starting a batch. This runner does not install MinerU or model weights.

`MINERU_N_WORKERS` controls layout concurrency. `MINERU_MAX_FAILURES` controls how
many layout failures are tolerated before later steps are stopped; set it to `0`
for strict processing. Choose concurrency according to your hardware and model
provider limits. Runtime and cost depend on your inputs and deployment.

## Run

From `extracting/` with the package environment activated:

```bash
python scripts/a100_stages_0_3/01_batch_mineru.py
python scripts/a100_stages_0_3/02_batch_metadata.py
python scripts/a100_stages_0_3/03_batch_units.py
```

Alternatively, run `python scripts/a100_stages_0_3/run_all.py` to execute the
steps in sequence. Metadata extraction makes provider requests; these commands
are not offline unit tests.

Outputs include `data/mineru_output/`, `data/patents/` and `data/units/`.
Layout and metadata steps skip outputs already present; unit materialization
updates unit files. Layout failures are recorded in
`$MINERU_OUTPUT_DIR/_failed_pdfs.txt`. Check that your own input and successful
output counts agree before continuing with later extraction stages.

See [schema_reference.md](schema_reference.md) for output field descriptions and
[the field contract](../../docs/KG_FIELD_CONTRACT.md) for the shared record format.
