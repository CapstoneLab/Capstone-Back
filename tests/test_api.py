from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app


class DummyTrigger:
    def trigger(self, *, job_id: str, repo_url: str, branch: str, env_vars=None) -> None:
        return


class DummyUser:
    id = 1
    github_id = 12345
    github_login = "testuser"


def test_start_and_get_results(monkeypatch):
    from app import main
    from app.auth import jwt_utils

    monkeypatch.setattr(main, "trigger_service", DummyTrigger())
    monkeypatch.setattr(jwt_utils, "get_current_user", lambda: DummyUser())

    from app.auth.jwt_utils import get_current_user
    app.dependency_overrides[get_current_user] = lambda: DummyUser()

    client = TestClient(app)

    start = client.post(
        "/start-pipeline",
        json={"repo_url": "https://github.com/example/repo.git", "branch": "main"},
    )
    assert start.status_code == 200
    job_id = start.json()["job_id"]

    result_payload = {
        "job_id": job_id,
        "status": "success",
        "repo_url": "https://github.com/example/repo.git",
        "branch": "main",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "logs": ["line1", "line2"],
        "metadata": {"k": "v"},
    }

    recv = client.post("/get-results", json=result_payload)
    assert recv.status_code == 200

    get_res = client.get("/get-results", params={"job_id": job_id})
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["found"] is True
    assert body["data"]["status"] == "success"

    app.dependency_overrides.clear()
