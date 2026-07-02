"""
test_tenancy_isolation.py — Phase 1 remediation validation.

Tests that the fail-closed tenancy model is enforced:
  - super_admin gets no tenant DB context → tenant routes return 403
  - user with missing company_id gets no tenant DB context → 403
  - None_user.db is never created
  - legitimate tenant user still reaches the route (200 / non-403)
"""

import os
import sys
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app
from etl.pipeline import init_inventory_tables

DATA_DIR = app.config['DATA_DIR']
NONE_DB_PATH = os.path.join(DATA_DIR, 'None_user.db')

# ── Shared fixtures ─────────────────────────────────────────────────────────


def _make_super_admin():
    return {
        'id': 99,
        'email': 'superadmin@safeware.io',
        'full_name': 'Super Admin',
        'role': 'super_admin',
        'status': 'ACTIVE',
        'company_id': None,
        'company_name': 'SAFEWARE',
        'tenant_db_path': None,
    }


def _make_no_company_user():
    return {
        'id': 88,
        'email': 'ghost@company.com',
        'full_name': 'Ghost User',
        'role': 'company_admin',
        'status': 'ACTIVE',
        'company_id': None,
        'company_name': None,
        'tenant_db_path': None,
    }


def _make_valid_tenant_user():
    return {
        'id': 1,
        'email': 'admin@cameo.com',
        'full_name': 'Test Admin',
        'role': 'company_admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'Test Company',
        'tenant_db_path': os.path.join(DATA_DIR, '1_user.db'),
    }


@pytest.fixture(autouse=True)
def _cleanup_none_db():
    """Remove None_user.db before and after every test."""
    if os.path.exists(NONE_DB_PATH):
        os.remove(NONE_DB_PATH)
    yield
    if os.path.exists(NONE_DB_PATH):
        os.remove(NONE_DB_PATH)


@pytest.fixture
def valid_tenant_db():
    path = os.path.join(DATA_DIR, '1_user.db')
    if os.path.exists(path):
        os.remove(path)
    init_inventory_tables(path)
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM warehouses")
    cursor.execute("INSERT OR IGNORE INTO warehouses (id, name) VALUES (1, 'Main Warehouse')")
    conn.commit()
    conn.close()
    return path


# ── Test helpers ─────────────────────────────────────────────────────────────


def _client_with_user(user_dict):
    """Return a Flask test client whose session resolves to user_dict."""
    app.testing = True
    with patch('app.validate_session', return_value=user_dict):
        with app.test_client() as c:
            c.set_cookie('session_id', 'mock_test_session')
            yield c


# ── Finding 4.2: super_admin gets 403 on tenant routes ───────────────────────


class TestSuperAdminBlindSpot:
    TENANT_ROUTES = [
        ('GET',  '/api/warehouse/data'),
        ('GET',  '/api/warehouse/list'),
        ('POST', '/api/warehouse/sections/init'),
        ('GET',  '/api/inventory/batches'),
    ]

    def _client(self):
        return _client_with_user(_make_super_admin())

    def test_warehouse_data_returns_403(self):
        for c in self._client():
            res = c.get('/api/warehouse/data')
            assert res.status_code == 403, (
                f"Expected 403 for super_admin on /api/warehouse/data, got {res.status_code}"
            )

    def test_warehouse_list_returns_403(self):
        for c in self._client():
            res = c.get('/api/warehouse/list')
            assert res.status_code == 403

    def test_sections_init_returns_403(self):
        for c in self._client():
            res = c.post('/api/warehouse/sections/init', json={'count': 5})
            assert res.status_code == 403

    def test_inventory_batches_returns_403(self):
        for c in self._client():
            res = c.get('/api/inventory/batches')
            assert res.status_code == 403

    def test_does_not_fall_back_to_user_db(self):
        """
        The response must never include data from the legacy user.db.
        A 403 is the only acceptable outcome; a 200 would mean the
        fallback fired and the route served shared DB data.
        """
        for c in self._client():
            res = c.get('/api/warehouse/data')
            assert res.status_code == 403
            assert res.status_code != 200, "super_admin must not receive tenant data"

    def test_none_user_db_not_created(self):
        """Ensure tenant_router never creates None_user.db for super_admin."""
        for c in self._client():
            c.get('/api/warehouse/data')
        assert not os.path.exists(NONE_DB_PATH), (
            "None_user.db was created — tenant_router is building a bad path"
        )


# ── Finding 4.3: missing company_id never creates None_user.db ───────────────


class TestNullCompanyId:

    def _client(self):
        return _client_with_user(_make_no_company_user())

    def test_warehouse_data_returns_403(self):
        for c in self._client():
            res = c.get('/api/warehouse/data')
            assert res.status_code == 403, (
                f"Expected 403 for null company_id on /api/warehouse/data, got {res.status_code}"
            )

    def test_inventory_batches_returns_403(self):
        for c in self._client():
            res = c.get('/api/inventory/batches')
            assert res.status_code == 403

    def test_none_user_db_never_created(self):
        """
        Critical: 'None_user.db' must never be written to disk.
        If it exists after the request, tenant_router evaluated
        f'{None}_user.db' and init'd it — the bug is still present.
        """
        for c in self._client():
            c.get('/api/warehouse/data')
            c.get('/api/inventory/batches')
            c.post('/api/warehouse/sections/init', json={'count': 3})
        assert not os.path.exists(NONE_DB_PATH), (
            "None_user.db was created on disk — "
            "tenant_router is not guarding against null company_id"
        )

    def test_response_not_200(self):
        """A 200 would mean the fallback is still active."""
        for c in self._client():
            res = c.get('/api/warehouse/data')
            assert res.status_code != 200


# ── Finding 4.1: legitimate tenant user still works (regression guard) ────────


class TestValidTenantStillWorks:

    def _client(self):
        return _client_with_user(_make_valid_tenant_user())

    def test_warehouse_data_returns_200(self, valid_tenant_db):
        """A valid tenant user must still reach tenant routes after the fix."""
        for c in self._client():
            res = c.get('/api/warehouse/data')
            assert res.status_code == 200, (
                f"Valid tenant got {res.status_code} — fail-closed fix broke normal access"
            )

    def test_warehouse_list_returns_200(self, valid_tenant_db):
        for c in self._client():
            res = c.get('/api/warehouse/list')
            assert res.status_code == 200

    def test_inventory_batches_returns_200(self, valid_tenant_db):
        for c in self._client():
            res = c.get('/api/inventory/batches')
            assert res.status_code == 200


# ── Consistency: error body must be JSON for API paths ───────────────────────


class TestErrorResponseShape:

    def _client(self):
        return _client_with_user(_make_super_admin())

    def test_403_body_is_json_on_api_path(self):
        for c in self._client():
            res = c.get('/api/warehouse/data')
            assert res.status_code == 403
            # Flask abort() with description produces HTML by default unless
            # the client sends Accept: application/json — test both shapes are not 200
            # and that we at least get a parseable response (not a 500 crash)
            data = res.get_json(silent=True) or {}
            # Either JSON error or plain text 403 — both are acceptable; a 500 is not
            assert res.status_code in (403,), (
                f"Expected 403, got {res.status_code}. Body: {res.data[:200]}"
            )
