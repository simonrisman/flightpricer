from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scraper.config import load_config
from scraper.schedule import select_origins, select_pairs

ROOT = Path(__file__).parents[1]


def _scripts(job: dict) -> str:
    chunks = []
    for step in job.get("steps") or []:
        run = step.get("run")
        if isinstance(run, str):
            chunks.append(run)
    return "\n".join(chunks)


def test_schedules_are_est_clocks_and_manual_runs_can_pick_one_origin():
    assert select_origins("schedule", "0 13 * * *") == ["BOS"]
    assert select_origins("schedule", "0 19 * * *") == ["JFK"]
    assert select_origins("schedule", "0 3 * * *") == ["EWR"]
    assert select_origins("schedule", "0 03 * * *") == ["EWR"]
    assert select_origins("workflow_dispatch", only_origin="all") == ["BOS", "JFK", "EWR"]
    assert select_origins("workflow_dispatch", only_origin="") == ["BOS", "JFK", "EWR"]
    assert select_origins("workflow_dispatch", only_origin="bos") == ["BOS"]
    with pytest.raises(ValueError):
        select_origins("workflow_dispatch", only_origin="LGA")
    with pytest.raises(ValueError):
        select_origins("schedule", "17 11 * * *")


def test_select_pairs_follows_the_origin_and_an_optional_route():
    config = load_config(ROOT / "config.yaml")
    bos = select_pairs(config, ["BOS"])
    assert [pair["dest"] for pair in bos["BOS"]] == ["BOG", "MDE", "EZE", "CDG", "MAD", "BCN", "TBS", "PDL", "FNC"]
    assert bos["JFK"] == []
    assert bos["EWR"] == []
    one = select_pairs(config, ["BOS"], only_route="bog")
    assert one["BOS"] == [{"route": "bog", "origin": "BOS", "dest": "BOG"}]
    with pytest.raises(ValueError, match="no pairs for route"):
        select_pairs(config, ["BOS"], only_route="lim")


def test_scrape_workflow_chains_pairs_per_origin_without_a_sleep():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8"))
    trigger = workflow["on"] if "on" in workflow else workflow[True]
    crons = [item["cron"] for item in trigger["schedule"]]
    assert crons == ["0 13 * * *", "0 19 * * *", "0 3 * * *"]
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["default"] == "full"
    assert inputs["only_origin"]["default"] == "all"
    assert inputs["only_route"]["default"] == ""
    action = (ROOT / ".github" / "actions" / "scrape-pair" / "action.yml").read_text(encoding="utf-8")
    assert "scraper.origin_job" in action
    assert "--route" in action
    assert "--dest" in action
    assert "scraper.shard merge" in action
    assert "git push" in action
    assert "rebase origin/main" in action
    assert "sleep " not in action
    assert "sleep " not in (ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8")
    for origin, group in (("bos", "scrape-BOS"), ("jfk", "scrape-JFK"), ("ewr", "scrape-EWR")):
        job = workflow["jobs"][origin]
        assert job["name"] == "${{ matrix.pair.origin }}-${{ matrix.pair.dest }}"
        assert job["strategy"]["max-parallel"] == 1
        assert job["strategy"]["fail-fast"] is False
        assert job["concurrency"]["group"] == group
        assert job["concurrency"]["cancel-in-progress"] is False
        assert job["timeout-minutes"] == 120
        assert job["steps"][0]["uses"] == "actions/checkout@v4"
        assert job["steps"][1]["uses"] == "./.github/actions/scrape-pair"
    assert "upload-artifact" not in yaml.dump(workflow)
    assert "publish" not in workflow["jobs"]
    assert workflow["jobs"]["adhoc"]["concurrency"]["group"] == "scrape-adhoc"
    assert "git push" in _scripts(workflow["jobs"]["adhoc"])
    prepare = "\n".join(
        step.get("run") or "" for step in workflow["jobs"]["prepare"]["steps"]
    )
    assert "pydantic==2.12.5" in prepare
    assert "PyYAML==6.0.3" in prepare
