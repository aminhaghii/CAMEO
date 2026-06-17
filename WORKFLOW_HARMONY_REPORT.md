# WORKFLOW HARMONY REPORT — SAFEWARE-CAMEO

**Audit type:** Deep, non-destructive, read-only architectural & logical audit
**Scope:** ETL→Warehouse lifecycle · UI/API harmony · Auth & reactivity loops · Multi-tenant isolation
**Date:** 2026-06-17
**Constraint honored:** No code was modified, edited, or written. This document is the only deliverable.

> **How to read this report.** Every finding lists a Trigger, the exact code path (`file:line`), why harmony breaks, and a Severity. Findings were produced by four parallel investigators and then **spot-verified against source** by the lead auditor. Where an investigator's claim was wrong or overstated, the correction is called out in an **`Auditor note`**. Line numbers are accurate as of the audit date; treat them as anchors, not contracts.

---

## Severity legend

| Severity | Meaning |
|---|---|
| **Critical** | Data corruption, cross-tenant leak, or unrecoverable lockout reachable in normal use. Fix before next release. |
| **High** | Silent state divergence, confusing dead-end, or race that needs a specific timing but causes real damage. |
| **Medium** | UX disharmony or latent fragility; no corruption today but breaks on refactor or edge input. |
| **Low** | Cosmetic, defensive, or theoretical. |

## Findings at a glance

| # | Area | Finding | Severity |
|---|---|---|---|
| 1.1 | ETL→Warehouse | Batch delete orphans `chemical_placements` (no FK/cascade) | **Critical** |
| 1.2 | ETL→Warehouse | Quantity edits never reach the warehouse (key-format mismatch → silent no-op) | **High** |
| 1.3 | ETL→Warehouse | Re-editing a placed staging row diverges staging vs. warehouse identity | **High** |
| 1.4 | ETL→Warehouse | Duplicate-import guard is correct but not atomic; misleading counts under concurrency | **Medium** |
| 1.5 | ETL→Warehouse | Soft-deleted `audit_trail` rows never reaped | **Low** |
| 2.1 | UI/API | `apply_recommended_layout` double-submit creates duplicate sections | **Critical** |
| 2.2 | UI/API | No async/disabled guard on Auto-Arrange / Accept Layout buttons | **High** |
| 2.3 | UI/API | Failed save (403/409) leaves stale ghost layout on screen | **Medium** |
| 2.4 | UI/API | Client allows NO_DATA placement that server rejects on save | **Medium** |
| 2.5 | UI/API | Inventory quantity edit doesn't refresh an open warehouse view | **Medium** |
| 3.1 | Loops | Force-password-change page is fragile inline HTML, no template/escape hatch | **High** |
| 3.2 | Loops | Operator sees warehouse mutate actions they can never execute (wrong error) | **High** |
| 3.3 | Loops | Super-admin nav links lead to tenant-only routes → dead-end | **Medium** |
| 3.4 | Loops | No guard against self-inflicted `force_password_change` / last-admin lockout | **Medium** |
| 4.1 | Tenancy | `g.tenant_db_path or USER_DB_PATH` fallback across 5 files → shared-DB leak | **Critical** |
| 4.2 | Tenancy | Super-admin mapped to first real tenant's DB; contradicts documented blind spot | **Critical** |
| 4.3 | Tenancy | `tenant_router` can build `None_user.db` / crash on null company_id | **High** |
| 4.4 | Tenancy | Reactivity audit log silently dropped when tenant context missing | **Medium** |

---

# Area 1 — The Grand "ETL-to-Warehouse" Lifecycle (Data Integrity & Orphans)

Lifecycle traced: **Upload Excel** (`etl/pipeline.py`) → **Staging** (`inventory_staging`) → **Manual resolution** (`routes/inventory.py`, `routes/inventory_actions.py`) → **Warehouse pool** (`warehouse.py::add_from_batch`) → **Placement in section** (`chemical_placements`).

