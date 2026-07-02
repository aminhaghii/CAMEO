"""
Tests for routes added in the latest session:
  - /settings, /help pages
  - PUT  /api/admin/users/<id>        (edit user profile)
  - POST /api/admin/users/<id>/reset-password
  - GET  /api/admin/platform-logs/export (Excel download)
"""

import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import app


# ── fixtures ──────────────────────────────────────────────────────

def _super():
    return {
        'id': 1, 'email': 'admin@safeware.io', 'role': 'super_admin',
        'full_name': 'Super Admin', 'status': 'ACTIVE',
        'company_id': 1, 'company_name': 'SAFEWARE Platform',
        'tenant_db_path': None, 'force_password_change': 0,
    }

def _cadmin():
    return {
        'id': 2, 'email': 'cadmin@acme.com', 'role': 'company_admin',
        'full_name': 'Co Admin', 'status': 'ACTIVE',
        'company_id': 10, 'company_name': 'ACME Corp',
        'tenant_db_path': '/tmp/fake_10_user.db', 'force_password_change': 0,
    }

def _make_auth_db(path):
    """Minimal global_auth.db with one company and two users."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY,
            name TEXT, license_status TEXT DEFAULT 'active',
            max_users INTEGER DEFAULT 50,
            contact_email TEXT, phone TEXT, address TEXT,
            registration_number TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE COLLATE NOCASE,
            full_name TEXT, password_hash TEXT,
            company_id INTEGER, role TEXT DEFAULT 'viewer',
            status TEXT DEFAULT 'ACTIVE',
            last_login DATETIME, failed_attempts INTEGER DEFAULT 0,
            locked_until DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            password_changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            force_password_change BOOLEAN DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, user_id INTEGER,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active DATETIME DEFAULT CURRENT_TIMESTAMP,
            absolute_expires_at DATETIME,
            ip_address TEXT, user_agent TEXT
        );
        INSERT OR IGNORE INTO companies (id, name) VALUES (10, 'ACME Corp');
        INSERT OR IGNORE INTO users
            (id, email, full_name, password_hash, company_id, role, status)
        VALUES
            (2, 'cadmin@acme.com', 'Co Admin', 'x', 10, 'company_admin', 'ACTIVE'),
            (3, 'op@acme.com',     'Operator', 'x', 10, 'operator',     'ACTIVE'),
            (4, 'pending@acme.com','Pending',  'x', 10, 'viewer',       'PENDING');
    """)
    conn.commit(); conn.close()


def _make_auth_db_with_password(path):
    """Minimal auth DB seeded with bcrypt hashes for user-create tests."""
    from auth.security import hash_password

    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY,
            name TEXT, license_status TEXT DEFAULT 'active',
            max_users INTEGER DEFAULT 50,
            contact_email TEXT, phone TEXT, address TEXT,
            registration_number TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE COLLATE NOCASE,
            full_name TEXT, password_hash TEXT,
            company_id INTEGER, role TEXT DEFAULT 'viewer',
            status TEXT DEFAULT 'ACTIVE',
            last_login DATETIME, failed_attempts INTEGER DEFAULT 0,
            locked_until DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            password_changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            force_password_change BOOLEAN DEFAULT 0
        );
        INSERT OR IGNORE INTO companies (id, name, max_users) VALUES (10, 'ACME Corp', 50);
    """)
    conn.execute(
        """
        INSERT OR IGNORE INTO users
            (id, email, full_name, password_hash, company_id, role, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (2, 'cadmin@acme.com', 'Co Admin', hash_password('Admin@123'), 10, 'company_admin', 'ACTIVE')
    )
    conn.commit(); conn.close()

def _make_tenant_db(path):
    """Tenant DB with activity_logs table."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT, category TEXT DEFAULT 'system',
            severity TEXT DEFAULT 'info', title TEXT,
            detail TEXT, user_id TEXT, entity_type TEXT,
            entity_id TEXT, entity_name TEXT, meta TEXT,
            ip_address TEXT, session_id TEXT, duration_ms INTEGER,
            created_at DATETIME DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        )
    """)
    conn.execute("INSERT INTO activity_logs (event_type, title) VALUES ('login','Login')")
    conn.commit(); conn.close()


# ═══════════════════════════════════════════════════════
#  /settings  /help
# ═══════════════════════════════════════════════════════

