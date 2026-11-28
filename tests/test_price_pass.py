from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scraper.config import ExtraDate, load_config
from scraper.expand import expand_routes, make_key
from scraper.main import parse_args, run
from scraper.models import FlightOption, Search, SearchResult
from scraper.price_pass import (
    DATE_CHUNK,
    DateSweepError,
    chunk_searches,
    collect_price_pass,
    date_filters,
    fetch_dates,
    format_price_plan,
    parse_date_prices,
    select_top,
    top_per_pair,
)

ROOT = Path(__file__).parents[1]
LENGTHS = list(range(7, 16))


def _trips(departures, lengths=None, *, origin="JFK", dest="BOG", route_id="bog", max_stops=None):
    lengths = LENGTHS if lengths is None else lengths
    found = []
    for depart in departures:
        for length in lengths:
            ret = depart + timedelta(days=length)
            found.append(
                Search(
                    key=make_key(route_id, origin, dest, depart, ret),
                    route_id=route_id,
                    origin=origin,
                    dest=dest,
                    depart=depart,
                    return_date=ret,
                    max_stops=max_stops,
                    target_price=None,
                )
            )
    return found


def _dated(depart: date, ret: date | None, price: int, *, origin="JFK", route_id="bog", dest="BOG"):
    item = Search(
        key=make_key(route_id, origin, dest, depart, ret),
        route_id=route_id,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=ret,
        max_stops=None,
        target_price=None,
    )
    return item, price


def test_chunks_group_by_trip_length_and_honor_the_cap():
    start = date(2027, 3, 1)
    departures = [start + timedelta(days=offset) for offset in range(40)]
    chunks = chunk_searches(_trips(departures), date_chunk=31)
    assert len(chunks) == 18  # 9 lengths × 2 windows (31 + 9)
    assert all(chunk.departure_days <= 31 for chunk in chunks)
    assert {chunk.duration for chunk in chunks} == set(LENGTHS)
    covered = [item.key for chunk in chunks for item in chunk.searches]
    assert len(covered) == len(set(covered)) == 40 * 9


def test_bog_window_uses_the_same_pairs_the_expander_generates():
    config = load_config(ROOT / "config.yaml")
    route = next(item for item in config.routes if item.id == "bog").model_copy(deep=True)
    route.origins = ["JFK"]
    found = expand_routes([route], date(2026, 11, 24))
    chunks = chunk_searches(found)
    assert [(chunk.duration, chunk.outbound_start.isoformat(), chunk.outbound_end.isoformat(), len(chunk.searches)) for chunk in chunks] == [
        (length, "2027-03-01", "2027-03-21", 21) for length in LENGTHS
    ]
    assert sorted(item.key for chunk in chunks for item in chunk.searches) == sorted(item.key for item in found)


def test_sparse_dates_and_extra_dates_do_not_fetch_the_gaps():
    config = load_config(ROOT / "config.yaml")
    route = next(item for item in config.routes if item.id == "bog").model_copy(deep=True)
    route.origins = ["JFK"]
    route.depart_days = ["thu"]
    route.depart_window.start = date(2027, 3, 1)
    route.depart_window.end = date(2027, 5, 31)
    route.extra_dates = [ExtraDate(depart=date(2027, 8, 2), return_date=date(2027, 8, 20), origin="JFK", dest="BOG")]
    found = expand_routes([route], date(2026, 11, 24))
    chunks = chunk_searches(found)
    assert chunks
    assert all(chunk.departure_days <= DATE_CHUNK for chunk in chunks)
    assert sorted(item.key for chunk in chunks for item in chunk.searches) == sorted(item.key for item in found)
    extras = [chunk for chunk in chunks if chunk.outbound_start == date(2027, 8, 2)]
    assert len(extras) == 1
    assert extras[0].duration == 18
    assert len(extras[0].searches) == 1
    # Thursday-only windows should not stretch across the skipped weekdays.
    for chunk in chunks:
        if chunk.duration == 18:
            continue
        span = (chunk.outbound_end - chunk.outbound_start).days + 1
        assert span == len({item.depart for item in chunk.searches})


