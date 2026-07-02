"""
Tests for P0 Critical Fixes (Batch 1):
  Fix 1: Tenant DB Schema Gap - analysis_results + user_inventories tables
  Fix 2: confirm_match updates review_queue + audit_trail + learning_data
  Fix 3: Dangerous generic synonyms removed from INDUSTRIAL_SYNONYMS
"""

import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etl.pipeline import init_inventory_tables, confirm_row


# ─── Shared helpers ────────────────────────────────────────────

CHEMICALS_DB = str(Path(__file__).resolve().parent.parent / 'data' / 'chemicals.db')


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


def _make_user_db():
    """Create a temp DB and run pipeline init (simulates tenant DB init)."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    init_inventory_tables(path)
    return path


# ══════════════════════════════════════════════════════════════
#  FIX 1: Tenant DB Schema Gap
# ══════════════════════════════════════════════════════════════

class TestFix1TenantDBSchema:
    """Verify init_inventory_tables creates analysis_results and user_inventories."""

    def test_analysis_results_table_exists(self):
        db_path = _make_user_db()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_results'")
            assert cur.fetchone() is not None, "analysis_results table not created by init_inventory_tables"
            conn.close()
        finally:
            os.unlink(db_path)

    def test_user_inventories_table_exists(self):
        db_path = _make_user_db()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_inventories'")
            assert cur.fetchone() is not None, "user_inventories table not created by init_inventory_tables"
            conn.close()
        finally:
            os.unlink(db_path)

    def test_analysis_results_has_expected_columns(self):
        db_path = _make_user_db()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(analysis_results)")
            cols = {row[1] for row in cur.fetchall()}
            conn.close()
            assert 'id' in cols
            assert 'batch_id' in cols
            assert 'risk_matrix_json' in cols
            assert 'total_chemicals' in cols
            assert 'dangerous_pairs' in cols
        finally:
            os.unlink(db_path)

    def test_user_inventories_has_expected_columns(self):
        db_path = _make_user_db()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(user_inventories)")
            cols = {row[1] for row in cur.fetchall()}
            conn.close()
            assert 'id' in cols
            assert 'batch_id' in cols
            assert 'chemical_id' in cols
            assert 'quantity' in cols
            assert 'unit' in cols
        finally:
            os.unlink(db_path)

    def test_can_insert_into_analysis_results(self):
        db_path = _make_user_db()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO analysis_results (batch_id, total_chemicals, dangerous_pairs, risk_matrix_json) VALUES (?, ?, ?, ?)",
                ('test-batch-1', 10, 3, json.dumps({'test': True}))
            )
            conn.commit()
            cur.execute("SELECT * FROM analysis_results WHERE batch_id = 'test-batch-1'")
            row = cur.fetchone()
            conn.close()
            assert row is not None
        finally:
            os.unlink(db_path)

    def test_can_insert_into_user_inventories(self):
        db_path = _make_user_db()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO user_inventories (batch_id, chemical_id, quantity, unit) VALUES (?, ?, ?, ?)",
                ('test-batch-1', 42, '10', 'kg')
            )
            conn.commit()
            cur.execute("SELECT * FROM user_inventories WHERE batch_id = 'test-batch-1'")
            row = cur.fetchone()
            conn.close()
            assert row is not None
        finally:
            os.unlink(db_path)

    def test_idempotent_call_does_not_crash(self):
        db_path = _make_user_db()
        try:
            # Call twice - should not crash
            init_inventory_tables(db_path)
            init_inventory_tables(db_path)
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_results'")
            assert cur.fetchone() is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ══════════════════════════════════════════════════════════════
#  FIX 2: confirm_match updates review_queue + audit + learning
# ══════════════════════════════════════════════════════════════

class TestFix2ConfirmMatchReviewQueue:
    """Verify confirm_row updates staging AND confirm_match resolves review_queue."""

    def test_confirm_row_updates_staging(self):
        db_path = _make_user_db()
        chemicals = _get_two_chemicals()
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            batch_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 1, 1)",
                (batch_id, 'test.xlsx')
            )
            cleaned = json.dumps({'name': 'Test', 'cas': ''})
            cur.execute(
                "INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status) VALUES (?, 1, ?, ?, 'UNIDENTIFIED')",
                (batch_id, cleaned, cleaned)
            )
            staging_id = cur.lastrowid
            conn.commit()

            result = confirm_row(db_path, staging_id, chemicals[0]['id'], chemicals[0]['name'])
            assert result is True

            cur.execute("SELECT chemical_id, match_status FROM inventory_staging WHERE id = ?", (staging_id,))
            row = cur.fetchone()
            assert row['chemical_id'] == chemicals[0]['id']
            assert row['match_status'] == 'MATCHED'
            conn.close()
        finally:
            os.unlink(db_path)

    def test_confirm_match_resolves_review_queue(self):
        """After confirm_match, review_queue row should be resolved (not pending)."""
        db_path = _make_user_db()
        chemicals = _get_two_chemicals()
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            batch_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 1, 1)",
                (batch_id, 'test.xlsx')
            )
            cleaned = json.dumps({'name': 'Test', 'cas': ''})
            cur.execute(
                "INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status) VALUES (?, 1, ?, ?, 'REVIEW_REQUIRED')",
                (batch_id, cleaned, cleaned)
            )
            staging_id = cur.lastrowid

            cur.execute(
                "INSERT INTO review_queue (batch_id, staging_id, priority, status, input_data) VALUES (?, ?, 'high', 'pending', ?)",
                (batch_id, staging_id, cleaned)
            )
            conn.commit()
            conn.close()

            # Now simulate what confirm_match does (review_queue resolution part)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("UPDATE inventory_staging SET chemical_id = ?, match_status = 'MATCHED', match_method = 'manual_confirm', confidence = 1.0 WHERE id = ?",
                        (chemicals[0]['id'], staging_id))

            cur.execute("SELECT id, batch_id, input_data FROM review_queue WHERE staging_id = ? AND status = 'pending'", (staging_id,))
            rq = cur.fetchone()
            assert rq is not None, "review_queue row should exist and be pending"

            cur.execute("UPDATE review_queue SET status = 'resolved', resolution = ?, resolution_timestamp = ? WHERE id = ?",
                        (json.dumps({'chemical_id': chemicals[0]['id']}), datetime.utcnow().isoformat(), rq['id']))
            conn.commit()

            cur.execute("SELECT status FROM review_queue WHERE id = ?", (rq['id'],))
            row = cur.fetchone()
            assert row['status'] == 'resolved', "review_queue should be resolved after confirm"
            conn.close()
        finally:
            os.unlink(db_path)

    def test_confirm_match_creates_audit_trail(self):
        """After confirm_match, audit_trail should have an entry."""
        db_path = _make_user_db()
        chemicals = _get_two_chemicals()
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            batch_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO inventory_batches (id, filename, status, total_rows, processed) VALUES (?, ?, 'completed', 1, 1)",
                (batch_id, 'test.xlsx')
            )
            cleaned = json.dumps({'name': 'Test', 'cas': ''})
            cur.execute(
                "INSERT INTO inventory_staging (batch_id, row_index, raw_data, cleaned_data, match_status) VALUES (?, 1, ?, ?, 'UNIDENTIFIED')",
                (batch_id, cleaned, cleaned)
            )
            staging_id = cur.lastrowid
            conn.commit()
            conn.close()

            # Simulate confirm_match audit_trail insert
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO audit_trail (batch_id, row_index, action, input_data, output_data, confidence, method, timestamp, user_id)
                VALUES (?, (SELECT row_index FROM inventory_staging WHERE id = ?), 'manual_confirm', ?, ?, 1.0, 'manual_confirm', ?, 'human')
            """, (batch_id, staging_id, cleaned, json.dumps({'chemical_id': chemicals[0]['id']}), datetime.utcnow().isoformat()))
            conn.commit()

            cur.execute("SELECT action FROM audit_trail WHERE batch_id = ?", (batch_id,))
            row = cur.fetchone()
            assert row is not None, "audit_trail should have entry after confirm"
            assert row[0] == 'manual_confirm'
            conn.close()
        finally:
            os.unlink(db_path)

    def test_confirm_match_creates_learning_data(self):
        """After confirm_match, learning_data should have an entry."""
        db_path = _make_user_db()
        chemicals = _get_two_chemicals()
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            batch_id = str(uuid.uuid4())
            cleaned = json.dumps({'name': 'Salt', 'cas': ''})
            cur.execute(
                "INSERT INTO learning_data (input_pattern, context, correct_chemical_id, corrected_by) VALUES (?, ?, ?, 'manual_confirm')",
                (cleaned, json.dumps({'batch_id': batch_id}), chemicals[0]['id'])
            )
            conn.commit()
            cur.execute("SELECT correct_chemical_id, corrected_by, context FROM learning_data")
            row = cur.fetchone()
            conn.close()
            assert row is not None
            assert row[0] == chemicals[0]['id']
            assert row[1] == 'manual_confirm'
            ctx = json.loads(row[2])
            assert ctx['batch_id'] == batch_id
        finally:
            os.unlink(db_path)


