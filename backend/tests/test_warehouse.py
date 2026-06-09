"""Unit and integration tests for CAMEO warehouse management, compatibility checks, and auto-arrange logic."""

import os
import json
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch
import pytest

from app import app
from etl.pipeline import init_inventory_tables
from logic.constants import Compatibility


def _ensure_phase2_tables(user_db_path: str):
    sql_path = Path(__file__).resolve().parents[1] / 'scripts' / 'create_inventory_tables.sql'
    conn = sqlite3.connect(user_db_path)
    with sql_path.open('r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def _get_chemicals_for_test(chemicals_db_path: str):
    """Fetch Acetone, Sulfuric Acid, and Ethanol for testing compatibility scenarios."""
    conn = sqlite3.connect(chemicals_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Fetch Acetone
    cursor.execute("""
        SELECT c.id, c.name, cc.cas_id
        FROM chemicals c
        JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE cc.cas_id = '67-64-1'
        LIMIT 1
    """)
    acetone = dict(cursor.fetchone())

    # Fetch Sulfuric Acid
    cursor.execute("""
        SELECT c.id, c.name, cc.cas_id
        FROM chemicals c
        JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE cc.cas_id = '7664-93-9'
        LIMIT 1
    """)
    sulfuric = dict(cursor.fetchone())

    # Fetch another chemical (e.g. Ethanol)
    cursor.execute("""
        SELECT c.id, c.name, cc.cas_id
        FROM chemicals c
        JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE cc.cas_id = '64-17-5'
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        other = dict(row)
    else:
        # Fallback if Ethanol is missing
        cursor.execute("""
            SELECT c.id, c.name, cc.cas_id
            FROM chemicals c
            JOIN chemical_cas cc ON c.id = cc.chem_id
            WHERE c.id NOT IN (?, ?)
            LIMIT 1
        """, (acetone['id'], sulfuric['id']))
        other = dict(cursor.fetchone())

    conn.close()
    return acetone, sulfuric, other


@pytest.fixture
def mock_user():
    # Mutatable user dict to test role-based access control (RBAC)
    return {
        'id': 1,
        'email': 'admin@cameo.com',
        'full_name': 'Test Admin',
        'role': 'admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'Test Company',
        'tenant_db_path': os.path.join(app.config['DATA_DIR'], '1_user.db')
    }


@pytest.fixture
def tenant_db_path(mock_user):
    return mock_user['tenant_db_path']


@pytest.fixture
def client(tenant_db_path, mock_user):
    init_inventory_tables(tenant_db_path)
    _ensure_phase2_tables(tenant_db_path)
    
    # Clean up existing tables to ensure isolation
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chemical_placements")
    cursor.execute("DELETE FROM warehouse_sections")
    cursor.execute("DELETE FROM warehouses")
    cursor.execute("INSERT INTO warehouses (id, name) VALUES (1, 'Main Warehouse')")
    cursor.execute("DELETE FROM placement_violations")
    cursor.execute("DELETE FROM audit_trail")
    cursor.execute("DELETE FROM inventory_staging")
    cursor.execute("DELETE FROM inventory_batches")
    conn.commit()
    conn.close()

    app.testing = True
    # Patch the middleware session validation in app.py to return our mock user
    with patch('app.validate_session', return_value=mock_user):
        with app.test_client() as c:
            # Set cookie to bypass tenant_router authentication redirect/error
            c.set_cookie('session_id', 'mock_test_session')
            yield c


def test_init_sections(client, tenant_db_path):
    # Initialize sections with valid count
    res = client.post('/api/warehouse/sections/init', json={'count': 5})
    assert res.status_code == 200
    assert res.get_json()['success'] is True
    assert '5 sections initialized' in res.get_json()['message']

    # Verify sections exist in DB
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM warehouse_sections")
    assert cursor.fetchone()[0] == 5
    conn.close()

    # Re-initialize to ensure it cleans and re-creates
    res = client.post('/api/warehouse/sections/init', json={'count': 3})
    assert res.status_code == 200
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM warehouse_sections")
    assert cursor.fetchone()[0] == 3
    conn.close()

    # Test bad count validations
    res = client.post('/api/warehouse/sections/init', json={'count': 0})
    assert res.status_code == 400
    res = client.post('/api/warehouse/sections/init', json={'count': 51})
    assert res.status_code == 400
    res = client.post('/api/warehouse/sections/init', json={'count': 'abc'})
    assert res.status_code == 400


def test_update_section(client, tenant_db_path):
    # Setup sections
    client.post('/api/warehouse/sections/init', json={'count': 3})
    
    # Get initialized section
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM warehouse_sections ORDER BY position_index LIMIT 1")
    section = cursor.fetchone()
    section_id = section[0]
    conn.close()

    # Rename section
    res = client.post('/api/warehouse/sections/update', json={
        'section_id': section_id,
        'name': 'Flammables A'
    })
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    # Verify rename in DB
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM warehouse_sections WHERE id = ?", (section_id,))
    assert cursor.fetchone()[0] == 'Flammables A'
    conn.close()

    # Verify input validation
    res = client.post('/api/warehouse/sections/update', json={'name': 'No Section ID'})
    assert res.status_code == 400
    res = client.post('/api/warehouse/sections/update', json={'section_id': section_id})
    assert res.status_code == 400


def test_add_from_batch(client, tenant_db_path):
    # Setup a mock staging batch
    acetone, sulfuric, _ = _get_chemicals_for_test(app.config['CHEMICALS_DB_PATH'])
    batch_id = f"batch-{uuid.uuid4()}"
    
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO inventory_batches (id, filename, status) VALUES (?, 'test.xlsx', 'completed')",
        (batch_id,)
    )
    
    row1 = {'name': acetone['name'], 'cas': acetone['cas_id'], 'quantity': '10', 'unit': 'kg'}
    row2 = {'name': sulfuric['name'], 'cas': sulfuric['cas_id'], 'quantity': '20', 'unit': 'kg'}
    
    cursor.execute(
        """
        INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id)
        VALUES (?, 1, ?, ?, 'MATCHED', ?)
        """,
        (batch_id, json.dumps(row1), json.dumps(row1), acetone['id'])
    )
    cursor.execute(
        """
        INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id)
        VALUES (?, 2, ?, ?, 'MATCHED', ?)
        """,
        (batch_id, json.dumps(row2), json.dumps(row2), sulfuric['id'])
    )
    conn.commit()
    conn.close()

    # Import to warehouse available pool
    res = client.post('/api/warehouse/add_from_batch', json={
        'batch_id': batch_id,
        'warehouse_name': 'Main Warehouse'
    })
    assert res.status_code == 200
    assert 'Successfully imported 2 chemicals' in res.get_json()['message']

    # Verify placement pool has 2 unplaced chemicals (section_id is NULL)
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, chemical_id, section_id, quantity_kg FROM chemical_placements")
    placements = cursor.fetchall()
    assert len(placements) == 2
    for p in placements:
        assert p[2] is None  # section_id is NULL
        assert p[3] in (10.0, 20.0)
    conn.close()

    # Test duplicates are ignored (importing same batch again should import 0)
    res = client.post('/api/warehouse/add_from_batch', json={
        'batch_id': batch_id,
        'warehouse_name': 'Main Warehouse'
    })
    assert res.status_code == 200
    assert 'imported 0 chemicals' in res.get_json()['message']


def test_move_placements_and_safety_rules(client, mock_user, tenant_db_path):
    # Setup sections
    client.post('/api/warehouse/sections/init', json={'count': 2})
    
    conn = sqlite3.connect(tenant_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM warehouse_sections ORDER BY position_index")
    sections = cursor.fetchall()
    sec1_id = sections[0]['id']
    sec2_id = sections[1]['id']
    
    # Get test chemicals
    acetone, sulfuric, other = _get_chemicals_for_test(app.config['CHEMICALS_DB_PATH'])
    
    # Fetch chemical groups to populate placements
    def _get_groups(chem_id):
        conn_chem = sqlite3.connect(app.config['CHEMICALS_DB_PATH'])
        cursor_chem = conn_chem.cursor()
        cursor_chem.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (chem_id,))
        groups = [r[0] for r in cursor_chem.fetchall()]
        conn_chem.close()
        return groups
    
    # Insert 3 chemicals into unplaced pool
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 10.0, ?)
        """,
        (acetone['id'], acetone['name'], acetone['cas_id'], json.dumps(_get_groups(acetone['id'])))
    )
    acetone_placement_id = cursor.lastrowid
    
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 15.0, ?)
        """,
        (sulfuric['id'], sulfuric['name'], sulfuric['cas_id'], json.dumps(_get_groups(sulfuric['id'])))
    )
    sulfuric_placement_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 5.0, ?)
        """,
        (other['id'], other['name'], other['cas_id'], json.dumps(_get_groups(other['id'])))
    )
    other_placement_id = cursor.lastrowid
    
    conn.commit()
    conn.close()

    # 1. Place Acetone in Section 1 (should succeed as section is empty)
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': acetone_placement_id,
        'section_id': sec1_id
    })
    assert res.status_code == 200
    
    # Verify placed status in DB
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id = ?", (acetone_placement_id,))
    assert cursor.fetchone()[0] == sec1_id
    conn.close()

    # 2. Place Sulfuric Acid in Section 1 (INCOMPATIBLE with Acetone: should throw 409 Safety Block)
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': sulfuric_placement_id,
        'section_id': sec1_id
    })
    assert res.status_code == 409
    payload = res.get_json()
    assert payload['code'] == 'INCOMPATIBLE'
    assert 'Safety Block' in payload['error']

    # Verify Sulfuric Acid is STILL unplaced in DB
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id = ?", (sulfuric_placement_id,))
    assert cursor.fetchone()[0] is None
    conn.close()

    # 3. Place Sulfuric Acid in Section 2 instead (should succeed since Section 2 is empty)
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': sulfuric_placement_id,
        'section_id': sec2_id
    })
    assert res.status_code == 200

    # 4. Role-based check on CAUTION placement
    # Let's mock a scenario where placement produces caution or no_data.
    # We'll use a chemical with NO reactive groups.
    # Insert a chemical with NO reactive groups to unplaced pool.
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, 9999, 'No Group Chem', '123-45-6', 1.0, '[]')
        """,
    )
    no_group_placement_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # As regular 'user' role (non-admin), attempting to place next to Acetone (Section 1)
    # should trigger CAUTION/NO_DATA check and return 403 Forbidden.
    mock_user['role'] = 'user'
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': no_group_placement_id,
        'section_id': sec1_id
    })
    assert res.status_code == 403
    assert res.get_json()['code'] == 'CAUTION_REQUIRES_ADMIN'

    # As 'admin' role, it should succeed
    mock_user['role'] = 'admin'
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': no_group_placement_id,
        'section_id': sec1_id
    })
    assert res.status_code == 200

    # 5. Test moving a placement back to sidebar pool (unplaced / section_id = None)
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': no_group_placement_id,
        'section_id': None
    })
    assert res.status_code == 200
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id = ?", (no_group_placement_id,))
    assert cursor.fetchone()[0] is None
    conn.close()


def test_remove_placement(client, tenant_db_path):
    # Setup placements
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, 1, 'Acetone', '67-64-1', 10.0, '[]')
        """,
    )
    placement_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Delete placement
    res = client.delete(f'/api/warehouse/placements/remove/{placement_id}')
    assert res.status_code == 200
    assert res.get_json()['success'] is True

    # Verify deletion in DB
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM chemical_placements WHERE id = ?", (placement_id,))
    assert cursor.fetchone()[0] == 0
    conn.close()

    # Verify auditing
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT action, input_data FROM audit_trail ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    assert row[0] == 'remove_chemical'
    assert 'Acetone' in row[1]
    conn.close()


