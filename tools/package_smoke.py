"""Run with a clean wheel installation to detect missing packaged resources."""

import hashlib
import json
import re
import tempfile
from importlib.resources import files
from pathlib import Path

import sampledata
from sampledata.api import create_app
from sampledata.config import Scenario
from sampledata.corpus import corpus
from sampledata.runs import generate, verify


def main():
    location = Path(sampledata.__file__).resolve()
    assert "site-packages" in location.parts, f"Not testing the installed wheel: {location}"
    resources = files("sampledata")
    for name in ("data/corpus.json", "data/uv.lock", "static/index.html"):
        assert resources.joinpath(name).is_file(), f"Missing package resource: {name}"
    data, _ = corpus()
    assert len(data.catalog["datasets"]) == 36
    for name, info in data.catalog["datasets"].items():
        original = resources.joinpath("data/support/" + name).read_bytes()
        assert hashlib.sha256(original).hexdigest() == info["sha256"]
        assert data.raw(name) == original
    index = resources.joinpath("static/index.html").read_text()
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index)
    assert len(assets) >= 2
    for asset in assets:
        assert resources.joinpath("static" + asset).is_file()
    scratch = Path("tmp")
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wheel-smoke-", dir=scratch) as temporary:
        root = Path(temporary)
        output = root / "run"
        manifest = generate(Scenario(people=17), output)
        assert manifest["dependency_lock_sha256"]
        assert manifest["python_version"]
        assert verify(output)["valid"]
        app = create_app(root / "workspace")
        static = next(route for route in app.routes if route.name == "workbench")
        assert "site-packages" in Path(static.app.directory).parts
        results = {
            "installed_wheel": True,
            "packaged_corpus": True,
            "complete_support_files_verified": len(data.catalog["datasets"]),
            "packaged_dependency_lock": True,
            "built_ui_assets": len(assets),
            "generated_records": manifest["total_records"],
            "verified": True,
            "api_uses_packaged_static_files": True,
        }
    target = Path("artifacts/verification/package-smoke.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
