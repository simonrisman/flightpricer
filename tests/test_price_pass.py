from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from scraper.config import ExtraDate, load_config
from scraper.expand import expand_routes, make_key
from scraper.main import parse_args, run
from scraper.models import FlightOption, Search, SearchResult
from scraper.price_pass import (
    CalendarAuthError,
    CalendarError,
    calendar_params,
    chunk_searches,
    collect_price_pass,
    fetch_calendar,
    parse_calendar,
    require_api_key,
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


def test_ten_departure_days_stay_inside_the_calendar_rectangle():
    start = date(2027, 3, 1)
    departures = [start + timedelta(days=offset) for offset in range(11)]
    chunks = chunk_searches(_trips(departures))
    assert [chunk.combos for chunk in chunks] == [180, 9]
    assert [len({item.depart for item in chunk.searches}) for chunk in chunks] == [10, 1]
    assert all(chunk.combos <= 200 for chunk in chunks)
    covered = [item.key for chunk in chunks for item in chunk.searches]
    assert len(covered) == len(set(covered)) == 11 * 9


def test_bog_window_uses_the_same_pairs_the_expander_generates():
    config = load_config(ROOT / "config.yaml")
    route = next(item for item in config.routes if item.id == "bog").model_copy(deep=True)
    route.origins = ["JFK"]
    found = expand_routes([route], date(2026, 11, 24))
    chunks = chunk_searches(found)
    assert [
        (
            chunk.outbound_start.isoformat(),
            chunk.outbound_end.isoformat(),
            chunk.return_start.isoformat(),
            chunk.return_end.isoformat(),
            chunk.combos,
            len(chunk.searches),
        )
        for chunk in chunks
    ] == [
        ("2027-03-01", "2027-03-10", "2027-03-08", "2027-03-25", 180, 90),
        ("2027-03-11", "2027-03-20", "2027-03-18", "2027-04-04", 180, 90),
        ("2027-03-21", "2027-03-21", "2027-03-28", "2027-04-05", 9, 9),
    ]
    assert [item.key for chunk in chunks for item in chunk.searches] == [item.key for item in found]


def test_sparse_dates_and_extra_dates_still_cover_every_expanded_pair():
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
    assert all(chunk.combos <= 200 for chunk in chunks)
    assert all(len({item.depart for item in chunk.searches}) <= 10 for chunk in chunks)
    assert [item.key for chunk in chunks for item in chunk.searches] == [item.key for item in found]
    assert any(chunk.outbound_start == date(2027, 8, 2) and len(chunk.searches) == 1 for chunk in chunks)


def test_a_wide_return_on_one_departure_is_split_under_the_cap():
    depart = date(2027, 4, 1)
    near = Search(
        key=make_key("bog", "JFK", "BOG", depart, depart + timedelta(days=7)),
        route_id="bog",
        origin="JFK",
        dest="BOG",
        depart=depart,
        return_date=depart + timedelta(days=7),
        max_stops=None,
        target_price=None,
    )
    far = Search(
        key=make_key("bog", "JFK", "BOG", depart, depart + timedelta(days=250)),
        route_id="bog",
        origin="JFK",
        dest="BOG",
        depart=depart,
        return_date=depart + timedelta(days=250),
        max_stops=None,
        target_price=None,
    )
    chunks = chunk_searches([near, far])
    assert len(chunks) == 2
    assert all(chunk.combos <= 200 for chunk in chunks)
    assert {item.key for chunk in chunks for item in chunk.searches} == {near.key, far.key}


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


def test_parse_calendar_keeps_prices_and_skips_empty_cells():
    prices = parse_calendar(
        {
            "calendar": [
                {"departure": "2027-03-13", "return": "2027-03-20", "price": 515},
                {"departure": "2027-03-13", "return": "2027-03-21", "price": "$507"},
                {"departure": "2027-03-14", "return": "2027-03-14", "has_no_flights": True},
                {"departure": "2027-03-15", "price": 80},
            ]
        }
    )
    assert prices[(date(2027, 3, 13), date(2027, 3, 20))] == 515
    assert prices[(date(2027, 3, 13), date(2027, 3, 21))] == 507
    assert prices[(date(2027, 3, 15), None)] == 80
    assert (date(2027, 3, 14), date(2027, 3, 14)) not in prices
    with pytest.raises(CalendarError):
        parse_calendar({"error": "Missing required parameter return_date"})


def test_calendar_params_describe_the_rectangle_without_the_key():
    chunk = chunk_searches(_trips([date(2027, 3, 1)], max_stops=0))[0]
    params = calendar_params(chunk, currency="USD", cabin="first", adults=1)
    assert "api_key" not in params
    assert params["engine"] == "google_flights_calendar"
    assert params["outbound_date_start"] == "2027-03-01"
    assert params["outbound_date_end"] == "2027-03-01"
    assert params["return_date_start"] == "2027-03-08"
    assert params["return_date_end"] == "2027-03-16"
    assert params["travel_class"] == "first_class"
    assert params["stops"] == "nonstop"
    assert chunk.combos == 9


def test_fetch_sends_the_key_in_the_header(monkeypatch):
    captured = {}

    class Response:
        def read(self):
            return b'{"calendar":[{"departure":"2027-03-01","return":"2027-03-08","price":100}]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_open(request, timeout=0):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_open)
    chunk = chunk_searches(_trips([date(2027, 3, 1)], lengths=[7]))[0]
    prices = fetch_calendar(chunk, api_key="secret-key", currency="USD", cabin="economy", adults=1)
    assert prices[(date(2027, 3, 1), date(2027, 3, 8))] == 100
    assert "secret-key" not in captured["url"]
    assert "api_key" not in captured["url"]
    assert captured["auth"] == "Bearer secret-key"


def test_missing_key_is_explicit():
    with pytest.raises(Exception) as caught:
        require_api_key({})
    assert "SEARCHAPI_API_KEY" in str(caught.value)
    assert "will not fall back" in str(caught.value)


def test_help_documents_price_pass(capsys):
    with pytest.raises(SystemExit) as caught:
        parse_args(["--help"])
    assert caught.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--price-pass" in text
    assert "SEARCHAPI_API_KEY" in text
    assert "3 cheapest" in text
    assert "does not fall back" in text
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
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")
    config = tmp_path / "config.yaml"
    _config(config, origins="[JFK, EWR]")
    data = tmp_path / "data"
    data.mkdir()
    detailed: list[str] = []

    def fake_fetch(chunk, *, api_key, currency, cabin, adults):
        assert api_key == "test-key"
        prices = {(date(2027, 1, 1), date(2027, 1, 8)): 1}
        for item in chunk.searches:
            length = (item.return_date - item.depart).days
            prices[(item.depart, item.return_date)] = 400 + item.depart.toordinal() + length
        return prices

    def fake_search(item, **kwargs):
        detailed.append(item.key)
        return _fare(item=item, fetched_at=kwargs["fetched_at"])

    monkeypatch.setattr("scraper.price_pass.fetch_calendar", fake_fetch)
    monkeypatch.setattr("scraper.source_google.search", fake_search)
    code = run(
        ["--price-pass", "--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"]
    )
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in latest["routes"][0]["searches"]}
    assert code == 0
    assert status["coverage"]["mode"] == "price-pass"
    assert status["coverage"]["calendar_calls"] == 2
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
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")
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

    monkeypatch.setattr("scraper.price_pass.fetch_calendar", fake_fetch)
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


