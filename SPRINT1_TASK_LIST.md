# SAFEWARE-CAMEO Sprint 1 — Complete Task List for External Agent

**Purpose:** This document contains ALL fixes, code changes, and test commands for Sprint 1. Another agent can execute these tasks independently and verify the results.

**Working Directory:** `C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend`

---

## TASK 1: Tenant DB Schema Gap (P0 Critical)

**Problem:** Tenant databases don't get `analysis_results` and `user_inventories` tables, causing 500 errors on compliance export.

**File:** `etl/pipeline.py`

**Change:** In `init_inventory_tables()` function, add two CREATE TABLE statements BEFORE the "Safe Column Migration" section (around line 199):

```python
    # Phase 2: User-managed finalized inventory
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_inventories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            chemical_id INTEGER NOT NULL,
            quantity TEXT,
            unit TEXT,
            storage_location TEXT,
            notes TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_inventories_batch ON user_inventories(batch_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_inventories_chemical ON user_inventories(chemical_id)")

    # Phase 2: Analysis result storage
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL,
            analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_chemicals INTEGER,
            dangerous_pairs INTEGER,
            storage_warnings INTEGER,
            risk_matrix_json TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_results_batch ON analysis_results(batch_id)")
```

**Test:** `python -m pytest tests/test_p0_critical_fixes.py::TestFix1TenantDBSchema -v`

---

## TASK 2: confirm_match Orphans Review Queue (P0 Critical)

**Problem:** When user confirms a match, review_queue stays 'pending', audit_trail is not created, learning_data is not stored.

**File:** `routes/inventory.py`

**Change:** Replace the entire `confirm_match()` function (around line 302-339) with:

```python
@inventory_bp.route('/api/inventory/confirm', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def confirm_match():
    """
    Human-in-the-loop: confirm a row's chemical match.
    Body: { staging_id: int, chemical_id: int, chemical_name: str }
    Also resolves any pending review_queue entry and records audit trail + learning data.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400

    staging_id = data.get('staging_id')
    chemical_id = data.get('chemical_id')
    chemical_name = data.get('chemical_name', '')

    if not staging_id or not chemical_id:
        return jsonify({'error': 'staging_id and chemical_id are required'}), 400

    # Anti-Hallucination: verify chemical_id exists in chemicals.db
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cursor_chem = conn_chem.cursor()
    cursor_chem.execute("SELECT id, name FROM chemicals WHERE id = ?", (chemical_id,))
    chem = cursor_chem.fetchone()
    conn_chem.close()

    if not chem:
        return jsonify({'error': f'chemical_id {chemical_id} does not exist in database'}), 400

    user_db = _get_db_path()
    conn = get_safe_connection(user_db)
    cursor = conn.cursor()

    # Update staging row
    cursor.execute("""
        UPDATE inventory_staging
        SET chemical_id = ?, match_status = 'MATCHED',
            match_method = 'manual_confirm', confidence = 1.0
        WHERE id = ?
    """, (chemical_id, staging_id))
    success = cursor.rowcount > 0

    if not success:
        conn.close()
        return jsonify({'error': 'Row not found'}), 404

    # Resolve any pending review_queue entry for this staging row
    cursor.execute("SELECT id, batch_id, input_data FROM review_queue WHERE staging_id = ? AND status = 'pending'", (staging_id,))
    rq = cursor.fetchone()
    batch_id = None
    input_data = '{}'
    if rq:
        batch_id = rq['batch_id']
        input_data = rq['input_data'] or '{}'
        cursor.execute("""
            UPDATE review_queue
            SET status = 'resolved', resolution = ?, resolution_timestamp = ?
            WHERE id = ?
        """, (json.dumps({'chemical_id': chemical_id, 'chemical_name': chem['name']}),
              datetime.utcnow().isoformat(), rq['id']))

    # If no batch_id from review_queue, get it from staging
    if not batch_id:
        cursor.execute("SELECT batch_id, raw_data FROM inventory_staging WHERE id = ?", (staging_id,))
        staging_row = cursor.fetchone()
        if staging_row:
            batch_id = staging_row['batch_id']
            input_data = staging_row['raw_data'] or '{}'

    # Store in learning_data for future improvement
    cursor.execute("""
        INSERT INTO learning_data
            (input_pattern, context, correct_chemical_id, corrected_by)
        VALUES (?, ?, ?, 'manual_confirm')
    """, (input_data, json.dumps({'batch_id': batch_id}), chemical_id))

    # Audit trail
    cursor.execute("""
        INSERT INTO audit_trail
            (batch_id, row_index, action, input_data, output_data,
             confidence, method, timestamp, user_id)
        VALUES (?, (SELECT row_index FROM inventory_staging WHERE id = ?),
                'manual_confirm', ?, ?, 1.0, 'manual_confirm', ?, 'human')
    """, (batch_id, staging_id, input_data,
          json.dumps({'chemical_id': chemical_id, 'chemical_name': chem['name']}),
          datetime.utcnow().isoformat()))

    conn.commit()
    conn.close()

    return jsonify({'success': True, 'chemical_name': chem['name']})
```

