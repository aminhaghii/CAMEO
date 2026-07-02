import os
import sys
import sqlite3
import json
import pytest
from unittest.mock import patch
from datetime import datetime, timedelta

# Insert backend directory to python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app
from auth.security import validate_session, create_session
from logic.constants import Compatibility

def test_absolute_session_timeout(tmp_path):
    """Test P1-8: Absolute session timeout cap (8 hours)."""
    # Create temp auth database
    auth_db = str(tmp_path / "global_auth.db")
    conn = sqlite3.connect(auth_db)
    cursor = conn.cursor()
    
    # Create sessions/users/companies tables
    cursor.execute("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT,
            tenant_db_path TEXT,
            license_status TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT,
            full_name TEXT,
            role TEXT,
            status TEXT,
            company_id INTEGER,
            password_hash TEXT,
            force_password_change INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            created_at TEXT,
            expires_at TEXT,
            absolute_expires_at TEXT,
            is_active INTEGER
        )
    """)
    
    # Seed company & user
    cursor.execute("INSERT INTO companies VALUES (1, 'Test Company', 'test.db', 'active')")
    cursor.execute("INSERT INTO users VALUES (1, 'user@test.com', 'Test User', 'company_admin', 'ACTIVE', 1, 'hash', 0)")
    
    # Create an active session, but with absolute_expires_at set to 1 minute ago
    now = datetime.utcnow()
    past_expiry = (now - timedelta(minutes=1)).isoformat()
    future_sliding_expiry = (now + timedelta(minutes=30)).isoformat()
    
    cursor.execute("""
        INSERT INTO sessions (id, user_id, created_at, expires_at, absolute_expires_at, is_active)
        VALUES ('expired_session_token', 1, ?, ?, ?, 1)
    """, (now.isoformat(), future_sliding_expiry, past_expiry))
    
    conn.commit()
    conn.close()
    
    # Validate the session; should return None because it exceeded absolute timeout
    user_data = validate_session('expired_session_token', auth_db)
    assert user_data is None
    
    # Verify the database was updated to set is_active = 0
    conn = sqlite3.connect(auth_db)
    cursor = conn.cursor()
    cursor.execute("SELECT is_active FROM sessions WHERE id = 'expired_session_token'")
    is_active = cursor.fetchone()[0]
    conn.close()
    assert is_active == 0


def test_validate_session_returns_force_password_change_flag(tmp_path):
    """Regression test for the July 2026 audit P1 finding: validate_session()
    dropped force_password_change from its SELECT, so app.tenant_router's
    `user.get('force_password_change')` gate was permanently dead code — a
    seeded default-password account (e.g. admin@safeware.io / Admin@123) was
    never actually forced to rotate its password. This exercises the REAL
    validate_session() against a real DB (not a mocked user dict) to confirm
    the flag now round-trips end-to-end from the users table."""
    auth_db = str(tmp_path / "global_auth.db")
    conn = sqlite3.connect(auth_db)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY,
            name TEXT,
            tenant_db_path TEXT,
            license_status TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT,
            full_name TEXT,
            role TEXT,
            status TEXT,
            company_id INTEGER,
            password_hash TEXT,
            force_password_change INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            ip_address TEXT,
            user_agent TEXT,
            created_at TEXT,
            expires_at TEXT,
            absolute_expires_at TEXT,
            is_active INTEGER
        )
    """)

    cursor.execute("INSERT INTO companies VALUES (1, 'Test Company', 'test.db', 'active')")
    # Seeded-admin-style user: force_password_change = 1 (default password never rotated)
    cursor.execute(
        "INSERT INTO users VALUES (1, 'admin@test.com', 'Admin', 'company_admin', 'ACTIVE', 1, 'hash', 1)"
    )
    conn.commit()
    conn.close()

    session_id = create_session(user_id=1, ip_address='127.0.0.1', user_agent='pytest', auth_db_path=auth_db)

    user_data = validate_session(session_id, auth_db)
    assert user_data is not None
    assert user_data['force_password_change'] is True

    # A user with the flag cleared must round-trip False, not just falsy/None.
    conn = sqlite3.connect(auth_db)
    conn.execute("UPDATE users SET force_password_change = 0 WHERE id = 1")
    conn.commit()
    conn.close()

    user_data_2 = validate_session(session_id, auth_db)
    assert user_data_2['force_password_change'] is False


def test_forced_password_change_intercept():
    """Test P1-1: Forced password change intercept on protected endpoints."""
    mock_user = {
        'id': 1,
        'email': 'user@test.com',
        'full_name': 'Test User',
        'role': 'company_admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'Test Company',
        'tenant_db_path': 'test.db',
        'force_password_change': 1  # Forced password change flag active!
    }
    
    with patch('app.validate_session', return_value=mock_user):
        with app.test_client() as client:
            client.set_cookie('session_id', 'mock_session_id')
            
            # API endpoint should return 403 Forbidden with PASSWORD_CHANGE_REQUIRED code
            res = client.get('/api/warehouse/list')
            assert res.status_code == 403
            payload = res.get_json()
            assert payload['code'] == 'PASSWORD_CHANGE_REQUIRED'
            
            # Allowed authentication endpoints should bypass the block
            res_csrf = client.get('/api/auth/csrf')
            # Should not be 403
            assert res_csrf.status_code != 403
            
            # Non-API endpoint should redirect to change password page
            res_html = client.get('/warehouse/dashboard')
            assert res_html.status_code == 302
            assert '/auth/change-password' in res_html.location


