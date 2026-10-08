import asyncio
import json
import time
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .models import Run
from .orchestrator import ARTIFACTS, run_scan, assert_safe_target
from .db import (
    create_run, get_run, get_runs, get_findings, get_finding,
    get_attacks, get_events, get_events_after, append_event,
    init_db, get_db_path
)

ARTIFACTS.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="VibeGuard AI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/api/artifacts", StaticFiles(directory=ARTIFACTS), name="artifacts")


class RunRequest(BaseModel):
    target_url: str
    replay: bool = False


@app.post("/api/runs")
async def create_run_endpoint(req: RunRequest):
    """
    Validate target URL, create and persist Run immediately in SQLite,
    start background scan task, and return run details immediately.
    """
    try:
        assert_safe_target(req.target_url)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    init_db(get_db_path())
    run_id = uuid.uuid4().hex[:8]
    run = Run(
        id=run_id,
        target_url=req.target_url.rstrip("/"),
        replay=req.replay,
        status="running"
    )
    create_run(run)

    # Launch background scan asynchronously
    asyncio.create_task(run_scan(req.target_url, req.replay, run=run))

    return run


@app.get("/api/runs")
async def list_runs_endpoint():
    """List all persisted runs from SQLite."""
    init_db(get_db_path())
    return get_runs()


@app.get("/api/runs/{run_id}")
async def get_run_endpoint(run_id: str):
    """Get run details from SQLite (source of truth)."""
    init_db(get_db_path())
    run_data = get_run(run_id)
    if not run_data:
        raise HTTPException(status_code=404, detail="run not found")

    findings = get_findings(run_id)
    attacks = get_attacks(run_id)
    events = get_events(run_id)
    run_data["findings"] = findings
    run_data["attacks"] = attacks
    run_data["events"] = events
    return run_data


@app.get("/api/runs/{run_id}/findings")
async def get_run_findings_endpoint(run_id: str):
    """Get findings for a run from SQLite."""
    init_db(get_db_path())
    run_data = get_run(run_id)
    if not run_data:
        raise HTTPException(status_code=404, detail="run not found")
    return get_findings(run_id)


@app.get("/api/runs/{run_id}/findings/{finding_id}")
async def get_run_finding_endpoint(run_id: str, finding_id: str):
    """Get a specific finding for a run from SQLite."""
    init_db(get_db_path())
    run_data = get_run(run_id)
    if not run_data:
        raise HTTPException(status_code=404, detail="run not found")
    finding = get_finding(run_id, finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="finding not found")
    return finding


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    last_event_id_query: Optional[str] = Query(None, alias="last_event_id"),
):
    """
    Stream live Server-Sent Events (SSE) for a run.
    Replays existing events, streams new events as they occur,
    sends keepalives, and terminates cleanly when the run finishes.
    """
    init_db(get_db_path())
    run_data = get_run(run_id)
    if not run_data:
        raise HTTPException(status_code=404, detail="run not found")

    # Determine last event id to resume after
    id_param = last_event_id if last_event_id is not None else last_event_id_query
    initial_last_id = 0
    if id_param is not None:
        try:
            initial_last_id = int(id_param)
        except (ValueError, TypeError):
            initial_last_id = 0

    async def event_generator():
        current_id = initial_last_id
        keepalive_interval = 15.0
        last_activity_time = time.time()

        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                break

            # Fetch any new events since current_id
            events = get_events_after(run_id, current_id)
            if events:
                for ev in events:
                    current_id = ev["event_id"]
                    ev_type = ev.get("type", "message")
                    payload = json.loads(ev["payload_json"]) if ev.get("payload_json") else {}
                    data = {
                        "event_id": ev["event_id"],
                        "run_id": ev["run_id"],
                        "type": ev_type,
                        "stage": ev_type,
                        "timestamp": ev["timestamp"],
                        **payload
                    }
                    data_json = json.dumps(data)
                    yield f"id: {current_id}\nevent: {ev_type}\ndata: {data_json}\n\n"
                    last_activity_time = time.time()

            # Check if run has ended
            current_run = get_run(run_id)
            if current_run and current_run.get("status") in ("completed", "failed"):
                # Drain any final events added right before termination
                remaining_events = get_events_after(run_id, current_id)
                for ev in remaining_events:
                    current_id = ev["event_id"]
                    ev_type = ev.get("type", "message")
                    payload = json.loads(ev["payload_json"]) if ev.get("payload_json") else {}
                    data = {
                        "event_id": ev["event_id"],
                        "run_id": ev["run_id"],
                        "type": ev_type,
                        "stage": ev_type,
                        "timestamp": ev["timestamp"],
                        **payload
                    }
                    data_json = json.dumps(data)
                    yield f"id: {current_id}\nevent: {ev_type}\ndata: {data_json}\n\n"
                # Clean termination
                break

            # Send keepalive comment if idle
            now = time.time()
            if now - last_activity_time >= keepalive_interval:
                yield ": keepalive\n\n"
                last_activity_time = now

            await asyncio.sleep(0.1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
