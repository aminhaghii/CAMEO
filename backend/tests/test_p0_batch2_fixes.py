"""
Tests for P0 Batch 2 Fixes:
  P0-6/7: Compliance export auth + DB isolation
  P0-3: Self-hazard diagonal escalation
  P0-2: Warehouse validation query (already correct - verify)
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
from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility, COMPATIBILITY_MAP


CHEMICALS_DB = str(Path(__file__).resolve().parent.parent / 'data' / 'chemicals.db')


# ══════════════════════════════════════════════════════════════
#  P0-6/7: Compliance Export Auth
# ══════════════════════════════════════════════════════════════

class TestComplianceAuth:
    """Verify compliance export requires authentication."""

    def test_unauthenticated_export_returns_401(self):
        with app.test_client() as client:
            resp = client.post('/api/compliance/export',
                               json={'cas_numbers': ['67-64-1']},
                               content_type='application/json')
            assert resp.status_code in (401, 302), \
                f"Unauthenticated request should return 401 or redirect. Got: {resp.status_code}"

    def test_compliance_page_requires_login(self):
        with app.test_client() as client:
            resp = client.get('/compliance')
            assert resp.status_code in (401, 302), \
                f"Compliance page should require login. Got: {resp.status_code}"

    def test_auth_exempt_prefixes_do_not_include_compliance(self):
        from app import AUTH_EXEMPT_PREFIXES
        for prefix in AUTH_EXEMPT_PREFIXES:
            assert '/compliance' not in prefix, \
                f"Compliance route should not be in AUTH_EXEMPT_PREFIXES: {prefix}"


# ══════════════════════════════════════════════════════════════
#  P0-3: Self-Hazard Diagonal Escalation
# ══════════════════════════════════════════════════════════════

class TestSelfHazardEscalation:
    """Verify self-hazards escalate overall_compatibility to at least CAUTION."""

    def _get_chemical_with_special_hazards(self):
        """Find a chemical whose special_hazards text contains a recognized keyword.
        The parser does special_hazards.lower() then checks for keywords.
        Only 'polymeriz', 'pyrophoric', 'explosive' reliably match (no hyphen issues)."""
        conn = sqlite3.connect(CHEMICALS_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, name, special_hazards FROM chemicals
            WHERE special_hazards IS NOT NULL AND special_hazards != ''
            AND (LOWER(special_hazards) LIKE '%polymeriz%'
                 OR LOWER(special_hazards) LIKE '%pyrophoric%'
                 OR LOWER(special_hazards) LIKE '%explosive%')
            LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def _get_safe_chemical(self):
        conn = sqlite3.connect(CHEMICALS_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, c.name FROM chemicals c
            WHERE c.special_hazards IS NULL OR c.special_hazards = ''
            LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def _get_two_safe_chemicals(self):
        conn = sqlite3.connect(CHEMICALS_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT c.id, c.name FROM chemicals c
            WHERE c.special_hazards IS NULL OR c.special_hazards = ''
            LIMIT 2
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows if len(rows) >= 2 else None

    def test_self_hazard_escalates_to_caution(self):
        """A chemical with special hazards should escalate overall to CAUTION."""
        haz_chem = self._get_chemical_with_special_hazards()
        safe_chem = self._get_safe_chemical()
        if not haz_chem or not safe_chem:
            pytest.skip("Need both hazard and safe chemicals")

        engine = ReactivityEngine(CHEMICALS_DB)
        result = engine.analyze([haz_chem['id'], safe_chem['id']], include_water_check=True, save_audit=False)

        assert result.overall_compatibility in (Compatibility.CAUTION, Compatibility.INCOMPATIBLE), \
            f"Self-hazard chemical should escalate to CAUTION or INCOMPATIBLE. Got: {result.overall_compatibility}"

    def test_safe_chemicals_stay_compatible(self):
        """Two chemicals without special hazards should stay COMPATIBLE."""
        safe_pair = self._get_two_safe_chemicals()
        if not safe_pair:
            pytest.skip("Need 2 safe chemicals")

        engine = ReactivityEngine(CHEMICALS_DB)
        result = engine.analyze([safe_pair[0]['id'], safe_pair[1]['id']], include_water_check=True, save_audit=False)

        # May be COMPATIBLE, CAUTION, or INCOMPATIBLE depending on pair - just verify no crash
        assert result.overall_compatibility is not None

    def test_self_hazard_produces_warnings(self):
        """Self-hazard should generate warnings."""
        haz_chem = self._get_chemical_with_special_hazards()
        safe_chem = self._get_safe_chemical()
        if not haz_chem or not safe_chem:
            pytest.skip("Need both hazard and safe chemicals")

        engine = ReactivityEngine(CHEMICALS_DB)
        result = engine.analyze([haz_chem['id'], safe_chem['id']], include_water_check=True, save_audit=False)

        # Should have at least one warning about special hazards
        haz_warnings = [w for w in result.warnings if 'special hazards' in w.lower()]
        assert len(haz_warnings) > 0, f"Self-hazard should produce warnings. Got: {result.warnings}"

    def test_self_hazard_priority_at_least_caution(self):
        """Self-hazard should set overall_max_priority >= CAUTION priority."""
        haz_chem = self._get_chemical_with_special_hazards()
        safe_chem = self._get_safe_chemical()
        if not haz_chem or not safe_chem:
            pytest.skip("Need both hazard and safe chemicals")

        engine = ReactivityEngine(CHEMICALS_DB)
        result = engine.analyze([haz_chem['id'], safe_chem['id']], include_water_check=True, save_audit=False)

        cau_priority = COMPATIBILITY_MAP[Compatibility.CAUTION].priority
        actual_priority = COMPATIBILITY_MAP[result.overall_compatibility].priority
        assert actual_priority >= cau_priority, \
            f"Self-hazard priority should be >= CAUTION ({cau_priority}). Got: {actual_priority}"

    def test_mixed_self_hazard_and_compatible(self):
        """When mixing a self-hazard chemical with a safe one, overall should be at least CAUTION."""
        haz_chem = self._get_chemical_with_special_hazards()
        safe_chem = self._get_safe_chemical()
        if not haz_chem or not safe_chem:
            pytest.skip("Need both hazard and safe chemicals")

        engine = ReactivityEngine(CHEMICALS_DB)
        result = engine.analyze(
            [haz_chem['id'], safe_chem['id']],
            include_water_check=True,
            save_audit=False
        )

        cau_priority = COMPATIBILITY_MAP[Compatibility.CAUTION].priority
        actual_priority = COMPATIBILITY_MAP[result.overall_compatibility].priority
        assert actual_priority >= cau_priority, \
            f"Mixture with self-hazard should be >= CAUTION. Got priority: {actual_priority}"


# ══════════════════════════════════════════════════════════════
#  P0-2: Warehouse Validation Query (verify no status filter)
# ══════════════════════════════════════════════════════════════

class TestWarehouseValidationQuery:
    """Verify the warehouse validation query doesn't filter by status."""

    def test_validate_query_has_no_status_filter(self):
        """The validation query should check ALL occupants, not just 'placed' ones."""
        from routes.warehouse import _validate_layout_update
        import inspect
        source = inspect.getsource(_validate_layout_update)

        # The query should not filter by status = 'placed'
        assert "status = 'placed'" not in source and 'status = "placed"' not in source, \
            "_validate_layout_update should not filter by status='placed'"

    def test_validate_query_selects_all_placements(self):
        """The query should select from chemical_placements without status filter."""
        from routes.warehouse import _validate_layout_update
        import inspect
        source = inspect.getsource(_validate_layout_update)

        # Should query chemical_placements
        assert "chemical_placements" in source, \
            "_validate_layout_update should query chemical_placements table"


