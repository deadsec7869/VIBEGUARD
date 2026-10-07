import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import (
    Action,
    AppMap,
    AttackScenario,
    Element,
    Evidence,
    Page,
    Step,
    TestResult,
)
from app.orchestrator import assert_safe_target
from app.planner import _validate_attack_scenarios, fallback_attack_plan
from app.findings import build_findings


def make_mock_app_map() -> AppMap:
    login_page = Page(
        path="/login",
        final_path="/login",
        status=200,
        title="Login",
        elements=[
            Element(role="link", name="Login", tag="a", target="role:link:Login", href="/login"),
            Element(role="link", name="Register", tag="a", target="role:link:Register", href="/register"),
            Element(role="link", name="Dashboard", tag="a", target="role:link:Dashboard", href="/dashboard"),
            Element(role="field", name="Email", tag="input", target="label:Email", type="email"),
            Element(role="field", name="Password", tag="input", target="label:Password", type="password"),
            Element(role="button", name="Sign in", tag="button", target="role:button:Sign in"),
        ],
    )
    register_page = Page(
        path="/register",
        final_path="/register",
        status=200,
        title="Register",
        elements=[
            Element(role="link", name="Login", tag="a", target="role:link:Login", href="/login"),
            Element(role="link", name="Register", tag="a", target="role:link:Register", href="/register"),
            Element(role="link", name="Dashboard", tag="a", target="role:link:Dashboard", href="/dashboard"),
            Element(role="field", name="Name", tag="input", target="label:Name", type="text"),
            Element(role="field", name="Email", tag="input", target="label:Email", type="email"),
            Element(role="button", name="Register", tag="button", target="role:button:Register"),
        ],
    )
    dashboard_page = Page(
        path="/dashboard",
        final_path="/dashboard",
        status=200,
        title="Dashboard",
        elements=[
            Element(role="link", name="Login", tag="a", target="role:link:Login", href="/login"),
            Element(role="link", name="Register", tag="a", target="role:link:Register", href="/register"),
            Element(role="link", name="Dashboard", tag="a", target="role:link:Dashboard", href="/dashboard"),
            Element(role="link", name="Logout", tag="a", target="role:link:Logout", href="/logout"),
            Element(role="button", name="Refresh", tag="button", target="role:button:Refresh"),
            Element(role="button", name="Settings", tag="button", target="role:button:Settings"),
        ],
    )
    return AppMap(base_url="http://localhost:3000", pages=[login_page, register_page, dashboard_page])


def test_fallback_attack_plan_categories_and_count():
    app_map = make_mock_app_map()
    attacks = fallback_attack_plan(app_map)

    assert len(attacks) == 12

    categories = {a.category for a in attacks}
    assert "authentication" in categories
    assert "functional" in categories
    assert "authorization" in categories
    assert "reliability" in categories
    assert "ux" in categories

    for attack in attacks:
        assert attack.id.startswith("ATTACK-")
        assert len(attack.steps) > 0
        assert attack.steps[0].action in ("goto", "navigate")
        assert any(s.action.startswith("expect_") for s in attack.steps)
        assert attack.expected_behavior


def test_validate_attack_scenarios():
    app_map = make_mock_app_map()
    valid_scenario = AttackScenario(
        id="ATTACK-001",
        category="authentication",
        title="Direct access check",
        goal="Test direct access",
        steps=[
            Step(action="navigate", value="/dashboard"),
            Step(action="expect_url", value="/login"),
        ],
        expected_behavior="Redirects to /login",
        severity_if_failed="high",
    )
    invalid_no_assertion = AttackScenario(
        id="ATTACK-002",
        category="functional",
        title="Incomplete attack",
        goal="No assertion",
        steps=[
            Step(action="navigate", value="/login"),
        ],
        expected_behavior="Nothing",
        severity_if_failed="low",
    )
    invalid_bad_target = AttackScenario(
        id="ATTACK-003",
        category="functional",
        title="Nonexistent element target",
        goal="Click phantom element",
        steps=[
            Step(action="navigate", value="/login"),
            Step(action="click", target="button:NonExistentPhantomButton"),
            Step(action="expect_url", value="/login"),
        ],
        expected_behavior="Stays on login",
        severity_if_failed="low",
    )

    validated = _validate_attack_scenarios(
        [valid_scenario, invalid_no_assertion, invalid_bad_target], app_map
    )
    assert len(validated) == 1
    assert validated[0].id == "ATTACK-001"


