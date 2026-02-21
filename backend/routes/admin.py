"""
admin.py — Admin Routes Blueprint (Company Admin + Super Admin).

Endpoints:
  GET  /admin/users               → User management page
  GET  /api/admin/pending-users   → List PENDING users
  POST /api/admin/approve-user    → Approve user + assign role
  POST /api/admin/suspend-user    → Suspend a user
  GET  /api/admin/users           → List all company users
  GET  /admin/companies           → Company management (Super Admin)
  GET  /api/admin/companies       → List companies (Super Admin)
  POST /api/admin/companies       → Create company (Super Admin)

RBAC:
  - Company Admin: manage their own company's users
  - Super Admin: manage companies + platform-wide (NO tenant data access)
"""

import sqlite3
import logging
import os
from datetime import datetime
from flask import (
    Blueprint, request, render_template, jsonify, g
)

from auth.decorators import login_required, role_required, super_admin_only
from auth.models import get_auth_db_connection
from auth.security import hash_password

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin_bp', __name__)


# ═══════════════════════════════════════════════════════
#  User Management (Company Admin)
# ═══════════════════════════════════════════════════════

@admin_bp.route('/admin/users', methods=['GET'])
@login_required
@role_required('company_admin', 'super_admin')
def users_page():
    """Render user management page."""
    return render_template('admin_users.html')


@admin_bp.route('/api/admin/users', methods=['GET'])
@login_required
@role_required('company_admin', 'super_admin')
def list_users():
    """List all users for the current company (or all for super admin)."""
    from flask import current_app
    conn = get_auth_db_connection(current_app.config['AUTH_DB_PATH'])
    cursor = conn.cursor()

    if g.user['role'] == 'super_admin':
        # Super admin sees all users
        cursor.execute("""
            SELECT u.id, u.email, u.full_name, u.role, u.status,
                   u.last_login, u.created_at, c.name as company_name
            FROM users u
            JOIN companies c ON u.company_id = c.id
            ORDER BY u.created_at DESC
        """)
    else:
        # Company admin sees only their company's users
        cursor.execute("""
            SELECT u.id, u.email, u.full_name, u.role, u.status,
                   u.last_login, u.created_at, c.name as company_name
            FROM users u
            JOIN companies c ON u.company_id = c.id
            WHERE u.company_id = ?
            ORDER BY u.created_at DESC
        """, (g.user['company_id'],))

    users = []
    for row in cursor.fetchall():
        users.append({
            'id': row['id'],
            'email': row['email'],
            'full_name': row['full_name'],
            'role': row['role'],
            'status': row['status'],
            'last_login': row['last_login'],
            'created_at': row['created_at'],
            'company_name': row['company_name'],
        })

    conn.close()
    return jsonify(users)


@admin_bp.route('/api/admin/pending-users', methods=['GET'])
@login_required
@role_required('company_admin', 'super_admin')
def pending_users():
    """List PENDING users for the current company."""
    from flask import current_app
    conn = get_auth_db_connection(current_app.config['AUTH_DB_PATH'])
    cursor = conn.cursor()

    if g.user['role'] == 'super_admin':
        cursor.execute("""
            SELECT u.id, u.email, u.full_name, u.created_at, c.name as company_name
            FROM users u
            JOIN companies c ON u.company_id = c.id
            WHERE u.status = 'PENDING'
            ORDER BY u.created_at ASC
        """)
    else:
        cursor.execute("""
            SELECT u.id, u.email, u.full_name, u.created_at, c.name as company_name
            FROM users u
            JOIN companies c ON u.company_id = c.id
            WHERE u.company_id = ? AND u.status = 'PENDING'
            ORDER BY u.created_at ASC
        """, (g.user['company_id'],))

    users = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(users)


@admin_bp.route('/api/admin/pending-count', methods=['GET'])
@login_required
@role_required('company_admin', 'super_admin')
def pending_count():
    """Get count of pending users (for notification badge)."""
    from flask import current_app
    conn = get_auth_db_connection(current_app.config['AUTH_DB_PATH'])
    cursor = conn.cursor()

    if g.user['role'] == 'super_admin':
        cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE status = 'PENDING'")
    else:
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE company_id = ? AND status = 'PENDING'",
            (g.user['company_id'],)
        )

    count = cursor.fetchone()['cnt']
    conn.close()
    return jsonify({'count': count})


