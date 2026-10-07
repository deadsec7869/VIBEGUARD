# Demo app (intentionally flawed)
Seeded defects:
- VG-SEED-001: `/dashboard` is reachable without authentication (auth bypass).
- VG-SEED-002: `/register` lacks input validation and accepts malformed input.
Run: `npm install && npm start` (port 3000, override with PORT).
