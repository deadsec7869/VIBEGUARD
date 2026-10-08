import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_connection
from app.db.repositories import (
    create_run, update_run, save_finding, save_fix_result,
    append_event, get_run, get_events, get_fix_result
)
from app.models import Run, Finding, Evidence, Step, FixResult


@pytest.fixture
def client():
    return TestClient(app)


def test_post_runs_returns_immediately_with_run_id(client):
    """POST /api/runs should validate URL, persist run, and return immediately with running status."""
    with patch("app.main.run_scan", new_callable=AsyncMock) as mock_scan:
        response = client.post("/api/runs", json={"target_url": "http://localhost:3000", "replay": False})
        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data or "id" in data
        run_id = data.get("run_id") or data.get("id")
        assert run_id is not None
        assert data["status"] == "running"
        assert data["target_url"] == "http://localhost:3000"

        # Verify run was persisted in SQLite
        db_run = get_run(run_id)
        assert db_run is not None
        assert db_run["status"] == "running"
        assert db_run["target_url"] == "http://localhost:3000"


def test_post_runs_url_validation_safety(client):
    """POST /api/runs must enforce localhost target safety guards."""
    # Remote target without override
    res1 = client.post("/api/runs", json={"target_url": "http://example.com:3000"})
    assert res1.status_code == 400

    # Invalid scheme
    res2 = client.post("/api/runs", json={"target_url": "ftp://localhost:3000"})
    assert res2.status_code == 400


def test_get_runs_and_get_run(client):
    """GET /api/runs and GET /api/runs/{run_id} use SQLite as source of truth."""
    run_id = "test-run-api-1"
    run = Run(id=run_id, target_url="http://localhost:3000", status="completed", plan_source="deterministic")
    create_run(run)

    # List runs
    res_list = client.get("/api/runs")
    assert res_list.status_code == 200
    runs = res_list.json()
    assert any(r["id"] == run_id or r["run_id"] == run_id for r in runs)

    # Get single run
    res_single = client.get(f"/api/runs/{run_id}")
    assert res_single.status_code == 200
    run_data = res_single.json()
    assert run_data["id"] == run_id
    assert run_data["target_url"] == "http://localhost:3000"
    assert run_data["status"] == "completed"
    assert "findings" in run_data
    assert "attacks" in run_data
    assert "events" in run_data

    # 404 on missing run
    res_missing = client.get("/api/runs/nonexistent-run-999")
    assert res_missing.status_code == 404