@admin_bp.route('/api/admin/approve-user', methods=['POST'])
@login_required
@role_required('company_admin', 'super_admin')
def approve_user():
    """
    Approve a PENDING user and assign their role.

    JSON: { "user_id": 5, "role": "operator" }
    """
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    data = request.get_json() or {}

    user_id = data.get('user_id')
    assigned_role = data.get('role', 'viewer')

    if not user_id:
        return jsonify({'success': False, 'error': 'user_id is required'}), 400

    # Validate role
    allowed_roles = {'company_admin', 'operator', 'viewer'}
    if g.user['role'] != 'super_admin':
        allowed_roles.discard('company_admin')  # Only super admin can promote to company_admin
    if assigned_role not in allowed_roles:
        return jsonify({
            'success': False,
            'error': f'Invalid role. Allowed: {", ".join(allowed_roles)}'
        }), 400

    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    # Verify the user belongs to this admin's company (unless super admin)
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    target_user = cursor.fetchone()

    if not target_user:
        conn.close()
        return jsonify({'success': False, 'error': 'User not found'}), 404

    if target_user['status'] != 'PENDING':
        conn.close()
        return jsonify({'success': False, 'error': 'User is not in PENDING status'}), 400

    if g.user['role'] != 'super_admin' and target_user['company_id'] != g.user['company_id']:
        conn.close()
        return jsonify({'success': False, 'error': 'You can only manage users in your company'}), 403

    # Approve
    cursor.execute("""
        UPDATE users SET status = 'ACTIVE', role = ?
        WHERE id = ?
    """, (assigned_role, user_id))
    conn.commit()
    conn.close()

    logger.info(
        f"✅ User approved: {target_user['email']} → role={assigned_role} "
        f"(by {g.user['email']})"
    )
    return jsonify({
        'success': True,
        'message': f"User {target_user['full_name']} approved as {assigned_role}"
    })


@admin_bp.route('/api/admin/suspend-user', methods=['POST'])
@login_required
@role_required('company_admin', 'super_admin')
def suspend_user():
    """Suspend a user account."""
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    data = request.get_json() or {}

    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': 'user_id is required'}), 400

    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    target_user = cursor.fetchone()

    if not target_user:
        conn.close()
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # Can't suspend yourself
    if target_user['id'] == g.user['id']:
        conn.close()
        return jsonify({'success': False, 'error': 'You cannot suspend yourself'}), 400

    # Can't suspend super admins (unless you are one)
    if target_user['role'] == 'super_admin' and g.user['role'] != 'super_admin':
        conn.close()
        return jsonify({'success': False, 'error': 'Cannot suspend a Super Admin'}), 403

    if g.user['role'] != 'super_admin' and target_user['company_id'] != g.user['company_id']:
        conn.close()
        return jsonify({'success': False, 'error': 'You can only manage users in your company'}), 403

    cursor.execute("UPDATE users SET status = 'SUSPENDED' WHERE id = ?", (user_id,))

    # Also invalidate all their sessions
    cursor.execute("UPDATE sessions SET is_active = 0 WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

    logger.info(f"🚫 User suspended: {target_user['email']} (by {g.user['email']})")
    return jsonify({
        'success': True,
        'message': f"User {target_user['full_name']} has been suspended"
    })


# ═══════════════════════════════════════════════════════
#  Company Management (Super Admin Only)
# ═══════════════════════════════════════════════════════

@admin_bp.route('/admin/companies', methods=['GET'])
@login_required
@super_admin_only
def companies_page():
    """Render company management page (super admin)."""
    return render_template('admin_users.html')  # Same template, different mode


@admin_bp.route('/api/admin/companies', methods=['GET'])
@login_required
@super_admin_only
def list_companies():
    """List all companies."""
    from flask import current_app
    conn = get_auth_db_connection(current_app.config['AUTH_DB_PATH'])
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM users WHERE company_id = c.id) as user_count,
               (SELECT COUNT(*) FROM users WHERE company_id = c.id AND status = 'PENDING') as pending_count
        FROM companies c
        ORDER BY c.created_at DESC
    """)

    companies = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(companies)


@admin_bp.route('/api/admin/companies', methods=['POST'])
@login_required
@super_admin_only
def create_company():
    """Create a new tenant company."""
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    data = request.get_json() or {}

    name = (data.get('name') or '').strip()
    max_users = data.get('max_users', 50)

    if not name or len(name) < 2:
        return jsonify({'success': False, 'error': 'Company name is required'}), 400

    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    # Check uniqueness
    cursor.execute("SELECT id FROM companies WHERE name = ?", (name,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Company name already exists'}), 409

    cursor.execute("""
        INSERT INTO companies (name, license_status, max_users)
        VALUES (?, 'active', ?)
    """, (name, max_users))

    company_id = cursor.lastrowid
    conn.commit()
    conn.close()

    logger.info(f"🏢 Company created: {name} (id={company_id}, max_users={max_users})")
    return jsonify({
        'success': True,
        'company_id': company_id,
        'message': f'Company "{name}" created successfully'
    }), 201
