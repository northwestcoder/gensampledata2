"""Streaming exports, isolated publication, and independently checked run contracts."""

import csv
import hashlib
import importlib.metadata
import itertools
import json
import os
import platform
import shutil
import sqlite3
import tempfile
import time
import uuid
from collections import Counter
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Callable, Iterator

from . import __version__
from .config import Scenario
from .engine import Engine, Record, timestamp, token
from .schema import KEYS, SCHEMAS, filename

TRUTH_COLUMNS = ("source", "row_number", "record_id", "entity_id", "is_duplicate")


class CancelledError(Exception):
    pass


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def safe_file(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe run file: {name}")
    return path


def tracer_records(path: Path, scenario: Scenario) -> Iterator[Record]:
    """Accept the original QA format; master_id stays exclusively in ground truth."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = set(SCHEMAS["customers"]) - {"customer_id", "dtUpdateDate"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("tracer CSV is missing customer columns")
        for index, raw in enumerate(reader):
            if None in raw or any(raw.get(key) is None for key in required):
                raise ValueError(f"malformed tracer row {index + 1}")
            entity = "T" + token(raw.get("master_id") or raw.get("customer_id") or str(index))
            record_id = "tracer-" + token(f"{scenario.seed}:{index}:{entity}")
            row = {key: raw.get(key, "") for key in SCHEMAS["customers"]}
            row.update(customer_id=record_id, dtUpdateDate=timestamp(scenario.reference_time))
            yield Record("customers", record_id, entity, row, row.copy(), [])


class RunWriter:
    def __init__(self, directory: Path, scenario: Scenario):
        self.directory, self.scenario = directory, scenario
        self.counts: Counter = Counter()
        self.duplicates: Counter = Counter()
        self.mutation_count = 0
        self.stack = ExitStack()
        self.writers = {}
        self.descriptors = {}
        for source in scenario.sources:
            for fmt in scenario.formats:
                name = filename(source, scenario.reference_time) + "." + fmt
                handle = self.stack.enter_context(
                    (directory / name).open("w", newline="", encoding="utf-8")
                )
                if fmt == "csv":
                    writer = csv.DictWriter(handle, SCHEMAS[source], quoting=csv.QUOTE_ALL)
                    writer.writeheader()
                    self.writers[source, fmt] = writer
                else:
                    self.writers[source, fmt] = handle
                self.descriptors[name] = {"role": "dataset", "source": source, "format": fmt}
        truth_handle = self.stack.enter_context(
            (directory / "ground_truth.csv").open("w", newline="", encoding="utf-8")
        )
        self.truth = csv.writer(truth_handle)
        self.truth.writerow(TRUTH_COLUMNS)
        self.lineage = self.stack.enter_context(
            (directory / "mutations.jsonl").open("w", encoding="utf-8")
        )
        self.descriptors["ground_truth.csv"] = {"role": "ground_truth", "format": "csv"}
        self.descriptors["mutations.jsonl"] = {"role": "mutations", "format": "jsonl"}

    def write(self, record: Record) -> None:
        source = record.source
        if set(record.data) != set(SCHEMAS[source]):
            raise ValueError(f"record does not match {source} schema")
        self.counts[source] += 1
        self.duplicates[source] += int(record.duplicate)
        for fmt in self.scenario.formats:
            writer = self.writers[source, fmt]
            if fmt == "csv":
                writer.writerow(record.data)
            else:
                writer.write(
                    json.dumps(
                        record.data, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                    )
                    + "\n"
                )
        self.truth.writerow(
            (source, self.counts[source], record.record_id, record.entity_id, int(record.duplicate))
        )
        if record.mutations:
            self.mutation_count += 1
            self.lineage.write(
                json.dumps(
                    {"source": source, "record_id": record.record_id, "changes": record.mutations},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    def close(self) -> None:
        self.stack.close()


def generate(
    scenario: Scenario,
    output: Path,
    progress: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    consume_tracer: bool = False,
    records: Iterator[Record] | None = None,
    provenance: dict | None = None,
) -> dict:
    """Refuse overwrites; publish a directory only after verification succeeds."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / f".{output.name}.lock"
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.partial"
    tracer_lock = None
    writer = None
    started = time.perf_counter()
    try:
        if output.exists():
            raise FileExistsError(f"run already exists: {output}; choose a new output directory")
        staging.mkdir()
        engine = Engine(scenario)
        snapshot = None
        if scenario.tracer_file:
            path = Path(scenario.tracer_file).resolve()
            if consume_tracer:
                candidate_lock = path.with_name(path.name + ".lock")
                fd = os.open(candidate_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                tracer_lock = candidate_lock
            (staging / "inputs").mkdir()
            snapshot = staging / "inputs/tracer.csv"
            shutil.copyfile(path, snapshot)
        elif consume_tracer:
            raise ValueError("--consume-tracer requires a tracer_file in the scenario")
        writer = RunWriter(staging, scenario)
        notify_every = max(1, scenario.batch_size)
        for index, record in enumerate(records if records is not None else engine.records()):
            if index % notify_every == 0:
                if cancelled and cancelled():
                    raise CancelledError("generation cancelled")
                if progress:
                    progress({"status": "running", "records": index, "counts": dict(writer.counts)})
            writer.write(record)
        if snapshot:
            for record in tracer_records(snapshot, scenario):
                if cancelled and cancelled():
                    raise CancelledError("generation cancelled")
                writer.write(record)
        writer.close()
        descriptors = writer.descriptors
        exported_scenario = scenario.model_dump(mode="json")
        if snapshot:
            exported_scenario["tracer_file"] = "inputs/tracer.csv"
            descriptors["inputs/tracer.csv"] = {"role": "input", "format": "csv"}
        json_write(staging / "scenario.json", exported_scenario)
        descriptors["scenario.json"] = {"role": "scenario", "format": "json"}
        source_lock = Path(__file__).resolve().parents[2] / "uv.lock"
        lock_resource = files("sampledata").joinpath("data/uv.lock")
        if source_lock.is_file():
            shutil.copyfile(source_lock, staging / "environment.lock")
        elif lock_resource.is_file():
            (staging / "environment.lock").write_bytes(lock_resource.read_bytes())
        if (staging / "environment.lock").is_file():
            descriptors["environment.lock"] = {"role": "environment", "format": "uv-lock"}
        for name, descriptor in descriptors.items():
            descriptor.update(
                sha256=checksum(staging / name), bytes=(staging / name).stat().st_size
            )
        counts = {source: writer.counts[source] for source in scenario.sources}
        manifest = {
            "manifest_version": 1,
            "status": "complete",
            "engine_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "scenario": exported_scenario,
            "corpus_sha256": engine.corpus_checksum,
            "corpus_files": engine.data.catalog["datasets"],
            "dependencies": {"pydantic": importlib.metadata.version("pydantic")},
            "python_version": platform.python_version(),
            "dependency_lock_sha256": descriptors.get("environment.lock", {}).get("sha256"),
            "files": descriptors,
            "counts": counts,
            "total_records": sum(counts.values()),
            "mutation_records": writer.mutation_count,
            "duplicate_records": dict(writer.duplicates),
            "duplicate_definition": "fraction of base people receiving copies_per_duplicate additional records",
            "realized_duplicate_row_fraction": {
                key: writer.duplicates[key] / value if value else 0 for key, value in counts.items()
            },
            "primary_people": scenario.people,
            "pos_shared_people": round(scenario.people * scenario.overlap_fraction)
            if "pos" in scenario.sources
            else 0,
            "provenance": provenance,
        }
        json_write(staging / "manifest.json", manifest)
        if progress:
            progress(
                {"status": "verifying", "records": manifest["total_records"], "counts": counts}
            )
        verification = verify(staging)
        if cancelled and cancelled():
            raise CancelledError("generation cancelled before publication")
        manifest["verification"] = verification
        manifest["elapsed_seconds"] = round(time.perf_counter() - started, 4)
        json_write(staging / "manifest.json", manifest)
        staging.rename(output)
        if consume_tracer and snapshot:
            # Consumption failure does not invalidate already completed data; make it explicit.
            original = Path(scenario.tracer_file)
            archive = original.parent / "processed"
            try:
                if checksum(original) != descriptors["inputs/tracer.csv"]["sha256"]:
                    raise ValueError("tracer input changed while generation was running")
                archive.mkdir(exist_ok=True)
                original.rename(archive / f"{original.stem}-{uuid.uuid4().hex}.csv")
                manifest["tracer_consumed"] = True
            except (OSError, ValueError) as exc:
                manifest["tracer_consumed"] = False
                manifest["tracer_warning"] = str(exc)
            json_write(output / "manifest.json", manifest)
        if progress:
            progress({"status": "complete", "records": manifest["total_records"], "counts": counts})
        return manifest
    except BaseException as exc:
        if staging.exists():
            json_write(
                staging / "failure.json",
                {
                    "status": "cancelled" if isinstance(exc, CancelledError) else "failed",
                    "error": str(exc),
                },
            )
            # A staged manifest never certifies a failed or cancelled run.
            (staging / "manifest.json").unlink(missing_ok=True)
        raise
    finally:
        if writer:
            writer.close()
        os.close(lock_fd)
        lock.unlink(missing_ok=True)
        if tracer_lock:
            tracer_lock.unlink(missing_ok=True)


def dataset_rows(path: Path, fmt: str, columns: tuple) -> Iterator[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        if fmt == "csv":
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(columns):
                raise ValueError(f"wrong column order in {path.name}")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"malformed CSV row in {path.name}")
                yield row
        else:
            for line in handle:
                row = json.loads(line)
                if not isinstance(row, dict) or set(row) != set(columns):
                    raise ValueError(f"wrong fields in {path.name}")
                yield row


def verify(directory: Path) -> dict:
    """Verify files, format parity, keys, truth mapping and declared relationships."""
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("manifest_version") != 1:
        raise ValueError("run has no supported completion manifest")
    scenario = Scenario.model_validate(manifest["scenario"])
    descriptors = manifest["files"]
    required = {"ground_truth.csv", "mutations.jsonl", "scenario.json"}
    if not required.issubset(descriptors):
        raise ValueError("run is missing required metadata files")
    for name, info in descriptors.items():
        path = safe_file(directory, name)
        if (
            not path.is_file()
            or path.stat().st_size != info["bytes"]
            or checksum(path) != info["sha256"]
        ):
            raise ValueError(f"checksum/size mismatch: {name}")
    scratch = Path.cwd() / "tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="verify-", dir=scratch) as temporary:
        with closing(sqlite3.connect(str(Path(temporary) / "keys.db"))) as db, db:
            db.executescript("""
                PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-8192;
                CREATE TABLE truth(source TEXT, row_number INTEGER, record_id TEXT, entity_id TEXT,
                    duplicate INTEGER, PRIMARY KEY(source,row_number), UNIQUE(source,record_id));
                CREATE TABLE parents(source TEXT, record_id TEXT, PRIMARY KEY(source,record_id));
                CREATE TABLE refs(parent TEXT, record_id TEXT);
            """)
            try:
                with (directory / "ground_truth.csv").open(newline="") as handle:
                    reader = csv.DictReader(handle)
                    if reader.fieldnames != list(TRUTH_COLUMNS):
                        raise ValueError("invalid ground truth schema")
                    for row in reader:
                        if (
                            row["source"] not in scenario.sources
                            or not row["entity_id"]
                            or not row["record_id"]
                        ):
                            raise ValueError("invalid ground truth record")
                        number, duplicate = int(row["row_number"]), int(row["is_duplicate"])
                        if number < 1 or duplicate not in (0, 1):
                            raise ValueError("invalid truth row number or duplicate flag")
                        db.execute(
                            "INSERT INTO truth VALUES(?,?,?,?,?)",
                            (row["source"], number, row["record_id"], row["entity_id"], duplicate),
                        )
            except sqlite3.IntegrityError as exc:
                raise ValueError("duplicate record ID or ground-truth row") from exc
            actual_counts = {}
            for source in scenario.sources:
                paths = {}
                for fmt in scenario.formats:
                    name = filename(source, scenario.reference_time) + "." + fmt
                    if name not in descriptors or descriptors[name].get("source") != source:
                        raise ValueError(f"missing dataset: {name}")
                    paths[fmt] = directory / name
                streams = [dataset_rows(path, fmt, SCHEMAS[source]) for fmt, path in paths.items()]
                count = 0
                truth_stream = iter(
                    db.execute(
                        "SELECT row_number, record_id FROM truth WHERE source=? ORDER BY row_number",
                        (source,),
                    )
                )
                for count, rows in enumerate(itertools.zip_longest(*streams), 1):
                    if any(row is None for row in rows):
                        raise ValueError(f"format row count mismatch: {source}")
                    normalized = [
                        {key: "" if value is None else str(value) for key, value in row.items()}
                        for row in rows
                    ]
                    if any(row != normalized[0] for row in normalized[1:]):
                        raise ValueError(f"CSV/JSONL mismatch: {source} row {count}")
                    row = rows[0]
                    truth = next(truth_stream, None)
                    if truth is None or truth[0] != count:
                        raise ValueError(f"missing truth row: {source} {count}")
                    if source in KEYS and str(row[KEYS[source]]) != truth[1]:
                        raise ValueError(f"record ID does not match truth: {source}")
                    if source in ("customers", "pos"):
                        db.execute(
                            "INSERT INTO parents VALUES(?,?)", (source, str(row["customer_id"]))
                        )
                    relationship = {
                        "transactions": ("customers", "customer_id"),
                        "pos_transactions": ("pos", "customer_id"),
                        "email": ("customers", "CUSTOMER_ID"),
                        "clickstream": ("customers", "cust_visid"),
                        "safety": ("customers", "customer_id"),
                        "locations": ("customers", "customer_id"),
                    }.get(source)
                    if relationship and relationship[0] in scenario.sources:
                        db.execute(
                            "INSERT INTO refs VALUES(?,?)",
                            (relationship[0], str(row[relationship[1]])),
                        )
                if next(truth_stream, None) is not None:
                    raise ValueError(f"extra truth rows: {source}")
                actual_counts[source] = count
            db.execute("CREATE INDEX refs_key ON refs(parent,record_id)")
            orphan = db.execute(
                "SELECT parent,record_id FROM refs r WHERE NOT EXISTS (SELECT 1 FROM parents p WHERE p.source=r.parent AND p.record_id=r.record_id) LIMIT 1"
            ).fetchone()
            if orphan:
                raise ValueError(f"broken foreign key: {orphan}")
            if (
                actual_counts != manifest["counts"]
                or sum(actual_counts.values()) != manifest["total_records"]
            ):
                raise ValueError("manifest row counts do not match datasets")
            duplicate_counts = dict(
                db.execute("SELECT source,SUM(duplicate) FROM truth GROUP BY source")
            )
            if any(
                duplicate_counts.get(source, 0) != manifest["duplicate_records"].get(source, 0)
                for source in scenario.sources
            ):
                raise ValueError("duplicate counts do not match ground truth")
            mutation_count = 0
            with (directory / "mutations.jsonl").open() as handle:
                for mutation_count, line in enumerate(handle, 1):
                    change = json.loads(line)
                    if (
                        not change["changes"]
                        or not db.execute(
                            "SELECT 1 FROM truth WHERE source=? AND record_id=?",
                            (change["source"], change["record_id"]),
                        ).fetchone()
                    ):
                        raise ValueError("mutation history references an unknown record")
            if mutation_count != manifest["mutation_records"]:
                raise ValueError("mutation count mismatch")
            entities = db.execute("SELECT COUNT(DISTINCT entity_id) FROM truth").fetchone()[0]
    return {
        "valid": True,
        "counts": actual_counts,
        "total_records": sum(actual_counts.values()),
        "unique_entities": entities,
        "checks": [
            "checksums",
            "schemas",
            "format_parity",
            "record_ids",
            "ground_truth",
            "foreign_keys",
            "mutation_history",
        ],
    }
