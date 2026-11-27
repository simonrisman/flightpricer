from __future__ import annotations

import time
from datetime import datetime

from scraper.backoff import is_throttle, throttle_delay
from scraper.models import FlightOption, Search, SearchResult


def parse_price(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        price = int(round(value))
        return price if price > 0 else None
    text = str(value).strip().replace(",", "").replace("$", "")
    cleaned = []
    for char in text:
        if char.isdigit() or char == ".":
            cleaned.append(char)
    token = "".join(cleaned)
    if not token:
        return None
    try:
        price = int(round(float(token)))
    except ValueError:
        return None
    return price if price > 0 else None


def format_minutes(minutes: int) -> str:
    minutes = max(0, int(minutes))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def format_clock(value: object) -> str:
    time_part = getattr(value, "time", None)
    if not isinstance(time_part, tuple) or len(time_part) < 2:
        return "—"
    hour, minute = time_part[0], time_part[1]
    try:
        return f"{int(hour):02d}:{int(minute):02d}"
    except (TypeError, ValueError):
        return "—"


def _as_datetime(value: object) -> datetime | None:
    date_part = getattr(value, "date", None)
    time_part = getattr(value, "time", None)
    if not isinstance(date_part, tuple) or not isinstance(time_part, tuple):
        return None
    if len(date_part) < 3 or len(time_part) < 2:
        return None
    try:
        return datetime(int(date_part[0]), int(date_part[1]), int(date_part[2]), int(time_part[0]), int(time_part[1]))
    except (TypeError, ValueError):
        return None


def format_airlines(raw: object) -> str:
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
        else:
            name = str(getattr(item, "name", None) or getattr(item, "code", "") or "").strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else "—"


def _outbound(segments: list, dest: str) -> list:
    if not segments:
        return []
    outbound: list = []
    for segment in segments:
        outbound.append(segment)
        airport = getattr(segment, "to_airport", None)
        code = getattr(airport, "code", None)
        if code == dest:
            break
    return outbound


def option_from_itinerary(itinerary: object, dest: str) -> FlightOption | None:
    price = parse_price(getattr(itinerary, "price", None))
    if price is None:
        return None
    segments = list(getattr(itinerary, "flights", None) or [])
    outbound = _outbound(segments, dest)
    if not outbound:
        return FlightOption(price=price, airlines=format_airlines(getattr(itinerary, "airlines", None)), stops=0, duration="—", depart_time="—", arrive_time="—")
    stops = max(0, len(outbound) - 1)
    start = _as_datetime(getattr(outbound[0], "departure", None))
    end = _as_datetime(getattr(outbound[-1], "arrival", None))
    if start and end:
        minutes = int((end - start).total_seconds() // 60)
        if minutes <= 0:
            minutes = sum(int(getattr(segment, "duration", 0) or 0) for segment in outbound)
    else:
        minutes = sum(int(getattr(segment, "duration", 0) or 0) for segment in outbound)
    return FlightOption(
        price=price,
        airlines=format_airlines(getattr(itinerary, "airlines", None)),
        stops=stops,
        duration=format_minutes(minutes),
        depart_time=format_clock(getattr(outbound[0], "departure", None)),
        arrive_time=format_clock(getattr(outbound[-1], "arrival", None)),
    )


def options_from_itineraries(itineraries: object, dest: str) -> list[FlightOption]:
    options: list[FlightOption] = []
    for itinerary in itineraries or []:
        option = option_from_itinerary(itinerary, dest)
        if option is not None:
            options.append(option)
    options.sort(key=lambda item: item.price)
    return options[:5]


def _blank(search: Search, error: str, google_url: str = "", fetched_at: str = "") -> SearchResult:
    return SearchResult(
        key=search.key,
        route_id=search.route_id,
        origin=search.origin,
        dest=search.dest,
        depart=search.depart,
        return_date=search.return_date,
        ok=False,
        error=error[:300],
        options=[],
        cheapest=None,
        google_url=google_url,
        fetched_at=fetched_at,
    )


UNREADABLE = "unreadable result"


def _short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    return text[:300]


def search(search_item: Search, *, currency: str, cabin: str, adults: int, fetched_at: str) -> SearchResult:
    try:
        from fast_flights import FlightQuery, FlightsNotFound, Passengers, create_query, get_flights
    except Exception as exc:  # pragma: no cover - depends on the runner image
        return _blank(search_item, _short_error(exc), fetched_at=fetched_at)

    seat = {
        "economy": "economy",
        "premium_economy": "premium-economy",
        "business": "business",
        "first": "first",
    }[cabin]
    legs = [
        FlightQuery(
            date=search_item.depart.isoformat(),
            from_airport=search_item.origin,
            to_airport=search_item.dest,
            max_stops=search_item.max_stops,
        )
    ]
    trip = "one-way"
    if search_item.return_date is not None:
        trip = "round-trip"
        legs.append(
            FlightQuery(
                date=search_item.return_date.isoformat(),
                from_airport=search_item.dest,
                to_airport=search_item.origin,
                max_stops=search_item.max_stops,
            )
        )
    try:
        query = create_query(
            flights=legs,
            seat=seat,
            trip=trip,
            passengers=Passengers(adults=adults),
            language="en",
            currency=currency,
        )
        google_url = query.url()
    except Exception as exc:
        return _blank(search_item, _short_error(exc), fetched_at=fetched_at)

    last_error = "empty result"
    attempt = 0
    throttle_attempts = 0
    while True:
        try:
            result = get_flights(query)
        except FlightsNotFound:
            return _blank(search_item, "empty result", google_url, fetched_at)
        except IndexError:
            return _blank(search_item, UNREADABLE, google_url, fetched_at)
        except Exception as exc:
            last_error = _short_error(exc)
            if is_throttle(exc) and throttle_attempts < 4:
                time.sleep(throttle_delay(throttle_attempts))
                throttle_attempts += 1
                continue
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                attempt += 1
                continue
            return _blank(search_item, last_error, google_url, fetched_at)
        try:
            options = options_from_itineraries(result, search_item.dest)
        except (IndexError, KeyError, TypeError):
            return _blank(search_item, UNREADABLE, google_url, fetched_at)
        if not options:
            return _blank(search_item, "empty result", google_url, fetched_at)
        return SearchResult(
            key=search_item.key,
            route_id=search_item.route_id,
            origin=search_item.origin,
            dest=search_item.dest,
            depart=search_item.depart,
            return_date=search_item.return_date,
            ok=True,
            error=None,
            options=options,
            cheapest=options[0],
            google_url=google_url,
            fetched_at=fetched_at,
        )
    return _blank(search_item, last_error, google_url, fetched_at)
