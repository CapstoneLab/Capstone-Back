from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query

from app.models import (
    PipelineResultPayload,
    PipelineResultResponse,
    StartPipelineRequest,
    StartPipelineResponse,
)
from app.service import ResultStore, TriggerService

app = FastAPI(title="Windows CI Trigger Backend", version="1.0.0")

trigger_service = TriggerService()
result_store = ResultStore()
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
        return PipelineResultResponse(found=False, data=None, message="result not found yet")

    return PipelineResultResponse(found=True, data=PipelineResultPayload(**data), message="ok")
