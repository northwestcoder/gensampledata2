"""Loopback workbench API with persisted scenarios and isolated worker processes."""

import json
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import tomllib
import uuid
import zipfile
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import SOURCES, Scenario, presets, scenario_toml
from .corpus import corpus
from .engine import preview
from .operations import publish
from .runs import CancelledError, dataset_rows, generate, safe_file, verify
from .schema import KEYS, SCHEMAS, filename


@contextmanager
def connect(root: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(root / "workbench.db", timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    (root / "cancel").mkdir(exist_ok=True)
    with connect(root) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS scenarios(
                name TEXT, version INTEGER, config TEXT, created TEXT,
                PRIMARY KEY(name,version));
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, status TEXT, config TEXT, counts TEXT DEFAULT '{}',
                records INTEGER DEFAULT 0, error TEXT, created TEXT, finished TEXT);
        """)


@contextmanager
def workspace_lease(root: Path):
    """Release on process death; a second server must not interrupt live jobs."""
    with (root / "server.lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.write(b"\0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError(
                "This workspace is already open in another server; choose another --data-dir"
            ) from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def worker(root_text: str, job_id: str, config: dict) -> None:
    root = Path(root_text)

    def update(value):
        with connect(root) as db:
            db.execute(
                "UPDATE jobs SET status=?,records=?,counts=? WHERE id=?",
                (value["status"], value["records"], json.dumps(value["counts"]), job_id),
            )

    try:
        generate(
            Scenario.model_validate(config),
            root / "runs" / job_id,
            progress=update,
            cancelled=lambda: (root / "cancel" / job_id).exists(),
        )
        status, error = "complete", None
    except CancelledError:
        status, error = "cancelled", None
    except Exception as exc:
        status, error = "failed", str(exc)
    with connect(root) as db:
        db.execute(
            "UPDATE jobs SET status=?,error=?,finished=? WHERE id=?",
            (status, error, datetime.now(UTC).isoformat(), job_id),
        )


class PreviewRequest(BaseModel):
    scenario: Scenario
    people: int = Field(default=5, ge=1, le=20)


class ImportRequest(BaseModel):
    text: str = Field(max_length=100000)
    format: str = "toml"


class PublishRequest(BaseModel):
    bucket: str = Field(min_length=1, max_length=255)
    prefix: str = Field(default="gensampledata", max_length=1000)
    include_truth: bool = False


def create_app(data_dir: Path = Path("artifacts/workbench-data")) -> FastAPI:
    root = data_dir.resolve()
    initialize(root)
    processes = {}
    process_guard = threading.Lock()

    def reap_workers():
        with process_guard:
            for job_id, process in list(processes.items()):
                if not process.is_alive():
                    process.join(timeout=0)
                    process.close()
                    del processes[job_id]
                    with connect(root) as db:
                        db.execute(
                            "UPDATE jobs SET status='failed',error='Worker exited unexpectedly' WHERE id=? AND status IN ('running','verifying','queued')",
                            (job_id,),
                        )

    @asynccontextmanager
    async def lifespan(app):
        with workspace_lease(root):
            with connect(root) as db:
                db.execute(
                    "UPDATE jobs SET status='interrupted',error='Server stopped before this run completed' WHERE status IN ('running','verifying','queued')"
                )
            try:
                yield
            finally:
                for job_id, process in list(processes.items()):
                    if process.is_alive():
                        (root / "cancel" / job_id).touch()
                        process.join(timeout=3)
                        if process.is_alive():
                            process.terminate()
                            process.join(timeout=3)
                    process.close()
                with connect(root) as db:
                    db.execute(
                        "UPDATE jobs SET status='interrupted',error='Server shut down' WHERE status IN ('running','verifying','queued')"
                    )

    app = FastAPI(title="gensampledata Workbench", version=__version__, lifespan=lifespan)
    allowed_hosts_raw = os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]")
    allowed_hosts = [h.strip() for h in allowed_hosts_raw.split(",") if h.strip()]

    if "*" not in allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and "*" not in allowed_hosts:
            hostname = urlsplit(origin).hostname
            if hostname not in allowed_hosts and hostname not in ("127.0.0.1", "localhost", "::1"):
                return JSONResponse(
                    {"detail": "This workbench accepts local browser requests only"}, status_code=403
                )
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def browser_scenario(scenario: Scenario) -> Scenario:
        if scenario.tracer_file:
            raise HTTPException(
                422,
                "Use the CLI for tracer-file imports; browser scenarios cannot read local paths",
            )
        return scenario

    def job(job_id: str) -> dict:
        with connect(root) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Run not found")
        value = dict(row)
        value["scenario"] = json.loads(value.pop("config"))
        value["counts"] = json.loads(value["counts"])
        return value

    def complete(job_id: str) -> Path:
        if job(job_id)["status"] != "complete":
            raise HTTPException(409, "Run has not completed")
        return root / "runs" / job_id

    @app.get("/api/meta")
    def meta(request: Request):
        iap_email = request.headers.get("x-goog-authenticated-user-email", "")
        if iap_email.startswith("accounts.google.com:"):
            iap_email = iap_email[len("accounts.google.com:"):]
        return {
            "version": __version__,
            "sources": SOURCES,
            "schemas": SCHEMAS,
            "presets": {key: value.model_dump(mode="json") for key, value in presets().items()},
            "max_people": 1_000_000,
            "max_active_runs": 2,
            "corpus": corpus()[0].summary(),
            "field_pool_targets": [
                f"{source}.{field}"
                for source, fields in SCHEMAS.items()
                for field in fields
                if field not in {KEYS.get(source), "customer_id", "CUSTOMER_ID", "cust_visid"}
            ],
            "iap_user": iap_email or None,
        }

    @app.get("/api/corpus")
    def corpus_summary():
        return corpus()[0].summary()

    @app.get("/api/corpus/{dataset}")
    def corpus_sample(dataset: str, limit: int = Query(10, ge=1, le=100)):
        data, _ = corpus()
        name = data.name(dataset)
        return {
            "dataset": name,
            **data.catalog["datasets"][name],
            "sample": data.pool(name)[:limit],
        }

    @app.post("/api/validate")
    def validate_scenario(scenario: Scenario):
        return {"valid": True, "scenario": browser_scenario(scenario).model_dump(mode="json")}

    @app.post("/api/preview")
    def get_preview(payload: PreviewRequest):
        return preview(browser_scenario(payload.scenario), payload.people)

    @app.get("/api/scenarios")
    def list_scenarios():
        with connect(root) as db:
            rows = db.execute(
                "SELECT s.* FROM scenarios s JOIN (SELECT name, MAX(version) version FROM scenarios GROUP BY name) latest USING(name,version) ORDER BY s.created DESC"
            ).fetchall()
        return [
            {
                "name": row["name"],
                "version": row["version"],
                "created": row["created"],
                "scenario": json.loads(row["config"]),
            }
            for row in rows
        ]

    @app.post("/api/scenarios")
    def save_scenario(scenario: Scenario):
        browser_scenario(scenario)
        with connect(root) as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM scenarios WHERE name=?", (scenario.name,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO scenarios VALUES(?,?,?,?)",
                (scenario.name, version, scenario.model_dump_json(), datetime.now(UTC).isoformat()),
            )
        return {"name": scenario.name, "version": version}

    @app.post("/api/scenarios/import")
    def import_scenario(payload: ImportRequest):
        if payload.format not in ("json", "toml"):
            raise ValueError("scenario format must be json or toml")
        data = json.loads(payload.text) if payload.format == "json" else tomllib.loads(payload.text)
        return browser_scenario(Scenario.model_validate(data)).model_dump(mode="json")

    @app.post("/api/scenarios/export")
    def export_scenario(scenario: Scenario):
        browser_scenario(scenario)
        return Response(
            scenario_toml(scenario),
            media_type="application/toml",
            headers={"Content-Disposition": f'attachment; filename="{scenario.name}.toml"'},
        )

    @app.get("/api/scenarios/{name}/versions")
    def scenario_versions(name: str):
        with connect(root) as db:
            rows = db.execute(
                "SELECT * FROM scenarios WHERE name=? ORDER BY version DESC", (name,)
            ).fetchall()
        return [
            {
                "version": row["version"],
                "created": row["created"],
                "scenario": json.loads(row["config"]),
            }
            for row in rows
        ]

    @app.get("/api/jobs")
    def list_jobs():
        reap_workers()
        with connect(root) as db:
            ids = [
                row[0] for row in db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT 100")
            ]
        return [job(value) for value in ids]

    @app.post("/api/jobs", status_code=202)
    def start_job(scenario: Scenario):
        browser_scenario(scenario)
        reap_workers()
        if scenario.people > 1_000_000:
            raise HTTPException(
                422,
                "The local workbench supports at most 1,000,000 people per run; use the CLI for larger runs",
            )
        with connect(root) as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','verifying')"
            ).fetchone()[0]
            if active >= 2:
                raise HTTPException(429, "Two runs are already active")
            job_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO jobs(id,status,config,created) VALUES(?,?,?,?)",
                (job_id, "queued", scenario.model_dump_json(), datetime.now(UTC).isoformat()),
            )
        process = multiprocessing.get_context("spawn").Process(
            target=worker, args=(str(root), job_id, scenario.model_dump(mode="json")), daemon=True
        )
        try:
            process.start()
        except Exception as exc:
            with connect(root) as db:
                db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc), job_id))
            raise HTTPException(500, "Could not start generation worker") from exc
        with process_guard:
            processes[job_id] = process
        return job(job_id)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        reap_workers()
        return job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        value = job(job_id)
        if value["status"] not in ("queued", "running", "verifying"):
            raise HTTPException(409, "Run is no longer active")
        (root / "cancel" / job_id).touch()
        return {"cancellation_requested": True}

    @app.get("/api/jobs/{job_id}/manifest")
    def manifest(job_id: str):
        return json.loads((complete(job_id) / "manifest.json").read_text())

    @app.post("/api/jobs/{job_id}/verify")
    def verify_job(job_id: str):
        return verify(complete(job_id))

    @app.get("/api/jobs/{job_id}/sample/{source}")
    def sample(job_id: str, source: str, limit: int = Query(20, ge=1, le=100)):
        directory = complete(job_id)
        scenario = Scenario.model_validate(job(job_id)["scenario"])
        if source not in scenario.sources:
            raise HTTPException(404, "Source is not present in this run")
        fmt = scenario.formats[0]
        path = directory / (filename(source, scenario.reference_time) + "." + fmt)
        import itertools

        return {
            "source": source,
            "rows": list(itertools.islice(dataset_rows(path, fmt, SCHEMAS[source]), limit)),
        }

    @app.get("/api/jobs/{job_id}/download/{name}")
    def download(job_id: str, name: str):
        directory = complete(job_id)
        content = json.loads((directory / "manifest.json").read_text())
        if name != "manifest.json" and (
            name not in content["files"] or content["files"][name]["role"] == "input"
        ):
            raise HTTPException(404, "File not found")
        return FileResponse(safe_file(directory, name), filename=name)

    @app.get("/api/jobs/{job_id}/archive")
    def download_archive(job_id: str):
        directory = complete(job_id)
        content = json.loads((directory / "manifest.json").read_text())
        scratch = Path.cwd() / "tmp/downloads"
        scratch.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="run-", suffix=".zip", dir=scratch)
        os.close(fd)
        archive = Path(temporary)
        try:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
                for name in [*content["files"], "manifest.json"]:
                    output.write(safe_file(directory, name), arcname=name)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
        return FileResponse(
            archive,
            filename=f"{job(job_id)['scenario']['name']}-{job_id[:8]}.zip",
            background=BackgroundTask(archive.unlink, missing_ok=True),
        )

    @app.post("/api/jobs/{job_id}/publish")
    def publish_job(job_id: str, payload: PublishRequest):
        try:
            return publish(complete(job_id), payload.bucket, payload.prefix, payload.include_truth)
        except OSError as exc:
            raise HTTPException(502, f"S3 publication failed: {exc}") from exc

    installed = Path(str(files("sampledata").joinpath("static")))
    local = Path(__file__).resolve().parents[2] / "artifacts/workbench-dist"
    static = local if (local / "index.html").is_file() else installed
    if (static / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static, html=True), name="workbench")
    else:

        @app.get("/")
        def missing_frontend():
            return JSONResponse(
                {"detail": "Build the workbench first: cd frontend && npm ci && npm run build"},
                status_code=503,
            )

    return app
