"""Exercise the built workbench against its live loopback server."""

import json
import re
import time
from pathlib import Path

import httpx


def main():
    results = {}
    with httpx.Client(base_url="http://127.0.0.1:8767", timeout=30) as client:
        page = client.get("/")
        page.raise_for_status()
        assert "gensampledata" in page.text
        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.text)
        assert len(assets) >= 2
        for asset in assets:
            response = client.get(asset)
            response.raise_for_status()
            assert len(response.content) > 100
        results["built_ui_assets"] = len(assets)
        meta = client.get("/api/meta").json()
        scenario = meta["presets"]["retail-demo"]
        scenario["people"] = 1001
        assert client.post("/api/validate", json=scenario).json()["valid"]
        saved = client.post("/api/scenarios", json=scenario)
        saved.raise_for_status()
        preview = client.post("/api/preview", json={"scenario": scenario, "people": 5})
        preview.raise_for_status()
        assert preview.json()["records"]
        exported = client.post("/api/scenarios/export", json=scenario)
        exported.raise_for_status()
        imported = client.post(
            "/api/scenarios/import", json={"text": exported.text, "format": "toml"}
        )
        assert imported.json() == scenario
        results["scenario_roundtrip"] = True
        started = client.post("/api/jobs", json=scenario)
        started.raise_for_status()
        job_id = started.json()["id"]

        def wait(job):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                value = client.get(f"/api/jobs/{job}").json()
                if value["status"] not in ("queued", "running", "verifying"):
                    return value
                time.sleep(0.1)
            raise AssertionError("Run did not finish before smoke-test deadline")

        completed = wait(job_id)
        assert completed["status"] == "complete", completed
        manifest = client.get(f"/api/jobs/{job_id}/manifest").json()
        assert client.post(f"/api/jobs/{job_id}/verify").json()["valid"]
        downloaded = 0
        for name in manifest["files"]:
            if "/" not in name:
                response = client.get(f"/api/jobs/{job_id}/download/{name}")
                response.raise_for_status()
                downloaded += 1
        sample = client.get(f"/api/jobs/{job_id}/sample/customers").json()
        assert sample["rows"]
        results.update(
            run_id=job_id,
            total_records=manifest["total_records"],
            downloads=downloaded,
            verified=True,
            samples=True,
        )
        scenario["people"] = 1000000
        scenario["name"] = "cancellation-check"
        cancel_id = client.post("/api/jobs", json=scenario).json()["id"]
        assert client.post(f"/api/jobs/{cancel_id}/cancel").is_success
        assert wait(cancel_id)["status"] == "cancelled"
        assert client.get(f"/api/jobs/{cancel_id}/manifest").status_code == 409
        results["cancelled_run_not_published"] = True
        assert (
            client.post(
                "/api/jobs", json=scenario, headers={"Origin": "https://unrelated.example"}
            ).status_code
            == 403
        )
        results["remote_origin_rejected"] = True
    path = Path("artifacts/verification/http-smoke.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
