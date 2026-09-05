from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.main import _normalise_step_log_lines
from app.models import PipelineResultPayload
from app.realtime import PipelineEventHub


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


def test_event_hub_sends_snapshot_before_live_batch() -> None:
    hub = PipelineEventHub()
    socket = _Socket()
    batch = {
        "type": "log_batch",
        "job_id": "job-1",
        "events": [
            {"sequence": 1, "step_name": "build", "message": "building"},
        ],
    }

    async def scenario() -> None:
        await hub.publish("job-1", batch)
        await hub.subscribe("job-1", socket)
        await hub.publish(
            "job-1",
            {
                **batch,
                "events": [{"sequence": 2, "step_name": "build", "message": "done"}],
            },
        )

    asyncio.run(scenario())

    assert socket.messages[0]["type"] == "log_snapshot"
    assert socket.messages[0]["lines"] == ["[build.log] building"]
    assert socket.messages[1]["type"] == "log_batch"
    assert socket.messages[1]["events"][0]["sequence"] == 2


def test_log_batch_payload_model_preserves_stream_fields() -> None:
    payload = PipelineResultPayload.model_validate(
        {
            "schema_version": 1,
            "type": "log_batch",
            "event_id": "event-1",
            "job_id": "job-1",
            "run_id": "run-1",
            "repo_url": "https://github.com/example/repo.git",
            "branch": "main",
            "pipeline_status": "running",
            "sequence_start": 1,
            "sequence_end": 1,
            "events": [{"sequence": 1, "step_name": "clone", "message": "cloning"}],
        }
    )

    assert payload.callback_type == "log_batch"
    assert payload.event_id == "event-1"
    assert payload.events[0]["step_name"] == "clone"


def test_realtime_and_deployment_routes_are_registered() -> None:
    paths = {route.path for route in app.routes}

    assert "/api/pipelines/{job_id}/logs/ws" in paths
    assert "/api/pipelines/{job_id}/deployment" in paths
    assert "/api/jobs/{job_id}/fail" in paths


def test_timestamped_internal_step_metadata_is_filtered() -> None:
    assert _normalise_step_log_lines(
        [
            "[2026-09-05 12:00:00] visible output",
            "[2026-09-05 12:00:01] [step_status] success",
            "[2026-09-05 12:00:01] [step_exit_code] 0",
        ]
    ) == ["[2026-09-05 12:00:00] visible output"]


def test_callback_rejects_wrong_shared_token(monkeypatch) -> None:
    class _Settings:
        engine_shared_token = "expected"

    monkeypatch.setattr(main, "get_settings", lambda: _Settings())
    client = TestClient(app)
    response = client.post(
        "/get-results",
        headers={"x-callback-token": "wrong"},
        json={
            "type": "log_batch",
            "job_id": "00000000-0000-0000-0000-000000000001",
            "repo_url": "https://github.com/example/repo.git",
            "branch": "main",
            "pipeline_status": "running",
            "events": [],
        },
    )

    assert response.status_code == 401


def test_step_complete_realtime_event_has_frontend_contract_fields(monkeypatch) -> None:
    published: list[dict] = []

    class _Settings:
        engine_shared_token = ""

    async def _publish(job_id: str, payload: dict) -> None:
        published.append(payload)

    monkeypatch.setattr(main, "get_settings", lambda: _Settings())
    monkeypatch.setattr(main.pipeline_event_hub, "publish", _publish)

    client = TestClient(app)
    response = client.post(
        "/get-results",
        json={
            "type": "step_complete",
            "job_id": "00000000-0000-0000-0000-000000000001",
            "repo_url": "https://github.com/example/repo.git",
            "branch": "main",
            "pipeline_status": "running",
            "step": {
                "step_name": "clone",
                "status": "success",
                "started_at": "2026-09-06T00:00:00+00:00",
                "ended_at": "2026-09-06T00:00:01+00:00",
                "duration_secs": 1,
            },
        },
    )

    assert response.status_code == 200
    assert published[-1]["type"] == "step_complete"
    assert published[-1]["step_name"] == "clone"
    assert published[-1]["status"] == "success"
