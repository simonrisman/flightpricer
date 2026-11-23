from datetime import date

from scraper.config import AppConfig, DateWindow, Route
from scraper.summarize import decide_state, summarize_routes


def _route() -> Route:
    return Route(
        id="bog",
        label="Bogotá",
        origins=["JFK"],
        destinations=["BOG"],
        trip="round_trip",
        depart_window=DateWindow(start=date(2027, 3, 1), end=date(2027, 3, 21)),
        trip_length_days=[7],
        target_price=300,
    )


def test_decide_state():
    assert decide_state(0, 0) == "ok"
    assert decide_state(4, 4) == "ok"
    assert decide_state(4, 3) == "degraded"
    assert decide_state(4, 0) == "failed"


def test_summary_delta_best_and_target():
    run_date = date(2026, 11, 24)
    entry = {
        "key": "bog|JFK|BOG|2027-03-05|2027-03-12",
        "route_id": "bog",
        "origin": "JFK",
        "dest": "BOG",
        "depart": "2027-03-05",
        "return": "2027-03-12",
        "price": 310,
        "fetched_at": "2026-11-24T12:00:00Z",
        "stale": False,
    }
    history = [
        {"date": "2026-11-20", "key": entry["key"], "price": 280, "depart": "2027-03-05"},
        {"date": "2026-11-22", "key": entry["key"], "price": 290, "depart": "2027-03-05"},
    ]
    routes = summarize_routes([_route()], {entry["key"]: entry}, {"bog": history}, run_date)
    search = routes[0]["searches"][0]
    assert search["prev_price"] == 290
    assert search["delta"] == 20
    assert search["best_ever"] == 280
    assert search["best_ever_date"] == "2026-11-20"
    assert search["hit_target"] is False
    assert routes[0]["last_success"] == "2026-11-24T12:00:00Z"
    assert routes[0]["cheapest"]["price"] == 310

    entry["price"] = 250
    routes = summarize_routes([_route()], {entry["key"]: entry}, {"bog": history}, run_date)
    assert routes[0]["searches"][0]["hit_target"] is True
    assert routes[0]["target_hits"] == 1
    assert routes[0]["searches"][0]["best_ever"] == 250


def test_stale_price_does_not_count_as_today():
    entry = {
        "key": "bog|JFK|BOG|2027-03-05|2027-03-12",
        "route_id": "bog",
        "origin": "JFK",
        "dest": "BOG",
        "depart": "2027-03-05",
        "return": "2027-03-12",
        "price": 400,
        "fetched_at": "2026-11-01T00:00:00Z",
        "stale": True,
    }
    routes = summarize_routes([_route()], {entry["key"]: entry}, {"bog": []}, date(2026, 11, 24))
    search = routes[0]["searches"][0]
    assert search["prev_price"] is None
    assert search["best_ever"] == 400
    assert search["best_ever_date"] == "2026-11-01"
    assert AppConfig.model_validate(
        {
            "routes": [
                {
                    "id": "x",
                    "label": "X",
                    "origins": ["JFK"],
                    "destinations": ["BOG"],
                    "trip": "one_way",
                    "depart_window": {"start": "2027-01-01", "end": "2027-01-02"},
                }
            ]
        }
    )
