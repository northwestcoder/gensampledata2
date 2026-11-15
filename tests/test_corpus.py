import csv
import hashlib
import io
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from sampledata.config import Scenario, load_scenario, scenario_toml
from sampledata.corpus import SupportCorpus, corpus
from sampledata.engine import Engine

ORIGINAL = Path(__file__).parents[1] / "support"
FILES = sorted(path.name for path in ORIGINAL.glob("*.csv"))


def test_catalog_covers_every_original_file_without_subsets():
    data, digest = corpus()
    assert len(FILES) == 36
    assert set(data.catalog["datasets"]) == set(FILES)
    assert len(digest) == 64
    assert data.summary()["total_bytes"] == sum(
        path.stat().st_size for path in ORIGINAL.glob("*.csv")
    )


@pytest.mark.parametrize("name", FILES)
def test_all_original_bytes_order_duplicates_and_entries_are_preserved(name):
    data, _ = corpus()
    raw = (ORIGINAL / name).read_bytes()
    assert data.raw(name) == raw
    assert data.catalog["datasets"][name]["sha256"] == hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    if name == "common_given_names.csv":
        expected = tuple(cell for row in csv.reader(io.StringIO(text)) for cell in row)
    else:
        expected = tuple(text.splitlines())
    assert data.pool(name) == expected
    assert len(expected) == data.catalog["datasets"][name]["entries"]


def test_literal_commas_leading_zeros_and_structured_rows():
    data, _ = corpus()
    assert "Wilson, Watts and Edwards" in data.pool("companies")
    assert any("," in value for value in data.pool("jobs"))
    assert len(data.pool("phones")) == 300000
    assert len(data.pool("common_given_names")) == 5163
    assert any(value.startswith("0") for value in data.pool("postalcodes"))
    assert len(data.rows("us_cities_states_counties")) == 63210
    assert all(len(row) == 5 for row in data.rows("us_cities_states_counties"))
    assert len(data.rows("given_name_alternatives")) == 1082
    assert data.rows("given_name_alternatives")[0][4] == "NAN"
    assert all(value != "Nan" for values in data.nicknames.values() for value in values)


@pytest.mark.parametrize("name", FILES)
def test_every_dataset_can_drive_an_output_field(name):
    scenario = Scenario(
        people=1,
        sources=("custom",),
        missing_probability=0,
        field_pools={"custom.StreetAddress": name},
    )
    record = next(Engine(scenario).records())
    assert record.data["StreetAddress"] in corpus()[0].pool(name)


@pytest.mark.parametrize(
    "target,dataset",
    [
        ("customers.customer_id", "phones"),
        ("transactions.customer_id", "phones"),
        ("customers.unknown", "phones"),
        ("unknown.email", "phones"),
        ("customers.phone", "../private"),
        ("customers.phone", "unknown"),
    ],
)
def test_bad_bindings_rejected_before_output(target, dataset):
    with pytest.raises(ValidationError):
        Scenario(field_pools={target: dataset})


def test_bindings_roundtrip_in_saved_scenarios(tmp_path):
    scenario = Scenario(field_pools={"wifi.device_uuid": "macaddr.csv"}, date_mode="original")
    path = tmp_path / "scenario.toml"
    path.write_text(scenario_toml(scenario))
    assert load_scenario(path) == scenario
    assert Scenario.model_validate_json(scenario.model_dump_json()) == scenario


@pytest.mark.parametrize(
    "name,window",
    [("last1hourdates", 3600), ("last24hoursdates", 86400), ("lastfiveyeardates", 1825 * 86400)],
)
def test_timestamp_modes_use_original_samples_without_stale_recent_events(name, window):
    data, _ = corpus()
    reference = datetime(2030, 1, 1, tzinfo=UTC)
    relative = data.sample_time(name, random.Random(42), reference)
    original = data.sample_time(name, random.Random(42), reference, original=True)
    assert reference - timedelta(seconds=window) <= relative <= reference
    assert original.strftime("%Y-%m-%d %H:%M:%S") in data.pool(name)
    next_day = data.sample_time(name, random.Random(42), reference + timedelta(days=1))
    assert next_day - relative == timedelta(days=1)


def test_changed_source_is_detected(tmp_path):
    data = SupportCorpus()
    data.root = tmp_path
    (tmp_path / "phones.csv").write_text("replacement data")
    with pytest.raises(ValueError, match="support dataset changed"):
        data.pool("phones")


def test_default_records_draw_from_original_pools():
    data, _ = corpus()
    engine = Engine(Scenario(people=1, missing_probability=0, duplicate_fraction=0))
    records = {record.source: record.data for record in engine.records()}
    person = records["customers"]
    for field, pool in (
        ("name_last", "lastnames"),
        ("employment", "companies"),
        ("job_title", "jobs"),
        ("phone", "phones"),
        ("birth_dt", "birthdates"),
        ("postal_code", "postalcodes"),
    ):
        assert person[field] in data.pool(pool)
    for source, field, pool in (
        ("loyalty", "current_tier", "loyaltytiers"),
        ("loyalty", "lmProgramName", "loyaltyprograms"),
        ("loyalty", "created", "lmscreatedates"),
        ("safety", "Description", "injuries"),
        ("locations", "TileLocation", "latlongs"),
        ("clickstream", "user_agent", "user_agent"),
        ("clickstream", "ip", "ipv6"),
        ("mobile", "client_ip", "ip_addresses"),
        ("wifi", "sha", "sha256"),
        ("wifi", "device_uuid", "uuid4"),
    ):
        assert records[source][field] in data.pool(pool)


def test_original_timestamp_mode_keeps_event_order_even_before_historical_reference():
    scenario = Scenario(
        people=2, date_mode="original", reference_time=datetime(1990, 1, 1, tzinfo=UTC)
    )
    for record in Engine(scenario).records():
        if record.source == "email":
            assert record.data["EVENT_CAPTURED_DT"] <= record.data["EVENT_STORED_DT"]
        if record.source == "clickstream":
            assert record.data["hit_time_gmt"] <= record.data["last_hit_time_gmt"]
