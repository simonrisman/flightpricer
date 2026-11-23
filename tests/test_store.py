from __future__ import annotations

from datetime import date
from pathlib import Path

from scraper.models import FlightOption, Search, SearchResult
from scraper.store import drop_expired, entries_from_latest, merge_results, update_history, write_json


def _result(key: str, *, ok: bool, price: int | None, depart: str = "2027-03-05") -> SearchResult:
    route_id, origin, dest, depart_text, ret = key.split("|")
    cheapest = None
    if ok and price is not None:
        cheapest = FlightOption(price=price, airlines="B6", stops=0, duration="5h 40m", depart_time="22:30", arrive_time="04:10")
    return SearchResult(
        key=key,
        route_id=route_id,
        origin=origin,
        dest=dest,
        depart=date.fromisoformat(depart_text or depart),
        return_date=None if ret == "-" else date.fromisoformat(ret),
        ok=ok,
        error=None if ok else "empty result",
        options=[cheapest] if cheapest else [],
        cheapest=cheapest,
        google_url="https://www.google.com/travel/flights",
        fetched_at="2026-11-24T12:00:00Z",
    )


def test_failed_search_keeps_the_old_price_and_past_dates_drop():
    fresh = "bog|JFK|BOG|2027-03-05|2027-03-12"
    failed = "bog|LGA|BOG|2027-03-05|2027-03-12"
    past = "bog|JFK|BOG|2026-01-01|2026-01-08"
    previous = {
        failed: {
            "key": failed,
            "route_id": "bog",
            "origin": "LGA",
            "dest": "BOG",
            "depart": "2027-03-05",
            "return": "2027-03-12",
            "price": 410,
            "fetched_at": "2026-11-20T00:00:00Z",
            "stale": False,
        },
        past: {
            "key": past,
            "route_id": "bog",
            "origin": "JFK",
            "dest": "BOG",
            "depart": "2026-01-01",
            "return": "2026-01-08",
            "price": 200,
            "fetched_at": "2026-01-01T00:00:00Z",
            "stale": False,
        },
    }
    merged = merge_results(
        previous,
        [_result(fresh, ok=True, price=280), _result(failed, ok=False, price=None)],
        "2026-11-24T12:00:00Z",
    )
    merged = drop_expired(merged, {"bog"}, date(2026, 11, 24))
    assert merged[fresh]["price"] == 280
    assert merged[fresh]["stale"] is False
    assert merged[failed]["price"] == 410
    assert merged[failed]["stale"] is True
    assert merged[failed]["fetched_at"] == "2026-11-20T00:00:00Z"
    assert merged[failed]["last_attempt_at"] == "2026-11-24T12:00:00Z"
    assert past not in merged


def test_history_overwrites_the_same_day_and_prunes_old_departures():
    history = update_history(
        {"route_id": "bog", "points": [{"date": "2026-11-24", "key": "k", "price": 300, "depart": "2027-03-05"}]},
        [{"key": "k", "price": 280, "depart": "2027-03-05"}],
        date(2026, 11, 24),
        date(2026, 11, 24),
    )
    assert history["points"] == [{"date": "2026-11-24", "key": "k", "price": 280, "depart": "2027-03-05"}]
    old = update_history(
        {"route_id": "bog", "points": [{"date": "2026-01-01", "key": "old", "price": 100, "depart": "2026-01-02"}]},
        [],
        date(2026, 11, 24),
        date(2026, 11, 24),
    )
    assert old["points"] == []


def test_entries_round_trip_through_latest_json(tmp_path: Path):
    payload = {
        "routes": [
            {
                "id": "bog",
                "searches": [{"key": "bog|JFK|BOG|2027-03-05|2027-03-12", "price": 10}],
            }
        ]
    }
    path = tmp_path / "latest.json"
    write_json(path, payload)
    loaded = entries_from_latest(__import__("json").loads(path.read_text(encoding="utf-8")))
    assert loaded["bog|JFK|BOG|2027-03-05|2027-03-12"]["price"] == 10
    assert isinstance(Search(key="k", route_id="r", origin="JFK", dest="BOG", depart=date(2027, 3, 5), return_date=None, max_stops=None, target_price=None), Search)
