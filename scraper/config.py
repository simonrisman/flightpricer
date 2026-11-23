from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
AIRPORT = re.compile(r"^[A-Z]{3}$")
ROUTE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class ConfigError(Exception):
    pass


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = "USD"
    adults: int = Field(default=1, ge=1, le=9)
    cabin: str = "economy"
    max_requests_per_run: int = Field(default=3000, ge=1, le=100000)
    delay_seconds: list[float] = Field(default_factory=lambda: [3.0, 7.0], min_length=2, max_length=2)
    dashboard_title: str = "Flight Pricer"
    source: str = "fast_flights"
    github_repo: str = "simonrisman/flightpricer"

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        code = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            raise ValueError("currency must be a 3-letter code")
        return code

    @field_validator("cabin")
    @classmethod
    def cabin_name(cls, value: str) -> str:
        name = value.strip().lower()
        allowed = {"economy", "premium_economy", "business", "first"}
        if name not in allowed:
            raise ValueError(f"cabin must be one of {sorted(allowed)}")
        return name

    @field_validator("source")
    @classmethod
    def source_name(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in {"fast_flights", "playwright"}:
            raise ValueError("source must be fast_flights or playwright")
        return name

    @field_validator("github_repo")
    @classmethod
    def repo_slug(cls, value: str) -> str:
        text = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
            raise ValueError("github_repo must look like owner/name")
        return text

    @field_validator("dashboard_title")
    @classmethod
    def title_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("dashboard_title cannot be empty")
        return text

    @model_validator(mode="after")
    def delay_order(self) -> Settings:
        low, high = self.delay_seconds
        if low < 0 or high < 0:
            raise ValueError("delay_seconds cannot be negative")
        if low > high:
            raise ValueError("delay_seconds minimum is greater than the maximum")
        return self


class DateWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> DateWindow:
        if self.start > self.end:
            raise ValueError("start is after end")
        return self


class ExtraDate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    depart: date
    return_date: date | None = Field(default=None, alias="return")
    trip_length_days: int | None = Field(default=None, ge=1, le=60)
    origin: str | None = None
    dest: str | None = None

    @field_validator("origin", "dest")
    @classmethod
    def airport(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        code = str(value).strip().upper()
        if not AIRPORT.fullmatch(code):
            raise ValueError("must be a 3-letter airport code")
        return code

    @model_validator(mode="after")
    def return_after_depart(self) -> ExtraDate:
        if self.return_date is not None and self.return_date <= self.depart:
            raise ValueError("return must be after depart")
        return self


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    origins: list[str] = Field(min_length=1)
    destinations: list[str] = Field(min_length=1)
    trip: str
    depart_window: DateWindow
    depart_days: list[str] | None = None
    trip_length_days: list[int] | None = None
    max_stops: int | None = Field(default=None, ge=0, le=3)
    target_price: int | None = Field(default=None, ge=1)
    active: bool = True
    extra_dates: list[ExtraDate] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def route_id(cls, value: str) -> str:
        text = value.strip().lower()
        if not ROUTE_ID.fullmatch(text):
            raise ValueError("id must be a short lowercase slug")
        return text

    @field_validator("label")
    @classmethod
    def label_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("label cannot be empty")
        return text

    @field_validator("origins", "destinations")
    @classmethod
    def airports(cls, value: list[str]) -> list[str]:
        codes: list[str] = []
        for item in value:
            code = str(item).strip().upper()
            if not AIRPORT.fullmatch(code):
                raise ValueError(f"{item!r} is not a 3-letter airport code")
            if code not in codes:
                codes.append(code)
        if not codes:
            raise ValueError("at least one airport is required")
        return codes

    @field_validator("trip")
    @classmethod
    def trip_name(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in {"one_way", "round_trip"}:
            raise ValueError("trip must be one_way or round_trip")
        return name

    @field_validator("depart_days")
    @classmethod
    def days(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        names: list[str] = []
        for item in value:
            name = str(item).strip().lower()
            if name not in DAY_NAMES:
                raise ValueError(f"unknown weekday {item!r}")
            if name not in names:
                names.append(name)
        return names or None

    @field_validator("trip_length_days")
    @classmethod
    def lengths(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        kept: list[int] = []
        for item in value:
            number = int(item)
            if number < 1 or number > 60:
                raise ValueError("trip_length_days must be between 1 and 60")
            if number not in kept:
                kept.append(number)
        return kept or None

    @model_validator(mode="after")
    def round_trip_lengths(self) -> Route:
        if self.trip == "round_trip" and not self.trip_length_days:
            raise ValueError("round_trip routes need trip_length_days")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: Settings = Field(default_factory=Settings)
    routes: list[Route] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> AppConfig:
        seen: set[str] = set()
        dupes: set[str] = set()
        for route in self.routes:
            if route.id in seen:
                dupes.add(route.id)
            seen.add(route.id)
        if dupes:
            raise ValueError(f"duplicate route ids: {', '.join(sorted(dupes))}")
        return self


def format_validation(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "(file)"
        lines.append(f"{loc}: {err['msg']}")
    return "\n".join(lines)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path.name}:\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must be a mapping with settings and routes")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid {path.name}:\n{format_validation(exc)}") from exc


def public_config(config: AppConfig) -> dict:
    return config.model_dump(mode="json", by_alias=True)


def write_public_config(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(public_config(config), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
