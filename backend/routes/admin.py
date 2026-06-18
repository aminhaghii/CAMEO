"""
admin.py — Admin Routes Blueprint (Company Admin + Super Admin).

Endpoints:
  GET  /admin/users                       → User management page
  GET  /api/admin/pending-users           → List PENDING users
  POST /api/admin/approve-user            → Approve user + assign role
  POST /api/admin/suspend-user            → Suspend a user
  GET  /api/admin/users                   → List all company users
  GET  /admin/companies                   → Company management (Super Admin)
  GET  /api/admin/companies               → List companies (Super Admin)
  POST /api/admin/companies               → Create company (Super Admin)
  GET  /admin/platform-logs               → Platform-wide log dashboard (Super Admin)
  GET  /api/admin/platform-logs           → Cross-tenant log data API (Super Admin)
  GET  /api/admin/platform-logs/stats     → Cross-tenant log stats (Super Admin)
  GET  /api/admin/platform-logs/export    → Enterprise Excel export (Super Admin)

RBAC:
  - Company Admin: manage their own company's users
  - Super Admin: manage companies + platform-wide (NO tenant data access)
"""

import re
import sqlite3
import logging
import os
from datetime import datetime
from flask import (
    Blueprint, request, render_template, jsonify, g, send_file
)

from auth.decorators import login_required, role_required, csrf_protect, super_admin_only
from auth.models import get_auth_db_connection
from db_utils import get_safe_connection
from auth.security import hash_password, validate_password_complexity
from activity_logger import log_event

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin_bp', __name__)

def _get_db_path(company_id):
    from flask import current_app
    import os
    return os.path.join(current_app.config['DATA_DIR'], f"{company_id}_user.db")


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


@admin_bp.route('/api/admin/users/create', methods=['POST'])
@login_required
@role_required('company_admin', 'super_admin')
@csrf_protect
def create_user():
    """Manually add a user to the company."""
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    data = request.get_json() or {}

    email = (data.get('email') or '').strip().lower()
    full_name = (data.get('full_name') or '').strip()
    password = data.get('password') or ''
    role = data.get('role', 'viewer')
    company_id = data.get('company_id') if g.user['role'] == 'super_admin' else g.user['company_id']

    if not email or not full_name or not password:
        return jsonify({'success': False, 'error': 'Email, full name, and password are required'}), 400

    if role not in ('company_admin', 'operator', 'viewer'):
        return jsonify({'success': False, 'error': 'Invalid role'}), 400

    pwd_valid, pwd_errors = validate_password_complexity(password)
    if not pwd_valid:
        return jsonify({'success': False, 'error': ' | '.join(pwd_errors)}), 400

    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    # Check max users limit
    cursor.execute("SELECT max_users FROM companies WHERE id = ?", (company_id,))
    comp_data = cursor.fetchone()
    if not comp_data:
        conn.close()
        return jsonify({'success': False, 'error': 'Company not found'}), 404

    cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE company_id = ?", (company_id,))
    current_count = cursor.fetchone()['cnt']
    
    if current_count >= comp_data['max_users']:
        conn.close()
        return jsonify({'success': False, 'error': 'Company has reached its maximum user allocation'}), 400

    # Check email uniqueness
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Email already registered in the system'}), 409

    hashed_pw = hash_password(password)
    
    # We set status directly to ACTIVE since an admin is creating them
    cursor.execute("""
        INSERT INTO users (email, full_name, password_hash, company_id, role, status)
        VALUES (?, ?, ?, ?, ?, 'ACTIVE')
    """, (email, full_name, hashed_pw, company_id, role))
    
    conn.commit()
    conn.close()

    log_event(g.user['id'], 'admin', f"Created new user: {email} with role {role}")
    return jsonify({'success': True})


