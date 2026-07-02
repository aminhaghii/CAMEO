"""
Tests for Super Admin Platform-Wide Activity Log Dashboard.

Covers:
  - /admin/platform-logs page: super_admin → 200, company_admin → 403
  - /api/admin/platform-logs: super_admin → data, company_admin → 403
  - /api/admin/platform-logs/stats: super_admin → stats structure
  - company_id filter via _get_all_tenant_db_paths helper
  - base.html nav includes Platform Logs link for super_admin
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app


# ── shared user fixtures ─────────────────────────────────────────

def make_super_admin():
    return {
        'id': 1,
        'email': 'admin@safeware.io',
        'role': 'super_admin',
        'full_name': 'Super Admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'SAFEWARE Platform',
        'tenant_db_path': None,
        'force_password_change': 0,
    }


def make_company_admin():
    return {
        'id': 2,
        'email': 'cadmin@acme.com',
        'role': 'company_admin',
        'full_name': 'Company Admin',
        'status': 'ACTIVE',
        'company_id': 10,
        'company_name': 'ACME Corp',
        'tenant_db_path': '/tmp/fake_10_user.db',
        'force_password_change': 0,
    }


def make_operator():
    return {
        'id': 3,
        'email': 'op@acme.com',
        'role': 'operator',
        'full_name': 'Operator',
        'status': 'ACTIVE',
        'company_id': 10,
        'company_name': 'ACME Corp',
        'tenant_db_path': '/tmp/fake_10_user.db',
        'force_password_change': 0,
    }


def seed_activity_logs(db_path, rows):
    """Create activity_logs table and insert test rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type   TEXT NOT NULL,
            category     TEXT NOT NULL DEFAULT 'system',
            severity     TEXT NOT NULL DEFAULT 'info',
            title        TEXT NOT NULL,
            detail       TEXT,
            user_id      TEXT,
            entity_type  TEXT,
            entity_id    TEXT,
            entity_name  TEXT,
            meta         TEXT,
            ip_address   TEXT,
            session_id   TEXT,
            duration_ms  INTEGER,
            created_at   DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO activity_logs (event_type, category, severity, title, user_id) VALUES (?,?,?,?,?)",
            (r.get('event_type', 'test'), r.get('category', 'system'),
             r.get('severity', 'info'), r.get('title', 'Test event'),
             r.get('user_id', 'user@test.com'))
        )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
#  Page auth guard
# ══════════════════════════════════════════════════════════════

class TestPlatformLogsPage:

    def test_page_unauthenticated_redirects(self):
        """Unauthenticated request to /admin/platform-logs redirects or 401."""
        with app.test_client() as client:
            resp = client.get('/admin/platform-logs')
            assert resp.status_code in (302, 401)

    def test_page_company_admin_forbidden(self):
        """company_admin must receive 403 on /admin/platform-logs."""
        user = make_company_admin()
        with patch('app.validate_session', return_value=user):
            with app.test_client() as client:
                client.set_cookie('session_id', 'fake-session')
                resp = client.get('/admin/platform-logs')
                assert resp.status_code == 403

    def test_page_super_admin_ok(self):
        """super_admin receives 200 on /admin/platform-logs."""
        user = make_super_admin()
        with patch('app.validate_session', return_value=user):
            with app.test_client() as client:
                client.set_cookie('session_id', 'fake-session')
                resp = client.get('/admin/platform-logs')
                assert resp.status_code == 200
                assert b'Platform Activity Logs' in resp.data

    def test_base_nav_has_platform_logs_link(self):
        """base.html must contain the Platform Logs nav link."""
        template_path = Path(__file__).resolve().parents[1] / 'templates' / 'base.html'
        content = template_path.read_text(encoding='utf-8')
        assert '/admin/platform-logs' in content, \
            "base.html must have a /admin/platform-logs nav link"
        assert 'Platform Logs' in content, \
            "base.html must label the nav link 'Platform Logs'"


