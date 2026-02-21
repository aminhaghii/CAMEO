"""
decorators.py — RBAC Middleware Decorators (Enterprise Grade).

Provides:
  @login_required     — Ensures user is authenticated
  @role_required()    — Ensures user has required role
  @csrf_protect       — Validates CSRF token on mutating requests
  @viewer_readonly    — Blocks all mutating operations for viewers

Security:
  - All decorators use functools.wraps to preserve function metadata
  - Role checks are fail-closed (deny by default)
  - Super Admin blind spot: cannot access tenant data
"""

import functools
import logging
from flask import g, request, redirect, url_for, jsonify, abort

logger = logging.getLogger(__name__)

# Paths that don't require authentication
PUBLIC_PATHS = frozenset({
    '/auth/login',
    '/auth/register',
    '/api/auth/check-session',
    '/static',
})


def _is_public_path(path: str) -> bool:
    """Check if the request path is public (no auth needed)."""
    for public in PUBLIC_PATHS:
        if path.startswith(public):
            return True
    return False


def _is_api_request() -> bool:
    """Check if this is an API request (expects JSON response)."""
    return (
        request.path.startswith('/api/') or
        request.accept_mimetypes.best == 'application/json' or
        request.is_json
    )


def login_required(f):
    """
    Decorator: Require authenticated user.

    If not authenticated:
    - API requests: 401 JSON response
    - Browser requests: redirect to /auth/login

    Sets g.user with the authenticated user's data.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, 'user') or g.user is None:
            if _is_api_request():
                return jsonify({
                    'error': 'Authentication required',
                    'code': 'AUTH_REQUIRED'
                }), 401
            return redirect(url_for('auth_bp.login_page'))
        return f(*args, **kwargs)
    return decorated


def role_required(*allowed_roles):
    """
    Decorator: Require specific role(s).

    Usage:
        @role_required('company_admin', 'super_admin')
        def manage_users(): ...

    If role doesn't match:
    - API: 403 JSON response
    - Browser: 403 page

    ⚠️ Must be used AFTER @login_required (or in a route
    where before_request already sets g.user).
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if not hasattr(g, 'user') or g.user is None:
                if _is_api_request():
                    return jsonify({
                        'error': 'Authentication required',
                        'code': 'AUTH_REQUIRED'
                    }), 401
                return redirect(url_for('auth_bp.login_page'))

            user_role = g.user.get('role', '')
            if user_role not in allowed_roles:
                logger.warning(
                    f"🚫 Access denied: {g.user['email']} (role={user_role}) "
                    f"tried to access {request.path} "
                    f"(requires: {', '.join(allowed_roles)})"
                )
                if _is_api_request():
                    return jsonify({
                        'error': 'Insufficient permissions',
                        'code': 'FORBIDDEN',
                        'required_roles': list(allowed_roles),
                        'your_role': user_role
                    }), 403
                abort(403)

            return f(*args, **kwargs)
        return decorated
    return decorator


def super_admin_only(f):
    """
    Decorator: Super Admin only + tenant data blind spot enforcement.

    Super Admins CANNOT access any tenant database.
    This is enforced at the middleware level — even if they somehow
    construct a request, g.tenant_db_path will be None.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(g, 'user') or g.user is None:
            if _is_api_request():
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('auth_bp.login_page'))

        if g.user.get('role') != 'super_admin':
            if _is_api_request():
                return jsonify({'error': 'Super Admin access required'}), 403
            abort(403)

        return f(*args, **kwargs)
    return decorated


def csrf_protect(f):
    """
    Decorator: CSRF protection using double-submit cookie pattern.

    Validates that the CSRF token in the request header/form
    matches the token stored in the session cookie.

    Applied to all mutating operations (POST, PUT, DELETE, PATCH).
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            # Get token from header or form
            token = (
                request.headers.get('X-CSRF-Token') or
                request.form.get('csrf_token') or
                (request.get_json(silent=True) or {}).get('csrf_token')
            )

            # Get expected token from cookie
            expected = request.cookies.get('csrf_token')

            if not token or not expected or token != expected:
                logger.warning(
                    f"🛡️ CSRF validation failed: {request.method} {request.path} "
                    f"(IP: {request.remote_addr})"
                )
                if _is_api_request():
                    return jsonify({
                        'error': 'CSRF token validation failed',
                        'code': 'CSRF_INVALID'
                    }), 403
                abort(403)

        return f(*args, **kwargs)
    return decorated


def viewer_readonly(f):
    """
    Decorator: Enforce read-only access for Viewer role.

    Any mutating request (POST/PUT/DELETE/PATCH) from a Viewer
    will be rejected with 403.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if (hasattr(g, 'user') and g.user and
                g.user.get('role') == 'viewer' and
                request.method in ('POST', 'PUT', 'DELETE', 'PATCH')):
            logger.warning(
                f"🔒 Viewer tried mutating operation: {g.user['email']} "
                f"{request.method} {request.path}"
            )
            if _is_api_request():
                return jsonify({
                    'error': 'Viewers have read-only access',
                    'code': 'VIEWER_READONLY'
                }), 403
            abort(403)
        return f(*args, **kwargs)
    return decorated
