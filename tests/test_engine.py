import csv
import json
import random
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from sampledata.config import SOURCES, Scenario, load_scenario, scenario_toml
from sampledata.engine import Engine, corrupt, preview
from sampledata.runs import generate, verify
from sampledata.schema import SCHEMAS, filename


def test_legacy_column_contract():
    legacy = runpy.run_path(str(Path(__file__).parents[1] / "datatypemap.py"))
    mappings = {
        "customers": "primaryCustomerColumns",
        "transactions": "transColumnData",
        "loyalty": "lmsColumns",
        "email": "emailColumns",
        "clickstream": "clickstreamColumns",
        "mobile": "mobileColumns",
        "wifi": "wifiColumns",
        "safety": "safetyReportColumns",
        "locations": "tiledataReportColumns",
    }
    for source, key in mappings.items():
        assert SCHEMAS[source] == tuple(legacy[key])


@pytest.mark.parametrize("people", [0, 1, 500, 1001, 2500])
def test_exact_base_counts_and_formats(tmp_path, people):
    scenario = Scenario(people=people, sources=("customers",), duplicate_fraction=0, batch_size=67)
    manifest = generate(scenario, tmp_path / "run")
    assert manifest["counts"]["customers"] == people
    assert verify(tmp_path / "run")["unique_entities"] == people


@pytest.mark.parametrize(
    "values",
    [
        {"people": -1},
        {"people": 1.2},
        {"people": True},
        {"duplicate_fraction": 1.1},
        {"missing_probability": float("nan")},
        {"reference_time": "2026-11-04T00:00:00"},
        {"sources": []},
        {"sources": ["transactions"]},
        {"sources": ["customers", "customers"]},
        {"formats": []},
        {"batch_size": 0},
        {"unexpected": 1},
        {"seed": 2**53},
    ],
)
def test_invalid_scenarios(values):
    with pytest.raises(ValidationError):
        Scenario(**values)


def test_scenario_roundtrip(tmp_path):
    scenario = Scenario(people=17, reference_time=datetime(2025, 1, 1, tzinfo=UTC))
    path = tmp_path / "scenario.toml"
    path.write_text(scenario_toml(scenario))
    assert load_scenario(path) == scenario


def test_batch_size_and_source_independence(tmp_path):
    base = Scenario(people=23, sources=("customers", "transactions"), batch_size=3)
    first = generate(base, tmp_path / "first")
    second = generate(
        base.model_copy(update={"batch_size": 10, "sources": SOURCES}), tmp_path / "second"
    )
    for source in base.sources:
        for fmt in base.formats:
            name = filename(source, base.reference_time) + "." + fmt
            assert first["files"][name]["sha256"] == second["files"][name]["sha256"]


@pytest.mark.parametrize("rate", [0, 0.15, 1])
def test_exact_duplicate_and_overlap_selection(tmp_path, rate):
    scenario = Scenario(
        people=31, duplicate_fraction=rate, overlap_fraction=rate, copies_per_duplicate=2
    )
    records = list(Engine(scenario).records())
    customers = [row for row in records if row.source == "customers"]
    pos = [row for row in records if row.source == "pos"]
    assert len(customers) == 31 + round(31 * rate) * 2
    assert sum(row.duplicate for row in customers) == round(31 * rate) * 2
    assert sum(row.entity_id.startswith("E") for row in pos if not row.duplicate) == round(
        31 * rate
    )
    manifest = generate(scenario, tmp_path / "run")
    assert manifest["counts"]["customers"] == len(customers)


@pytest.mark.parametrize(
    "kind", ["name", "given_name", "birthdate", "email", "phone", "postal", "address"]
)
@pytest.mark.parametrize("value", [None, "", "a", "a@b", "02108"])
def test_corruption_boundary_contract(kind, value):
    changed, operation = corrupt(value, kind, random.Random(42))
    assert changed is None if value is None else isinstance(changed, str)
    assert isinstance(operation, str)


def test_original_nickname_data_is_used_without_nan_values():
    changes = [corrupt("Aaron", "given_name", random.Random(seed)) for seed in range(20)]
    assert any(operation == "nickname" for _, operation in changes)
    assert all(value and value.lower() != "nan" for value, _ in changes)


def test_preview_matches_full_run_records():
    scenario = Scenario(people=37)
    actual = list(Engine(scenario).records(limit_people=5))
    shown = preview(scenario, 5)["records"]
    assert [(row.source, row.record_id, row.data) for row in actual] == [
        (row["source"], row["record_id"], row["data"]) for row in shown
    ]


def test_daily_times_are_current_and_ordered():
    scenario = Scenario(people=20, transaction_window="daily")
    start = scenario.reference_time - timedelta(days=1)
    for record in Engine(scenario).records():
        if "purchasedate" in record.data:
            assert (
                start
                <= datetime.fromisoformat(record.data["purchasedate"])
                <= scenario.reference_time
            )
        if record.source == "email":
            assert record.data["EVENT_CAPTURED_DT"] <= record.data["EVENT_STORED_DT"]
        if record.source == "clickstream":
            assert (
                record.data["first_hit_time_gmt"]
                <= record.data["hit_time_gmt"]
                <= record.data["last_hit_time_gmt"]
            )


def test_postal_codes_remain_strings_and_truth_is_separate(tmp_path):
    scenario = Scenario(
        people=40, sources=("customers",), missing_probability=0, duplicate_fraction=0
    )
    directory = tmp_path / "run"
    generate(scenario, directory)
    name = filename("customers", scenario.reference_time)
    rows = [json.loads(line) for line in (directory / (name + ".jsonl")).read_text().splitlines()]
    assert any(row["postal_code"].startswith("0") for row in rows)
    assert all(isinstance(row["postal_code"], str) and "entity_id" not in row for row in rows)
    with (directory / (name + ".csv")).open(newline="") as handle:
        assert list(csv.DictReader(handle))[0]["postal_code"] == rows[0]["postal_code"]