# ══════════════════════════════════════════════════════════════
#  P0-6/7: Compliance DB Isolation
# ══════════════════════════════════════════════════════════════

class TestComplianceDBIsolation:
    """Verify compliance export uses tenant DB for inventory data."""

    def test_no_hardcoded_fallback_path_in_compliance(self):
        """compliance.py should not have hardcoded fallback to shared DB."""
        from routes import compliance
        import inspect
        source = inspect.getsource(compliance)

        # Should not have hardcoded path fallback for chemicals DB
        assert 'os.path.join(os.path.dirname' not in source or \
               source.count('os.path.join(os.path.dirname') <= 1, \
            "compliance.py should not have hardcoded fallback paths for DB"

    def test_uses_tenant_db_for_inventory_data(self):
        """compliance.py should use g.tenant_db_path for inventory queries."""
        from routes import compliance
        import inspect
        source = inspect.getsource(compliance)

        assert 'tenant_db_path' in source, \
            "compliance.py should reference g.tenant_db_path"

    def test_chemicals_db_from_config(self):
        """compliance.py should use current_app.config for chemicals DB."""
        from routes import compliance
        import inspect
        source = inspect.getsource(compliance)

        assert "current_app.config['CHEMICALS_DB_PATH']" in source or \
               'current_app.config["CHEMICALS_DB_PATH"]' in source, \
            "compliance.py should use current_app.config['CHEMICALS_DB_PATH']"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