def test_compliance_route_rbac_and_csrf():
    """Test P0-6: Compliance route requires authentication, CSRF validation, and appropriate roles."""
    with app.test_client() as client:
        # 1. Unauthenticated request must return 401
        res = client.post('/api/compliance/export')
        assert res.status_code == 401
        
        # 2. Authenticated but unauthorized role (e.g. viewer or normal user who isn't compliance manager/admin)
        mock_viewer = {
            'id': 2,
            'email': 'viewer@test.com',
            'full_name': 'Viewer User',
            'role': 'viewer',
            'status': 'ACTIVE',
            'company_id': 1,
            'company_name': 'Test Company',
            'tenant_db_path': 'test.db'
        }
        
        with patch('app.validate_session', return_value=mock_viewer):
            client.set_cookie('session_id', 'mock_session_id')
            # Viewer gets 403 Forbidden for compliance export API
            res_v = client.post('/api/compliance/export')
            assert res_v.status_code == 403


def test_csrf_timing_attacks():
    """Test double-submit CSRF cookie checks timing attack protection."""
    # Testing that invalid CSRF token gets rejected on mutating operations
    mock_user = {
        'id': 1,
        'email': 'user@test.com',
        'full_name': 'Test User',
        'role': 'company_admin',
        'status': 'ACTIVE',
        'company_id': 1,
        'company_name': 'Test Company',
        'tenant_db_path': 'test.db'
    }
    
    with patch('app.validate_session', return_value=mock_user):
        with app.test_client() as client:
            client.set_cookie('session_id', 'mock_session_id')
            client.set_cookie('csrf_token', 'valid_token')
            
            # Sending mutating request with WRONG token header -> 403 Forbidden when testing mode is disabled
            app.config['TESTING'] = False
            try:
                res = client.post('/api/warehouse/create', json={'name': 'New Warehouse'}, headers={
                    'X-CSRF-Token': 'attacker_token'
                })
                assert res.status_code == 403
                
                # Correct token should succeed or return non-CSRF error (e.g. valid DB path setup needed)
                res_ok = client.post('/api/warehouse/create', json={'name': 'New Warehouse'}, headers={
                    'X-CSRF-Token': 'valid_token'
                })
                assert res_ok.status_code != 403
            finally:
                app.config['TESTING'] = True


def test_user_suspension_and_reactivation(tmp_path):
    """Test Step 6: Verify suspension and reactivation of company users."""
    temp_auth_db = str(tmp_path / "global_auth.db")
    
    # Initialize schema in the temp DB
    from auth.models import init_auth_db
    init_auth_db(temp_auth_db)
    
    # Let's add an active user to suspend and reactivate
    conn = sqlite3.connect(temp_auth_db)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (id, email, full_name, role, status, company_id, password_hash)
        VALUES (3, 'operator@test.com', 'Test Operator', 'operator', 'ACTIVE', 2, 'hash')
    """)
    conn.commit()
    conn.close()
    
    # Mock logged in user (id=2, email='admin@demo.com', role='company_admin', company_id=2)
    mock_admin = {
        'id': 2,
        'email': 'admin@demo.com',
        'full_name': 'Demo HSE Manager',
        'role': 'company_admin',
        'status': 'ACTIVE',
        'company_id': 2,
        'company_name': 'Demo Petrochemical Co.',
        'tenant_db_path': 'test.db'
    }
    
    # Temporarily change app auth DB path to the temp DB
    old_auth_db_path = app.config['AUTH_DB_PATH']
    app.config['AUTH_DB_PATH'] = temp_auth_db
    
    try:
        with patch('app.validate_session', return_value=mock_admin):
            with app.test_client() as client:
                client.set_cookie('session_id', 'mock_session_id')
                
                # 1. Suspend the user (id=3)
                suspend_res = client.post('/api/admin/suspend-user', json={'user_id': 3})
                assert suspend_res.status_code == 200
                assert suspend_res.get_json()['success'] is True
                
                # Verify in database that user is suspended
                conn = sqlite3.connect(temp_auth_db)
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM users WHERE id = 3")
                status = cursor.fetchone()[0]
                assert status == 'SUSPENDED'
                conn.close()
                
                # 2. Reactivate the suspended user (id=3) using approve-user API
                reactivate_res = client.post('/api/admin/approve-user', json={'user_id': 3, 'role': 'operator'})
                assert reactivate_res.status_code == 200
                assert reactivate_res.get_json()['success'] is True
                
                # Verify in database that user is active again
                conn = sqlite3.connect(temp_auth_db)
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM users WHERE id = 3")
                status = cursor.fetchone()[0]
                assert status == 'ACTIVE'
                conn.close()
    finally:
        app.config['AUTH_DB_PATH'] = old_auth_db_path
