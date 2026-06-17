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
        'role': 'company_admin',
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
    # Remove stale DB so init_inventory_tables always applies the current schema
    # (including any new indexes or columns added in migrations).
    import os
    if os.path.exists(tenant_db_path):
        os.remove(tenant_db_path)

    init_inventory_tables(tenant_db_path)
    _ensure_phase2_tables(tenant_db_path)

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

    # Seed a placement in DB first
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups) "
        "VALUES (1, NULL, 1, 'Acetone', '67-64-1', 10.0, '[]')"
    )
    conn.commit()
    conn.close()

    # Re-initialize to ensure it cleans and re-creates sections but PRESERVES placements (setting section_id = NULL)
    res = client.post('/api/warehouse/sections/init', json={'count': 3})
    assert res.status_code == 200
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM warehouse_sections")
    assert cursor.fetchone()[0] == 3

    # Verify placement STILL exists and section_id is NULL
    cursor.execute("SELECT COUNT(*), section_id FROM chemical_placements")
    row = cursor.fetchone()
    assert row[0] == 1
    assert row[1] is None
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
    res_json = res.get_json()
    assert 'Successfully imported 2 chemicals' in res_json['message']
    assert 'warehouse_id' in res_json
    w_id = res_json['warehouse_id']
    assert w_id is not None

    # Check that data endpoint returns placement list with 'id' and 'placement_id'
    data_res = client.get(f"/api/warehouse/data?warehouse_id={w_id}")
    assert data_res.status_code == 200
    data_json = data_res.get_json()
    assert 'inventory' in data_json
    assert len(data_json['inventory']) == 2
    for item in data_json['inventory']:
        assert 'id' in item
        assert 'placement_id' in item
        assert item['id'] == item['placement_id']

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

    # 4. NO_DATA placement (chemical with NO reactive groups) next to Acetone.
    # Policy: NO_DATA is NOT a hard block on manual save/move — it is admin-overridable.
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

    # As operator (read-only role) the mutating request is rejected up-front.
    mock_user['role'] = 'operator'
    res = client.post('/api/warehouse/placements/move', json={
        'placement_id': no_group_placement_id,
        'section_id': sec1_id
    })
    assert res.status_code == 403
    assert res.get_json()['code'] == 'OPERATOR_READONLY'

    # As company_admin the NO_DATA placement is allowed (admin override of the
    # caution/no-data gate). NO_DATA only forces hard separation in auto-arrange.
    mock_user['role'] = 'company_admin'
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


