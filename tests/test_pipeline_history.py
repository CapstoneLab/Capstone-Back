import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.db_models import PipelineJob
from app.main import (
    _authenticated_user_id,
    _normalise_step_log_lines,
    _upsert_step_log,
    app,
    get_pipeline_history,
)
from scripts.apply_migrations import migration_sort_key


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _StepLogSession:
    def __init__(self):
        self.log = None

    async def execute(self, _statement, *_args, **_kwargs):
        return _ScalarResult(self.log)

    def add(self, value):
        self.log = value


class _HistoryRowsResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _HistorySession:
    def __init__(self, jobs):
        self.jobs = jobs
        self.execute_count = 0

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _ScalarCountResult(len(self.jobs))
        return _HistoryRowsResult(self.jobs)


class _ScalarCountResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


def test_history_route_is_registered() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/pipelines/history" in paths


def test_step_log_upsert_reuses_one_row_and_preserves_longer_callback_log() -> None:
    session = _StepLogSession()

    first = asyncio.run(
        _upsert_step_log(
            session,
            job_id="00000000-0000-0000-0000-000000000001",
            step_id="00000000-0000-0000-0000-000000000002",
            raw_lines=["installing", "done", "[step_status] success"],
            delivery_id="delivery-1",
            source="step_complete",
        )
    )
    original = session.log

    second = asyncio.run(
        _upsert_step_log(
            session,
            job_id="00000000-0000-0000-0000-000000000001",
            step_id="00000000-0000-0000-0000-000000000002",
            raw_lines=["done"],
            delivery_id=None,
            source="pipeline_complete",
        )
    )

    assert first == (2, len("installing\ndone".encode("utf-8")))
    assert second == first
    assert session.log is original
    assert session.log.log_content == "installing\ndone"
    assert session.log.delivery_id == "delivery-1"
    assert session.log.source == "step_complete"


def test_internal_step_metadata_is_not_saved_as_user_log() -> None:
    assert _normalise_step_log_lines([
        "output",
        "[step_summary] complete",
        "[step_exit_code] 0",
    ]) == ["output"]


def test_shorter_retry_never_replaces_a_longer_step_log() -> None:
    session = _StepLogSession()
    asyncio.run(
        _upsert_step_log(
            session,
            job_id="00000000-0000-0000-0000-000000000001",
            step_id="00000000-0000-0000-0000-000000000002",
            raw_lines=["first", "second", "third"],
            delivery_id=None,
            source="pipeline_complete",
        )
    )

    result = asyncio.run(
        _upsert_step_log(
            session,
            job_id="00000000-0000-0000-0000-000000000001",
            step_id="00000000-0000-0000-0000-000000000002",
            raw_lines=["third"],
            delivery_id="retry-delivery",
            source="step_complete",
        )
    )

    assert result == (3, len("first\nsecond\nthird".encode("utf-8")))
    assert session.log.log_content == "first\nsecond\nthird"
    assert session.log.source == "pipeline_complete"


def test_history_returns_progress_for_the_authenticated_user() -> None:
    now = datetime.now(timezone.utc)
    job = PipelineJob(
        job_id="00000000-0000-0000-0000-000000000003",
        repo_url="https://github.com/example/repo.git",
        branch="main",
        trigger_source="manual",
        status="running",
        source="capstone",
        environment="development",
        selected_items=["sql-injection"],
        user_id=2,
        completed_steps=3,
        total_steps=6,
        latest_step_name="test",
        created_at=now,
        last_event_at=now,
    )
    session = _HistorySession([job])

    response = asyncio.run(
        get_pipeline_history(
            status_filter=None,
            repo=None,
            limit=20,
            offset=0,
            current_user={"id": 2},
            db=session,
        )
    )

    assert response["total"] == 1
    assert response["has_more"] is False
    assert response["items"][0]["job_id"] == job.job_id
    assert response["items"][0]["progress_percent"] == 50
    assert response["items"][0]["latest_step_name"] == "test"
    assert response["items"][0]["selected_items"] == ["sql-injection"]


def test_history_rejects_unknown_status_before_querying_database() -> None:
    session = _HistorySession([])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            get_pipeline_history(
                status_filter="unknown",
                repo=None,
                limit=20,
                offset=0,
                current_user={"id": 2},
                db=session,
            )
        )

    assert exc_info.value.status_code == 400
    assert session.execute_count == 0


def test_authenticated_user_id_accepts_dict_and_model_shapes() -> None:
    class UserShape:
        id = 7

    assert _authenticated_user_id({"id": 2}) == 2
    assert _authenticated_user_id(UserShape()) == 7
    with pytest.raises(HTTPException) as exc_info:
        _authenticated_user_id({"github_id": 123})
    assert exc_info.value.status_code == 401


def test_migrations_are_sorted_by_numeric_version() -> None:
    paths = [Path("v10_future.sql"), Path("v2_security.sql"), Path("v6_history.sql")]

    assert [path.name for path in sorted(paths, key=migration_sort_key)] == [
        "v2_security.sql",
        "v6_history.sql",
        "v10_future.sql",
    ]
