"""Scrape every leg for one origin airport and write a shard. Does not push."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scraper.backoff import jitter_pause
from scraper.config import AppConfig, load_config
from scraper.main import run
from scraper.shard import history_additions, snapshot_history, utc_now, write_shard
from scraper.store import StoreError, read_json


def origin_legs(config: AppConfig, origin: str) -> list[dict]:
    code = origin.upper()
    legs: list[dict] = []
    for route in config.routes:
        if not route.active or code not in route.origins:
            continue
        for dest in route.destinations:
            legs.append({"route": route.id, "label": route.label, "origin": code, "dest": dest})
    return legs


def execute(
    origin: str,
    *,
    config_path: Path,
    data_dir: Path,
    shard_dir: Path,
    skip: int = 0,
    only_route: str = "",
    only_dest: str = "",
    pause=jitter_pause,
) -> int:
    config = load_config(config_path)
    code = origin.strip().upper()
    data_dir.mkdir(parents=True, exist_ok=True)
    before = snapshot_history(data_dir, config)
    legs = origin_legs(config, code)
    route_id = only_route.strip().lower()
    dest_code = only_dest.strip().upper()
    if route_id:
        legs = [leg for leg in legs if leg["route"] == route_id]
    if dest_code:
        legs = [leg for leg in legs if leg["dest"] == dest_code]
    if (route_id or dest_code) and not legs:
        print(f"no leg for {code} route={route_id or '*'} dest={dest_code or '*'}", file=sys.stderr)
        return 1
    records: list[dict] = []
    low, high = config.settings.delay_seconds
    worst = 0
    shard_path = shard_dir / "shard.json"
    for index, leg in enumerate(legs):
        if index:
            pause(low, high)
        started = utc_now()
        argv = [
            "--price-pass",
            "--config",
            str(config_path),
            "--data-dir",
            str(data_dir),
            "--only-route",
            leg["route"],
            "--only-origin",
            code,
            "--only-dest",
            leg["dest"],
        ]
        if skip:
            argv.extend(["--skip", str(skip)])
        status_path = data_dir / "status.json"
        status_path.unlink(missing_ok=True)
        exit_code = run(argv)
        finished = utc_now()
        status: dict = {}
        if status_path.exists():
            try:
                status = read_json(status_path) or {}
            except StoreError:
                status = {}
        coverage = status.get("coverage") or {}
        records.append(
            {
                "route": leg["route"],
                "origin": code,
                "dest": leg["dest"],
                "state": "done" if exit_code == 0 else "failed",
                "started_at": started,
                "finished_at": finished,
                "searches_planned": int(status.get("searches_planned") or 0),
                "searches_attempted": int(status.get("searches_attempted") or 0),
                "searches_succeeded": int(status.get("searches_succeeded") or 0),
                "errors": [error for error in (status.get("errors") or []) if isinstance(error, str)][:10],
                "stopped_early": bool(status.get("stopped_early")),
                "date_calls": int(coverage.get("date_calls") or coverage.get("calendar_calls") or 0),
                "detail_calls": int(coverage.get("detail_calls") or 0),
            }
        )
        worst = max(worst, exit_code)
        write_shard(
            shard_path,
            origin=code,
            data_dir=data_dir,
            legs=records,
            finished=index == len(legs) - 1,
            history_adds=history_additions(before, data_dir, config),
        )
        print(
            f"[origin {code}] {leg['route']} {leg['dest']} {records[-1]['state']}",
            flush=True,
        )
    if not legs:
        write_shard(
            shard_path,
            origin=code,
            data_dir=data_dir,
            legs=[],
            finished=True,
            history_adds={},
        )
    return 0 if worst == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape one origin airport into a shard.")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data-dir", default="site/data")
    parser.add_argument("--shard-dir", required=True)
    parser.add_argument("--route", default="", help="Limit the job to one route id")
    parser.add_argument("--dest", default="", help="Limit the job to one destination")
    parser.add_argument("--skip", type=int, default=0)
    args = parser.parse_args(argv)
    if args.skip < 0:
        print("--skip must be 0 or more", file=sys.stderr)
        return 1
    return execute(
        args.origin,
        config_path=Path(args.config),
        data_dir=Path(args.data_dir),
        shard_dir=Path(args.shard_dir),
        skip=args.skip,
        only_route=args.route,
        only_dest=args.dest,
    )


if __name__ == "__main__":
    raise SystemExit(main())
