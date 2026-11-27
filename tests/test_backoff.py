from __future__ import annotations

from datetime import date

import pytest

from scraper.backoff import call_with_backoff, is_throttle, jitter_pause, throttle_delay
from scraper.models import Search
from scraper.price_pass import DateChunk, fetch_dates
from scraper.source_google import search


def test_throttle_detection_covers_status_and_message():
    assert is_throttle(RuntimeError("HTTP 429 Too Many Requests"))
    assert is_throttle(RuntimeError("Google is throttling this IP"))
    assert is_throttle(RuntimeError("rate limit exceeded"))
    wrapped = RuntimeError("date sweep failed")
    wrapped.__cause__ = RuntimeError("429")
    assert is_throttle(wrapped)
    assert is_throttle(ValueError("no flights")) is False


def test_throttle_delay_grows_and_stays_capped():
    delays = [throttle_delay(attempt, rng=lambda: 0) for attempt in range(6)]
    assert delays[:4] == [2.0, 4.0, 8.0, 16.0]
    assert delays[-1] == 60.0


def test_backoff_retries_only_throttle():
    calls = {"n": 0}
    slept: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 too many requests")
        return "ok"

    assert call_with_backoff(flaky, sleep=slept.append, delay=lambda attempt: float(attempt + 1)) == "ok"
    assert slept == [1.0, 2.0]

    def other():
        raise RuntimeError("airport closed")

    with pytest.raises(RuntimeError, match="airport closed"):
        call_with_backoff(other, sleep=slept.append)


def test_jitter_pause_uses_the_delay_window():
    chosen: list[float] = []
    jitter_pause(3, 7, sleep=chosen.append)
    assert len(chosen) == 1
    assert 3 <= chosen[0] <= 7
    jitter_pause(0, 0, sleep=chosen.append)
    assert len(chosen) == 1


def test_fetch_dates_retries_a_429(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    class Row:
        def __init__(self):
            self.date = (date(2027, 3, 1), date(2027, 3, 8))
            self.price = 650

    class FakeSearch:
        def search(self, *_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                from fli.search.exceptions import SearchHTTPError

                raise SearchHTTPError("slow down", status_code=429)
            return [Row()]

    monkeypatch.setattr("fli.search.SearchDates", FakeSearch)
    monkeypatch.setattr("scraper.backoff.time.sleep", slept.append)
    chunk = DateChunk(
        route_id="bog",
        origin="JFK",
        dest="BOG",
        outbound_start=date(2027, 3, 1),
        outbound_end=date(2027, 3, 1),
        duration=7,
        searches=(),
        max_stops=None,
    )
    prices = fetch_dates(chunk, currency="USD", cabin="economy", adults=1)
    assert calls["n"] == 2
    assert prices[(date(2027, 3, 1), date(2027, 3, 8))] == 650
    assert slept and slept[0] >= 2


def test_detail_search_backs_off_on_throttle(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    class Query:
        def url(self):
            return "https://example.test/flights"

    class Fare:
        price = 500
        airlines = ["B6"]
        flights = []

    def get_flights(_query):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP 429")
        return [Fare()]

    monkeypatch.setattr("fast_flights.create_query", lambda **_kwargs: Query())
    monkeypatch.setattr("fast_flights.get_flights", get_flights)
    monkeypatch.setattr("scraper.source_google.time.sleep", slept.append)
    result = search(
        Search(
            key="bog|JFK|BOG|2027-03-01|2027-03-08",
            route_id="bog",
            origin="JFK",
            dest="BOG",
            depart=date(2027, 3, 1),
            return_date=date(2027, 3, 8),
            max_stops=None,
            target_price=None,
        ),
        currency="USD",
        cabin="economy",
        adults=1,
        fetched_at="2026-11-27T00:00:00Z",
    )
    assert calls["n"] == 2
    assert result.ok is True
    assert result.cheapest is not None
    assert result.cheapest.price == 500
    assert slept and slept[0] >= 2
