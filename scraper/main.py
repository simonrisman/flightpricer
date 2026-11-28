from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from scraper.backoff import jitter_pause
from scraper.config import AppConfig, ConfigError, Route, load_config
from scraper.expand import build_adhoc, expand_routes, interleave_by_origin, select_slice
from scraper.models import Search, SearchResult
from scraper.price_pass import (
    chunk_searches,
    collect_price_pass,
    format_price_plan,
)
from scraper.store import (
    StoreError,
    drop_expired,
    entries_from_latest,
    load_history,
    merge_results,
    read_json,
    update_history,
    write_json,
)
from scraper.summarize import decide_state, summarize_routes


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape configured Google Flights searches into site/data.")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--data-dir", default="site/data", help="directory for latest.json, status.json, and history")
    parser.add_argument("--dry-run", action="store_true", help="print the expanded searches and exit")
    parser.add_argument("--limit", type=int, default=None, help="run at most this many searches")
    parser.add_argument("--route-id", default=None, help="with origin, dest, and depart, scrape one date")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--only-origin", default=None, help="limit a full run to one origin airport")
    parser.add_argument("--only-route", default=None, help="limit a full run to one route id")
    parser.add_argument("--only-dest", default=None, help="limit a full run to one destination airport")
    parser.add_argument("--skip", type=int, default=0, help="skip this many searches already finished in today's slice")
    parser.add_argument("--dest", default=None)
    parser.add_argument("--depart", default=None, help="YYYY-MM-DD")
    parser.add_argument("--return-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD), for tests")
    parser.add_argument(
        "--price-pass",
        action="store_true",
        help=(
            "Sweep cheapest Google Flights date prices with fli (the flights package) for every "
            "expanded date pair, then fetch a full itinerary with fast-flights for the 3 cheapest "
            "dates on each origin-destination pair. Date sweeps are grouped by trip length and "
            "stay within fli's 93-date cap. The scheduled GitHub job uses this path. Without this "
            "flag, the run stays a full fast-flights search per date, which is what an ad-hoc "
            "one-date job uses. Honors --only-route, --only-origin, --only-dest, "
            "--limit, and --skip, and does not use the rotating daily slice."
        ),
    )
    return parser.parse_args(argv)


