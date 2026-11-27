from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scraper.config import load_config, public_config

ROOT = Path(__file__).parents[1]


def test_published_config_json_matches_yaml():
    published = json.loads((ROOT / "site" / "data" / "config.json").read_text(encoding="utf-8"))
    assert published == public_config(load_config(ROOT / "config.yaml"))


def test_dashboard_emitter_round_trips_the_real_config():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to check the dashboard YAML emitter")
    config = load_config(ROOT / "config.yaml")
    payload = public_config(config)
    script = r"""
const fs = require("fs");
const vm = require("vm");
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
if (context.weekdayName("2027-03-04") !== "thu") throw new Error("weekday");
if (context.weekdayName("2027-03-07") !== "sun") throw new Error("sunday");
if (context.searchKey("bog", "JFK", "BOG", "2027-03-05", "2027-03-12") !== "bog|JFK|BOG|2027-03-05|2027-03-12") {
  throw new Error("key");
}
if (context.addDays("2027-03-05", 7) !== "2027-03-12") throw new Error("addDays");
const bog = config.routes.find((route) => route.id === "bog");
if (!context.inWindow(bog, "2027-03-05", "2027-03-12")) throw new Error("expected inside window");
if (!context.inWindow(bog, "2027-03-05", "2027-03-20")) throw new Error("15-day trip should be inside");
if (!context.inWindow(bog, "2027-03-07", "2027-03-14")) throw new Error("sunday should be inside the window");
if (context.inWindow(bog, "2027-03-05", "2027-03-11")) throw new Error("6-day trip should be outside");
if (context.inWindow(bog, "2027-03-05", "2027-03-21")) throw new Error("16-day trip should be outside");
const withExtra = JSON.parse(JSON.stringify(config));
withExtra.routes[0].extra_dates = [{ depart: "2027-08-05", return: "2027-08-12", origin: "BOS", dest: "BOG" }];
fs.writeFileSync(process.argv[3], context.emitYaml(withExtra));
"""
    emitted = Path("/tmp/flightpricer-emitted.yaml")
    source = Path("/tmp/flightpricer-public.json")
    source.write_text(json.dumps(payload), encoding="utf-8")
    completed = subprocess.run(
        [node, "-e", script, str(ROOT / "site" / "config-io.js"), str(source), str(emitted)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    reloaded = load_config(emitted)
    again = public_config(reloaded)
    original = json.loads(json.dumps(payload))
    original["routes"][0]["extra_dates"] = [
        {"depart": "2027-08-05", "return": "2027-08-12", "trip_length_days": None, "origin": "BOS", "dest": "BOG"}
    ]
    # The emitter only adds the extra date we asked for; compare the rest, then the extra itself.
    assert again["settings"] == original["settings"]
    assert [route["id"] for route in again["routes"]] == [route["id"] for route in original["routes"]]
    bog = again["routes"][0]
    assert bog["extra_dates"] == [
        {"depart": "2027-08-05", "return": "2027-08-12", "trip_length_days": None, "origin": "BOS", "dest": "BOG"}
    ]
    assert bog["label"] == "Bogotá"
    assert bog["origins"] == ["JFK", "EWR", "BOS"]
