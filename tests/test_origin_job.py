from __future__ import annotations

import json
from pathlib import Path

from scraper.config import load_config
from scraper.origin_job import execute, origin_legs


ROOT = Path(__file__).parents[1]


def test_real_config_gives_each_origin_its_own_legs():
    config = load_config(ROOT / "config.yaml")
    seen = set()
    for origin in ("JFK", "EWR", "BOS"):
        legs = origin_legs(config, origin)
        assert len(legs) == 9
        assert {leg["origin"] for leg in legs} == {origin}
        assert {leg["route"] for leg in legs} == {route.id for route in config.routes if route.active}
        seen.update(leg["route"] for leg in legs)
    assert seen.isdisjoint({"lim", "bkk", "han", "tyo", "sgn"})


def test_origin_job_walks_legs_in_order_and_writes_a_shard(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
settings:
  currency: USD
  delay_seconds: [0, 0]
routes:
  - id: bog
    label: Bogota
    origins: [JFK, EWR]
    destinations: [BOG]
    trip: round_trip
    depart_window: { start: 2027-03-01, end: 2027-03-02 }
    trip_length_days: [7]
    active: true
  - id: mde
    label: Medellin
    origins: [JFK]
    destinations: [MDE]
    trip: round_trip
    depart_window: { start: 2027-03-01, end: 2027-03-02 }
    trip_length_days: [7]
    active: true
""",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    calls: list[list[str]] = []

    def fake_run(argv):
        calls.append(argv)
        route = argv[argv.index("--only-route") + 1]
        dest = argv[argv.index("--only-dest") + 1]
        current = json.loads((data / "latest.json").read_text(encoding="utf-8")) if (data / "latest.json").exists() else {"routes": []}
        routes = [item for item in current.get("routes") or [] if item.get("id") != route]
        routes.append(
            {
                "id": route,
                "searches": [
                    {
                        "key": f"{route}|JFK|{dest}|2027-03-01|2027-03-08",
                        "route_id": route,
                        "origin": "JFK",
                        "dest": dest,
                        "depart": "2027-03-01",
                        "return": "2027-03-08",
                        "price": 100,
                        "fetched_at": "2026-11-27T12:00:00Z",
                        "stale": False,
                    }
                ],
            }
        )
        (data / "latest.json").write_text(
            json.dumps({"generated_at": "2026-11-27T12:00:00Z", "currency": "USD", "routes": routes}),
            encoding="utf-8",
        )
        (data / "status.json").write_text(
            json.dumps(
                {
                    "searches_planned": 2,
                    "searches_attempted": 2,
                    "searches_succeeded": 2,
                    "errors": [],
                    "stopped_early": False,
                    "coverage": {"date_calls": 1, "detail_calls": 1},
                }
            ),
            encoding="utf-8",
        )
        history = data / "history"
        history.mkdir(exist_ok=True)
        (history / f"{route}.json").write_text(
            json.dumps(
                {
                    "route_id": route,
                    "points": [
                        {
                            "date": "2026-11-27",
                            "key": f"{route}|JFK|{dest}|2027-03-01|2027-03-08",
                            "price": 100,
                            "depart": "2027-03-01",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr("scraper.origin_job.run", fake_run)
    code = execute("jfk", config_path=config, data_dir=data, shard_dir=tmp_path / "shard", skip=0, pause=lambda *_args: None)
    shard = json.loads((tmp_path / "shard" / "shard.json").read_text(encoding="utf-8"))
    assert code == 0
    assert [argv[argv.index("--only-route") + 1] for argv in calls] == ["bog", "mde"]
    assert all("--only-origin" in argv and argv[argv.index("--only-origin") + 1] == "JFK" for argv in calls)
    assert shard["origin"] == "JFK"
    assert shard["finished"] is True
    assert [leg["dest"] for leg in shard["legs"]] == ["BOG", "MDE"]
    assert {entry["dest"] for entry in shard["entries"]} == {"BOG", "MDE"}
    assert "bog" in shard["history_additions"]
    assert "mde" in shard["history_additions"]