**Test:** `python -m pytest tests/test_p0_critical_fixes.py::TestFix2ConfirmMatchReviewQueue -v`

---

## TASK 3: Dangerous Generic Synonyms (P0 Critical)

**Problem:** 'Salt' auto-commits as Sodium Chloride at 100% confidence. 'Alcohol' and 'Peroxide' are equally dangerous.

**File:** `etl/match.py`

**Change:** In `INDUSTRIAL_SYNONYMS` dictionary (around line 53), REMOVE these entries:
```python
# REMOVE these three lines:
'alcohol': 'ethanol',
'table salt': 'sodium chloride',
'salt': 'sodium chloride',
'peroxide': 'hydrogen peroxide',
```

**Test:** `python -m pytest tests/test_p0_critical_fixes.py::TestFix3GenericSynonymsRemoved -v`

---

## TASK 4: Compliance DB Isolation (P0 Security)

**Problem:** `compliance.py` has a hardcoded fallback path for chemicals.db.

**File:** `routes/compliance.py`

**Change:** Around line 65-68, replace:
```python
    db_path = current_app.config.get(
        "CHEMICALS_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "chemicals.db"),
    )
```
With:
```python
    db_path = current_app.config['CHEMICALS_DB_PATH']
```

**Test:** `python -m pytest tests/test_p0_batch2_fixes.py::TestComplianceDBIsolation -v`

---

## TASK 5: Self-Hazard Escalation (P0 Security)

**Problem:** Self-hazard escalation uses `if` instead of `max()`, may not reliably escalate.

**File:** `logic/reactivity_engine.py`

**Change:** Around line 528-531, replace:
```python
                        # Escalate overall compatibility priority for self-hazard
                        self_priority = COMPATIBILITY_MAP[Compatibility.CAUTION].priority
                        if self_priority > overall_max_priority:
                            overall_max_priority = self_priority
```
With:
```python
                        # Escalate overall compatibility priority for self-hazard (at least CAUTION)
                        self_priority = COMPATIBILITY_MAP[Compatibility.CAUTION].priority
                        overall_max_priority = max(overall_max_priority, self_priority)
```

**Test:** `python -m pytest tests/test_p0_batch2_fixes.py::TestSelfHazardEscalation -v`

---

## TASK 6: Container Unit Conversion (P1 Functional)

**Problem:** '5 drums' silently becomes 1.0 kg. 'Full' also becomes 1.0 kg.

**File:** `routes/warehouse.py`

**Change:** Around line 619-627 in `add_from_batch`, replace the quantity parsing block:
```python
            # Parse quantity to kg
            try:
                qty = float(qty_str)
                if unit_str in ('g', 'grams', 'gr'):
                    qty /= 1000.0
                elif unit_str in ('lb', 'lbs', 'pounds'):
                    qty *= 0.453592
            except Exception:
                qty = 1.0
```
With:
```python
            # Parse quantity to kg
            CONTAINER_KG = {
                'drum': 200, 'drums': 200,
                'cylinder': 50, 'cylinders': 50,
                'bottle': 2, 'bottles': 2,
                'jug': 4, 'jugs': 4,
                'container': 10, 'containers': 10,
                'tank': 500, 'tanks': 500,
                'pail': 20, 'pails': 20,
                'bag': 25, 'bags': 25,
                'sack': 50, 'sacks': 50,
                'tote': 1000, 'totes': 1000,
                'keg': 60, 'kegs': 60,
            }
            try:
                qty = float(qty_str)
                if unit_str in ('g', 'grams', 'gr'):
                    qty /= 1000.0
                elif unit_str in ('lb', 'lbs', 'pounds'):
                    qty *= 0.453592
                elif unit_str in ('oz', 'ounces'):
                    qty *= 0.0283495
                elif unit_str in ('ton', 'tons'):
                    qty *= 907.185
                elif unit_str in ('mt', 'metric ton', 'metric tons', 'tonnes'):
                    qty *= 1000.0
                elif unit_str in CONTAINER_KG:
                    qty *= CONTAINER_KG[unit_str]
            except (ValueError, TypeError):
                qty = None
```

