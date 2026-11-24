from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from scraper.main import run
from scraper.models import FlightOption, SearchResult


ROOT = Path(__file__).parents[1]


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_dry_run_prints_the_real_search_count(capsys):
    code = run(["--dry-run", "--today", "2026-11-23", "--config", str(ROOT / "config.yaml")])
    captured = capsys.readouterr()
    assert code == 0
    assert "searches" in captured.out
    assert "bog|" in captured.out
    assert "scheduled" in captured.out


def test_only_origin_keeps_one_airport(capsys):
    code = run(
        ["--dry-run", "--today", "2026-11-23", "--config", str(ROOT / "config.yaml"), "--only-origin", "jfk"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "|JFK|" in captured.out
    assert "|EWR|" not in captured.out
    assert "|BOS|" not in captured.out
    assert "scheduled 3000" in captured.out


def test_only_route_keeps_one_city(capsys):
    code = run(
        ["--dry-run", "--today", "2026-11-23", "--config", str(ROOT / "config.yaml"), "--only-route", "bog"]
    )
    captured = capsys.readouterr()
    assert code == 0
    lines = [line for line in captured.out.splitlines() if line.startswith("bog|")]
    assert lines
    assert "mde|" not in captured.out
    assert f"scheduled {len(lines)}" in captured.out


def test_failures_do_not_blank_saved_prices(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    _write(
        config,
        """
settings:
  currency: USD
  max_requests_per_run: 10
  delay_seconds: [0, 0]
routes:
  - id: bog
    label: Bogota
    origins: [JFK, LGA]
    destinations: [BOG]
    trip: round_trip
    depart_window: { start: 2027-03-04, end: 2027-03-04 }
    depart_days: [thu]
    trip_length_days: [7]
    target_price: 300
    active: true
""",
    )
    data = tmp_path / "data"
    data.mkdir()
    previous = {
        "generated_at": "2026-11-20T00:00:00Z",
        "currency": "USD",
        "routes": [
            {
                "id": "bog",
                "searches": [
                    {
                        "key": "bog|LGA|BOG|2027-03-04|2027-03-11",
                        "route_id": "bog",
                        "origin": "LGA",
                        "dest": "BOG",
                        "depart": "2027-03-04",
                        "return": "2027-03-11",
                        "price": 410,
                        "fetched_at": "2026-11-20T00:00:00Z",
                        "stale": False,
                        "google_url": "https://www.google.com/travel/flights",
                        "airline": "Old",
                        "stops": 1,
                        "duration": "8h",
                    },
                    {
                        "key": "bog|JFK|BOG|2026-01-01|2026-01-08",
                        "route_id": "bog",
                        "origin": "JFK",
                        "dest": "BOG",
                        "depart": "2026-01-01",
                        "return": "2026-01-08",
                        "price": 111,
                        "fetched_at": "2026-01-01T00:00:00Z",
                        "stale": False,
                    },
                ],
            }
        ],
    }
    (data / "latest.json").write_text(json.dumps(previous), encoding="utf-8")
    (data / "status.json").write_text(json.dumps({"last_success": "2026-11-20T00:00:00Z", "state": "ok"}), encoding="utf-8")

    def fake_searcher(_source):
        def search(item, **kwargs):
            ok = item.origin == "JFK"
            cheapest = FlightOption(280, "B6", 0, "5h 40m", "22:30", "04:10") if ok else None
            return SearchResult(
                key=item.key,
                route_id=item.route_id,
                origin=item.origin,
                dest=item.dest,
                depart=item.depart,
                return_date=item.return_date,
                ok=ok,
                error=None if ok else "empty result",
                options=[cheapest] if cheapest else [],
                cheapest=cheapest,
                google_url="https://www.google.com/travel/flights?tfs=abc",
                fetched_at=kwargs["fetched_at"],
            )

        return search

    monkeypatch.setattr("scraper.main._searcher", fake_searcher)
    code = run(
        [
            "--config",
            str(config),
            "--data-dir",
            str(data),
            "--today",
            "2027-03-04",
        ]
    )
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in latest["routes"][0]["searches"]}
    assert code == 0
    assert status["state"] == "degraded"
    assert rows["bog|JFK|BOG|2027-03-04|2027-03-11"]["price"] == 280
    assert rows["bog|JFK|BOG|2027-03-04|2027-03-11"]["stale"] is False
    assert rows["bog|JFK|BOG|2027-03-04|2027-03-11"]["delta"] is None
    assert rows["bog|LGA|BOG|2027-03-04|2027-03-11"]["price"] == 410
    assert rows["bog|LGA|BOG|2027-03-04|2027-03-11"]["stale"] is True
    assert rows["bog|LGA|BOG|2027-03-04|2027-03-11"]["fetched_at"] == "2026-11-20T00:00:00Z"
    assert "bog|JFK|BOG|2026-01-01|2026-01-08" not in rows
    history = json.loads((data / "history" / "bog.json").read_text(encoding="utf-8"))
    assert history["points"][0]["price"] == 280
    assert date(2027, 3, 4).isoformat() == "2027-03-04"


def test_empty_results_do_not_stop_the_run(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.yaml"
    _write(
        config,
        """
settings:
  currency: USD
  max_requests_per_run: 20
  delay_seconds: [0, 0]
routes:
  - id: x
    label: Test
    origins: [JFK]
    destinations: [BOG]
    trip: one_way
    depart_window: { start: 2027-04-01, end: 2027-04-06 }
    active: true
""",
    )
    data = tmp_path / "data"
    data.mkdir()

    def fake_searcher(_source):
        def search(item, **kwargs):
            return SearchResult(
                key=item.key,
                route_id=item.route_id,
                origin=item.origin,
                dest=item.dest,
                depart=item.depart,
                return_date=item.return_date,
                ok=False,
                error="empty result",
                options=[],
                cheapest=None,
                google_url="",
                fetched_at=kwargs["fetched_at"],
            )

        return search

    monkeypatch.setattr("scraper.main._searcher", fake_searcher)
    code = run(["--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    assert code == 1
    assert status["stopped_early"] is False
    assert status["searches_attempted"] == 6


def test_skip_continues_past_finished_searches(tmp_path: Path, capsys):
    config = tmp_path / "config.yaml"
    _write(
        config,
        """
settings:
  currency: USD
  max_requests_per_run: 20
  delay_seconds: [0, 0]
routes:
  - id: x
    label: Test
    origins: [JFK]
    destinations: [BOG]
    trip: one_way
    depart_window: { start: 2027-04-01, end: 2027-04-06 }
    active: true
""",
    )
    code = run(["--dry-run", "--config", str(config), "--today", "2027-04-01", "--skip", "2"])
    assert code == 0
    assert "scheduled 4" in capsys.readouterr().out


def test_circuit_breaker_stops_a_blocked_run(tmp_path: Path):
    config = tmp_path / "config.yaml"
    _write(
        config,
        """
settings:
  currency: USD
  max_requests_per_run: 20
  delay_seconds: [0, 0]
  source: playwright
routes:
  - id: x
    label: Test
    origins: [JFK]
    destinations: [BOG]
    trip: one_way
    depart_window: { start: 2027-04-01, end: 2027-04-06 }
    active: true
""",
    )
    data = tmp_path / "data"
    data.mkdir()
    code = run(["--config", str(config), "--data-dir", str(data), "--today", "2027-04-01"])
    status = json.loads((data / "status.json").read_text(encoding="utf-8"))
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    assert code == 1
    assert status["state"] == "failed"
    assert status["stopped_early"] is True
    assert status["searches_attempted"] == 5
    assert latest["routes"][0]["searches"] == []
