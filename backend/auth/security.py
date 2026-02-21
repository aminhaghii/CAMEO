"""
security.py — Enterprise Security Utilities (NIST 800-63B Compliant).

Provides:
  - Password hashing (bcrypt, cost=12)
  - Password complexity validation (NIST 800-63B)
  - Session token generation (48-byte cryptographic random)
  - Brute-force detection and account lockout
  - Login attempt recording
  - CSRF token generation (double-submit cookie)

Security Standards Applied:
  - OWASP Authentication Cheat Sheet
  - NIST SP 800-63B (Digital Identity Guidelines)
  - CWE-307 (Brute Force Protection)
  - CWE-521 (Weak Password Requirements)
"""

import re
import secrets
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Tuple, List, Optional

import bcrypt

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  Configuration Constants
# ═══════════════════════════════════════════════════════
BCRYPT_COST_FACTOR = 12
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15
SESSION_DURATION_MINUTES = 30
SESSION_TOKEN_BYTES = 48
CSRF_TOKEN_BYTES = 32

# NIST 800-63B minimum requirements
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


# ═══════════════════════════════════════════════════════
#  Password Hashing (bcrypt)
# ═══════════════════════════════════════════════════════

def hash_password(plain_password: str) -> str:
    """
    Hash a password using bcrypt with cost factor 12.

    bcrypt is chosen over SHA-256 because:
    - It's purposely slow (resistant to brute-force)
    - It includes a built-in salt
    - It's recommended by OWASP for password storage

    Args:
        plain_password: The plaintext password

    Returns:
        Hashed password string (60 chars, includes salt)
    """
    password_bytes = plain_password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against its bcrypt hash.
    Uses constant-time comparison to prevent timing attacks.

    Args:
        plain_password: The plaintext password to check
        hashed_password: The stored bcrypt hash

    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except (ValueError, TypeError) as e:
        logger.warning(f"Password verification error: {e}")
        return False


# ═══════════════════════════════════════════════════════
#  Password Complexity (NIST 800-63B)
# ═══════════════════════════════════════════════════════