def test_top_three_breaks_ties_by_departure_then_return():
    rows = [
        _dated(date(2027, 4, 5), date(2027, 4, 12), 300),
        _dated(date(2027, 4, 4), date(2027, 4, 14), 300),
        _dated(date(2027, 4, 4), date(2027, 4, 12), 300),
        _dated(date(2027, 4, 2), date(2027, 4, 9), 500),
        _dated(date(2027, 4, 1), date(2027, 4, 8), 100),
        _dated(date(2027, 4, 6), date(2027, 4, 13), 300, origin="EWR"),
    ]
    chosen = select_top(rows[:5], 3)
    assert [(item.depart, item.return_date, price) for item, price in chosen] == [
        (date(2027, 4, 1), date(2027, 4, 8), 100),
        (date(2027, 4, 4), date(2027, 4, 12), 300),
        (date(2027, 4, 4), date(2027, 4, 14), 300),
    ]
    paired = top_per_pair(rows, 3)
    assert [item.origin for item, _price in paired].count("EWR") == 1
    assert [item.origin for item, _price in paired].count("JFK") == 3


def test_parse_date_prices_keeps_round_trip_and_one_way():
    prices = parse_date_prices(
        [
            SimpleNamespace(date=(datetime(2027, 3, 13), datetime(2027, 3, 20)), price=515),
            SimpleNamespace(date=(datetime(2027, 3, 13), datetime(2027, 3, 21)), price=507.4),
            SimpleNamespace(date=(datetime(2027, 3, 15),), price=80),
            SimpleNamespace(date=(datetime(2027, 3, 16), datetime(2027, 3, 23)), price=0),
        ]
    )
    assert prices[(date(2027, 3, 13), date(2027, 3, 20))] == 515
    assert prices[(date(2027, 3, 13), date(2027, 3, 21))] == 507
    assert prices[(date(2027, 3, 15), None)] == 80
    assert (date(2027, 3, 16), date(2027, 3, 23)) not in prices


def test_date_filters_describe_a_round_trip_window():
    from fli.models import Airport, MaxStops, SeatType, TripType

    chunk = chunk_searches(_trips([date(2027, 3, 1)], lengths=[7], max_stops=0))[0]
    filters = date_filters(chunk, cabin="first", adults=1)
    assert filters.trip_type == TripType.ROUND_TRIP
    assert filters.duration == 7
    assert filters.from_date == "2027-03-01"
    assert filters.to_date == "2027-03-01"
    assert filters.seat_type == SeatType.FIRST
    assert filters.stops == MaxStops.NON_STOP
    assert filters.passenger_info.adults == 1
    assert filters.flight_segments[0].departure_airport[0][0] == Airport.JFK
    assert filters.flight_segments[0].arrival_airport[0][0] == Airport.BOG
    assert filters.flight_segments[0].travel_date == "2027-03-01"
    assert filters.flight_segments[1].travel_date == "2027-03-08"


def test_fetch_dates_calls_fli_without_a_network(monkeypatch):
    captured = {}

    class FakeSearch:
        def search(self, filters, currency=None, language=None, country=None):
            captured["filters"] = filters
            captured["currency"] = currency
            captured["language"] = language
            captured["country"] = country
            return [
                SimpleNamespace(
                    date=(datetime(2027, 3, 1), datetime(2027, 3, 8)),
                    price=100,
                )
            ]

    monkeypatch.setattr("fli.search.SearchDates", FakeSearch)
    chunk = chunk_searches(_trips([date(2027, 3, 1)], lengths=[7]))[0]
    prices = fetch_dates(chunk, currency="USD", cabin="economy", adults=1)
    assert prices[(date(2027, 3, 1), date(2027, 3, 8))] == 100
    assert captured["currency"] == "USD"
    assert captured["language"] == "en"
    assert captured["country"] == "us"
    assert captured["filters"].duration == 7


