from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scraper.schedule import select_origins

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


def test_scrape_workflow_staggers_origins_and_each_one_pushes():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8"))
    trigger = workflow["on"] if "on" in workflow else workflow[True]
    crons = [item["cron"] for item in trigger["schedule"]]
    assert crons == ["0 13 * * *", "0 19 * * *", "0 3 * * *"]
    assert trigger["workflow_dispatch"]["inputs"]["only_origin"]["default"] == "all"
    scrape = workflow["jobs"]["scrape"]
    assert scrape["strategy"]["max-parallel"] == 1
    assert scrape["strategy"]["fail-fast"] is False
    assert scrape["concurrency"]["group"] == "scrape-${{ matrix.origin }}"
    assert scrape["concurrency"]["cancel-in-progress"] is False
    assert scrape["timeout-minutes"] == 360
    script = _scripts(scrape)
    assert "scraper.origin_job" in script
    assert "scraper.shard merge" in script
    assert "--origin" in script
    assert "git push" in script
    assert "rebase origin/main" in script
    assert "upload-artifact" not in yaml.dump(workflow)
    assert "publish" not in workflow["jobs"]
    assert workflow["jobs"]["adhoc"]["concurrency"]["group"] == "scrape-adhoc"
    assert "git push" in _scripts(workflow["jobs"]["adhoc"])