def _parse_iso(value: str | None, flag: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{flag} must be YYYY-MM-DD") from exc


def _find_route(config: AppConfig, route_id: str) -> Route:
    for route in config.routes:
        if route.id == route_id:
            if not route.active:
                raise ConfigError(f"route {route_id} is inactive")
            return route
    raise ConfigError(f"unknown route {route_id}")


def _keeps_going(error: str | None) -> bool:
    if not error:
        return False
    return error in ("empty result", "unreadable result") or error.startswith("IndexError")


def _failed_result(item: Search, error: str, fetched_at: str) -> SearchResult:
    return SearchResult(
        key=item.key,
        route_id=item.route_id,
        origin=item.origin,
        dest=item.dest,
        depart=item.depart,
        return_date=item.return_date,
        ok=False,
        error=error,
        options=[],
        cheapest=None,
        google_url="",
        fetched_at=fetched_at,
    )


def _searcher(source: str):
    if source == "playwright":
        from scraper.source_google_playwright import search
    else:
        from scraper.source_google import search
    return search


def _print_plan(config: AppConfig, searches: list[Search], chosen: list[Search], offset: int) -> None:
    counts: dict[str, int] = {}
    for item in searches:
        counts[item.route_id] = counts.get(item.route_id, 0) + 1
    print(f"{len(searches)} searches", flush=True)
    for route in config.routes:
        if route.active:
            print(f"  {route.id}: {counts.get(route.id, 0)}", flush=True)
    for item in searches:
        print(item.key, flush=True)
    print(f"scheduled {len(chosen)} offset {offset}", flush=True)


def _write_status(path: Path, status: dict) -> None:
    write_json(path, status)


def _fail_status(data_dir: Path, message: str, previous: dict | None) -> None:
    now = utc_now()
    status = {
        "last_run": now,
        "state": "failed",
        "searches_planned": 0,
        "searches_attempted": 0,
        "searches_succeeded": 0,
        "errors": [message[:500]],
        "last_success": None if previous is None else previous.get("last_success"),
        "coverage": None,
        "stopped_early": False,
    }
    try:
        _write_status(data_dir / "status.json", status)
    except OSError as exc:
        print(f"could not write status.json: {exc}", file=sys.stderr)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config)
    data_dir = Path(args.data_dir)
    try:
        config = load_config(config_path)
        today = _parse_iso(args.today, "--today") or datetime.now(timezone.utc).date()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        if args.dry_run is False and data_dir.exists():
            previous = None
            try:
                previous = read_json(data_dir / "status.json")
            except StoreError:
                previous = None
            _fail_status(data_dir, str(exc), previous)
        return 1

    adhoc_flags = [args.route_id, args.origin, args.dest, args.depart]
    adhoc = any(adhoc_flags)
    if adhoc and not all(adhoc_flags):
        print("An ad-hoc search needs --route-id, --origin, --dest, and --depart.", file=sys.stderr)
        return 1
    if args.price_pass and adhoc:
        print(
            "--price-pass sweeps a date window. An ad-hoc search stays one full itinerary; "
            "drop --price-pass or the ad-hoc flags.",
            file=sys.stderr,
        )
        return 1

    try:
        if adhoc:
            route = _find_route(config, args.route_id)
            depart = _parse_iso(args.depart, "--depart")
            return_date = _parse_iso(args.return_date, "--return-date")
            if depart is None:
                raise ConfigError("--depart must be YYYY-MM-DD")
            chosen = [build_adhoc(route, args.origin, args.dest, depart, return_date, today)]
            searches = chosen
            offset = 0
        else:
            searches = expand_routes(config.routes, today)
            only_route = (args.only_route or "").strip().lower()
            if only_route:
                _find_route(config, only_route)
                searches = [item for item in searches if item.route_id == only_route]
                if not searches:
                    raise ConfigError(f"no searches for route {only_route}")
            only_origin = (args.only_origin or "").strip().upper()
            if only_origin:
                searches = [item for item in searches if item.origin == only_origin]
                if not searches:
                    raise ConfigError(f"no searches for origin {only_origin}")
            only_dest = (args.only_dest or "").strip().upper()
            if only_dest:
                searches = [item for item in searches if item.dest == only_dest]
                if not searches:
                    raise ConfigError(f"no searches for destination {only_dest}")
            if args.price_pass:
                chosen = list(searches)
                offset = 0
            else:
                chosen, offset = select_slice(searches, config.settings.max_requests_per_run, today)
            if args.skip < 0:
                raise ConfigError("--skip must be 0 or more")
            if args.skip:
                chosen = chosen[args.skip :]
            if args.limit is not None:
                if args.limit < 1:
                    raise ConfigError("--limit must be at least 1")
                chosen = chosen[: args.limit]
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        if adhoc and not args.dry_run and data_dir.exists():
            ret = args.return_date or "-"
            key = f"{args.route_id}|{(args.origin or '').upper()}|{(args.dest or '').upper()}|{args.depart}|{ret}"
            previous = None
            try:
                previous = read_json(data_dir / "status.json")
            except StoreError:
                previous = None
            _fail_status(data_dir, f"{key}: {exc}", previous)
        return 1

    if args.dry_run:
        if args.price_pass:
            print(format_price_plan(chosen, chunk_searches(chosen)), flush=True)
        else:
            _print_plan(config, searches, chosen, offset)
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    status_path = data_dir / "status.json"
    latest_path = data_dir / "latest.json"
    try:
        previous_status = read_json(status_path)
        previous_latest = read_json(latest_path)
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    now = utc_now()
    run_date = today
    search_fn = _searcher(config.settings.source)
    results: list[SearchResult] = []
    errors: list[str] = []
    consecutive_failures = 0
    stopped_early = False
    detail_failures = 0
    calendar_calls = 0
    detail_calls = 0
    low, high = config.settings.delay_seconds

    if args.price_pass:
        from scraper import source_google

        def pause() -> None:
            jitter_pause(low, high)

        raw_cooldown = os.environ.get("COOLDOWN_SECONDS", "").strip()
        if raw_cooldown:
            retry_cooldown = float(raw_cooldown)
        else:
            retry_cooldown = float(config.settings.cooldown_minutes) * 60
        outcome = collect_price_pass(
            chosen,
            currency=config.settings.currency,
            cabin=config.settings.cabin,
            adults=config.settings.adults,
            detail=source_google.search,
            pause=pause,
            now=utc_now,
            retry_cooldown=retry_cooldown,
        )
        results = outcome.results
        errors = outcome.errors
        stopped_early = outcome.stopped_early
        detail_failures = outcome.detail_failures
        calendar_calls = outcome.date_calls
        detail_calls = outcome.detail_calls
        run_list = chosen
    else:
        run_list = chosen if adhoc else interleave_by_origin(chosen)
        print(f"running {len(run_list)} of {len(searches)} searches", flush=True)
        for index, item in enumerate(run_list, start=1):
            if index > 1 and not adhoc:
                jitter_pause(low, high)
            fetched_at = utc_now()
            try:
                result = search_fn(
                    item,
                    currency=config.settings.currency,
                    cabin=config.settings.cabin,
                    adults=config.settings.adults,
                    fetched_at=fetched_at,
                )
            except IndexError:
                result = _failed_result(item, "unreadable result", fetched_at)
            except Exception as exc:
                result = _failed_result(item, f"{type(exc).__name__}: {exc}"[:300], fetched_at)
            results.append(result)
            if result.ok and result.cheapest is not None:
                consecutive_failures = 0
                print(
                    f"[{index}/{len(run_list)}] {item.origin}->{item.dest} {item.depart} ${result.cheapest.price} {result.cheapest.airlines}",
                    flush=True,
                )
            elif _keeps_going(result.error):
                label = "no flights" if result.error == "empty result" else "unreadable, skipping"
                message = f"{result.key}: {label}"
                errors.append(message)
                print(f"[{index}/{len(run_list)}] {message}", flush=True)
            else:
                consecutive_failures += 1
                message = f"{result.key}: {result.error}"
                errors.append(message)
                print(f"[{index}/{len(run_list)}] FAIL {message}", flush=True)
                if consecutive_failures >= 5:
                    stopped_early = True
                    print("circuit breaker: 5 consecutive errors, stopping", flush=True)
                    break

    attempted = len(results)
    succeeded = sum(1 for result in results if result.ok and result.cheapest is not None)
    state = decide_state(attempted, succeeded)
    if (stopped_early or detail_failures) and state == "ok":
        state = "degraded"

    try:
        merged = merge_results(entries_from_latest(previous_latest), results, now)
        active_ids = {route.id for route in config.routes if route.active}
        merged = drop_expired(merged, active_ids, today)
        history_points: dict[str, list[dict]] = {}
        history_dir = data_dir / "history"
        for route in config.routes:
            if not route.active:
                continue
            history = load_history(history_dir / f"{route.id}.json", route.id)
            successes = [
                merged[result.key]
                for result in results
                if result.route_id == route.id and result.ok and result.key in merged
            ]
            history = update_history(history, successes, run_date, today)
            write_json(history_dir / f"{route.id}.json", history)
            history_points[route.id] = history["points"]
        routes_payload = summarize_routes(config.routes, merged, history_points, run_date)
        latest = {
            "generated_at": now,
            "currency": config.settings.currency,
            "routes": routes_payload,
        }
        write_json(latest_path, latest)
        last_success = now if succeeded else (None if previous_status is None else previous_status.get("last_success"))
        status = {
            "last_run": now,
            "state": state,
            "searches_planned": len(run_list),
            "searches_attempted": attempted,
            "searches_succeeded": succeeded,
            "errors": errors[:10],
            "last_success": last_success,
            "coverage": {
                "total": len(searches),
                "scheduled": len(run_list),
                "offset": offset,
                "origin": (args.only_origin or "").strip().upper() or None,
                **(
                    {
                        "mode": "price-pass",
                        "date_calls": calendar_calls,
                        "calendar_calls": calendar_calls,
                        "detail_calls": detail_calls,
                    }
                    if args.price_pass
                    else {}
                ),
            },
            "stopped_early": stopped_early,
        }
        _write_status(status_path, status)
    except (StoreError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        _fail_status(data_dir, str(exc), previous_status)
        return 1

    print(f"state={state} succeeded={succeeded} attempted={attempted}", flush=True)
    return 0 if state != "failed" else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