**Test:** `python -m pytest tests/test_p1_batch3_fixes.py::TestContainerUnitConversion -v`

---

## TASK 7: Stale cleaned_data on Override (P1 Functional)

**Problem:** resolve_review updates chemical_id but leaves old name in cleaned_data JSON.

**File:** `routes/inventory.py`

**Change:** In `resolve_review()` function, after getting the staging_id and batch_id (around line 586-587), ADD this block BEFORE the staging UPDATE:

```python
    # Fetch current cleaned_data to update name/cas
    cursor.execute("SELECT cleaned_data FROM inventory_staging WHERE id = ?", (staging_id,))
    staging_row = cursor.fetchone()
    cleaned = {}
    if staging_row and staging_row['cleaned_data']:
        try:
            cleaned = json.loads(staging_row['cleaned_data'])
        except (json.JSONDecodeError, TypeError):
            pass

    # Update cleaned_data with confirmed chemical info
    cleaned['name'] = chem['name']
    # Also update CAS if available from chemicals.db
    cursor_chem2 = conn_chem.cursor() if not conn_chem.closed else None
    if cursor_chem2 is None:
        conn_chem2 = get_safe_connection(chemicals_db, readonly=True)
        cursor_chem2 = conn_chem2.cursor()
    cursor_chem2.execute(
        "SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1",
        (chemical_id,)
    )
    cas_row = cursor_chem2.fetchone()
    if cas_row:
        cleaned['cas'] = cas_row['cas_id']
        cleaned['cas_valid'] = True
    conn_chem2.close()
```

Then UPDATE the staging UPDATE statement to include `cleaned_data = ?`:
```python
    cursor.execute("""
        UPDATE inventory_staging
        SET chemical_id = ?, match_status = 'MATCHED',
            match_method = 'manual_review', confidence = 1.0,
            cleaned_data = ?
        WHERE id = ?
    """, (chemical_id, json.dumps(cleaned), staging_id))
```

**Test:** `python -m pytest tests/test_p1_batch3_fixes.py::TestStaleCleanedDataOnOverride -v`

---

## TASK 8: Water Reactivity in Auto-Arrange (P1 Functional)

**Problem:** Auto-arrange ignores water-reactive self-hazards.

**File:** `routes/warehouse.py`

**Change 1:** Add import at top (around line 9):
```python
from logic.constants import Compatibility, COMPATIBILITY_MAP, WATER_GROUP_ID
```

**Change 2:** Add helper functions BEFORE `_is_section_conflict` (around line 699):
```python
def _is_water_reactive(chemical_id, chemicals_db_path):
    """Check if a chemical has a water-reactive self-hazard in special_hazards."""
    try:
        conn = get_safe_connection(chemicals_db_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT special_hazards FROM chemicals WHERE id = ?", (chemical_id,))
        row = cur.fetchone()
        conn.close()
        if row and row['special_hazards']:
            special = row['special_hazards'].lower()
            return 'water reactive' in special or 'water-reactive' in special
    except Exception:
        pass
    return False


def _has_water_group(placement):
    """Check if a chemical belongs to the Water reactive group (104)."""
    groups = _groups_for_placement(placement)
    return WATER_GROUP_ID in groups
```

