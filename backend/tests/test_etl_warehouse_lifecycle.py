"""
test_etl_warehouse_lifecycle.py — Phase 2 remediation validation.

Covers Findings 1.1 – 1.4 from WORKFLOW_HARMONY_REPORT:
  1.1  Deleting a batch removes its warehouse placements (no orphans).
  1.2  Editing a staging row's quantity correctly updates the warehouse placement.
  1.3  Attempting to change chemical_id of a placed row returns 400.
  1.4  Double-importing the same batch row is safely ignored (UNIQUE constraint).
"""

import json
import os
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app
from etl.pipeline import init_inventory_tables

DATA_DIR = app.config['DATA_DIR']
CHEMICALS_DB = app.config['CHEMICALS_DB_PATH']


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _get_test_chemical(chemicals_db: str):
    """Return (chemical_id, name, cas) for Acetone — always present in chemicals.db."""
    conn = sqlite3.connect(chemicals_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, cc.cas_id
        FROM chemicals c
        JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE cc.cas_id = '67-64-1'
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _get_second_chemical(chemicals_db: str, exclude_id: int):
    """Return a second chemical for identity-change tests (Ethanol)."""
    conn = sqlite3.connect(chemicals_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, cc.cas_id
        FROM chemicals c
        JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE cc.cas_id = '64-17-5' AND c.id != ?
        LIMIT 1
    """, (exclude_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.fixture
def mock_user(tmp_path):
    return {
        'id': 1,
        'email': 'admin@cameo.com',
        'full_name': 'Test Admin',
        'role': 'company_admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'Test Company',
        'tenant_db_path': str(tmp_path / '1_user.db'),
    }


@pytest.fixture
def tenant_db(mock_user):
    path = mock_user['tenant_db_path']
    if os.path.exists(path):
        os.remove(path)
    init_inventory_tables(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chemical_placements")
    cursor.execute("DELETE FROM warehouse_sections")
    cursor.execute("DELETE FROM warehouses")
    cursor.execute("DELETE FROM inventory_staging")
    cursor.execute("DELETE FROM inventory_batches")
    cursor.execute("DELETE FROM review_queue")
    cursor.execute("INSERT INTO warehouses (id, name) VALUES (1, 'Test Warehouse')")
    for i in range(1, 4):
        cursor.execute(
            "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (1, ?, ?, 'slate')",
            (f"Section {i}", i - 1)
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def client(tenant_db, mock_user):
    app.testing = True
    original_data_dir = app.config['DATA_DIR']
    app.config['DATA_DIR'] = str(Path(tenant_db).parent)
    with patch('app.DATA_DIR', str(Path(tenant_db).parent)), patch('app.validate_session', return_value=mock_user):
        with app.test_client() as c:
            c.set_cookie('session_id', 'mock_test_session')
            yield c
    app.config['DATA_DIR'] = original_data_dir


# ── Helpers ───────────────────────────────────────────────────────────────────

def _seed_batch_and_staging(tenant_db_path: str, chemical: dict, quantity: str = '50.0', unit: str = 'kg'):
    """Seed one inventory_batch + one MATCHED staging row. Returns (batch_id, staging_id)."""
    batch_id = f"batch-{uuid.uuid4()}"
    cleaned = {
        'name': chemical['name'],
        'cas': chemical['cas_id'],
        'quantity': quantity,
        'unit': unit,
    }
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO inventory_batches (id, filename, status) VALUES (?, 'test.xlsx', 'completed')",
        (batch_id,)
    )
    cursor.execute(
        """INSERT INTO inventory_staging
            (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id)
           VALUES (?, 1, ?, ?, 'MATCHED', ?)""",
        (batch_id, json.dumps(cleaned), json.dumps(cleaned), chemical['id'])
    )
    staging_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return batch_id, staging_id


def _import_to_warehouse(client, batch_id: str, warehouse_name: str = 'Test Warehouse'):
    res = client.post('/api/warehouse/add_from_batch', json={
        'batch_id': batch_id,
        'warehouse_name': warehouse_name,
    })
    assert res.status_code == 200, f"add_from_batch failed: {res.get_json()}"
    return res.get_json()


def _count_placements(tenant_db_path: str, batch_id: str) -> int:
    conn = sqlite3.connect(tenant_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM chemical_placements WHERE batch_id = ?", (batch_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count


def _get_placement_qty(tenant_db_path: str, batch_id: str, staging_row_id: int):
    conn = sqlite3.connect(tenant_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT quantity_kg FROM chemical_placements WHERE batch_id = ? AND staging_row_id = ?",
        (batch_id, staging_row_id)
    )
    row = cursor.fetchone()
    conn.close()
    return row['quantity_kg'] if row else None


def _row_version(tenant_db_path: str, staging_id: int) -> str:
    """Compute the row_version hash expected by edit endpoint."""
    import hashlib
    conn = sqlite3.connect(tenant_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, cleaned_data, match_status, chemical_id, quality_score, confidence FROM inventory_staging WHERE id = ?",
        (staging_id,)
    )
    row = cursor.fetchone()
    conn.close()
    payload = (
        f"{row['id']}|{row['cleaned_data'] or ''}|{row['match_status'] or ''}|"
        f"{row['chemical_id'] or ''}|{row['quality_score'] or ''}|{row['confidence'] or ''}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Finding 1.1: Batch delete removes placements (no orphans) ─────────────────

class TestBatchDeleteCascade:

    def test_delete_batch_removes_placements(self, client, tenant_db):
        """Deleting a batch must also remove all its warehouse placements."""
        chem = _get_test_chemical(CHEMICALS_DB)
        assert chem, "Acetone not in chemicals.db — run ensure_data.py first"

        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem)
        _import_to_warehouse(client, batch_id)

        # Confirm the placement exists
        assert _count_placements(tenant_db, batch_id) == 1, \
            "Placement should exist after import"

        # Delete the batch
        res = client.delete(f'/api/inventory/batches/delete/{batch_id}')
        assert res.status_code == 200

        # Placement must be gone — no orphan
        assert _count_placements(tenant_db, batch_id) == 0, \
            "Orphaned chemical_placements found after batch delete (Finding 1.1)"

    def test_delete_batch_with_no_placements_succeeds(self, client, tenant_db):
        """Batch delete works even when the batch was never imported to warehouse."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, _ = _seed_batch_and_staging(tenant_db, chem)

        res = client.delete(f'/api/inventory/batches/delete/{batch_id}')
        assert res.status_code == 200

        conn = sqlite3.connect(tenant_db)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM inventory_batches WHERE id = ?", (batch_id,))
        assert cursor.fetchone()[0] == 0
        conn.close()


class TestRowDeleteCascade:

    def test_delete_inventory_row_removes_matching_warehouse_placement(self, client, tenant_db):
        """Deleting one staged row must remove its imported warehouse placement."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem)
        _import_to_warehouse(client, batch_id)

        assert _count_placements(tenant_db, batch_id) == 1

        res = client.delete(f'/api/inventory/delete/{staging_id}?batch_id={batch_id}')
        assert res.status_code == 200, f"Row delete failed: {res.get_json()}"

        assert _count_placements(tenant_db, batch_id) == 0, (
            "Deleting a staged row must not leave a stale warehouse placement"
        )


# ── Finding 1.2: Quantity edit syncs to warehouse ────────────────────────────

class TestQuantitySyncToWarehouse:

    def test_quantity_edit_updates_warehouse_placement(self, client, tenant_db):
        """Editing quantity on a placed staging row must update chemical_placements.quantity_kg."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem, quantity='50.0', unit='kg')
        _import_to_warehouse(client, batch_id)

        # Confirm initial quantity
        qty_before = _get_placement_qty(tenant_db, batch_id, staging_id)
        assert qty_before == pytest.approx(50.0, rel=0.01), \
            f"Expected 50.0 kg after import, got {qty_before}"

        # Edit quantity via API
        row_ver = _row_version(tenant_db, staging_id)
        res = client.post('/api/inventory/edit', json={
            'batch_id': batch_id,
            'staging_id': staging_id,
            'quantity': '75.5',
            'unit': 'kg',
            'row_version': row_ver,
        })
        assert res.status_code == 200, f"Edit failed: {res.get_json()}"

        # Verify warehouse synced
        qty_after = _get_placement_qty(tenant_db, batch_id, staging_id)
        assert qty_after == pytest.approx(75.5, rel=0.01), (
            f"Warehouse quantity not synced: expected 75.5 kg, got {qty_after} "
            f"(Finding 1.2 — silent no-op still present)"
        )

    def test_quantity_edit_unit_conversion(self, client, tenant_db):
        """Quantity editing with unit conversion (g→kg) syncs correctly."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem, quantity='1.0', unit='kg')
        _import_to_warehouse(client, batch_id)

        row_ver = _row_version(tenant_db, staging_id)
        res = client.post('/api/inventory/edit', json={
            'batch_id': batch_id,
            'staging_id': staging_id,
            'quantity': '2000',
            'unit': 'g',
            'row_version': row_ver,
        })
        assert res.status_code == 200

        qty_after = _get_placement_qty(tenant_db, batch_id, staging_id)
        assert qty_after == pytest.approx(2.0, rel=0.01), \
            f"2000g should become 2.0 kg in warehouse, got {qty_after}"

    def test_quantity_edit_unimported_batch_is_noop(self, client, tenant_db):
        """Editing quantity for a row NOT yet in warehouse is a safe no-op (no error)."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem, quantity='10.0', unit='kg')
        # Do NOT import to warehouse

        row_ver = _row_version(tenant_db, staging_id)
        res = client.post('/api/inventory/edit', json={
            'batch_id': batch_id,
            'staging_id': staging_id,
            'quantity': '20.0',
            'unit': 'kg',
            'row_version': row_ver,
        })
        assert res.status_code == 200
        # No placement exists — no error should occur
        assert _count_placements(tenant_db, batch_id) == 0


# ── Finding 1.3: Identity change blocked for placed rows ─────────────────────

class TestIdentityChangeLocked:

    def test_changing_chemical_id_of_placed_row_returns_400(self, client, tenant_db):
        """Cannot change chemical_id of a staging row that is already placed in warehouse."""
        chem = _get_test_chemical(CHEMICALS_DB)
        second = _get_second_chemical(CHEMICALS_DB, chem['id'])
        assert second, "Need at least 2 chemicals in chemicals.db"

        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem)
        _import_to_warehouse(client, batch_id)

        # Attempt identity change
        row_ver = _row_version(tenant_db, staging_id)
        res = client.post('/api/inventory/edit', json={
            'batch_id': batch_id,
            'staging_id': staging_id,
            'chemical_id': second['id'],
            'row_version': row_ver,
        })
        assert res.status_code == 400, (
            f"Expected 400 when changing identity of placed row, got {res.status_code} "
            f"(Finding 1.3 — identity divergence still possible)"
        )
        body = res.get_json()
        assert body.get('code') == 'PLACED_IN_WAREHOUSE'

    def test_changing_chemical_id_of_unplaced_row_succeeds(self, client, tenant_db):
        """Changing chemical_id of a staging row NOT yet in warehouse is allowed."""
        chem = _get_test_chemical(CHEMICALS_DB)
        second = _get_second_chemical(CHEMICALS_DB, chem['id'])
        assert second, "Need at least 2 chemicals in chemicals.db"

        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem)
        # Do NOT import to warehouse

        row_ver = _row_version(tenant_db, staging_id)
        res = client.post('/api/inventory/edit', json={
            'batch_id': batch_id,
            'staging_id': staging_id,
            'chemical_id': second['id'],
            'row_version': row_ver,
        })
        assert res.status_code == 200

    def test_quantity_edit_of_placed_row_succeeds(self, client, tenant_db):
        """Quantity-only edit of a placed row must still work (identity not changing)."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem, quantity='10.0')
        _import_to_warehouse(client, batch_id)

        row_ver = _row_version(tenant_db, staging_id)
        res = client.post('/api/inventory/edit', json={
            'batch_id': batch_id,
            'staging_id': staging_id,
            'quantity': '30.0',
            'unit': 'kg',
            'row_version': row_ver,
        })
        assert res.status_code == 200, \
            f"Quantity-only edit of placed row failed: {res.get_json()}"
        qty = _get_placement_qty(tenant_db, batch_id, staging_id)
        assert qty == pytest.approx(30.0, rel=0.01)


# ── Finding 1.4: Duplicate import guard (DB constraint) ──────────────────────

class TestDuplicateImportGuard:

    def test_double_import_inserts_only_one_placement(self, client, tenant_db):
        """Importing the same batch twice must result in exactly one placement per staging row."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, staging_id = _seed_batch_and_staging(tenant_db, chem)

        r1 = _import_to_warehouse(client, batch_id)
        r2 = _import_to_warehouse(client, batch_id)

        assert r1['imported_count'] == 1, "First import should report 1 imported"
        assert r2['imported_count'] == 0, "Second import should report 0 (duplicate ignored)"

        # Exactly one row in DB
        assert _count_placements(tenant_db, batch_id) == 1, (
            "Expected exactly 1 placement after double-import, "
            "UNIQUE constraint may not be enforced (Finding 1.4)"
        )

    def test_double_import_api_returns_200_both_times(self, client, tenant_db):
        """Both import calls must return 200 — second one is a graceful no-op."""
        chem = _get_test_chemical(CHEMICALS_DB)
        batch_id, _ = _seed_batch_and_staging(tenant_db, chem)

        res1 = client.post('/api/warehouse/add_from_batch', json={'batch_id': batch_id})
        res2 = client.post('/api/warehouse/add_from_batch', json={'batch_id': batch_id})

        assert res1.status_code == 200
        assert res2.status_code == 200

    def test_multiple_rows_same_batch_each_inserted_once(self, client, tenant_db):
        """Each staging row in a batch gets exactly one placement even on double-import."""
        # Seed 2 staging rows for the same batch
        chem = _get_test_chemical(CHEMICALS_DB)
        second = _get_second_chemical(CHEMICALS_DB, chem['id'])
        if not second:
            pytest.skip("Need at least 2 chemicals in chemicals.db")

        batch_id = f"batch-{uuid.uuid4()}"
        conn = sqlite3.connect(tenant_db)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO inventory_batches (id, filename, status) VALUES (?, 'test.xlsx', 'completed')",
            (batch_id,)
        )
        for idx, c in enumerate([chem, second], start=1):
            cleaned = {'name': c['name'], 'cas': c['cas_id'], 'quantity': '10', 'unit': 'kg'}
            cursor.execute(
                """INSERT INTO inventory_staging
                    (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id)
                   VALUES (?, ?, ?, ?, 'MATCHED', ?)""",
                (batch_id, idx, json.dumps(cleaned), json.dumps(cleaned), c['id'])
            )
        conn.commit()
        conn.close()

        _import_to_warehouse(client, batch_id)
        _import_to_warehouse(client, batch_id)

        assert _count_placements(tenant_db, batch_id) == 2, \
            "Expected exactly 2 placements (one per row) after double-import"
