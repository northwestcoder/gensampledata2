"""Versioned scenarios shared by the CLI, previews, workers, and manifests."""

import json
import math
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Source = Literal[
    "customers",
    "transactions",
    "pos",
    "pos_transactions",
    "loyalty",
    "email",
    "clickstream",
    "mobile",
    "wifi",
    "safety",
    "locations",
    "custom",
]
SOURCES: tuple[Source, ...] = (
    "customers",
    "transactions",
    "pos",
    "pos_transactions",
    "loyalty",
    "email",
    "clickstream",
    "mobile",
    "wifi",
    "safety",
    "locations",
    "custom",
)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    profile: Literal["legacy-retail"] = "legacy-retail"
    name: str = Field(default="retail-demo", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
    people: int = Field(default=1000, ge=0, le=10_000_000, strict=True)
    seed: int = Field(default=42, ge=0, le=2**53 - 1, strict=True)
    reference_time: datetime = datetime(2026, 11, 4, tzinfo=UTC)
    batch_size: int = Field(default=1000, ge=1, le=100_000, strict=True)
    sources: tuple[Source, ...] = SOURCES
    formats: tuple[Literal["csv", "jsonl"], ...] = ("csv", "jsonl")
    # Fraction of base people receiving extra records, NOT fraction of final rows.
    duplicate_fraction: float = Field(default=0.15, ge=0, le=1)
    copies_per_duplicate: int = Field(default=1, ge=1, le=10, strict=True)
    overlap_fraction: float = Field(default=0.5, ge=0, le=1)
    corruption_probability: float = Field(default=0.25, ge=0, le=1)
    missing_probability: float = Field(default=0.02, ge=0, le=1)
    max_transactions: int = Field(default=4, ge=1, le=100, strict=True)
    transaction_window: Literal["full", "daily"] = "full"
    points_percent: float = Field(default=2, ge=0, le=100)
    tracer_file: str | None = None
    date_mode: Literal["relative", "original"] = "relative"
    field_pools: dict[str, str] = Field(default_factory=dict)

    @field_validator("reference_time")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference_time must include a timezone, e.g. 2026-11-04T00:00:00Z")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def relationships(self) -> Self:
        if not self.sources or len(set(self.sources)) != len(self.sources):
            raise ValueError("select at least one source, without duplicates")
        if not self.formats or len(set(self.formats)) != len(self.formats):
            raise ValueError("select at least one output format, without duplicates")
        for child, parent in (("transactions", "customers"), ("pos_transactions", "pos")):
            if child in self.sources and parent not in self.sources:
                raise ValueError(f"{child} requires {parent}")
        if self.tracer_file and "customers" not in self.sources:
            raise ValueError("tracer_file requires the customers source")
        if self.field_pools:
            from .corpus import corpus
            from .schema import KEYS, SCHEMAS

            for target, dataset in self.field_pools.items():
                source, separator, field = target.partition(".")
                if not separator or source not in SCHEMAS or field not in SCHEMAS[source]:
                    raise ValueError(f"unknown source field: {target}")
                if field in {KEYS.get(source), "customer_id", "CUSTOMER_ID", "cust_visid"}:
                    raise ValueError(
                        f"field pools cannot override identity or foreign keys: {target}"
                    )
                corpus()[0].name(dataset)
        return self

    def selects(self, index: int, fraction: float, stream: int = 0) -> bool:
        """An exact-size subset with O(1) memory, independent of batch boundaries."""
        if not self.people:
            return False
        multiplier = 7919
        while math.gcd(multiplier, self.people) != 1:
            multiplier += 2
        offset = (self.seed + stream * 104729) % self.people
        return (index * multiplier + offset) % self.people < round(self.people * fraction)


def load_scenario(path: Path) -> Scenario:
    raw = path.read_bytes()
    data = json.loads(raw) if path.suffix == ".json" else tomllib.loads(raw.decode())
    scenario = Scenario.model_validate(data)
    if scenario.tracer_file:
        scenario = scenario.model_copy(
            update={"tracer_file": str((path.parent / scenario.tracer_file).resolve())}
        )
    return scenario


def scenario_toml(scenario: Scenario) -> str:
    lines = []
    tables = []
    for key, value in scenario.model_dump(mode="json").items():
        if isinstance(value, dict):
            if value:
                tables.extend(
                    [
                        "",
                        f"[{key}]",
                        *[f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items()],
                    ]
                )
        elif value is not None:
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines + tables) + "\n"


def presets() -> dict[str, Scenario]:
    base = Scenario()
    return {
        "retail-demo": base,
        "clean-retail": base.model_copy(
            update={
                "name": "clean-retail",
                "duplicate_fraction": 0.0,
                "corruption_probability": 0.0,
                "missing_probability": 0.0,
            }
        ),
        "typo-heavy": base.model_copy(
            update={
                "name": "typo-heavy",
                "duplicate_fraction": 0.4,
                "copies_per_duplicate": 2,
                "corruption_probability": 0.7,
            }
        ),
        "sparse-identifiers": base.model_copy(
            update={
                "name": "sparse-identifiers",
                "missing_probability": 0.5,
            }
        ),
        "daily-activity": base.model_copy(
            update={
                "name": "daily-activity",
                "transaction_window": "daily",
            }
        ),
    }
