import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from .discovery import discover
from .executor import execute_all
from .findings import build_findings
from .llm import LLM
from .models import Event, Run
from .planner import make_plan
from .db import (
    create_run, update_run, save_attack, save_finding, append_event,
    init_db, get_db_path
)

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(os.getenv("VIBEGUARD_ARTIFACTS", ROOT / "artifacts"))
CACHE_DIR = ARTIFACTS / "_llm_cache"


def assert_safe_target(url: str) -> None:
    """Authorization guard: only test systems we own (localhost) unless explicitly overridden."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("target_url must be an http(s) URL")
    if u.hostname not in ("localhost", "127.0.0.1", "::1") and os.getenv("VIBEGUARD_ALLOW_REMOTE") != "1":
        raise PermissionError("Refusing non-localhost target. Set VIBEGUARD_ALLOW_REMOTE=1 only for systems you own.")


async def run_scan(target_url: str, replay: bool = False, run: Optional[Run] = None) -> Run:
    assert_safe_target(target_url)
    init_db(get_db_path())
    if run is None:
        run = Run(id=uuid.uuid4().hex[:8], target_url=target_url.rstrip("/"), replay=replay)
        create_run(run)

    art = ARTIFACTS / run.id
    art.mkdir(parents=True, exist_ok=True)

    def emit(stage: str, message: str, level: str = "info"):
        event = Event(stage=stage, level=level, message=message)
        run.events.append(event)
        try:
            append_event(run.id, stage, message, {"level": level})
        except Exception as e:
            print(f"Warning: Failed to persist event: {e}")
        print(f"[{time.strftime('%H:%M:%S')}] {stage:<9} {message}")

    emit("system", f"Run {run.id} started for target {run.target_url}")
    if replay:
        emit("system", "REPLAY MODE: using cached LLM responses", "warn")
    llm = LLM(CACHE_DIR, replay=replay)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                emit("discovery", "Discovery started")
                run.app_map = await discover(browser, run.target_url, emit)
                if not run.app_map.pages:
                    raise RuntimeError(f"No pages reachable at {run.target_url}. Is the app running?")
                emit("vibe_attack", "Planning Vibe Attack scenarios...")
                run.tests, run.plan_source = await make_plan(llm, run.app_map, emit)
                for t in run.tests:
                    save_attack(run.id, t, "pending")
                run.results = await execute_all(browser, run.target_url, run.tests, art, emit)
                for r in run.results:
                    t = next((t for t in run.tests if t.id == r.test_id), None)
                    if t:
                        save_attack(run.id, t, r.status)
            finally:
                await browser.close()
        run.findings = build_findings(run.tests, run.results)
        for f in run.findings:
            save_finding(run.id, f, attack_id=f.attack_id)
        run.status = "completed"
        emit("system", f"Vibe Attack complete: {len(run.findings)} finding(s) from {len(run.tests)} attacks "
                       f"(plan source: {run.plan_source})")
    except Exception as e:
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        emit("system", run.error, "error")
    run.finished_at = time.time()
    try:
        update_run(run)
    except Exception as e:
        emit("system", f"Failed to update run status: {e}", "error")
    (art / "run.json").write_text(run.model_dump_json(indent=2))
    return run