@admin_bp.route('/api/admin/approve-user', methods=['POST'])
@login_required
@role_required('company_admin', 'super_admin')
@csrf_protect
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

    if target_user['status'] not in ('PENDING', 'SUSPENDED'):
        conn.close()
        return jsonify({'success': False, 'error': 'User is not in PENDING or SUSPENDED status'}), 400

    if g.user['role'] != 'super_admin' and target_user['company_id'] != g.user['company_id']:
        conn.close()
        return jsonify({'success': False, 'error': 'You can only manage users in your company'}), 403

    # Guard: if re-activating a company_admin with a lower role, ensure they are not the last admin
    if target_user['role'] == 'company_admin' and assigned_role != 'company_admin':
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE company_id = ? AND role = 'company_admin' AND status = 'ACTIVE'",
            (target_user['company_id'],)
        )
        remaining_admins = cursor.fetchone()['cnt']
        if remaining_admins == 0:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Cannot demote the last active administrator of this company. Promote another user to Company Admin first.'
            }), 400

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
    log_event(
        db_path=_get_db_path(target_user['company_id']),
        event_type='user_approve',
        category='system',
        severity='info',
        title='User account approved',
        detail=f"User {target_user['email']} approved as role '{assigned_role}' by admin {g.user['email']}",
        user_id=g.user['id'],
        entity_type='user',
        entity_id=target_user['id'],
        entity_name=target_user['full_name'],
    )
    return jsonify({
        'success': True,
        'message': f"User {target_user['full_name']} approved as {assigned_role}"
    })


