# VibeGuard AI: vertical slice 1

Discover -> plan (Gemini, schema-validated) -> execute (Playwright DSL, 3x re-run) -> findings.

## Run
```bash
# terminal 1: target
cd demo-app && npm install && npm start            # http://localhost:3000

# terminal 2: VibeGuard
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
export GEMINI_API_KEY=...   # optional: without it the deterministic fallback planner runs
python -m app.cli http://localhost:3000            # CLI
uvicorn app.main:app --port 8000                   # or API
curl -X POST localhost:8000/api/runs -H 'content-type: application/json' \
     -d '{"target_url":"http://localhost:3000"}'
```
Expected: finding VG-00x "Unauthenticated direct access to /dashboard should redirect to login",
reproducible, with a screenshot in `artifacts/<run_id>/`.

`--replay` / `"replay": true` reuses cached Gemini responses (cache fills on every live run).
Only localhost targets are allowed unless `VIBEGUARD_ALLOW_REMOTE=1`.
