# VibeGuard AI: Slices 1–4

> 📖 Full, illustrated documentation lives in the [root README](../README.md).

Target App -> Discovery -> Vibe Attack Planner -> Action DSL -> Playwright -> Evidence -> Findings -> Fix Agent (Slice 3) -> Independent Verification (Slice 4).

```bash
python -m app.cli http://localhost:3000          # attack → artifacts/<run_id>/run.json
python -m app.cli_fix <run_id> VG-001            # Slice 3: fix (verification stays pending)
python -m app.cli_verify <run_id> VG-001         # Slice 4: independent verification
```

## Attack Categories Covered
1. **Functional**: Empty required fields, malformed input (e.g. invalid email format), boundary/5000-char input, duplicate submission.
2. **Authentication**: Direct protected-route access, revisit protected page after logout, invalid credentials.
3. **Authorization**: Privileged role escalation parameter checks, unauthorized resource probing.
4. **Reliability / State**: Refresh during workflow, unexpected back navigation.
5. **UX**: Mobile viewport checks, visible error feedback on failure.

## Run
```bash
# terminal 1: target demo app
cd demo-app && npm install && npm start            # http://localhost:3000

# terminal 2: VibeGuard backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
export GEMINI_API_KEY=...   # optional: without it the deterministic fallback attack planner runs
python -m app.cli http://localhost:3000            # CLI
uvicorn app.main:app --port 8000                   # or API
curl -X POST localhost:8000/api/runs -H 'content-type: application/json' \
     -d '{"target_url":"http://localhost:3000"}'
```

Expected findings on seeded demo app:
- `VG-001 HIGH Authentication bypass` (Direct protected-route access to `/dashboard`)
- `VG-002 MEDIUM Input validation failure` (Registration endpoint accepting malformed email without validation)

Each failed attack captures reproducible evidence including screenshots in `artifacts/<run_id>/`.
`--replay` / `"replay": true` reuses cached Gemini responses.
Only localhost targets are allowed unless `VIBEGUARD_ALLOW_REMOTE=1`.
