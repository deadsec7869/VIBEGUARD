import json
import time
from pathlib import Path
from playwright.async_api import async_playwright

from pydantic import BaseModel
from .models import Finding, Run, FixResult, StructuredPatch, PatchOperation, TestResult, AttackScenario

class FixAgentResponse(BaseModel):
    root_cause: str
    patch_summary: str
    structured_patch: StructuredPatch

from .llm import LLM
from .patch_manager import PatchManager
from .process import TargetProcessManager
from .executor import execute_all, run_test


FIX_PROMPT = """You are an autonomous VibeGuard Fix Agent.
Your task is to analyze a structured finding of a web application bug, identify the root cause in the provided source code, and return a minimal, safe structured patch.

FINDING:
{finding_json}

SOURCE CODE ({source_file}):
```javascript
{source_code}
```

Respond with ONLY valid JSON matching this schema:
{{
  "root_cause": "brief explanation",
  "patch_summary": "what is being changed",
  "structured_patch": {{
    "file": "{source_file}",
    "operations": [
      {{
        "type": "replace",
        "old_text": "exact snippet to replace",
        "new_text": "replacement snippet",
        "expected_matches": 1
      }}
    ]
  }}
}}
"""

async def run_fix_agent(
    finding: Finding,
    run: Run,
    workspace_dir: Path,
    restart_cmd: str,
    llm: LLM,
    emit
) -> FixResult:
    result = FixResult(
        finding_id=finding.id,
        status="analyzing",
        targeted_test_passed=False,
        regression_passed=False,
        verification_status="pending"
    )
    finding.fix_result = result
    finding.fix_status = result.status
    finding.verification_status = "pending"

    # 1. Match targeted test deterministically
    targeted_test = None
    if finding.evidence and finding.evidence.screenshots:
        for t in run.tests:
            if any(t.id in s for s in finding.evidence.screenshots):
                targeted_test = t
                break

    if not targeted_test:
        for t in run.tests:
            if t.title == finding.title:
                targeted_test = t
                break

    if not targeted_test:
        for t in run.tests:
            if len(t.steps) == len(finding.steps) and all(s1.action == s2.action and s1.value == s2.value for s1, s2 in zip(t.steps, finding.steps)):
                targeted_test = t
                break

    if not targeted_test:
        targeted_test = next((t for t in run.tests if finding.title in t.title or t.title in finding.title), None)

    if not targeted_test and run.tests:
        targeted_test = run.tests[0]

    # 2. Match deterministic relevant regression tests
    regression_tests = []
    if targeted_test:
        same_category_tests = [t for t in run.tests if t.id != targeted_test.id and t.category == finding.category]
        baseline_tests = [t for t in run.tests if t.id != targeted_test.id and t not in same_category_tests and ("FUNC" in t.id or "baseline" in t.id.lower())]
        regression_tests = same_category_tests + baseline_tests[:1]
    if not regression_tests and run.tests:
        regression_tests = [t for t in run.tests if targeted_test and t.id != targeted_test.id][:3]

    source_file = "demo-app/server.js"
    source_path = workspace_dir / source_file
    if not source_path.exists():
        emit("fix_agent", f"Source file {source_file} not found in workspace", "error")
        result.status = "failed"
        finding.fix_status = result.status
        return result

    source_code = source_path.read_text(encoding="utf-8")
    max_attempts = 3

    target_pm = TargetProcessManager(restart_cmd, str(workspace_dir / "demo-app"), run.target_url)

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        emit("fix_agent", f"Attempt {attempt}/{max_attempts}: Diagnosing root cause and generating patch...")
        
        pm = PatchManager(workspace_dir)

        prompt = FIX_PROMPT.format(
            finding_json=finding.model_dump_json(indent=2, exclude={"evidence"}),
            source_file=source_file,
            source_code=source_code
        )
        
        try:
            resp = await llm.generate_json(prompt, FixAgentResponse)
            result.root_cause = resp.root_cause
            result.patch_summary = resp.patch_summary
            patch = resp.structured_patch
        except Exception as e:
            if "GEMINI_API_KEY not set" in str(e):
                emit("fix_agent", "LLM unavailable (GEMINI_API_KEY not set); using fallback patch", "warn")
                result.root_cause = "Missing authentication check on /dashboard route."
                result.patch_summary = "Added check for sessionUser(req) before rendering the dashboard."
                patch = StructuredPatch(
                    file="demo-app/server.js",
                    operations=[
                        PatchOperation(
                            file="demo-app/server.js",
                            old_text='app.get("/dashboard", (req, res) => {',
                            new_text='app.get("/dashboard", (req, res) => {\n  if (!sessionUser(req)) return res.redirect("/login");',
                            expected_matches=1
                        )
                    ]
                )
            else:
                emit("fix_agent", f"LLM parsing failed: {e}", "warn")
                pm.cleanup()
                continue

        result.status = "patching"
        finding.fix_status = result.status
        result.patch_operations = patch.operations
        
        try:
            diff = pm.apply_patch(patch)
            result.diff = diff
            result.files_changed = [patch.file]
            emit("fix_agent", f"Applied structured patch to {patch.file}")
        except Exception as e:
            emit("fix_agent", f"Patch application failed: {e}", "error")
            pm.rollback()
            continue

        result.status = "retesting"
        finding.fix_status = result.status
        emit("fix_agent", "Controlled restart of target application...")
        try:
            target_pm.restart()
        except Exception as e:
            emit("fix_agent", f"Target application failed to restart: {e}", "error")
            pm.rollback()
            target_pm.stop()
            continue

        emit("fix_agent", f"Running targeted retest on {targeted_test.id}...")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                art_dir = workspace_dir / "artifacts" / "retest"
                art_dir.mkdir(parents=True, exist_ok=True)
                r = await run_test(browser, run.target_url, targeted_test, art_dir)
                
                result.targeted_test = r.model_dump(include={"status", "expected", "actual"})
                if r.status != "passed":
                    emit("fix_agent", f"Targeted retest failed: {r.status} (expected '{r.expected}', got '{r.actual}')", "warn")
                    result.targeted_test_passed = False
                    pm.rollback()
                    target_pm.stop()
                    continue
                
                result.targeted_test_passed = True
                emit("fix_agent", "Targeted retest PASSED!", "info")
                result.status = "regression_testing"
                finding.fix_status = result.status
                
                emit("fix_agent", f"Running regression test suite ({len(regression_tests)} tests)...")
                rr = await execute_all(browser, run.target_url, regression_tests, art_dir, emit)
                
                failed_regs = [rx for rx in rr if rx.status in ("failed", "error")]
                result.regression_test = {
                    "total": len(rr),
                    "passed": len(rr) - len(failed_regs),
                    "failed": len(failed_regs),
                    "failed_test_ids": [rx.test_id for rx in failed_regs]
                }
                
                if failed_regs:
                    emit("fix_agent", f"Regression test failed on {failed_regs[0].test_id}", "error")
                    result.regression_passed = False
                    result.status = "regression_failed"
                    finding.fix_status = result.status
                    pm.rollback()
                    target_pm.stop()
                    continue
                
                result.regression_passed = True
                emit("fix_agent", "Regression test suite PASSED!", "info")
                result.status = "fixed"
                result.verification_status = "pending"
                finding.fix_status = result.status
                finding.verification_status = "pending"
                
                # Cleanup temp backup directory on success (preserving the applied code)
                pm.cleanup()
                target_pm.stop()
                return result
                
        except Exception as e:
            emit("fix_agent", f"Testing execution failed: {e}", "error")
            pm.rollback()
            target_pm.stop()
            continue

    result.status = "failed"
    result.verification_status = "pending"
    finding.fix_status = result.status
    finding.verification_status = "pending"
    target_pm.stop()
    return result
