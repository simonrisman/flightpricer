"""Pick the origin airports for a scheduled or manual scrape.

Times are UTC crons locked to Eastern Standard Time. In daylight time the
same clock is one hour later on the Eastern clock: BOS 9:00am, JFK 3:00pm,
EWR 11:00pm.
"""

from __future__ import annotations

import json
import os
import sys

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
    args = parser.parse_args(argv)
    try:
        origins = select_origins(args.event, args.schedule, args.only_origin)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(origins))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
