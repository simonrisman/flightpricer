from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scraper.config import load_config
from scraper.shard import merge_shards


def _config(path: Path) -> None:
    path.write_text(
        """
settings:
  currency: USD
  delay_seconds: [0, 0]
routes:
  - id: bog
    label: Bogota
    origins: [JFK, EWR, BOS]
    destinations: [BOG]
    trip: round_trip
    depart_window: { start: 2027-03-01, end: 2027-03-21 }
    trip_length_days: [7]
    active: true
""",
        encoding="utf-8",
    )


def _entry(origin: str, price: int) -> dict:
    return {
        "key": f"bog|{origin}|BOG|2027-03-01|2027-03-08",
        "route_id": "bog",
        "origin": origin,
        "dest": "BOG",
        "depart": "2027-03-01",
        "return": "2027-03-08",
        "price": price,
        "airline": "Old",
        "stops": 1,
        "fetched_at": "2026-11-20T00:00:00Z",
        "stale": False,
    }


def _shard(origin: str, price: int, *, finished: bool = True, legs: list | None = None, history: bool = True) -> dict:
    if legs is None:
        legs = [
            {
                "route": "bog",
                "origin": origin,
                "dest": "BOG",
                "state": "done",
                "started_at": "2026-11-27T12:00:00Z",
                "finished_at": "2026-11-27T12:10:00Z",
                "searches_planned": 1,
                "searches_attempted": 1,
                "searches_succeeded": 1,
                "errors": [],
                "stopped_early": False,
                "date_calls": 1,
                "detail_calls": 1,
            }
        ]
    return {
        "origin": origin,
        "finished": finished,
        "legs": legs,
        "entries": [_entry(origin, price)],
        "history_additions": {
            "bog": [
                {
                    "date": "2026-11-27",
                    "key": f"bog|{origin}|BOG|2027-03-01|2027-03-08",
                    "price": price,
                    "depart": "2027-03-01",
                }
            ]
        }
        if history
        else {},
    }


