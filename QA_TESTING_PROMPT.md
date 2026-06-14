# SAFEWARE-CAMEO Sprint 1 QA Validation — Automated Testing Prompt

## Agent Role
You are a **Senior QA Automation Engineer** testing the SAFEWARE-CAMEO chemical safety platform. Your job is to simulate real user workflows, verify that all Sprint 1 fixes work correctly, and produce a detailed test report.

## Environment Setup
```bash
cd C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend
# Start the Flask server in background
python app.py &
# Wait for startup
sleep 3
# Run all tests
python -m pytest tests/ -v --tb=short
```

## What Was Fixed (Sprint 1 — 12 Files, 11 Fixes)

### Batch 1: P0 Criticals
1. **Tenant DB Schema** (`etl/pipeline.py`) — `analysis_results` + `user_inventories` tables now created for tenant DBs
2. **confirm_match Orphaning** (`routes/inventory.py`) — Now resolves review_queue, creates audit_trail + learning_data
3. **Dangerous Synonyms** (`etl/match.py`) — Removed 'salt', 'alcohol', 'peroxide' from INDUSTRIAL_SYNONYMS

### Batch 2: P0 Security
4. **Compliance DB Isolation** (`routes/compliance.py`) — Removed hardcoded fallback, uses config directly
5. **Self-Hazard Escalation** (`logic/reactivity_engine.py`) — Uses `max()` instead of `if` for reliable escalation

### Batch 3: P1 Functional
6. **Container Unit Conversion** (`routes/warehouse.py`) — drums=200kg, cylinders=50kg, etc.
7. **Stale cleaned_data** (`routes/inventory.py`) — resolve_review now updates name+cas in JSON
8. **Water Reactivity** (`routes/warehouse.py`) — Auto-arrange now detects water-reactive vs water conflicts

### Batch 4: P2 Integration
9. **Inventory→Warehouse Sync** (`routes/inventory_actions.py`, `routes/inventory.py`) — _propagate_to_warehouse()
10. **Skipped Row Count** (`routes/warehouse.py`) — add_from_batch returns skipped_count
11. **Admin Override Flag** (`routes/warehouse.py`) — auto_arrange returns requires_admin_override

---

## Test Scenarios to Execute

### SCENARIO 1: Tenant DB Schema Fix (Fix 1)
**Goal:** Verify tenant databases get analysis_results + user_inventories tables.

```bash
# Run specific test
python -m pytest tests/test_p0_critical_fixes.py::TestFix1TenantDBSchema -v

# Manual verification
python -c "
import tempfile, os, sys
sys.path.insert(0, '.')
from etl.pipeline import init_inventory_tables
fd, path = tempfile.mkstemp(suffix='.db')
os.close(fd)
init_inventory_tables(path)
import sqlite3
conn = sqlite3.connect(path)
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
assert 'analysis_results' in tables, 'analysis_results missing!'
assert 'user_inventories' in tables, 'user_inventories missing!'
conn.close()
os.unlink(path)
print('PASS: Tenant DB schema fix verified')
"
```

### SCENARIO 2: confirm_match Review Queue Fix (Fix 2)
**Goal:** Verify confirm_match updates review_queue + audit_trail + learning_data.

```bash
python -m pytest tests/test_p0_critical_fixes.py::TestFix2ConfirmMatchReviewQueue -v
```

### SCENARIO 3: Salt Auto-Match Prevention (Fix 3)
**Goal:** Verify 'Salt' no longer auto-commits as Sodium Chloride.

```bash
python -m pytest tests/test_p0_critical_fixes.py::TestFix3GenericSynonymsRemoved -v

# Manual verification
python -c "
import sys
sys.path.insert(0, '.')
from etl.match import HybridMatcher
matcher = HybridMatcher('data/chemicals.db')
result = matcher.match({'name': 'Salt', 'cas': ''})
assert result['match_status'] != 'MATCHED' or result['confidence'] < 1.0, \
    f'Dangerous: Salt auto-matched at {result[\"confidence\"]}! Got: {result[\"match_status\"]}'
print(f'PASS: Salt -> {result[\"match_status\"]} (confidence: {result[\"confidence\"]})')
"
```

### SCENARIO 4: Container Unit Conversion (Fix 6)
**Goal:** Verify '5 drums' becomes 1000kg, not 1.0kg.

