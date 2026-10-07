"""Vibe Attack Planner: Gemini generates structured attack scenarios; code validates; deterministic fallback."""
import json
from urllib.parse import urlparse

from .llm import LLM
from .models import (
    AppMap,
    AttackScenario,
    Category,
    LLMAttackPlan,
    Severity,
    Step,
)

PROTECTED_HINTS = (
    "dashboard", "admin", "projects", "tasks", "team", "settings",
    "account", "profile"
)

DSL_REFERENCE = """Actions (JSON field `action`):
- navigate / goto: value = relative path like "/login" or "/dashboard"
- fill: target = element target from the map, value = text to type
- clear: target = element target from the map
- click / double_click: target = element target from the map
- reload: no arguments
- go_back / go_forward: no arguments
- resize: value = "WIDTHxHEIGHT", e.g. "375x812"
- expect_url: value = substring the current URL must contain (assertion)
- expect_text: value = text that must be visible (assertion)
- expect_no_text: value = text that must NOT be visible (assertion)

Targets MUST be copied verbatim from the `target` fields in the app map."""

ATTACK_CATEGORIES_GUIDE = """Attack Categories & Goals:
1. functional: empty required fields, malformed input (e.g. invalid email format), boundary/5000-char input, duplicate submission.
   - For form input attacks (e.g. malformed email on registration), assert that registration does NOT complete (e.g. expect_no_text of post-registration landing/sign-in text) or that an explicit validation error is visible, rather than assuming URL preservation alone.
2. authentication: direct protected-route access, revisit protected page after logout, invalid credentials.
3. authorization: unauthorized resource access, parameter manipulation (e.g. ?user=admin or ?role=admin).
4. reliability: refresh during workflow, unexpected back/forward navigation.
5. ux: mobile viewport layout checks, missing visible error feedback on failure.

Every attack scenario must actively attempt to break the target application.
Every attack scenario must conclude with at least one assertion (expect_*) stating the expected correct behavior."""


def compact_map(m: AppMap) -> dict:
    return {
        "base_url": m.base_url,
        "pages": [
            {
                "path": p.path,
                "final_path": p.final_path,
                "status": p.status,
                "title": p.title,
                "text": p.text_excerpt[:160],
                "elements": [
                    {"role": e.role, "name": e.name, "type": e.type, "target": e.target}
                    for e in p.elements
                ],
            }
            for p in m.pages
        ],
    }


def build_attack_prompt(m: AppMap) -> str:
    return (
        "You are an adversarial QA and security Vibe Attack Planner for a web application.\n"
        "Your mission is to actively try to break the target application using structured attack scenarios.\n\n"
        f"{DSL_REFERENCE}\n\n"
        f"{ATTACK_CATEGORIES_GUIDE}\n\n"
        "Generate 10 to 15 relevant attack scenarios covering functional, authentication, authorization, reliability, and ux categories.\n"
        "Each scenario must begin with a navigate or goto step, and end with an expect_* assertion.\n\n"
        f"DISCOVERED APP MAP:\n{json.dumps(compact_map(m), indent=1)}"
    )


def _validate_attack_scenarios(scenarios: list[AttackScenario], m: AppMap) -> list[AttackScenario]:
    known_targets = {e.target for p in m.pages for e in p.elements}
    valid_scenarios: list[AttackScenario] = []

    for sc in scenarios:
        if not (0 < len(sc.steps) <= 12):
            continue
        first = sc.steps[0]
        if first.action not in ("goto", "navigate"):
            continue

        ok = True
        for s in sc.steps:
            if s.action in ("goto", "navigate"):
                val = s.value or s.url or ""
                if val.startswith("http"):
                    u = urlparse(val)
                    val = u.path or "/"
                if s.action == "navigate":
                    s.value = val
                else:
                    s.value = val
                ok &= bool(val and val.startswith("/"))
            elif s.action in ("fill", "clear", "click", "double_click"):
                ok &= bool(s.target) and (
                    s.target in known_targets or s.target.startswith(("text:", "css:", "label:", "role:"))
                )
                if s.action == "fill":
                    ok &= s.value is not None
            elif s.action in ("expect_url", "expect_text", "expect_no_text", "resize"):
                ok &= bool(s.value)
            elif s.action in ("reload", "go_back", "go_forward"):
                pass
            else:
                ok = False

        ok &= any(s.action.startswith("expect_") for s in sc.steps)
        if ok:
            valid_scenarios.append(sc)

    return valid_scenarios


