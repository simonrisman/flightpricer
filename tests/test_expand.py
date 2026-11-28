from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from scraper.config import ConfigError, load_config
from scraper.expand import build_adhoc, expand_routes, interleave_by_origin, make_key, select_slice
from scraper.models import Search
from scraper.price_pass import chunk_searches

ROOT = Path(__file__).parents[1]
TODAY = date(2026, 11, 23)
ORIGINS = ["JFK", "EWR", "BOS"]
LENGTHS = [7, 8, 9, 10, 11, 12, 13, 14, 15]
EXPECTED = {
    "bog": ("Bogotá", ["BOG"], "2027-03-01", "2027-03-21"),
    "mde": ("Medellín", ["MDE"], "2027-03-01", "2027-03-21"),
    "eze": ("Buenos Aires", ["EZE"], "2027-04-01", "2027-05-15"),
    "par": ("Paris", ["CDG"], "2027-05-17", "2027-06-25"),
    "mad": ("Madrid", ["MAD"], "2027-04-12", "2027-05-31"),
    "bcn": ("Barcelona", ["BCN"], "2027-05-17", "2027-06-20"),
    "tbs": ("Tbilisi", ["TBS"], "2027-08-01", "2027-09-30"),
    "pdl": ("Ponta Delgada", ["PDL"], "2027-06-01", "2027-07-10"),
    "fnc": ("Funchal", ["FNC"], "2027-04-12", "2027-06-10"),
}


def test_real_config_matches_the_route_list():
    config = load_config(ROOT / "config.yaml")
    assert [route.id for route in config.routes] == list(EXPECTED)
    for route in config.routes:
        label, dests, start, end = EXPECTED[route.id]
        assert route.label == label
        assert route.destinations == dests
        assert route.origins == ORIGINS
        assert route.trip == "round_trip"
        assert route.active is True
        assert route.depart_window.start.isoformat() == start
        assert route.depart_window.end.isoformat() == end
        assert route.depart_days is None
        assert route.trip_length_days == ([14, 15, 16, 17] if route.id == "tbs" else LENGTHS)
        assert route.target_price is None


