"""Calendar price sweep, then full itineraries for the cheapest dates.

SearchAPI's google_flights_calendar engine prices every outbound/return pair inside a
date rectangle, and it returns nothing once that rectangle exceeds 200 combinations.
Trip lengths 7-15 across 10 departure days are 10 by 18 = 180, so a chunk takes at
most 10 departure dates and fewer when a gap would push the rectangle over the limit.
The dates themselves still come from expand_routes.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from scraper.models import FlightOption, Search, SearchResult
from scraper.source_google import parse_price

SEARCH_URL = "https://www.searchapi.io/api/v1/search"
MAX_COMBOS = 200
DEPARTURE_CHUNK = 10
TOP_DATES = 3
CONSECUTIVE_CHUNK_FAILURES = 5

TRAVEL_CLASS = {
    "economy": "economy",
    "premium_economy": "premium_economy",
    "business": "business",
    "first": "first_class",
}


class PricePassError(Exception):
    pass


class CalendarError(PricePassError):
    pass


class CalendarAuthError(PricePassError):
    pass


@dataclass(frozen=True)
class CalendarChunk:
    route_id: str
    origin: str
    dest: str
    outbound_start: date
    outbound_end: date
    return_start: date | None
    return_end: date | None
    searches: tuple[Search, ...]
    max_stops: int | None

    @property
    def combos(self) -> int:
        outbound = inclusive_days(self.outbound_start, self.outbound_end)
        if self.return_start is None or self.return_end is None:
            return outbound
        return outbound * inclusive_days(self.return_start, self.return_end)


@dataclass
class PricePassOutcome:
    results: list[SearchResult]
    errors: list[str]
    stopped_early: bool
    detail_failures: int
    calendar_calls: int
    detail_calls: int


def inclusive_days(start: date, end: date) -> int:
    return (end - start).days + 1


def require_api_key(environ: dict | None = None) -> str:
    source = os.environ if environ is None else environ
    key = str(source.get("SEARCHAPI_API_KEY") or "").strip()
    if not key:
        raise PricePassError(
            "SEARCHAPI_API_KEY is not set. The price pass needs a SearchAPI key for the "
            "calendar sweep and will not fall back to querying every date."
        )
    return key


def stops_param(max_stops: int | None) -> str | None:
    if max_stops is None:
        return None
    if max_stops <= 0:
        return "nonstop"
    if max_stops == 1:
        return "one_stop_or_fewer"
    return "two_stops_or_fewer"


def calendar_params(chunk: CalendarChunk, *, currency: str, cabin: str, adults: int) -> dict:
    flight_type = "one_way" if chunk.return_start is None else "round_trip"
    params: dict = {
        "engine": "google_flights_calendar",
        "flight_type": flight_type,
        "departure_id": chunk.origin,
        "arrival_id": chunk.dest,
        "outbound_date": chunk.outbound_start.isoformat(),
        "outbound_date_start": chunk.outbound_start.isoformat(),
        "outbound_date_end": chunk.outbound_end.isoformat(),
        "currency": currency,
        "travel_class": TRAVEL_CLASS.get(cabin, cabin),
        "adults": str(adults),
        "hl": "en",
        "gl": "us",
    }
    if flight_type == "round_trip":
        params["return_date"] = chunk.return_start.isoformat()
        params["return_date_start"] = chunk.return_start.isoformat()
        params["return_date_end"] = chunk.return_end.isoformat()
    stops = stops_param(chunk.max_stops)
    if stops is not None:
        params["stops"] = stops
    return params


def parse_calendar(payload: object) -> dict[tuple[date, date | None], int]:
    if not isinstance(payload, dict):
        raise CalendarError("calendar response was not a JSON object")
    error = payload.get("error")
    if error:
        text = error if isinstance(error, str) else json.dumps(error)
        raise CalendarError(text.replace("\n", " ")[:300])
    rows = payload.get("calendar")
    if not isinstance(rows, list):
        raise CalendarError("calendar response did not include prices")
    prices: dict[tuple[date, date | None], int] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("has_no_flights"):
            continue
        depart_text = row.get("departure")
        if not isinstance(depart_text, str):
            continue
        try:
            depart = date.fromisoformat(depart_text)
        except ValueError:
            continue
        return_text = row.get("return")
        return_date = None
        if return_text:
            try:
                return_date = date.fromisoformat(str(return_text))
            except ValueError:
                continue
        price = parse_price(row.get("price"))
        if price is None:
            continue
        prices[(depart, return_date)] = price
    return prices


def fetch_calendar(
    chunk: CalendarChunk,
    *,
    api_key: str,
    currency: str,
    cabin: str,
    adults: int,
) -> dict[tuple[date, date | None], int]:
    params = calendar_params(chunk, currency=currency, cabin=cabin, adults=adults)
    request = urllib.request.Request(
        f"{SEARCH_URL}?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise CalendarAuthError(
                "SearchAPI rejected SEARCHAPI_API_KEY. Saved prices were left unchanged."
            ) from exc
        detail = exc.read().decode("utf-8", errors="replace").replace("\n", " ")[:200]
        raise CalendarError(f"calendar HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise CalendarError(f"calendar request failed: {exc.reason}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise CalendarError("calendar response was not JSON") from exc
    return parse_calendar(payload)


def chunk_searches(
    searches: list[Search],
    *,
    departure_chunk: int = DEPARTURE_CHUNK,
    max_combos: int = MAX_COMBOS,
) -> list[CalendarChunk]:
    grouped: dict[tuple[str, str, str], list[Search]] = {}
    order: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in searches:
        if item.key in seen:
            continue
        seen.add(item.key)
        pair = (item.route_id, item.origin, item.dest)
        if pair not in grouped:
            grouped[pair] = []
            order.append(pair)
        grouped[pair].append(item)
    chunks: list[CalendarChunk] = []
    for pair in order:
        chunks.extend(_chunk_pair(grouped[pair], departure_chunk=departure_chunk, max_combos=max_combos))
    return chunks


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


def format_price_plan(searches: list[Search], chunks: list[CalendarChunk]) -> str:
    lines = [f"price-pass {len(searches)} date pairs in {len(chunks)} calendar chunks"]
    for chunk in chunks:
        window = f"{chunk.outbound_start.isoformat()}..{chunk.outbound_end.isoformat()}"
        if chunk.return_start is not None and chunk.return_end is not None:
            window += f" return {chunk.return_start.isoformat()}..{chunk.return_end.isoformat()}"
        lines.append(
            f"  {chunk.route_id} {chunk.origin}->{chunk.dest} {window} "
            f"combos {chunk.combos} pairs {len(chunk.searches)}"
        )
    return "\n".join(lines)


def collect_price_pass(
    searches: list[Search],
    *,
    api_key: str,
    currency: str,
    cabin: str,
    adults: int,
    detail: Callable[..., SearchResult],
    pause: Callable[[], None] | None = None,
    now: Callable[[], str] | None = None,
    fetch: Callable[..., dict] | None = None,
) -> PricePassOutcome:
    stamp = now or _utc_now
    fetch_fn = fetch or fetch_calendar
    unique = _dedupe(searches)
    chunks = chunk_searches(unique)
    print(
        f"price-pass {len(unique)} date pairs, {len(chunks)} calendar chunks, "
        f"then up to {TOP_DATES} itineraries per airport pair",
        flush=True,
    )
    results_by_key: dict[str, SearchResult] = {}
    priced: list[tuple[Search, int]] = []
    errors: list[str] = []
    stopped_early = False
    consecutive = 0
    calendar_calls = 0
    calls = 0

    for index, chunk in enumerate(chunks, start=1):
        calls += 1
        if calls > 1 and pause is not None:
            pause()
        fetched_at = stamp()
        calendar_calls += 1
        try:
            found = fetch_fn(chunk, api_key=api_key, currency=currency, cabin=cabin, adults=adults)
        except CalendarAuthError:
            raise
        except Exception as exc:
            message = _public_error(exc, api_key)
            errors.append(f"{chunk.origin}->{chunk.dest} {chunk.outbound_start.isoformat()}: {message}")
            for item in chunk.searches:
                results_by_key[item.key] = _failed(item, message, fetched_at)
            consecutive += 1
            print(f"[calendar {index}/{len(chunks)}] FAIL {chunk.origin}->{chunk.dest} {message}", flush=True)
            if consecutive >= CONSECUTIVE_CHUNK_FAILURES:
                stopped_early = True
                print("circuit breaker: 5 consecutive calendar errors, stopping", flush=True)
                break
            continue
        consecutive = 0
        matched = 0
        for item in chunk.searches:
            price = found.get((item.depart, item.return_date)) if isinstance(found, dict) else None
            if not isinstance(price, int):
                results_by_key[item.key] = _failed(item, "empty result", fetched_at)
                continue
            matched += 1
            results_by_key[item.key] = _price_only(item, price, fetched_at)
            priced.append((item, price))
        print(
            f"[calendar {index}/{len(chunks)}] {chunk.origin}->{chunk.dest} "
            f"{chunk.outbound_start.isoformat()}..{chunk.outbound_end.isoformat()} {matched} prices",
            flush=True,
        )

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
            error = _public_error(exc, api_key)
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
        calendar_calls=calendar_calls,
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


def _chunk_pair(searches: list[Search], *, departure_chunk: int, max_combos: int) -> list[CalendarChunk]:
    round_trips = [item for item in searches if item.return_date is not None]
    one_way = [item for item in searches if item.return_date is None]
    chunks: list[CalendarChunk] = []
    if round_trips:
        chunks.extend(_chunk_round_trips(round_trips, departure_chunk=departure_chunk, max_combos=max_combos))
    if one_way:
        chunks.extend(_chunk_one_way(one_way, max_combos=max_combos))
    return chunks


def _chunk_round_trips(searches: list[Search], *, departure_chunk: int, max_combos: int) -> list[CalendarChunk]:
    by_depart: dict[date, list[Search]] = {}
    for item in searches:
        by_depart.setdefault(item.depart, []).append(item)
    departures = sorted(by_depart)
    chunks: list[CalendarChunk] = []
    index = 0
    while index < len(departures):
        best: int | None = None
        limit = min(len(departures), index + max(1, departure_chunk))
        for end in range(index + 1, limit + 1):
            if _rectangle(departures[index:end], by_depart)[4] <= max_combos:
                best = end
            else:
                break
        if best is None:
            chunks.extend(_split_one_departure(by_depart[departures[index]], max_combos=max_combos))
            index += 1
            continue
        chunks.append(_round_chunk(departures[index:best], by_depart))
        index = best
    return chunks


def _split_one_departure(items: list[Search], *, max_combos: int) -> list[CalendarChunk]:
    ordered = sorted(items, key=lambda item: (item.return_date or date.min, item.key))
    buckets: list[list[Search]] = []
    current: list[Search] = []
    for item in ordered:
        trial = current + [item]
        if _return_span(trial) <= max_combos:
            current = trial
            continue
        if current:
            buckets.append(current)
        current = [item]
    if current:
        buckets.append(current)
    return [_round_chunk_from_items(bucket) for bucket in buckets if bucket]


def _chunk_one_way(searches: list[Search], *, max_combos: int) -> list[CalendarChunk]:
    by_depart: dict[date, Search] = {}
    for item in searches:
        by_depart.setdefault(item.depart, item)
    departures = sorted(by_depart)
    chunks: list[CalendarChunk] = []
    index = 0
    while index < len(departures):
        best = index + 1
        for end in range(index + 1, len(departures) + 1):
            if inclusive_days(departures[index], departures[end - 1]) <= max_combos:
                best = end
            else:
                break
        selected = departures[index:best]
        items = tuple(by_depart[day] for day in selected)
        first = items[0]
        chunks.append(
            CalendarChunk(
                route_id=first.route_id,
                origin=first.origin,
                dest=first.dest,
                outbound_start=selected[0],
                outbound_end=selected[-1],
                return_start=None,
                return_end=None,
                searches=items,
                max_stops=first.max_stops,
            )
        )
        index = best
    return chunks


def _rectangle(departures: list[date], by_depart: dict[date, list[Search]]) -> tuple[date, date, date, date, int]:
    outbound_start = departures[0]
    outbound_end = departures[-1]
    returns = [
        item.return_date
        for depart in departures
        for item in by_depart[depart]
        if item.return_date is not None
    ]
    return_start = min(returns)
    return_end = max(returns)
    combos = inclusive_days(outbound_start, outbound_end) * inclusive_days(return_start, return_end)
    return outbound_start, outbound_end, return_start, return_end, combos


def _return_span(items: list[Search]) -> int:
    returns = [item.return_date for item in items if item.return_date is not None]
    if not returns:
        return 1
    return inclusive_days(min(returns), max(returns))


def _round_chunk(departures: list[date], by_depart: dict[date, list[Search]]) -> CalendarChunk:
    items: list[Search] = []
    for depart in departures:
        items.extend(sorted(by_depart[depart], key=lambda item: (item.return_date or date.min, item.key)))
    return _round_chunk_from_items(items)


def _round_chunk_from_items(items: list[Search]) -> CalendarChunk:
    first = items[0]
    departures = [item.depart for item in items]
    returns = [item.return_date for item in items if item.return_date is not None]
    return CalendarChunk(
        route_id=first.route_id,
        origin=first.origin,
        dest=first.dest,
        outbound_start=min(departures),
        outbound_end=max(departures),
        return_start=min(returns) if returns else None,
        return_end=max(returns) if returns else None,
        searches=tuple(items),
        max_stops=first.max_stops,
    )


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


def _public_error(exc: BaseException, api_key: str) -> str:
    message = str(exc).replace("\n", " ").strip()
    if isinstance(exc, PricePassError) and message:
        text = message
    elif message:
        text = f"{type(exc).__name__}: {message}"
    else:
        text = type(exc).__name__
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return text[:300]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
