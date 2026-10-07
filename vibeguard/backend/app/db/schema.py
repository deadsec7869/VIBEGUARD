import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    target_url TEXT NOT NULL,
    status TEXT NOT NULL,
    plan_source TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    started_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS attacks (
    attack_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    expected_behavior TEXT NOT NULL,
    severity_if_failed TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, attack_id),
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    attack_id TEXT,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    expected TEXT NOT NULL,
    actual TEXT NOT NULL,
    reproducible BOOLEAN NOT NULL,
    confidence REAL NOT NULL,
    evidence_json TEXT,
    reproduction_steps_json TEXT,
    status TEXT NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, finding_id),
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fix_results (
    finding_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    root_cause TEXT,
    files_changed_json TEXT,
    patch_summary TEXT,
    diff TEXT,
    patch_operations_json TEXT,
    targeted_test_passed BOOLEAN NOT NULL,
    regression_passed BOOLEAN NOT NULL,
    verification_status TEXT NOT NULL,
    error TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, finding_id),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings (run_id, finding_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verification_results (
    finding_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL,
    verifier_reason TEXT,
    targeted_test_passed BOOLEAN NOT NULL,
    regression_passed BOOLEAN NOT NULL,
    evidence_json TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (run_id, finding_id),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings (run_id, finding_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    type TEXT NOT NULL,
    timestamp REAL NOT NULL,
    payload_json TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE
);
"""

def init_db(db_path: Path):
    """Idempotent database initialization."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA)
        # Migrate existing database if column is missing
        cursor = conn.execute("PRAGMA table_info(findings)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "verification_status" not in columns:
            conn.execute("ALTER TABLE findings ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'pending'")
        conn.commit()
