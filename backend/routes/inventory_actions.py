"""
inventory_actions.py — Interactive inventory row actions for Phase 2.
Provides add/edit/delete APIs on staged inventory rows before analysis.
"""

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

inventory_actions_bp = Blueprint('inventory_actions', __name__)

_QUANTITY_REGEX = re.compile(r'^\s*\d+(?:\.\d+)?\s*$')


def _row_version_hash(row: sqlite3.Row) -> str:
    payload = f"{row['id']}|{row['cleaned_data'] or ''}|{row['match_status'] or ''}|{row['chemical_id'] or ''}|{row['quality_score'] or ''}|{row['confidence'] or ''}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _validate_quantity(quantity) -> bool:
    if quantity is None:
        return True
    q = str(quantity).strip()
    if not q:
        return True
    return bool(_QUANTITY_REGEX.match(q))


def _fetch_chemical(chemical_id: int):
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn = sqlite3.connect(chemicals_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.id, c.name, c.formulas,
               (SELECT cas_id FROM chemical_cas cc WHERE cc.chem_id = c.id ORDER BY sort LIMIT 1) AS cas_number
        FROM chemicals c
        WHERE c.id = ?
        """,
        (chemical_id,)
    )
    chem = cursor.fetchone()
    conn.close()
    return chem


@inventory_actions_bp.route('/api/inventory/edit', methods=['POST'])
def edit_inventory_row():
    """Edit a staged inventory row safely with optimistic concurrency check."""
    try:
        data = request.get_json(silent=True) or {}
        batch_id = (data.get('batch_id') or '').strip()
        staging_id = data.get('staging_id')
        row_version = (data.get('row_version') or '').strip()

        if not batch_id or not staging_id:
            return jsonify({'error': 'batch_id and staging_id are required'}), 400

        quantity = data.get('quantity', '')
        unit = (data.get('unit') or '').strip()
        location = (data.get('location') or '').strip()
        notes = (data.get('notes') or '').strip()

        if not _validate_quantity(quantity):
            return jsonify({'error': 'Quantity must be numeric (e.g., 10 or 10.5)'}), 400

        user_db = current_app.config['USER_DB_PATH']
        conn = sqlite3.connect(user_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, row_index, cleaned_data, raw_data, match_status, chemical_id, quality_score, confidence
            FROM inventory_staging
            WHERE id = ? AND batch_id = ?
            """,
            (staging_id, batch_id)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Row not found'}), 404

        current_version = _row_version_hash(row)
        if row_version and row_version != current_version:
            conn.close()
            return jsonify({'error': 'Row changed by another action. Please refresh and retry.', 'code': 'VERSION_CONFLICT'}), 409

        cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
        raw = json.loads(row['raw_data']) if row['raw_data'] else {}

        selected_chemical_id = data.get('chemical_id')
        if selected_chemical_id:
            try:
                selected_chemical_id = int(selected_chemical_id)
            except (TypeError, ValueError):
                conn.close()
                return jsonify({'error': 'chemical_id must be integer'}), 400

            chem = _fetch_chemical(selected_chemical_id)
            if not chem:
                conn.close()
                return jsonify({'error': 'Selected chemical not found in CAMEO DB'}), 400

            cleaned['name'] = chem['name']
            cleaned['cas'] = chem['cas_number'] or cleaned.get('cas', '')
            new_match_status = 'MATCHED'
            new_match_method = 'manual_edit'
            new_confidence = 1.0
            new_chemical_id = selected_chemical_id
        else:
            chem = None
            new_match_status = row['match_status']
            new_match_method = 'manual_edit'
            new_confidence = row['confidence']
            new_chemical_id = row['chemical_id']

        if quantity not in (None, ''):
            cleaned['quantity'] = str(quantity).strip()
        if unit:
            cleaned['unit'] = unit
        if location:
            cleaned['location'] = location
        cleaned['notes'] = notes

        if 'quantity' in raw and quantity not in (None, ''):
            raw['quantity'] = str(quantity).strip()
        if 'unit' in raw and unit:
            raw['unit'] = unit
        if 'location' in raw and location:
            raw['location'] = location
        raw['notes'] = notes

        cursor.execute(
            """
            UPDATE inventory_staging
            SET cleaned_data = ?, raw_data = ?, match_status = ?, match_method = ?,
                confidence = ?, chemical_id = ?
            WHERE id = ? AND batch_id = ?
            """,
            (
                json.dumps(cleaned, default=str),
                json.dumps(raw, default=str),
                new_match_status,
                new_match_method,
                new_confidence,
                new_chemical_id,
                staging_id,
                batch_id,
            )
        )

        cursor.execute(
            """
            INSERT INTO audit_trail
                (batch_id, row_index, action, input_data, output_data, confidence, method, timestamp, user_id)
            VALUES (?, ?, 'manual_edit', ?, ?, ?, ?, ?, 'human')
            """,
            (
                batch_id,
                row['row_index'],
                json.dumps({'staging_id': staging_id}),
                json.dumps({'chemical_id': new_chemical_id, 'quantity': cleaned.get('quantity', ''), 'location': cleaned.get('location', '')}),
                new_confidence,
                new_match_method,
                datetime.now(timezone.utc).isoformat(),
            )
        )

        conn.commit()

        cursor.execute(
            """
            SELECT id, row_index, cleaned_data, raw_data, match_status, chemical_id, confidence, quality_score, issues
            FROM inventory_staging
            WHERE id = ?
            """,
            (staging_id,)
        )
        updated = cursor.fetchone()
        conn.close()

        cleaned_updated = json.loads(updated['cleaned_data']) if updated['cleaned_data'] else {}
        raw_updated = json.loads(updated['raw_data']) if updated['raw_data'] else {}
        issues = json.loads(updated['issues']) if updated['issues'] else []

        response_row = {
            'staging_id': updated['id'],
            'row_index': updated['row_index'],
            'chemical_id': updated['chemical_id'],
            'name': cleaned_updated.get('name') or raw_updated.get('name', ''),
            'cas': cleaned_updated.get('cas') or raw_updated.get('cas', ''),
            'quantity': cleaned_updated.get('quantity') or raw_updated.get('quantity', ''),
            'unit': cleaned_updated.get('unit') or raw_updated.get('unit', ''),
            'location': cleaned_updated.get('location') or raw_updated.get('location', ''),
            'notes': cleaned_updated.get('notes', ''),
            'match_status': updated['match_status'],
            'confidence': updated['confidence'],
            'quality_score': updated['quality_score'],
            'issues': issues,
            'row_version': _row_version_hash(updated),
        }

        logger.info("[Batch %s] Row %s edited successfully", batch_id[:8], updated['row_index'])
        return jsonify({'success': True, 'row': response_row})

    except Exception as e:
        logger.error("edit_inventory_row failed: %s", e, exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@inventory_actions_bp.route('/api/inventory/delete/<int:staging_id>', methods=['DELETE'])
def delete_inventory_row(staging_id):
    """Delete a staged row after frontend confirmation modal."""
    try:
        batch_id = (request.args.get('batch_id') or '').strip()
        if not batch_id:
            return jsonify({'error': 'batch_id query param is required'}), 400

        user_db = current_app.config['USER_DB_PATH']
        conn = sqlite3.connect(user_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT row_index, cleaned_data FROM inventory_staging WHERE id = ? AND batch_id = ?",
            (staging_id, batch_id)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Row not found'}), 404

        cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}

        cursor.execute("DELETE FROM review_queue WHERE staging_id = ?", (staging_id,))
        cursor.execute("DELETE FROM inventory_staging WHERE id = ? AND batch_id = ?", (staging_id, batch_id))

        cursor.execute(
            """
            INSERT INTO audit_trail
                (batch_id, row_index, action, input_data, output_data, confidence, method, timestamp, user_id)
            VALUES (?, ?, 'manual_delete', ?, ?, 1.0, 'manual_delete', ?, 'human')
            """,
            (
                batch_id,
                row['row_index'],
                json.dumps({'staging_id': staging_id, 'name': cleaned.get('name', '')}),
                json.dumps({'deleted': True}),
                datetime.now(timezone.utc).isoformat(),
            )
        )

        conn.commit()
        conn.close()

        logger.info("[Batch %s] Row %s deleted (staging_id=%s)", batch_id[:8], row['row_index'], staging_id)
        return jsonify({'success': True, 'deleted_staging_id': staging_id})

    except Exception as e:
        logger.error("delete_inventory_row failed: %s", e, exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@inventory_actions_bp.route('/api/inventory/add', methods=['POST'])
def add_inventory_row():
    """Add a new row to staged inventory using selected chemical_id."""
    try:
        data = request.get_json(silent=True) or {}
        batch_id = (data.get('batch_id') or '').strip()
        chemical_id = data.get('chemical_id')
        quantity = data.get('quantity', '')
        unit = (data.get('unit') or '').strip()
        location = (data.get('location') or '').strip()
        notes = (data.get('notes') or '').strip()

        if not batch_id or not chemical_id:
            return jsonify({'error': 'batch_id and chemical_id are required'}), 400

        try:
            chemical_id = int(chemical_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'chemical_id must be integer'}), 400

        if not _validate_quantity(quantity):
            return jsonify({'error': 'Quantity must be numeric (e.g., 10 or 10.5)'}), 400

        chem = _fetch_chemical(chemical_id)
        if not chem:
            return jsonify({'error': 'chemical_id does not exist in CAMEO DB'}), 400

        user_db = current_app.config['USER_DB_PATH']
        conn = sqlite3.connect(user_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT 1 FROM inventory_batches WHERE id = ?", (batch_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'error': 'batch_id not found'}), 404

        cursor.execute(
            "SELECT MAX(row_index) AS max_row FROM inventory_staging WHERE batch_id = ?",
            (batch_id,)
        )
        max_row = cursor.fetchone()['max_row'] or 0
        next_row_index = max_row + 1

        cursor.execute(
            """
            SELECT row_index FROM inventory_staging
            WHERE batch_id = ? AND chemical_id = ?
            ORDER BY row_index LIMIT 1
            """,
            (batch_id, chemical_id)
        )
        duplicate = cursor.fetchone()
        duplicate_warning = None
        if duplicate:
            duplicate_warning = f"This chemical already exists in row {duplicate['row_index']}"

        cleaned = {
            'name': chem['name'],
            'cas': chem['cas_number'] or '',
            'quantity': str(quantity).strip() if quantity not in (None, '') else '',
            'unit': unit,
            'location': location,
            'notes': notes,
        }

        raw = dict(cleaned)
        issues = [f"WARNING: {duplicate_warning}"] if duplicate_warning else []

        cursor.execute(
            """
            INSERT INTO inventory_staging
                (batch_id, row_index, raw_data, cleaned_data, match_status,
                 chemical_id, match_method, confidence, quality_score, issues,
                 suggestions, signals_json, conflicts_json, field_swaps_json)
            VALUES (?, ?, ?, ?, 'MATCHED', ?, 'manual_add', 1.0, 100, ?, '[]', '[]', '[]', '[]')
            """,
            (
                batch_id,
                next_row_index,
                json.dumps(raw, default=str),
                json.dumps(cleaned, default=str),
                chemical_id,
                json.dumps(issues),
            )
        )
        staging_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO audit_trail
                (batch_id, row_index, action, input_data, output_data, confidence, method, timestamp, user_id)
            VALUES (?, ?, 'manual_add', ?, ?, 1.0, 'manual_add', ?, 'human')
            """,
            (
                batch_id,
                next_row_index,
                json.dumps({'chemical_id': chemical_id}),
                json.dumps({'staging_id': staging_id, 'name': chem['name']}),
                datetime.now(timezone.utc).isoformat(),
            )
        )

        conn.commit()

        cursor.execute(
            """
            SELECT id, row_index, cleaned_data, raw_data, match_status, chemical_id, confidence, quality_score, issues
            FROM inventory_staging
            WHERE id = ?
            """,
            (staging_id,)
        )
        row = cursor.fetchone()
        conn.close()

        cleaned_row = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
        raw_row = json.loads(row['raw_data']) if row['raw_data'] else {}
        row_issues = json.loads(row['issues']) if row['issues'] else []

        response_row = {
            'staging_id': row['id'],
            'row_index': row['row_index'],
            'chemical_id': row['chemical_id'],
            'name': cleaned_row.get('name') or raw_row.get('name', ''),
            'cas': cleaned_row.get('cas') or raw_row.get('cas', ''),
            'quantity': cleaned_row.get('quantity') or raw_row.get('quantity', ''),
            'unit': cleaned_row.get('unit') or raw_row.get('unit', ''),
            'location': cleaned_row.get('location') or raw_row.get('location', ''),
            'notes': cleaned_row.get('notes', ''),
            'match_status': row['match_status'],
            'confidence': row['confidence'],
            'quality_score': row['quality_score'],
            'issues': row_issues,
            'row_version': _row_version_hash(row),
        }

        logger.info("[Batch %s] Added chemical %s as row %s", batch_id[:8], chemical_id, next_row_index)
        return jsonify({'success': True, 'row': response_row, 'warning': duplicate_warning})

    except Exception as e:
        logger.error("add_inventory_row failed: %s", e, exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500