# ══════════════════════════════════════════════════════════════
#  API auth guard
# ══════════════════════════════════════════════════════════════

class TestPlatformLogsApiAuth:

    def test_api_unauthenticated_returns_401(self):
        """Unauthenticated request to /api/admin/platform-logs returns 401."""
        with app.test_client() as client:
            resp = client.get('/api/admin/platform-logs')
            assert resp.status_code == 401

    def test_api_company_admin_forbidden(self):
        """company_admin must receive 403 from /api/admin/platform-logs."""
        user = make_company_admin()
        with patch('app.validate_session', return_value=user):
            with app.test_client() as client:
                client.set_cookie('session_id', 'fake-session')
                resp = client.get('/api/admin/platform-logs')
                assert resp.status_code == 403

    def test_api_stats_unauthenticated_returns_401(self):
        """Unauthenticated request to /api/admin/platform-logs/stats returns 401."""
        with app.test_client() as client:
            resp = client.get('/api/admin/platform-logs/stats')
            assert resp.status_code == 401

    def test_api_stats_company_admin_forbidden(self):
        """company_admin must receive 403 from /api/admin/platform-logs/stats."""
        user = make_company_admin()
        with patch('app.validate_session', return_value=user):
            with app.test_client() as client:
                client.set_cookie('session_id', 'fake-session')
                resp = client.get('/api/admin/platform-logs/stats')
                assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════
#  API data (super_admin with temp DB)
# ══════════════════════════════════════════════════════════════

class TestPlatformLogsApiData:

    def test_api_super_admin_returns_success(self):
        """super_admin gets success=True and data list from /api/admin/platform-logs."""
        user = make_super_admin()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, '99_user.db')
            seed_activity_logs(db_path, [
                {'event_type': 'login_success', 'category': 'system', 'severity': 'info',
                 'title': 'User logged in', 'user_id': 'tester@co.com'},
                {'event_type': 'file_upload', 'category': 'import', 'severity': 'info',
                 'title': 'File uploaded', 'user_id': 'tester@co.com'},
            ])
            # Patch DATA_DIR and AUTH_DB_PATH in app config temporarily
            original_data_dir = app.config.get('DATA_DIR')
            original_auth_db = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmpdir
            app.config['AUTH_DB_PATH'] = os.path.join(tmpdir, 'global_auth.db')
            try:
                with patch('app.validate_session', return_value=user):
                    with app.test_client() as client:
                        client.set_cookie('session_id', 'fake-session')
                        resp = client.get('/api/admin/platform-logs')
                        assert resp.status_code == 200
                        body = resp.get_json()
                        assert body is not None
                        assert body.get('success') is True
                        assert 'data' in body
                        assert 'total' in body
                        assert isinstance(body['data'], list)
            finally:
                app.config['DATA_DIR'] = original_data_dir
                app.config['AUTH_DB_PATH'] = original_auth_db


