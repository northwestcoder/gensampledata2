# gensampledata 2

A local synthetic-data workbench, command-line tool, and Python library for creating related customer datasets with controlled imperfections.

Generate e-commerce and POS customers and orders, loyalty, email campaigns, clickstream, mobile, Wi-Fi, safety reports, location records, and custom objects. Every run includes a separate answer key showing which records describe the same person, a mutation log, and a verification manifest.

## Install

Use Python 3.14 or newer, Node.js 22.12 or newer, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Node is needed to build the visual editor, not for generating data from an installed package.

```sh
npm --prefix frontend ci
npm --prefix frontend run build
uv sync --locked --all-extras
```

Dependencies are isolated in `.venv/`; Python and npm lockfiles are committed. `pip install '.[web,s3]'` is also supported after building the frontend, but does not use `uv.lock`.

Local configuration, credentials, caches, `tmp/`, and generated `artifacts/` are excluded from Git. All original `support/` files and both dependency lockfiles belong in the repository. Build the frontend before packaging, running `docker build`, or using `publish_cloud_run.sh`; Docker and Cloud Run include `artifacts/workbench-dist/` while excluding other generated output.

## Visual workbench

```sh
uv run gensampledata serve
```

Open **http://127.0.0.1:8767**. The local workbench includes:

- Named presets, saved scenario versions, cloning, and TOML/JSON import and export.
- Independent source selection, duplicate frequency, source overlap, corruption, and missingness.
- Linked-record previews with a separate person identity and highlighted before/after mutations.
- Generation in worker processes, live record counts, cancellation, and persistent run history.
- Verified results, dataset samples, individual and complete-run ZIP downloads, and optional S3 publication.

Scenarios and status live in `artifacts/workbench-data/workbench.db`; datasets live in `artifacts/workbench-data/runs/`. Use `--data-dir PATH` or `--port PORT` to change these. This is a loopback-only local application, without multi-user hosting or authentication.

For frontend development, run the API as above and `npm --prefix frontend run dev` in a second terminal. Vite proxies `/api` to port 8767.

## CLI

```sh
# Start with the included scenario, or create another preset
uv run gensampledata init scenarios/my-demo.toml --preset typo-heavy
uv run gensampledata validate scenarios/retail-demo.toml
uv run gensampledata preview scenarios/retail-demo.toml --people 5

# Output paths must be new: existing runs are never overwritten
uv run gensampledata generate scenarios/retail-demo.toml --output artifacts/runs/demo-001
uv run gensampledata verify artifacts/runs/demo-001
```

Commands print JSON to stdout. Generation progress goes to stderr. Invalid configuration, failed verification, and output conflicts return a nonzero exit status.

### Configuration semantics

The included `scenarios/retail-demo.toml` is a complete, editable configuration. Presets include `retail-demo`, `clean-retail`, `typo-heavy`, `sparse-identifiers`, and `daily-activity`.

- `people`: exact base people in each customer system, before extra copies. POS overlap determines which POS people also occur in the primary population.
- `duplicate_fraction`: fraction of base people receiving extra records in duplicate-capable sources. At 1,000 people, 0.15 and one copy creates 1,150 customer rows. The realized duplicate fraction of final rows is about 13%, and is reported separately.
- `copies_per_duplicate`: extra records per selected person.
- `overlap_fraction`: fraction of the POS base population shared with primary people. Independent of duplication.
- `corruption_probability`: probability of changing supported mutable fields on extra copies.
- `missing_probability`: probability of making supported mutable fields empty, on originals and copies. Record keys are never corrupted.
- `reference_time`: explicit timezone-aware timestamp used to anchor activity windows.
- `date_mode`: `relative` rebases original activity timestamps to `reference_time`, preserving their distribution; `original` samples their historical values unchanged.
- `field_pools`: optional assignments from any original support dataset to existing output fields. Identity and foreign-key fields are protected.
- `seed`: deterministic random streams. Changing batch size or adding an unrelated source does not change customer records.
- `formats`: `csv`, `jsonl`, or both. Transactions require their parent customer source.

Names, email addresses, phones, addresses, and postal identifiers support mutations. This release intentionally starts with the retail model and controlled presets; it does not infer arbitrary schemas or model household life histories.

### Complete original support data

All 36 original files in `support/` are used or available as field pools and included in both package distributions, byte for byte. This includes all 300,000 phones, 100,000 companies, 50,120 surnames, and 63,210 structured geography records. Ordering, duplicates, formatting, and original values are retained. `src/sampledata/data/corpus.json` is now an integrity catalog of the complete files, rather than a reduced collection of samples.

Inspect the catalog or a bounded sample:

```sh
uv run gensampledata corpus
uv run gensampledata corpus --dataset companies.csv --limit 5
```

The workbench's **Original support data** panel exposes every dataset and lets you assign a pool to an existing field. CLI and library scenarios accept the same assignments:

```toml
[field_pools]
"wifi.device_uuid" = "macaddr.csv"
"custom.StreetAddress" = "streetnames2.csv"
```

Overrides sample stored strings, including historical date strings. They preserve the selected pool's original values; choose a pool appropriate to the destination field. The common-name file is one wide CSV row, nickname and geography files contain structured rows, and other pools are line-oriented. Commas inside companies, job titles, user agents, and coordinate strings are preserved. Default city/state pairs come from the structured geography records; postal codes are sampled independently from the complete postal pool.

