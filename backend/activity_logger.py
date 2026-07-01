"""
activity_logger.py
------------------
Centralised activity-logging module for SAFEWARE-CAMEO.

Usage:
    from activity_logger import log_event

    log_event(
        db_path      = g.tenant_db_path,
        event_type   = 'warehouse_import',
        category     = 'import',
        severity     = 'info',          # 'info' | 'warning' | 'error' | 'critical'
        title        = 'Batch imported to warehouse',
        detail       = '42 chemicals added to Main Warehouse',
        user_id      = g.user['id'],
        meta         = {'batch_id': batch_id, 'count': 42},
        entity_type  = 'batch',         # 'batch' | 'chemical' | 'warehouse' | 'user' | 'system'
        entity_id    = batch_id,
        entity_name  = filename,
    )

The function is a no-op (never raises) so it is safe to call from any route
without wrapping in try/except.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

from db_utils import get_safe_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL – run once per connection on first call
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS activity_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT    NOT NULL,
    category     TEXT    NOT NULL DEFAULT 'system',
    severity     TEXT    NOT NULL DEFAULT 'info',
    title        TEXT    NOT NULL,
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
"""

_MIGRATE_COLUMNS = [
    ("severity",    "TEXT NOT NULL DEFAULT 'info'"),
    ("entity_type", "TEXT"),
    ("entity_id",   "TEXT"),
    ("entity_name", "TEXT"),
    ("meta",        "TEXT"),
    ("ip_address",  "TEXT"),
    ("session_id",  "TEXT"),
    ("duration_ms", "INTEGER"),
]


def _ensure_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute(_CREATE_TABLE)
    # Safe-add any new columns so old DBs are upgraded automatically
    try:
        cursor.execute("PRAGMA table_info(activity_logs)")
        existing = {row[1] for row in cursor.fetchall()}
        for col_name, col_def in _MIGRATE_COLUMNS:
            if col_name not in existing:
                cursor.execute(
                    f"ALTER TABLE activity_logs ADD COLUMN {col_name} {col_def}"
                )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_event(
    *,
    db_path: str,
    event_type: str,
    category: str = "system",
    severity: str = "info",
    title: str,
    detail: str = None,
    user_id=None,
    entity_type: str = None,
    entity_id: str = None,
    entity_name: str = None,
    meta: dict = None,
    ip_address: str = None,
    session_id: str = None,
    duration_ms: int = None,
) -> None:
    """Write a single activity log entry.  Never raises."""
    if not db_path:
        return
    try:
        conn = get_safe_connection(db_path)
        cursor = conn.cursor()
        _ensure_table(cursor)
        cursor.execute(
            """
            INSERT INTO activity_logs
                (event_type, category, severity, title, detail,
                 user_id, entity_type, entity_id, entity_name,
                 meta, ip_address, session_id, duration_ms,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (
                event_type,
                category,
                severity,
                title,
                detail,
                str(user_id) if user_id is not None else None,
                entity_type,
                str(entity_id) if entity_id is not None else None,
                entity_name,
                json.dumps(meta, default=str) if meta else None,
                ip_address,
                session_id,
                duration_ms,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # pragma: no cover
        logger.warning("activity_logger.log_event failed: %s", exc)


# ---------------------------------------------------------------------------
# Helper: back-fill existing audit_trail rows into activity_logs
# ---------------------------------------------------------------------------
def migrate_audit_trail(db_path: str) -> int:
    """
    One-time migration: copy existing audit_trail rows that have not yet
    been copied to activity_logs.  Returns number of rows migrated.
    """
    if not db_path:
        return 0
    migrated = 0
    try:
        conn = get_safe_connection(db_path)
        cursor = conn.cursor()
        _ensure_table(cursor)

        # Check audit_trail exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_trail'"
        )
        if not cursor.fetchone():
            conn.close()
            return 0

        # Load already migrated legacy audit trail IDs
        cursor.execute("SELECT meta FROM activity_logs WHERE meta LIKE '%legacy_audit_id%'")
        migrated_ids = set()
        for r in cursor.fetchall():
            try:
                if r['meta']:
                    m = json.loads(r['meta'])
                    if 'legacy_audit_id' in m:
                        migrated_ids.add(int(m['legacy_audit_id']))
            except Exception:
                pass

        cursor.execute(
            """
            SELECT at.id, at.batch_id, at.action, at.method, at.timestamp,
                   at.input_data, at.output_data, at.confidence, at.user_id,
                   ib.filename
            FROM audit_trail at
            LEFT JOIN inventory_batches ib ON at.batch_id = ib.id
            WHERE (at.is_deleted IS NULL OR at.is_deleted = 0)
            ORDER BY at.timestamp ASC
            """
        )
        rows = cursor.fetchall()
        for row in rows:
            # Skip if already migrated
            if row["id"] in migrated_ids:
                continue

            category = _map_action_category(row["action"] or "")
            severity = "warning" if category == "alert" else "info"
            meta = {"legacy_audit_id": row["id"]}
            if row["input_data"]:
                try:
                    meta["input"] = json.loads(row["input_data"])
                except Exception:
                    meta["input"] = row["input_data"]
            if row["output_data"]:
                try:
                    meta["output"] = json.loads(row["output_data"])
                except Exception:
                    meta["output"] = row["output_data"]
            conf_val = 0.0
            if row["confidence"] is not None:
                try:
                    conf_val = float(row["confidence"])
                except (ValueError, TypeError):
                    pass
            if row["confidence"]:
                meta["confidence"] = round(conf_val * 100)
            if row["method"]:
                meta["method"] = row["method"]
            if row["batch_id"]:
                meta["batch_id"] = row["batch_id"]

            cursor.execute(
                """
                INSERT INTO activity_logs
                    (event_type, category, severity, title, detail,
                     user_id, entity_type, entity_id, entity_name,
                     meta, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE(?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now')))
                """,
                (
                    row["action"] or "system",
                    category,
                    severity,
                    _map_action_title(row["action"] or "", row["filename"]),
                    f"Method: {row['method'] or 'N/A'} | Confidence: {int(conf_val * 100)}%",
                    row["user_id"],
                    "batch",
                    row["batch_id"],
                    row["filename"],
                    json.dumps(meta) if meta else None,
                    row["timestamp"],
                ),
            )
            migrated += 1

        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("migrate_audit_trail failed: %s", exc)
    return migrated


def _map_action_category(action: str) -> str:
    if action in ("upload", "import_batch_to_warehouse"):
        return "import"
    if action in ("match", "manual_review", "column_map", "analysis"):
        return "analysis"
    if action in ("reactivity_block", "safety_alert"):
        return "alert"
    if action in ("manual_edit", "remove_chemical", "unplace_chemical"):
        return "edit"
    if action in ("place_chemical", "auto_arrange"):
        return "warehouse"
    return "system"


def _map_action_title(action: str, filename=None) -> str:
    mapping = {
        "upload":                   f"File uploaded: {filename or 'unknown'}",
        "match":                    "Chemical matched automatically",
        "manual_review":            "Manual review completed",
        "column_map":               "Column mapping detected",
        "manual_edit":              "Chemical record edited",
        "import_batch_to_warehouse":"Batch imported to warehouse",
        "place_chemical":           "Chemical placed in section",
        "unplace_chemical":         "Chemical removed from section",
        "remove_chemical":          "Chemical removed from warehouse",
        "auto_arrange":             "AI auto-arrange executed",
        "reactivity_block":         "Reactive placement blocked",
        "safety_alert":             "Safety alert triggered",
    }
    return mapping.get(action, action.replace("_", " ").title() if action else "System event")
