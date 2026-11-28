from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scraper.config import load_config
from scraper.schedule import render_chain_jobs, select_origins, select_pairs

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


def test_scrape_workflow_chains_each_route_and_waits_between_them():
    text = (ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8")
    config = load_config(ROOT / "config.yaml")
    assert render_chain_jobs(config) in text
    workflow = yaml.safe_load(text)
    trigger = workflow["on"] if "on" in workflow else workflow[True]
    crons = [item["cron"] for item in trigger["schedule"]]
    assert crons == ["0 13 * * *", "0 19 * * *", "0 3 * * *"]
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["default"] == "full"
    assert inputs["only_origin"]["default"] == "all"
    assert inputs["only_route"]["default"] == ""
    assert "cooldown_minutes" in inputs
    action = (ROOT / ".github" / "actions" / "scrape-pair" / "action.yml").read_text(encoding="utf-8")
    assert "scraper.origin_job" in action
    assert "--route" in action
    assert "--dest" in action
    assert "scraper.shard merge" in action
    assert "git push" in action
    assert "merging this origin onto latest main" in action
    assert "rebase origin/main" not in action
    assert "sleep " not in action
    assert "COOLDOWN_SECONDS" in action
    plan = select_pairs(config, ["BOS", "JFK", "EWR"])
    for origin, legs in plan.items():
        previous = None
        for leg in legs:
            job_id = f"{origin.lower()}-{leg['route']}"
            job = workflow["jobs"][job_id]
            assert job["name"] == f"{origin}-{leg['dest']}"
            assert job["timeout-minutes"] == 120
            assert job["concurrency"]["group"] == f"scrape-{origin}"
            assert job["concurrency"]["cancel-in-progress"] is False
            assert "!cancelled()" in str(job["if"]) or previous is None
            needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
            assert "prepare" in needs
            names = [step.get("name") for step in job["steps"]]
            if previous is None:
                assert "Cooldown" not in names
            else:
                assert previous in needs
                cooldown = next(step for step in job["steps"] if step.get("name") == "Cooldown")
                assert cooldown["run"].startswith("sleep ")
                assert "success" in cooldown["if"] and "failure" in cooldown["if"]
            previous = job_id
    assert "upload-artifact" not in yaml.dump(workflow)
    assert "publish" not in workflow["jobs"]
    assert workflow["jobs"]["adhoc"]["concurrency"]["group"] == "scrape-adhoc"
    assert "git push" in _scripts(workflow["jobs"]["adhoc"])
    prepare = "\n".join(step.get("run") or "" for step in workflow["jobs"]["prepare"]["steps"])
    assert "pydantic==2.12.5" in prepare
    assert "PyYAML==6.0.3" in prepare
    assert '"cooldown"' in prepare