def test_get_findings_and_get_finding(client):
    """GET /api/runs/{run_id}/findings and GET /api/runs/{run_id}/findings/{finding_id}."""
    run_id = "test-run-findings-1"
    create_run(Run(id=run_id, target_url="http://localhost:3000"))

    f1 = Finding(
        id="VG-001", category="security", type="auth", severity="high",
        title="Auth bypass", target="/dashboard", expected="redirect", actual="200 OK",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    f2 = Finding(
        id="VG-002", category="functional", type="input", severity="medium",
        title="Input validation", target="/register", expected="error", actual="success",
        reproducible=True, confidence=0.9, evidence=Evidence(), steps=[]
    )
    save_finding(run_id, f1)
    save_finding(run_id, f2)

    # List findings
    res_findings = client.get(f"/api/runs/{run_id}/findings")
    assert res_findings.status_code == 200
    findings = res_findings.json()
    assert len(findings) == 2
    f_ids = {f["id"] for f in findings}
    assert "VG-001" in f_ids and "VG-002" in f_ids

    # Single finding
    res_f1 = client.get(f"/api/runs/{run_id}/findings/VG-001")
    assert res_f1.status_code == 200
    f1_data = res_f1.json()
    assert f1_data["id"] == "VG-001"
    assert f1_data["title"] == "Auth bypass"

    # 404 for nonexistent finding
    res_not_found = client.get(f"/api/runs/{run_id}/findings/VG-999")
    assert res_not_found.status_code == 404

    # 404 for nonexistent run
    res_no_run = client.get("/api/runs/missing-run-404/findings")
    assert res_no_run.status_code == 404


def test_run_isolation(client):
    """Ensure findings from different runs remain isolated across API endpoints."""
    run_a = "run-iso-api-a"
    run_b = "run-iso-api-b"
    create_run(Run(id=run_a, target_url="http://localhost:3000"))
    create_run(Run(id=run_b, target_url="http://localhost:3000"))

    fa = Finding(
        id="VG-001", category="security", type="auth", severity="high",
        title="Run A finding", target="/a", expected="E", actual="A",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    save_finding(run_a, fa)

    # Run A should have VG-001
    res_a = client.get(f"/api/runs/{run_a}/findings/VG-001")
    assert res_a.status_code == 200
    assert res_a.json()["title"] == "Run A finding"

    # Run B should NOT have VG-001
    res_b = client.get(f"/api/runs/{run_b}/findings/VG-001")
    assert res_b.status_code == 404


def test_sse_returns_existing_events_and_format(client):
    """SSE endpoint should replay existing events with correct text/event-stream format."""
    run_id = "test-sse-stream-1"
    create_run(Run(id=run_id, target_url="http://localhost:3000", status="completed"))

    append_event(run_id, "discovery", "Discovery started", {"pages": 3})
    append_event(run_id, "vibe_attack", "Planning attacks", {"count": 5})

    response = client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    body = response.text
    # SSE events should be separated by \n\n
    events_raw = [e.strip() for e in body.strip().split("\n\n") if e.strip()]
    assert len(events_raw) >= 2

    # Verify SSE format for first event
    first_event = events_raw[0]
    lines = first_event.split("\n")
    id_line = next(l for l in lines if l.startswith("id:"))
    event_line = next(l for l in lines if l.startswith("event:"))
    data_line = next(l for l in lines if l.startswith("data:"))

    assert id_line.startswith("id: ")
    assert "discovery" in event_line
    data_content = json.loads(data_line[len("data: "):])
    assert data_content["type"] == "discovery"
    assert data_content["message"] == "Discovery started"
    assert data_content["pages"] == 3


def test_sse_reconnect_last_event_id(client):
    """SSE endpoint should support Last-Event-ID header and query parameter to resume stream."""
    run_id = "test-sse-resume-1"
    create_run(Run(id=run_id, target_url="http://localhost:3000", status="completed"))

    append_event(run_id, "ev1", "Message 1")
    append_event(run_id, "ev2", "Message 2")
    append_event(run_id, "ev3", "Message 3")

    evs = get_events(run_id)
    first_id = evs[0]["event_id"]

    # Reconnect using Last-Event-ID header
    response = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(first_id)})
    assert response.status_code == 200
    body = response.text
    events_raw = [e.strip() for e in body.strip().split("\n\n") if e.strip()]

    # Should only return events with ID > first_id
    assert len(events_raw) == 2
    assert "Message 1" not in body
    assert "Message 2" in body
    assert "Message 3" in body

    # Reconnect using query parameter
    second_id = evs[1]["event_id"]
    response_q = client.get(f"/api/runs/{run_id}/events?last_event_id={second_id}")
    assert response_q.status_code == 200
    body_q = response_q.text
    events_q = [e.strip() for e in body_q.strip().split("\n\n") if e.strip()]
    assert len(events_q) == 1
    assert "Message 3" in body_q