class TestTenantActivityLogs:

    def test_company_admin_sees_operator_and_viewer_logs_in_own_tenant(self):
        """company_admin /api/logs response includes operator/viewer activity from the same tenant DB."""
        user = make_company_admin()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, '10_user.db')
            seed_activity_logs(db_path, [
                {'event_type': 'file_upload', 'category': 'import', 'severity': 'info',
                 'title': 'Operator uploaded inventory', 'user_id': 'op@acme.com'},
                {'event_type': 'view_report', 'category': 'system', 'severity': 'info',
                 'title': 'Viewer opened report', 'user_id': 'viewer@acme.com'},
            ])
            original_data_dir = app.config.get('DATA_DIR')
            original_module_data_dir = sys.modules['app'].DATA_DIR
            app.config['DATA_DIR'] = tmpdir
            sys.modules['app'].DATA_DIR = tmpdir
            try:
                with patch('app.validate_session', return_value=user):
                    with app.test_client() as client:
                        client.set_cookie('session_id', 'fake-session')
                        resp = client.get('/api/logs')
                        assert resp.status_code == 200
                        body = resp.get_json()
                        assert body['success'] is True
                        titles = [r['title'] for r in body['data']]
                        assert 'Operator uploaded inventory' in titles
                        assert 'Viewer opened report' in titles
            finally:
                app.config['DATA_DIR'] = original_data_dir
                sys.modules['app'].DATA_DIR = original_module_data_dir

    def test_operator_cannot_read_tenant_activity_logs(self):
        """operator role must not read company-wide tenant activity logs."""
        user = make_operator()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, '10_user.db')
            seed_activity_logs(db_path, [
                {'event_type': 'file_upload', 'category': 'import', 'severity': 'info',
                 'title': 'Operator uploaded inventory', 'user_id': 'op@acme.com'},
            ])
            original_data_dir = app.config.get('DATA_DIR')
            original_module_data_dir = sys.modules['app'].DATA_DIR
            app.config['DATA_DIR'] = tmpdir
            sys.modules['app'].DATA_DIR = tmpdir
            try:
                with patch('app.validate_session', return_value=user):
                    with app.test_client() as client:
                        client.set_cookie('session_id', 'fake-session')
                        resp = client.get('/api/logs')
                        assert resp.status_code == 403
            finally:
                app.config['DATA_DIR'] = original_data_dir
                sys.modules['app'].DATA_DIR = original_module_data_dir

    def test_api_super_admin_sees_seeded_logs(self):
        """super_admin log response includes events from seeded tenant DB."""
        user = make_super_admin()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, '7_user.db')
            seed_activity_logs(db_path, [
                {'event_type': 'warehouse_move', 'category': 'warehouse', 'severity': 'info',
                 'title': 'Chemical moved to zone A', 'user_id': 'op@acme.com'},
                {'event_type': 'login_failure', 'category': 'system', 'severity': 'warning',
                 'title': 'Failed login attempt', 'user_id': 'bad@user.com'},
            ])
            original_data_dir = app.config.get('DATA_DIR')
            original_auth_db = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmpdir
            app.config['AUTH_DB_PATH'] = os.path.join(tmpdir, 'global_auth.db')
            try:
                with patch('app.validate_session', return_value=user):
                    with app.test_client() as client:
                        client.set_cookie('session_id', 'fake-session')
                        resp = client.get('/api/admin/platform-logs')
                        assert resp.status_code == 200
                        body = resp.get_json()
                        assert body['total'] == 2
                        titles = [r['title'] for r in body['data']]
                        assert 'Chemical moved to zone A' in titles
                        assert 'Failed login attempt' in titles
            finally:
                app.config['DATA_DIR'] = original_data_dir
                app.config['AUTH_DB_PATH'] = original_auth_db

    def test_api_stats_returns_structure(self):
        """Stats endpoint returns total, by_severity, by_category, per_company."""
        user = make_super_admin()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, '5_user.db')
            seed_activity_logs(db_path, [
                {'event_type': 'w', 'category': 'warehouse', 'severity': 'info',
                 'title': 'T1', 'user_id': 'op@co.com'},
                {'event_type': 'l', 'category': 'system', 'severity': 'warning',
                 'title': 'T2', 'user_id': 'bad@co.com'},
            ])
            original_data_dir = app.config.get('DATA_DIR')
            original_auth_db = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmpdir
            app.config['AUTH_DB_PATH'] = os.path.join(tmpdir, 'global_auth.db')
            try:
                with patch('app.validate_session', return_value=user):
                    with app.test_client() as client:
                        client.set_cookie('session_id', 'fake-session')
                        resp = client.get('/api/admin/platform-logs/stats')
                        assert resp.status_code == 200
                        body = resp.get_json()
                        assert body.get('success') is True
                        stats = body.get('data', {})
                        assert 'total' in stats
                        assert 'by_severity' in stats
                        assert 'by_category' in stats
                        assert 'per_company' in stats
                        assert stats['total'] == 2
            finally:
                app.config['DATA_DIR'] = original_data_dir
                app.config['AUTH_DB_PATH'] = original_auth_db

    def test_api_stats_counts_errors(self):
        """Stats errors_warnings count reflects warning/error/critical rows."""
        user = make_super_admin()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = os.path.join(tmpdir, '3_user.db')
            seed_activity_logs(db_path, [
                {'event_type': 'ok', 'category': 'system', 'severity': 'info', 'title': 'Fine'},
                {'event_type': 'bad', 'category': 'system', 'severity': 'error', 'title': 'Error'},
                {'event_type': 'warn', 'category': 'system', 'severity': 'warning', 'title': 'Warn'},
            ])
            original_data_dir = app.config.get('DATA_DIR')
            original_auth_db = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmpdir
            app.config['AUTH_DB_PATH'] = os.path.join(tmpdir, 'global_auth.db')
            try:
                with patch('app.validate_session', return_value=user):
                    with app.test_client() as client:
                        client.set_cookie('session_id', 'fake-session')
                        resp = client.get('/api/admin/platform-logs/stats')
                        assert resp.status_code == 200
                        body = resp.get_json()
                        stats = body['data']
                        assert stats['errors_warnings'] == 2
                        assert stats['total'] == 3
            finally:
                app.config['DATA_DIR'] = original_data_dir
                app.config['AUTH_DB_PATH'] = original_auth_db