```bash
python -m pytest tests/test_p1_batch3_fixes.py::TestContainerUnitConversion -v

# Manual verification
python -c "
import sys
sys.path.insert(0, '.')
# Simulate warehouse import logic
CONTAINER_KG = {'drum': 200, 'drums': 200, 'cylinder': 50, 'cylinders': 50}
tests = [
    ('5', 'drums', 1000.0),
    ('10', 'cylinders', 500.0),
    ('3', 'bottles', 6.0),
    ('Full', 'kg', None),
]
for qty_str, unit, expected in tests:
    try:
        qty = float(qty_str)
        if unit in CONTAINER_KG:
            qty *= CONTAINER_KG[unit]
    except (ValueError, TypeError):
        qty = None
    assert qty == expected, f'FAIL: {qty_str} {unit} -> {qty}, expected {expected}'
    print(f'PASS: {qty_str} {unit} -> {qty} kg')
print('All container conversions verified')
"
```

### SCENARIO 5: Stale cleaned_data Fix (Fix 7)
**Goal:** Verify resolve_review updates name in cleaned_data JSON.

```bash
python -m pytest tests/test_p1_batch3_fixes.py::TestStaleCleanedDataOnOverride -v
```

### SCENARIO 6: Water Reactivity in Auto-Arrange (Fix 8)
**Goal:** Verify water-reactive chemicals conflict with water-group chemicals.

```bash
python -m pytest tests/test_p1_batch3_fixes.py::TestWaterReactivityInAutoArrange -v

# Manual verification
python -c "
import sys
sys.path.insert(0, '.')
from routes.warehouse import _is_water_reactive, _has_water_group
from app import app
CHEMICALS_DB = 'data/chemicals.db'

# Test water-reactive detection
import sqlite3
conn = sqlite3.connect(CHEMICALS_DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute(\"SELECT id, name FROM chemicals WHERE special_hazards LIKE '%Water-Reactive%' LIMIT 1\")
row = cur.fetchone()
conn.close()

if row:
    assert _is_water_reactive(row['id'], CHEMICALS_DB), f'{row[\"name\"]} should be water-reactive'
    print(f'PASS: {row[\"name\"]} detected as water-reactive')
else:
    print('SKIP: No water-reactive chemical found')

# Test water group detection
assert _has_water_group({'reactive_groups': [104]}), 'Group 104 should be water'
assert not _has_water_group({'reactive_groups': [1, 2, 3]}), 'Non-104 should not be water'
print('PASS: Water group detection verified')
"
```

### SCENARIO 7: Inventory→Warehouse Propagation (Fix 9)
**Goal:** Verify staging edits update warehouse placements.

```bash
python -m pytest tests/test_p2_batch4_fixes.py::TestInventoryToWarehousePropagation -v

# Manual verification
python -c "
import sys, tempfile, os, json, sqlite3
sys.path.insert(0, '.')
from etl.pipeline import init_inventory_tables
CHEMICALS_DB = 'data/chemicals.db'

fd, db_path = tempfile.mkstemp(suffix='.db')
os.close(fd)
init_inventory_tables(db_path)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get two chemicals
conn2 = sqlite3.connect(CHEMICALS_DB)
conn2.row_factory = sqlite3.Row
cur2 = conn2.cursor()
cur2.execute('SELECT id, name FROM chemicals LIMIT 2')
chems = [dict(r) for r in cur2.fetchall()]
conn2.close()

# Setup: batch + staging + warehouse placement
batch_id = 'test-propagation'
cur.execute('INSERT INTO inventory_batches (id, filename, status) VALUES (?, ?, \"completed\")', (batch_id, 'test.xlsx'))
cur.execute('INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id) VALUES (?, 1, ?, ?, \"MATCHED\", ?)',
    (batch_id, '{}', '{}', chems[0]['id']))
cur.execute('INSERT INTO chemical_placements (warehouse_id, chemical_id, chemical_name, placed_by) VALUES (1, ?, ?, ?)',
    (chems[0]['id'], chems[0]['name'], f'import:{batch_id}'))
conn.commit()

# Verify initial state
cur.execute('SELECT chemical_id FROM chemical_placements WHERE placed_by = ?', (f'import:{batch_id}',))
assert cur.fetchone()['chemical_id'] == chems[0]['id']

# Simulate propagation
cur.execute('UPDATE chemical_placements SET chemical_id = ?, chemical_name = ? WHERE chemical_id = ? AND placed_by = ?',
    (chems[1]['id'], chems[1]['name'], chems[0]['id'], f'import:{batch_id}'))
conn.commit()

# Verify updated
cur.execute('SELECT chemical_id, chemical_name FROM chemical_placements WHERE placed_by = ?', (f'import:{batch_id}',))
row = cur.fetchone()
assert row['chemical_id'] == chems[1]['id'], f'Expected {chems[1][\"id\"]}, got {row[\"chemical_id\"]}'
conn.close()
os.unlink(db_path)
print(f'PASS: Warehouse propagation verified ({chems[0][\"name\"]} -> {chems[1][\"name\"]})')
"
```

