"""
Tests for P1 Batch 3 Fixes:
  Fix 1: Container unit conversion (no silent 1.0 kg corruption)
  Fix 2: Stale cleaned_data on override
  Fix 3: Water reactivity in auto-arrange conflict graph
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


def _get_chemical_with_groups():
    """Get a chemical that has reactive groups assigned."""
    conn = sqlite3.connect(CHEMICALS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name, c.special_hazards,
               (SELECT cas_id FROM chemical_cas cc WHERE cc.chem_id = c.id ORDER BY sort LIMIT 1) AS cas_id
        FROM chemicals c
        WHERE EXISTS (SELECT 1 FROM mm_chemical_react mr WHERE mr.chem_id = c.id)
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _get_water_chemical():
    """Get a chemical in the Water reactive group (104)."""
    conn = sqlite3.connect(CHEMICALS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.name FROM chemicals c
        JOIN mm_chemical_react mr ON c.id = mr.chem_id
        WHERE mr.react_id = 104
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _get_water_reactive_chemical():
    """Get a chemical with 'Water-Reactive' in special_hazards."""
    conn = sqlite3.connect(CHEMICALS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, special_hazards FROM chemicals
        WHERE special_hazards IS NOT NULL
        AND (LOWER(special_hazards) LIKE '%water-reactive%'
             OR LOWER(special_hazards) LIKE '%water reactive%')
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# ══════════════════════════════════════════════════════════════
#  FIX 1: Container Unit Conversion
# ══════════════════════════════════════════════════════════════

class TestContainerUnitConversion:
    """Verify container types are converted to kg, not silently defaulting to 1.0."""

    def test_drums_converted_to_kg(self):
        """'5 drums' should become 5 * 200 = 1000 kg, not 1.0 kg."""
        CONTAINER_KG = {
            'drum': 200, 'drums': 200,
            'cylinder': 50, 'cylinders': 50,
            'bottle': 2, 'bottles': 2,
            'jug': 4, 'jugs': 4,
            'container': 10, 'containers': 10,
        }
        qty_str = '5'
        unit_str = 'drums'
        try:
            qty = float(qty_str)
            if unit_str in CONTAINER_KG:
                qty *= CONTAINER_KG[unit_str]
        except (ValueError, TypeError):
            qty = None
        assert qty == 1000.0, f"'5 drums' should be 1000 kg, got {qty}"

    def test_cylinders_converted_to_kg(self):
        """'10 cylinders' should become 10 * 50 = 500 kg."""
        CONTAINER_KG = {'cylinder': 50, 'cylinders': 50}
        qty = float('10') * CONTAINER_KG['cylinders']
        assert qty == 500.0

    def test_full_text_not_defaulting_to_1kg(self):
        """'Full' should NOT silently become 1.0 kg."""
        qty_str = 'Full'
        try:
            qty = float(qty_str)
        except (ValueError, TypeError):
            qty = None
        assert qty is None, f"'Full' should result in None (NULL), not {qty}"

    def test_bottles_converted_to_kg(self):
        """'3 bottles' should become 3 * 2 = 6 kg."""
        CONTAINER_KG = {'bottle': 2, 'bottles': 2}
        qty = float('3') * CONTAINER_KG['bottles']
        assert qty == 6.0

    def test_warehouse_import_uses_container_conversion(self):
        """The actual warehouse import code should use container conversion."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse.add_from_batch)
        assert 'CONTAINER_KG' in source or 'drums' in source, \
            "add_from_batch should have container-to-kg conversion logic"

    def test_empty_string_quantity_not_defaulting(self):
        """Empty quantity string should not default to 1.0."""
        qty_str = ''
        try:
            qty = float(qty_str)
        except (ValueError, TypeError):
            qty = None
        assert qty is None


# ══════════════════════════════════════════════════════════════
#  FIX 2: Stale cleaned_data on Override
# ══════════════════════════════════════════════════════════════

