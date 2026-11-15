"""A small CLI; every command calls the same library as the workbench."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .config import load_scenario, presets, scenario_toml
from .corpus import corpus
from .engine import preview
from .operations import publish, replay_transactions
from .runs import generate, json_write, verify


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="gensampledata", description="Related, reproducible synthetic datasets"
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("corpus", help="inspect every original support data pool")
    catalog.add_argument("--dataset", help="support filename or stem, e.g. phones.csv")
    catalog.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    init = commands.add_parser(
        "init", help="write a named scenario without overwriting existing files"
    )
    init.add_argument("path", type=Path)
    init.add_argument("--preset", choices=presets(), default="retail-demo")
    for name in ("validate", "preview", "generate"):
        cmd = commands.add_parser(name)
        cmd.add_argument("scenario", type=Path)
        if name == "preview":
            cmd.add_argument("--people", type=int, default=5, choices=range(1, 21), metavar="1..20")
        if name == "generate":
            cmd.add_argument("--output", required=True, type=Path)
            cmd.add_argument("--consume-tracer", action="store_true")
    check = commands.add_parser("verify")
    check.add_argument("run", type=Path)
    upload = commands.add_parser("publish")
    upload.add_argument("run", type=Path)
    upload.add_argument("--bucket", required=True)
    upload.add_argument("--prefix", default="gensampledata")
    upload.add_argument(
        "--include-truth",
        action="store_true",
        help="also publish the answer key and mutation history",
    )
    replay = commands.add_parser(
        "replay", help="create fresh daily transactions for an explicitly selected historical run"
    )
    replay.add_argument("run", type=Path)
    replay.add_argument("--output", required=True, type=Path)
    replay.add_argument("--reference-time", required=True, type=datetime.fromisoformat)
    replay.add_argument("--seed", type=int, default=42)
    replay.add_argument("--max-transactions", type=int, default=4)
    serve = commands.add_parser("serve", help="start the local visual workbench")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8767")))
    serve.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("DATA_DIR", "artifacts/workbench-data")),
    )
    bench = commands.add_parser("benchmark")
    bench.add_argument("--sizes", nargs="+", type=int, default=[1000, 100000, 1000000])
    bench.add_argument("--output", type=Path, default=Path("artifacts/benchmark.json"))
    worker = commands.add_parser("_benchmark-worker", help=argparse.SUPPRESS)
    worker.add_argument("people", type=int)
    return root


def benchmark_one(people: int) -> dict:
    try:
        import resource
    except ImportError as exc:
        raise ValueError("benchmark memory measurements currently support macOS and Linux") from exc

    from .config import Scenario

    scratch = Path("tmp")
    scratch.mkdir(exist_ok=True)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=scratch) as temporary:
        manifest = generate(
            Scenario(
                people=people,
                sources=("customers", "transactions"),
                formats=("csv",),
                max_transactions=2,
            ),
            Path(temporary) / "run",
        )
        size = sum(info["bytes"] for info in manifest["files"].values())
    elapsed = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "people": people,
        "total_records": manifest["total_records"],
        "elapsed_seconds": round(elapsed, 3),
        "records_per_second": round(manifest["total_records"] / elapsed),
        "peak_memory_mib": round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 2),
        "output_bytes": size,
        "verified": True,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "corpus":
            data, _ = corpus()
            result = data.summary()
            if args.dataset:
                name = data.name(args.dataset)
                result = {
                    "dataset": name,
                    **data.catalog["datasets"][name],
                    "sample": data.pool(name)[: args.limit],
                }
        elif args.command == "init":
            args.path.parent.mkdir(parents=True, exist_ok=True)
            with args.path.open("x") as handle:
                handle.write(scenario_toml(presets()[args.preset]))
            result = {"scenario": str(args.path)}
        elif args.command in ("validate", "preview", "generate"):
            scenario = load_scenario(args.scenario)
            if args.command == "validate":
                result = {"valid": True, "scenario": scenario.model_dump(mode="json")}
            elif args.command == "preview":
                result = preview(scenario, args.people)
            else:
                result = generate(
                    scenario,
                    args.output,
                    consume_tracer=args.consume_tracer,
                    progress=lambda value: print(json.dumps(value), file=sys.stderr),
                )
        elif args.command == "verify":
            result = verify(args.run)
        elif args.command == "publish":
            result = publish(args.run, args.bucket, args.prefix, args.include_truth)
        elif args.command == "replay":
            result = replay_transactions(
                args.run, args.output, args.reference_time, args.seed, args.max_transactions
            )
        elif args.command == "serve":
            try:
                import uvicorn

                from .api import create_app
            except ImportError as exc:
                raise ValueError("install web dependencies: uv sync --extra web") from exc
            uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
            return 0
        elif args.command == "_benchmark-worker":
            result = benchmark_one(args.people)
        else:
            measurements = []
            for size in args.sizes:
                completed = subprocess.run(
                    [sys.executable, "-m", "sampledata", "_benchmark-worker", str(size)],
                    check=True,
                    text=True,
                    capture_output=True,
                )
                item = json.loads(completed.stdout)
                measurements.append(item)
                print(json.dumps(item), file=sys.stderr)
            result = {
                "engine_version": __version__,
                "measured_at": datetime.now(UTC).isoformat(),
                "python": sys.version,
                "platform": sys.platform,
                "workload": {
                    "sources": ["customers", "transactions"],
                    "formats": ["csv"],
                    "max_transactions": 2,
                    "seed": 42,
                    "duplicate_fraction": 0.15,
                },
                "includes": "generation, checksums, full verification, and temporary-output cleanup",
                "measurements": measurements,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            json_write(args.output, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
