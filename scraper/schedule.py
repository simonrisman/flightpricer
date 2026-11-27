"""Pick the origin airports for a scheduled or manual scrape.

Times are UTC crons locked to Eastern Standard Time. In daylight time the
same clock is one hour later on the Eastern clock: BOS 9:00am, JFK 3:00pm,
EWR 11:00pm.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from scraper.config import AppConfig

# minute, hour (UTC) -> airport. EST: 08:00, 14:00, and 22:00.
SCHEDULED = {
    (0, 13): "BOS",
    (0, 19): "JFK",
    (0, 3): "EWR",
}
ORDER = ("BOS", "JFK", "EWR")


def select_origins(event_name: str, schedule: str = "", only_origin: str = "") -> list[str]:
    if event_name == "schedule":
        return [_origin_for_schedule(schedule)]
    choice = (only_origin or "all").strip().upper()
    if choice in ("", "ALL"):
        return list(ORDER)
    if choice not in ORDER:
        raise ValueError("origin must be BOS, JFK, EWR, or all")
    return [choice]


def select_pairs(config: AppConfig, origins: list[str], only_route: str = "") -> dict[str, list[dict]]:
    """Airport pairs each selected origin should scrape, in route order.

    ``only_route`` keeps a single route id. An unknown id is an error so a
    manual run does not silently do nothing.
    """
    from scraper.origin_job import origin_legs

    wanted = only_route.strip().lower()
    grouped = {code: [] for code in ORDER}
    for origin in origins:
        for leg in origin_legs(config, origin):
            if wanted and leg["route"] != wanted:
                continue
            grouped[origin].append({"route": leg["route"], "origin": origin, "dest": leg["dest"]})
    if wanted and not any(grouped[origin] for origin in origins):
        raise ValueError(f"no pairs for route {wanted}")
    return grouped


def _origin_for_schedule(schedule: str) -> str:
    parts = schedule.split()
    if len(parts) < 2:
        raise ValueError(f"no origin mapped for schedule {schedule!r}")
    try:
        clock = (int(parts[0]), int(parts[1]))
    except ValueError as exc:
        raise ValueError(f"no origin mapped for schedule {schedule!r}") from exc
    try:
        return SCHEDULED[clock]
    except KeyError as exc:
        raise ValueError(f"no origin mapped for schedule {schedule!r}") from exc


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Print the origins a scrape run should cover.")
    parser.add_argument("--event", default=os.environ.get("EVENT_NAME", ""))
    parser.add_argument("--schedule", default=os.environ.get("SCHEDULE", ""))
    parser.add_argument("--only-origin", default=os.environ.get("ONLY_ORIGIN", ""))
    parser.add_argument("--only-route", default=os.environ.get("ONLY_ROUTE", ""))
    parser.add_argument("--config", default=os.environ.get("CONFIG_PATH", "config.yaml"))
    parser.add_argument("--pairs", action="store_true", help="print {origin: [pairs]} instead of the origin list")
    args = parser.parse_args(argv)
    try:
        origins = select_origins(args.event, args.schedule, args.only_origin)
        if not args.pairs:
            print(json.dumps(origins))
            return 0
        from scraper.config import load_config

        pairs = select_pairs(load_config(Path(args.config)), origins, args.only_route)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({origin: pairs[origin] for origin in origins}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