Activity timestamps use the original one-hour, daily, and five-year distributions. `date_mode = "relative"` shifts those distributions to the scenario reference time; `"original"` retains their historical dates. Stored birthdate and loyalty creation-date strings remain original values in either mode. To intentionally update source data, preserve its contents and rebuild the catalog with `python tools/build_corpus.py`; runtime integrity checks reject unexpected changes. Keep `support/` intact when retiring legacy code.

### Output and identity

The `legacy-retail` profile keeps the original column names, column order, and dated filename fragments. IDs and postal codes remain strings. CSV is fully quoted; JSONL uses one JSON object per line. It fixes the old custom-object email concatenation and several inconsistent legacy output behaviors, so generated values are not byte-for-byte reproductions of the old algorithm.

Each completed run contains:

- Dataset files such as `2026_11_04Customers.csv` and `2026_11_04Customers.jsonl`.
- `ground_truth.csv`: source, one-based row number, record ID, person identity, and duplicate flag. Sources without an ID column are mapped by row number.
- `mutations.jsonl`: actual field changes, including original and changed values.
- `scenario.json`: resolved configuration; tracer inputs, if any, are snapshotted under `inputs/`.
- `environment.lock`: the exact project dependency lockfile used for the release.
- `manifest.json`: counts, realized rates, checksums, engine/corpus/Python versions, and verification results.

Keep the answer key separate from inputs to your matching system. Reproducibility means identical **dataset checksums** with the same engine, corpus, scenario, and locked environment. Operational metadata such as run creation and elapsed time naturally differs.

Generation streams records into a unique staging directory. Verification checks hashes, schemas, CSV/JSONL parity, IDs, answer-key correspondence, row counts, foreign keys, and mutation references before publication. Failed and cancelled runs retain `.partial` diagnostics without a completion manifest. No shared ID counters or append-in-place outputs are used. Following a hard process crash, confirm no process is using an output before removing its specific stale `.lock` file, or choose a different output name.

### Historical transaction replay

Select the existing run explicitly. Its customer rows and person identities are preserved, and new daily transactions are generated against those IDs:

```sh
uv run gensampledata replay artifacts/runs/demo-001 \
  --output artifacts/runs/daily-002 \
  --reference-time 2026-11-05T00:00:00Z
```

The parent manifest checksum is recorded. This replaces implicit archive searches and append-to-today behavior with separate, ingestible runs.

### Known QA / tracer records

Set `tracer_file = "../tracer_orig/tracer_ecomm.csv"` in a scenario. Paths resolve relative to the scenario file. The original customer CSV format is accepted; an optional `master_id` groups known matching records in the answer key without adding it to dataset columns.

Inputs are retained by default. To consume only that input after successful generation:

```sh
uv run gensampledata generate scenarios/tracer.toml \
  --output artifacts/runs/tracer-001 --consume-tracer
```

The one consumed input moves into a sibling `processed/` directory. Unrelated files are untouched, and inputs remain in place on generation failure. The output retains an input snapshot. Browser scenarios cannot read arbitrary local paths; use the CLI for tracer imports.

### S3 publication

Configure AWS through the normal boto3 credential chain, then explicitly publish a verified run:

```sh
uv run gensampledata publish artifacts/runs/demo-001 \
  --bucket my-demo-bucket --prefix demos/retail
```

Files use an immutable run prefix and AES256 server-side encryption. The publication manifest is uploaded last. Retries of completed identical publications are no-ops. Dataset and scenario files are included by default; add `--include-truth` to also publish answer keys, mutations, and tracer snapshots. To change that selection after publication, choose another prefix. No AWS credentials are stored in scenarios or browser state.

### Python library

```python
from pathlib import Path
from sampledata.config import Scenario
from sampledata.engine import preview
from sampledata.runs import generate, verify

scenario = Scenario(people=1001, seed=17)
records = preview(scenario, people=3)
manifest = generate(scenario, Path("artifacts/runs/python-001"))
assert verify(Path("artifacts/runs/python-001"))["valid"]
```

### Original entry point

After installation, `uv run python gensampledata.py` translates the repository's `settings.py` into a modern run under `artifacts/runs/`. It does not truncate old files, consume tracers, or automatically upload to S3. Arguments are forwarded to the new CLI. Implicit archival replay and random rate variance require explicit scenarios or replay commands instead. The unused original generator modules have been removed. The original `support/` files are actively supported runtime data and must be retained. `datatypemap.py` and `tracer_orig/tracer_ecomm.csv` remain as compatibility test fixtures.

## Verification and benchmarks

```sh
uv run pytest -q
uv run ruff check src tests
npm --prefix frontend test
npm --prefix frontend run build
uv build --out-dir artifacts/dist

uv run gensampledata benchmark --sizes 1000 100000 1000000 \
  --output artifacts/benchmark.json
```

The benchmark uses actual customer and order files, the same settings at every size, and full verification. It reports wall time, peak resident memory, record counts, bytes, and throughput. Temporary output is removed after each measurement. Larger source sets and JSONL output have different costs; the measured workload is recorded with the results. Benchmarking currently supports macOS and Linux.

GitHub Actions checks the Python suite on 3.14 and 3.15, builds and tests the frontend, and builds the distribution. See [architecture and contracts](docs/architecture.md) for design details.
