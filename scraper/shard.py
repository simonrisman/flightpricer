"""Per-origin scrape shards and the single merge that publishes them.

An origin job writes ``shard.json`` and does not touch git. The publish job
loads every shard it can find. An origin with no shard keeps the prices,
history, and queue timestamps already on main.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from scraper.config import AppConfig, load_config
from scraper.queue import init_queue, leg_id
from scraper.store import (
    drop_expired,
    entries_from_latest,
    load_history,
    read_json,
    update_history,
    write_json,
)
from scraper.summarize import decide_state, summarize_routes


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def snapshot_history(data_dir: Path, config: AppConfig) -> dict[str, list[dict]]:
    saved: dict[str, list[dict]] = {}
    history_dir = data_dir / "history"
    for route in config.routes:
        if not route.active:
            continue
        history = load_history(history_dir / f"{route.id}.json", route.id)
        saved[route.id] = [dict(point) for point in history.get("points") or []]
    return saved


def history_additions(before: dict[str, list[dict]], data_dir: Path, config: AppConfig) -> dict[str, list[dict]]:
    additions: dict[str, list[dict]] = {}
    history_dir = data_dir / "history"
    for route in config.routes:
        if not route.active:
            continue
        history = load_history(history_dir / f"{route.id}.json", route.id)
        seen = {_point_identity(point) for point in before.get(route.id) or []}
        fresh = [dict(point) for point in history.get("points") or [] if _point_identity(point) not in seen]
        if fresh:
            additions[route.id] = fresh
    return additions


def write_shard(
    path: Path,
    *,
    origin: str,
    data_dir: Path,
    legs: list[dict],
    finished: bool,
    history_adds: dict[str, list[dict]],
) -> None:
    latest = read_json(data_dir / "latest.json")
    if latest is None:
        entries = None
    else:
        entries = [
            dict(entry)
            for entry in entries_from_latest(latest).values()
            if str(entry.get("origin") or "").upper() == origin.upper()
        ]
    payload = {
        "origin": origin.upper(),
        "finished": finished,
        "legs": legs,
        "entries": entries,
        "history_additions": history_adds,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_shards(root: Path) -> dict[str, dict]:
    found: dict[str, dict] = {}
    if not root.exists():
        return found
    for path in sorted(root.rglob("shard.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        origin = str(data.get("origin") or "").upper()
        if not origin:
            continue
        found[origin] = data
    return found


def merge_shards(
    shard_root: Path,
    data_dir: Path,
    *,
    config: AppConfig,
    run_id: str,
    run_url: str,
    now: str | None = None,
    today: date | None = None,
    scope: list[str] | None = None,
) -> dict:
    """Fold this run's origin shards into ``data_dir``.

    ``scope`` is the origins this run is responsible for. Every other origin
    keeps its prices, history, and queue state. An origin in scope with no
    usable shard is marked failed and its saved prices stay.
    """
    stamp = now or utc_now()
    run_day = today or datetime.now(timezone.utc).date()
    shards = load_shards(shard_root)
    if scope is None:
        scope = [origin for origin in _expected_origins(config) if origin in shards]
    else:
        scope = [origin.strip().upper() for origin in scope if origin.strip()]
    present = [origin for origin in scope if origin in shards and isinstance(shards[origin].get("entries"), list)]
    missing = [origin for origin in scope if origin not in present]

    latest = read_json(data_dir / "latest.json")
    previous_status = read_json(data_dir / "status.json")
    entries = entries_from_latest(latest)
    for origin in present:
        entries = _replace_origin(entries, origin, shards[origin].get("entries") or [])
    active_ids = {route.id for route in config.routes if route.active}
    entries = drop_expired(entries, active_ids, run_day)

    history_points: dict[str, list[dict]] = {}
    history_dir = data_dir / "history"
    for route in config.routes:
        if not route.active:
            continue
        history = load_history(history_dir / f"{route.id}.json", route.id)
        additions: list[dict] = []
        for origin in present:
            additions.extend((shards[origin].get("history_additions") or {}).get(route.id) or [])
        history = update_history(history, additions, run_day, run_day)
        write_json(history_dir / f"{route.id}.json", history)
        history_points[route.id] = history["points"]

    routes_payload = summarize_routes(config.routes, entries, history_points, run_day)
    write_json(
        data_dir / "latest.json",
        {"generated_at": stamp, "currency": config.settings.currency, "routes": routes_payload},
    )

    queue = _merge_queue(
        data_dir / "queue.json",
        config,
        shards,
        scope,
        present,
        missing,
        stamp,
        run_id,
        run_url,
    )
    status = _merge_status(previous_status, shards, scope, present, missing, stamp)
    write_json(data_dir / "status.json", status)
    return {"present": present, "missing": missing, "status": status, "queue": queue}


def _expected_origins(config: AppConfig) -> list[str]:
    found: list[str] = []
    for route in config.routes:
        if not route.active:
            continue
        for origin in route.origins:
            if origin not in found:
                found.append(origin)
    return found


def _replace_origin(entries: dict[str, dict], origin: str, shard_entries: list) -> dict[str, dict]:
    kept = {key: value for key, value in entries.items() if str(value.get("origin") or "").upper() != origin}
    for entry in shard_entries:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        if str(entry.get("origin") or "").upper() != origin:
            continue
        kept[str(entry["key"])] = dict(entry)
    return kept


def _route_dicts(config: AppConfig) -> list[dict]:
    return [
        {
            "id": route.id,
            "label": route.label,
            "origins": list(route.origins),
            "destinations": list(route.destinations),
            "active": route.active,
        }
        for route in config.routes
    ]


def _merge_queue(
    path: Path,
    config: AppConfig,
    shards: dict[str, dict],
    scope: list[str],
    present: list[str],
    missing: list[str],
    now: str,
    run_id: str,
    run_url: str,
) -> dict:
    previous = None
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
    queue = init_queue(_route_dicts(config), previous if isinstance(previous, dict) else None, now, run_id, run_url)
    _restore_other_origins(queue, previous if isinstance(previous, dict) else None, set(scope))
    for origin in present:
        shard = shards[origin]
        done = {
            (str(leg.get("route")), str(leg.get("dest")).upper()): leg
            for leg in shard.get("legs") or []
            if isinstance(leg, dict)
        }
        for leg in queue["legs"]:
            if leg["origin"] != origin:
                continue
            record = done.get((leg["route"], leg["dest"]))
            if record is not None:
                leg["state"] = record.get("state") or "failed"
                leg["started_at"] = record.get("started_at")
                leg["finished_at"] = record.get("finished_at") or now
                continue
            if not shard.get("finished", False):
                leg["state"] = "failed"
                leg["finished_at"] = now
    for origin in missing:
        for leg in queue["legs"]:
            if leg["origin"] != origin:
                continue
            leg["state"] = "failed"
            leg["finished_at"] = now
    queue["updated_at"] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return queue


def _restore_other_origins(queue: dict, previous: dict | None, scope: set[str]) -> None:
    prior = {}
    for leg in (previous or {}).get("legs") or []:
        if not isinstance(leg, dict):
            continue
        prior[leg_id(str(leg.get("route") or ""), str(leg.get("origin") or ""), str(leg.get("dest") or ""))] = leg
    for leg in queue.get("legs") or []:
        if leg["origin"] in scope:
            continue
        old = prior.get(leg_id(leg["route"], leg["origin"], leg["dest"]))
        if old is None:
            continue
        leg["state"] = old.get("state") or "pending"
        leg["started_at"] = old.get("started_at")
        leg["finished_at"] = old.get("finished_at")


def _leg_numbers(shard: dict) -> dict:
    planned = attempted = succeeded = date_calls = detail_calls = 0
    errors: list[str] = []
    stopped = False
    for leg in shard.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        planned += int(leg.get("searches_planned") or 0)
        attempted += int(leg.get("searches_attempted") or 0)
        succeeded += int(leg.get("searches_succeeded") or 0)
        date_calls += int(leg.get("date_calls") or 0)
        detail_calls += int(leg.get("detail_calls") or 0)
        stopped = stopped or bool(leg.get("stopped_early"))
        for error in leg.get("errors") or []:
            if isinstance(error, str) and error:
                errors.append(error)
    return {
        "planned": planned,
        "attempted": attempted,
        "succeeded": succeeded,
        "date_calls": date_calls,
        "detail_calls": detail_calls,
        "stopped_early": stopped,
        "errors": errors,
    }


def _merge_status(
    previous: dict | None,
    shards: dict[str, dict],
    scope: list[str],
    present: list[str],
    missing: list[str],
    now: str,
) -> dict:
    totals = {"planned": 0, "attempted": 0, "succeeded": 0, "date_calls": 0, "detail_calls": 0}
    errors: list[str] = []
    stopped = False
    for origin in present:
        numbers = _leg_numbers(shards[origin])
        for key in totals:
            totals[key] += numbers[key]
        stopped = stopped or numbers["stopped_early"]
        errors.extend(numbers["errors"])
    for origin in missing:
        errors.append(f"{origin}: origin job did not finish; saved prices were left unchanged")
    if present:
        state = decide_state(totals["attempted"], totals["succeeded"])
    else:
        state = "failed"
    if (missing or stopped) and state == "ok":
        state = "degraded"
    succeeded = totals["succeeded"]
    last_success = now if succeeded else (None if previous is None else previous.get("last_success"))
    return {
        "last_run": now,
        "state": state,
        "searches_planned": totals["planned"],
        "searches_attempted": totals["attempted"],
        "searches_succeeded": succeeded,
        "errors": errors[:10],
        "last_success": last_success,
        "coverage": {
            "total": totals["planned"],
            "scheduled": totals["planned"],
            "offset": 0,
            "origin": scope[0] if len(scope) == 1 else None,
            "mode": "price-pass",
            "date_calls": totals["date_calls"],
            "calendar_calls": totals["date_calls"],
            "detail_calls": totals["detail_calls"],
            "origins": {origin: ("merged" if origin in present else "preserved") for origin in scope},
        },
        "stopped_early": stopped,
    }


def _point_identity(point: dict) -> tuple:
    return (point.get("date"), point.get("key"), point.get("price"), point.get("depart"))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Merge per-origin scrape shards into site/data.")
    parser.add_argument("command", choices=["merge"])
    parser.add_argument("--shards", required=True)
    parser.add_argument("--data-dir", default="site/data")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--today", default=None)
    parser.add_argument("--origin", action="append", default=[], help="origin this run should merge; repeatable")
    args = parser.parse_args(argv)
    config = load_config(Path(args.config))
    today = date.fromisoformat(args.today) if args.today else None
    report = merge_shards(
        Path(args.shards),
        Path(args.data_dir),
        config=config,
        run_id=args.run_id,
        run_url=args.run_url,
        today=today,
        scope=args.origin or None,
    )
    present = ", ".join(report["present"]) or "none"
    missing = ", ".join(report["missing"]) or "none"
    print(f"merged {present}; preserved {missing}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
