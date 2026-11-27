"""Date-price sweep with fli, then full itineraries for the cheapest dates.

fli (the PyPI ``flights`` package) prices a departure window at one trip length
per call. Google's public search page has no calendar grid, so each departure
date is one page fetch, and one call may cover at most 93 dates. This module
groups expanded searches by airport pair and trip length, splits them into
consecutive departure windows at or under that cap, and builds the same
``(depart, return_date) -> price`` map the rest of the pipeline already uses.
The dates themselves still come from expand_routes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from scraper.backoff import call_with_backoff
from scraper.models import FlightOption, Search, SearchResult
from scraper.source_google import parse_price

DATE_CHUNK = 31
MAX_DATES_PER_SEARCH = 93
TOP_DATES = 3
CONSECUTIVE_CHUNK_FAILURES = 5
DEFAULT_MAX_WORKERS = 2

SEAT = {
    "economy": "ECONOMY",
    "premium_economy": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}


class PricePassError(Exception):
    pass


class DateSweepError(PricePassError):
    pass


@dataclass(frozen=True)
class DateChunk:
    route_id: str
    origin: str
    dest: str
    outbound_start: date
    outbound_end: date
    duration: int | None
    searches: tuple[Search, ...]
    max_stops: int | None

    @property
    def departure_days(self) -> int:
        return inclusive_days(self.outbound_start, self.outbound_end)


@dataclass
class PricePassOutcome:
    results: list[SearchResult]
    errors: list[str]
    stopped_early: bool
    detail_failures: int
    date_calls: int
    detail_calls: int

    @property
    def calendar_calls(self) -> int:
        return self.date_calls


def inclusive_days(start: date, end: date) -> int:
    return (end - start).days + 1


def stops_name(max_stops: int | None) -> str:
    if max_stops is None:
        return "ANY"
    if max_stops <= 0:
        return "NON_STOP"
    if max_stops == 1:
        return "ONE_STOP_OR_FEWER"
    return "TWO_OR_FEWER_STOPS"


def _airport(code: str):
    from fli.models import Airport

    try:
        return Airport[code]
    except KeyError as exc:
        raise DateSweepError(f"fli does not recognize airport {code}") from exc


def date_filters(chunk: DateChunk, *, cabin: str, adults: int):
    """Build the fli DateSearchFilters for one departure-window sweep."""
    from fli.models import DateSearchFilters, FlightSegment, MaxStops, PassengerInfo, SeatType, TripType

    origin = _airport(chunk.origin)
    dest = _airport(chunk.dest)
    seat = SeatType[SEAT.get(cabin, cabin.upper())]
    stops = MaxStops[stops_name(chunk.max_stops)]
    outbound = chunk.outbound_start.isoformat()
    if chunk.duration is None:
        return DateSearchFilters(
            trip_type=TripType.ONE_WAY,
            passenger_info=PassengerInfo(adults=adults),
            flight_segments=[
                FlightSegment(departure_airport=[[origin, 0]], arrival_airport=[[dest, 0]], travel_date=outbound)
            ],
            stops=stops,
            seat_type=seat,
            from_date=chunk.outbound_start.isoformat(),
            to_date=chunk.outbound_end.isoformat(),
        )
    returning = (chunk.outbound_start + timedelta(days=chunk.duration)).isoformat()
    return DateSearchFilters(
        trip_type=TripType.ROUND_TRIP,
        passenger_info=PassengerInfo(adults=adults),
        flight_segments=[
            FlightSegment(departure_airport=[[origin, 0]], arrival_airport=[[dest, 0]], travel_date=outbound),
            FlightSegment(departure_airport=[[dest, 0]], arrival_airport=[[origin, 0]], travel_date=returning),
        ],
        stops=stops,
        seat_type=seat,
        from_date=chunk.outbound_start.isoformat(),
        to_date=chunk.outbound_end.isoformat(),
        duration=chunk.duration,
    )


def parse_date_prices(rows: object) -> dict[tuple[date, date | None], int]:
    prices: dict[tuple[date, date | None], int] = {}
    if not rows:
        return prices
    for row in rows:
        raw_date = getattr(row, "date", None)
        if not raw_date:
            continue
        try:
            depart_value = raw_date[0]
            depart = depart_value.date() if hasattr(depart_value, "date") else date.fromisoformat(str(depart_value)[:10])
        except (IndexError, TypeError, ValueError):
            continue
        return_date = None
        if len(raw_date) > 1 and raw_date[1] is not None:
            try:
                return_value = raw_date[1]
                return_date = (
                    return_value.date() if hasattr(return_value, "date") else date.fromisoformat(str(return_value)[:10])
                )
            except (TypeError, ValueError):
                continue
        price = parse_price(getattr(row, "price", None))
        if price is None:
            continue
        prices[(depart, return_date)] = price
    return prices


def fetch_dates(
    chunk: DateChunk,
    *,
    currency: str,
    cabin: str,
    adults: int,
) -> dict[tuple[date, date | None], int]:
    from fli.search import SearchDates
    from fli.search.exceptions import SearchClientError

    def once():
        filters = date_filters(chunk, cabin=cabin, adults=adults)
        return SearchDates().search(filters, currency=currency, language="en", country="us")

    try:
        found = call_with_backoff(once)
    except DateSweepError:
        raise
    except SearchClientError as exc:
        raise DateSweepError(str(exc).replace("\n", " ")[:300]) from exc
    except Exception as exc:
        raise DateSweepError(f"{type(exc).__name__}: {exc}"[:300]) from exc
    return parse_date_prices(found)


def chunk_searches(
    searches: list[Search],
    *,
    date_chunk: int = DATE_CHUNK,
    max_dates: int = MAX_DATES_PER_SEARCH,
) -> list[DateChunk]:
    limit = max(1, min(date_chunk, max_dates))
    grouped: dict[tuple[str, str, str, int | None], list[Search]] = {}
    order: list[tuple[str, str, str, int | None]] = []
    seen: set[str] = set()
    for item in searches:
        if item.key in seen:
            continue
        seen.add(item.key)
        duration = None if item.return_date is None else (item.return_date - item.depart).days
        key = (item.route_id, item.origin, item.dest, duration)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)
    chunks: list[DateChunk] = []
    for key in order:
        chunks.extend(_chunk_group(grouped[key], limit=limit))
    return chunks


def _chunk_group(searches: list[Search], *, limit: int) -> list[DateChunk]:
    by_depart: dict[date, list[Search]] = {}
    for item in searches:
        by_depart.setdefault(item.depart, []).append(item)
    departures = sorted(by_depart)
    runs: list[list[date]] = []
    current: list[date] = []
    for day in departures:
        if current and (day - current[-1]).days != 1:
            runs.extend(_split_run(current, limit))
            current = [day]
            continue
        current.append(day)
    if current:
        runs.extend(_split_run(current, limit))
    chunks: list[DateChunk] = []
    for run in runs:
        items: list[Search] = []
        for day in run:
            items.extend(sorted(by_depart[day], key=lambda item: (item.return_date or date.min, item.key)))
        first = items[0]
        duration = None if first.return_date is None else (first.return_date - first.depart).days
        chunks.append(
            DateChunk(
                route_id=first.route_id,
                origin=first.origin,
                dest=first.dest,
                outbound_start=run[0],
                outbound_end=run[-1],
                duration=duration,
                searches=tuple(items),
                max_stops=first.max_stops,
            )
        )
    return chunks


def _split_run(days: list[date], limit: int) -> list[list[date]]:
    return [days[index : index + limit] for index in range(0, len(days), limit)]


def select_top(priced: list[tuple[Search, int]], limit: int = TOP_DATES) -> list[tuple[Search, int]]:
    ordered = sorted(
        priced,
        key=lambda item: (item[1], item[0].depart, item[0].return_date or date.min, item[0].key),
    )
    return ordered[:limit]


def top_per_pair(priced: list[tuple[Search, int]], limit: int = TOP_DATES) -> list[tuple[Search, int]]:
    grouped: dict[tuple[str, str, str], list[tuple[Search, int]]] = {}
    order: list[tuple[str, str, str]] = []
    for item, price in priced:
        pair = (item.route_id, item.origin, item.dest)
        if pair not in grouped:
            grouped[pair] = []
            order.append(pair)
        grouped[pair].append((item, price))
    chosen: list[tuple[Search, int]] = []
    for pair in order:
        chosen.extend(select_top(grouped[pair], limit))
    return chosen


def format_price_plan(searches: list[Search], chunks: list[DateChunk]) -> str:
    lines = [f"price-pass {len(searches)} date pairs in {len(chunks)} date sweeps"]
    for chunk in chunks:
        window = f"{chunk.outbound_start.isoformat()}..{chunk.outbound_end.isoformat()}"
        if chunk.duration is not None:
            window += f" nights {chunk.duration}"
        else:
            window += " one-way"
        lines.append(
            f"  {chunk.route_id} {chunk.origin}->{chunk.dest} {window} "
            f"days {chunk.departure_days} pairs {len(chunk.searches)}"
        )
    return "\n".join(lines)


def _configure_workers() -> int:
    raw = str(os.environ.get("FLIGHTS_MAX_WORKERS") or DEFAULT_MAX_WORKERS).strip()
    try:
        workers = int(raw)
    except ValueError:
        workers = DEFAULT_MAX_WORKERS
    workers = max(1, min(workers, 10))
    try:
        from fli.search._concurrency import configure_concurrency

        configure_concurrency(workers)
    except Exception:
        pass
    return workers


def collect_price_pass(
    searches: list[Search],
    *,
    currency: str,
    cabin: str,
    adults: int,
    detail: Callable[..., SearchResult],
    pause: Callable[[], None] | None = None,
    now: Callable[[], str] | None = None,
    fetch: Callable[..., dict] | None = None,
) -> PricePassOutcome:
    stamp = now or _utc_now
    fetch_fn = fetch or fetch_dates
    unique = _dedupe(searches)
    chunks = chunk_searches(unique)
    workers = _configure_workers()
    print(
        f"price-pass {len(unique)} date pairs, {len(chunks)} date sweeps "
        f"(up to {workers} workers), then up to {TOP_DATES} itineraries per airport pair",
        flush=True,
    )
    results_by_key: dict[str, SearchResult] = {}
    priced: list[tuple[Search, int]] = []
    stopped_early = False
    consecutive = 0
    date_calls = 0
    calls = 0

    def _sweep(index: int, total: int, chunk: DateChunk, *, label_prefix: str) -> bool:
        """Fetch one chunk. Return False when the chunk itself failed."""
        nonlocal date_calls
        fetched_at = stamp()
        date_calls += 1
        try:
            found = fetch_fn(chunk, currency=currency, cabin=cabin, adults=adults)
        except Exception as exc:
            message = _public_error(exc, "")
            for item in chunk.searches:
                results_by_key[item.key] = _failed(item, message, fetched_at)
            print(f"{label_prefix} {index}/{total}] FAIL {chunk.origin}->{chunk.dest} {message}", flush=True)
            return False
        matched = 0
        for item in chunk.searches:
            price = found.get((item.depart, item.return_date)) if isinstance(found, dict) else None
            if not isinstance(price, int):
                results_by_key[item.key] = _failed(item, "empty result", fetched_at)
                continue
            matched += 1
            results_by_key[item.key] = _price_only(item, price, fetched_at)
            priced.append((item, price))
        nights = f"nights {chunk.duration}" if chunk.duration is not None else "one-way"
        print(
            f"{label_prefix} {index}/{total}] {chunk.origin}->{chunk.dest} "
            f"{chunk.outbound_start.isoformat()}..{chunk.outbound_end.isoformat()} "
            f"{nights} {matched} prices",
            flush=True,
        )
        return True

    for index, chunk in enumerate(chunks, start=1):
        calls += 1
        if calls > 1 and pause is not None:
            pause()
        if _sweep(index, len(chunks), chunk, label_prefix="[dates"):
            consecutive = 0
            continue
        consecutive += 1
        if consecutive >= CONSECUTIVE_CHUNK_FAILURES:
            stopped_early = True
            print("circuit breaker: 5 consecutive date-sweep errors, stopping", flush=True)
            break

    failed = [item for item in unique if item.key in results_by_key and not results_by_key[item.key].ok]
    if failed:
        print(f"retrying {len(failed)} failed dates once", flush=True)
        retry_chunks = chunk_searches(failed)
        recovered = 0
        for index, chunk in enumerate(retry_chunks, start=1):
            calls += 1
            if pause is not None:
                pause()
            before = sum(1 for item in chunk.searches if results_by_key[item.key].ok)
            _sweep(index, len(retry_chunks), chunk, label_prefix="[retry")
            after = sum(1 for item in chunk.searches if results_by_key[item.key].ok)
            recovered += max(0, after - before)
        print(f"retry recovered {recovered} of {len(failed)} dates", flush=True)

    errors = [
        f"{item.origin}->{item.dest} {item.depart.isoformat()}: {results_by_key[item.key].error}"
        for item in unique
        if item.key in results_by_key and not results_by_key[item.key].ok
    ]
    priced_dates = sum(1 for item in unique if item.key in results_by_key and results_by_key[item.key].ok)
    still_failed = sum(1 for item in unique if item.key in results_by_key and not results_by_key[item.key].ok)
    print(f"dates priced {priced_dates} failed {still_failed}", flush=True)

    winners = top_per_pair(priced, TOP_DATES)
    detail_calls = 0
    detail_failures = 0
    for index, (item, _price) in enumerate(winners, start=1):
        calls += 1
        if calls > 1 and pause is not None:
            pause()
        fetched_at = stamp()
        detail_calls += 1
        error = "empty result"
        try:
            detailed = detail(
                item,
                currency=currency,
                cabin=cabin,
                adults=adults,
                fetched_at=fetched_at,
            )
        except IndexError:
            detailed = None
            error = "unreadable result"
        except Exception as exc:
            detailed = None
            error = _public_error(exc, "")
        if detailed is not None and detailed.ok and detailed.cheapest is not None:
            results_by_key[item.key] = detailed
            print(
                f"[detail {index}/{len(winners)}] {item.origin}->{item.dest} {item.depart.isoformat()} "
                f"${detailed.cheapest.price} {detailed.cheapest.airlines}",
                flush=True,
            )
            continue
        if detailed is not None and detailed.error:
            error = detailed.error
        detail_failures += 1
        errors.append(f"{item.key}: itinerary {error}")
        print(f"[detail {index}/{len(winners)}] FAIL {item.key}: {error}", flush=True)

    results = [results_by_key[item.key] for item in unique if item.key in results_by_key]
    return PricePassOutcome(
        results=results,
        errors=errors,
        stopped_early=stopped_early,
        detail_failures=detail_failures,
        date_calls=date_calls,
        detail_calls=detail_calls,
    )


def _dedupe(searches: list[Search]) -> list[Search]:
    kept: list[Search] = []
    seen: set[str] = set()
    for item in searches:
        if item.key in seen:
            continue
        seen.add(item.key)
        kept.append(item)
    return kept


def _price_only(item: Search, price: int, fetched_at: str) -> SearchResult:
    option = FlightOption(price=price)
    return SearchResult(
        key=item.key,
        route_id=item.route_id,
        origin=item.origin,
        dest=item.dest,
        depart=item.depart,
        return_date=item.return_date,
        ok=True,
        error=None,
        options=[option],
        cheapest=option,
        google_url="",
        fetched_at=fetched_at,
    )


def _failed(item: Search, error: str, fetched_at: str) -> SearchResult:
    return SearchResult(
        key=item.key,
        route_id=item.route_id,
        origin=item.origin,
        dest=item.dest,
        depart=item.depart,
        return_date=item.return_date,
        ok=False,
        error=error[:300],
        options=[],
        cheapest=None,
        google_url="",
        fetched_at=fetched_at,
    )


def _public_error(exc: BaseException, _unused: str) -> str:
    message = str(exc).replace("\n", " ").strip()
    if isinstance(exc, PricePassError) and message:
        text = message
    elif message:
        text = f"{type(exc).__name__}: {message}"
    else:
        text = type(exc).__name__
    return text[:300]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
