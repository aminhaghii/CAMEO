# CAMEO — Agent Instructions

## Commands

- **Install:** `pip install -r backend/requirements.txt`
- **Run server:** `python backend/app.py` (port 5000)
- **Run tests:** `cd backend && python -m pytest tests/ -v`
- **Single test:** `cd backend && python -m pytest tests/test_warehouse.py -v`
- **Single test function:** `cd backend && python -m pytest tests/test_warehouse.py::TestWarehouse -k auto_arrange -v`
- **Seed test data:** `python backend/scripts/ensure_data.py`

## Critical: Test Working Directory

Always `cd backend` before running pytest. Running from repo root picks up the **gitignored** root `tests/` directory (legacy scripts + Playwright E2E), not the live `backend/tests/` unit suite.

## Database

- All DBs are SQLite in `backend/data/`, gitignored and generated at runtime.
- `chemicals.db` must exist before tests pass. Run `python backend/scripts/ensure_data.py` if missing.
- Auth DBs (`global_auth.db`, `{id}_user.db`) are auto-created on first startup.
- **Always** use `db_utils.get_safe_connection()` for DB connections — it enforces foreign keys, WAL mode, and busy_timeout. Never bare `sqlite3.connect()` in app code.
- Tenant isolation: each company gets `{company_id}_user.db`. Super admin has no tenant DB (`g.tenant_db_path = None`) — routes must call `_require_tenant_db()` and return 403 if None.

## Architecture

- **Entrypoint:** `backend/app.py` — Flask app, `tenant_router` middleware, search, favorites, dashboard, matrix, log routes.
- **Blueprints:** `routes/` — `inventory`, `inventory_actions`, `inventory_analysis`, `auth`, `admin`, `compliance`, `warehouse`.
- **ETL:** `etl/pipeline.py` runs ingest→column map→clean→match→validate in a **background thread**. Do not call synchronously from request handlers.
- **Reactivity engine:** `logic/reactivity_engine.py` — safety-critical; compatibility decisions affect human safety. Changes here need extreme care.
- **Constants:** `logic/constants.py` — `Compatibility` enum, `COMPATIBILITY_MAP`, `WATER_GROUP_ID` (104), `AIR_GROUP_ID` (101).
- **Auth:** `auth/` — `models.py` (DB), `security.py` (hashing, sessions, CSRF), `decorators.py` (`@login_required`, `@csrf_protect`, `@role_required`, `@viewer_readonly`).
- **Activity logger:** `activity_logger.py` — `log_event()` is always a no-op (never raises). Safe to call anywhere without try/except.

## Reactivity Engine (Safety-Critical)

- **Fail-safe default:** If no reactivity rule exists between two groups, returns `NO_DATA` (treated as Caution), never Compatible.
- **Cartesian product logic:** All combinations of chemical A's groups × chemical B's groups are checked. Worst result wins (priority: INCOMPATIBLE > CAUTION/NO_DATA > COMPATIBLE).
- **Self-hazard escalation:** Diagonal matrix cells check `special_hazards` field. Chemicals with `peroxide`, `pyrophoric`, `water reactive` flags escalate to CAUTION even alone.
- **Water reactivity check:** Optional `include_water_check` parameter cross-checks every chemical's groups against `WATER_GROUP_ID` (104). Water-reactive chemicals generate warnings to store dry.
- **Chemicals with no groups:** Always result in `NO_DATA` — never assumed compatible.
- **Audit logging:** `engine.analyze()` writes to `audit_log` table in the tenant DB. Must pass `audit_db_path` explicitly or rely on request-context `g.tenant_db_path`.

## Compatibility Codes & Priority

| Enum Value | Code | Priority | Color | Label EN |
|---|---|---|---|---|
| `COMPATIBLE` | `C` | 1 | Green | Compatible |
| `CAUTION` | `I-C` | 2 | Yellow | Caution |
| `INCOMPATIBLE` | `I` | 3 | Red | Incompatible |
| `NO_DATA` | `N` | 2 (fail-safe = same as Caution) | Orange | No Data |

DB values `'Compatible'`, `'Caution'`, `'Incompatible'` are also accepted via `DB_COMPATIBILITY_MAP`.

## ETL Matching Pipeline