class TestSettingsHelp:

    def test_settings_unauth_redirects(self):
        with app.test_client() as c:
            r = c.get('/settings')
            assert r.status_code in (302, 401)

    def test_settings_authenticated_200(self):
        with patch('app.validate_session', return_value=_cadmin()):
            with app.test_client() as c:
                c.set_cookie('session_id', 'x')
                r = c.get('/settings')
                assert r.status_code == 200
                assert b'Settings' in r.data

    def test_help_unauth_redirects(self):
        with app.test_client() as c:
            r = c.get('/help')
            assert r.status_code in (302, 401)

    def test_help_authenticated_200(self):
        with patch('app.validate_session', return_value=_cadmin()):
            with app.test_client() as c:
                c.set_cookie('session_id', 'x')
                r = c.get('/help')
                assert r.status_code == 200
                assert b'Help' in r.data

    def test_settings_template_has_security_tab(self):
        tmpl = (Path(__file__).resolve().parents[1] / 'templates' / 'settings.html').read_text()
        assert 'security' in tmpl
        assert 'change-password' in tmpl or 'changePassword' in tmpl

    def test_help_template_has_faq(self):
        tmpl = (Path(__file__).resolve().parents[1] / 'templates' / 'help.html').read_text()
        assert 'faq' in tmpl.lower() or 'FAQ' in tmpl


# ═══════════════════════════════════════════════════════
#  PUT /api/admin/users/<id>  — edit user
# ═══════════════════════════════════════════════════════