**Change 3:** Replace `_is_section_conflict` function:
```python
def _is_section_conflict(engine, placement_a, placement_b):
    pair_res = engine._analyze_pair(
        placement_a['chemical_id'],
        placement_b['chemical_id'],
        placement_a['chemical_name'],
        placement_b['chemical_name'],
        _groups_for_placement(placement_a),
        _groups_for_placement(placement_b),
    )
    if pair_res.compatibility in SECTION_CONFLICT_COMPATIBILITIES:
        return True

    # Water-reactive self-hazard check:
    # If one chemical is water-reactive AND the other has water group, block.
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    a_water_reactive = _is_water_reactive(placement_a['chemical_id'], chemicals_db)
    b_water_reactive = _is_water_reactive(placement_b['chemical_id'], chemicals_db)
    a_has_water = _has_water_group(placement_a)
    b_has_water = _has_water_group(placement_b)

    if (a_water_reactive and b_has_water) or (b_water_reactive and a_has_water):
        return True

    return False
```

**Test:** `python -m pytest tests/test_p1_batch3_fixes.py::TestWaterReactivityInAutoArrange -v`

---

## TASK 9: Inventory→Warehouse Propagation (P2 Integration)

**Problem:** Staging edits don't update warehouse placements.

**File 1:** `routes/inventory_actions.py`

**Change:** Add `_propagate_to_warehouse()` function BEFORE `edit_inventory_row` route (around line 58):
```python
def _propagate_to_warehouse(cursor, batch_id, staging_id, new_chemical_id, chem):
    """Update chemical_placements if this batch was already imported to warehouse."""
    if not chem:
        return

    chemicals_db = current_app.config['CHEMICALS_DB_PATH']

    # Get new chemical info
    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cur = conn_chem.cursor()
    cur.execute("SELECT name FROM chemicals WHERE id = ?", (new_chemical_id,))
    name_row = cur.fetchone()
    chem_name = name_row['name'] if name_row else chem.get('name', '')

    cur.execute("SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1", (new_chemical_id,))
    cas_row = cur.fetchone()
    cas_number = cas_row['cas_id'] if cas_row else ''
    conn_chem.close()

    # Get reactive groups
    conn_g = get_safe_connection(chemicals_db, readonly=True)
    cur_g = conn_g.cursor()
    cur_g.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (new_chemical_id,))
    groups = [r[0] for r in cur_g.fetchall()]
    conn_g.close()
    groups_json = json.dumps(groups)

    # Find the OLD chemical_id from the staging row
    cursor.execute("SELECT chemical_id FROM inventory_staging WHERE id = ?", (staging_id,))
    staging_row = cursor.fetchone()
    if not staging_row or not staging_row['chemical_id']:
        return
    old_chemical_id = staging_row['chemical_id']

    if old_chemical_id == new_chemical_id:
        return

    # Update warehouse placements imported from this batch
    import_tag = f"import:{batch_id}"
    cursor.execute(
        """UPDATE chemical_placements
           SET chemical_id = ?, chemical_name = ?, cas_number = ?, reactive_groups = ?
           WHERE chemical_id = ? AND placed_by = ?""",
        (new_chemical_id, chem_name, cas_number, groups_json, old_chemical_id, import_tag)
    )
    if cursor.rowcount > 0:
        logger.info(
            "Propagated to warehouse: %d placement(s) updated "
            "(old_id=%s -> new_id=%s, batch=%s)",
            cursor.rowcount, old_chemical_id, new_chemical_id, batch_id[:8]
        )
```

Then in `edit_inventory_row()`, AFTER `conn.commit()` (around line 186), ADD:
```python
        # ── Propagate to warehouse if batch was already imported ──
        old_chem_id = row['chemical_id'] if 'chemical_id' in row.keys() else None
        if new_chemical_id and new_chemical_id != old_chem_id:
            _propagate_to_warehouse(cursor, batch_id, staging_id, new_chemical_id, chem)
            conn.commit()
```

**File 2:** `routes/inventory.py`