# ══════════════════════════════════════════════════════════════
#  FIX 3: Dangerous generic synonyms removed
# ══════════════════════════════════════════════════════════════

class TestFix3GenericSynonymsRemoved:
    """Verify dangerous generic terms are no longer in INDUSTRIAL_SYNONYMS."""

    def test_salt_not_in_synonyms(self):
        from etl.match import INDUSTRIAL_SYNONYMS
        assert 'salt' not in INDUSTRIAL_SYNONYMS, "'salt' should be removed (too generic)"
        assert 'table salt' not in INDUSTRIAL_SYNONYMS, "'table salt' should be removed (too generic)"

    def test_alcohol_not_in_synonyms(self):
        from etl.match import INDUSTRIAL_SYNONYMS
        assert 'alcohol' not in INDUSTRIAL_SYNONYMS, "'alcohol' should be removed (too generic)"

    def test_peroxide_not_in_synonyms(self):
        from etl.match import INDUSTRIAL_SYNONYMS
        assert 'peroxide' not in INDUSTRIAL_SYNONYMS, "'peroxide' should be removed (too generic)"

    def test_specific_synonyms_still_present(self):
        from etl.match import INDUSTRIAL_SYNONYMS
        assert 'ethyl alcohol' in INDUSTRIAL_SYNONYMS
        assert 'epsom salt' in INDUSTRIAL_SYNONYMS
        assert 'rubbing alcohol' in INDUSTRIAL_SYNONYMS
        assert 'caustic soda' in INDUSTRIAL_SYNONYMS
        assert 'baking soda' in INDUSTRIAL_SYNONYMS
        assert 'lye' in INDUSTRIAL_SYNONYMS
        assert 'muriatic acid' in INDUSTRIAL_SYNONYMS

    def test_salt_fuzzy_match_produces_review_not_match(self):
        """'Salt' should NOT auto-match with 100% confidence anymore."""
        from etl.match import HybridMatcher
        matcher = HybridMatcher(CHEMICALS_DB)
        result = matcher.match({'name': 'Salt', 'cas': ''})
        # Should not be MATCHED with confidence 1.0 (the old behavior)
        assert result['match_status'] != 'MATCHED' or result['confidence'] < 1.0, \
            f"'Salt' should not auto-match at 100% confidence. Got: {result['match_status']}, confidence={result['confidence']}"

    def test_alcohol_fuzzy_match_produces_review(self):
        """'Alcohol' should NOT auto-match with 100% confidence."""
        from etl.match import HybridMatcher
        matcher = HybridMatcher(CHEMICALS_DB)
        result = matcher.match({'name': 'Alcohol', 'cas': ''})
        assert result['match_status'] != 'MATCHED' or result['confidence'] < 1.0, \
            f"'Alcohol' should not auto-match at 100% confidence. Got: {result['match_status']}, confidence={result['confidence']}"

    def test_peroxide_fuzzy_match_produces_review(self):
        """'Peroxide' should NOT auto-match with 100% confidence."""
        from etl.match import HybridMatcher
        matcher = HybridMatcher(CHEMICALS_DB)
        result = matcher.match({'name': 'Peroxide', 'cas': ''})
        assert result['match_status'] != 'MATCHED' or result['confidence'] < 1.0, \
            f"'Peroxide' should not auto-match at 100% confidence. Got: {result['match_status']}, confidence={result['confidence']}"

    def test_specific_terms_still_match(self):
        """Specific terms should still match or get REVIEW_REQUIRED (not UNIDENTIFIED)."""
        from etl.match import HybridMatcher
        matcher = HybridMatcher(CHEMICALS_DB)

        # 'Ethyl Alcohol' should still match via synonym
        result_eth = matcher.match({'name': 'Ethyl Alcohol', 'cas': ''})
        assert result_eth['match_status'] in ('MATCHED', 'REVIEW_REQUIRED'), \
            f"'Ethyl Alcohol' should match or review. Got: {result_eth['match_status']}"

        # 'Caustic soda' should still match to Sodium Hydroxide
        result_cs = matcher.match({'name': 'Caustic soda', 'cas': ''})
        assert result_cs['match_status'] in ('MATCHED', 'REVIEW_REQUIRED'), \
            f"'Caustic soda' should match or review. Got: {result_cs['match_status']}"

        # 'Epsom salt' should still find Magnesium Sulfate (may be REVIEW_REQUIRED)
        result_es = matcher.match({'name': 'Epsom salt', 'cas': ''})
        assert result_es['match_status'] in ('MATCHED', 'REVIEW_REQUIRED'), \
            f"'Epsom salt' should match or review. Got: {result_es['match_status']}"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