class TestEditUser:

    def _setup(self, tmpdir):
        auth_db = os.path.join(tmpdir, 'global_auth.db')
        _make_auth_db(auth_db)
        orig = app.config.get('AUTH_DB_PATH')
        app.config['AUTH_DB_PATH'] = auth_db
        return auth_db, orig

    def test_unauth_returns_401(self):
        with app.test_client() as c:
            r = c.put('/api/admin/users/3',
                      json={'full_name': 'X'},
                      content_type='application/json')
            assert r.status_code == 401

    def test_company_admin_can_edit_own_company_user(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_cadmin()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        r = c.put('/api/admin/users/3',
                                  json={'full_name': 'New Name'},
                                  headers={'X-CSRF-Token': 'tok'},
                                  content_type='application/json')
                        assert r.status_code == 200
                        assert r.get_json()['success'] is True
            finally:
                app.config['AUTH_DB_PATH'] = orig

    def test_edit_persists_name_in_db(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            auth_db, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_cadmin()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        c.put('/api/admin/users/3',
                              json={'full_name': 'Changed Name'},
                              headers={'X-CSRF-Token': 'tok'},
                              content_type='application/json')
                conn = sqlite3.connect(auth_db)
                row = conn.execute("SELECT full_name FROM users WHERE id=3").fetchone()
                conn.close()
                assert row[0] == 'Changed Name'
            finally:
                app.config['AUTH_DB_PATH'] = orig

    def test_edit_duplicate_email_returns_409(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_cadmin()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        # Try to give user 3 the same email as user 2
                        r = c.put('/api/admin/users/3',
                                  json={'email': 'cadmin@acme.com'},
                                  headers={'X-CSRF-Token': 'tok'},
                                  content_type='application/json')
                        assert r.status_code == 409
            finally:
                app.config['AUTH_DB_PATH'] = orig

    def test_edit_nonexistent_user_returns_404(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_super()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        r = c.put('/api/admin/users/9999',
                                  json={'full_name': 'X'},
                                  headers={'X-CSRF-Token': 'tok'},
                                  content_type='application/json')
                        assert r.status_code == 404
            finally:
                app.config['AUTH_DB_PATH'] = orig


class TestCreateUserLogging:

    def _setup(self, tmpdir):
        auth_db = os.path.join(tmpdir, 'global_auth.db')
        _make_auth_db_with_password(auth_db)
        orig_auth = app.config.get('AUTH_DB_PATH')
        orig_data = app.config.get('DATA_DIR')
        app.config['AUTH_DB_PATH'] = auth_db
        app.config['DATA_DIR'] = tmpdir
        return auth_db, orig_auth, orig_data

    def test_company_admin_create_operator_writes_tenant_log(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, orig_auth, orig_data = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_cadmin()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        r = c.post(
                            '/api/admin/users/create',
                            json={
                                'email': 'newop@acme.com',
                                'full_name': 'New Operator',
                                'password': 'NewPass@123',
                                'role': 'operator',
                            },
                            headers={'X-CSRF-Token': 'tok'},
                            content_type='application/json',
                        )
                        assert r.status_code == 200
                        assert r.get_json()['success'] is True

                tenant_db = os.path.join(tmp, '10_user.db')
                conn = sqlite3.connect(tenant_db)
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT event_type, category, title, user_id, entity_name, meta FROM activity_logs"
                ).fetchone()
                conn.close()

                assert row is not None
                assert row['event_type'] == 'user_create'
                assert row['category'] == 'system'
                assert row['title'] == 'User account created'
                assert row['user_id'] == '2'
                assert row['entity_name'] == 'New Operator'
                assert 'newop@acme.com' in row['meta']
            finally:
                app.config['AUTH_DB_PATH'] = orig_auth
                app.config['DATA_DIR'] = orig_data


# ═══════════════════════════════════════════════════════
#  POST /api/admin/users/<id>/reset-password
# ═══════════════════════════════════════════════════════

class TestAdminResetPassword:

    def _setup(self, tmpdir):
        auth_db = os.path.join(tmpdir, 'global_auth.db')
        _make_auth_db(auth_db)
        orig = app.config.get('AUTH_DB_PATH')
        app.config['AUTH_DB_PATH'] = auth_db
        return auth_db, orig

    def test_unauth_returns_401(self):
        with app.test_client() as c:
            r = c.post('/api/admin/users/3/reset-password', json={'new_password': 'Test@123'})
            assert r.status_code == 401

    def test_weak_password_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_cadmin()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        r = c.post('/api/admin/users/3/reset-password',
                                   json={'new_password': '123'},
                                   headers={'X-CSRF-Token': 'tok'},
                                   content_type='application/json')
                        assert r.status_code == 400
            finally:
                app.config['AUTH_DB_PATH'] = orig

    def test_valid_reset_sets_force_change_flag(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            auth_db, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_cadmin()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        r = c.post('/api/admin/users/3/reset-password',
                                   json={'new_password': 'NewPass@123'},
                                   headers={'X-CSRF-Token': 'tok'},
                                   content_type='application/json')
                        assert r.status_code == 200
                        assert r.get_json()['success'] is True
                conn = sqlite3.connect(auth_db)
                row = conn.execute("SELECT force_password_change FROM users WHERE id=3").fetchone()
                conn.close()
                assert row[0] == 1
            finally:
                app.config['AUTH_DB_PATH'] = orig

    def test_reset_nonexistent_user_returns_404(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            _, orig = self._setup(tmp)
            try:
                with patch('app.validate_session', return_value=_super()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        c.set_cookie('csrf_token', 'tok')
                        r = c.post('/api/admin/users/9999/reset-password',
                                   json={'new_password': 'Test@123'},
                                   headers={'X-CSRF-Token': 'tok'},
                                   content_type='application/json')
                        assert r.status_code == 404
            finally:
                app.config['AUTH_DB_PATH'] = orig


# ═══════════════════════════════════════════════════════
#  GET /api/admin/platform-logs/export
# ═══════════════════════════════════════════════════════

class TestPlatformLogsExport:

    def test_unauth_returns_401(self):
        with app.test_client() as c:
            r = c.get('/api/admin/platform-logs/export')
            assert r.status_code == 401

    def test_company_admin_returns_403(self):
        with patch('app.validate_session', return_value=_cadmin()):
            with app.test_client() as c:
                c.set_cookie('session_id', 'x')
                r = c.get('/api/admin/platform-logs/export')
                assert r.status_code == 403

    def test_super_admin_returns_xlsx(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # seed one tenant DB
            db = os.path.join(tmp, '10_user.db')
            _make_tenant_db(db)
            auth_db = os.path.join(tmp, 'global_auth.db')
            _make_auth_db(auth_db)
            orig_data = app.config.get('DATA_DIR')
            orig_auth = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmp
            app.config['AUTH_DB_PATH'] = auth_db
            try:
                with patch('app.validate_session', return_value=_super()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        r = c.get('/api/admin/platform-logs/export')
                        assert r.status_code == 200
                        assert r.content_type == (
                            'application/vnd.openxmlformats-officedocument'
                            '.spreadsheetml.sheet'
                        )
                        assert len(r.data) > 1000  # valid xlsx has substance
            finally:
                app.config['DATA_DIR'] = orig_data
                app.config['AUTH_DB_PATH'] = orig_auth

    def test_export_filename_has_xlsx_extension(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            auth_db = os.path.join(tmp, 'global_auth.db')
            _make_auth_db(auth_db)
            orig_data = app.config.get('DATA_DIR')
            orig_auth = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmp
            app.config['AUTH_DB_PATH'] = auth_db
            try:
                with patch('app.validate_session', return_value=_super()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        r = c.get('/api/admin/platform-logs/export')
                        cd = r.headers.get('Content-Disposition', '')
                        assert '.xlsx' in cd
            finally:
                app.config['DATA_DIR'] = orig_data
                app.config['AUTH_DB_PATH'] = orig_auth

    def test_export_with_filters_still_200(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            auth_db = os.path.join(tmp, 'global_auth.db')
            _make_auth_db(auth_db)
            orig_data = app.config.get('DATA_DIR')
            orig_auth = app.config.get('AUTH_DB_PATH')
            app.config['DATA_DIR'] = tmp
            app.config['AUTH_DB_PATH'] = auth_db
            try:
                with patch('app.validate_session', return_value=_super()):
                    with app.test_client() as c:
                        c.set_cookie('session_id', 'x')
                        r = c.get('/api/admin/platform-logs/export?severity=error&date_range=7d')
                        assert r.status_code == 200
            finally:
                app.config['DATA_DIR'] = orig_data
                app.config['AUTH_DB_PATH'] = orig_auth


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