def test_help_documents_price_pass(capsys):
    with pytest.raises(SystemExit) as caught:
        parse_args(["--help"])
    assert caught.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--price-pass" in text
    assert "fli" in text
    assert "SEARCHAPI" not in text
    assert "3 cheapest" in text
    assert "GitHub job" in text


def _config(path: Path, *, origins="[JFK]", end="2027-04-04", cap=1) -> None:
    path.write_text(
        f"""
settings:
  currency: USD
  max_requests_per_run: {cap}
  delay_seconds: [0, 0]
routes:
  - id: bog
    label: Bogota
    origins: {origins}
    destinations: [BOG]
    trip: round_trip
    depart_window: {{ start: 2027-04-01, end: {end} }}
    trip_length_days: [7, 8]
    active: true
""",
        encoding="utf-8",
    )


def _fare(**kwargs) -> SearchResult:
    item = kwargs.pop("item")
    ok = kwargs.pop("ok", True)
    price = kwargs.pop("price", 111)
    fare = FlightOption(price=price, airlines="B6", stops=1, duration="6h", depart_time="08:15", arrive_time="14:15")
    return SearchResult(
        key=item.key,
        route_id=item.route_id,
        origin=item.origin,
        dest=item.dest,
        depart=item.depart,
        return_date=item.return_date,
        ok=ok,
        error=None if ok else "empty result",
        options=[fare] if ok else [],
        cheapest=fare if ok else None,
        google_url="https://www.google.com/travel/flights?q=1" if ok else "",
        fetched_at=kwargs.get("fetched_at", "2027-04-01T00:00:00Z"),
    )


def test_price_pass_stores_sweep_prices_and_details_only_the_top_three(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    _config(config, origins="[JFK, EWR]")
    data = tmp_path / "data"
    data.mkdir()
    detailed: list[str] = []

    def fake_fetch(chunk, *, currency, cabin, adults):
        assert currency == "USD"
        assert cabin == "economy"
        assert adults == 1
        prices = {(date(2027, 1, 1), date(2027, 1, 8)): 1}
        for item in chunk.searches:
            length = (item.return_date - item.depart).days
            prices[(item.depart, item.return_date)] = 400 + item.depart.toordinal() + length
        return prices

    def fake_search(item, **kwargs):
        detailed.append(item.key)
        return _fare(item=item, fetched_at=kwargs["fetched_at"])

    monkeypatch.setattr("scraper.price_pass.fetch_dates", fake_fetch)
    monkeypatch.setattr("scraper.source_google.search", fake_search)
    code = run(
        ["--price-pass", "--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"]
    )
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in latest["routes"][0]["searches"]}
    assert code == 0
    assert status["coverage"]["mode"] == "price-pass"
    assert status["coverage"]["date_calls"] == 4  # 2 origins × 2 trip lengths
    assert status["coverage"]["detail_calls"] == 6
    assert len(rows) == 16
    assert len(detailed) == 6
    assert "2027-01-01" not in json.dumps(rows)
    for origin in ("JFK", "EWR"):
        ranked = []
        for day in range(4):
            depart = date(2027, 4, 1) + timedelta(days=day)
            for length in (7, 8):
                ranked.append((400 + depart.toordinal() + length, depart, depart + timedelta(days=length)))
        ranked.sort()
        winners = {make_key("bog", origin, "BOG", depart, ret) for _price, depart, ret in ranked[:3]}
        assert set(detailed) & {key for key in rows if f"|{origin}|" in key} == winners
        for key, row in rows.items():
            if f"|{origin}|" not in key:
                continue
            if key in winners:
                assert row["price"] == 111
                assert row["airline"] == "B6"
                assert row["stops"] == 1
                assert row["depart_time"] == "08:15"
                assert row["google_url"].startswith("https://www.google.com/travel/flights")
            else:
                assert row["airline"] is None
                assert row["stops"] is None
                assert row["duration"] is None
                assert row["depart_time"] is None
                assert row["arrive_time"] is None
                assert row["price"] > 111