def fallback_attack_plan(m: AppMap) -> list[AttackScenario]:
    """Deterministic fallback attack library covering the 5 attack categories."""
    attacks: list[AttackScenario] = []

    def add(
        attack_id: str,
        category: Category,
        title: str,
        goal: str,
        steps: list[Step],
        expected_behavior: str,
        severity_if_failed: Severity = "medium",
        rationale: str = "",
        preconditions: list[str] | None = None,
    ):
        attacks.append(
            AttackScenario(
                id=attack_id,
                category=category,
                title=title,
                goal=goal,
                preconditions=preconditions or [],
                steps=steps,
                expected_behavior=expected_behavior,
                severity_if_failed=severity_if_failed,
                rationale=rationale or goal,
                confidence=0.95,
            )
        )

    # Locate discovered elements
    login_page = next((p for p in m.pages if any(e.type == "password" for e in p.elements)), None)
    login_path = login_page.final_path if login_page else "/login"

    register_page = next(
        (p for p in m.pages if "register" in p.path or "register" in p.final_path),
        None,
    )
    register_path = register_page.final_path if register_page else "/register"

    login_email = None
    login_pw = None
    login_btn = None
    if login_page:
        login_email = next((e for e in login_page.elements if e.role == "field" and e.type in ("email", "text")), None)
        login_pw = next((e for e in login_page.elements if e.type == "password"), None)
        login_btn = next((e for e in login_page.elements if e.role == "button"), None)

    reg_name = None
    reg_email = None
    reg_btn = None
    if register_page:
        reg_name = next((e for e in register_page.elements if e.role == "field" and e.name.lower() in ("name", "username")), None)
        reg_email = next((e for e in register_page.elements if e.role == "field" and ("email" in e.name.lower() or e.type == "email")), None)
        reg_btn = next((e for e in register_page.elements if e.role == "button"), None)

    # 1. Authentication attacks
    # ATTACK-AUTH-001: Direct protected-route access
    add(
        attack_id="ATTACK-AUTH-001",
        category="authentication",
        title="Direct protected-route access",
        goal="Determine whether a protected page is accessible without authentication",
        preconditions=[],
        steps=[
            Step(action="navigate", value="/dashboard"),
            Step(action="expect_url", value=login_path),
        ],
        expected_behavior="Redirect to /login",
        severity_if_failed="high",
        rationale="Protected routes like /dashboard must require an active session",
    )

    # ATTACK-AUTH-002: Revisit protected page after logout / session termination
    add(
        attack_id="ATTACK-AUTH-002",
        category="authentication",
        title="Session termination on logout",
        goal="Verify invoking logout terminates session state and directs user to login",
        preconditions=[],
        steps=[
            Step(action="navigate", value="/logout"),
            Step(action="expect_url", value=login_path),
        ],
        expected_behavior="Redirect to /login and clear session cookie",
        severity_if_failed="high",
        rationale="Logging out must securely navigate to login and clear session",
    )

    # ATTACK-AUTH-003: Invalid credentials
    if login_email and login_pw and login_btn:
        add(
            attack_id="ATTACK-AUTH-003",
            category="authentication",
            title="Invalid credentials rejected",
            goal="Ensure incorrect credentials do not grant access to the application",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="fill", target=login_email.target, value="unregistered_user@example.com"),
                Step(action="fill", target=login_pw.target, value="wrongpassword123"),
                Step(action="click", target=login_btn.target),
                Step(action="expect_url", value=login_path),
            ],
            expected_behavior="Deny login and remain on login page",
            severity_if_failed="medium",
            rationale="Invalid login submissions must be rejected",
        )

    # 2. Functional attacks
    # ATTACK-FUNC-001: Empty required fields
    if login_btn:
        add(
            attack_id="ATTACK-FUNC-001",
            category="functional",
            title="Empty required fields submission",
            goal="Verify submitting empty login form is rejected",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="click", target=login_btn.target),
                Step(action="expect_url", value=login_path),
            ],
            expected_behavior="Form submission blocked; remain on login page",
            severity_if_failed="medium",
            rationale="Empty submissions must not proceed",
        )

    # ATTACK-FUNC-002: Malformed input on registration
    if reg_btn and reg_email:
        reg_steps = [
            Step(action="navigate", value=register_path),
        ]
        if reg_name:
            reg_steps.append(Step(action="fill", target=reg_name.target, value="Attack Tester"))
        completion_marker = (login_btn.name if login_btn else None) or "Sign in"
        reg_steps.extend([
            Step(action="fill", target=reg_email.target, value="not-an-email"),
            Step(action="click", target=reg_btn.target),
            Step(action="expect_no_text", value=completion_marker),
        ])
        add(
            attack_id="ATTACK-FUNC-002",
            category="functional",
            title="Malformed email input validation",
            goal="Verify registration rejects malformed email and does not complete registration",
            steps=reg_steps,
            expected_behavior=f"Registration must not complete with malformed email (must not proceed to {completion_marker})",
            severity_if_failed="medium",
            rationale="Registration endpoint must validate email syntax before completing registration and proceeding to sign in",
        )
    elif login_email and login_btn:
        add(
            attack_id="ATTACK-FUNC-002",
            category="functional",
            title="Malformed input rejected",
            goal="Verify login rejects malformed email input",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="fill", target=login_email.target, value="not-an-email"),
                Step(action="click", target=login_btn.target),
                Step(action="expect_url", value=login_path),
            ],
            expected_behavior="Reject invalid email format; remain on login page",
            severity_if_failed="medium",
            rationale="Endpoints should validate email formats",
        )

    # ATTACK-FUNC-003: Boundary / 5000-char input
    if login_email and login_pw and login_btn:
        add(
            attack_id="ATTACK-FUNC-003",
            category="functional",
            title="Boundary/large input handling",
            goal="Verify 5000-character payload is handled safely without crashing",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="fill", target=login_email.target, value="a" * 5000),
                Step(action="fill", target=login_pw.target, value="x"),
                Step(action="click", target=login_btn.target),
                Step(action="expect_url", value=login_path),
            ],
            expected_behavior="Handle oversized input without 500 crash or unexpected redirect",
            severity_if_failed="low",
            rationale="Boundary input must not crash backend process",
        )

    # ATTACK-FUNC-004: Duplicate submission
    if login_email and login_btn:
        add(
            attack_id="ATTACK-FUNC-004",
            category="functional",
            title="Duplicate rapid submission",
            goal="Verify double-click submission does not trigger inconsistent state",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="fill", target=login_email.target, value="nobody@example.com"),
                Step(action="double_click", target=login_btn.target),
                Step(action="expect_url", value=login_path),
            ],
            expected_behavior="Gracefully handle repeated submission",
            severity_if_failed="low",
            rationale="Double submission should be handled idempotently",
        )

    # 3. Authorization attacks
    # ATTACK-AUTHZ-001: Unauthorized resource manipulation
    add(
        attack_id="ATTACK-AUTHZ-001",
        category="authorization",
        title="Unauthorized role escalation parameter check",
        goal="Determine whether injecting privileged role parameter grants elevated view without authorization",
        steps=[
            Step(action="navigate", value="/login?role=admin"),
            Step(action="expect_url", value=login_path),
        ],
        expected_behavior="Retain unauthenticated login view without privilege escalation",
        severity_if_failed="high",
        rationale="Unauthenticated role tampering must not grant elevated capabilities",
    )

    # 4. Reliability / State attacks
    # ATTACK-STATE-001: Refresh during workflow
    if login_email:
        add(
            attack_id="ATTACK-STATE-001",
            category="reliability",
            title="Refresh during workflow",
            goal="Verify application remains stable when reloaded during form interaction",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="fill", target=login_email.target, value="tester@vibeguard.dev"),
                Step(action="reload"),
                Step(action="expect_url", value=login_path),
            ],
            expected_behavior="Page reloads cleanly without state corruption",
            severity_if_failed="low",
            rationale="Reloading mid-form should be reliable",
        )

    # ATTACK-STATE-002: Unexpected back/forward navigation
    add(
        attack_id="ATTACK-STATE-002",
        category="reliability",
        title="Unexpected back navigation",
        goal="Verify browser back action transitions correctly between routes",
        steps=[
            Step(action="navigate", value=login_path),
            Step(action="navigate", value=register_path),
            Step(action="go_back"),
            Step(action="expect_url", value=login_path),
        ],
        expected_behavior="Clean navigation back to login page",
        severity_if_failed="low",
        rationale="Back button history transitions must function smoothly",
    )

    # 5. UX attacks
    # ATTACK-UX-001: Mobile viewport checks
    add(
        attack_id="ATTACK-UX-001",
        category="ux",
        title="Mobile viewport layout check",
        goal="Verify login view remains usable and visible on mobile viewport",
        steps=[
            Step(action="navigate", value=login_path),
            Step(action="resize", value="375x812"),
            Step(action="expect_text", value="Sign in"),
        ],
        expected_behavior="Login interface renders properly on mobile viewport",
        severity_if_failed="low",
        rationale="Mobile screens must render key text and forms",
    )

    # ATTACK-UX-002: Visible error feedback on failed login
    if login_email and login_pw and login_btn:
        add(
            attack_id="ATTACK-UX-002",
            category="ux",
            title="Visible error feedback check",
            goal="Verify clear error notification is visible to the user upon failed login",
            steps=[
                Step(action="navigate", value=login_path),
                Step(action="fill", target=login_email.target, value="baduser@vibeguard.dev"),
                Step(action="fill", target=login_pw.target, value="invalidpass"),
                Step(action="click", target=login_btn.target),
                Step(action="expect_text", value="Invalid"),
            ],
            expected_behavior="Clear error banner or alert displayed to user",
            severity_if_failed="medium",
            rationale="Failed attempts must give clear user-facing feedback",
        )

    return attacks


