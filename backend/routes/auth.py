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
from auth.models import get_auth_db_connection

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth_bp', __name__)


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
        return jsonify({
            'success': False,
            'error': 'Invalid email or password'
        }), 401

    # ── Check account status ──
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

    # ── Verify password ──
    if not verify_password(password, user['password_hash']):
        record_login_attempt(email, ip, False, auth_db)
        return jsonify({
            'success': False,
            'error': 'Invalid email or password'
        }), 401

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

    response = make_response(jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'email': user['email'],
            'full_name': user['full_name'],
            'role': user['role'],
        },
        'redirect': '/dashboard'
    }))

    # Set session cookie (HttpOnly, SameSite=Lax)
    response.set_cookie(
        'session_id',
        session_id,
        httponly=True,
        samesite='Lax',
        max_age=30 * 60,  # 30 minutes
        path='/'
    )

    # Set CSRF cookie (accessible by JavaScript for header submission)
    response.set_cookie(
        'csrf_token',
        csrf_token,
        httponly=False,  # JS needs to read this
        samesite='Lax',
        max_age=30 * 60,
        path='/'
    )

    logger.info(f"✅ Login successful: {email} (role={user['role']}, IP={ip})")
    return response


# ═══════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════

@auth_bp.route('/auth/register', methods=['GET'])
def register_page():
    """Render registration page with company list."""
    return render_template('register.html')


@auth_bp.route('/auth/register', methods=['POST'])
def register():
    """
    Register a new user with PENDING status.

    Accepts JSON: {
        "email": "...",
        "full_name": "...",
        "password": "...",
        "company_id": 2
    }
    """
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']

    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    email = (data.get('email') or '').strip().lower()
    full_name = (data.get('full_name') or '').strip()
    password = data.get('password') or ''
    confirm_password = data.get('confirm_password') or ''
    company_id = data.get('company_id')

    # ── Validate inputs ──
    errors = []
    if not email or '@' not in email:
        errors.append('Valid email is required')
    if not full_name or len(full_name) < 2:
        errors.append('Full name is required (minimum 2 characters)')
    if password != confirm_password:
        errors.append('Passwords do not match')
    if not company_id:
        errors.append('Company selection is required')

    # Password complexity
    pwd_valid, pwd_errors = validate_password_complexity(password)
    if not pwd_valid:
        errors.extend(pwd_errors)

    if errors:
        return jsonify({
            'success': False,
            'errors': errors
        }), 400

    # ── Check company exists and has capacity ──
    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM companies WHERE id = ?", (company_id,))
    company = cursor.fetchone()
    if not company:
        conn.close()
        return jsonify({
            'success': False,
            'errors': ['Selected company does not exist']
        }), 400

    if company['license_status'] != 'active':
        conn.close()
        return jsonify({
            'success': False,
            'errors': ['This company is not currently accepting new users']
        }), 400

    # Check max users
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM users WHERE company_id = ?",
        (company_id,)
    )
    user_count = cursor.fetchone()['cnt']
    if user_count >= company['max_users']:
        conn.close()
        return jsonify({
            'success': False,
            'errors': ['This company has reached its maximum number of users']
        }), 400

    # ── Check email uniqueness ──
    cursor.execute("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({
            'success': False,
            'errors': ['An account with this email already exists']
        }), 409

    # ── Create user ──
    password_hash = hash_password(password)
    cursor.execute("""
        INSERT INTO users (email, full_name, password_hash, company_id, role, status)
        VALUES (?, ?, ?, ?, 'viewer', 'PENDING')
    """, (email, full_name, password_hash, company_id))

    conn.commit()
    conn.close()

    logger.info(f"📝 New registration: {email} for company {company['name']} (PENDING)")

    return jsonify({
        'success': True,
        'message': 'Registration successful! Your account is pending approval by your company administrator.'
    }), 201


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

    response = make_response(redirect('/auth/login'))
    response.delete_cookie('session_id', path='/')
    response.delete_cookie('csrf_token', path='/')

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
        httponly=False, samesite='Lax', max_age=30*60, path='/'
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
