import csv
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest

from sampledata.config import Scenario
from sampledata.operations import publish, replay_transactions
from sampledata.runs import CancelledError, checksum, generate, json_write, verify
from sampledata.schema import SCHEMAS, filename


def test_failed_or_cancelled_run_cannot_appear_complete(tmp_path):
    scenario = Scenario(people=10)
    output = tmp_path / "run"
    with pytest.raises(CancelledError):
        generate(scenario, output, cancelled=lambda: True)
    assert not output.exists()
    partial = next(tmp_path.glob(".*.partial"))
    assert (partial / "failure.json").exists()
    assert not (partial / "manifest.json").exists()
    assert not (tmp_path / ".run.lock").exists()
    assert generate(scenario, output)["verification"]["valid"]


def test_overwrite_and_concurrent_output_reservation(tmp_path):
    output = tmp_path / "run"
    generate(Scenario(people=1), output)
    original = checksum(output / "manifest.json")
    with pytest.raises(FileExistsError):
        generate(Scenario(people=2), output)
    assert checksum(output / "manifest.json") == original
    lock = tmp_path / ".other.lock"
    lock.touch()
    with pytest.raises(FileExistsError):
        generate(Scenario(people=1), tmp_path / "other")
    assert lock.exists()


def test_corrupted_file_is_detected(tmp_path):
    output = tmp_path / "run"
    generate(Scenario(people=5), output)
    (output / "ground_truth.csv").write_text("broken")
    with pytest.raises(ValueError, match="checksum"):
        verify(output)


def rewrite_checksum(directory, name):
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["files"][name].update(
        sha256=checksum(directory / name), bytes=(directory / name).stat().st_size
    )
    json_write(directory / "manifest.json", manifest)


def test_recomputed_checksums_do_not_hide_broken_foreign_keys(tmp_path):
    scenario = Scenario(people=3, sources=("customers", "transactions"), formats=("csv",))
    output = tmp_path / "run"
    generate(scenario, output)
    name = filename("transactions", scenario.reference_time) + ".csv"
    with (output / name).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["customer_id"] = "missing-customer"
    with (output / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, SCHEMAS["transactions"])
        writer.writeheader()
        writer.writerows(rows)
    rewrite_checksum(output, name)
    with pytest.raises(ValueError, match="foreign key"):
        verify(output)


def test_recomputed_checksums_do_not_hide_format_disagreement(tmp_path):
    scenario = Scenario(people=3, sources=("customers",))
    output = tmp_path / "run"
    generate(scenario, output)
    name = filename("customers", scenario.reference_time) + ".jsonl"
    rows = [json.loads(line) for line in (output / name).read_text().splitlines()]
    rows[0]["name_first"] = "different"
    (output / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
    rewrite_checksum(output, name)
    with pytest.raises(ValueError, match="CSV/JSONL"):
        verify(output)


def make_tracer(path: Path):
    original = Path(__file__).parents[1] / "tracer_orig/tracer_ecomm.csv"
    path.write_bytes(original.read_bytes())


def test_tracer_is_snapshotted_without_deleting_other_inputs(tmp_path):
    tracer = tmp_path / "tracer.csv"
    make_tracer(tracer)
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep")
    scenario = Scenario(people=2, sources=("customers",), tracer_file=str(tracer))
    manifest = generate(scenario, tmp_path / "first")
    assert tracer.exists()
    assert manifest["counts"]["customers"] > 2
    generate(scenario, tmp_path / "second", consume_tracer=True)
    assert not tracer.exists()
    assert unrelated.read_text() == "keep"
    assert (tmp_path / "second/inputs/tracer.csv").exists()
    assert len(list((tmp_path / "processed").glob("*.csv"))) == 1


def test_tracer_lock_is_owned_and_input_retained_on_failure(tmp_path):
    tracer = tmp_path / "tracer.csv"
    make_tracer(tracer)
    lock = tmp_path / "tracer.csv.lock"
    lock.touch()
    scenario = Scenario(people=1, sources=("customers",), tracer_file=str(tracer))
    with pytest.raises(FileExistsError):
        generate(scenario, tmp_path / "locked", consume_tracer=True)
    assert tracer.exists() and lock.exists()
    lock.unlink()
    with pytest.raises(CancelledError):
        generate(scenario, tmp_path / "cancelled", consume_tracer=True, cancelled=lambda: True)
    assert tracer.exists() and not lock.exists()


def test_historical_replay_preserves_person_and_customer_ids(tmp_path):
    old = tmp_path / "old"
    scenario = Scenario(people=4)
    generate(scenario, old)
    output = tmp_path / "new"
    result = replay_transactions(old, output, scenario.reference_time + timedelta(days=1))
    assert result["verification"]["valid"]
    old_name = filename("customers", scenario.reference_time) + ".csv"
    new_name = filename("customers", scenario.reference_time + timedelta(days=1)) + ".csv"
    assert (old / old_name).read_bytes() == (output / new_name).read_bytes()
    assert result["provenance"]["parent_manifest_sha256"] == checksum(old / "manifest.json")


class MissingObject(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class S3:
    def __init__(self, fail=False):
        self.calls = []
        self.objects = {}
        self.fail = fail

    def get_object(self, **kwargs):
        key = kwargs["Key"]
        if key not in self.objects:
            raise MissingObject()
        return {"Body": io.BytesIO(self.objects[key])}

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.calls.append(("upload", key))
        if self.fail:
            raise OSError("upload interrupted")
        self.objects[key] = Path(path).read_bytes()

    def put_object(self, **kwargs):
        self.calls.append(("complete", kwargs["Key"]))
        self.objects[kwargs["Key"]] = kwargs["Body"]


def test_s3_manifest_last_truth_opt_in_and_idempotent_retry(tmp_path):
    output = tmp_path / "run"
    generate(Scenario(people=3), output)
    client = S3()
    result = publish(output, "demo-bucket", client=client)
    assert not result["already_published"]
    assert client.calls[-1][0] == "complete"
    assert not any("ground_truth" in key or "mutations" in key for _, key in client.calls)
    before = len(client.calls)
    assert publish(output, "demo-bucket", client=client)["already_published"]
    assert len(client.calls) == before


def test_failed_upload_has_no_completion_marker(tmp_path):
    output = tmp_path / "run"
    generate(Scenario(people=1), output)
    client = S3(fail=True)
    with pytest.raises(OSError):
        publish(output, "demo-bucket", include_truth=True, client=client)
    assert not any(call[0] == "complete" for call in client.calls)
    client.fail = False
    publish(output, "demo-bucket", include_truth=True, client=client)
    assert any("ground_truth.csv" in key for _, key in client.calls)
