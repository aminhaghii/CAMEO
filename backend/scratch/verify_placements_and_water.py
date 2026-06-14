import sys
import os
import json
import sqlite3

sys.path.insert(0, r"C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend")
from app import app
from routes.warehouse import _validate_layout_update, _is_section_conflict, _is_water_reactive, _has_water_group
from logic.reactivity_engine import ReactivityEngine
from db_utils import get_safe_connection
from flask import g

user_db = r"C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend\data\2_user.db"
chemicals_db = r"C:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend\data\chemicals.db"

with app.app_context(), app.test_request_context():
    # Setup mock request variables
    g.tenant_db_path = user_db
    g.user = {'email': 'qa_operator@demo.com', 'role': 'operator', 'company_id': 2}
    
    print("=== Test 1: Favorites Table Exists ===")
    conn = get_safe_connection(user_db)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='favorites'")
    row = cursor.fetchone()
    if row:
        print("Success: favorites table exists in tenant DB.")
    else:
        print("FAIL: favorites table missing.")
    conn.close()

    print("\n=== Test 2: Reactivity Engine Audit Log Redirection ===")
    engine = ReactivityEngine(chemicals_db)
    # Perform a mock analysis
    try:
        # IDs 90 (AMMONIUM HYDROGEN SULFATE) and 19945 (CAFFEINE)
        analysis = engine.analyze([90, 19945], include_water_check=True, save_audit=True)
        print("Success: analyze executed successfully.")
        
        # Check if audit_log table exists in 2_user.db and contains our record
        conn = get_safe_connection(user_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'")
        row = cursor.fetchone()
        if row:
            cursor.execute("SELECT COUNT(*) FROM audit_log")
            count = cursor.fetchone()[0]
            print(f"Success: audit_log table exists in tenant DB with {count} records.")
        else:
            print("FAIL: audit_log table not found in tenant DB.")
        conn.close()
    except Exception as e:
        print("FAIL: engine.analyze failed:", e)

    print("\n=== Test 3: Water Reactivity Manual Placement Validation ===")
    # Section ID 5 currently has Sodium Peroxide (id=1516), which has reactive groups [10, 44] and is water-reactive.
    # Group 104 is Water and Aqueous Solutions. Ammonium Hydroxide (id=2434) has reactive groups [61, 100].
    # Let's test _is_section_conflict for Sodium Peroxide and Ammonium Hydroxide (neither is group 104 directly, but Sodium Peroxide is water-reactive).
    # Wait, let's create two mock placement objects.
    # placement_a is Sodium Peroxide (chemical_id=1516, water_reactive=True)
    # placement_b is a water solution (chemical_id=2434, but let's give it group 104)
    p_a = {
        'chemical_id': 1516,
        'chemical_name': 'SODIUM PEROXIDE',
        'reactive_groups': '[10, 44]'
    }
    p_b = {
        'chemical_id': 2434,
        'chemical_name': 'WATER SOLUTION',
        'reactive_groups': '[104]'
    }
    
    conflict = _is_section_conflict(engine, p_a, p_b)
    print("Conflict detected between Sodium Peroxide and Water Solution:", conflict)
    if conflict:
        print("Success: Water reactivity conflict correctly identified.")
    else:
        print("FAIL: Water reactivity conflict missed.")

    # Now let's test if _validate_layout_update blocks them.
    # Let's temporarily insert mock placements in a test warehouse/section.
    conn = get_safe_connection(user_db)
    cursor = conn.cursor()
    
    # Create test warehouse and section
    cursor.execute("INSERT INTO warehouses (name) VALUES ('Temp Test WH')")
    test_wh_id = cursor.lastrowid
    cursor.execute("INSERT INTO warehouse_sections (warehouse_id, name, position_index) VALUES (?, 'Sec 1', 0)", (test_wh_id,))
    test_sec_id = cursor.lastrowid
    
    # Insert Sodium Peroxide (1516) placed in Sec 1
    cursor.execute(
        """INSERT INTO chemical_placements 
            (warehouse_id, section_id, chemical_id, chemical_name, reactive_groups, status)
           VALUES (?, ?, 1516, 'SODIUM PEROXIDE', '[10, 44]', 'placed')""",
        (test_wh_id, test_sec_id)
    )
    p_a_id = cursor.lastrowid
    
    # Insert Water Solution (2434) unplaced
    cursor.execute(
        """INSERT INTO chemical_placements 
            (warehouse_id, section_id, chemical_id, chemical_name, reactive_groups, status)
           VALUES (?, NULL, 2434, 'WATER SOLUTION', '[104]', 'placed')""",
        (test_wh_id,)
    )
    p_b_id = cursor.lastrowid
    
    # Validate update: move Water Solution (p_b_id) to Sec 1 (test_sec_id)
    # This should be BLOCKED because of the water-reactivity conflict!
    ok, status_code, payload = _validate_layout_update(conn, {p_b_id: test_sec_id}, 'operator')
    print("Validation result for dropping Water Solution next to Sodium Peroxide:")
    print("OK:", ok, "Status Code:", status_code, "Payload:", payload)
    
    # Rollback/cleanup test placements
    cursor.execute("DELETE FROM chemical_placements WHERE warehouse_id = ?", (test_wh_id,))
    cursor.execute("DELETE FROM warehouse_sections WHERE warehouse_id = ?", (test_wh_id,))
    cursor.execute("DELETE FROM warehouses WHERE id = ?", (test_wh_id,))
    conn.commit()
    conn.close()
    
    if not ok and status_code == 409 and payload.get('code') == 'INCOMPATIBLE':
        print("Success: _validate_layout_update correctly BLOCKED the water-reactive drop.")
    else:
        print("FAIL: _validate_layout_update failed to block the water-reactive drop.")

    print("\n=== Test 4: Unmatched-to-Matched Chemical Propagation ===")
    conn = get_safe_connection(user_db)
    cursor = conn.cursor()
    
    # Let's insert a dummy staging row with match_status='REVIEW_REQUIRED' (unmatched)
    batch_id = 'test-propagation-batch-id'
    cursor.execute("DELETE FROM chemical_placements WHERE placed_by = ?", (f"import:{batch_id}",))
    cursor.execute("DELETE FROM inventory_staging WHERE batch_id = ?", (batch_id,))
    cursor.execute("DELETE FROM inventory_batches WHERE id = ?", (batch_id,))
    cursor.execute("INSERT INTO inventory_batches (id, filename, status) VALUES (?, 'test_prop.xlsx', 'completed')", (batch_id,))
    cursor.execute(
        """INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id)
           VALUES (?, 1, '{"name": "Dummy"}', '{"name": "Dummy"}', 'REVIEW_REQUIRED', NULL)""",
        (batch_id,)
    )
    staging_id = cursor.lastrowid
    
    # Simulate that the batch has already been imported to warehouse 2
    # Create a dummy placement indicating the batch was imported
    cursor.execute(
        """INSERT INTO chemical_placements 
            (warehouse_id, section_id, chemical_id, chemical_name, reactive_groups, status, placed_by)
           VALUES (2, NULL, 999999, 'OTHER CHEM', '[]', 'placed', ?)""",
        (f"import:{batch_id}",)
    )
    other_p_id = cursor.lastrowid
    conn.commit()
    
    # Verify confirm_match propagation
    # We will simulate calling confirm_match for staging_id to match Caffeine (19945)
    # This should trigger _propagate_to_warehouse and INSERT a new placement for Caffeine in warehouse 2 since it was previously NULL!
    from routes.inventory import confirm_match
    
    # Prepare POST request
    import flask
    with app.test_client() as client:
        # Mock login session
        with client.session_transaction() as sess:
            sess['user_id'] = 5  # qa_operator
            
        res = client.post('/api/inventory/confirm', json={
            'staging_id': staging_id,
            'chemical_id': 19945,
            'chemical_name': 'CAFFEINE'
        }, headers={'X-CSRF-Token': 'mock_token'}) # Note: csrf is bypassed in test client if session CSRF matches or we mock it.
        # Wait, if csrf is enabled, we need to bypass or supply csrf. Let's do it via python context directly rather than client POST.
    
    # Let's call confirm_row/propagate programmatically to bypass HTTP headers:
    from routes.inventory import _propagate_to_warehouse
    cursor.execute("SELECT cleaned_data, chemical_id FROM inventory_staging WHERE id = ?", (staging_id,))
    st_row = cursor.fetchone()
    old_chem_id = st_row['chemical_id']
    
    # Perform manual confirm staging update
    cursor.execute(
        "UPDATE inventory_staging SET chemical_id=19945, match_status='MATCHED', cleaned_data='{\"name\":\"CAFFEINE\",\"quantity\":\"10\",\"unit\":\"kg\"}' WHERE id=?",
        (staging_id,)
    )
    # Call propagation with old_chemical_id = None
    _propagate_to_warehouse(cursor, batch_id, old_chem_id, 19945, {'name': 'CAFFEINE'})
    conn.commit()
    
    # Check if a placement for Caffeine (19945) was inserted under warehouse 2
    cursor.execute(
        "SELECT id, quantity_kg FROM chemical_placements WHERE chemical_id=19945 AND placed_by LIKE ?",
        (f"import:{batch_id}%",)
    )
    p_row = cursor.fetchone()
    if p_row:
        print("Success: Caffeine placement automatically created in warehouse.")
        print("Placement details:", dict(p_row))
    else:
        print("FAIL: Caffeine placement not created.")
        
    # Cleanup dummy batch, staging and placements
    cursor.execute("DELETE FROM chemical_placements WHERE placed_by = ?", (f"import:{batch_id}",))
    cursor.execute("DELETE FROM inventory_staging WHERE batch_id = ?", (batch_id,))
    cursor.execute("DELETE FROM inventory_batches WHERE id = ?", (batch_id,))
    conn.commit()
    conn.close()
