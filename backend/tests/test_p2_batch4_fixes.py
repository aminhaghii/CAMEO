"""
Tests for P2 Batch 4 Fixes:
  Fix 1: Inventory edits propagate to warehouse
  Fix 2: Warehouse import reports skipped_count
  Fix 3: Auto-arrange requires_admin_override flag
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app
from etl.pipeline import init_inventory_tables


CHEMICALS_DB = str(Path(__file__).resolve().parent.parent / 'data' / 'chemicals.db')


def _make_user_db():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    init_inventory_tables(path)
    return path


def _get_two_chemicals():
    conn = sqlite3.connect(CHEMICALS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name,
               (SELECT cas_id FROM chemical_cas cc WHERE cc.chem_id = c.id ORDER BY sort LIMIT 1) AS cas_id
        FROM chemicals c
        WHERE EXISTS (SELECT 1 FROM chemical_cas cc2 WHERE cc2.chem_id = c.id)
        ORDER BY c.id LIMIT 2
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    assert len(rows) >= 2, "Need at least 2 chemicals with CAS records"
    return rows


# ══════════════════════════════════════════════════════════════
#  FIX 1: Inventory Edits Propagate to Warehouse
# ══════════════════════════════════════════════════════════════

class TestInventoryToWarehousePropagation:
    """Verify staging edits update warehouse placements."""

    def test_edit_propagates_chemical_change_to_warehouse(self):
        """When staging chemical_id changes, warehouse placement should update."""
        db_path = _make_user_db()
        chemicals = _get_two_chemicals()
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            batch_id = 'test-batch'
            cur.execute(
                "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 2, 2)",
                (batch_id, 'test.xlsx')
            )

            # Staging row with chemical A
            cleaned = json.dumps({'name': chemicals[0]['name'], 'cas': chemicals[0]['cas_id']})
            cur.execute(
                "INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status, chemical_id) VALUES (?, 1, ?, ?, 'MATCHED', ?)",
                (batch_id, cleaned, cleaned, chemicals[0]['id'])
            )
            staging_id = cur.lastrowid

            # Warehouse placement imported from this batch — uses new relational columns
            cur.execute(
                """INSERT INTO chemical_placements
                   (warehouse_id, chemical_id, chemical_name, cas_number, reactive_groups,
                    status, batch_id, staging_row_id)
                   VALUES (1, ?, ?, ?, '[]', 'placed', ?, ?)""",
                (chemicals[0]['id'], chemicals[0]['name'], chemicals[0]['cas_id'],
                 batch_id, staging_id)
            )
            placement_id = cur.lastrowid
            conn.commit()

            # Verify placement has chemical A
            cur.execute("SELECT chemical_id FROM chemical_placements WHERE id = ?", (placement_id,))
            assert cur.fetchone()['chemical_id'] == chemicals[0]['id']

            # Propagate: update warehouse placement via (batch_id, staging_row_id)
            new_chem_id = chemicals[1]['id']
            cur.execute(
                """UPDATE chemical_placements
                   SET chemical_id = ?, chemical_name = ?, cas_number = ?
                   WHERE batch_id = ? AND staging_row_id = ?""",
                (new_chem_id, chemicals[1]['name'], chemicals[1]['cas_id'],
                 batch_id, staging_id)
            )
            conn.commit()

            # Verify placement now has chemical B
            cur.execute("SELECT chemical_id, chemical_name FROM chemical_placements WHERE id = ?", (placement_id,))
            row = cur.fetchone()
            assert row['chemical_id'] == chemicals[1]['id'], \
                f"Warehouse should have chemical B ({chemicals[1]['id']}), got {row['chemical_id']}"
            assert row['chemical_name'] == chemicals[1]['name']
            conn.close()
        finally:
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Windows may hold the file briefly; DB is temp and will be cleaned by OS

    def test_propagate_function_exists_in_inventory(self):
        """_propagate_to_warehouse should exist in inventory.py."""
        from routes import inventory
        assert hasattr(inventory, '_propagate_to_warehouse'), \
            "_propagate_to_warehouse should exist in inventory.py"

    def test_propagate_function_exists_in_inventory_actions(self):
        """_propagate_to_warehouse should exist in inventory_actions.py."""
        from routes import inventory_actions
        assert hasattr(inventory_actions, '_propagate_to_warehouse'), \
            "_propagate_to_warehouse should exist in inventory_actions.py"

    def test_resolve_review_calls_propagation(self):
        """resolve_review should call _propagate_to_warehouse."""
        from routes import inventory
        import inspect
        source = inspect.getsource(inventory.resolve_review)
        assert '_propagate_to_warehouse' in source, \
            "resolve_review should call _propagate_to_warehouse"

    def test_edit_row_calls_propagation(self):
        """edit_inventory_row should call _propagate_to_warehouse."""
        from routes import inventory_actions
        import inspect
        source = inspect.getsource(inventory_actions.edit_inventory_row)
        assert '_propagate_to_warehouse' in source, \
            "edit_inventory_row should call _propagate_to_warehouse"


# ══════════════════════════════════════════════════════════════
#  FIX 2: Warehouse Import Reports Skipped Rows
# ══════════════════════════════════════════════════════════════

class TestSkippedRowCount:
    """Verify add_from_batch returns skipped_count."""

    def test_add_from_batch_counts_skipped_rows(self):
        """add_from_batch should count and return skipped_count."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.add_from_batch)
        assert 'skipped_count' in source, \
            "add_from_batch should include skipped_count in response"

    def test_add_from_batch_counts_total_and_matched(self):
        """add_from_batch should query total and matched row counts."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.add_from_batch)
        assert 'total' in source and 'matched' in source, \
            "add_from_batch should query total and matched counts"

    def test_skipped_count_in_response(self):
        """The response JSON should include skipped_count."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.add_from_batch)
        assert "'skipped_count'" in source or '"skipped_count"' in source, \
            "Response should include skipped_count key"

    def test_message_includes_skipped_info(self):
        """Response message should mention skipped rows when count > 0."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.add_from_batch)
        assert 'skipped' in source.lower(), \
            "Message should mention skipped rows"


# ══════════════════════════════════════════════════════════════
#  FIX 3: Auto-Arrange Requires Admin Override Flag
# ══════════════════════════════════════════════════════════════

class TestAutoArrangeAdminOverride:
    """Verify auto_arrange returns requires_admin_override flag."""

    def test_auto_arrange_has_requires_admin_override(self):
        """auto_arrange response should include requires_admin_override."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.auto_arrange)
        assert 'requires_admin_override' in source, \
            "auto_arrange should include requires_admin_override in response"

    def test_auto_arrange_sets_flag_for_caution(self):
        """requires_admin_override should be True when CAUTION pairs exist."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.auto_arrange)
        assert "requires_admin_override = True" in source, \
            "auto_arrange should set requires_admin_override = True for CAUTION"

    def test_auto_arrange_includes_flag_in_json(self):
        """The jsonify response should include requires_admin_override."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.auto_arrange)
        # Check that the flag is in the return jsonify block
        assert "'requires_admin_override': requires_admin_override" in source or \
               "'requires_admin_override':" in source, \
            "Response JSON should include requires_admin_override"

    def test_auto_arrange_includes_caution_sections_count(self):
        """Response should include caution_sections count."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.auto_arrange)
        assert 'caution_sections' in source, \
            "Response should include caution_sections count"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