def test_detail_failure_keeps_the_sweep_price(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    _config(config, end="2027-04-01")
    data = tmp_path / "data"
    data.mkdir()
    previous = {
        "generated_at": "2026-11-20T00:00:00Z",
        "currency": "USD",
        "routes": [
            {
                "id": "bog",
                "searches": [
                    {
                        "key": "bog|JFK|BOG|2027-04-01|2027-04-08",
                        "route_id": "bog",
                        "origin": "JFK",
                        "dest": "BOG",
                        "depart": "2027-04-01",
                        "return": "2027-04-08",
                        "price": 999,
                        "airline": "Old",
                        "stops": 0,
                        "fetched_at": "2026-11-20T00:00:00Z",
                        "stale": False,
                    }
                ],
            }
        ],
    }
    (data / "latest.json").write_text(json.dumps(previous), encoding="utf-8")

    def fake_fetch(chunk, **_kwargs):
        return {(item.depart, item.return_date): 250 for item in chunk.searches}

    def fake_search(item, **kwargs):
        return _fare(item=item, ok=False, fetched_at=kwargs["fetched_at"])

    monkeypatch.setattr("scraper.price_pass.fetch_dates", fake_fetch)
    monkeypatch.setattr("scraper.source_google.search", fake_search)
    code = run(["--price-pass", "--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in latest["routes"][0]["searches"]}
    kept = rows["bog|JFK|BOG|2027-04-01|2027-04-08"]
    assert code == 0
    assert kept["price"] == 250
    assert kept["stale"] is False
    assert kept["airline"] is None
    assert kept["stops"] is None


def test_date_sweep_failure_does_not_wipe_a_saved_price(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    _config(config, end="2027-04-01")
    data = tmp_path / "data"
    data.mkdir()
    previous = {
        "generated_at": "2026-11-20T00:00:00Z",
        "currency": "USD",
        "routes": [
            {
                "id": "bog",
                "searches": [
                    {
                        "key": "bog|JFK|BOG|2027-04-01|2027-04-08",
                        "route_id": "bog",
                        "origin": "JFK",
                        "dest": "BOG",
                        "depart": "2027-04-01",
                        "return": "2027-04-08",
                        "price": 410,
                        "airline": "Old",
                        "stops": 1,
                        "fetched_at": "2026-11-20T00:00:00Z",
                        "stale": False,
                    }
                ],
            }
        ],
    }
    (data / "latest.json").write_text(json.dumps(previous), encoding="utf-8")
    (data / "status.json").write_text(json.dumps({"last_success": "2026-11-20T00:00:00Z"}), encoding="utf-8")

    def fake_fetch(_chunk, **_kwargs):
        raise DateSweepError("google blocked the date sweep")

    def fake_search(_item, **_kwargs):
        raise AssertionError("detail search should not run")

    monkeypatch.setattr("scraper.price_pass.fetch_dates", fake_fetch)
    monkeypatch.setattr("scraper.source_google.search", fake_search)
    code = run(["--price-pass", "--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    row = latest["routes"][0]["searches"][0]
    assert code == 1
    assert status["state"] == "failed"
    assert row["price"] == 410
    assert row["stale"] is True
    assert row["airline"] == "Old"
    assert row["fetched_at"] == "2026-11-20T00:00:00Z"


def test_dry_run_price_pass_needs_no_key(capsys):
    code = run(
        [
            "--dry-run",
            "--price-pass",
            "--today",
            "2026-11-24",
            "--config",
            str(ROOT / "config.yaml"),
            "--only-route",
            "bog",
            "--only-origin",
            "JFK",
            "--only-dest",
            "BOG",
        ]
    )
    text = capsys.readouterr().out
    assert code == 0
    assert "date sweeps" in text
    assert "nights 7" in text
    assert format_price_plan([], []).startswith("price-pass 0 date pairs")


def test_default_run_does_not_call_the_date_sweep(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    _config(config, end="2027-04-01", cap=10)
    data = tmp_path / "data"
    data.mkdir()

    def boom(*_args, **_kwargs):
        raise AssertionError("date sweep should not be called")

    def fake_search(item, **kwargs):
        return _fare(item=item, fetched_at=kwargs["fetched_at"])

    monkeypatch.setattr("scraper.price_pass.fetch_dates", boom)
    monkeypatch.setattr("scraper.main._searcher", lambda _source: fake_search)
    code = run(["--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    assert code == 0


def test_five_date_sweep_failures_stop_before_later_chunks():
    departures = [date(2027, 4, 1) + timedelta(days=40 * offset) for offset in range(6)]
    searches = _trips(departures, lengths=[7])
    calls = {"n": 0}
    seen: list[date] = []

    def fake_fetch(chunk, **_kwargs):
        calls["n"] += 1
        seen.append(chunk.outbound_start)
        raise DateSweepError("down")

    def fake_search(_item, **_kwargs):
        raise AssertionError("detail search should not run")

    outcome = collect_price_pass(
        searches,
        currency="USD",
        cabin="economy",
        adults=1,
        detail=fake_search,
        fetch=fake_fetch,
        now=lambda: "2027-04-01T00:00:00Z",
    )
    sixth = departures[5]
    assert calls["n"] == 10
    assert sixth not in seen
    assert seen.count(departures[0]) == 2
    assert outcome.stopped_early is True
    assert len(outcome.results) == 5
    assert outcome.detail_calls == 0
    assert outcome.date_calls == 10


def test_failed_dates_are_retried_once_and_can_recover():
    searches = _trips([date(2027, 4, 1), date(2027, 4, 2)], lengths=[7])
    calls = {"n": 0}

    def fake_fetch(chunk, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}
        return {(item.depart, item.return_date): 500 for item in chunk.searches}

    def fake_search(item, **kwargs):
        return _fare(item=item, fetched_at=kwargs["fetched_at"])

    outcome = collect_price_pass(
        searches,
        currency="USD",
        cabin="economy",
        adults=1,
        detail=fake_search,
        fetch=fake_fetch,
        now=lambda: "2027-04-01T00:00:00Z",
    )
    assert calls["n"] == 2
    assert outcome.date_calls == 2
    assert all(result.ok for result in outcome.results)
    assert outcome.detail_calls == 2


def test_retry_waits_out_the_cooldown_before_the_second_pass():
    searches = _trips([date(2027, 4, 1)], lengths=[7])
    slept: list[float] = []

    def fake_fetch(chunk, **_kwargs):
        if not slept:
            return {}
        return {(item.depart, item.return_date): 500 for item in chunk.searches}

    outcome = collect_price_pass(
        searches,
        currency="USD",
        cabin="economy",
        adults=1,
        detail=lambda item, **kwargs: _fare(item=item, fetched_at=kwargs["fetched_at"]),
        fetch=fake_fetch,
        now=lambda: "2027-04-01T00:00:00Z",
        retry_cooldown=180,
        sleep=slept.append,
    )
    assert slept == [180]
    assert outcome.results[0].ok is True


def test_a_date_that_fails_twice_is_not_retried_again():
    searches = _trips([date(2027, 4, 1)], lengths=[7])
    calls = {"n": 0}

    def fake_fetch(_chunk, **_kwargs):
        calls["n"] += 1
        return {}

    outcome = collect_price_pass(
        searches,
        currency="USD",
        cabin="economy",
        adults=1,
        detail=lambda *_args, **_kwargs: None,
        fetch=fake_fetch,
        now=lambda: "2027-04-01T00:00:00Z",
    )
    assert calls["n"] == 2
    assert outcome.date_calls == 2
    assert outcome.results[0].ok is False
    assert outcome.detail_calls == 0
