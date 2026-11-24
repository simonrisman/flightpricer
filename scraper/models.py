from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FlightOption:
    price: int
    airlines: str | None = None
    stops: int | None = None
    duration: str | None = None
    depart_time: str | None = None
    arrive_time: str | None = None


@dataclass(frozen=True)
class Search:
    key: str
    route_id: str
    origin: str
    dest: str
    depart: date
    return_date: date | None
    max_stops: int | None
    target_price: int | None


@dataclass
class SearchResult:
    key: str
    route_id: str
    origin: str
    dest: str
    depart: date
    return_date: date | None
    ok: bool
    error: str | None
    options: list[FlightOption]
    cheapest: FlightOption | None
    google_url: str
    fetched_at: str
