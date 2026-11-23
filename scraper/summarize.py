from __future__ import annotations

from datetime import date

from scraper.config import Route


def decide_state(attempted: int, succeeded: int) -> str:
    if attempted == 0 or succeeded == attempted:
        return "ok"
    if succeeded == 0:
        return "failed"
    return "degraded"


def _points_for_key(points: list[dict], key: str) -> list[dict]:
    return [point for point in points if point.get("key") == key and isinstance(point.get("price"), int)]


def _with_today(entry: dict, points: list[dict], run_date: date) -> list[dict]:
    if entry.get("stale") or not isinstance(entry.get("price"), int):
        return points
    run_day = run_date.isoformat()
    if any(point.get("key") == entry["key"] and point.get("date") == run_day for point in points):
        return points
    return points + [
        {"date": run_day, "key": entry["key"], "price": entry["price"], "depart": entry.get("depart")}
    ]


def summarize_entry(entry: dict, points: list[dict], run_date: date, target_price: int | None) -> dict:
    summarized = dict(entry)
    points = _with_today(entry, points, run_date)
    earlier = [
        point
        for point in _points_for_key(points, entry["key"])
        if point.get("date") and point["date"] < run_date.isoformat()
    ]
    earlier.sort(key=lambda point: point["date"])
    if earlier:
        prev = earlier[-1]["price"]
        summarized["prev_price"] = prev
        summarized["delta"] = entry["price"] - prev
    else:
        summarized["prev_price"] = None
        summarized["delta"] = None

    history = _points_for_key(points, entry["key"])
    best = None
    for point in history:
        if best is None or point["price"] < best["price"] or (
            point["price"] == best["price"] and point["date"] < best["date"]
        ):
            best = point
    if best is None and isinstance(entry.get("price"), int):
        summarized["best_ever"] = entry["price"]
        fetched = str(entry.get("fetched_at") or "")
        summarized["best_ever_date"] = fetched[:10] or None
    elif best is not None:
        summarized["best_ever"] = best["price"]
        summarized["best_ever_date"] = best["date"]
    else:
        summarized["best_ever"] = None
        summarized["best_ever_date"] = None

    price = entry.get("price")
    summarized["hit_target"] = bool(target_price is not None and isinstance(price, int) and price <= target_price)
    return summarized


def summarize_routes(
    routes: list[Route],
    entries: dict[str, dict],
    history_by_route: dict[str, list[dict]],
    run_date: date,
) -> list[dict]:
    grouped: dict[str, list[dict]] = {route.id: [] for route in routes if route.active}
    for entry in entries.values():
        route_id = entry.get("route_id")
        if route_id in grouped:
            grouped[route_id].append(entry)

    payload: list[dict] = []
    for route in routes:
        if not route.active:
            continue
        points = history_by_route.get(route.id, [])
        searches = [
            summarize_entry(entry, points, run_date, route.target_price)
            for entry in grouped.get(route.id, [])
            if isinstance(entry.get("price"), int)
        ]
        searches.sort(key=lambda item: (item["depart"], item.get("return") or "", item["origin"], item["dest"]))
        priced = [item for item in searches if isinstance(item.get("price"), int)]
        cheapest = None
        if priced:
            winner = min(priced, key=lambda item: (item["price"], item["depart"], item["origin"]))
            cheapest = {"key": winner["key"], "price": winner["price"]}
        successes = [item["fetched_at"] for item in searches if item.get("fetched_at")]
        successes.sort()
        payload.append(
            {
                "id": route.id,
                "label": route.label,
                "target_price": route.target_price,
                "origins": list(route.origins),
                "destinations": list(route.destinations),
                "trip": route.trip,
                "depart_window": {
                    "start": route.depart_window.start.isoformat(),
                    "end": route.depart_window.end.isoformat(),
                },
                "depart_days": list(route.depart_days) if route.depart_days else None,
                "trip_length_days": list(route.trip_length_days) if route.trip_length_days else None,
                "last_success": successes[-1] if successes else None,
                "target_hits": sum(1 for item in searches if item["hit_target"]),
                "searches": searches,
                "cheapest": cheapest,
            }
        )
    return payload