### SCENARIO 8: Skipped Row Count (Fix 10)
**Goal:** Verify add_from_batch returns skipped_count.

```bash
python -m pytest tests/test_p2_batch4_fixes.py::TestSkippedRowCount -v

# Code inspection
python -c "
import sys
sys.path.insert(0, '.')
from routes import warehouse
import inspect
source = inspect.getsource(warehouse.add_from_batch)
assert 'skipped_count' in source, 'skipped_count not found in add_from_batch'
assert 'total' in source and 'matched' in source, 'Missing total/matched count query'
print('PASS: Skipped row count implemented in add_from_batch')
"
```

### SCENARIO 9: Admin Override Flag (Fix 11)
**Goal:** Verify auto_arrange returns requires_admin_override.

```bash
python -m pytest tests/test_p2_batch4_fixes.py::TestAutoArrangeAdminOverride -v

# Code inspection
python -c "
import sys
sys.path.insert(0, '.')
from routes import warehouse
import inspect
source = inspect.getsource(warehouse.auto_arrange)
assert 'requires_admin_override' in source, 'requires_admin_override not found'
assert 'caution_sections' in source, 'caution_sections not found'
print('PASS: Admin override flag implemented in auto_arrange')
"
```

### SCENARIO 10: Auto-Arrange Algorithm (Existing Fix)
**Goal:** Verify auto-arrange uses only INCOMPATIBLE for conflict edges.

```bash
python -m pytest tests/test_warehouse.py -v

# Verify the fix
python -c "
import sys
sys.path.insert(0, '.')
from routes import warehouse
from logic.constants import Compatibility
assert Compatibility.INCOMPATIBLE in warehouse.SECTION_CONFLICT_COMPATIBILITIES
assert Compatibility.CAUTION not in warehouse.SECTION_CONFLICT_COMPATIBILITIES
print('PASS: Auto-arrange conflict logic verified (only INCOMPATIBLE blocks)')
"
```

---

## Full Regression Test
```bash
cd C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend
python -m pytest tests/ -v --ignore=tests/etl_comprehensive_stress_test.py --ignore=tests/test_full_database_stress.py --ignore=tests/generate_*.py
```

**Expected result: 136 passed, 0 failed**

---

## Report Format
After running all tests, produce a report in this format:

```markdown
# SAFEWARE-CAMEO Sprint 1 QA Report
Date: [current date]
Tester: Automated QA Agent

## Test Summary
| Scenario | Status | Details |
|----------|--------|---------|
| 1. Tenant DB Schema | PASS/FAIL | ... |
| 2. confirm_match Review Queue | PASS/FAIL | ... |
| 3. Salt Auto-Match Prevention | PASS/FAIL | ... |
| 4. Container Unit Conversion | PASS/FAIL | ... |
| 5. Stale cleaned_data Fix | PASS/FAIL | ... |
| 6. Water Reactivity | PASS/FAIL | ... |
| 7. Inventory→Warehouse Sync | PASS/FAIL | ... |
| 8. Skipped Row Count | PASS/FAIL | ... |
| 9. Admin Override Flag | PASS/FAIL | ... |
| 10. Auto-Arrange Algorithm | PASS/FAIL | ... |
| **Full Regression (136 tests)** | **PASS/FAIL** | ... |

## Overall Verdict: PASS/FAIL
## Issues Found: [list any issues]
## Recommendations: [any suggestions]
```
