"""
auth.py — Authentication Routes Blueprint.

Endpoints:
  GET  /auth/login         → Login page
  POST /auth/login         → Authenticate user
  GET  /auth/register      → Registration page
  POST /auth/register      → Create new user (PENDING)
  GET  /auth/logout        → Invalidate session
  GET  /api/auth/me        → Current user info (JSON)
  GET  /api/auth/csrf      → Get CSRF token

Security:
  - Rate limited: 5 login attempts per minute per IP
  - Account lockout after 5 failures (15 min)
  - Bcrypt password hashing (cost=12)
  - HttpOnly/Secure/SameSite session cookies
  - CSRF tokens on all forms
"""

import os
import sqlite3
import logging
from flask import (
    Blueprint, request, render_template, redirect,
    url_for, jsonify, make_response, g
)

from auth.security import (
    hash_password, verify_password, validate_password_complexity,
    check_brute_force, record_login_attempt,
    create_session, validate_session, invalidate_session,
    generate_csrf_token
)
from auth.decorators import csrf_protect
from auth.models import get_auth_db_connection
from activity_logger import log_event

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth_bp', __name__)

_is_prod = (
    os.environ.get('FLASK_ENV') == 'production'
    or os.environ.get('COOKIE_SECURE') == '1'
)

def _get_db_path(company_id):
    from flask import current_app
    import os
    return os.path.join(current_app.config['DATA_DIR'], f"{company_id}_user.db")


# ═══════════════════════════════════════════════════════
#  Login
# ═══════════════════════════════════════════════════════

@auth_bp.route('/auth/login', methods=['GET'])
def login_page():
    """Render login page. Redirect to dashboard if already logged in."""
    session_id = request.cookies.get('session_id')
    if session_id:
        from flask import current_app
        user = validate_session(session_id, current_app.config['AUTH_DB_PATH'])
        if user:
            return redirect('/dashboard')
    return render_template('login.html')


@auth_bp.route('/auth/login', methods=['POST'])
def login():
    """
    Authenticate user and create session.

    Accepts JSON: { "email": "...", "password": "..." }
    Or form data.

    Returns:
    - 200 + session cookie on success
    - 401 on invalid credentials
    - 423 on account locked
    - 403 on account pending/suspended
    """
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']

    # Parse input
    if request.is_json:
        data = request.get_json()
        email = (data.get('email') or '').strip().lower()
        password = data.get('password') or ''
    else:
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''

    ip = request.remote_addr or '0.0.0.0'

    # Validate input
    if not email or not password:
        return jsonify({
            'success': False,
            'error': 'Email and password are required'
        }), 400

    # ── Brute-force check ──
    is_blocked, seconds_remaining = check_brute_force(email, ip, auth_db)
    if is_blocked:
        minutes = seconds_remaining // 60 + 1
        return jsonify({
            'success': False,
            'error': f'Account temporarily locked. Try again in {minutes} minute(s).',
            'code': 'ACCOUNT_LOCKED',
            'retry_after': seconds_remaining
        }), 423

    # ── Look up user ──
    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
        (email,)
    )
    user = cursor.fetchone()
    conn.close()

    if not user:
        record_login_attempt(email, ip, False, auth_db)
        log_event(
            db_path=current_app.config['USER_DB_PATH'],
            event_type='login_failure',
            category='system',
            severity='warning',
            title='Failed login attempt (Unknown user)',
            detail=f"Failed login attempt for email {email} from IP {ip}",
            user_id=None,
        )
        return jsonify({
            'success': False,
            'error': 'Invalid email or password',
            'code': 'INVALID_CREDENTIALS'
        }), 401

    # ── Verify password FIRST (fix P1-9 status leak) ──
    if not verify_password(password, user['password_hash']):
        record_login_attempt(email, ip, False, auth_db)
        log_event(
            db_path=_get_db_path(user['company_id']),
            event_type='login_failure',
            category='system',
            severity='warning',
            title='Failed login attempt (Invalid credentials)',
            detail=f"Failed login attempt for user {user['email']} from IP {ip}",
            user_id=user['id'],
        )
        return jsonify({
            'success': False,
            'error': 'Invalid email or password',
            'code': 'INVALID_CREDENTIALS'
        }), 401

    # ── Check account status AFTER password verified ──
    if user['status'] == 'PENDING':
        return jsonify({
            'success': False,
            'error': 'Your account is pending approval by your company administrator.',
            'code': 'ACCOUNT_PENDING'
        }), 403

    if user['status'] == 'SUSPENDED':
        return jsonify({
            'success': False,
            'error': 'Your account has been suspended. Contact your administrator.',
            'code': 'ACCOUNT_SUSPENDED'
        }), 403

    # ── Success! Create session ──
    record_login_attempt(email, ip, True, auth_db)
    user_agent = request.headers.get('User-Agent', '')[:500]

    session_id = create_session(
        user_id=user['id'],
        ip_address=ip,
        user_agent=user_agent,
        auth_db_path=auth_db
    )

    csrf_token = generate_csrf_token()

    # If force_password_change is active, redirect to change password page instead of dashboard
    next_redirect = '/auth/change-password' if user['force_password_change'] else '/dashboard'

    response = make_response(jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'email': user['email'],
            'full_name': user['full_name'],
            'role': user['role'],
            'force_password_change': bool(user['force_password_change']),
        },
        'redirect': next_redirect
    }))

    # Set session cookie (HttpOnly, SameSite=Lax)
    response.set_cookie(
        'session_id',
        session_id,
        httponly=True,
        samesite='Lax',
        max_age=30 * 60,  # 30 minutes
        path='/',
        secure=_is_prod,
    )

    # Set CSRF cookie (accessible by JavaScript for header submission)
    response.set_cookie(
        'csrf_token',
        csrf_token,
        httponly=False,  # JS needs to read this
        samesite='Lax',
        max_age=30 * 60,
        path='/',
        secure=_is_prod,
    )

    logger.info(f"✅ Login successful: {email} (role={user['role']}, IP={ip})")
    log_event(
        db_path=_get_db_path(user['company_id']),
        event_type='login_success',
        category='system',
        severity='info',
        title='User logged in successfully',
        detail=f"User {user['email']} logged in from IP {ip}",
        user_id=user['id'],
    )
    return response


