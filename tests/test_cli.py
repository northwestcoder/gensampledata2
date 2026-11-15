import json
import subprocess
import sys


def invoke(directory, *args):
    return subprocess.run(
        [sys.executable, "-m", "sampledata", *args], cwd=directory, text=True, capture_output=True
    )


def test_installed_cli_scenario_to_verified_output(tmp_path):
    scenario = tmp_path / "retail.toml"
    created = invoke(tmp_path, "init", str(scenario), "--preset", "clean-retail")
    assert created.returncode == 0, created.stderr
    scenario.write_text(scenario.read_text().replace("people = 1000", "people = 13"))
    valid = invoke(tmp_path, "validate", str(scenario))
    assert valid.returncode == 0 and json.loads(valid.stdout)["valid"]
    shown = invoke(tmp_path, "preview", str(scenario), "--people", "1")
    assert shown.returncode == 0 and json.loads(shown.stdout)["records"]
    output = tmp_path / "run"
    generated = invoke(tmp_path, "generate", str(scenario), "--output", str(output))
    assert generated.returncode == 0, generated.stderr
    assert json.loads(generated.stdout)["counts"]["customers"] == 13
    checked = invoke(tmp_path, "verify", str(output))
    assert checked.returncode == 0 and json.loads(checked.stdout)["valid"]
    # Both scenario creation and generation refuse overwrites with a clear failure status.
    assert invoke(tmp_path, "init", str(scenario)).returncode == 1
    assert invoke(tmp_path, "generate", str(scenario), "--output", str(output)).returncode == 1