The structural root cause behind most of this area: **`chemical_placements` is linked to its batch only by a denormalized string** in `placed_by` (format `import:{batch_id}:{staging_row_id}`, `warehouse.py:711`). There is no foreign key, and different code paths format or parse that string inconsistently.

---

## Finding 1.1 — Deleting a batch orphans its warehouse placements — **Critical**

**Trigger:** A user deletes a batch (`routes/inventory.py`, batch-delete handler ~`:304–348`) while one or more of that batch's chemicals are placed in a warehouse section.

**Code path / evidence:**
- Batch delete removes/marks four tables:
  - `DELETE FROM review_queue WHERE batch_id = ?`
  - `UPDATE audit_trail SET is_deleted = 1 WHERE batch_id = ?`
  - `DELETE FROM inventory_staging WHERE batch_id = ?`
  - `DELETE FROM inventory_batches WHERE id = ?`
- `chemical_placements` is **never touched** by this handler.
- Schema (`etl/pipeline.py:170–183`): `placed_by TEXT` — no FK to `inventory_batches`, no `ON DELETE` behavior. (The table's only cascades are `warehouse_id`/`section_id` → warehouses/sections.)

**Why harmony breaks:** After deletion, placements survive holding `placed_by = "import:{deleted_batch}:{row}"` that resolves to nothing. The chemical's provenance (which import, which staging row, original match confidence) is gone, but the physical placement still drives reactivity/auto-arrange decisions. For a safety system, a stored chemical whose origin can no longer be audited is the worst kind of orphan.

**Blast radius:** Audit trail for that chemical is broken; re-import detection (Finding 1.4) can't recognize the row anymore; warehouse safety analysis keeps running on a record nobody can trace.

---

## Finding 1.2 — Inventory quantity edits silently never reach the warehouse — **High**

**Trigger:** A user edits the quantity of a staging row (`routes/inventory_actions.py::edit` ~`:262+`) for a chemical already imported into the warehouse.

**Code path / evidence:**
- `_propagate_quantity_to_warehouse()` builds the lookup key as:
  - `inventory_actions.py:213` → `import_tag = f"import:{batch_id}"`
- and updates with an **exact-match** WHERE:
  - `inventory_actions.py:248–252` → `UPDATE chemical_placements SET quantity_kg = ? WHERE chemical_id = ? AND placed_by = ?` with `placed_by = import_tag`.
- But placements are written with a **three-part** key:
  - `warehouse.py:711` → `placed_by = f"import:{batch_id}:{staging_row_id}"`.

**Why harmony breaks:** `"import:{batch_id}"` never equals `"import:{batch_id}:{row}"` under `=`, so the UPDATE matches **zero rows every time**. The edit succeeds in staging, `cursor.rowcount` is 0, the info-log on `:254` never fires, and the warehouse keeps the original imported quantity. The user believes inventory and warehouse are in sync; they are not.

> **Auditor note — correction to investigator claim.** The Area-1 investigator reported this as "updates ALL placements of that chemical (corruption)," reasoning the key was a prefix used with `LIKE`. Verified false: the query uses `=`, not `LIKE`, and the prefix is missing the `:{row}` segment. The real failure mode is the **opposite** — a **silent no-op**, not over-broad corruption. Severity stays High because it is a silent data-divergence on a safety-relevant quantity, but the mechanism in any fix ticket must read "no rows matched," not "too many rows matched."

---

## Finding 1.3 — Re-resolving a placed staging row diverges identity — **High**

**Trigger:** A row is imported to the warehouse, then the user re-opens that staging row and re-assigns it to a different `chemical_id` (edit handler, `inventory_actions.py:262+`).

**Code path / evidence:**
- The edit path freely rewrites `inventory_staging.chemical_id` and `match_status`; there is **no check** for whether the row was already imported (no `locked_in_warehouse` flag, no `placed_by` lookup before allowing the edit).
- Warehouse `chemical_placements` froze `chemical_id`, `chemical_name`, `cas_number`, and `reactive_groups` at import time (`warehouse.py:705–712`); nothing re-syncs them.

**Why harmony breaks:** Staging now says the row is chemical B; the warehouse still stores and analyzes chemical A. Reactivity/auto-arrange operate on the stale A identity. Combined with Finding 1.2, even the quantity bridge that was *supposed* to connect them is inert, so there is no path for the new identity to propagate.

**Blast radius:** Safety analysis on a mislabeled chemical. This is the most safety-pointed of the Area-1 items even though it requires a deliberate re-edit.

---

## Finding 1.4 — Duplicate-import guard works but isn't atomic — **Medium**

**Trigger:** `add_from_batch` is invoked twice for the same `(batch_id, warehouse_id)` — by a double-click or two near-simultaneous requests.

**Code path / evidence:**
- Per-row guard (`warehouse.py:704–709`): selects an existing placement by exact `placed_by = "import:{batch_id}:{row}"` and `warehouse_id`; if present, `continue`.
- The whole import runs under `BEGIN EXCLUSIVE` (`warehouse.py:584`).

**Why harmony breaks (and what doesn't):** Because `placed_by` is deterministic per staging row and the write is wrapped in an EXCLUSIVE transaction, **SQLite serializes the two imports and the second is correctly a no-op** — there is no duplicate-row corruption here. The residual gap is two-fold and milder than the investigator framed it:
1. **No DB-level uniqueness.** Protection depends entirely on the application check; there is no `UNIQUE(placed_by, warehouse_id)` index as a backstop if a future code path inserts without the guard.
2. **Misleading success message under concurrency.** The second request returns success with `imported_count = 0` and a "Skipped N rows (not yet matched)" style message (`warehouse.py:748+`), which conflates "already imported" with "not matched" and can confuse the operator.

> **Auditor note.** Investigator graded this **High** citing a check-then-insert race. Downgraded to **Medium**: `BEGIN EXCLUSIVE` closes the race for concurrent calls on the same tenant DB. The real residue is the missing DB constraint and the ambiguous user-facing count.

---

## Finding 1.5 — Soft-deleted audit rows are never reaped — **Low**

**Trigger:** Repeated create/delete of batches over time.

**Code path / evidence:** Batch delete does `UPDATE audit_trail SET is_deleted = 1 WHERE batch_id = ?` (`inventory.py` ~`:323`). No process ever hard-deletes `is_deleted = 1` rows; `batch_id` is plain TEXT, not an FK.

**Why harmony breaks:** Unbounded growth of tombstoned audit rows. Retaining audit history is *correct* for a safety product, so this is intentional-but-unbounded rather than a bug. Only flagged for a future retention/TTL policy.

---

# Area 2 — UI & Button-State Harmony (Alpine.js ↔ Flask)

Investigated `templates/warehouse.html` (the `warehouseApp()` component) against `routes/warehouse.py`, plus the inventory templates. **No phantom endpoints and no response-field name mismatches were found** — every button maps to a real route and reads fields the backend actually returns (`suggested_layout`, `confidence_score`, `recommendation`, `requires_admin_override`). The problems are all about *timing and state reset*, not wiring.

---

## Finding 2.1 — Double-submitting "Accept Layout" creates duplicate sections — **Critical**

**Trigger:** With an active recommendation, the user clicks **Accept Layout** more than once before the first request resolves.

**Code path / evidence:**
- `saveSuggestedLayout()` chooses the endpoint based on `recommendation.can_auto_create` and posts to `/api/warehouse/recommendation/apply` (handler `warehouse.py:1209+`).
- That handler computes the next section index *fresh per request* (`MAX(position_index)+1`, `warehouse.py:1242–1253`) and **INSERTs new sections** before applying the layout.
- The button (`warehouse.html`, Accept Layout) has **no `disabled`/in-flight guard**.

**Why harmony breaks:** Two overlapping requests both read the same `MAX(position_index)`, both create the requested N sections → **2N sections**. The `virtual_N` placeholders in the layout map resolve against different real section IDs between the two runs, so chemicals land in one set while a duplicate empty set is orphaned. Result: ghost sections plus placements split across the wrong sections — requires manual cleanup.

**Note:** `recommendation/apply` runs under `BEGIN EXCLUSIVE`, so the two runs are serialized — but serialization doesn't help, because each run *legitimately* creates a new batch of sections from a stale base index. The fix domain is **idempotency + client in-flight lock**, not locking alone.

---

## Finding 2.2 — No loading/disabled state on async actions — **High**

**Trigger:** Rapid clicks on **Auto-Arrange (AI Proposal)** or **Accept Layout**.

**Code path / evidence:** `triggerAutoArrange()` and `saveSuggestedLayout()` are `async` but set no `isLoading` flag; the buttons (`warehouse.html`) have no `:disabled`. Backend `auto_arrange` (`warehouse.py:955+`) has no per-request dedupe.

**Why harmony breaks:** Multiple concurrent `auto_arrange` POSTs race; the last response wins and the UI flickers through intermediate proposals. For Auto-Arrange this is "merely" confusing; it becomes Critical specifically through the `recommendation/apply` path (Finding 2.1). This finding is the **general missing-guard** of which 2.1 is the damaging instance.

---

## Finding 2.3 — Failed save leaves a stale ghost layout — **Medium**

**Trigger:** User clicks **Accept Layout**; backend returns **403 `CAUTION_REQUIRES_ADMIN`** or **409 `INCOMPATIBLE`** from `_validate_layout_update` (`warehouse.py:137–152`).

**Code path / evidence:** In `saveSuggestedLayout()`, `this.suggestedLayout = null` runs **only inside the `res.ok && data.success` branch**; the `else` branch just `alert()`s. Ghosts are rendered from `suggestedLayout`/`getMergedPlacements`, so they remain.

**Why harmony breaks:** After the error alert is dismissed, the proposed (rejected) layout is still painted into the sections. The user reasonably reads "ghosts on screen" as "saved," contradicting the error they just acknowledged. State must reset (or explicitly re-enter editable preview) on failure.

---

## Finding 2.4 — Client allows a placement the server will reject — **Medium**

**Trigger:** Non-admin builds a preview (or drags) such that a section contains a **NO_DATA** pair, then saves.

**Code path / evidence:**
- Client `handleDrop()` hard-blocks **only `INCOMPATIBLE`** (`warehouse.html`), letting CAUTION/NO_DATA through with a tooltip warning.
- Server manual-save policy (`_validate_layout_update`, `warehouse.py:144–152`) blocks CAUTION **and** NO_DATA for non-admins (`CAUTION_REQUIRES_ADMIN`).

**Why harmony breaks:** The client's permissive rule and the server's stricter rule disagree for NO_DATA/CAUTION, so a non-admin only discovers the block *after* committing the action — pairs with 2.3 to produce a confusing "accepted then denied" sequence.

> **Cross-reference to recent policy change.** Auto-arrange now isolates NO_DATA (`AUTO_ARRANGE_CONFLICT_COMPATIBILITIES`, `warehouse.py:29–33`) while *manual* save still treats NO_DATA as admin-overridable. That split is intentional and defensible, but the **client validation was not updated to mirror it**, which is the actual disharmony here.

---

## Finding 2.5 — Inventory quantity edit doesn't refresh an open warehouse view — **Medium**

**Trigger:** User edits a quantity in Inventory, then views Warehouse (or has it open in another tab).

**Code path / evidence:** Warehouse data is pulled once via `/api/warehouse/data` in `loadData()`; there is no event, poll, or invalidation after an inventory edit. (Independently, Finding 1.2 means the underlying warehouse value didn't change anyway.)

**Why harmony breaks:** Even if the propagation bug (1.2) were fixed, an already-rendered warehouse view would still show the old number until a manual reload. Two layers — broken write *and* no refresh — hide each other.

---

# Area 3 — Circular Dependencies & Dead Ends (The "Loops")

---

## Finding 3.1 — Force-password-change page is fragile and escape-less — **High**

**Trigger:** A freshly created user (or seeded admin) with `force_password_change = 1` logs in.

**Code path / evidence:**
- Middleware allow-list while the flag is set (`app.py:213–233`): `/auth/logout`, `/api/auth/change-password`, `/auth/change-password`, `/api/auth/me`, `/api/auth/csrf`. Everything else → redirect to `/auth/change-password` (page) or 403 `PASSWORD_CHANGE_REQUIRED` (API).
- The page route renders a **large inline HTML string with embedded JS** (`routes/auth.py:243+`), not a Jinja template.

**Why harmony breaks:** The allow-list itself is correct — the page and its API are reachable, so there is **no infinite redirect** in the current code (the investigator's "infinite loop" is not substantiated; downgraded accordingly). The real risk is **resilience**: the only way out of the forced-change gate is one hand-rolled inline page. If that string render throws, or its inline CSRF/JS breaks under a CSP or refactor, the user has **no fallback and no escape hatch** except `/auth/logout` — and logging back in returns them to the same gate. For an account-recovery surface, single-point fragility with no admin reset is a High concern.

> **Auditor note.** Reframed from "infinite redirect loop (HIGH)" to "fragile single-point recovery surface (HIGH)." Same severity, accurate mechanism.

---

## Finding 3.2 — Operators see warehouse mutate actions they can never execute — **High**

**Trigger:** A user with role `operator` opens the Warehouse view and attempts any move / auto-arrange / save.

**Code path / evidence:**
- Every warehouse mutating route is stacked `@login_required` → `@viewer_readonly` → `@csrf_protect` (e.g., `warehouse.py:397–401`, `955–958`).
- `viewer_readonly` (`auth/decorators.py:195`) blocks **both** `viewer` and `operator` on any `POST/PUT/DELETE/PATCH`, returning `403 OPERATOR_READONLY` — *before* the body runs.
- Therefore `_validate_layout_update`'s `CAUTION_REQUIRES_ADMIN` branch (`warehouse.py:147–152`) is **unreachable for operators**; only admins ever get that far.
- Template guards are inconsistent: the **Configure Sections** and **Auto-Arrange** buttons are wrapped in `{% if current_user.role in ['company_admin','super_admin'] %}` (`warehouse.html:260, 273`), but the **drag-and-drop placement UI and per-card delete/move controls are not role-gated**, so operators still see and can initiate them.

**Why harmony breaks:** The operator drags a chemical, the server answers `OPERATOR_READONLY` (a generic "you can't mutate") rather than anything actionable. The UI presents a capability the role fundamentally lacks — a dead-end. Either hide all mutate affordances for operators, or convert the block into a guided "request admin approval" path.

---

## Finding 3.3 — Super-admin nav links dead-end into tenant-only routes — **Medium**

**Trigger:** A `super_admin` clicks **Warehouse View**, **Inventory**, etc. in the sidebar.

**Code path / evidence:** `templates/base.html` shows Dashboard/Inventory/Matrix/Warehouse to all authenticated users (the per-link `{% if %}` guards cover only Logs/Users/Platform, `base.html:148–217`). Several tenant routes call `_require_tenant_db()` and 403 when there's no tenant context.

**Why harmony breaks:** Depending on how tenant context resolves for a super-admin (see Finding 4.2), these links either 403 ("Tenant context required") or silently render another tenant's data. Both are wrong: one is a dead-end, the other is a leak. At minimum the tenant-only links should be hidden from super-admins.

---

## Finding 3.4 — No guard against self-inflicted lockout — **Medium**

**Trigger:** (a) An admin account ends up with `force_password_change = 1` on itself and the old password is unknown; or (b) the last remaining admin of a company is suspended/demoted.

**Code path / evidence:** `suspend_user` guards self-suspension via `if target_user['id'] == g.user['id']` (`routes/admin.py` ~`:321`), but there is **no equivalent guard** for: setting `force_password_change` on oneself, demoting the last admin, or a "last admin standing" invariant. The change-password flow requires the old password (`auth.py` ~`:341`), so a forced-change with an unknown old password is terminal without a higher-privilege reset.

**Why harmony breaks:** The system can be driven into a state with no usable administrator and no in-product recovery. Today this is partly mitigated (no public endpoint sets `force_password_change` on existing users), so it's latent — Medium — but the missing invariant is real and one feature away from Critical.

---

# Area 4 — Multi-Tenant Cross-Contamination

This area corroborates the known **`RISK_HARDCODED_USER_DB_PATH`** entry in the risk register and adds a second, sharper issue around super-admin tenant resolution.

---

## Finding 4.1 — `g.tenant_db_path or USER_DB_PATH` fallback enables a shared-DB leak — **Critical**

**Trigger:** Any request where `g.tenant_db_path` is falsy (None) reaches a route that uses the legacy fallback.

**Code path / evidence — every occurrence:**
- `routes/warehouse.py:30` → `return getattr(g, 'tenant_db_path', None) or current_app.config['USER_DB_PATH']`
- `routes/inventory.py:33` → identical pattern
- `routes/inventory_actions.py:26` → identical pattern
- `routes/inventory_analysis.py` → four endpoints at ~`:233, :384, :486, :550` → `getattr(g,'tenant_db_path',None) or current_app.config['USER_DB_PATH']`
- App-level `get_user_db_connection()` uses the same `… or USER_DB_PATH` shape.

**Why harmony breaks:** Whenever the fallback fires, the handler operates on the **single shared `data/user.db`** instead of a tenant DB. Two different tenants (or a tenant and a super-admin) that both hit the fallback read and write the **same** favorites, staging, placements, and audit rows — a direct cross-tenant leak. This violates the project's own rule ("Use `g.tenant_db_path`, not `current_app.config['USER_DB_PATH']`"). The correct behavior on missing tenant context is **403**, never a silent shared default.

---

## Finding 4.2 — Super-admin is mapped to a real tenant's DB, contradicting the documented blind spot — **Critical**

**Trigger:** Any `super_admin` request after login.

**Code path / evidence:**
- The middleware docstring states the intended contract: `Super Admin blind spot: g.tenant_db_path = None` (`app.py:182`); the request default is even set to `None` at `app.py:185`.
- But the implementation (`app.py:236–250`) does: `SELECT id FROM companies WHERE id != 1 ORDER BY id ASC LIMIT 1` and sets `g.tenant_db_path = data/{that_id}_user.db`, falling back to `USER_DB_PATH` if there's no such company or on exception.

**Why harmony breaks:** Instead of having *no* tenant context, the super-admin silently inherits **the lowest-numbered real tenant's database**. They can read/export/analyze that company's private inventory, and any write lands in that tenant's DB. This is the exact opposite of the documented isolation guarantee, and it makes Finding 3.3's "dead-end" links actually render *another company's* data rather than 403. Code and the stated security model are in direct conflict; the security model should win.

---

## Finding 4.3 — `tenant_router` can construct `None_user.db` / crash on bad company_id — **High**

**Trigger:** An authenticated non-super-admin whose session/user record has a null or missing `company_id` (deleted company, partial provisioning, migration gap).

**Code path / evidence:** `app.py:252–259`: `company_id = user['company_id']` → `tenant_filename = f"{company_id}_user.db"` → if `company_id` is None this yields `"None_user.db"`; the code then `_init_tenant_db()`s a path derived from invalid input with no validation.

**Why harmony breaks:** Best case, a junk `None_user.db` file is created and the user silently operates in a nonexistent tenant; worse case, downstream operations 500. There is no `if company_id is None: → 401/403` guard at the boundary. Every authenticated route inherits the bad context.

---

## Finding 4.4 — Reactivity audit log silently dropped when tenant context is absent — **Medium**

**Trigger:** `ReactivityEngine.analyze(..., save_audit=True)` is called with no explicit `audit_db_path` while `g.tenant_db_path` is None.

**Code path / evidence:** `logic/reactivity_engine.py:376–386`: if no `audit_db_path` and no `g.tenant_db_path`, it logs a warning and `return None` — the audit row is skipped (it does **not** fall back to `USER_DB_PATH`, which is the *safe* choice for isolation but means **lost audit data**).

**Why harmony breaks:** Today all in-tree callers either pass `audit_db_path` explicitly (the `/api/analyze` path) or set `save_audit=False` (warehouse auto-arrange), so this is latent. But any future call that forgets both will silently lose the safety audit trail for super-admin/edge contexts. Flagged Medium as a resilience gap, not an active bug.

> **Consistency observation.** Note the engine chooses to **drop** rather than fall back to the shared DB (good for isolation), while the route helpers in 4.1 choose to **fall back** to the shared DB (bad for isolation). The two halves of the codebase disagree on the correct failure mode for "no tenant context." Unifying on *fail-closed (403 / skip)* would resolve both 4.1 and 4.4.

---

# Cross-cutting themes (for sprint planning)

1. **The `placed_by` string is load-bearing and under-engineered.** Findings 1.1, 1.2, 1.3, and 1.4 all trace to a denormalized `import:{batch}:{row}` key with no FK, inconsistent formatting between writer (`warehouse.py:711`) and reader (`inventory_actions.py:213`), and no uniqueness constraint. A single modeling fix (real foreign key + consistent key helper) collapses four findings.

2. **"No tenant context" has two contradictory behaviors.** Route helpers fall back to a shared DB (leak, 4.1/4.2); the reactivity engine fails closed (4.4). Pick fail-closed everywhere and the Critical tenancy leaks close.

3. **Optimistic UI without in-flight locks or failure-reset.** Findings 2.1–2.4 are one pattern: the client mutates/keeps state assuming success, with no disabled-during-flight and no reset-on-error. A small shared "async action" wrapper (disable + reset) addresses the cluster, with 2.1 as the must-fix instance.

4. **Role affordances aren't aligned with role permissions.** Operators are shown warehouse mutate controls they cannot use (3.2); super-admins are shown tenant links that dead-end or leak (3.3/4.2). Template role-gating should be derived from the same role matrix the decorators enforce.

---

## Verification log (what the lead auditor re-checked against source)

| Claim re-checked | Source verified | Outcome |
|---|---|---|
| Quantity propagation corrupts all placements | `inventory_actions.py:213,248–252` vs `warehouse.py:711` | **Corrected** → silent no-op (key mismatch), not corruption (1.2) |
| Duplicate-import is a live race | `warehouse.py:584 (BEGIN EXCLUSIVE), 704–709` | **Downgraded** High→Medium; EXCLUSIVE serializes (1.4) |
| Force-password-change is an infinite redirect | `app.py:213–233`, `auth.py:243+` | **Reframed** → allow-list is correct; real risk is fragility/no escape (3.1) |
| Super-admin blind spot = `None` | `app.py:182,185` vs `236–250` | **Confirmed contradiction**; code maps to real tenant DB (4.2) |
| `USER_DB_PATH` fallback locations | `warehouse.py:30`, `inventory.py:33`, `inventory_actions.py:26`, `inventory_analysis.py:233/384/486/550` | **Confirmed** (4.1) |
| Batch delete touches `chemical_placements` | `inventory.py:304–348` | **Confirmed** it does not (1.1) |

---

*End of report. No code was modified. Findings are ordered by area; severities reflect post-verification grading and may differ from the raw investigator notes where the Verification log records a correction.*