def test_merge_keeps_an_origin_whose_shard_is_missing(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    _config(config_path)
    data = tmp_path / "data"
    (data / "history").mkdir(parents=True)
    latest = {
        "generated_at": "2026-11-20T00:00:00Z",
        "currency": "USD",
        "routes": [
            {
                "id": "bog",
                "searches": [_entry("JFK", 400), _entry("EWR", 410), _entry("BOS", 420)],
            }
        ],
    }
    (data / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    (data / "status.json").write_text(json.dumps({"last_success": "2026-11-20T00:00:00Z"}), encoding="utf-8")
    (data / "queue.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-11-20T00:00:00Z",
                "run_id": "old",
                "run_url": None,
                "legs": [
                    {
                        "route": "bog",
                        "label": "Bogota",
                        "origin": origin,
                        "dest": "BOG",
                        "state": "done",
                        "started_at": "2026-11-20T00:00:00Z",
                        "finished_at": "2026-11-20T00:20:00Z",
                    }
                    for origin in ("JFK", "EWR", "BOS")
                ],
            }
        ),
        encoding="utf-8",
    )
    (data / "history" / "bog.json").write_text(
        json.dumps(
            {
                "route_id": "bog",
                "points": [
                    {
                        "date": "2026-11-20",
                        "key": "bog|EWR|BOG|2027-03-01|2027-03-08",
                        "price": 410,
                        "depart": "2027-03-01",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    shards = tmp_path / "shards" / "BOS"
    shards.mkdir(parents=True)
    (shards / "shard.json").write_text(json.dumps(_shard("BOS", 320)), encoding="utf-8")

    report = merge_shards(
        tmp_path / "shards",
        data,
        config=load_config(config_path),
        run_id="99",
        run_url="https://example.test/99",
        now="2026-11-27T13:00:00Z",
        today=date(2026, 11, 27),
        scope=["BOS"],
    )
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in latest["routes"][0]["searches"]}
    assert rows["bog|JFK|BOG|2027-03-01|2027-03-08"]["price"] == 400
    assert rows["bog|EWR|BOG|2027-03-01|2027-03-08"]["price"] == 410
    assert rows["bog|EWR|BOG|2027-03-01|2027-03-08"]["airline"] == "Old"
    assert rows["bog|BOS|BOG|2027-03-01|2027-03-08"]["price"] == 320
    history = json.loads((data / "history" / "bog.json").read_text(encoding="utf-8"))
    keys = {point["key"] for point in history["points"]}
    assert "bog|EWR|BOG|2027-03-01|2027-03-08" in keys
    assert "bog|BOS|BOG|2027-03-01|2027-03-08" in keys
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "ok"
    assert status["searches_succeeded"] == 1
    assert status["errors"] == []
    assert status["coverage"]["origin"] == "BOS"
    assert status["coverage"]["origins"] == {"BOS": "merged"}
    queue = json.loads((data / "queue.json").read_text(encoding="utf-8"))
    by_origin = {leg["origin"]: leg for leg in queue["legs"]}
    assert by_origin["JFK"]["state"] == "done"
    assert by_origin["JFK"]["finished_at"] == "2026-11-20T00:20:00Z"
    assert by_origin["EWR"]["state"] == "done"
    assert by_origin["EWR"]["finished_at"] == "2026-11-20T00:20:00Z"
    assert by_origin["BOS"]["state"] == "done"
    assert by_origin["BOS"]["finished_at"] == "2026-11-27T12:10:00Z"
    assert queue["run_id"] == "99"
    assert report["missing"] == []
    assert report["present"] == ["BOS"]


def test_unfinished_shard_fails_the_legs_it_did_not_reach(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    _config(config_path)
    text = config_path.read_text(encoding="utf-8").replace("destinations: [BOG]", "destinations: [BOG, MDE]")
    config_path.write_text(text, encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    jfk_bog = _entry("JFK", 400)
    jfk_mde = dict(jfk_bog)
    jfk_mde["key"] = "bog|JFK|MDE|2027-03-01|2027-03-08"
    jfk_mde["dest"] = "MDE"
    jfk_mde["price"] = 450
    (data / "latest.json").write_text(
        json.dumps({"generated_at": "2026-11-20T00:00:00Z", "currency": "USD", "routes": [{"id": "bog", "searches": [jfk_bog, jfk_mde, _entry("EWR", 410)]}]}),
        encoding="utf-8",
    )
    (data / "queue.json").write_text(
        json.dumps(
            {
                "legs": [
                    {
                        "route": "bog",
                        "label": "Bogota",
                        "origin": "EWR",
                        "dest": "BOG",
                        "state": "done",
                        "started_at": "2026-11-20T00:00:00Z",
                        "finished_at": "2026-11-20T00:20:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    shard = _shard("JFK", 300, finished=False)
    shard["entries"] = [dict(jfk_bog, price=300), jfk_mde]
    shards = tmp_path / "shards" / "scrape-JFK"
    shards.mkdir(parents=True)
    (shards / "shard.json").write_text(json.dumps(shard), encoding="utf-8")
    merge_shards(
        tmp_path / "shards",
        data,
        config=load_config(config_path),
        run_id="1",
        run_url="",
        now="2026-11-27T13:00:00Z",
        today=date(2026, 11, 27),
        scope=["JFK"],
    )
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in latest["routes"][0]["searches"]}
    assert rows["bog|JFK|BOG|2027-03-01|2027-03-08"]["price"] == 300
    assert rows["bog|JFK|MDE|2027-03-01|2027-03-08"]["price"] == 450
    assert rows["bog|EWR|BOG|2027-03-01|2027-03-08"]["price"] == 410
    queue = json.loads((data / "queue.json").read_text(encoding="utf-8"))
    states = {(leg["origin"], leg["dest"]): leg["state"] for leg in queue["legs"]}
    assert states[("JFK", "BOG")] == "done"
    assert states[("JFK", "MDE")] == "failed"
    assert states[("EWR", "BOG")] == "done"
