"""
pipeline.py — ETL orchestrator (v4).
Runs: Layer 1 (Ingest) → Layer 2 (Column Map) → Layer 3 (Clean) →
      Layer 4 (Match) → Layer 5 (Validate & Report) in a background thread.
Supports human-in-the-loop confirmation for REVIEW_REQUIRED rows.
Stores per-row signals, conflicts, field-swap diagnostics, and audit trail.

Never crashes — all errors are caught and stored in batch status.
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime

from etl.ingest import read_file
from etl.schema import map_columns
from etl.clean import validate_row
from etl.match import ChemicalMatcher
from etl.report import generate_summary
from etl.models import MatchResult

logger = logging.getLogger(__name__)


def init_inventory_tables(user_db_path: str):
    """Create inventory tables in user.db if they don't exist (Layer 5 included)."""
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_batches (
            id              TEXT PRIMARY KEY,
            filename        TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            total_rows      INTEGER DEFAULT 0,
            processed       INTEGER DEFAULT 0,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at    DATETIME,
            summary_json    TEXT,
            error_msg       TEXT,
            ingestion_meta  TEXT,
            column_mapping  TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_staging (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id         TEXT NOT NULL REFERENCES inventory_batches(id),
            row_index        INTEGER NOT NULL,
            raw_data         TEXT NOT NULL,
            cleaned_data     TEXT,
            match_status     TEXT DEFAULT 'pending',
            chemical_id      INTEGER,
            match_method     TEXT,
            confidence       REAL DEFAULT 0,
            quality_score    INTEGER DEFAULT 0,
            issues           TEXT,
            suggestions      TEXT,
            signals_json     TEXT,
            conflicts_json   TEXT,
            field_swaps_json TEXT
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_staging_batch
        ON inventory_staging(batch_id)
    """)

    # Layer 5: Review queue (prioritized)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS review_queue (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id            TEXT NOT NULL REFERENCES inventory_batches(id),
            staging_id          INTEGER NOT NULL REFERENCES inventory_staging(id),
            priority            TEXT DEFAULT 'medium',
            status              TEXT DEFAULT 'pending',
            input_data          TEXT,
            candidates          TEXT,
            assigned_to         TEXT,
            resolution_timestamp DATETIME,
            resolution          TEXT
        )
    """)

    # Layer 5: Audit trail
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id    TEXT NOT NULL,
            row_index   INTEGER,
            action      TEXT NOT NULL,
            input_data  TEXT,
            output_data TEXT,
            confidence  REAL,
            method      TEXT,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_id     TEXT
        )
    """)

    # Layer 5: Learning data (corrections for future improvement)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS learning_data (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            input_pattern       TEXT NOT NULL,
            context             TEXT,
            correct_chemical_id INTEGER,
            correction_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            corrected_by        TEXT,
            confidence_before   REAL,
            applied_to_rules    BOOLEAN DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Inventory tables initialized in user.db (v4 with Layer 5)")


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


def confirm_row(user_db_path: str, staging_id: int, chemical_id: int, chemical_name: str) -> bool:
    """
    Human-in-the-loop: confirm a REVIEW_REQUIRED or UNIDENTIFIED row
    by manually linking it to a chemical_id.
    """
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE inventory_staging
        SET chemical_id = ?, match_status = 'MATCHED',
            match_method = 'manual_confirm', confidence = 1.0
        WHERE id = ?
    """, (chemical_id, staging_id))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def get_review_rows(user_db_path: str, batch_id: str) -> list[dict]:
    """Get all rows that need human review for a batch."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, row_index, raw_data, cleaned_data, match_status,
               chemical_id, match_method, confidence, quality_score,
               issues, suggestions, signals_json, conflicts_json, field_swaps_json
        FROM inventory_staging
        WHERE batch_id = ? AND match_status IN ('REVIEW_REQUIRED', 'UNIDENTIFIED')
        ORDER BY row_index
    """, (batch_id,))
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        raw = {}
        cleaned = {}
        suggestions = []
        signals = []
        conflicts = []
        field_swaps = []
        try:
            raw = json.loads(row['raw_data']) if row['raw_data'] else {}
            cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
            suggestions = json.loads(row['suggestions']) if row['suggestions'] else []
            signals = json.loads(row['signals_json']) if row['signals_json'] else []
            conflicts = json.loads(row['conflicts_json']) if row['conflicts_json'] else []
            field_swaps = json.loads(row['field_swaps_json']) if row['field_swaps_json'] else []
        except (json.JSONDecodeError, TypeError):
            pass

        result.append({
            'staging_id': row['id'],
            'row_index': row['row_index'],
            'input_name': raw.get('name', cleaned.get('name', '')),
            'input_cas': raw.get('cas', ''),
            'match_status': row['match_status'],
            'match_method': row['match_method'],
            'confidence': row['confidence'],
            'quality_score': row['quality_score'],
            'chemical_id': row['chemical_id'],
            'issues': json.loads(row['issues']) if row['issues'] else [],
            'suggestions': suggestions,
            'signals': signals,
            'conflicts': conflicts,
            'field_swaps': field_swaps,
        })
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
    Layer 1 (Ingest) → Layer 2 (Column Map) → Layer 3 (Clean) →
    Layer 4 (Match) → Layer 5 (Validate & Report).

    Never crashes — all errors are caught and stored in batch status.
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

        # ══════════════════════════════════════════════
        #  LAYER 1: Smart File Ingestion
        # ══════════════════════════════════════════════
        logger.info(f"[Batch {batch_id[:8]}] Layer 1: Ingesting file: {filepath}")
        df = read_file(filepath)

        # Store ingestion metadata
        ingestion_meta = {}
        if hasattr(df, 'attrs') and 'ingestion_metadata' in df.attrs:
            meta = df.attrs['ingestion_metadata']
            ingestion_meta = {
                'status': meta.get('status', 'unknown'),
                'metadata': meta.get('metadata', {}),
                'confidence': meta.get('confidence', {}),
                'warnings': meta.get('warnings', []),
            }

        total = len(df)
        if total == 0:
            # File read failed or empty — store metadata and mark as error
            cursor.execute(
                "UPDATE inventory_batches SET status = 'error', error_msg = ?, ingestion_meta = ? WHERE id = ?",
                ("File is empty or could not be read", json.dumps(ingestion_meta, default=str), batch_id)
            )
            conn.commit()
            logger.warning(f"[Batch {batch_id[:8]}] Layer 1 failed: empty dataframe")
            return

        cursor.execute(
            "UPDATE inventory_batches SET total_rows = ?, ingestion_meta = ? WHERE id = ?",
            (total, json.dumps(ingestion_meta, default=str), batch_id)
        )
        conn.commit()
        logger.info(f"[Batch {batch_id[:8]}] Layer 1 complete: {total} rows ingested")

        # ══════════════════════════════════════════════
        #  LAYER 2: Intelligent Column Mapping
        # ══════════════════════════════════════════════
        logger.info(f"[Batch {batch_id[:8]}] Layer 2: Mapping columns...")
        col_result = map_columns(df)
        canonical_rename = col_result['canonical_rename']

        # Store column mapping in batch
        col_mapping_json = json.dumps(col_result, default=str)
        cursor.execute(
            "UPDATE inventory_batches SET column_mapping = ? WHERE id = ?",
            (col_mapping_json, batch_id)
        )
        conn.commit()

        # Rename columns to canonical names
        df = df.rename(columns=canonical_rename)

        logger.info(
            f"[Batch {batch_id[:8]}] Layer 2 complete: "
            f"found {col_result['critical_fields_found']}, "
            f"missing {col_result['missing_fields']}"
        )

        # ══════════════════════════════════════════════
        #  LAYER 3 + 4: Clean → Match per row
        # ══════════════════════════════════════════════
        logger.info(f"[Batch {batch_id[:8]}] Layer 3+4: Processing {total} rows...")
        matcher = ChemicalMatcher(chemicals_db_path)

        for idx, (_, row) in enumerate(df.iterrows()):
            try:
                row_dict = {k: (str(v) if v and str(v).strip() else '') for k, v in row.to_dict().items()}

                # ── Layer 3: Clean ──
                clean_result = validate_row(row_dict)
                cleaned = clean_result['cleaned']
                issues = clean_result['issues']
                quality_score = clean_result['quality_score']

                # ── Layer 4: Match ──
                match_result = matcher.match(cleaned)

                # ── Layer 5: Anti-Hallucination Validation ──
                try:
                    validated = MatchResult(
                        chemical_id=match_result.get('chemical_id'),
                        chemical_name=match_result.get('chemical_name'),
                        match_method=match_result.get('match_method', 'unmatched'),
                        confidence=match_result.get('confidence', 0.0),
                        match_status=match_result.get('match_status', 'UNIDENTIFIED'),
                        suggestions=[],
                    )
                except Exception as ve:
                    logger.warning(f"[Batch {batch_id[:8]}] Row {idx+1} validation error: {ve}")
                    validated = MatchResult()

                # Extra quality penalty for invalid CAS when matched by name
                match_status = validated.match_status
                if issues and any('Invalid CAS' in i for i in issues):
                    if match_status == 'MATCHED' and validated.match_method != 'exact_cas':
                        quality_score = min(quality_score, 70)

                # Serialize diagnostics
                suggestions_json = json.dumps(match_result.get('suggestions', []))
                signals_json = json.dumps(match_result.get('signals', []))
                conflicts_json = json.dumps(match_result.get('conflicts', []))
                field_swaps_json = json.dumps(match_result.get('field_swaps', []))

                # Merge field_swaps and conflicts into issues for visibility
                for fs in match_result.get('field_swaps', []):
                    issues.append(f"FIELD_SWAP: {fs}")
                for cf in match_result.get('conflicts', []):
                    issues.append(f"CONFLICT: {cf}")

                # Insert staging row
                cursor.execute("""
                    INSERT INTO inventory_staging
                        (batch_id, row_index, raw_data, cleaned_data, match_status,
                         chemical_id, match_method, confidence, quality_score, issues,
                         suggestions, signals_json, conflicts_json, field_swaps_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    batch_id,
                    idx + 1,
                    json.dumps(row_dict),
                    json.dumps(cleaned, default=str),
                    validated.match_status,
                    validated.chemical_id,
                    validated.match_method,
                    validated.confidence,
                    quality_score,
                    json.dumps(issues),
                    suggestions_json,
                    signals_json,
                    conflicts_json,
                    field_swaps_json,
                ))

                # ── Layer 5: Audit trail ──
                cursor.execute("""
                    INSERT INTO audit_trail
                        (batch_id, row_index, action, input_data, output_data,
                         confidence, method, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    batch_id,
                    idx + 1,
                    'auto_committed' if validated.match_status == 'MATCHED' else validated.match_status.lower(),
                    json.dumps({'name': cleaned.get('name', ''), 'cas': cleaned.get('cas', '')}),
                    json.dumps({'chemical_id': validated.chemical_id, 'chemical_name': validated.chemical_name}),
                    validated.confidence,
                    validated.match_method,
                    datetime.utcnow().isoformat(),
                ))

                # ── Layer 5: Add to review queue if needed ──
                if validated.match_status in ('REVIEW_REQUIRED', 'UNIDENTIFIED'):
                    staging_id = cursor.lastrowid
                    priority = _determine_review_priority(
                        validated.match_status, validated.confidence,
                        match_result.get('conflicts', []), issues
                    )
                    cursor.execute("""
                        INSERT INTO review_queue
                            (batch_id, staging_id, priority, status, input_data, candidates)
                        VALUES (?, ?, ?, 'pending', ?, ?)
                    """, (
                        batch_id,
                        staging_id,
                        priority,
                        json.dumps({'name': cleaned.get('name', ''), 'cas': cleaned.get('cas', '')}),
                        suggestions_json,
                    ))

            except Exception as row_err:
                # Never crash on a single row — log and continue
                logger.warning(f"[Batch {batch_id[:8]}] Row {idx+1} error: {row_err}")
                cursor.execute("""
                    INSERT INTO inventory_staging
                        (batch_id, row_index, raw_data, match_status, quality_score, issues)
                    VALUES (?, ?, ?, 'ERROR', 0, ?)
                """, (
                    batch_id, idx + 1,
                    json.dumps(row.to_dict(), default=str),
                    json.dumps([f"Processing error: {str(row_err)}"]),
                ))

            # Update progress every row
            cursor.execute(
                "UPDATE inventory_batches SET processed = ? WHERE id = ?",
                (idx + 1, batch_id)
            )
            if (idx + 1) % 10 == 0 or idx + 1 == total:
                conn.commit()

        conn.commit()

        # ══════════════════════════════════════════════
        #  LAYER 5: Generate Summary Report
        # ══════════════════════════════════════════════
        logger.info(f"[Batch {batch_id[:8]}] Layer 5: Generating report...")
        summary = generate_summary(user_db_path, batch_id)

        cursor.execute("""
            UPDATE inventory_batches
            SET status = 'completed', completed_at = ?, summary_json = ?
            WHERE id = ?
        """, (datetime.utcnow().isoformat(), json.dumps(summary), batch_id))
        conn.commit()

        logger.info(
            f"[Batch {batch_id[:8]}] Pipeline complete: "
            f"{summary.get('matched', 0)}/{summary.get('total_rows', 0)} matched, "
            f"{summary.get('review_required', 0)} review, "
            f"{summary.get('unidentified', 0)} unidentified"
        )

    except Exception as e:
        logger.error(f"[Batch {batch_id[:8]}] Pipeline error: {e}", exc_info=True)
        try:
            cursor.execute(
                "UPDATE inventory_batches SET status = 'error', error_msg = ? WHERE id = ?",
                (str(e)[:500], batch_id)
            )
            conn.commit()
        except Exception:
            pass

    finally:
        conn.close()


def _determine_review_priority(status: str, confidence: float,
                                conflicts: list, issues: list) -> str:
    """Determine review queue priority based on match result."""
    # Critical: conflicts or completely unidentified
    if conflicts:
        return 'critical'
    if status == 'UNIDENTIFIED':
        return 'high'
    # High: very low confidence
    if confidence < 0.65:
        return 'high'
    # Medium: moderate confidence
    if confidence < 0.80:
        return 'medium'
    # Low: high confidence but flagged
    return 'low'
