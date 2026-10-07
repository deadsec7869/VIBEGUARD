import pytest
from pathlib import Path
from app.models import Finding, Run, AttackScenario, Evidence, Step
from app.verification_agent import run_verification_agent
from pydantic import BaseModel

class DummyEmit:
    def __call__(self, stage, msg, level="info"):
        pass

@pytest.fixture
def dummy_run():
    return Run(
        id="dummy_run",
        target_url="http://localhost:3000",
        tests=[
            AttackScenario(
                id="TEST-1",
                category="authentication",
                title="Direct protected-route access",
                goal="Check /dashboard",
                steps=[Step(action="navigate", value="/dashboard"), Step(action="expect_url", value="/login")],
                expected_behavior="Redirect to /login",
                severity_if_failed="high",
                rationale="Security"
            ),
            AttackScenario(
                id="TEST-2",
                category="authentication",
                title="Logout",
                goal="Check /logout",
                steps=[Step(action="navigate", value="/logout"), Step(action="expect_url", value="/login")],
                expected_behavior="Redirect to /login",
                severity_if_failed="high",
                rationale="Security"
            )
        ]
    )

@pytest.fixture
def dummy_finding():
    return Finding(
        id="VG-001",
        category="authentication",
        type="authentication",
        severity="high",
        title="Direct protected-route access",
        target="/dashboard",
        expected="Redirect to /login",
        actual="Stayed on /dashboard",
        reproducible=True,
        confidence=1.0,
        evidence=Evidence(screenshots=["TEST-1.png"]),
        steps=[Step(action="navigate", value="/dashboard"), Step(action="expect_url", value="/login")],
        reproduction_steps=["1. navigate", "2. expect_url", "Result: Stayed on /dashboard"]
    )

@pytest.mark.asyncio
async def test_successful_verification(tmp_path, dummy_run, dummy_finding, monkeypatch):
    workspace = tmp_path
    
    # Mock run_test to always pass
    from app import verification_agent
    
    async def mock_run_test(*args, **kwargs):
        from app.models import TestResult, Evidence
        return TestResult(test_id="VERIFY-VG-001", status="passed", runs=1, failures=0, duration_ms=10, evidence=Evidence())
    
    async def mock_execute_all(*args, **kwargs):
        return []
    
    monkeypatch.setattr(verification_agent, "run_test", mock_run_test)
    monkeypatch.setattr(verification_agent, "execute_all", mock_execute_all)
    
    class MockProcessManager:
        def __init__(self, *args): pass
        def restart(self): pass
        def stop(self): pass
        
    monkeypatch.setattr(verification_agent, "TargetProcessManager", MockProcessManager)
    
    # Mock playwright
    class MockBrowser:
        async def new_context(self, *args, **kwargs): return self
        async def new_page(self): return self
        async def close(self): pass
        async def launch(self, **kwargs): return self
        
    class MockPlaywright:
        @property
        def chromium(self): return MockBrowser()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        
    def mock_async_playwright():
        return MockPlaywright()
        
    monkeypatch.setattr(verification_agent, "async_playwright", mock_async_playwright)
    
    # Run
    res = await run_verification_agent(dummy_finding, dummy_run, workspace, "echo 1", DummyEmit())
    
    assert res.status == "verified"
    assert res.targeted_test_passed is True
    assert res.regression_passed is True
    assert res.confidence == 1.0

@pytest.mark.asyncio
async def test_verification_rejection(tmp_path, dummy_run, dummy_finding, monkeypatch):
    workspace = tmp_path
    
    from app import verification_agent
    async def mock_run_test(*args, **kwargs):
        from app.models import TestResult, Evidence
        return TestResult(test_id="VERIFY-VG-001", status="failed", runs=1, failures=1, duration_ms=10, evidence=Evidence())
    
    monkeypatch.setattr(verification_agent, "run_test", mock_run_test)
    
    class MockProcessManager:
        def __init__(self, *args): pass
        def restart(self): pass
        def stop(self): pass
    monkeypatch.setattr(verification_agent, "TargetProcessManager", MockProcessManager)
    
    class MockBrowser:
        async def launch(self, **kwargs): return self
    class MockPlaywright:
        @property
        def chromium(self): return MockBrowser()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    def mock_async_playwright(): return MockPlaywright()
    monkeypatch.setattr(verification_agent, "async_playwright", mock_async_playwright)
    
    res = await run_verification_agent(dummy_finding, dummy_run, workspace, "echo 1", DummyEmit())
    
    assert res.status == "rejected"
    assert res.targeted_test_passed is False

@pytest.mark.asyncio
async def test_stale_fixresult_ignored(tmp_path, dummy_run, dummy_finding, monkeypatch):
    # Verification should ignore existing fix_result
    from app.models import FixResult
    dummy_finding.fix_result = FixResult(finding_id="VG-001", status="fixed", targeted_test_passed=True, regression_passed=True, verification_status="pending")
    dummy_finding.verification_status = "pending"
    
    workspace = tmp_path
    from app import verification_agent
    
    async def mock_run_test(*args, **kwargs):
        from app.models import TestResult, Evidence
        return TestResult(test_id="VERIFY-VG-001", status="failed", runs=1, failures=1, duration_ms=10, evidence=Evidence())
    monkeypatch.setattr(verification_agent, "run_test", mock_run_test)
    
    class MockProcessManager:
        def __init__(self, *args): pass
        def restart(self): pass
        def stop(self): pass
    monkeypatch.setattr(verification_agent, "TargetProcessManager", MockProcessManager)
    
    class MockBrowser:
        async def launch(self, **kwargs): return self
    class MockPlaywright:
        @property
        def chromium(self): return MockBrowser()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    def mock_async_playwright(): return MockPlaywright()
    monkeypatch.setattr(verification_agent, "async_playwright", mock_async_playwright)
    
    res = await run_verification_agent(dummy_finding, dummy_run, workspace, "echo 1", DummyEmit())
    
    # Despite FixResult saying "fixed", verifier still rejects it because runtime failed.
    assert res.status == "rejected"

