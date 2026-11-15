# Generation and publication contracts

The Python library in `src/sampledata/` is shared by the command line and the local workbench. The unused original top-level generator modules have been removed. The compatibility entry point translates `settings.py` through `sampledata.legacy` without importing pandas. The original schema and tracer fixtures remain for compatibility tests, and every original support file remains available to the runtime.

## Scenario semantics

- `people` is the exact base population of each customer system, before copies. POS has its own population of this size; `overlap_fraction` determines how many are the same underlying people as the primary population. POS-only people have a separate entity namespace.
- `duplicate_fraction` selects exactly `round(people * duplicate_fraction)` base people in each duplicate-capable source. Each gets `copies_per_duplicate` extra records. This is a fraction of base people, not the duplicate fraction of final output rows. Customers, POS, loyalty, Wi-Fi, and custom objects support copies. Events are separately generated.
- `corruption_probability` applies to supported mutable fields on extra copies. `missing_probability` applies to supported fields on originals and copies. Keys are never corrupted. `mutations.jsonl` records changes that actually altered values.
- Person and source-specific random streams are derived with BLAKE2. Selection uses a deterministic modular permutation, avoiding an in-memory population-wide shuffle. Batch size and enabled unrelated sources cannot change another source's records.
- All 36 original support files are packaged byte for byte. A catalog records their parsers, entry counts, lengths, and SHA-256 checksums; runtime reads validate integrity. Pools retain ordering and duplicates. Structured geography supplies city/state pairs; the complete postal pool is sampled independently.
- Activity timestamps sample the original hour/day/five-year pools. Relative mode rebases their distribution to the explicit UTC reference time; original mode retains historical timestamps. Stored birthdates and loyalty creation dates remain original strings.
- Every support dataset is inspectable through the CLI, API, and workbench. Scenario field-pool assignments can sample any complete pool into existing non-identity fields, with separate deterministic random streams. Line-oriented pools preserve embedded commas; wide and structured CSV files have explicit parsers.
- `legacy-retail` preserves column names, order, and dated filename fragments. It intentionally corrects inconsistent JSON, invalid custom-object email concatenation, stale dates, and unsafe overwriting. Values and distributions are versioned engine behavior, not byte-for-byte reproduction of the 2018 algorithm. IDs are now always strings. JSON output uses the `.jsonl` extension.

## Identity and integrity

Every source record has an internal `record_id`, and every person has an `entity_id`. For sources without a record-ID column, `ground_truth.csv` maps the record by source and one-based row number. Neither internal entity IDs nor `master_id` are added to dataset columns. Known tracer clusters use `master_id` in the input only; it is translated to ground truth.

Hard foreign keys are verified for orders, email, clickstream, safety, and location records when the corresponding parent customer source is present. Loyalty and email-based identity relationships are represented in the answer key; they are not falsely declared to have a literal shared customer-ID column.

## Run lifecycle

Generation reserves an output name with an exclusive lock. Writers stream into a unique sibling `.partial` directory. A cancelled or failed run retains diagnostic files there but has no completion manifest. Retrying uses a fresh staging directory; published output is never overwritten. A crashed process can leave its reservation lock: confirm no process is using that output before manually removing the specific lock. A different output name remains usable.

Completed output includes CSV/JSONL datasets, ground truth, mutations, the resolved scenario, input snapshots where applicable, and a manifest. Before directory rename, verification checks file hashes and sizes, schemas, CSV/JSONL parity, unique record IDs, truth correspondence, row counts, declared foreign keys, and mutation references. SQLite scratch storage in project-local `tmp/` keeps verification memory bounded. Rename publication requires staging and destination on the same filesystem.

The manifest records engine and Python versions, corpus checksum, validation-library version, configuration, realized row counts and duplicate fractions, and dataset checksums. Each run snapshots `uv.lock` as `environment.lock` and records its checksum. Commit the lockfile with each engine release to reproduce the dependency environment. Operational metadata such as elapsed time and creation timestamp is intentionally not deterministic; dataset hashes are.

Tracer inputs are retained by default. `--consume-tracer` locks the one input, snapshots it, completes the run, verifies the input checksum again, and moves only that input to a `processed/` directory. Other files are untouched. A consumption warning is reported in the completed manifest. File inputs are a CLI capability; the browser API cannot read arbitrary paths.

Historical replay requires a selected verified run. It copies that run's customer records and ground-truth identities, then generates a new daily transaction window. The parent manifest checksum records provenance. There is no implicit archive glob or shared counter state. Append-in-place is deliberately replaced by immutable runs and explicit replay; downstream ingestion should use completion manifests as its checkpoint.

## Workbench

FastAPI binds to loopback. An OS-level workspace lease prevents a second server from interrupting active jobs in the same data directory. A React/TypeScript editor uses the same scenario model as the CLI. Scenario versions and run status are stored in a local SQLite database. Generation runs in separate spawned worker processes, with a maximum of two active runs. Cancellation is cooperative at processing checkpoints. Verification may finish its current scan before cancellation is observed. Server shutdown cancels workers; jobs interrupted by process failure are marked interrupted when the server restarts.

The app offers saved versions, cloning, TOML/JSON import, TOML export, source selection, quality controls, linked-record preview, run progress, samples, file downloads, verification, and optional S3 publication. UI-generated runs are capped at one million base people; the CLI supports up to ten million. These are validation limits, not throughput guarantees.

This release is a local tool. Hosted multi-user authentication, cross-host job scheduling, shared storage quotas, and distributed generation are separate deployment work.

## S3

The S3 client uses boto3's normal credential chain and server-side AES256 encryption. Each immutable manifest hash gets its own prefix. Only dataset and scenario files are uploaded by default. Answer keys, mutation history, and tracer snapshots require `--include-truth`. All selected files are uploaded before the publication manifest. A failed upload has no completion marker; retry uploads the files again. A completed identical publication is a no-op. The remote publication manifest lists the exported subset and is distinct from a complete local run manifest. Credentials are never stored in scenario files or browser state.

## Scale measurements

`gensampledata benchmark` runs each size in a separate subprocess, generates actual files, performs full verification, measures wall time and peak resident memory, and cleans only its own temporary output. The standard workload uses customers and orders, CSV, a fixed seed, 15% of base people receiving a copy, and at most two orders per customer record. It measures the whole pipeline, not only the inner loop. Other source combinations and JSONL exports need their own measurements.
