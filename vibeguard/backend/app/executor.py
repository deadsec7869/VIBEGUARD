"""Executor: runs the action DSL deterministically with Playwright. No LLM.
Assertion failures (expect_*) are product findings. Any other exception is a plan
error (bad selector etc.), reported as status=error and never turned into a finding."""
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PWTimeout

from .models import Evidence, Step, TestCase, TestResult

REPEATS = 3
ACTION_TIMEOUT = 5000


def locate(page, target: str):
    kind, _, rest = target.partition(":")
    if kind == "label":
        return page.get_by_label(rest).first
    if kind == "role":
        role, _, name = rest.partition(":")
        return page.get_by_role(role, name=name).first
    if kind == "text":
        return page.get_by_text(rest).first
    if kind == "css":
        return page.locator(rest).first
    raise ValueError(f"unsupported target: {target}")


async def _excerpt(page) -> str:
    try:
        return (await page.inner_text("body"))[:300].replace("\n", " | ")
    except Exception:
        return ""


def _path(url: str) -> str:
    return urlparse(url).path or "/"


async def do_step(page, base_url: str, s: Step):
    """Returns (ok, expected, actual). Actions return (True, None, None)."""
    a = s.action
    if a in ("goto", "navigate"):
        target_path = s.value or s.url or "/"
        await page.goto(base_url + target_path, wait_until="load", timeout=10000)
    elif a == "fill":
        await locate(page, s.target).fill(s.value or "", timeout=ACTION_TIMEOUT)
    elif a == "clear":
        await locate(page, s.target).fill("", timeout=ACTION_TIMEOUT)
    elif a in ("click", "double_click"):
        loc = locate(page, s.target)
        if a == "click":
            await loc.click(timeout=ACTION_TIMEOUT)
        else:
            await loc.dblclick(timeout=ACTION_TIMEOUT)
        try:
            await page.wait_for_load_state("load", timeout=3000)
        except PWTimeout:
            pass
    elif a == "resize":
        w, h = s.value.lower().split("x")
        await page.set_viewport_size({"width": int(w), "height": int(h)})
    elif a == "reload":
        await page.reload(wait_until="load")
    elif a == "go_back":
        await page.go_back(wait_until="load", timeout=5000)
    elif a == "go_forward":
        await page.go_forward(wait_until="load", timeout=5000)
    elif a == "expect_url":
        try:
            await page.wait_for_url(re.compile(re.escape(s.value)), timeout=2500)
        except PWTimeout:
            return False, f"URL containing '{s.value}'", f"URL was {_path(page.url)}; page showed: {await _excerpt(page)}"
    elif a == "expect_text":
        try:
            await page.get_by_text(s.value).first.wait_for(state="visible", timeout=3000)
        except PWTimeout:
            return False, f"text '{s.value}' visible", f"text not found; page showed: {await _excerpt(page)}"
    elif a == "expect_no_text":
        await page.wait_for_timeout(300)
        if await page.get_by_text(s.value).count() > 0:
            return False, f"text '{s.value}' absent", f"text '{s.value}' is visible on {_path(page.url)}"
    return True, None, None


def describe(s: Step) -> str:
    val = s.value or s.url or ""
    return " ".join(x for x in (s.action, s.target, val[:60]) if x)


async def _attempt(browser, base_url: str, test: TestCase, shot: Path | None):
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page = await ctx.new_page()
    console: list[str] = []
    net: list[dict] = []
    page.on("console", lambda m: console.append(m.text[:300]) if m.type == "error" else None)
    page.on("pageerror", lambda e: console.append(f"pageerror: {str(e)[:300]}"))
    page.on("response", lambda r: net.append(
        {"method": r.request.method, "url": r.url, "status": r.status}) if len(net) < 40 else None)

    status, idx, expected, actual, taken = "passed", None, None, None, []
    try:
        for i, step in enumerate(test.steps):
            try:
                ok, exp, act = await do_step(page, base_url, step)
            except Exception as e:
                status, idx, actual = "error", i, f"{type(e).__name__}: {str(e)[:200]}"
                break
            taken.append(describe(step))
            if not ok:
                status, idx, expected, actual = "failed", i, exp, act
                break
        ev = Evidence(console_errors=console, network_events=net, final_url=page.url,
                      page_text_excerpt=await _excerpt(page), steps_taken=taken)
        if status != "passed" and shot is not None:
            await page.screenshot(path=str(shot))
    finally:
        await ctx.close()
    return status, idx, expected, actual, ev


async def run_test(browser, base_url: str, test: TestCase, art_dir: Path) -> TestResult:
    t0 = time.time()
    shot = art_dir / f"{test.id}.png"
    outcomes = []
    for n in range(REPEATS):
        o = await _attempt(browser, base_url, test, shot)
        outcomes.append(o)
        if o[0] != "failed":          # pass or plan error: no point re-running 3x
            break
    failures = sum(1 for o in outcomes if o[0] == "failed")
    last = outcomes[-1]
    if all(o[0] == "passed" for o in outcomes):
        status = "passed"
    elif last[0] == "error" and failures == 0:
        status = "error"
    elif failures == REPEATS:
        status = "failed"
    else:
        status = "flaky"
    bad = next((o for o in reversed(outcomes) if o[0] in ("failed", "error")), last)
    ev = bad[4]
    if status in ("failed", "flaky"):
        ev.screenshots = [f"{art_dir.name}/{shot.name}"]
    return TestResult(test_id=test.id, status=status, runs=len(outcomes), failures=failures,
                      duration_ms=int((time.time() - t0) * 1000), failed_step=bad[1],
                      expected=bad[2], actual=bad[3], evidence=ev)


async def execute_all(browser, base_url: str, tests: list[TestCase], art_dir: Path, emit):
    results = []
    for t in tests:
        label = getattr(t, "title", None) or getattr(t, "rationale", "")
        cat = getattr(t, "category", "attack")
        emit("executor", f"{t.id} [{cat}] {label}")
        r = await run_test(browser, base_url, t, art_dir)
        lvl = "info" if r.status == "passed" else ("error" if r.status == "failed" else "warn")
        emit("executor", f"{t.id} -> {r.status.upper()} ({r.failures}/{r.runs} failing runs)", lvl)
        results.append(r)
    return results
