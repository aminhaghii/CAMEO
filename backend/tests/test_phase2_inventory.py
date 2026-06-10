"""Phase 2 integration smoke tests for inventory interactions and batch analysis."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

from app import app
from etl.pipeline import init_inventory_tables


def _ensure_phase2_tables(user_db_path: str):
    sql_path = Path(__file__).resolve().parents[1] / 'scripts' / 'create_inventory_tables.sql'
    conn = sqlite3.connect(user_db_path)
    with sql_path.open('r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def _get_test_chemicals(chemicals_db_path: str):
    """Fetch two valid chemicals from chemicals.db for stable tests."""
    conn = sqlite3.connect(chemicals_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Prefer known common records if available
    cursor.execute(
        """
        SELECT c.id, c.name, cc.cas_id
        FROM chemicals c
        JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE cc.cas_id IN ('67-64-1', '7664-93-9')
        ORDER BY CASE cc.cas_id WHEN '67-64-1' THEN 1 WHEN '7664-93-9' THEN 2 ELSE 3 END
        LIMIT 2
        """
    )
    rows = cursor.fetchall()

    if len(rows) < 2:
        cursor.execute(
            """
            SELECT c.id, c.name, cc.cas_id
            FROM chemicals c
            JOIN chemical_cas cc ON c.id = cc.chem_id
            ORDER BY c.id
            LIMIT 2
            """
        )
        rows = cursor.fetchall()

    conn.close()
    if len(rows) < 2:
        raise RuntimeError("Need at least 2 chemicals with CAS records for Phase 2 tests")

    return [dict(r) for r in rows]


def _create_batch_with_rows(user_db_path: str, batch_id: str, chemicals: list[dict]):
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 2, 2)",
        (batch_id, 'phase2_test.xlsx')
    )

    row1_cleaned = {
        'name': chemicals[0]['name'],
        'cas': chemicals[0]['cas_id'],
        'quantity': '10',
        'unit': 'L',
        'location': 'A-1',
        'notes': '',
    }
    row2_cleaned = {
        'name': chemicals[1]['name'],
        'cas': chemicals[1]['cas_id'],
        'quantity': '5',
        'unit': 'L',
        'location': 'A-1',
        'notes': '',
    }

    cursor.execute(
        """
        INSERT INTO inventory_staging
            (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id,
             match_method, confidence, quality_score, issues, suggestions, signals_json, conflicts_json, field_swaps_json)
        VALUES (?, 1, ?, ?, 'MATCHED', ?, 'exact_cas', 1.0, 100, '[]', '[]', '[]', '[]', '[]')
        """,
        (batch_id, json.dumps(row1_cleaned), json.dumps(row1_cleaned), chemicals[0]['id'])
    )
    cursor.execute(
        """
        INSERT INTO inventory_staging
            (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id,
             match_method, confidence, quality_score, issues, suggestions, signals_json, conflicts_json, field_swaps_json)
        VALUES (?, 2, ?, ?, 'MATCHED', ?, 'exact_name', 0.95, 95, '[]', '[]', '[]', '[]', '[]')
        """,
        (batch_id, json.dumps(row2_cleaned), json.dumps(row2_cleaned), chemicals[1]['id'])
    )

    conn.commit()
    conn.close()


def _cleanup_batch(user_db_path: str, batch_id: str):
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM review_queue WHERE batch_id = ?", (batch_id,))
    cursor.execute("DELETE FROM audit_trail WHERE batch_id = ?", (batch_id,))
    cursor.execute("DELETE FROM inventory_staging WHERE batch_id = ?", (batch_id,))
    cursor.execute("DELETE FROM inventory_batches WHERE id = ?", (batch_id,))
    cursor.execute("DELETE FROM analysis_results WHERE batch_id = ?", (batch_id,))
    cursor.execute("DELETE FROM user_inventories WHERE batch_id = ?", (batch_id,))
    conn.commit()
    conn.close()


@pytest.fixture
def client():
    from unittest.mock import patch
    user_db = app.config['USER_DB_PATH']
    init_inventory_tables(user_db)
    _ensure_phase2_tables(user_db)
    app.testing = True
    mock_user = {
        'id': 1,
        'email': 'super@cameo.com',
        'full_name': 'Super Admin',
        'role': 'super_admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'Global Corp',
        'tenant_db_path': user_db
    }
    with patch('app.validate_session', return_value=mock_user):
        with app.test_client() as c:
            c.set_cookie('session_id', 'mock_test_session')
            yield c


def test_inventory_actions_flow(client):
    user_db = app.config['USER_DB_PATH']
    chemicals = _get_test_chemicals(app.config['CHEMICALS_DB_PATH'])
    batch_id = f"phase2-{uuid.uuid4()}"
    _create_batch_with_rows(user_db, batch_id, chemicals)

    try:
        rows_res = client.get(f"/api/inventory/rows/{batch_id}")
        assert rows_res.status_code == 200
        rows_payload = rows_res.get_json()
        assert rows_payload['count'] == 2

        first_row = rows_payload['rows'][0]
        edit_res = client.post(
            '/api/inventory/edit',
            json={
                'batch_id': batch_id,
                'staging_id': first_row['staging_id'],
                'row_version': first_row['row_version'],
                'quantity': '12',
                'unit': 'L',
                'location': 'B-2',
                'notes': 'Updated by test',
            }
        )
        assert edit_res.status_code == 200
        assert edit_res.get_json()['row']['quantity'] == '12'

        add_res = client.post(
            '/api/inventory/add',
            json={
                'batch_id': batch_id,
                'chemical_id': chemicals[0]['id'],
                'quantity': '1',
                'unit': 'L',
                'location': 'B-2',
                'notes': 'Added by test',
            }
        )
        assert add_res.status_code == 200

        added_row = add_res.get_json()['row']
        delete_res = client.delete(f"/api/inventory/delete/{added_row['staging_id']}?batch_id={batch_id}")
        assert delete_res.status_code == 200
        assert delete_res.get_json()['success'] is True

    finally:
        _cleanup_batch(user_db, batch_id)


def test_inventory_analysis_flow(client):
    user_db = app.config['USER_DB_PATH']
    chemicals = _get_test_chemicals(app.config['CHEMICALS_DB_PATH'])
    batch_id = f"phase2-{uuid.uuid4()}"
    _create_batch_with_rows(user_db, batch_id, chemicals)

    try:
        analyze_res = client.post('/api/inventory/analyze', json={'batch_id': batch_id})
        assert analyze_res.status_code == 200
        analyze_payload = analyze_res.get_json()
        assert analyze_payload['status'] == 'success'

        fetch_res = client.get(f"/api/inventory/analysis/{batch_id}")
        assert fetch_res.status_code == 200
        fetch_payload = fetch_res.get_json()
        assert fetch_payload['batch_id'] == batch_id
        assert fetch_payload['total_chemicals'] >= 2

        excel_res = client.get(f"/api/inventory/analysis/{batch_id}/export/excel")
        assert excel_res.status_code == 200
        assert excel_res.headers.get('Content-Type', '').startswith(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

        pdf_res = client.get(f"/api/inventory/analysis/{batch_id}/export/pdf")
        # reportlab may not be installed in all environments; endpoint should fail gracefully.
        assert pdf_res.status_code in (200, 501)

    finally:
        _cleanup_batch(user_db, batch_id)


def test_manual_batch_crud_and_counts(client):
    user_db = app.config['USER_DB_PATH']
    chemicals = _get_test_chemicals(app.config['CHEMICALS_DB_PATH'])

    # 1. Create a custom manual list/batch
    create_res = client.post('/api/inventory/batches/create', json={'name': 'Custom Unit Test Inventory'})
    assert create_res.status_code == 200
    create_payload = create_res.get_json()
    assert create_payload['success'] is True
    batch_id = create_payload['batch']['id']
    assert create_payload['batch']['filename'] == 'Custom Unit Test Inventory'
    assert create_payload['batch']['total_rows'] == 0

    try:
        # 2. Check that it is listed in /api/inventory/batches
        list_res = client.get('/api/inventory/batches')
        assert list_res.status_code == 200
        batches_list = list_res.get_json()['batches']
        assert any(b['id'] == batch_id for b in batches_list)

        # 3. Add a chemical to this custom manual list
        add_res = client.post(
            '/api/inventory/add',
            json={
                'batch_id': batch_id,
                'chemical_id': chemicals[0]['id'],
                'quantity': '25',
                'unit': 'kg',
                'location': 'Shelf-Z',
                'notes': 'Manually added to custom list',
            }
        )
        assert add_res.status_code == 200
        add_payload = add_res.get_json()
        assert add_payload['success'] is True
        staging_id = add_payload['row']['staging_id']

        # Verify row count updated in the batch
        list_res2 = client.get('/api/inventory/batches')
        batches_list2 = list_res2.get_json()['batches']
        target_batch = next(b for b in batches_list2 if b['id'] == batch_id)
        assert target_batch['total_rows'] == 1

        # 4. Edit the manually added chemical row
        edit_res = client.post(
            '/api/inventory/edit',
            json={
                'batch_id': batch_id,
                'staging_id': staging_id,
                'row_version': add_payload['row']['row_version'],
                'quantity': '30',
                'unit': 'kg',
                'location': 'Shelf-Y',
                'notes': 'Manually updated details',
            }
        )
        assert edit_res.status_code == 200
        assert edit_res.get_json()['row']['quantity'] == '30'

        # 5. Delete the chemical row
        delete_row_res = client.delete(f"/api/inventory/delete/{staging_id}?batch_id={batch_id}")
        assert delete_row_res.status_code == 200

        # Verify row count decremented back to 0
        list_res3 = client.get('/api/inventory/batches')
        batches_list3 = list_res3.get_json()['batches']
        target_batch_dec = next(b for b in batches_list3 if b['id'] == batch_id)
        assert target_batch_dec['total_rows'] == 0

    finally:
        # 6. Clean up / delete the batch list
        delete_batch_res = client.delete(f"/api/inventory/batches/delete/{batch_id}")
        assert delete_batch_res.status_code == 200
        assert delete_batch_res.get_json()['success'] is True


def test_upload_to_existing_batch(client):
    user_db = app.config['USER_DB_PATH']

    # 1. Create a custom manual list/batch
    create_res = client.post('/api/inventory/batches/create', json={'name': 'List to Upload Into'})
    assert create_res.status_code == 200
    batch_id = create_res.get_json()['batch']['id']

    # Pre-populate it with one row manually
    conn = sqlite3.connect(user_db)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status)
        VALUES (?, 1, '{}', '{}', 'MATCHED')
        """,
        (batch_id,)
    )
    conn.commit()
    conn.close()

    try:
        # 2. Upload a file using the existing batch_id
        import io
        excel_data = io.BytesIO(b"dummy excel data")
        upload_res = client.post(
            f"/api/inventory/upload?batch_id={batch_id}",
            data={
                'file': (excel_data, 'test.xlsx')
            },
            content_type='multipart/form-data'
        )
        assert upload_res.status_code == 200
        assert upload_res.get_json()['batch_id'] == batch_id

        # Verify that the staging row we pre-populated is deleted (cleared)
        conn = sqlite3.connect(user_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM inventory_staging WHERE batch_id = ?", (batch_id,))
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 0

    finally:
        # Clean up the batch
        client.delete(f"/api/inventory/batches/delete/{batch_id}")