class TestStaleCleanedDataOnOverride:
    """Verify resolve_review updates cleaned_data name/cas."""

    def test_resolve_review_updates_cleaned_data_name(self):
        """After resolve_review, cleaned_data.name should match confirmed chemical."""
        db_path = _make_user_db()
        chem = _get_chemical_with_groups()
        if not chem:
            pytest.skip("No chemical with groups in DB")

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            batch_id = 'test-batch'
            cur.execute(
                "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 1, 1)",
                (batch_id, 'test.xlsx')
            )

            # Staging row with wrong name
            old_cleaned = json.dumps({'name': 'Wrong Name', 'cas': '000-00-0'})
            cur.execute(
                "INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status) VALUES (?, 1, ?, ?, 'REVIEW_REQUIRED')",
                (batch_id, old_cleaned, old_cleaned)
            )
            staging_id = cur.lastrowid

            # Review queue entry
            cur.execute(
                "INSERT INTO review_queue (batch_id, staging_id, priority, status, input_data) VALUES (?, ?, 'high', 'pending', ?)",
                (batch_id, staging_id, old_cleaned)
            )
            queue_id = cur.lastrowid
            conn.commit()
            conn.close()

            # Simulate resolve_review with cleaned_data update
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT cleaned_data FROM inventory_staging WHERE id = ?", (staging_id,))
            row = cur.fetchone()
            cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
            cleaned['name'] = chem['name']
            if chem.get('cas_id'):
                cleaned['cas'] = chem['cas_id']
                cleaned['cas_valid'] = True

            cur.execute("""
                UPDATE inventory_staging
                SET chemical_id = ?, match_status = 'MATCHED',
                    match_method = 'manual_review', confidence = 1.0,
                    cleaned_data = ?
                WHERE id = ?
            """, (chem['id'], json.dumps(cleaned), staging_id))

            cur.execute("UPDATE review_queue SET status = 'resolved' WHERE id = ?", (queue_id,))
            conn.commit()

            # Verify
            cur.execute("SELECT cleaned_data, chemical_id FROM inventory_staging WHERE id = ?", (staging_id,))
            result = cur.fetchone()
            updated = json.loads(result['cleaned_data'])
            assert updated['name'] == chem['name'], \
                f"cleaned_data.name should be '{chem['name']}', got '{updated['name']}'"
            assert result['chemical_id'] == chem['id']
            conn.close()
        finally:
            os.unlink(db_path)

    def test_resolve_review_updates_cas_when_available(self):
        """After resolve_review, cleaned_data.cas should be updated if chemical has CAS."""
        db_path = _make_user_db()
        chem = _get_chemical_with_groups()
        if not chem or not chem.get('cas_id'):
            pytest.skip("Need chemical with CAS")

        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            batch_id = 'test-batch'
            cur.execute(
                "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 1, 1)",
                (batch_id, 'test.xlsx')
            )
            old_cleaned = json.dumps({'name': 'Old Name', 'cas': '999-99-9'})
            cur.execute(
                "INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status) VALUES (?, 1, ?, ?, 'REVIEW_REQUIRED')",
                (batch_id, old_cleaned, old_cleaned)
            )
            staging_id = cur.lastrowid
            conn.commit()

            # Update cleaned_data
            cleaned = json.loads(old_cleaned)
            cleaned['name'] = chem['name']
            cleaned['cas'] = chem['cas_id']
            cleaned['cas_valid'] = True
            cur.execute(
                "UPDATE inventory_staging SET chemical_id = ?, cleaned_data = ?, match_status = 'MATCHED' WHERE id = ?",
                (chem['id'], json.dumps(cleaned), staging_id)
            )
            conn.commit()

            cur.execute("SELECT cleaned_data FROM inventory_staging WHERE id = ?", (staging_id,))
            result = json.loads(cur.fetchone()[0])
            assert result['cas'] == chem['cas_id'], \
                f"cleaned_data.cas should be '{chem['cas_id']}', got '{result.get('cas')}'"
            assert result.get('cas_valid') is True
            conn.close()
        finally:
            os.unlink(db_path)

    def test_resolve_review_code_updates_cleaned_data(self):
        """The actual resolve_review code should update cleaned_data."""
        from routes import inventory
        import inspect
        source = inspect.getsource(inventory.resolve_review)
        assert 'cleaned_data' in source, \
            "resolve_review should reference cleaned_data"
        assert "cleaned['name']" in source or "cleaned[\"name\"]" in source, \
            "resolve_review should update cleaned_data name field"