**Change:** Add same `_propagate_to_warehouse()` function AFTER `_get_db_path()` (around line 32):
```python
def _propagate_to_warehouse(cursor, batch_id, staging_id, new_chemical_id, chem):
    """Update chemical_placements if this batch was already imported to warehouse."""
    if not chem:
        return

    chemicals_db = current_app.config['CHEMICALS_DB_PATH']

    # Get new chemical info
    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cur = conn_chem.cursor()
    cur.execute("SELECT name FROM chemicals WHERE id = ?", (new_chemical_id,))
    name_row = cur.fetchone()
    chem_name = name_row['name'] if name_row else chem.get('name', '')

    cur.execute("SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1", (new_chemical_id,))
    cas_row = cur.fetchone()
    cas_number = cas_row['cas_id'] if cas_row else ''
    conn_chem.close()

    # Get reactive groups
    conn_g = get_safe_connection(chemicals_db, readonly=True)
    cur_g = conn_g.cursor()
    cur_g.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (new_chemical_id,))
    groups = [r[0] for r in cur_g.fetchall()]
    conn_g.close()
    groups_json = json.dumps(groups)

    # Find the OLD chemical_id from the staging row
    cursor.execute("SELECT chemical_id FROM inventory_staging WHERE id = ?", (staging_id,))
    staging_row = cursor.fetchone()
    if not staging_row or not staging_row['chemical_id']:
        return
    old_chemical_id = staging_row['chemical_id']

    if old_chemical_id == new_chemical_id:
        return

    # Update warehouse placements imported from this batch
    import_tag = f"import:{batch_id}"
    cursor.execute(
        """UPDATE chemical_placements
           SET chemical_id = ?, chemical_name = ?, cas_number = ?, reactive_groups = ?
           WHERE chemical_id = ? AND placed_by = ?""",
        (new_chemical_id, chem_name, cas_number, groups_json, old_chemical_id, import_tag)
    )
    if cursor.rowcount > 0:
        logger.info(
            "Propagated to warehouse: %d placement(s) updated "
            "(old_id=%s -> new_id=%s, batch=%s)",
            cursor.rowcount, old_chemical_id, new_chemical_id, batch_id[:8]
        )
```

Then in `resolve_review()`, AFTER the staging UPDATE and BEFORE the review_queue UPDATE, ADD:
```python
    # Propagate to warehouse if batch was already imported
    _propagate_to_warehouse(cursor, batch_id, staging_id, chemical_id, chem)
```

**Test:** `python -m pytest tests/test_p2_batch4_fixes.py::TestInventoryToWarehousePropagation -v`

---

## TASK 10: Warehouse Import Skipped Row Count (P2 Integration)

**Problem:** add_from_batch silently skips unresolved rows without telling the user.

**File:** `routes/warehouse.py`

**Change:** In `add_from_batch()`, AFTER the `rows = cursor.fetchall()` line (around line 597), ADD:
```python
        # Count total and skipped rows for reporting
        cursor.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN match_status = 'MATCHED' THEN 1 ELSE 0 END) as matched FROM inventory_staging WHERE batch_id = ?",
            (batch_id,)
        )
        count_row = cursor.fetchone()
        total_rows = count_row['total'] if count_row else 0
        matched_rows = count_row['matched'] if count_row else 0
        skipped_count = total_rows - matched_rows
```

Then REPLACE the return jsonify block (around line 690-694):
```python
        return jsonify({
            'success': True, 
            'warehouse_id': warehouse_id,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'message': f'Successfully imported {imported_count} chemicals to the warehouse pool.'
                       + (f' {skipped_count} rows skipped (not yet matched).' if skipped_count > 0 else '')
        })
```

**Test:** `python -m pytest tests/test_p2_batch4_fixes.py::TestSkippedRowCount -v`

---

## TASK 11: Auto-Arrange Admin Override Flag (P2 Integration)

**Problem:** Auto-arrange proposes layouts with CAUTION pairs that non-admins cannot save.

**File:** `routes/warehouse.py`

**Change 1:** In `auto_arrange()`, in the safety score calculation section (around line 1032-1049), ADD `requires_admin_override = False` and set it:
```python
        total_sections = len(sections)
        hazard_free_sections = total_sections
        caution_sections_count = 0
        incompatible_sections_count = 0
        requires_admin_override = False
        
        for section_id, occupants in final_by_section.items():
            if len(occupants) < 2:
                continue
            chem_ids = [o['chemical_id'] for o in occupants]
            analysis = engine.analyze(chem_ids, include_water_check=True, save_audit=False)
            if analysis.overall_compatibility == Compatibility.INCOMPATIBLE:
                incompatible_sections_count += 1
                hazard_free_sections -= 1
            elif analysis.overall_compatibility in (Compatibility.CAUTION, Compatibility.NO_DATA):
                caution_sections_count += 1
                hazard_free_sections -= 1
                requires_admin_override = True
```

