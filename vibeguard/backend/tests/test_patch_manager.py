import pytest
from pathlib import Path
from app.models import StructuredPatch, PatchOperation
from app.patch_manager import PatchManager

@pytest.fixture
def mock_workspace(tmp_path):
    demo_app = tmp_path / "demo-app"
    demo_app.mkdir()
    server_js = demo_app / "server.js"
    server_js.write_text("app.get('/dashboard', (req, res) => {\n  res.send('Dashboard');\n});\n", encoding="utf-8")
    return tmp_path

def test_patch_manager_apply(mock_workspace):
    pm = PatchManager(mock_workspace)
    patch = StructuredPatch(
        file="demo-app/server.js",
        operations=[
            PatchOperation(
                old_text="app.get('/dashboard', (req, res) => {",
                new_text="app.get('/dashboard', (req, res) => {\n  if(!req.user) return res.redirect('/login');"
            )
        ]
    )
    diff = pm.apply_patch(patch)
    content = (mock_workspace / "demo-app/server.js").read_text(encoding="utf-8")
    assert "redirect('/login')" in content
    assert "--- a/demo-app/server.js" in diff
    assert "+++ b/demo-app/server.js" in diff
    assert "+  if(!req.user) return res.redirect('/login');" in diff

def test_patch_manager_diff_generation(mock_workspace):
    pm = PatchManager(mock_workspace)
    patch = StructuredPatch(
        file="demo-app/server.js",
        operations=[
            PatchOperation(
                old_text="res.send('Dashboard');",
                new_text="res.send('Patched Dashboard');"
            )
        ]
    )
    diff = pm.apply_patch(patch)
    assert "-  res.send('Dashboard');" in diff
    assert "+  res.send('Patched Dashboard');" in diff

def test_patch_manager_rollback_and_no_bak_files(mock_workspace):
    pm = PatchManager(mock_workspace)
    original_text = (mock_workspace / "demo-app/server.js").read_text(encoding="utf-8")
    patch = StructuredPatch(
        file="demo-app/server.js",
        operations=[
            PatchOperation(
                old_text="Dashboard",
                new_text="Hacked"
            )
        ]
    )
    pm.apply_patch(patch)
    assert "Hacked" in (mock_workspace / "demo-app/server.js").read_text(encoding="utf-8")
    
    # Confirm NO .bak files were created inside the mock_workspace
    bak_files = list(mock_workspace.glob("**/*.bak"))
    assert len(bak_files) == 0

    pm.rollback()
    restored_text = (mock_workspace / "demo-app/server.js").read_text(encoding="utf-8")
    assert restored_text == original_text
    assert "Hacked" not in restored_text

def test_patch_manager_safe_resolve_traversal(mock_workspace):
    pm = PatchManager(mock_workspace)
    patch = StructuredPatch(
        file="../outside.js",
        operations=[PatchOperation(old_text="a", new_text="b")]
    )
    with pytest.raises(PermissionError):
        pm.apply_patch(patch)

def test_patch_manager_ambiguous(mock_workspace):
    (mock_workspace / "demo-app/server.js").write_text("function a() { return 1; }\nfunction b() { return 1; }", encoding="utf-8")
    pm = PatchManager(mock_workspace)
    patch = StructuredPatch(
        file="demo-app/server.js",
        operations=[PatchOperation(old_text="return 1;", new_text="return 2;", expected_matches=1)]
    )
    with pytest.raises(ValueError, match="Patch ambiguity"):
        pm.apply_patch(patch)
    
    # Verify file was not modified
    content = (mock_workspace / "demo-app/server.js").read_text(encoding="utf-8")
    assert "return 2;" not in content

def test_patch_manager_nonexistent_file(mock_workspace):
    pm = PatchManager(mock_workspace)
    patch = StructuredPatch(
        file="demo-app/missing.js",
        operations=[PatchOperation(old_text="a", new_text="b")]
    )
    with pytest.raises(FileNotFoundError):
        pm.apply_patch(patch)

def test_failed_patch_attempt_rollback_and_retry(mock_workspace):
    file_path = mock_workspace / "demo-app/server.js"
    original_text = file_path.read_text(encoding="utf-8")
    
    # Attempt 1 fails
    pm1 = PatchManager(mock_workspace)
    bad_patch = StructuredPatch(
        file="demo-app/server.js",
        operations=[PatchOperation(old_text="Dashboard", new_text="Broken1")]
    )
    pm1.apply_patch(bad_patch)
    assert "Broken1" in file_path.read_text(encoding="utf-8")
    # Simulate test failure -> rollback
    pm1.rollback()
    assert file_path.read_text(encoding="utf-8") == original_text

    # Attempt 2 succeeds
    pm2 = PatchManager(mock_workspace)
    good_patch = StructuredPatch(
        file="demo-app/server.js",
        operations=[PatchOperation(old_text="Dashboard", new_text="Fixed2")]
    )
    diff = pm2.apply_patch(good_patch)
    assert "Fixed2" in file_path.read_text(encoding="utf-8")
    assert "-  res.send('Dashboard');" in diff
    assert "+  res.send('Fixed2');" in diff
    pm2.cleanup()

    # Workspace has no .bak files
    assert list(mock_workspace.glob("**/*.bak")) == []

