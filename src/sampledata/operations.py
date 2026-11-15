"""Explicit historical replay and idempotent manifest-last S3 publication."""

import csv
import json
from pathlib import Path

from .config import Scenario
from .engine import Engine, Record
from .runs import checksum, dataset_rows, generate, verify
from .schema import SCHEMAS, filename


def replay_transactions(
    previous: Path, output: Path, reference_time, seed: int = 42, max_transactions: int = 4
) -> dict:
    verify(previous)
    old = json.loads((previous / "manifest.json").read_text())
    old_scenario = Scenario.model_validate(old["scenario"])
    sources = tuple(
        source
        for source in ("customers", "transactions", "pos", "pos_transactions")
        if source.replace("_transactions", "") in old_scenario.sources
        or (source == "transactions" and "customers" in old_scenario.sources)
    )
    if not sources:
        raise ValueError("historical run has no customer sources to replay")
    scenario = Scenario(
        name="daily-replay",
        sources=sources,
        people=old_scenario.people,
        seed=seed,
        reference_time=reference_time,
        transaction_window="daily",
        max_transactions=max_transactions,
        duplicate_fraction=old_scenario.duplicate_fraction,
        copies_per_duplicate=old_scenario.copies_per_duplicate,
        overlap_fraction=old_scenario.overlap_fraction,
        corruption_probability=0,
        missing_probability=0,
    )
    engine = Engine(scenario)

    def records():
        for source, child in (("customers", "transactions"), ("pos", "pos_transactions")):
            if source not in sources:
                continue
            fmt = old_scenario.formats[0]
            path = previous / (filename(source, old_scenario.reference_time) + "." + fmt)
            rows = dataset_rows(path, fmt, SCHEMAS[source])
            with (previous / "ground_truth.csv").open(newline="") as handle:
                truth = (row for row in csv.DictReader(handle) if row["source"] == source)
                for index, (row, link) in enumerate(zip(rows, truth, strict=True)):
                    yield Record(
                        source,
                        link["record_id"],
                        link["entity_id"],
                        row,
                        row.copy(),
                        [],
                        link["is_duplicate"] == "1",
                    )
                    yield from engine.transactions(
                        index, child, row["customer_id"], link["entity_id"]
                    )

    return generate(
        scenario,
        output,
        records=records(),
        provenance={
            "kind": "historical-transaction-replay",
            "parent_manifest_sha256": checksum(previous / "manifest.json"),
        },
    )


def _publish(
    directory: Path,
    bucket: str,
    prefix: str = "gensampledata",
    include_truth: bool = False,
    client=None,
) -> dict:
    verify(directory)
    if not bucket.strip() or any(char in bucket for char in ("/", "\\", ":")):
        raise ValueError("provide an S3 bucket name, without s3:// or a path")
    if client is None:
        try:
            import boto3
        except ImportError as exc:
            raise ValueError("install the s3 extra: uv sync --extra s3") from exc
        client = boto3.client("s3")
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    identity = checksum(manifest_path)
    run_prefix = "/".join(part for part in (prefix.strip("/"), identity) if part)
    completion_key = run_prefix + "/manifest.json"
    # An existing completion marker makes retries a no-op. No marker means retry all files.
    try:
        existing = client.get_object(Bucket=bucket, Key=completion_key)
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code not in ("404", "NoSuchKey", "NotFound"):
            raise
    else:
        remote = json.loads(existing["Body"].read())
        if (
            remote.get("source_manifest_sha256") != identity
            or remote.get("include_truth") != include_truth
        ):
            raise ValueError("publication destination already contains a different export")
        return {"bucket": bucket, "prefix": run_prefix, "already_published": True}
    roles = {"dataset", "scenario", "environment"}
    if include_truth:
        roles.update(("ground_truth", "mutations", "input"))
    selected = {name: value for name, value in manifest["files"].items() if value["role"] in roles}
    for name in sorted(selected):
        client.upload_file(
            str(directory / name),
            bucket,
            run_prefix + "/" + name,
            ExtraArgs={"ServerSideEncryption": "AES256"},
        )
    publication = {
        "publication_version": 1,
        "status": "complete",
        "source_manifest_sha256": identity,
        "include_truth": include_truth,
        "files": selected,
        "counts": manifest["counts"],
        "engine_version": manifest["engine_version"],
    }
    client.put_object(
        Bucket=bucket,
        Key=completion_key,
        Body=json.dumps(publication).encode(),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return {
        "bucket": bucket,
        "prefix": run_prefix,
        "already_published": False,
        "files": len(selected),
    }


def publish(
    directory: Path,
    bucket: str,
    prefix: str = "gensampledata",
    include_truth: bool = False,
    client=None,
) -> dict:
    try:
        return _publish(directory, bucket, prefix, include_truth, client)
    except Exception as exc:
        if type(exc).__module__.startswith("botocore."):
            raise ValueError(f"S3 publication failed: {exc}") from exc
        raise
