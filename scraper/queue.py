"""The site's scrape queue. Each airport-to-city pair is one leg."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def leg_id(route: str, origin: str, dest: str) -> str:
    return f"{route}|{origin.upper()}|{dest.upper()}"


def build_legs(routes: list[dict]) -> list[dict]:
    legs: list[dict] = []
    for route in routes:
        if not route.get("active", True):
            continue
        for origin in route.get("origins") or []:
            for dest in route.get("destinations") or []:
                legs.append(
                    {
                        "route": route["id"],
                        "label": route.get("label") or route["id"],
                        "origin": origin,
                        "dest": dest,
                        "state": "pending",
                        "started_at": None,
                        "finished_at": None,
                    }
                )
    return legs


def stamp_from_latest(queue: dict, latest: dict | None) -> dict:
    newest: dict[str, str] = {}
    for route in (latest or {}).get("routes") or []:
        for search in route.get("searches") or []:
            fetched = search.get("fetched_at")
            origin = search.get("origin")
            dest = search.get("dest")
            if not fetched or not origin or not dest:
                continue
            key = leg_id(route["id"], origin, dest)
            if key not in newest or fetched > newest[key]:
                newest[key] = fetched
    for leg in queue.get("legs") or []:
        if leg.get("finished_at"):
            continue
        stamped = newest.get(leg_id(leg["route"], leg["origin"], leg["dest"]))
        if stamped:
            leg["finished_at"] = stamped
    return queue


def init_queue(routes: list[dict], previous: dict | None, now: str, run_id: str, run_url: str) -> dict:
    prior = {}
    for leg in (previous or {}).get("legs") or []:
        prior[leg_id(leg["route"], leg["origin"], leg["dest"])] = leg
    legs = build_legs(routes)
    for leg in legs:
        old = prior.get(leg_id(leg["route"], leg["origin"], leg["dest"]))
        if old and old.get("finished_at"):
            leg["finished_at"] = old["finished_at"]
    return {"updated_at": now, "run_id": run_id or None, "run_url": run_url or None, "legs": legs}


def mark_leg(queue: dict, route: str, origin: str, dest: str, state: str, now: str) -> dict:
    target = leg_id(route, origin, dest)
    found = False
    for leg in queue.get("legs") or []:
        if leg_id(leg["route"], leg["origin"], leg["dest"]) != target:
            continue
        found = True
        leg["state"] = state
        if state == "running":
            leg["started_at"] = now
        if state in ("done", "failed"):
            leg["finished_at"] = now
        break
    if not found:
        raise ValueError(f"queue has no leg {target}")
    queue["updated_at"] = now
    return queue


def read_queue(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_queue(path: Path, queue: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update the site scrape queue.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--config", default="config.yaml")
    init.add_argument("--queue", default="site/data/queue.json")
    init.add_argument("--run-id", default="")
    init.add_argument("--run-url", default="")
    init.add_argument("--latest", default="")

    mark = sub.add_parser("mark")
    mark.add_argument("--queue", default="site/data/queue.json")
    mark.add_argument("--route", required=True)
    mark.add_argument("--origin", required=True)
    mark.add_argument("--dest", required=True)
    mark.add_argument("--state", required=True, choices=["running", "done", "failed"])

    args = parser.parse_args(argv)
    path = Path(args.queue)
    now = utc_now()
    if args.command == "init":
        config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
        queue = init_queue(config.get("routes") or [], read_queue(path), now, args.run_id, args.run_url)
        if args.latest:
            latest = json.loads(Path(args.latest).read_text(encoding="utf-8"))
            stamp_from_latest(queue, latest)
    else:
        current = read_queue(path)
        if current is None:
            raise SystemExit(f"{path} does not exist")
        queue = mark_leg(current, args.route, args.origin, args.dest, args.state, now)
    write_queue(path, queue)
    print(f"{args.command} {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