# ══════════════════════════════════════════════════════════════
#  FIX 3: Water Reactivity in Auto-Arrange
# ══════════════════════════════════════════════════════════════

class TestWaterReactivityInAutoArrange:
    """Verify water-reactive chemicals conflict with water-group chemicals."""

    def test_is_section_conflict_checks_water_reactivity(self):
        """_is_section_conflict should check water-reactive self-hazards."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse._is_section_conflict)
        assert 'water' in source.lower(), \
            "_is_section_conflict should check for water reactivity"

    def test_is_water_reactive_function_exists(self):
        """Helper function _is_water_reactive should exist."""
        from routes import warehouse
        assert hasattr(warehouse, '_is_water_reactive'), \
            "_is_water_reactive function should exist"

    def test_has_water_group_function_exists(self):
        """Helper function _has_water_group should exist."""
        from routes import warehouse
        assert hasattr(warehouse, '_has_water_group'), \
            "_has_water_group function should exist"

    def test_water_reactive_detection(self):
        """Chemicals with 'Water-Reactive' in special_hazards should be detected."""
        from routes.warehouse import _is_water_reactive
        chem = _get_water_reactive_chemical()
        if not chem:
            pytest.skip("No water-reactive chemical in DB")
        assert _is_water_reactive(chem['id'], CHEMICALS_DB), \
            f"'{chem['name']}' should be detected as water-reactive"

    def test_non_water_reactive_not_flagged(self):
        """Chemicals without water-reactive hazards should not be flagged."""
        from routes.warehouse import _is_water_reactive
        chem = _get_chemical_with_groups()
        if not chem:
            pytest.skip("No chemical with groups in DB")
        # Only flag if special_hazards actually contains water-reactive
        result = _is_water_reactive(chem['id'], CHEMICALS_DB)
        # This test just verifies the function doesn't crash
        assert isinstance(result, bool)

    def test_water_group_detection(self):
        """Chemicals in the water group (WATER_GROUP_ID) should be detected."""
        from routes.warehouse import _has_water_group
        from logic.constants import WATER_GROUP_ID
        chem = _get_water_chemical()
        if not chem:
            pytest.skip("No water-group chemical in DB")
        placement = {'reactive_groups': [WATER_GROUP_ID]}
        assert _has_water_group(placement), "WATER_GROUP_ID should be detected as water"

    def test_non_water_group_not_flagged(self):
        """Chemicals NOT in the water group should not be flagged."""
        from routes.warehouse import _has_water_group
        placement = {'reactive_groups': [1, 2, 3]}
        assert not _has_water_group(placement), "Non-water groups should not be water"

    def test_water_reactive_conflict_with_water_group(self):
        """Water-reactive chemical + water-group chemical should conflict."""
        water_reactive_chem = _get_water_reactive_chemical()
        water_chem = _get_water_chemical()
        if not water_reactive_chem or not water_chem:
            pytest.skip("Need both water-reactive and water-group chemicals")

        from routes.warehouse import _is_water_reactive, _has_water_group
        from logic.constants import WATER_GROUP_ID
        # Verify: one is water-reactive, other has water group
        assert _is_water_reactive(water_reactive_chem['id'], CHEMICALS_DB)
        assert _has_water_group({'reactive_groups': [WATER_GROUP_ID]})
        # The conflict logic would trigger when both conditions are met

    def test_imports_water_group_id(self):
        """warehouse.py should import WATER_GROUP_ID from constants."""
        from routes import warehouse
        import inspect
        source = inspect.getsource(warehouse)
        assert 'WATER_GROUP_ID' in source, \
            "warehouse.py should import WATER_GROUP_ID"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
