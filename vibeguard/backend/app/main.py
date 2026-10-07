from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .models import Run
from .orchestrator import ARTIFACTS, run_scan

ARTIFACTS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="VibeGuard AI")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3001"],
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/api/artifacts", StaticFiles(directory=ARTIFACTS), name="artifacts")


class RunRequest(BaseModel):
    target_url: str
    replay: bool = False


@app.post("/api/runs", response_model=Run)
async def create_run(req: RunRequest):
    """Slice 1: blocks until the scan finishes. Background execution + SSE comes next."""
    try:
        return await run_scan(req.target_url, req.replay)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs/{run_id}", response_model=Run)
async def get_run(run_id: str):
    f = ARTIFACTS / run_id / "run.json"
    if not run_id.isalnum() or not f.exists():
        raise HTTPException(404, "run not found")
    return Run.model_validate_json(f.read_text())


@app.get("/api/runs/{run_id}/findings")
async def get_findings(run_id: str):
    return (await get_run(run_id)).findings
