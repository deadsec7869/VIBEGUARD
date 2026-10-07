import time
from pathlib import Path
from playwright.async_api import async_playwright

from .models import Finding, Run, VerificationResult, AttackScenario, TestResult
from .process import TargetProcessManager
from .executor import execute_all, run_test

async def run_verification_agent(
    finding: Finding,
    run: Run,
    workspace_dir: Path,
    restart_cmd: str,
    emit
) -> VerificationResult:
    v_result = VerificationResult(
        finding_id=finding.id,
        status="running",
        confidence=0.0
    )
    finding.verification_status = "running"
    finding.verification_result = v_result

    target_pm = TargetProcessManager(restart_cmd, str(workspace_dir / "demo-app"), run.target_url)

    # Reconstruct the targeted test case from the finding
    targeted_test = AttackScenario(
        id=f"VERIFY-{finding.id}",
        category=finding.category,
        title=f"Verification for {finding.title}",
        goal=f"Verify finding {finding.id} is resolved",
        steps=finding.steps,
        expected_behavior=finding.expected
    )

    # 2. Match deterministic relevant regression tests
    same_category_tests = [t for t in run.tests if t.category == finding.category and t.title != finding.title]
    baseline_tests = [t for t in run.tests if t not in same_category_tests and ("FUNC" in t.id or "baseline" in t.id.lower())]
    regression_tests = same_category_tests + baseline_tests[:1]
    if not regression_tests and run.tests:
        regression_tests = run.tests[:3]
    # filter out tests that perfectly match the targeted test
    regression_tests = [t for t in regression_tests if not (len(t.steps) == len(finding.steps) and all(s1.action == s2.action and s1.value == s2.value for s1, s2 in zip(t.steps, finding.steps)))]

    try:
        emit("verifier", "Starting target application for independent verification...")
        target_pm.restart()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            art_dir = workspace_dir / "artifacts" / "verification"
            art_dir.mkdir(parents=True, exist_ok=True)
            
            emit("verifier", f"Running fresh independent reproduction of {finding.id}...")
            r = await run_test(browser, run.target_url, targeted_test, art_dir)
            
            v_result.evidence = r.evidence
            v_result.reproduction_steps = finding.reproduction_steps
            v_result.expected = r.expected or finding.expected
            v_result.actual = r.actual or ""
            
            if r.status != "passed":
                emit("verifier", f"Targeted reproduction failed (bug is still present): {r.status} (expected '{r.expected}', got '{r.actual}')", "warn")
                v_result.targeted_test_passed = False
                v_result.status = "rejected"
                v_result.verifier_reason = f"The issue is still reproducible. Actual behavior: {r.actual}"
                finding.verification_status = v_result.status
                target_pm.stop()
                return v_result
            
            v_result.targeted_test_passed = True
            emit("verifier", "Targeted reproduction PASSED (bug no longer present)!", "info")
            
            emit("verifier", f"Running regression test suite ({len(regression_tests)} tests)...")
            rr = await execute_all(browser, run.target_url, regression_tests, art_dir, emit)
            
            failed_regs = [rx for rx in rr if rx.status in ("failed", "error")]
            if failed_regs:
                emit("verifier", f"Regression test failed on {failed_regs[0].test_id}", "error")
                v_result.regression_passed = False
                v_result.status = "rejected"
                v_result.verifier_reason = f"Fix caused a regression on test {failed_regs[0].test_id}."
                finding.verification_status = v_result.status
                target_pm.stop()
                return v_result
            
            v_result.regression_passed = True
            emit("verifier", "Regression test suite PASSED!", "info")
            v_result.status = "verified"
            v_result.confidence = 1.0
            v_result.verifier_reason = "Fresh reproduction confirms the bug is fixed and regressions pass."
            v_result.verified_at = time.time()
            finding.verification_status = v_result.status
            
            target_pm.stop()
            return v_result
            
    except Exception as e:
        emit("verifier", f"Verification execution failed: {e}", "error")
        v_result.status = "error"
        v_result.verifier_reason = f"Execution error: {str(e)}"
        finding.verification_status = v_result.status
        target_pm.stop()
        return v_result

