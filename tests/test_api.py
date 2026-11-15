import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from sampledata.api import create_app
from sampledata.config import Scenario


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "workbench"), base_url="http://127.0.0.1") as value:
        yield value


def test_shared_validation_preview_and_scenario_versions(client):
    config = Scenario(people=5).model_dump(mode="json")
    assert client.get("/api/meta").status_code == 200
    assert client.post("/api/validate", json=config).json()["valid"]
    assert client.post("/api/preview", json={"scenario": config, "people": 2}).json()["records"]
    assert client.post("/api/scenarios", json=config).json()["version"] == 1
    assert client.post("/api/scenarios", json=config).json()["version"] == 2
    assert client.get("/api/scenarios").json()[0]["version"] == 2
    assert len(client.get(f"/api/scenarios/{config['name']}/versions").json()) == 2
    text = client.post("/api/scenarios/export", json=config).text
    assert (
        client.post("/api/scenarios/import", json={"text": text, "format": "toml"}).json() == config
    )
    assert client.post("/api/scenarios/import", json={"text": "broken = ["}).status_code == 422


def test_bad_scenarios_local_paths_and_remote_origins_rejected(client):
    config = Scenario().model_dump(mode="json")
    assert client.post("/api/jobs", json={**config, "people": -1}).status_code == 422
    assert (
        client.post("/api/jobs", json={**config, "tracer_file": "/etc/passwd"}).status_code == 422
    )
    assert (
        client.post(
            "/api/jobs", json=config, headers={"Origin": "https://unrelated.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/meta", headers={"Host": "unrelated.example"}).status_code == 400


def test_full_support_catalog_and_field_overrides(client):
    catalog = client.get("/api/corpus").json()
    assert catalog["file_count"] == 36
    assert catalog["datasets"]["phones.csv"]["entries"] == 300000
    assert client.get("/api/corpus/common_given_names?limit=3").json()["entries"] == 5163
    assert len(client.get("/api/corpus/phones.csv?limit=3").json()["sample"]) == 3
    assert client.get("/api/corpus/missing.csv").status_code == 422
    config = Scenario(
        people=1,
        sources=("wifi",),
        missing_probability=0,
        field_pools={"wifi.device_uuid": "macaddr"},
    ).model_dump(mode="json")
    preview = client.post("/api/preview", json={"scenario": config, "people": 1}).json()
    from sampledata.corpus import corpus

    assert preview["records"][0]["data"]["device_uuid"] in corpus()[0].pool("macaddr")


def wait_job(client, job_id):
    for _ in range(300):
        value = client.get(f"/api/jobs/{job_id}").json()
        if value["status"] not in ("queued", "running", "verifying"):
            return value
        time.sleep(0.02)
    pytest.fail("worker did not finish")


def test_real_worker_to_verified_download(client):
    config = Scenario(people=12).model_dump(mode="json")
    response = client.post("/api/jobs", json=config)
    assert response.status_code == 202
    job_id = response.json()["id"]
    completed = wait_job(client, job_id)
    assert completed["status"] == "complete", completed
    manifest = client.get(f"/api/jobs/{job_id}/manifest").json()
    assert manifest["verification"]["valid"]
    assert client.post(f"/api/jobs/{job_id}/verify").json()["valid"]
    sample = client.get(f"/api/jobs/{job_id}/sample/customers").json()
    assert len(sample["rows"]) == manifest["counts"]["customers"]
    name = next(key for key, value in manifest["files"].items() if value["role"] == "dataset")
    assert client.get(f"/api/jobs/{job_id}/download/{name}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/download/workbench.db").status_code == 404
    archive = client.get(f"/api/jobs/{job_id}/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert "ground_truth.csv" in bundle.namelist()
        assert name in bundle.namelist()


def test_real_worker_cancellation(client):
    config = Scenario(people=1000000).model_dump(mode="json")
    job_id = client.post("/api/jobs", json=config).json()["id"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert wait_job(client, job_id)["status"] == "cancelled"
    assert client.get(f"/api/jobs/{job_id}/manifest").status_code == 409


def test_second_server_cannot_interrupt_a_live_workspace(tmp_path):
    root = tmp_path / "shared-workspace"
    with TestClient(create_app(root), base_url="http://127.0.0.1") as first:
        with pytest.raises(ValueError, match="already open"):
            with TestClient(create_app(root), base_url="http://127.0.0.1"):
                pass
        assert first.get("/api/meta").status_code == 200
    # Closing the owning server releases the lease for a normal restart.
    with TestClient(create_app(root), base_url="http://127.0.0.1") as restarted:
        assert restarted.get("/api/meta").status_code == 200