def test_auto_arrange_and_save_layout(client, tenant_db_path):
    # Setup sections
    client.post('/api/warehouse/sections/init', json={'count': 3})
    
    # Get section IDs
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM warehouse_sections ORDER BY position_index")
    sections = [r[0] for r in cursor.fetchall()]
    sec1_id, sec2_id, sec3_id = sections[0], sections[1], sections[2]
    
    # Get test chemicals (Acetone, Sulfuric Acid, and Ethanol)
    acetone, sulfuric, other = _get_chemicals_for_test(app.config['CHEMICALS_DB_PATH'])
    
    # Helper to get groups
    def _get_groups(chem_id):
        conn_chem = sqlite3.connect(app.config['CHEMICALS_DB_PATH'])
        cursor_chem = conn_chem.cursor()
        cursor_chem.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (chem_id,))
        groups = [r[0] for r in cursor_chem.fetchall()]
        conn_chem.close()
        return groups

    # Seed placements in unplaced pool
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 10.0, ?)
        """,
        (acetone['id'], acetone['name'], acetone['cas_id'], json.dumps(_get_groups(acetone['id'])))
    )
    p1_id = cursor.lastrowid
    
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 15.0, ?)
        """,
        (sulfuric['id'], sulfuric['name'], sulfuric['cas_id'], json.dumps(_get_groups(sulfuric['id'])))
    )
    p2_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 5.0, ?)
        """,
        (other['id'], other['name'], other['cas_id'], json.dumps(_get_groups(other['id'])))
    )
    p3_id = cursor.lastrowid
    
    conn.commit()
    conn.close()

    # Call auto arrange
    res = client.post('/api/warehouse/auto_arrange')
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['success'] is True
    assert 'suggested_layout' in payload
    assert 'confidence_score' in payload
    
    # Verify that incompatible chemicals (Acetone and Sulfuric Acid) are suggested in DIFFERENT sections
    suggested = payload['suggested_layout']
    acetone_sec = suggested.get(str(p1_id))
    sulfuric_sec = suggested.get(str(p2_id))
    assert acetone_sec != sulfuric_sec

    # Save the suggested layout
    save_res = client.post('/api/warehouse/layout/save', json={
        'layout': suggested
    })
    assert save_res.status_code == 200
    assert save_res.get_json()['success'] is True

    # Verify placements updated in database
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id = ?", (p1_id,))
    assert cursor.fetchone()[0] == acetone_sec
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id = ?", (p2_id,))
    assert cursor.fetchone()[0] == sulfuric_sec
    conn.close()


def test_multiple_warehouses(client, tenant_db_path):
    # 1. Create a warehouse
    res = client.post('/api/warehouse/create', json={'name': 'North Store'})
    assert res.status_code == 200
    w1_id = res.get_json()['warehouse_id']
    assert w1_id is not None
    
    # 2. Create another warehouse
    res = client.post('/api/warehouse/create', json={'name': 'South Store'})
    assert res.status_code == 200
    w2_id = res.get_json()['warehouse_id']
    assert w2_id is not None
    
    # 3. List warehouses
    res = client.get('/api/warehouse/list')
    assert res.status_code == 200
    warehouses = res.get_json()['warehouses']
    names = [w['name'] for w in warehouses]
    assert 'North Store' in names
    assert 'South Store' in names
    
    # 4. Get data for a specific warehouse
    res = client.get(f'/api/warehouse/data?warehouse_id={w1_id}')
    assert res.status_code == 200
    data = res.get_json()
    assert len(data['sections']) == 10  # auto-seeded with 10 sections
    
    # 5. Rename warehouse
    res = client.post('/api/warehouse/rename', json={'warehouse_id': w1_id, 'name': 'North Store Updated'})
    assert res.status_code == 200
    
    res = client.get('/api/warehouse/list')
    names = [w['name'] for w in res.get_json()['warehouses']]
    assert 'North Store Updated' in names
    assert 'North Store' not in names
    
    # 6. Initialize sections for a specific warehouse
    res = client.post('/api/warehouse/sections/init', json={'warehouse_id': w1_id, 'count': 5})
    assert res.status_code == 200
    
    # Verify count for w1 is 5
    res = client.get(f'/api/warehouse/data?warehouse_id={w1_id}')
    assert len(res.get_json()['sections']) == 5
    
    # Verify count for w2 is still 10
    res = client.get(f'/api/warehouse/data?warehouse_id={w2_id}')
    assert len(res.get_json()['sections']) == 10
    
    # 7. Delete warehouse
    res = client.delete(f'/api/warehouse/delete/{w1_id}')
    assert res.status_code == 200
    
    res = client.get('/api/warehouse/list')
    names = [w['name'] for w in res.get_json()['warehouses']]
    assert 'North Store Updated' not in names
    assert 'South Store' in names
