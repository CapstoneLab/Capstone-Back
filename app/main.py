from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query

from app.auth.router import router as auth_router
from app.db import Base, engine
from app.db_models import User  # noqa: F401  (ensures table is registered)
from app.models import (
    PipelineResultPayload,
    PipelineResultResponse,
    StartPipelineRequest,
    StartPipelineResponse,
)
from app.service import ResultStore, TriggerService, UbuntuResultFetcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("[startup] DB connected, tables ensured.")
    except Exception as exc:
        print(
            f"[startup] WARNING: DB init skipped ({exc.__class__.__name__}: {exc}). "
            "Server will run, but /auth/* endpoints will fail until DB is reachable."
        )
    yield


app = FastAPI(title="Windows CI Trigger Backend", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router)

trigger_service = TriggerService()
result_store = ResultStore()
result_fetcher = UbuntuResultFetcher()
job_state: dict[str, dict] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/start-pipeline", response_model=StartPipelineResponse)
def start_pipeline(req: StartPipelineRequest) -> StartPipelineResponse:
    job_id = str(uuid4())
    now = datetime.now(timezone.utc)

    job_state[job_id] = {
        "status": "queued",
        "repo_url": str(req.repo_url),
        "branch": req.branch,
        "requested_at": now.isoformat(),
    }

    try:
        trigger_service.trigger(job_id=job_id, repo_url=str(req.repo_url), branch=req.branch)
        job_state[job_id]["status"] = "triggered"
    except Exception as exc:
        job_state[job_id]["status"] = "failed_to_trigger"
        job_state[job_id]["error"] = str(exc)
        raise HTTPException(status_code=500, detail=f"failed to trigger ubuntu pipeline: {exc}") from exc

    return StartPipelineResponse(
        job_id=job_id,
        status="triggered",
        message="pipeline was triggered on ubuntu",
    )


@app.post("/get-results")
def receive_result(payload: PipelineResultPayload) -> dict[str, str]:
    obj = payload.model_dump(mode="json")

    # Prefer exact step statuses from Ubuntu pipeline_result.json
    # so Windows output matches Ubuntu summary (e.g. skipped vs success).
    if not obj.get("steps"):
        run_id = (obj.get("metadata") or {}).get("run_id", "")
        steps = result_fetcher.fetch_steps(run_id=run_id)
        if steps:
            obj["steps"] = steps

    result_store.save(payload.job_id, obj)

    existing = job_state.get(payload.job_id, {})
    existing.update(
        {
            "status": payload.status,
            "repo_url": payload.repo_url,
            "branch": payload.branch,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    job_state[payload.job_id] = existing

    return {"message": "result stored"}


@app.get("/get-results", response_model=PipelineResultResponse)
def get_result(job_id: str = Query(..., min_length=1)) -> PipelineResultResponse:
    data = result_store.load(job_id)
    if data is None:
        remote_data = result_fetcher.fetch_result_by_job_id(job_id=job_id)
        if remote_data is not None:
            result_store.save(job_id, remote_data)
            existing = job_state.get(job_id, {})
            existing.update(
                {
                    "status": remote_data.get("status", existing.get("status", "unknown")),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            job_state[job_id] = existing
            return PipelineResultResponse(found=True, data=PipelineResultPayload(**remote_data), message="ok")

        return PipelineResultResponse(found=False, data=None, message="result not found yet")

    return PipelineResultResponse(found=True, data=PipelineResultPayload(**data), message="ok")
