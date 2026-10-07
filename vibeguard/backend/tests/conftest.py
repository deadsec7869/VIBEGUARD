import os
import tempfile
import pytest
from pathlib import Path
from app.db.schema import init_db

@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["VIBEGUARD_DB_PATH"] = path
    init_db(Path(path))
    
    yield
    
    try:
        os.remove(path)
    except Exception:
        pass
