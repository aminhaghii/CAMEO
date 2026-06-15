"""
models.py — Enterprise Auth Database Schema (global_auth.db).

Tables:
  - companies:      Tenant registration and license management
  - users:          Authentication credentials, RBAC roles, account status
  - sessions:       Active session tracking for invalidation
  - login_attempts: Brute-force protection audit trail

Security Standards:
  - Passwords stored as bcrypt hashes (cost=12)
  - Account lockout after 5 failed attempts (15-minute window)
  - Session tokens: 48-byte cryptographically secure random
  - Role enforcement: super_admin, company_admin, operator, viewer
"""

import os
import sqlite3
import logging
from datetime import datetime
from db_utils import get_safe_connection

logger = logging.getLogger(__name__)


def init_auth_db(db_path: str):
    """
    Initialize global_auth.db with enterprise auth schema.
    Safe to call multiple times — uses IF NOT EXISTS.
    Also handles schema migrations for existing databases.
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = get_safe_connection(db_path)
    cursor = conn.cursor()

    # ═══════════════════════════════════════════════════════
    #  Table: companies (Tenant Registry)
    # ═══════════════════════════════════════════════════════
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            tenant_db_path  TEXT,
            license_status  TEXT NOT NULL DEFAULT 'active'
                            CHECK(license_status IN ('active', 'suspended', 'expired')),
            max_users       INTEGER NOT NULL DEFAULT 50,
            contact_email   TEXT,
            phone           TEXT,
            address         TEXT,
            registration_number TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: users (Authentication + RBAC)
    # ═══════════════════════════════════════════════════════
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            email               TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name           TEXT NOT NULL,
            password_hash       TEXT NOT NULL,
            company_id          INTEGER NOT NULL REFERENCES companies(id),
            role                TEXT NOT NULL DEFAULT 'viewer'
                                CHECK(role IN ('super_admin', 'company_admin', 'operator', 'viewer')),
            status              TEXT NOT NULL DEFAULT 'PENDING'
                                CHECK(status IN ('PENDING', 'ACTIVE', 'SUSPENDED')),
            last_login          DATETIME,
            failed_attempts     INTEGER NOT NULL DEFAULT 0,
            locked_until        DATETIME,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            password_changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            force_password_change BOOLEAN NOT NULL DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: sessions (Active Session Tracking)
    # ═══════════════════════════════════════════════════════
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            ip_address  TEXT,
            user_agent  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at  DATETIME NOT NULL,
            is_active   BOOLEAN NOT NULL DEFAULT 1,
            absolute_expires_at DATETIME
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)
    """)

    # ═══════════════════════════════════════════════════════
    #  Table: login_attempts (Brute-Force Audit Trail)
    # ═══════════════════════════════════════════════════════
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT NOT NULL,
            ip_address  TEXT,
            success     BOOLEAN NOT NULL DEFAULT 0,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_login_attempts_email
        ON login_attempts(email, timestamp)
    """)

    conn.commit()

    # ── Schema Migration checks ──
    # Check for users.force_password_change
    try:
        cursor.execute("SELECT force_password_change FROM users LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("Migrating: Adding force_password_change column to users table")
        cursor.execute("ALTER TABLE users ADD COLUMN force_password_change BOOLEAN NOT NULL DEFAULT 0")
        conn.commit()

    # Check for sessions.absolute_expires_at
    try:
        cursor.execute("SELECT absolute_expires_at FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("Migrating: Adding absolute_expires_at column to sessions table")
        cursor.execute("ALTER TABLE sessions ADD COLUMN absolute_expires_at DATETIME")
        cursor.execute("UPDATE sessions SET absolute_expires_at = datetime(created_at, '+8 hours') WHERE absolute_expires_at IS NULL")
        conn.commit()

    # Check for new company metadata columns
    try:
        cursor.execute("SELECT contact_email FROM companies LIMIT 1")
    except sqlite3.OperationalError:
        logger.info("Migrating: Adding metadata columns to companies table")
        cursor.execute("ALTER TABLE companies ADD COLUMN contact_email TEXT")
        cursor.execute("ALTER TABLE companies ADD COLUMN phone TEXT")
        cursor.execute("ALTER TABLE companies ADD COLUMN address TEXT")
        cursor.execute("ALTER TABLE companies ADD COLUMN registration_number TEXT")
        conn.commit()

    conn.close()
    logger.info("global_auth.db initialized successfully")


def seed_default_company_and_admin(db_path: str, password_hash: str):
    """
    Seed the default SAFEWARE company and super admin user.
    Only runs if no companies exist yet. Idempotent.
    """
    conn = get_safe_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM companies")
    if cursor.fetchone()[0] > 0:
        conn.close()
        return  # Already seeded

    # Create SAFEWARE platform company (for super admins)
    cursor.execute("""
        INSERT INTO companies (name, tenant_db_path, license_status, max_users)
        VALUES ('SAFEWARE Platform', NULL, 'active', 999)
    """)
    safeware_id = cursor.lastrowid

    # Create a default demo company (for testing)
    cursor.execute("""
        INSERT INTO companies (name, tenant_db_path, license_status, max_users)
        VALUES ('Demo Petrochemical Co.', NULL, 'active', 50)
    """)
    demo_id = cursor.lastrowid

    # Create super admin user (default password will require reset on first login)
    cursor.execute("""
        INSERT INTO users (email, full_name, password_hash, company_id, role, status, force_password_change)
        VALUES (?, ?, ?, ?, 'super_admin', 'ACTIVE', 1)
    """, ('admin@safeware.io', 'SAFEWARE Admin', password_hash, safeware_id))

    # Create demo company admin (default password will require reset on first login)
    cursor.execute("""
        INSERT INTO users (email, full_name, password_hash, company_id, role, status, force_password_change)
        VALUES (?, ?, ?, ?, 'company_admin', 'ACTIVE', 1)
    """, ('admin@demo.com', 'Demo HSE Manager', password_hash, demo_id))

    conn.commit()
    conn.close()
    logger.info("Default company and admin users seeded")


def get_auth_db_connection(db_path: str) -> sqlite3.Connection:
    """Get a connection to global_auth.db with safety features."""
    return get_safe_connection(db_path)
