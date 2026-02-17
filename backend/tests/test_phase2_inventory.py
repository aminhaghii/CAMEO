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
    user_db = app.config['USER_DB_PATH']
    init_inventory_tables(user_db)
    _ensure_phase2_tables(user_db)
    app.testing = True
    with app.test_client() as c:
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