def test_build_findings():
    scenario = AttackScenario(
        id="ATTACK-AUTH-001",
        category="authentication",
        title="Direct protected-route access",
        goal="Determine whether a protected page is accessible without authentication",
        steps=[
            Step(action="navigate", value="/dashboard"),
            Step(action="expect_url", value="/login"),
        ],
        expected_behavior="Redirect to /login",
        severity_if_failed="high",
    )
    result_failed = TestResult(
        test_id="ATTACK-AUTH-001",
        status="failed",
        runs=3,
        failures=3,
        duration_ms=450,
        expected="URL containing '/login'",
        actual="URL was /dashboard; page showed: Welcome, guest",
        evidence=Evidence(
            screenshots=["test_run/ATTACK-AUTH-001.png"],
            final_url="http://localhost:3000/dashboard",
            steps_taken=["navigate /dashboard", "expect_url /login"],
        ),
    )

    findings = build_findings([scenario], [result_failed])
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "VG-001"
    assert f.severity == "high"
    assert f.category == "authentication"
    assert f.reproducible is True
    assert len(f.reproduction_steps) > 0
    assert "test_run/ATTACK-AUTH-001.png" in f.evidence.screenshots


def test_safe_target_guard():
    # localhost must succeed
    assert_safe_target("http://localhost:3000")
    assert_safe_target("http://127.0.0.1:8000")

    # non-localhost should raise PermissionError
    with pytest.raises(PermissionError):
        assert_safe_target("http://malicious-external-target.com")

    # invalid url should raise ValueError
    with pytest.raises(ValueError):
        assert_safe_target("ftp://invalid-scheme")


def test_api_404_on_missing_run():
    client = TestClient(app)
    response = client.get("/api/runs/nonexistent123")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_playwright_executor_attack_actions():
    import urllib.parse
    from playwright.async_api import async_playwright
    from app.executor import describe, do_step, locate

    s = Step(action="fill", target="label:Email", value="test@example.com")
    assert describe(s) == "fill label:Email test@example.com"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            html = """
            <!DOCTYPE html>
            <html>
            <body>
                <h1 id="title">Vibe Attack Target</h1>
                <input id="input1" name="email_field" aria-label="User Email" value="" />
                <button id="btn1">Submit</button>
                <div id="status"></div>
                <script>
                    document.getElementById('btn1').addEventListener('click', () => {
                        document.getElementById('status').innerText = 'Submitted: ' + document.getElementById('input1').value;
                    });
                </script>
            </body>
            </html>
            """
            data_url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)

            # 1. navigate / goto
            ok, _, _ = await do_step(page, "", Step(action="goto", value=data_url))
            assert ok is True

            # 2. locate strategies
            assert await locate(page, "label:User Email").count() == 1
            assert await locate(page, "id:input1").count() == 1
            assert await locate(page, "name:email_field").count() == 1
            assert await locate(page, "#title").count() == 1

            # 3. fill
            ok, _, _ = await do_step(page, "", Step(action="fill", target="id:input1", value="hacker@vibeguard.io"))
            assert ok is True
            assert await locate(page, "id:input1").input_value() == "hacker@vibeguard.io"

            # 4. clear
            ok, _, _ = await do_step(page, "", Step(action="clear", target="id:input1"))
            assert ok is True
            assert await locate(page, "id:input1").input_value() == ""

            # 5. fill again & click
            await do_step(page, "", Step(action="fill", target="id:input1", value="break_me"))
            ok, _, _ = await do_step(page, "", Step(action="click", target="id:btn1"))
            assert ok is True

            # 6. expect_text assertion passing
            ok, _, _ = await do_step(page, "", Step(action="expect_text", value="Submitted: break_me"))
            assert ok is True

            # 7. expect_no_text assertion passing
            ok, _, _ = await do_step(page, "", Step(action="expect_no_text", value="NonExistentTextHere"))
            assert ok is True

            # 8. expect_text assertion failing (detection of defect)
            ok_fail, exp_fail, _ = await do_step(page, "", Step(action="expect_text", value="ExpectedNeverShownError"))
            assert ok_fail is False
            assert "text 'ExpectedNeverShownError' visible" in exp_fail

            # 9. resize & reload
            ok, _, _ = await do_step(page, "", Step(action="resize", value="800x600"))
            assert ok is True
            ok, _, _ = await do_step(page, "", Step(action="reload"))
            assert ok is True

            await page.close()
        finally:
            await browser.close()

