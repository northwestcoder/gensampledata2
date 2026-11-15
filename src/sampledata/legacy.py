"""Translate the original settings module without importing the old pandas pipeline."""

import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .cli import main as cli_main
from .config import Scenario
from .runs import generate


def from_settings(path: Path) -> Scenario:
    spec = importlib.util.spec_from_file_location("legacy_sampledata_settings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    switches = {
        "customers": "CREATE_PRIMARY_CUSTOMERS",
        "transactions": "CREATE_PRIMARY_TRANSACTIONS",
        "pos": "CREATE_DS_SECONDARY",
        "pos_transactions": "CREATE_DS_SECONDARY",
        "loyalty": "CREATE_DS_LOYALTY",
        "email": "CREATE_DS_EMAILCAMPAIGN",
        "clickstream": "CREATE_DS_CLICKSTREAM",
        "mobile": "CREATE_DS_MOBILE",
        "wifi": "CREATE_DS_WIFI",
        "safety": "CREATE_SAFETY",
        "locations": "CREATE_TILEDATA",
        "custom": "CREATE_CUSTOM_OBJECT",
    }
    sources = [key for key, flag in switches.items() if getattr(module, flag, False)]
    if "customers" not in sources and "transactions" in sources:
        raise ValueError(
            "enable CREATE_PRIMARY_CUSTOMERS for transactions, or use replay with an existing run"
        )
    if getattr(module, "USE_ARCHIVAL", False):
        raise ValueError(
            "USE_ARCHIVAL now requires an explicit historical run: gensampledata replay RUN --output NEW --reference-time TIME"
        )
    if getattr(module, "VARIANCE_DUPE_RATE", 0):
        raise ValueError(
            "VARIANCE_DUPE_RATE is unsupported; save each realized rate explicitly in a scenario"
        )
    tracer = Path(module.tracerdir) / "tracer.csv"
    return Scenario(
        name="legacy-retail",
        people=module.ITERATE,
        batch_size=module.LOOPER,
        duplicate_fraction=module.OVERALL_DUPE_RATE / 100,
        sources=tuple(sources),
        points_percent=module.POINTS_EARNED,
        max_transactions=module.MAX_TRANS_PER_CUSTOMER,
        reference_time=datetime.now(UTC).replace(microsecond=0),
        transaction_window=module.TRANS_TYPE,
        tracer_file=str(tracer)
        if getattr(module, "TRACER_DATA", False) and tracer.exists()
        else None,
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        return cli_main(args)
    try:
        path = Path.cwd() / "settings.py"
        if not path.exists():
            raise ValueError("settings.py not found; use gensampledata init scenario.toml")
        scenario = from_settings(path)
        directory = (
            Path("artifacts/runs")
            / f"legacy-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        )
        result = generate(scenario, directory)
        print(
            json.dumps(
                {
                    "output": str(directory),
                    "counts": result["counts"],
                    "note": "Translated settings.py. Runs are isolated; no old files were truncated or uploaded.",
                },
                indent=2,
            )
        )
        return 0
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
