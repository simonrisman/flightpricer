from scraper.queue import init_queue, mark_leg, stamp_from_latest


ROUTES = [
    {"id": "bog", "label": "Bogotá", "origins": ["JFK", "EWR", "BOS"], "destinations": ["BOG"], "active": True},
    {"id": "tyo", "label": "Tokyo", "origins": ["BOS"], "destinations": ["HND", "NRT"], "active": True},
]


def test_init_keeps_the_previous_scrape_time_and_resets_state():
    previous = {
        "legs": [
            {
                "route": "bog",
                "origin": "JFK",
                "dest": "BOG",
                "state": "done",
                "started_at": "2026-11-23T12:00:00Z",
                "finished_at": "2026-11-23T12:20:00Z",
            }
        ]
    }
    queue = init_queue(ROUTES, previous, "2026-11-24T11:17:00Z", "99", "https://example.test/run/99")
    assert [leg["origin"] + "-" + leg["dest"] for leg in queue["legs"]] == ["JFK-BOG", "EWR-BOG", "BOS-BOG", "BOS-HND", "BOS-NRT"]
    assert queue["legs"][0]["state"] == "pending"
    assert queue["legs"][0]["finished_at"] == "2026-11-23T12:20:00Z"
    assert queue["legs"][1]["finished_at"] is None
    assert queue["run_url"] == "https://example.test/run/99"


def test_mark_running_then_done():
    queue = init_queue(ROUTES, None, "2026-11-24T11:17:00Z", "", "")
    mark_leg(queue, "tyo", "bos", "nrt", "running", "2026-11-24T12:00:00Z")
    narita = queue["legs"][-1]
    assert narita["state"] == "running"
    assert narita["started_at"] == "2026-11-24T12:00:00Z"
    mark_leg(queue, "tyo", "BOS", "NRT", "done", "2026-11-24T12:40:00Z")
    assert narita["state"] == "done"
    assert narita["finished_at"] == "2026-11-24T12:40:00Z"


def test_stamp_uses_the_newest_saved_search():
    queue = init_queue(ROUTES, None, "2026-11-24T11:17:00Z", "", "")
    latest = {
        "routes": [
            {
                "id": "bog",
                "searches": [
                    {"origin": "BOS", "dest": "BOG", "fetched_at": "2026-11-23T19:00:00Z"},
                    {"origin": "BOS", "dest": "BOG", "fetched_at": "2026-11-23T19:40:00Z"},
                ],
            }
        ]
    }
    stamp_from_latest(queue, latest)
    boston = next(leg for leg in queue["legs"] if leg["origin"] == "BOS" and leg["dest"] == "BOG")
    newark = next(leg for leg in queue["legs"] if leg["origin"] == "EWR")
    assert boston["finished_at"] == "2026-11-23T19:40:00Z"
    assert boston["state"] == "pending"
    assert newark["finished_at"] is None
