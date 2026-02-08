"""
pipeline.py — ETL orchestrator.
Runs: Ingest → Clean → Match → Report in a background thread.
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime

from etl.ingest import read_file
from etl.clean import validate_row
from etl.match import ChemicalMatcher
from etl.report import generate_summary

logger = logging.getLogger(__name__)


def init_inventory_tables(user_db_path: str):
    """Create inventory tables in user.db if they don't exist."""
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_batches (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            total_rows  INTEGER DEFAULT 0,
            processed   INTEGER DEFAULT 0,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at DATETIME,
            summary_json TEXT,
            error_msg   TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_staging (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id     TEXT NOT NULL REFERENCES inventory_batches(id),
            row_index    INTEGER NOT NULL,
            raw_data     TEXT NOT NULL,
            cleaned_data TEXT,
            match_status TEXT DEFAULT 'pending',
            chemical_id  INTEGER,
            match_method TEXT,
            confidence   REAL DEFAULT 0,
            quality_score INTEGER DEFAULT 0,
            issues       TEXT
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_staging_batch
        ON inventory_staging(batch_id)
    """)

    conn.commit()
    conn.close()
    logger.info("Inventory tables initialized in user.db")


def create_batch(user_db_path: str, filename: str) -> str:
    """Create a new batch record and return its UUID."""
    batch_id = str(uuid.uuid4())
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO inventory_batches (id, filename, status) VALUES (?, ?, 'pending')",
        (batch_id, filename)
    )
    conn.commit()
    conn.close()
    return batch_id


def get_batch_status(user_db_path: str, batch_id: str) -> dict:
    """Get current status of a batch for polling."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_batches WHERE id = ?", (batch_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {'error': 'Batch not found'}

    result = {
        'id': row['id'],
        'filename': row['filename'],
        'status': row['status'],
        'total_rows': row['total_rows'],
        'processed': row['processed'],
        'created_at': row['created_at'],
        'completed_at': row['completed_at'],
        'error_msg': row['error_msg'],
    }

    if row['summary_json']:
        try:
            result['summary'] = json.loads(row['summary_json'])
        except (json.JSONDecodeError, TypeError):
            result['summary'] = None
    else:
        result['summary'] = None

    return result


def run_async(user_db_path: str, chemicals_db_path: str,
              batch_id: str, filepath: str):
    """Start the pipeline in a background thread."""
    t = threading.Thread(
        target=_run_pipeline,
        args=(user_db_path, chemicals_db_path, batch_id, filepath),
        daemon=True
    )
    t.start()
    return t


def _run_pipeline(user_db_path: str, chemicals_db_path: str,
                  batch_id: str, filepath: str):
    """
    Main pipeline logic (runs in background thread).
    Ingest → Clean → Match → Report.
    """
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()

    try:
        # ── Mark as processing ──
        cursor.execute(
            "UPDATE inventory_batches SET status = 'processing' WHERE id = ?",
            (batch_id,)
        )
        conn.commit()

        # ── Step 1: Ingest ──
        logger.info(f"[Batch {batch_id[:8]}] Ingesting file: {filepath}")
        df = read_file(filepath)
        total = len(df)

        cursor.execute(
            "UPDATE inventory_batches SET total_rows = ? WHERE id = ?",
            (total, batch_id)
        )
        conn.commit()

        # ── Step 2: Initialize matcher ──
        matcher = ChemicalMatcher(chemicals_db_path)

        # ── Step 3: Process each row ──
        for idx, (_, row) in enumerate(df.iterrows()):
            row_dict = {k: (str(v) if v else '') for k, v in row.to_dict().items()}

            # Clean
            clean_result = validate_row(row_dict)
            cleaned = clean_result['cleaned']
            issues = clean_result['issues']
            quality_score = clean_result['quality_score']

            # Match
            match_result = matcher.match(cleaned)

            # Determine final status
            match_status = match_result['match_status']
            if issues and any('Invalid CAS' in i for i in issues):
                # CAS was provided but invalid — flag even if name matched
                if match_status == 'matched' and match_result['match_method'] != 'exact_cas':
                    quality_score = min(quality_score, 70)

            # Insert staging row
            cursor.execute("""
                INSERT INTO inventory_staging
                    (batch_id, row_index, raw_data, cleaned_data, match_status,
                     chemical_id, match_method, confidence, quality_score, issues)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                batch_id,
                idx + 1,
                json.dumps(row_dict),
                json.dumps(cleaned, default=str),
                match_status,
                match_result['chemical_id'],
                match_result['match_method'],
                match_result['confidence'],
                quality_score,
                json.dumps(issues),
            ))

            # Update progress
            cursor.execute(
                "UPDATE inventory_batches SET processed = ? WHERE id = ?",
                (idx + 1, batch_id)
            )
            conn.commit()

        # ── Step 4: Generate report ──
        logger.info(f"[Batch {batch_id[:8]}] Generating report...")
        summary = generate_summary(user_db_path, batch_id)

        cursor.execute("""
            UPDATE inventory_batches
            SET status = 'completed', completed_at = ?, summary_json = ?
            WHERE id = ?
        """, (datetime.utcnow().isoformat(), json.dumps(summary), batch_id))
        conn.commit()

        logger.info(f"[Batch {batch_id[:8]}] Completed: {summary['matched']}/{summary['total_rows']} matched")

    except Exception as e:
        logger.error(f"[Batch {batch_id[:8]}] Pipeline error: {e}", exc_info=True)
        cursor.execute(
            "UPDATE inventory_batches SET status = 'error', error_msg = ? WHERE id = ?",
            (str(e), batch_id)
        )
        conn.commit()

    finally:
        conn.close()