- **Match statuses:** `MATCHED` (confidence ≥ 0.80), `REVIEW_REQUIRED` (0.50–0.80), `UNIDENTIFIED` (< 0.50).
- **Signal-based architecture:** Each field (CAS, name, formula, UN, synonym) independently generates weighted signals. Fusion layer picks best candidate.
- **CAS is gold standard:** `cas_exact` weight = 1.00. CAS exact match returns immediately with confidence 1.0.
- **Field-swap detection:** CAS in name column or name in CAS column is detected and re-routed with appropriate weights.
- **Safety veto system:** `etl/semantics.py` classifies tokens as BASE/SALT/FORM/GRADE/SAFETY/HAZARD. Benign inputs (SAFETY context like "salt", "alcohol", "peroxide") are blocked from matching hazardous chemicals.
- **Anti-hallucination:** Matcher never creates new chemicals. Output is always an existing `chemical_id` or `None`.
- **Multi-field agreement boost:** If ≥2 independent field categories (e.g. CAS + formula) agree on the same candidate, confidence gets +0.12 per extra category.
- **Conflict detection:** Cross-field conflicts (CAS says X, name says Y) cap confidence at 0.84 (REVIEW_REQUIRED).

## Warehouse Validation Rules

- **Section blocking compatibilities:** `{INCOMPATIBLE, CAUTION, NO_DATA}` — these block placement if detected between any pair in a section.
- **Section conflict compatibilities:** `{INCOMPATIBLE}` — always blocks, no override possible.
- **CAUTION/NO_DATA override:** `company_admin` or `super_admin` can override CAUTION and NO_DATA blocks. Operators and viewers cannot.
- **Water-reactive isolation:** Auto-arrange algorithm isolates water-reactive chemicals from Water group (104) chemicals.
- **Auto-arrange:** Greedy graph-coloring algorithm. Chemicals are vertices, incompatibilities are edges. Goal: minimum sections with no adjacent incompatible chemicals.
- **Cross-warehouse moves:** Blocked — placements can only move to sections within the same warehouse.

## Auth & RBAC

- **Roles (fail-closed):** `super_admin`, `company_admin`, `operator`, `viewer`.
- **Session:** Cookie name `session_id`, 48-byte random token, 30-min sliding expiry + absolute expiry.
- **CSRF:** Double-submit cookie pattern. All POST/PUT/DELETE require `@csrf_protect` decorator.
- **Brute-force protection:** 5 failed attempts → 15-min lockout. Tracked in `login_attempts` table.
- **Password policy:** bcrypt cost=12, min 8 chars, max 128, NIST 800-63B compliant.
- **`force_password_change`:** When flag is set on a user, only `/auth/logout`, `/api/auth/change-password`, `/api/auth/me`, `/api/auth/csrf` are accessible. Enforced in `tenant_router` middleware.
- **Super admin blind spot:** `g.tenant_db_path = None` for super_admin. Routes accessing tenant data must call `_require_tenant_db()` and return 403 if None.

## Test Patterns

- Tests import `app` from `backend/app.py` and use Flask test client.
- **chemicals.db dependency:** Warehouse and reactivity tests require real chemicals in `backend/data/chemicals.db`. Use `ensure_data.py` or verify CAS numbers (Acetone: 67-64-1, Sulfuric Acid: 7664-93-9, Ethanol: 64-17-5).
- **Phase 2 tables:** Test helpers call `_ensure_phase2_tables()` which reads `backend/scripts/create_inventory_tables.sql`.
- **Mock user fixture:** `{"id": 1, "email": "admin@cameo.com", "role": "company_admin", "company_id": 1, "tenant_db_path": "..."}` — patch `g.user` and `g.tenant_db_path` for authenticated routes.
- **No pytest.ini or conftest.py** — tests use `sys.path.insert(0, ...)` to resolve imports. No shared fixtures file.

## Conventions

- No linter, formatter, typecheck, or CI is configured — no config files exist.
- Static assets (Tailwind, Alpine) auto-download from CDN on startup with SHA-256 hash verification.
- API responses follow envelope pattern: `{"success": true/false, "data": ..., "error": ...}`.
- Secrets: `.flask_secret_key` auto-generated in `backend/data/` if `FLASK_SECRET_KEY` env var not set.
- `activity_logger.log_event()` never raises — safe to call from any route without try/except.