def test_calendar_failure_does_not_wipe_a_saved_price(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")
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
        raise CalendarError("calendar down")

    def fake_search(_item, **_kwargs):
        raise AssertionError("detail search should not run")

    monkeypatch.setattr("scraper.price_pass.fetch_calendar", fake_fetch)
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


def test_missing_key_does_not_query_dates_or_change_prices(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
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
                        "price": 410,
                        "stale": False,
                        "depart": "2027-04-01",
                        "route_id": "bog",
                    }
                ],
            }
        ],
    }
    latest_path = data / "latest.json"
    latest_path.write_text(json.dumps(previous), encoding="utf-8")
    before = latest_path.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise AssertionError("calendar should not be called")

    monkeypatch.setattr("scraper.price_pass.fetch_calendar", boom)
    monkeypatch.setattr("scraper.source_google.search", boom)
    code = run(["--price-pass", "--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    captured = capsys.readouterr()
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    assert code == 1
    assert "SEARCHAPI_API_KEY" in captured.err
    assert "will not fall back" in captured.err
    assert latest_path.read_text(encoding="utf-8") == before
    assert status["state"] == "failed"


def test_rejected_key_leaves_saved_prices_untouched(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")
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
                        "depart": "2027-04-01",
                        "price": 410,
                        "stale": False,
                        "airline": "Old",
                    }
                ],
            }
        ],
    }
    latest_path = data / "latest.json"
    latest_path.write_text(json.dumps(previous), encoding="utf-8")
    before = latest_path.read_text(encoding="utf-8")

    def fake_fetch(_chunk, **_kwargs):
        raise CalendarAuthError("SearchAPI rejected SEARCHAPI_API_KEY. Saved prices were left unchanged.")

    monkeypatch.setattr("scraper.price_pass.fetch_calendar", fake_fetch)
    code = run(["--price-pass", "--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    assert code == 1
    assert latest_path.read_text(encoding="utf-8") == before


def test_dry_run_price_pass_needs_no_key(capsys, monkeypatch):
    monkeypatch.delenv("SEARCHAPI_API_KEY", raising=False)
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
    assert "calendar chunks" in text
    assert "combos 180" in text


def test_default_run_does_not_call_the_calendar(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEARCHAPI_API_KEY", "test-key")
    config = tmp_path / "config.yaml"
    _config(config, end="2027-04-01", cap=10)
    data = tmp_path / "data"
    data.mkdir()

    def boom(*_args, **_kwargs):
        raise AssertionError("calendar should not be called")

    def fake_search(item, **kwargs):
        return _fare(item=item, fetched_at=kwargs["fetched_at"])

    monkeypatch.setattr("scraper.price_pass.fetch_calendar", boom)
    monkeypatch.setattr("scraper.main._searcher", lambda _source: fake_search)
    code = run(["--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    assert code == 0


def test_five_calendar_failures_stop_before_later_chunks():
    departures = [date(2027, 4, 1) + timedelta(days=40 * offset) for offset in range(6)]
    searches = _trips(departures, lengths=[7])
    calls = {"n": 0}

    def fake_fetch(_chunk, **_kwargs):
        calls["n"] += 1
        raise CalendarError("down")

    def fake_search(_item, **_kwargs):
        raise AssertionError("detail search should not run")

    outcome = collect_price_pass(
        searches,
        api_key="test-key",
        currency="USD",
        cabin="economy",
        adults=1,
        detail=fake_search,
        fetch=fake_fetch,
        now=lambda: "2027-04-01T00:00:00Z",
    )
    assert calls["n"] == 5
    assert outcome.stopped_early is True
    assert len(outcome.results) == 5
    assert outcome.detail_calls == 0