def test_failed_runs_persist_error(client):
    """Ensure failed run records error in SQLite and returns it via API."""
    run_id = "test-run-failed-api"
    run = Run(id=run_id, target_url="http://localhost:3000", status="failed", error="RuntimeError: Connection refused")
    create_run(run)

    res = client.get(f"/api/runs/{run_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "failed"
    assert "RuntimeError: Connection refused" in data["error"]


def test_fix_result_error_persistence():
    """Verify that FixResult.error is properly persisted in SQLite."""
    run_id = "test-fix-err-run"
    create_run(Run(id=run_id, target_url="http://localhost:3000"))

    finding = Finding(
        id="VG-ERR-1", category="security", type="auth", severity="high",
        title="Error finding", target="/", expected="A", actual="B",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    save_finding(run_id, finding)

    fix = FixResult(
        finding_id="VG-ERR-1",
        status="failed",
        attempts=3,
        root_cause="Syntax error in patch generation",
        targeted_test_passed=False,
        regression_passed=False,
        error="LLM syntax parsing error: unexpected token"
    )
    save_fix_result(run_id, fix)

    saved_fix = get_fix_result(run_id, "VG-ERR-1")
    assert saved_fix is not None
    assert saved_fix["status"] == "failed"
    assert saved_fix["error"] == "LLM syntax parsing error: unexpected token"


def test_sse_live_streaming_until_completion(client):
    """Ensure SSE stream receives newly added events during an active run and terminates upon completion."""
    import threading
    import time

    run_id = "test-sse-live-stream"
    create_run(Run(id=run_id, target_url="http://localhost:3000", status="running"))
    append_event(run_id, "system", "Run started")

    def worker():
        time.sleep(0.15)
        append_event(run_id, "vibe_attack", "Attack started")
        time.sleep(0.15)
        r = get_run(run_id)
        update_run(Run(id=run_id, target_url=r["target_url"], status="completed"))

    t = threading.Thread(target=worker)
    t.start()

    response = client.get(f"/api/runs/{run_id}/events")
    t.join()

    assert response.status_code == 200
    assert "Run started" in response.text
    assert "Attack started" in response.text


def test_api_created_run_persisted_exactly_once(client):
    """Verify API-created Run is persisted in DB exactly once."""
    with patch("app.main.run_scan", new_callable=AsyncMock):
        response = client.post("/api/runs", json={"target_url": "http://localhost:3000"})
        assert response.status_code == 200
        run_id = response.json()["id"]

        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchall()
            assert len(rows) == 1
            row = rows[0]
            # True created_at semantics
            assert row["created_at"] is not None
            assert row["created_at"] > 0
            assert abs(row["created_at"] - row["started_at"]) < 1.0


def test_api_created_run_retains_events_after_scan_begins(client):
    """Verify API-created Run retains all events when the background scan begins and progresses."""
    captured_run = None

    async def fake_scan(target_url, replay=False, run=None):
        nonlocal captured_run
        captured_run = run
        append_event(run.id, "discovery", "Discovery started")
        append_event(run.id, "vibe_attack", "Vibe attack finished")

    with patch("app.main.run_scan", side_effect=fake_scan):
        res = client.post("/api/runs", json={"target_url": "http://localhost:3000"})
        assert res.status_code == 200
        run_id = res.json()["id"]

    # Verify events in SQLite
    evs = get_events(run_id)
    assert len(evs) == 2
    types = [e["type"] for e in evs]
    assert "discovery" in types
    assert "vibe_attack" in types


@pytest.mark.asyncio
async def test_no_duplicate_run_creation():
    """Verify run_scan does NOT call create_run when run is already supplied, but DOES call when run is None."""
    from app.orchestrator import run_scan

    # Case 1: run is supplied (API path) -> create_run MUST NOT be called inside run_scan
    existing_run = Run(id="pre-created-1", target_url="http://localhost:3000")
    create_run(existing_run)

    with patch("app.orchestrator.create_run") as mock_create:
        with patch("app.orchestrator.async_playwright") as mock_pw:
            # Mock playwright to exit cleanly
            mock_pw.return_value.__aenter__.side_effect = RuntimeError("Skip browser")
            res = await run_scan("http://localhost:3000", run=existing_run)
            mock_create.assert_not_called()

    # Case 2: run is None (CLI path) -> create_run MUST be called exactly once
    with patch("app.orchestrator.create_run") as mock_create:
        with patch("app.orchestrator.async_playwright") as mock_pw:
            mock_pw.return_value.__aenter__.side_effect = RuntimeError("Skip browser")
            res = await run_scan("http://localhost:3000", run=None)
            mock_create.assert_called_once()


def test_child_events_and_findings_survive_repeated_repository_operations():
    """Verify that repeated create_run or update_run calls do not cascade-delete child records."""
    run_id = "test-cascade-safety"
    run = Run(id=run_id, target_url="http://localhost:3000", status="running")
    create_run(run)

    # Insert child finding
    f = Finding(
        id="VG-SAFE-1", category="security", type="auth", severity="high",
        title="Safe Finding", target="/", expected="A", actual="B",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    save_finding(run_id, f)

    # Insert child event
    append_event(run_id, "test_stage", "Test event")

    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM findings WHERE run_id=?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0] == 1

    # Call create_run again on the same run (e.g. repeated upsert)
    create_run(run)

    # Verify children were NOT deleted by CASCADE
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM findings WHERE run_id=?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0] == 1

    # Call update_run
    run.status = "completed"
    update_run(run)

    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM findings WHERE run_id=?", (run_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0] == 1


def test_post_runs_returns_before_delayed_scan_completes(client):
    """Verify POST /api/runs returns immediately (< 0.2s) even if the background scan is delayed."""
    import time
    import asyncio

    async def slow_scan(*args, **kwargs):
        await asyncio.sleep(0.5)

    with patch("app.main.run_scan", side_effect=slow_scan):
        t0 = time.time()
        response = client.post("/api/runs", json={"target_url": "http://localhost:3000"})
        duration = time.time() - t0

        assert response.status_code == 200
        assert response.json()["status"] == "running"
        # Promptly returned before slow_scan completes
        assert duration < 0.2