# Backwards compatibility alias
fallback_plan = fallback_attack_plan


async def make_attack_plan(llm: LLM, m: AppMap, emit) -> tuple[list[AttackScenario], str]:
    """Plans attacks via Gemini if available, with deterministic fallback."""
    try:
        plan = await llm.generate_json(build_attack_prompt(m), LLMAttackPlan)
        raw_scenarios: list[AttackScenario] = []
        for s in plan.scenarios:
            raw_scenarios.append(
                AttackScenario(
                    id=s.id or f"ATTACK-{len(raw_scenarios) + 1:03d}",
                    category=s.category,
                    title=s.title,
                    goal=s.goal,
                    steps=[Step(**step.model_dump()) for step in s.steps],
                    expected_behavior=s.expected_behavior,
                    severity_if_failed=s.severity_if_failed,
                    rationale=s.rationale,
                )
            )

        valid_scenarios = _validate_attack_scenarios(raw_scenarios, m)
        emit("planner", f"Gemini proposed {len(raw_scenarios)} attacks, {len(valid_scenarios)} passed validation")
        if valid_scenarios:
            valid_scenarios = valid_scenarios[:15]
            for i, sc in enumerate(valid_scenarios, 1):
                if not sc.id or sc.id.startswith("T"):
                    sc.id = f"ATTACK-{i:03d}"
            return valid_scenarios, "gemini-replay" if llm.replay else "gemini"
    except Exception as e:
        emit("planner", f"LLM planning unavailable ({type(e).__name__}: {e}); using fallback attack library", "warn")

    attacks = fallback_attack_plan(m)
    emit("planner", f"Fallback Vibe Attack library produced {len(attacks)} attack scenarios")
    return attacks, "fallback"


# Backwards compatibility alias for Slice 1 orchestrator callers
make_plan = make_attack_plan
