import json
import time
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from .database import get_connection
from ..models import Run, AttackScenario, Finding, FixResult, VerificationResult, Event

def _json_dump(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump_json()
    return json.dumps(obj)

def _json_load(data: Optional[str]) -> Any:
    if data is None:
        return None
    return json.loads(data)

# --- RUNS ---
def create_run(run: Run):
    created_at = getattr(run, "created_at", None) or run.started_at
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO runs (run_id, target_url, status, plan_source, error, created_at, started_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                   target_url=excluded.target_url,
                   status=excluded.status,
                   plan_source=excluded.plan_source,
                   error=excluded.error,
                   completed_at=excluded.completed_at""",
            (run.id, run.target_url, run.status, run.plan_source, run.error, created_at, run.started_at, run.finished_at)
        )
        conn.commit()

def update_run(run: Run):
    with get_connection() as conn:
        conn.execute(
            """UPDATE runs SET status=?, plan_source=?, error=?, completed_at=? WHERE run_id=?""",
            (run.status, run.plan_source, run.error, run.finished_at, run.id)
        )
        conn.commit()

def get_runs() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM runs ORDER BY created_at DESC"""
        ).fetchall()
        runs = []
        for r in rows:
            d = dict(r)
            d["id"] = d["run_id"]
            runs.append(d)
        return runs

# --- ATTACKS ---
def save_attack(run_id: str, attack: AttackScenario, status: str = "pending"):
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO attacks (attack_id, run_id, category, title, goal, expected_behavior, severity_if_failed, payload_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (attack.id, run_id, attack.category, attack.title, attack.goal, attack.expected_behavior, attack.severity_if_failed, _json_dump(attack), status, time.time())
        )
        conn.commit()

# --- FINDINGS ---
def save_finding(run_id: str, finding: Finding, attack_id: Optional[str] = None):
    eff_attack_id = attack_id if attack_id is not None else getattr(finding, "attack_id", None)
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO findings (
                finding_id, run_id, attack_id, category, severity, title, expected, actual,
                reproducible, confidence, evidence_json, reproduction_steps_json,
                status, verification_status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                finding.id,
                run_id,
                eff_attack_id,
                finding.category,
                finding.severity,
                finding.title,
                finding.expected,
                finding.actual,
                finding.reproducible,
                finding.confidence,
                _json_dump(finding.evidence),
                _json_dump(finding.reproduction_steps),
                finding.fix_status,
                finding.verification_status,
                time.time()
            )
        )
        conn.commit()

def update_finding_status(run_id: str, finding_id: str, fix_status: str, verification_status: str):
    with get_connection() as conn:
        conn.execute(
            """UPDATE findings SET status=?, verification_status=? WHERE run_id=? AND finding_id=?""",
            (fix_status, verification_status, run_id, finding_id)
        )
        conn.commit()

def get_finding(run_id: str, finding_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM findings WHERE run_id=? AND finding_id=?""",
            (run_id, finding_id)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["id"] = d["finding_id"]
        if d.get("evidence_json"):
            d["evidence"] = _json_load(d["evidence_json"])
        if d.get("reproduction_steps_json"):
            d["reproduction_steps"] = _json_load(d["reproduction_steps_json"])
        return d

def get_findings(run_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM findings WHERE run_id=?""",
            (run_id,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["id"] = d["finding_id"]
            if d.get("evidence_json"):
                d["evidence"] = _json_load(d["evidence_json"])
            if d.get("reproduction_steps_json"):
                d["reproduction_steps"] = _json_load(d["reproduction_steps_json"])
            results.append(d)
        return results

# --- FIX RESULTS ---
def save_fix_result(run_id: str, fix: FixResult):
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO fix_results (finding_id, run_id, status, attempts, root_cause, files_changed_json, patch_summary, diff, patch_operations_json, targeted_test_passed, regression_passed, verification_status, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fix.finding_id, run_id, fix.status, fix.attempts, fix.root_cause, _json_dump(fix.files_changed), fix.patch_summary, fix.diff, _json_dump(fix.patch_operations), fix.targeted_test_passed, fix.regression_passed, fix.verification_status, getattr(fix, "error", None), time.time())
        )
        conn.commit()

def get_fix_result(run_id: str, finding_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM fix_results WHERE run_id=? AND finding_id=?""",
            (run_id, finding_id)
        ).fetchone()
        return dict(row) if row else None

# --- VERIFICATION RESULTS ---
def save_verification_result(run_id: str, v: VerificationResult):
    with get_connection() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO verification_results (finding_id, run_id, status, confidence, verifier_reason, targeted_test_passed, regression_passed, evidence_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (v.finding_id, run_id, v.status, v.confidence, v.verifier_reason, v.targeted_test_passed, v.regression_passed, _json_dump(v.evidence), v.verified_at or time.time())
        )
        conn.commit()

# --- EVENTS ---
def append_event(run_id: str, event_type: str, message: str, payload: Dict[str, Any] = None):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO events (run_id, type, timestamp, payload_json) VALUES (?, ?, ?, ?)""",
            (run_id, event_type, time.time(), _json_dump({"message": message, **(payload or {})}))
        )
        conn.commit()

def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM runs WHERE run_id=?""",
            (run_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["id"] = d["run_id"]
        return d

def get_attacks(run_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM attacks WHERE run_id=?""",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_events(run_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM events WHERE run_id=? ORDER BY event_id ASC""",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_events_after(run_id: str, last_event_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM events WHERE run_id=? AND event_id > ? ORDER BY event_id ASC""",
            (run_id, last_event_id)
        ).fetchall()
        return [dict(r) for r in rows]