**Change 2:** In the return jsonify block (around line 1056-1067), ADD the new fields:
```python
        return jsonify({
            'success': True,
            'layout': mapping,
            'suggested_layout': mapping,
            'unplaced': [p['placement_id'] for p in unplaced],
            'safety_score': safety_score,
            'confidence_score': safety_score,
            'requires_admin_override': requires_admin_override,
            'caution_sections': caution_sections_count,
            'warnings': warnings,
            'recommendation': recommendation,
            'exact_complete': exact_complete,
            'message': msg
        })
```

**Test:** `python -m pytest tests/test_p2_batch4_fixes.py::TestAutoArrangeAdminOverride -v`

---

## TASK 12: confirm_match Updates cleaned_data (Bug Fix)

**Problem:** confirm_match updates chemical_id but leaves old name in cleaned_data JSON.

**File:** `routes/inventory.py`

**Change:** In `confirm_match()`, BEFORE the staging UPDATE, add cleaned_data update:

```python
    # Fetch current cleaned_data to update name/cas
    cursor.execute("SELECT cleaned_data FROM inventory_staging WHERE id = ?", (staging_id,))
    staging_row = cursor.fetchone()
    cleaned = {}
    if staging_row and staging_row['cleaned_data']:
        try:
            cleaned = json.loads(staging_row['cleaned_data'])
        except (json.JSONDecodeError, TypeError):
            pass

    # Update cleaned_data with confirmed chemical info
    cleaned['name'] = chem['name']
    conn_c = get_safe_connection(chemicals_db, readonly=True)
    cur_c = conn_c.cursor()
    cur_c.execute("SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1", (chemical_id,))
    cas_row = cur_c.fetchone()
    if cas_row:
        cleaned['cas'] = cas_row['cas_id']
        cleaned['cas_valid'] = True
    conn_c.close()
```

Then update the staging UPDATE to include `cleaned_data = ?`.

**Test:** `python -m pytest tests/test_p0_critical_fixes.py::TestFix2ConfirmMatchReviewQueue -v`

---

## TASK 13: _propagate_to_warehouse Accepts old_chemical_id (Bug Fix)

**Problem:** _propagate_to_warehouse reads old chemical_id from DB AFTER update, so old==new and propagation is skipped.

**Files:** `routes/inventory_actions.py` and `routes/inventory.py`

**Change:** Update function signature from:
```python
def _propagate_to_warehouse(cursor, batch_id, staging_id, new_chemical_id, chem):
```
To:
```python
def _propagate_to_warehouse(cursor, batch_id, old_chemical_id, new_chemical_id, chem):
```

Remove the DB query that reads old_chemical_id (it's now passed as parameter).

Update call sites to pass old_chemical_id captured BEFORE the staging UPDATE.

**Test:** `python -m pytest tests/test_p2_batch4_fixes.py::TestInventoryToWarehousePropagation -v`

---

## VERIFICATION: Run Full Regression

After ALL tasks are complete, run:
```bash
cd C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend
python -m pytest tests/ -v --ignore=tests/etl_comprehensive_stress_test.py --ignore=tests/test_full_database_stress.py --ignore=tests/generate_*.py
```

**Expected result: 136 passed, 0 failed**

---

## Quick Reference: Files to Modify

| Task | File | Lines (approx) |
|------|------|-----------------|
| 1 | `etl/pipeline.py` | ~199 (before column migration) |
| 2 | `routes/inventory.py` | ~302-339 (replace confirm_match) |
| 3 | `etl/match.py` | ~53-88 (remove 4 synonyms) |
| 4 | `routes/compliance.py` | ~65-68 (simplify db_path) |
| 5 | `logic/reactivity_engine.py` | ~528-531 (if → max) |
| 6 | `routes/warehouse.py` | ~619-627 (add container conversion) |
| 7 | `routes/inventory.py` | ~586-623 (add cleaned_data update) |
| 8 | `routes/warehouse.py` | ~9, ~699 (add import + helpers + update conflict) |
| 9 | `routes/inventory_actions.py` + `routes/inventory.py` | ~58 (add function) + ~186 (add call) |
| 10 | `routes/warehouse.py` | ~597, ~690 (add count + response) |
| 11 | `routes/warehouse.py` | ~1032, ~1056 (add flag + response) |
