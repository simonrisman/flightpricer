from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from scraper.models import SearchResult


class StoreError(Exception):
    pass


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StoreError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise StoreError(f"{path} must contain a JSON object")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def entries_from_latest(latest: dict | None) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    if not latest:
        return entries
    for route in latest.get("routes") or []:
        if not isinstance(route, dict):
            continue
        for search in route.get("searches") or []:
            if isinstance(search, dict) and search.get("key"):
                entries[search["key"]] = dict(search)
    return entries


def apply_result(result: SearchResult, previous: dict | None, now: str) -> dict | None:
    if result.ok and result.cheapest is not None:
        return {
            "key": result.key,
            "route_id": result.route_id,
            "origin": result.origin,
            "dest": result.dest,
            "depart": result.depart.isoformat(),
            "return": result.return_date.isoformat() if result.return_date else None,
            "price": result.cheapest.price,
            "airline": result.cheapest.airlines,
            "stops": result.cheapest.stops,
            "duration": result.cheapest.duration,
            "depart_time": result.cheapest.depart_time,
            "arrive_time": result.cheapest.arrive_time,
            "google_url": result.google_url,
            "fetched_at": result.fetched_at or now,
            "stale": False,
            "error": None,
        }
    if previous and isinstance(previous.get("price"), int):
        kept = dict(previous)
        kept["stale"] = True
        kept["last_attempt_at"] = now
        kept["error"] = result.error
        return kept
    return None


def merge_results(previous: dict[str, dict], results: list[SearchResult], now: str) -> dict[str, dict]:
    merged = {key: dict(value) for key, value in previous.items()}
    for result in results:
        updated = apply_result(result, merged.get(result.key), now)
        if updated is None:
            merged.pop(result.key, None)
        else:
            merged[result.key] = updated
    return merged


def drop_expired(entries: dict[str, dict], active_route_ids: set[str], today: date) -> dict[str, dict]:
    kept: dict[str, dict] = {}
    for key, entry in entries.items():
        route_id = entry.get("route_id")
        if route_id not in active_route_ids:
            continue
        try:
            depart = date.fromisoformat(entry["depart"])
        except (KeyError, TypeError, ValueError):
            continue
        if depart < today:
            continue
        kept[key] = entry
    return kept


def depart_from_key(key: str) -> date | None:
    parts = key.split("|")
    if len(parts) < 5:
        return None
    try:
        return date.fromisoformat(parts[3])
    except ValueError:
        return None


def update_history(history: dict, successes: list[dict], run_date: date, today: date) -> dict:
    points = [dict(point) for point in history.get("points") or [] if isinstance(point, dict)]
    route_id = history.get("route_id")
    for item in successes:
        points = [
            point
            for point in points
            if not (point.get("key") == item["key"] and point.get("date") == run_date.isoformat())
        ]
        points.append(
            {
                "date": run_date.isoformat(),
                "key": item["key"],
                "price": item["price"],
                "depart": item["depart"],
            }
        )
    cutoff = today - timedelta(days=30)
    pruned: list[dict] = []
    for point in points:
        depart_text = point.get("depart")
        try:
            depart = date.fromisoformat(depart_text) if depart_text else depart_from_key(str(point.get("key", "")))
        except ValueError:
            depart = depart_from_key(str(point.get("key", "")))
        if depart is not None and depart < cutoff:
            continue
        pruned.append(point)
    pruned.sort(key=lambda point: (point.get("date", ""), point.get("key", "")))
    return {"route_id": route_id, "points": pruned}


def load_history(path: Path, route_id: str) -> dict:
    data = read_json(path)
    if data is None:
        return {"route_id": route_id, "points": []}
    if not isinstance(data.get("points"), list):
        raise StoreError(f"{path} is missing a points list")
    data["route_id"] = route_id
    return data