# ══════════════════════════════════════════════════════════════
#  Helper unit tests (no HTTP)
# ══════════════════════════════════════════════════════════════

class TestHelperFunctions:

    def test_helper_scans_tenant_dbs(self):
        """_get_all_tenant_db_paths finds numbered *_user.db files."""
        from routes.admin import _get_all_tenant_db_paths
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ['1_user.db', '2_user.db', '10_user.db', 'user.db', 'chemicals.db']:
                open(os.path.join(tmpdir, name), 'w').close()
            results = _get_all_tenant_db_paths(tmpdir)
            company_ids = {cid for cid, _ in results}
            assert 1 in company_ids
            assert 2 in company_ids
            assert 10 in company_ids
            assert 0 in company_ids   # user.db → company_id=0
            # chemicals.db must NOT appear
            assert 'chemicals.db' not in [Path(p).name for _, p in results]

    def test_helper_company_filter(self):
        """_get_all_tenant_db_paths respects company_filter."""
        from routes.admin import _get_all_tenant_db_paths
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ['1_user.db', '2_user.db', '3_user.db']:
                open(os.path.join(tmpdir, name), 'w').close()
            results = _get_all_tenant_db_paths(tmpdir, company_filter=2)
            assert len(results) == 1
            assert results[0][0] == 2

    def test_route_structure_has_super_admin_only(self):
        """admin.py must have @super_admin_only on all 3 new platform-log routes."""
        source = (Path(__file__).resolve().parents[1] / 'routes' / 'admin.py').read_text(encoding='utf-8')
        assert source.count('@super_admin_only') >= 5, \
            "admin.py should have at least 5 @super_admin_only usages (2 existing + 3 new routes)"

    def test_template_exists(self):
        """admin_platform_logs.html template must exist."""
        t = Path(__file__).resolve().parents[1] / 'templates' / 'admin_platform_logs.html'
        assert t.exists(), "admin_platform_logs.html template must exist"

    def test_template_has_key_elements(self):
        """Template must contain stats cards, filter bar, table, and export."""
        content = (Path(__file__).resolve().parents[1] / 'templates' / 'admin_platform_logs.html').read_text(encoding='utf-8')
        assert 'platformLogsApp' in content
        assert 'filterCompany' in content
        assert 'filterSeverity' in content
        assert '/api/admin/platform-logs' in content
        # exportExcel replaced the old exportCsv
        assert 'exportExcel' in content


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
