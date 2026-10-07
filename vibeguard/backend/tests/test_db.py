import os
import tempfile
from pathlib import Path

import pytest
import sqlite3

from app.db.schema import init_db
from app.db.database import get_connection, get_db_path
from app.db.repositories import (
    create_run, update_run, save_attack, save_finding,
    save_fix_result, save_verification_result, append_event, update_finding_status,
    get_finding, get_findings, get_run, get_attacks, get_events
)
from app.models import Run, AttackScenario, Finding, Step, Evidence, FixResult, VerificationResult


def test_init_db():
    """Test idempotent initialization."""
    db_path = get_db_path()
    init_db(db_path)  # Call it again
    
    with get_connection(db_path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {t["name"] for t in tables}
        
    assert "runs" in table_names
    assert "attacks" in table_names
    assert "findings" in table_names
    assert "fix_results" in table_names
    assert "verification_results" in table_names
    assert "events" in table_names


def test_run_persistence():
    run = Run(id="run123", target_url="http://localhost:3000", status="running", error=None)
    create_run(run)
    
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id='run123'").fetchone()
        assert row["status"] == "running"
        assert row["target_url"] == "http://localhost:3000"
        
    run.status = "completed"
    run.error = "test error"
    update_run(run)
    
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id='run123'").fetchone()
        assert row["status"] == "completed"
        assert row["error"] == "test error"


def test_full_pipeline_persistence():
    run_id = "run456"
    run = Run(id=run_id, target_url="http://localhost:3000")
    create_run(run)
    
    # Attack
    attack = AttackScenario(
        id="ATK-1", category="functional", title="Test Atk",
        goal="Do bad", expected_behavior="Block", severity_if_failed="high",
        steps=[Step(action="navigate", value="/")]
    )
    save_attack(run_id, attack, "failed")
    
    # Finding
    finding = Finding(
        id="VG-001", attack_id="ATK-1", category="functional", type="auth", severity="high",
        title="Test Finding", target="/", expected="A", actual="B",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    save_finding(run_id, finding)
    
    # Fix Result
    fix = FixResult(
        finding_id="VG-001", status="fixed", attempts=1, root_cause="missing auth",
        diff="--- a\n+++ b", targeted_test_passed=True, regression_passed=True
    )
    save_fix_result(run_id, fix)
    update_finding_status(run_id, "VG-001", "fixed", "pending")
    
    # Verification Result
    ver = VerificationResult(
        finding_id="VG-001", status="verified", confidence=1.0, verifier_reason="Looks good",
        targeted_test_passed=True, regression_passed=True
    )
    save_verification_result(run_id, ver)
    update_finding_status(run_id, "VG-001", "fixed", "verified")
    
    # Events
    append_event(run_id, "system", "Test complete")
    
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attacks").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fix_results").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM verification_results").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        
        row = conn.execute("SELECT status, verification_status FROM findings WHERE finding_id='VG-001'").fetchone()
        assert row["status"] == "fixed"
        assert row["verification_status"] == "verified"


def test_attack_finding_relationship():
    """Verify that Finding retains its originating AttackScenario ID."""
    run_id = "run-link-1"
    attack_id = "ATK-SEC-001"
    create_run(Run(id=run_id, target_url="http://localhost:3000"))
    
    attack = AttackScenario(
        id=attack_id, category="security", title="SQL Injection Test",
        goal="Bypass auth", expected_behavior="Reject input", severity_if_failed="critical",
        steps=[Step(action="navigate", value="/login")]
    )
    save_attack(run_id, attack, "failed")
    
    finding = Finding(
        id="VG-001", attack_id=attack_id, category="security", type="sqli", severity="critical",
        title="SQLi Found", target="/login", expected="Reject", actual="Bypassed",
        reproducible=True, confidence=0.95, evidence=Evidence(), steps=[]
    )
    save_finding(run_id, finding, attack_id=attack_id)
    
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM findings WHERE run_id=? AND finding_id=?", (run_id, "VG-001")).fetchone()
        assert row is not None
        assert row["attack_id"] == attack_id
        
        # Verify relation to attacks table
        atk_row = conn.execute("SELECT * FROM attacks WHERE run_id=? AND attack_id=?", (run_id, row["attack_id"])).fetchone()
        assert atk_row is not None
        assert atk_row["attack_id"] == attack_id


def test_run_scoped_findings_isolation():
    """Ensure identical finding IDs (e.g. VG-001) in separate runs do not collide or mix."""
    r1, r2 = "run-iso-1", "run-iso-2"
    create_run(Run(id=r1, target_url="http://localhost:3000"))
    create_run(Run(id=r2, target_url="http://localhost:3000"))
    
    f1 = Finding(
        id="VG-001", attack_id="ATK-1", category="security", type="auth", severity="high",
        title="Run 1 Finding", target="/r1", expected="E1", actual="A1",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    f2 = Finding(
        id="VG-001", attack_id="ATK-2", category="functionality", type="nav", severity="low",
        title="Run 2 Finding", target="/r2", expected="E2", actual="A2",
        reproducible=True, confidence=0.8, evidence=Evidence(), steps=[]
    )
    save_finding(r1, f1)
    save_finding(r2, f2)
    
    # Test scoped queries
    r1_finding = get_finding(r1, "VG-001")
    r2_finding = get_finding(r2, "VG-001")
    
    assert r1_finding is not None
    assert r2_finding is not None
    assert r1_finding["title"] == "Run 1 Finding"
    assert r2_finding["title"] == "Run 2 Finding"
    assert r1_finding["attack_id"] == "ATK-1"
    assert r2_finding["attack_id"] == "ATK-2"
    
    r1_list = get_findings(r1)
    r2_list = get_findings(r2)
    assert len(r1_list) == 1
    assert len(r2_list) == 1
    assert r1_list[0]["title"] == "Run 1 Finding"
    assert r2_list[0]["title"] == "Run 2 Finding"


def test_fix_and_verification_status_independence():
    """Ensure fix_status and verification_status persist independently without conflation."""
    run_id = "run-status-1"
    create_run(Run(id=run_id, target_url="http://localhost:3000"))
    
    finding = Finding(
        id="VG-001", category="security", type="auth", severity="high",
        title="Status Test Finding", target="/", expected="A", actual="B",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[],
        fix_status="pending", verification_status="pending"
    )
    save_finding(run_id, finding)
    
    # 1. Initial state: both pending
    f_db = get_finding(run_id, "VG-001")
    assert f_db["status"] == "pending"
    assert f_db["verification_status"] == "pending"
    
    # 2. Slice 3 Fix Agent succeeds: fix_status = fixed, verification_status remains pending!
    # Slice 3 must NEVER claim verification.
    update_finding_status(run_id, "VG-001", fix_status="fixed", verification_status="pending")
    f_db = get_finding(run_id, "VG-001")
    assert f_db["status"] == "fixed"
    assert f_db["verification_status"] == "pending"
    
    # 3. Slice 4 Verification Agent independently verifies: verification_status = verified
    update_finding_status(run_id, "VG-001", fix_status="fixed", verification_status="verified")
    f_db = get_finding(run_id, "VG-001")
    assert f_db["status"] == "fixed"
    assert f_db["verification_status"] == "verified"
    
    # 4. If Verification Agent rejects: verification_status = rejected
    update_finding_status(run_id, "VG-001", fix_status="fixed", verification_status="rejected")
    f_db = get_finding(run_id, "VG-001")
    assert f_db["status"] == "fixed"
    assert f_db["verification_status"] == "rejected"


def test_fix_result_verification_status_contract():
    """Ensure FixResult verification_status contract remains 'pending' on fix."""
    run_id = "run-fix-contract"
    create_run(Run(id=run_id, target_url="http://localhost:3000"))
    
    finding = Finding(
        id="VG-001", category="security", type="auth", severity="high",
        title="Fix Contract Finding", target="/", expected="A", actual="B",
        reproducible=True, confidence=1.0, evidence=Evidence(), steps=[]
    )
    save_finding(run_id, finding)
    
    fix = FixResult(
        finding_id="VG-001", status="fixed", attempts=1, root_cause="input validation",
        diff="--- a\n+++ b", targeted_test_passed=True, regression_passed=True,
        verification_status="pending"
    )
    save_fix_result(run_id, fix)
    
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM fix_results WHERE run_id=? AND finding_id=?", (run_id, "VG-001")).fetchone()
        assert row["status"] == "fixed"
        assert row["targeted_test_passed"] == 1
        assert row["regression_passed"] == 1
        assert row["verification_status"] == "pending"
