from __future__ import annotations

from datetime import date, timedelta

from scraper.config import DAY_NAMES, Route
from scraper.models import Search


def make_key(route_id: str, origin: str, dest: str, depart: date, return_date: date | None) -> str:
    ret = return_date.isoformat() if return_date else "-"
    return f"{route_id}|{origin}|{dest}|{depart.isoformat()}|{ret}"


def _search(
    route: Route,
    origin: str,
    dest: str,
    depart: date,
    return_date: date | None,
) -> Search | None:
    if return_date is not None and return_date <= depart:
        return None
    return Search(
        key=make_key(route.id, origin, dest, depart, return_date),
        route_id=route.id,
        origin=origin,
        dest=dest,
        depart=depart,
        return_date=return_date,
        max_stops=route.max_stops,
        target_price=route.target_price,
    )


def _window_dates(route: Route, today: date) -> list[date]:
    allowed = set(route.depart_days) if route.depart_days else set(DAY_NAMES)
    dates: list[date] = []
    cursor = route.depart_window.start
    while cursor <= route.depart_window.end:
        if cursor >= today and DAY_NAMES[cursor.weekday()] in allowed:
            dates.append(cursor)
        cursor += timedelta(days=1)
    return dates


def _route_searches(route: Route, today: date) -> list[Search]:
    found: list[Search] = []
    lengths = route.trip_length_days or []
    for depart in _window_dates(route, today):
        if route.trip == "round_trip":
            pairs = [(depart, depart + timedelta(days=length)) for length in lengths]
        else:
            pairs = [(depart, None)]
        for origin in route.origins:
            for dest in route.destinations:
                for leave, back in pairs:
                    item = _search(route, origin, dest, leave, back)
                    if item is not None:
                        found.append(item)

    for extra in route.extra_dates:
        if extra.depart < today:
            continue
        origins = [extra.origin] if extra.origin else list(route.origins)
        dests = [extra.dest] if extra.dest else list(route.destinations)
        if extra.return_date is not None:
            pairs = [(extra.depart, extra.return_date)]
        elif extra.trip_length_days is not None:
            pairs = [(extra.depart, extra.depart + timedelta(days=extra.trip_length_days))]
        elif route.trip == "round_trip":
            pairs = [(extra.depart, extra.depart + timedelta(days=length)) for length in lengths]
        else:
            pairs = [(extra.depart, None)]
        for origin in origins:
            for dest in dests:
                for leave, back in pairs:
                    item = _search(route, origin, dest, leave, back)
                    if item is not None:
                        found.append(item)
    return found


def expand_routes(routes: list[Route], today: date) -> list[Search]:
    found: list[Search] = []
    seen: set[str] = set()
    for route in routes:
        if not route.active:
            continue
        for item in _route_searches(route, today):
            if item.key in seen:
                continue
            seen.add(item.key)
            found.append(item)
    return found


def select_slice(searches: list[Search], cap: int, today: date) -> tuple[list[Search], int]:
    ordered = sorted(searches, key=lambda item: item.key)
    total = len(ordered)
    if total == 0 or total <= cap:
        return ordered, 0
    offset = (today.timetuple().tm_yday * cap) % total
    chosen = [ordered[(offset + index) % total] for index in range(cap)]
    return chosen, offset


def interleave_by_origin(searches: list[Search]) -> list[Search]:
    buckets: dict[str, list[Search]] = {}
    order: list[str] = []
    for item in searches:
        if item.origin not in buckets:
            buckets[item.origin] = []
            order.append(item.origin)
        buckets[item.origin].append(item)
    mixed: list[Search] = []
    while any(buckets.values()):
        for origin in order:
            bucket = buckets[origin]
            if bucket:
                mixed.append(bucket.pop(0))
    return mixed


def build_adhoc(
    route: Route,
    origin: str,
    dest: str,
    depart: date,
    return_date: date | None,
    today: date,
) -> Search:
    origin = origin.strip().upper()
    dest = dest.strip().upper()
    if origin not in route.origins:
        raise ValueError(f"{origin} is not an origin on {route.id}")
    if dest not in route.destinations:
        raise ValueError(f"{dest} is not a destination on {route.id}")
    if depart < today:
        raise ValueError("departure date is in the past")
    if route.trip == "round_trip" and return_date is None:
        raise ValueError("a return date is required for this round trip")
    if route.trip == "one_way":
        return_date = None
    item = _search(route, origin, dest, depart, return_date)
    if item is None:
        raise ValueError("return date must be after the departure date")
    return item
