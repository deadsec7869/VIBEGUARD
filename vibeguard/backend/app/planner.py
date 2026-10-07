"""Planner: Gemini proposes structured tests; code validates; deterministic fallback."""
import json
from urllib.parse import urlparse

from .llm import LLM
from .models import AppMap, LLMPlan, Step, TestCase

PROTECTED_HINTS = ("dashboard", "admin", "projects", "tasks", "team", "settings",
                   "account", "profile")

DSL_REFERENCE = """Actions (JSON field `action`):
- goto: value = path like "/login"
- fill: target = element target from the map, value = text to type
- click / double_click: target = element target from the map
- expect_url: value = substring the current URL must contain (assertion)
- expect_text: value = text that must be visible (assertion)
- expect_no_text: value = text that must NOT be visible (assertion)
- resize: value = "WIDTHxHEIGHT", e.g. "375x812"
- reload: no arguments
Targets MUST be copied verbatim from the `target` fields in the app map."""

ATTACKS = """Choose attacks that are relevant to what was discovered. Examples:
authentication (unauthenticated access to protected-looking paths, malformed credentials),
input (empty, 5000-char, special characters, malformed email),
workflow (double submit, reload mid-flow), ux (mobile viewport), reliability (revisit after change).
Every test must end with at least one assertion (expect_*) stating the CORRECT behavior.
Assume no valid credentials exist unless the page text reveals them."""


def compact_map(m: AppMap) -> dict:
    return {"base_url": m.base_url, "pages": [{
        "path": p.path, "final_path": p.final_path, "status": p.status, "title": p.title,
        "text": p.text_excerpt[:160],
        "elements": [{"role": e.role, "name": e.name, "type": e.type, "target": e.target}
                     for e in p.elements]} for p in m.pages]}


def build_prompt(m: AppMap) -> str:
    return (f"You are a QA and security test planner for a web app.\n\n{DSL_REFERENCE}\n\n{ATTACKS}\n\n"
            f"Produce 5 to 10 tests. Each test starts with a goto step.\n\n"
            f"APP MAP:\n{json.dumps(compact_map(m), indent=1)}")


def _validate(tests: list[TestCase], m: AppMap) -> list[TestCase]:
    known = {e.target for p in m.pages for e in p.elements}
    good = []
    for t in tests:
        ok = 0 < len(t.steps) <= 12 and t.steps[0].action == "goto"
        for s in t.steps:
            if s.action == "goto":
                v = (s.value or "")
                if v.startswith("http"):
                    u = urlparse(v)
                    s.value = u.path or "/"
                ok &= bool(s.value and s.value.startswith("/"))
            elif s.action in ("fill", "click", "double_click"):
                ok &= bool(s.target) and (s.target in known or s.target.startswith(("text:", "css:")))
                if s.action == "fill":
                    ok &= s.value is not None
            elif s.action in ("expect_url", "expect_text", "expect_no_text", "resize"):
                ok &= bool(s.value)
        ok &= any(s.action.startswith("expect_") for s in t.steps)
        if ok:
            good.append(t)
    return good


def fallback_plan(m: AppMap) -> list[TestCase]:
    tests: list[TestCase] = []

    def add(**kw):
        tests.append(TestCase(id=f"T{len(tests) + 1:02d}", **kw))

    login = next((p for p in m.pages if any(e.type == "password" for e in p.elements)), None)
    login_path = login.final_path if login else "/login"
    for p in m.pages:
        if any(h in p.path for h in PROTECTED_HINTS):
            add(category="security", attack_type="auth_bypass", severity_hint="high",
                rationale=f"Unauthenticated direct access to {p.path} should redirect to login",
                steps=[Step(action="goto", value=p.path), Step(action="expect_url", value=login_path)])
    if login:
        email = next((e for e in login.elements if e.role == "field" and e.type in ("email", "text")), None)
        pw = next((e for e in login.elements if e.type == "password"), None)
        btn = next((e for e in login.elements if e.role == "button"), None)
        if email and pw and btn:
            go = Step(action="goto", value=login_path)
            stay = Step(action="expect_url", value=login_path)
            add(category="functionality", attack_type="bad_credentials", severity_hint="medium",
                rationale="Wrong credentials must not log the user in",
                steps=[go, Step(action="fill", target=email.target, value="nobody@example.com"),
                       Step(action="fill", target=pw.target, value="wrong-password"),
                       Step(action="click", target=btn.target), stay])
            add(category="functionality", attack_type="empty_submit", severity_hint="medium",
                rationale="Submitting an empty login form must not log the user in",
                steps=[go, Step(action="click", target=btn.target), stay])
            add(category="reliability", attack_type="long_input", severity_hint="low",
                rationale="A 5000-character email must be handled without leaving the login page",
                steps=[go, Step(action="fill", target=email.target, value="a" * 5000),
                       Step(action="fill", target=pw.target, value="x"),
                       Step(action="click", target=btn.target), stay])
    return tests


async def make_plan(llm: LLM, m: AppMap, emit) -> tuple[list[TestCase], str]:
    try:
        plan = await llm.generate_json(build_prompt(m), LLMPlan)
        raw = [TestCase(id="", category=t.category, attack_type=t.attack_type,
                        rationale=t.rationale, severity_hint=t.severity,
                        steps=[Step(**s.model_dump()) for s in t.steps]) for t in plan.tests]
        tests = _validate(raw, m)
        emit("planner", f"Gemini proposed {len(raw)} tests, {len(tests)} passed validation")
        if tests:
            tests = tests[:10]
            for i, t in enumerate(tests, 1):
                t.id = f"T{i:02d}"
            return tests, "gemini-replay" if llm.replay else "gemini"
    except Exception as e:
        emit("planner", f"LLM planning unavailable ({type(e).__name__}: {e}); using fallback", "warn")
    tests = fallback_plan(m)
    emit("planner", f"Fallback planner produced {len(tests)} tests")
    return tests, "fallback"