def validate_password_complexity(password: str) -> Tuple[bool, List[str]]:
    """
    Validate password against enterprise security requirements.

    NIST 800-63B + Enterprise Standards:
    - Minimum 8 characters
    - Maximum 128 characters
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 digit
    - At least 1 special character
    - Not a commonly breached password

    Args:
        password: The password to validate

    Returns:
        (is_valid, list_of_error_messages)
    """
    errors = []

    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    if len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Password must not exceed {MAX_PASSWORD_LENGTH} characters")

    if not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter")

    if not re.search(r'[a-z]', password):
        errors.append("Password must contain at least one lowercase letter")

    if not re.search(r'\d', password):
        errors.append("Password must contain at least one digit")

    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?~`]', password):
        errors.append("Password must contain at least one special character")

    # Check against common breached passwords (top 100)
    COMMON_PASSWORDS = {
        'password', 'password1', '12345678', '123456789', 'qwerty123',
        'abc12345', 'password123', 'admin123', 'letmein1', 'welcome1',
        'iloveyou', 'sunshine1', 'princess1', '1234567890', 'football1',
        'charlie1', 'shadow12', 'master12', 'dragon12', 'monkey123',
    }
    if password.lower() in COMMON_PASSWORDS:
        errors.append("This password is too common and has been found in data breaches")

    return (len(errors) == 0, errors)


# ═══════════════════════════════════════════════════════
#  Session Token Generation
# ═══════════════════════════════════════════════════════

def generate_session_id() -> str:
    """
    Generate a cryptographically secure session token.
    Uses secrets.token_urlsafe for 48 bytes of entropy (384 bits).
    This exceeds OWASP's recommendation of 128 bits minimum.
    """
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def generate_csrf_token() -> str:
    """
    Generate a CSRF token for double-submit cookie pattern.
    32 bytes = 256 bits of entropy.
    """
    return secrets.token_urlsafe(CSRF_TOKEN_BYTES)


# ═══════════════════════════════════════════════════════
#  Brute-Force Protection (CWE-307)
# ═══════════════════════════════════════════════════════

def check_brute_force(
    email: str,
    ip_address: str,
    auth_db_path: str
) -> Tuple[bool, int]:
    """
    Check if an account or IP is locked due to brute-force attempts.

    Strategy:
    1. Check user's `locked_until` field first (account-level lock).
    2. Count recent failed attempts from this IP in the last 15 minutes.
    3. If either exceeds threshold, block the attempt.

    Args:
        email: The email attempting login
        ip_address: The client's IP address
        auth_db_path: Path to global_auth.db

    Returns:
        (is_blocked, seconds_remaining_until_unlock)
    """
    conn = sqlite3.connect(auth_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    now = datetime.utcnow()

    # Check account-level lock
    cursor.execute(
        "SELECT locked_until, failed_attempts FROM users WHERE email = ? COLLATE NOCASE",
        (email,)
    )
    user = cursor.fetchone()

    if user and user['locked_until']:
        locked_until = datetime.fromisoformat(user['locked_until'])
        if now < locked_until:
            remaining = int((locked_until - now).total_seconds())
            conn.close()
            return (True, remaining)
        else:
            # Lock expired — reset
            cursor.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE email = ? COLLATE NOCASE",
                (email,)
            )
            conn.commit()

    # Check IP-level rate limiting (last 15 minutes)
    window = (now - timedelta(minutes=LOCKOUT_DURATION_MINUTES)).isoformat()
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM login_attempts
        WHERE ip_address = ? AND success = 0 AND timestamp > ?
    """, (ip_address, window))
    ip_fails = cursor.fetchone()['cnt']

    conn.close()

    if ip_fails >= MAX_FAILED_ATTEMPTS * 2:  # IP gets double threshold
        return (True, LOCKOUT_DURATION_MINUTES * 60)

    return (False, 0)


def record_login_attempt(
    email: str,
    ip_address: str,
    success: bool,
    auth_db_path: str
):
    """
    Record a login attempt and handle account lockout.

    On failure:
    - Increment failed_attempts counter
    - If threshold reached, set locked_until

    On success:
    - Reset failed_attempts to 0
    - Update last_login timestamp
    """
    conn = sqlite3.connect(auth_db_path)
    cursor = conn.cursor()
    now = datetime.utcnow()

    # Record the attempt
    cursor.execute("""
        INSERT INTO login_attempts (email, ip_address, success, timestamp)
        VALUES (?, ?, ?, ?)
    """, (email, ip_address, success, now.isoformat()))

    if success:
        # Reset on successful login
        cursor.execute("""
            UPDATE users
            SET failed_attempts = 0, locked_until = NULL, last_login = ?
            WHERE email = ? COLLATE NOCASE
        """, (now.isoformat(), email))
    else:
        # Increment failure count
        cursor.execute("""
            UPDATE users
            SET failed_attempts = failed_attempts + 1
            WHERE email = ? COLLATE NOCASE
        """, (email,))

        # Check if we need to lock
        cursor.execute(
            "SELECT failed_attempts FROM users WHERE email = ? COLLATE NOCASE",
            (email,)
        )
        user = cursor.fetchone()
        if user and user[0] >= MAX_FAILED_ATTEMPTS:
            lock_time = now + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            cursor.execute("""
                UPDATE users SET locked_until = ?
                WHERE email = ? COLLATE NOCASE
            """, (lock_time.isoformat(), email))
            logger.warning(
                f"🔒 Account locked: {email} (IP: {ip_address}) — "
                f"{MAX_FAILED_ATTEMPTS} failed attempts. "
                f"Locked until {lock_time.isoformat()}"
            )

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════
#  Session Management
# ═══════════════════════════════════════════════════════

def create_session(
    user_id: int,
    ip_address: str,
    user_agent: str,
    auth_db_path: str
) -> str:
    """
    Create a new session in global_auth.db.

    Returns the session ID (to be set as cookie).
    Session expires after SESSION_DURATION_MINUTES.
    """
    session_id = generate_session_id()
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=SESSION_DURATION_MINUTES)

    conn = sqlite3.connect(auth_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO sessions (id, user_id, ip_address, user_agent, created_at, expires_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (session_id, user_id, ip_address, user_agent, now.isoformat(), expires_at.isoformat()))

    conn.commit()
    conn.close()

    return session_id


def validate_session(
    session_id: str,
    auth_db_path: str
) -> Optional[dict]:
    """
    Validate a session token and return user data if valid.

    Checks:
    1. Session exists in DB
    2. Session is still active (not invalidated)
    3. Session has not expired
    4. Extends session on each valid access (sliding window)

    Returns:
        User dict {id, email, full_name, role, status, company_id, company_name,
                    tenant_db_path} or None if invalid.
    """
    if not session_id:
        return None

    conn = sqlite3.connect(auth_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT s.*, u.id as user_id, u.email, u.full_name, u.role, u.status,
               u.company_id, c.name as company_name, c.tenant_db_path,
               c.license_status
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        JOIN companies c ON u.company_id = c.id
        WHERE s.id = ? AND s.is_active = 1
    """, (session_id,))

    row = cursor.fetchone()

    if not row:
        conn.close()
        return None

    # Check expiry
    expires_at = datetime.fromisoformat(row['expires_at'])
    now = datetime.utcnow()

    if now > expires_at:
        # Session expired — invalidate
        cursor.execute(
            "UPDATE sessions SET is_active = 0 WHERE id = ?",
            (session_id,)
        )
        conn.commit()
        conn.close()
        return None

    # Check user status
    if row['status'] != 'ACTIVE':
        conn.close()
        return None

    # Check company license
    if row['license_status'] not in ('active',):
        conn.close()
        return None

    # Sliding window: extend session on each valid access
    new_expires = now + timedelta(minutes=SESSION_DURATION_MINUTES)
    cursor.execute(
        "UPDATE sessions SET expires_at = ? WHERE id = ?",
        (new_expires.isoformat(), session_id)
    )
    conn.commit()
    conn.close()

    return {
        'id': row['user_id'],
        'email': row['email'],
        'full_name': row['full_name'],
        'role': row['role'],
        'status': row['status'],
        'company_id': row['company_id'],
        'company_name': row['company_name'],
        'tenant_db_path': row['tenant_db_path'],
    }


def invalidate_session(session_id: str, auth_db_path: str):
    """Invalidate a session (logout)."""
    conn = sqlite3.connect(auth_db_path)
    conn.execute(
        "UPDATE sessions SET is_active = 0 WHERE id = ?",
        (session_id,)
    )
    conn.commit()
    conn.close()


def invalidate_all_sessions(user_id: int, auth_db_path: str):
    """Invalidate ALL sessions for a user (force logout everywhere)."""
    conn = sqlite3.connect(auth_db_path)
    conn.execute(
        "UPDATE sessions SET is_active = 0 WHERE user_id = ?",
        (user_id,)
    )
    conn.commit()
    conn.close()


def cleanup_expired_sessions(auth_db_path: str):
    """Remove expired sessions (garbage collection). Call periodically."""
    conn = sqlite3.connect(auth_db_path)
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        "DELETE FROM sessions WHERE expires_at < ? OR is_active = 0",
        (now,)
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    if count > 0:
        logger.info(f"Cleaned up {count} expired sessions")