def _seed_placement(tenant_db_path, chem_id, name, cas, groups):
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups)
        VALUES (1, NULL, ?, ?, ?, 10.0, ?)
        """,
        (chem_id, name, cas, json.dumps(groups))
    )
    pid = cursor.lastrowid
    conn.commit()
    conn.close()
    return pid


def _groups_from_chemicals_db(chem_id):
    conn = sqlite3.connect(app.config['CHEMICALS_DB_PATH'])
    cursor = conn.cursor()
    cursor.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (chem_id,))
    groups = [r[0] for r in cursor.fetchall()]
    conn.close()
    return groups


def test_auto_arrange_allows_caution_pair(client, tenant_db_path):
    """A CAUTION pair (Acetone + Ethanol) may co-locate: both fit in a single section."""
    client.post('/api/warehouse/sections/init', json={'count': 1})
    acetone, _sulfuric, ethanol = _get_chemicals_for_test(app.config['CHEMICALS_DB_PATH'])

    p1 = _seed_placement(tenant_db_path, acetone['id'], acetone['name'], acetone['cas_id'],
                         _groups_from_chemicals_db(acetone['id']))
    p2 = _seed_placement(tenant_db_path, ethanol['id'], ethanol['name'], ethanol['cas_id'],
                         _groups_from_chemicals_db(ethanol['id']))

    res = client.post('/api/warehouse/auto_arrange')
    assert res.status_code == 200
    payload = res.get_json()
    suggested = payload['suggested_layout']

    # Both placed (nothing left in the pool) and in the SAME single section.
    assert payload['unplaced'] == []
    assert suggested[str(p1)] is not None
    assert suggested[str(p1)] == suggested[str(p2)]


def test_auto_arrange_isolates_no_data(client, tenant_db_path):
    """A NO_DATA chemical (no reactive groups) is force-separated from everything else."""
    client.post('/api/warehouse/sections/init', json={'count': 1})
    acetone, _sulfuric, _ethanol = _get_chemicals_for_test(app.config['CHEMICALS_DB_PATH'])

    p1 = _seed_placement(tenant_db_path, acetone['id'], acetone['name'], acetone['cas_id'],
                         _groups_from_chemicals_db(acetone['id']))
    # Unknown chemical with no reactive groups → NO_DATA against Acetone.
    p2 = _seed_placement(tenant_db_path, 999999, 'Mystery Chem', '000-00-0', [])

    res = client.post('/api/warehouse/auto_arrange')
    assert res.status_code == 200
    payload = res.get_json()
    suggested = payload['suggested_layout']

    # Only one section exists, so one of them cannot be placed (they must not share).
    s1 = suggested.get(str(p1))
    s2 = suggested.get(str(p2))
    assert s1 != s2
    assert None in (s1, s2)
    assert len(payload['unplaced']) == 1
    # The solver should recommend adding exactly one more section to fit both.
    rec = payload['recommendation']
    assert rec['has_recommendation'] is True
    assert rec['add_sections_needed'] == 1


def test_save_layout_blocks_incompatible_payload(client, tenant_db_path):
    client.post('/api/warehouse/sections/init', json={'count': 2})

    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM warehouse_sections ORDER BY position_index LIMIT 1")
    section_id = cursor.fetchone()[0]
    acetone, sulfuric, _ = _get_chemicals_for_test(app.config['CHEMICALS_DB_PATH'])

    def _get_groups(chem_id):
        conn_chem = sqlite3.connect(app.config['CHEMICALS_DB_PATH'])
        cursor_chem = conn_chem.cursor()
        cursor_chem.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (chem_id,))
        groups = [r[0] for r in cursor_chem.fetchall()]
        conn_chem.close()
        return groups

    cursor.execute(
        "INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups) "
        "VALUES (1, NULL, ?, ?, ?, 10.0, ?)",
        (acetone['id'], acetone['name'], acetone['cas_id'], json.dumps(_get_groups(acetone['id'])))
    )
    p1_id = cursor.lastrowid
    cursor.execute(
        "INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups) "
        "VALUES (1, NULL, ?, ?, ?, 15.0, ?)",
        (sulfuric['id'], sulfuric['name'], sulfuric['cas_id'], json.dumps(_get_groups(sulfuric['id'])))
    )
    p2_id = cursor.lastrowid
    conn.commit()
    conn.close()

    res = client.post('/api/warehouse/layout/save', json={
        'layout': {str(p1_id): section_id, str(p2_id): section_id}
    })
    assert res.status_code == 409
    assert res.get_json()['code'] == 'INCOMPATIBLE'

    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id IN (?, ?) ORDER BY id", (p1_id, p2_id))
    assert [row[0] for row in cursor.fetchall()] == [None, None]
    conn.close()


def test_save_layout_blocks_cross_warehouse_section(client, tenant_db_path):
    client.post('/api/warehouse/create', json={'name': 'Second Warehouse'})

    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM warehouses WHERE name = 'Second Warehouse'")
    second_warehouse_id = cursor.fetchone()[0]
    cursor.execute("SELECT id FROM warehouse_sections WHERE warehouse_id = ? ORDER BY position_index LIMIT 1", (second_warehouse_id,))
    foreign_section_id = cursor.fetchone()[0]

    cursor.execute(
        "INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups) "
        "VALUES (1, NULL, 1, 'Acetone', '67-64-1', 10.0, '[]')"
    )
    placement_id = cursor.lastrowid
    conn.commit()
    conn.close()

    res = client.post('/api/warehouse/layout/save', json={
        'layout': {str(placement_id): foreign_section_id}
    })
    assert res.status_code == 409
    assert res.get_json()['code'] == 'WAREHOUSE_MISMATCH'


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


def test_auto_arrange_recommendation(client, tenant_db_path):
    # Setup ONLY 1 section (so we can easily force incompatibilities)
    client.post('/api/warehouse/sections/init', json={'count': 1})
    
    # Get section IDs
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM warehouse_sections ORDER BY position_index")
    sections = [r[0] for r in cursor.fetchall()]
    
    # Get test chemicals: Acetone and Sulfuric Acid (which are incompatible)
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
    
    conn.commit()
    conn.close()

    # Call auto arrange
    res = client.post('/api/warehouse/auto_arrange')
    assert res.status_code == 200
    payload = res.get_json()
    assert payload['success'] is True
    assert 'suggested_layout' in payload
    assert 'recommendation' in payload
    
    recommendation = payload['recommendation']
    assert recommendation['has_recommendation'] is True
    assert recommendation['add_sections_needed'] == 1
    assert recommendation['can_auto_create'] is True
    assert recommendation['virtual_layout'] is not None
    assert "Adding 1 more sections" in recommendation['message']
    
    # Check that in suggested_layout, one of them has None as section_id
    suggested = payload['suggested_layout']
    s1 = suggested.get(str(p1_id))
    s2 = suggested.get(str(p2_id))
    assert s1 is None or s2 is None
    assert s1 != s2

    # Apply the recommendation atomically: create the missing section and save the complete layout.
    apply_res = client.post('/api/warehouse/recommendation/apply', json={
        'warehouse_id': 1,
        'extra_sections': recommendation['add_sections_needed'],
        'virtual_layout': recommendation['virtual_layout'],
    })
    assert apply_res.status_code == 200
    assert apply_res.get_json()['success'] is True

    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM warehouse_sections WHERE warehouse_id = 1")
    assert cursor.fetchone()[0] == 2
    cursor.execute("SELECT COUNT(*) FROM chemical_placements WHERE warehouse_id = 1 AND section_id IS NULL")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT section_id FROM chemical_placements WHERE id IN (?, ?) ORDER BY id", (p1_id, p2_id))
    placed_sections = [row[0] for row in cursor.fetchall()]
    assert placed_sections[0] != placed_sections[1]
    conn.close()


def test_init_sections_preserves_layout(client, tenant_db_path):
    # 1. Initialize 3 sections
    client.post('/api/warehouse/sections/init', json={'count': 3})
    
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM warehouse_sections ORDER BY position_index")
    sec_ids = [r[0] for r in cursor.fetchall()]
    sec1_id, sec2_id, sec3_id = sec_ids[0], sec_ids[1], sec_ids[2]
    
    # Place a chemical in Section 2 (which will be kept)
    cursor.execute(
        "INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups) "
        "VALUES (1, ?, 1, 'Acetone', '67-64-1', 10.0, '[]')",
        (sec2_id,)
    )
    # Place another chemical in Section 3 (which will be deleted if we reduce count to 2)
    cursor.execute(
        "INSERT INTO chemical_placements (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups) "
        "VALUES (1, ?, 2, 'Sulfuric Acid', '7664-93-9', 15.0, '[]')",
        (sec3_id,)
    )
    conn.commit()
    conn.close()
    
    # 2. Reconfigure to 2 sections (so Section 3 is deleted, but Section 2 is kept)
    res = client.post('/api/warehouse/sections/init', json={'count': 2})
    assert res.status_code == 200
    
    # Verify section count is 2
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM warehouse_sections")
    assert cursor.fetchone()[0] == 2
    
    # Verify Acetone is STILL in Section 2 (sec2_id)
    cursor.execute("SELECT section_id FROM chemical_placements WHERE chemical_id = 1")
    assert cursor.fetchone()[0] == sec2_id
    
    # Verify Sulfuric Acid is returned to pool (section_id IS NULL)
    cursor.execute("SELECT section_id FROM chemical_placements WHERE chemical_id = 2")
    assert cursor.fetchone()[0] is None
    conn.close()