def test_invalid_config_explains_the_field(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
settings:
  currency: USD
routes:
  - id: bog
    label: Bogota
    origins: [JFK]
    destinations: [BOG]
    trip: round_trip
    depart_window: { start: 2027-03-21, end: 2027-03-01 }
    trip_length_days: [7]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert "depart_window" in str(caught.value)


def _route(config, route_id):
    return next(route for route in config.routes if route.id == route_id)


def test_expand_filters_weekdays_lengths_and_past_dates():
    config = load_config(ROOT / "config.yaml")
    route = _route(config, "bog").model_copy(deep=True)
    route.origins = ["JFK", "EWR"]
    route.destinations = ["BOG"]
    route.depart_window.start = date(2027, 3, 1)
    route.depart_window.end = date(2027, 3, 7)
    route.depart_days = ["thu"]
    route.trip_length_days = [7, 8]
    found = expand_routes([route], date(2027, 3, 4))
    assert date(2027, 3, 4).strftime("%A") == "Thursday"
    assert len(found) == 4
    assert {item.depart for item in found} == {date(2027, 3, 4)}
    assert {item.return_date for item in found} == {date(2027, 3, 11), date(2027, 3, 12)}
    assert make_key("bog", "JFK", "BOG", date(2027, 3, 4), date(2027, 3, 11)) in {item.key for item in found}


def test_extra_date_outside_the_window_is_included_once():
    config = load_config(ROOT / "config.yaml")
    route = _route(config, "eze").model_copy(deep=True)
    route.origins = ["JFK"]
    route.destinations = ["EZE"]
    route.extra_dates = []
    from scraper.config import ExtraDate

    route.extra_dates = [ExtraDate(depart=date(2027, 6, 2), return_date=date(2027, 6, 16), origin="BOS", dest="EZE")]
    found = expand_routes([route], TODAY)
    extra = [item for item in found if item.depart == date(2027, 6, 2)]
    assert len(extra) == 1
    assert extra[0].origin == "BOS"
    assert extra[0].return_date == date(2027, 6, 16)
    assert len(found) == len({item.key for item in found})


def test_inactive_route_is_skipped():
    config = load_config(ROOT / "config.yaml")
    route = _route(config, "bog").model_copy(deep=True)
    route.active = False
    assert expand_routes([route], TODAY) == []


def test_one_way_key_and_adhoc_round_trip():
    config = load_config(ROOT / "config.yaml")
    route = _route(config, "eze").model_copy(deep=True)
    route.trip = "one_way"
    route.trip_length_days = None
    route.depart_days = None
    route.depart_window.start = date(2027, 4, 1)
    route.depart_window.end = date(2027, 4, 1)
    route.origins = ["EWR"]
    found = expand_routes([route], TODAY)
    assert len(found) == 1
    assert found[0].key.endswith("|-")
    assert found[0].return_date is None

    round_trip = _route(config, "eze")
    adhoc = build_adhoc(round_trip, "jfk", "eze", date(2027, 8, 5), date(2027, 8, 15), TODAY)
    assert adhoc.key == "eze|JFK|EZE|2027-08-05|2027-08-15"
    with pytest.raises(ValueError):
        build_adhoc(round_trip, "JFK", "EZE", TODAY - timedelta(days=1), TODAY, TODAY)


def test_slice_covers_every_search_and_interleave_spreads_origins():
    searches = [
        Search(
            key=f"r|{origin}|BOG|2027-03-{index + 1:02d}|-",
            route_id="r",
            origin=origin,
            dest="BOG",
            depart=date(2027, 3, index + 1),
            return_date=None,
            max_stops=None,
            target_price=None,
        )
        for origin in ("JFK", "LGA")
        for index in range(5)
    ]
    seen: set[str] = set()
    for day in range(1, 367):
        chosen, _offset = select_slice(searches, 4, date(2027, 1, 1) + timedelta(days=day - 1))
        assert len(chosen) == 4
        seen.update(item.key for item in chosen)
    assert seen == {item.key for item in searches}

    ordered = [item for item in searches if item.origin == "JFK"] + [item for item in searches if item.origin == "LGA"]
    mixed = interleave_by_origin(ordered)
    assert [item.origin for item in mixed] == ["JFK", "LGA"] * 5


def test_real_routes_only_use_requested_weekdays():
    config = load_config(ROOT / "config.yaml")
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    found = expand_routes(config.routes, TODAY)
    assert len(found) > 500
    by_route = {route.id: [] for route in config.routes}
    for item in found:
        by_route[item.route_id].append(item)
    for route in config.routes:
        assert by_route[route.id]
        for item in by_route[route.id]:
            if item.depart < route.depart_window.start or item.depart > route.depart_window.end:
                continue
            assert names[item.depart.weekday()] in (route.depart_days or names)
            assert item.origin in route.origins
            assert item.dest in route.destinations
            if route.trip == "round_trip":
                assert (item.return_date - item.depart).days in route.trip_length_days


def test_tbilisi_window_and_trip_length_set_its_date_sweeps():
    config = load_config(ROOT / "config.yaml")
    route = _route(config, "tbs")
    found = expand_routes([route], TODAY)
    departures = {item.depart for item in found}
    lengths = {(item.return_date - item.depart).days for item in found}
    assert min(departures) == date(2027, 8, 1)
    assert max(departures) == date(2027, 9, 30)
    assert len(departures) == 61
    assert lengths == {14, 15, 16, 17}
    assert len(found) == 61 * 4 * 3
    bos = [item for item in found if item.origin == "BOS"]
    sweeps = chunk_searches(bos)
    assert len(sweeps) == 8
    assert {chunk.duration for chunk in sweeps} == {14, 15, 16, 17}
    assert sum(len(chunk.searches) for chunk in sweeps) == len(bos)
