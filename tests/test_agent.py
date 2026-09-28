"""The runbook on a healthy platform, with the offline model: no faults here."""

from __future__ import annotations

import pytest

import ops
from agent import IncidentAgent
from runtime import open_model


def handle(tmp_path, world: ops.World) -> list[dict]:
    log = ops.ActionLog(tmp_path / "actions.jsonl")
    ops_url, stop_ops = ops.serve_in_background(log, world)
    model, close = open_model()
    try:
        IncidentAgent(model, ops_url).handle()
    finally:
        close()
        stop_ops()
    return log.entries()


@pytest.fixture(autouse=True)
def offline_model(monkeypatch):
    monkeypatch.delenv("INCIDENT_LIVE", raising=False)


def events(actions: list[dict]) -> list[str]:
    return [a["event"] for a in actions]


def test_a_real_outage_rolls_back_the_bad_deploy_and_gets_a_person(tmp_path):
    actions = handle(tmp_path, ops.World(outage=True))
    assert events(actions) == ["deploy.rolled_back", "statuspage.posted", "oncall.paged"]
    assert actions[0]["deploy_id"] == "d-4127"
    assert actions[2]["service"] == ops.SERVICE


def test_a_false_alarm_is_resolved_without_a_page(tmp_path):
    actions = handle(tmp_path, ops.World(outage=False))
    assert events(actions) == ["alert.resolved"]
    assert actions[0]["alert_id"] == ops.ALERT_ID