@admin_bp.route('/api/admin/suspend-user', methods=['POST'])
@login_required
@role_required('company_admin', 'super_admin')
@csrf_protect
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

    # Guard: cannot suspend the last active company_admin of a company
    if target_user['role'] == 'company_admin':
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE company_id = ? AND role = 'company_admin' AND status = 'ACTIVE' AND id != ?",
            (target_user['company_id'], user_id)
        )
        remaining_admins = cursor.fetchone()['cnt']
        if remaining_admins == 0:
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Cannot suspend the last active administrator of this company. Promote another user to Company Admin first.'
            }), 400

    cursor.execute("UPDATE users SET status = 'SUSPENDED' WHERE id = ?", (user_id,))

    # Also invalidate all their sessions
    cursor.execute("UPDATE sessions SET is_active = 0 WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()

    logger.info(f"🚫 User suspended: {target_user['email']} (by {g.user['email']})")
    log_event(
        db_path=_get_db_path(target_user['company_id']),
        event_type='user_suspend',
        category='system',
        severity='warning',
        title='User account suspended',
        detail=f"User {target_user['email']} suspended by admin {g.user['email']}",
        user_id=g.user['id'],
        entity_type='user',
        entity_id=target_user['id'],
        entity_name=target_user['full_name'],
    )
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
    return render_template('admin_companies.html')


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
@csrf_protect
def create_company():
    """Create a new tenant company and its initial company_admin."""
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    data = request.get_json() or {}

    name = (data.get('name') or '').strip()
    max_users = data.get('max_users', 50)
    contact_email = (data.get('contact_email') or '').strip()
    phone = (data.get('phone') or '').strip()
    address = (data.get('address') or '').strip()
    registration_number = (data.get('registration_number') or '').strip()

    admin_email = (data.get('admin_email') or '').strip().lower()
    admin_name = (data.get('admin_name') or '').strip()
    admin_password = data.get('admin_password') or ''

    if not name or len(name) < 2:
        return jsonify({'success': False, 'error': 'Company name is required'}), 400

    if not admin_email or not admin_name or not admin_password:
        return jsonify({'success': False, 'error': 'Initial admin details (email, name, password) are required'}), 400

    pwd_valid, pwd_errors = validate_password_complexity(admin_password)
    if not pwd_valid:
        return jsonify({'success': False, 'error': ' | '.join(pwd_errors)}), 400

    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    # Check company uniqueness
    cursor.execute("SELECT id FROM companies WHERE name = ?", (name,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Company name already exists'}), 409

    # Check user uniqueness
    cursor.execute("SELECT id FROM users WHERE email = ?", (admin_email,))
    if cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Admin email already exists in the system'}), 409

    try:
        # Insert Company
        cursor.execute("""
            INSERT INTO companies (name, license_status, max_users, contact_email, phone, address, registration_number)
            VALUES (?, 'active', ?, ?, ?, ?, ?)
        """, (name, max_users, contact_email, phone, address, registration_number))
        
        company_id = cursor.lastrowid

        # Insert Admin User
        hashed_pw = hash_password(admin_password)
        cursor.execute("""
            INSERT INTO users (email, full_name, password_hash, company_id, role, status)
            VALUES (?, ?, ?, ?, 'company_admin', 'ACTIVE')
        """, (admin_email, admin_name, hashed_pw, company_id))

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.error(f"Failed to create company and admin: {e}")
        return jsonify({'success': False, 'error': 'Internal database error'}), 500

    conn.close()

    logger.info(f"🏢 Company created: {name} (id={company_id}, max_users={max_users})")
    return jsonify({
        'success': True,
        'company_id': company_id,
        'message': f'Company "{name}" created successfully'
    }), 201


@admin_bp.route('/api/admin/companies/<int:company_id>', methods=['PUT'])
@login_required
@super_admin_only
@csrf_protect
def update_company(company_id):
    """Update a tenant company (e.g., max_users, license_status)."""
    from flask import current_app
    auth_db = current_app.config['AUTH_DB_PATH']
    data = request.get_json() or {}

    max_users = data.get('max_users')
    license_status = data.get('license_status')

    if max_users is None and not license_status:
        return jsonify({'success': False, 'error': 'No update fields provided'}), 400

    conn = get_auth_db_connection(auth_db)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM companies WHERE id = ?", (company_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'error': 'Company not found'}), 404

    updates = []
    params = []

    if max_users is not None:
        updates.append("max_users = ?")
        params.append(max_users)

    if license_status:
        if license_status not in ('active', 'suspended', 'expired'):
            conn.close()
            return jsonify({'success': False, 'error': 'Invalid license status'}), 400
        updates.append("license_status = ?")
        params.append(license_status)

    if updates:
        params.append(company_id)
        cursor.execute(f"UPDATE companies SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    conn.close()
    
    logger.info(f"🏢 Company updated: id={company_id}, updates={data}")
    return jsonify({
        'success': True,
        'message': f'Company updated successfully'
    })


# ═══════════════════════════════════════════════════════
#  Platform-Wide Logs (Super Admin Only)
# ═══════════════════════════════════════════════════════

def _get_all_tenant_db_paths(data_dir, company_filter=None):
    """Return list of (company_id, db_path) for every *_user.db in data_dir.

    company_filter: int company_id to restrict to one tenant, or None for all.
    Includes user.db mapped as company_id=0 (legacy/unknown-user events).
    """
    results = []
    try:
        for fname in os.listdir(data_dir):
            m = re.match(r'^(\d+)_user\.db$', fname)
            if m:
                cid = int(m.group(1))
                if company_filter is None or cid == company_filter:
                    results.append((cid, os.path.join(data_dir, fname)))
            elif fname == 'user.db' and (company_filter is None or company_filter == 0):
                results.append((0, os.path.join(data_dir, fname)))
    except OSError:
        pass
    return results


def _build_log_where(args):
    """Build (WHERE clause, params list) from request.args for activity_logs queries."""
    conditions = []
    params = []

    category = args.get('category', 'ALL')
    if category and category != 'ALL':
        conditions.append("category = ?")
        params.append(category)

    severity = args.get('severity', 'ALL')
    if severity and severity != 'ALL':
        conditions.append("severity = ?")
        params.append(severity)

    search = (args.get('search') or '').strip()
    if search:
        conditions.append("(title LIKE ? OR detail LIKE ? OR user_id LIKE ? OR entity_name LIKE ?)")
        params.extend([f'%{search}%'] * 4)

    date_range = args.get('date_range', 'all')
    if date_range == '24h':
        conditions.append("created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 day')")
    elif date_range == '7d':
        conditions.append("created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-7 days')")
    elif date_range == '30d':
        conditions.append("created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-30 days')")

    user_filter = args.get('user_id', 'ALL')
    if user_filter and user_filter != 'ALL':
        conditions.append("user_id = ?")
        params.append(user_filter)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where_sql, params


@admin_bp.route('/admin/platform-logs', methods=['GET'])
@login_required
@super_admin_only
def platform_logs_page():
    """Render super-admin platform-wide activity log dashboard."""
    return render_template('admin_platform_logs.html')


@admin_bp.route('/api/admin/platform-logs', methods=['GET'])
@login_required
@super_admin_only
def platform_logs_api():
    """Return paginated cross-tenant activity logs for all companies."""
    from flask import current_app
    data_dir = current_app.config['DATA_DIR']
    auth_db = current_app.config['AUTH_DB_PATH']

    # Build company_id → name map
    company_map = {0: 'Legacy / Unknown'}
    try:
        conn_auth = get_auth_db_connection(auth_db)
        for row in conn_auth.execute("SELECT id, name FROM companies").fetchall():
            company_map[row['id']] = row['name']
        conn_auth.close()
    except Exception:
        pass

    # Parse filters
    try:
        company_filter_raw = request.args.get('company_id', 'ALL')
        company_filter = int(company_filter_raw) if company_filter_raw not in ('ALL', '', None) else None
    except (ValueError, TypeError):
        company_filter = None

    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = min(100, max(10, int(request.args.get('per_page', 50))))
    except (ValueError, TypeError):
        per_page = 50

    where_sql, where_params = _build_log_where(request.args)

    all_rows = []
    tenant_dbs = _get_all_tenant_db_paths(data_dir, company_filter)

    for (cid, db_path) in tenant_dbs:
        if not os.path.exists(db_path):
            continue
        company_name = company_map.get(cid, f'Company {cid}')
        try:
            conn = get_safe_connection(db_path, readonly=True)
            # Verify table exists
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_logs'"
            ).fetchone()
            if not tbl:
                conn.close()
                continue
            rows = conn.execute(
                f"SELECT * FROM activity_logs {where_sql} ORDER BY created_at DESC",
                where_params
            ).fetchall()
            for r in rows:
                entry = dict(r)
                entry['company_id'] = cid
                entry['company_name'] = company_name
                # Parse meta JSON safely
                try:
                    import json as _json
                    entry['meta'] = _json.loads(entry['meta']) if entry.get('meta') else None
                except Exception:
                    entry['meta'] = None
                all_rows.append(entry)
            conn.close()
        except Exception as e:
            logger.warning(f"platform_logs_api: error reading {db_path}: {e}")

    # Sort merged results
    all_rows.sort(key=lambda x: x.get('created_at') or '', reverse=True)

    total = len(all_rows)
    offset = (page - 1) * per_page
    page_rows = all_rows[offset: offset + per_page]

    return jsonify({
        'success': True,
        'data': page_rows,
        'total': total,
        'page': page,
        'per_page': per_page,
    })


@admin_bp.route('/api/admin/platform-logs/stats', methods=['GET'])
@login_required
@super_admin_only
def platform_logs_stats():
    """Return aggregate stats across all tenant DBs for the platform log dashboard."""
    from flask import current_app
    data_dir = current_app.config['DATA_DIR']
    auth_db = current_app.config['AUTH_DB_PATH']

    company_map = {0: 'Legacy / Unknown'}
    try:
        conn_auth = get_auth_db_connection(auth_db)
        for row in conn_auth.execute("SELECT id, name FROM companies").fetchall():
            company_map[row['id']] = row['name']
        conn_auth.close()
    except Exception:
        pass

    total = 0
    by_category = {}
    by_severity = {}
    recent_24h = 0
    errors_warnings = 0
    per_company = {}

    tenant_dbs = _get_all_tenant_db_paths(data_dir)

    for (cid, db_path) in tenant_dbs:
        if not os.path.exists(db_path):
            continue
        company_name = company_map.get(cid, f'Company {cid}')
        try:
            conn = get_safe_connection(db_path, readonly=True)
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_logs'"
            ).fetchone()
            if not tbl:
                conn.close()
                continue

            c_total = conn.execute("SELECT COUNT(*) FROM activity_logs").fetchone()[0]
            total += c_total

            for row in conn.execute(
                "SELECT category, COUNT(*) as cnt FROM activity_logs GROUP BY category"
            ).fetchall():
                by_category[row['category']] = by_category.get(row['category'], 0) + row['cnt']

            for row in conn.execute(
                "SELECT severity, COUNT(*) as cnt FROM activity_logs GROUP BY severity"
            ).fetchall():
                by_severity[row['severity']] = by_severity.get(row['severity'], 0) + row['cnt']

            c_24h = conn.execute(
                "SELECT COUNT(*) FROM activity_logs WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')"
            ).fetchone()[0]
            recent_24h += c_24h

            c_err = conn.execute(
                "SELECT COUNT(*) FROM activity_logs WHERE severity IN ('error','critical','warning')"
            ).fetchone()[0]
            errors_warnings += c_err

            per_company[str(cid)] = {
                'company_id': cid,
                'company_name': company_name,
                'total': c_total,
                'errors_warnings': c_err,
                'recent_24h': c_24h,
            }
            conn.close()
        except Exception as e:
            logger.warning(f"platform_logs_stats: error reading {db_path}: {e}")

    return jsonify({
        'success': True,
        'data': {
            'total': total,
            'by_category': by_category,
            'by_severity': by_severity,
            'recent_24h': recent_24h,
            'errors_warnings': errors_warnings,
            'companies_count': len(per_company),
            'per_company': per_company,
        }
    })


@admin_bp.route('/api/admin/platform-logs/export', methods=['GET'])
@login_required
@super_admin_only
def platform_logs_export():
    """Export cross-tenant activity logs as enterprise multi-sheet Excel workbook."""
    from flask import current_app
    from logic.platform_logs_excel import generate_platform_logs_excel

    data_dir = current_app.config['DATA_DIR']
    auth_db = current_app.config['AUTH_DB_PATH']

    company_map = {0: 'Legacy / Unknown'}
    try:
        conn_auth = get_auth_db_connection(auth_db)
        for row in conn_auth.execute("SELECT id, name FROM companies").fetchall():
            company_map[row['id']] = row['name']
        conn_auth.close()
    except Exception:
        pass

    try:
        company_filter_raw = request.args.get('company_id', 'ALL')
        company_filter = int(company_filter_raw) if company_filter_raw not in ('ALL', '', None) else None
    except (ValueError, TypeError):
        company_filter = None

    where_sql, where_params = _build_log_where(request.args)

    all_rows = []
    tenant_dbs = _get_all_tenant_db_paths(data_dir, company_filter)

    for (cid, db_path) in tenant_dbs:
        if not os.path.exists(db_path):
            continue
        company_name = company_map.get(cid, f'Company {cid}')
        try:
            conn = get_safe_connection(db_path, readonly=True)
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='activity_logs'"
            ).fetchone()
            if not tbl:
                conn.close()
                continue
            rows = conn.execute(
                f"SELECT * FROM activity_logs {where_sql} ORDER BY created_at DESC",
                where_params
            ).fetchall()
            for r in rows:
                entry = dict(r)
                entry['company_id'] = cid
                entry['company_name'] = company_name
                try:
                    import json as _json
                    entry['meta'] = _json.loads(entry['meta']) if entry.get('meta') else None
                except Exception:
                    entry['meta'] = None
                all_rows.append(entry)
            conn.close()
        except Exception as e:
            logger.warning(f"platform_logs_export: error reading {db_path}: {e}")

    all_rows.sort(key=lambda x: x.get('created_at') or '', reverse=True)

    # Build stats for analytics sheets
    total = len(all_rows)
    by_category = {}
    by_severity = {}
    per_company_stats = {}
    errors_warnings = 0
    recent_24h = 0
    cutoff_24h = (datetime.utcnow().replace(microsecond=0)
                  .strftime('%Y-%m-%dT%H:%M:%SZ'))
    # compute cutoff as a simple string-comparable ISO timestamp 24h ago
    from datetime import timedelta
    cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')

    for entry in all_rows:
        cat = entry.get('category') or 'system'
        sev = entry.get('severity') or 'info'
        ts  = entry.get('created_at') or ''
        by_category[cat] = by_category.get(cat, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1
        is_alert = sev in ('error', 'critical', 'warning')
        if is_alert:
            errors_warnings += 1
        if ts >= cutoff_24h:
            recent_24h += 1

        cid_str = str(entry.get('company_id', 0))
        if cid_str not in per_company_stats:
            per_company_stats[cid_str] = {
                'company_id': entry.get('company_id', 0),
                'company_name': entry.get('company_name', 'Unknown'),
                'total': 0,
                'errors_warnings': 0,
                'recent_24h': 0,
            }
        per_company_stats[cid_str]['total'] += 1
        if is_alert:
            per_company_stats[cid_str]['errors_warnings'] += 1
        if ts >= cutoff_24h:
            per_company_stats[cid_str]['recent_24h'] += 1

    stats = {
        'total': total,
        'by_category': by_category,
        'by_severity': by_severity,
        'errors_warnings': errors_warnings,
        'recent_24h': recent_24h,
        'companies_count': len(per_company_stats),
        'per_company': per_company_stats,
    }

    xlsx_bytes = generate_platform_logs_excel(all_rows, stats)

    filename = f"safeware_platform_logs_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        xlsx_bytes,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename,
    )
