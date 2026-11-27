from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def _scripts(job: dict) -> str:
    chunks = []
    for step in job.get("steps") or []:
        run = step.get("run")
        if isinstance(run, str):
            chunks.append(run)
    return "\n".join(chunks)


def test_scrape_workflow_runs_three_origins_and_publishes_once():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "scrape.yml").read_text(encoding="utf-8"))
    origins = workflow["jobs"]["origins"]
    publish = workflow["jobs"]["publish"]
    assert origins["strategy"]["fail-fast"] is False
    assert origins["strategy"]["max-parallel"] == 3
    assert origins["strategy"]["matrix"]["origin"] == ["JFK", "EWR", "BOS"]
    assert origins["timeout-minutes"] == 360
    assert "git push" not in _scripts(origins)
    assert "upload-artifact" in yaml.dump(origins)
    assert publish["needs"] == "origins"
    assert "always()" in publish["if"]
    assert "download-artifact" in yaml.dump(publish)
    assert "scraper.shard merge" in _scripts(publish)
    assert "git push" in _scripts(publish)
    assert "git push" in _scripts(workflow["jobs"]["adhoc"])
