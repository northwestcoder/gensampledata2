"""Lossless access to every original support file, locally and in distributions."""

import csv
import hashlib
import io
import json
import random
from datetime import UTC, datetime
from functools import lru_cache
from importlib.resources import files
from pathlib import Path


class SupportCorpus:
    def __init__(self):
        resource = files("sampledata").joinpath("data/corpus.json")
        raw = resource.read_bytes()
        self.catalog = json.loads(raw)
        if self.catalog.get("format_version") != 2:
            raise ValueError("unsupported support-data catalog; rebuild with tools/build_corpus.py")
        self.checksum = hashlib.sha256(raw).hexdigest()
        packaged = files("sampledata").joinpath("data/support")
        checkout = Path(__file__).resolve().parents[2] / "support"
        self.root = checkout if checkout.is_dir() else packaged
        self._pools: dict[str, tuple] = {}
        self._rows: dict[str, tuple] = {}
        self._dates: dict[str, tuple] = {}
        self._nicknames = None

    def name(self, dataset: str) -> str:
        name = dataset if dataset.endswith(".csv") else dataset + ".csv"
        if name not in self.catalog["datasets"]:
            raise ValueError(f"unknown support dataset: {dataset}")
        return name

    def raw(self, dataset: str) -> bytes:
        name = self.name(dataset)
        raw = self.root.joinpath(name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.catalog["datasets"][name]["sha256"]:
            raise ValueError(f"support dataset changed: {name}; rebuild tools/build_corpus.py")
        return raw

    def pool(self, dataset: str) -> tuple[str, ...]:
        name = self.name(dataset)
        if name not in self._pools:
            text = self.raw(name).decode("utf-8-sig")
            if self.catalog["datasets"][name]["parser"] == "csv_cells":
                values = tuple(cell for row in csv.reader(io.StringIO(text)) for cell in row)
            else:
                # These files are line-oriented, despite their .csv suffix. Do not
                # split company names, job titles, user agents, or coordinates.
                values = tuple(text.splitlines())
            self._pools[name] = values
        return self._pools[name]

    def rows(self, dataset: str) -> tuple[tuple[str, ...], ...]:
        name = self.name(dataset)
        if name not in self._rows:
            text = self.raw(name).decode("utf-8-sig")
            self._rows[name] = tuple(tuple(row) for row in csv.reader(io.StringIO(text)))
        return self._rows[name]

    def pick(self, dataset: str, rng: random.Random) -> str:
        return rng.choice(self.pool(dataset))

    @property
    def nicknames(self) -> dict[str, list[str]]:
        if self._nicknames is None:
            mapping: dict[str, list[str]] = {}
            for row in self.rows("given_name_alternatives"):
                mapping.setdefault(row[0].lower(), []).extend(
                    name.title() for name in row[1:] if name and name.upper() != "NAN"
                )
            self._nicknames = mapping
        return self._nicknames

    def sample_time(
        self, dataset: str, rng: random.Random, reference: datetime, original: bool = False
    ) -> datetime:
        name = self.name(dataset)
        if name not in self._dates:
            values = tuple(
                datetime.fromisoformat(value).replace(tzinfo=UTC) for value in self.pool(name)
            )
            self._dates[name] = (values, max(values))
        values, anchor = self._dates[name]
        value = rng.choice(values)
        return value if original else reference - (anchor - value)

    def summary(self) -> dict:
        datasets = self.catalog["datasets"]
        return {
            "sha256": self.checksum,
            "file_count": len(datasets),
            "total_bytes": sum(item["bytes"] for item in datasets.values()),
            "datasets": datasets,
        }


@lru_cache(maxsize=1)
def corpus() -> tuple[SupportCorpus, str]:
    data = SupportCorpus()
    return data, data.checksum
