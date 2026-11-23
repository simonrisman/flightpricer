from types import SimpleNamespace

from scraper.source_google import options_from_itineraries, parse_price
from scraper.source_google_playwright import search as playwright_search
from scraper.models import Search
from datetime import date


def _segment(origin, dest, depart, arrive):
    return SimpleNamespace(
        from_airport=SimpleNamespace(code=origin),
        to_airport=SimpleNamespace(code=dest),
        departure=SimpleNamespace(date=depart[0], time=depart[1]),
        arrival=SimpleNamespace(date=arrive[0], time=arrive[1]),
        duration=60,
    )


def test_parse_price():
    assert parse_price("$1,234") == 1234
    assert parse_price("USD 450") == 450
    assert parse_price(312) == 312
    assert parse_price(None) is None
    assert parse_price(0) is None
    assert parse_price("n/a") is None


def test_round_trip_stops_count_only_the_outbound_leg():
    itinerary = SimpleNamespace(
        price=312,
        airlines=["B6", "AV"],
        flights=[
            _segment("JFK", "MIA", ((2027, 3, 4), (22, 30)), ((2027, 3, 5), (1, 30))),
            _segment("MIA", "BOG", ((2027, 3, 5), (3, 0)), ((2027, 3, 5), (6, 10))),
            _segment("BOG", "JFK", ((2027, 3, 12), (8, 0)), ((2027, 3, 12), (16, 0))),
        ],
    )
    options = options_from_itineraries([itinerary, SimpleNamespace(price=None, airlines=[], flights=[])], "BOG")
    assert len(options) == 1
    assert options[0].price == 312
    assert options[0].stops == 1
    assert options[0].airlines == "B6, AV"
    assert options[0].depart_time == "22:30"
    assert options[0].arrive_time == "06:10"
    assert options[0].duration == "7h 40m"


def test_playwright_adapter_is_only_a_stub():
    item = Search(
        key="bog|JFK|BOG|2027-03-05|-",
        route_id="bog",
        origin="JFK",
        dest="BOG",
        depart=date(2027, 3, 5),
        return_date=None,
        max_stops=None,
        target_price=300,
    )
    result = playwright_search(item, currency="USD", cabin="economy", adults=1, fetched_at="2026-11-24T00:00:00Z")
    assert result.ok is False
    assert "fast_flights" in result.error
