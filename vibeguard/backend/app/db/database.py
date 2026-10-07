import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DB_PATH = Path(os.getenv("VIBEGUARD_ARTIFACTS", ROOT / "artifacts")) / "vibeguard.db"

def get_db_path() -> Path:
    db_path = os.getenv("VIBEGUARD_DB_PATH")
    if db_path:
        return Path(db_path)
    return DEFAULT_DB_PATH

@contextmanager
def get_connection(db_path: Path = None):
    if db_path is None:
        db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    # Enable foreign keys and use WAL for better concurrency
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