# ═══════════════════════════════════════════════════════
#  Forced Password Change Endpoints
# ═══════════════════════════════════════════════════════

@auth_bp.route('/auth/change-password', methods=['GET'])
def change_password_page():
    """Render a simple, secure page to change password on first login."""
    session_id = request.cookies.get('session_id')
    if not session_id:
        return redirect('/auth/login')
        
    from flask import current_app
    user = validate_session(session_id, current_app.config['AUTH_DB_PATH'])
    if not user:
        return redirect('/auth/login')
        
    return '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Change Password - SAFEWARE</title>
        <style>
            body { font-family: sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .card { background: #1e293b; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); width: 320px; }
            h2 { margin-top: 0; text-align: center; }
            input { width: 100%; padding: 0.5rem; margin: 0.5rem 0; border: 1px solid #475569; background: #0f172a; color: white; border-radius: 4px; box-sizing: border-box; }
            button { width: 100%; padding: 0.75rem; background: #2563eb; border: none; color: white; font-weight: bold; border-radius: 4px; margin-top: 1rem; cursor: pointer; }
            button:hover { background: #1d4ed8; }
            .error { color: #f87171; font-size: 0.875rem; margin-top: 0.5rem; text-align: center; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Change Password</h2>
            <p style="font-size: 0.875rem; color: #94a3b8; text-align: center;">You are using a default password and must change it to continue.</p>
            <form id="changeForm">
                <input type="password" id="old_password" placeholder="Current Password" required>
                <input type="password" id="new_password" placeholder="New Password" required>
                <button type="submit">Update Password</button>
                <div class="error" id="msg"></div>
            </form>
        </div>
        <script>
            function getCookie(name) {
                let matches = document.cookie.match(new RegExp(
                    "(?:^|; )" + name.replace(/([\.$?*|{}\(\)\[\]\\\/\+^])/g, '\\\\$1') + "=([^;]*)"
                ));
                return matches ? decodeURIComponent(matches[1]) : undefined;
            }
            
            document.getElementById('changeForm').onsubmit = async (e) => {
                e.preventDefault();
                const old_password = document.getElementById('old_password').value;
                const new_password = document.getElementById('new_password').value;
                const token = getCookie('csrf_token');
                
                const res = await fetch('/api/auth/change-password', {
                    method: 'POST',
                    headers: { 
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': token
                    },
                    body: JSON.stringify({ old_password, new_password })
                });
                const data = await res.json();
                if (res.ok) {
                    alert('Password changed successfully. Please log in again.');
                    window.location.href = '/auth/logout';
                } else {
                    document.getElementById('msg').innerText = data.error || 'An error occurred';
                }
            };
        </script>
    </body>
    </html>
    '''


@auth_bp.route('/api/auth/change-password', methods=['POST'])
@csrf_protect
def change_password():
    """Change password for the authenticated user, clearing the force_password_change flag."""
    if not hasattr(g, 'user') or g.user is None:
        return jsonify({'error': 'Authentication required'}), 401
        
    data = request.get_json() or {}
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    
    if not old_password or not new_password:
        return jsonify({'error': 'Old and new passwords are required'}), 400
        
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    
    # Fetch latest user details (including password_hash)
    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE id = ?", (g.user['id'],))
    row = cursor.fetchone()
    
    if not row or not verify_password(old_password, row['password_hash']):
        conn.close()
        return jsonify({'error': 'Invalid old password'}), 400
        
    # Validate new password complexity
    pwd_valid, pwd_errors = validate_password_complexity(new_password)
    if not pwd_valid:
        conn.close()
        return jsonify({'error': '; '.join(pwd_errors)}), 400
        
    # Hash and save new password
    new_hash = hash_password(new_password)
    cursor.execute(
        "UPDATE users SET password_hash = ?, force_password_change = 0, password_changed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (new_hash, g.user['id'])
    )
    conn.commit()
    conn.close()
    
    logger.info(f"🔑 Password successfully changed for user ID: {g.user['id']}")
    log_event(
        db_path=_get_db_path(g.user['company_id']),
        event_type='password_change',
        category='system',
        severity='info',
        title='User changed password',
        detail=f"User {g.user['email']} changed password",
        user_id=g.user['id'],
    )
    return jsonify({'success': True, 'message': 'Password changed successfully.'})


# ═══════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════

@auth_bp.route('/auth/register', methods=['GET'])
def register_page():
    """Registration is disabled. Only admins can create users."""
    flash("Public registration is disabled. Please contact your administrator.", "error")
    return redirect(url_for('auth_bp.login_page'))


@auth_bp.route('/auth/register', methods=['POST'])
def register():
    """Registration is disabled. Only admins can create users."""
    return jsonify({
        'success': False,
        'errors': ['Public registration is disabled. Please contact your administrator to receive an invite.']
    }), 403



# ═══════════════════════════════════════════════════════
#  Logout
# ═══════════════════════════════════════════════════════

@auth_bp.route('/auth/logout', methods=['GET', 'POST'])
def logout():
    """Invalidate session and clear cookies."""
    from flask import current_app
    session_id = request.cookies.get('session_id')

    if session_id:
        invalidate_session(session_id, current_app.config['AUTH_DB_PATH'])

    if hasattr(g, 'user') and g.user:
        log_event(
            db_path=_get_db_path(g.user['company_id']),
            event_type='logout',
            category='system',
            severity='info',
            title='User logged out',
            detail=f"User {g.user['email']} logged out",
            user_id=g.user['id'],
        )

    response = make_response(redirect('/auth/login'))
    response.delete_cookie('session_id', path='/', secure=_is_prod)
    response.delete_cookie('csrf_token', path='/', secure=_is_prod)

    logger.info(f"👋 Logout: session invalidated")
    return response


# ═══════════════════════════════════════════════════════
#  API: Current User Info
# ═══════════════════════════════════════════════════════

@auth_bp.route('/api/auth/me', methods=['GET'])
def current_user():
    """Return current authenticated user's info."""
    if not hasattr(g, 'user') or g.user is None:
        return jsonify({'authenticated': False}), 401

    return jsonify({
        'authenticated': True,
        'user': g.user
    })


@auth_bp.route('/api/auth/csrf', methods=['GET'])
def get_csrf():
    """Get a CSRF token for forms/API calls."""
    token = generate_csrf_token()
    response = make_response(jsonify({'csrf_token': token}))
    response.set_cookie(
        'csrf_token', token,
        httponly=False, samesite='Lax', max_age=30*60, path='/',
        secure=_is_prod,
    )
    return response


@auth_bp.route('/api/auth/companies', methods=['GET'])
def list_companies():
    """List available companies for registration dropdown."""
    from flask import current_app
    conn = get_auth_db_connection(current_app.config['AUTH_DB_PATH'])
    cursor = conn.cursor()

    # Don't show SAFEWARE Platform company (super admin only)
    cursor.execute("""
        SELECT id, name FROM companies
        WHERE license_status = 'active' AND name != 'SAFEWARE Platform'
        ORDER BY name
    """)
    companies = [{'id': row['id'], 'name': row['name']} for row in cursor.fetchall()]
    conn.close()

    return jsonify(companies)
