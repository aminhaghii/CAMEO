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

from flask import Blueprint, current_app, jsonify, request, g
from auth.decorators import login_required, csrf_protect, viewer_readonly
from db_utils import get_safe_connection
from activity_logger import log_event

logger = logging.getLogger(__name__)

inventory_actions_bp = Blueprint('inventory_actions', __name__)


@inventory_actions_bp.before_request
def _enforce_tenant_context():
    """Fail-closed guard: reject ALL inventory_actions requests without a tenant DB context."""
    if not getattr(g, 'user', None):
        return None
    tenant_db = getattr(g, 'tenant_db_path', None)
    if not tenant_db:
        return jsonify({
            'error': 'Tenant context required. Super Admins cannot access tenant-specific routes directly.',
            'code': 'NO_TENANT_CONTEXT'
        }), 403


_QUANTITY_REGEX = re.compile(r'^\s*\d+(?:\.\d+)?\s*$')


def _get_db_path() -> str:
    tenant_db = getattr(g, 'tenant_db_path', None)
    if not tenant_db:
        from flask import abort
        abort(403, description="Tenant context required. Super Admins cannot access tenant-specific routes directly.")
    return tenant_db


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
    conn = get_safe_connection(chemicals_db, readonly=True)
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


def _propagate_to_warehouse(cursor, batch_id, old_chemical_id, new_chemical_id, chem, staging_id=None):
    """Update chemical_placements if this batch was already imported to warehouse.
    Called after staging edits that change chemical identity.
    old_chemical_id MUST be captured BEFORE the staging update."""
    if not chem:
        return

    chemicals_db = current_app.config['CHEMICALS_DB_PATH']

    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cur = conn_chem.cursor()
    cur.execute("SELECT name FROM chemicals WHERE id = ?", (new_chemical_id,))
    name_row = cur.fetchone()
    chem_name = name_row['name'] if name_row else chem.get('name', '')
    cur.execute("SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1", (new_chemical_id,))
    cas_row = cur.fetchone()
    cas_number = cas_row['cas_id'] if cas_row else ''
    conn_chem.close()

    conn_g = get_safe_connection(chemicals_db, readonly=True)
    cur_g = conn_g.cursor()
    cur_g.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (new_chemical_id,))
    groups = [r[0] for r in cur_g.fetchall()]
    conn_g.close()
    groups_json = json.dumps(groups)

    # Previously-unmatched row now has a chemical: INSERT a new placement.
    if old_chemical_id is None:
        if not staging_id:
            return
        cursor.execute(
            "SELECT DISTINCT warehouse_id FROM chemical_placements WHERE batch_id = ?",
            (batch_id,)
        )
        imported_warehouses = [r[0] for r in cursor.fetchall()]
        if not imported_warehouses:
            return

        cursor.execute("SELECT cleaned_data FROM inventory_staging WHERE id = ?", (staging_id,))
        st_row = cursor.fetchone()
        qty = None
        if st_row and st_row['cleaned_data']:
            try:
                cd = json.loads(st_row['cleaned_data'])
                if 'quantity' not in cd or cd.get('quantity') in (None, ''):
                    logger.warning(
                        f"Staging row {staging_id} (batch {batch_id}) has no quantity; "
                        f"defaulting to 1.0 for warehouse placement"
                    )
                qty_str = cd.get('quantity', '1.0') or '1.0'
                unit_str = cd.get('unit', 'kg').lower()
                CONTAINER_KG = {
                    'drum': 200, 'drums': 200, 'cylinder': 50, 'cylinders': 50,
                    'bottle': 2, 'bottles': 2, 'jug': 4, 'jugs': 4,
                    'container': 10, 'containers': 10, 'tank': 500, 'tanks': 500,
                    'pail': 20, 'pails': 20, 'bag': 25, 'bags': 25,
                    'sack': 50, 'sacks': 50, 'tote': 1000, 'totes': 1000,
                    'keg': 60, 'kegs': 60,
                }
                qty = float(qty_str)
                if unit_str in ('g', 'grams', 'gr'):
                    qty /= 1000.0
                elif unit_str in ('lb', 'lbs', 'pounds'):
                    qty *= 0.453592
                elif unit_str in ('oz', 'ounces'):
                    qty *= 0.0283495
                elif unit_str in ('ton', 'tons'):
                    qty *= 907.185
                elif unit_str in ('mt', 'metric ton', 'metric tons', 'tonnes'):
                    qty *= 1000.0
                elif unit_str in CONTAINER_KG:
                    qty *= CONTAINER_KG[unit_str]
            except Exception as e:
                logger.warning(
                    f"Staging row {staging_id} (batch {batch_id}) has an unparsable "
                    f"quantity/unit; storing NULL quantity_kg: {e}"
                )
                qty = None

        for wh_id in imported_warehouses:
            cursor.execute(
                """INSERT OR IGNORE INTO chemical_placements
                    (warehouse_id, section_id, chemical_id, chemical_name, cas_number,
                     quantity_kg, reactive_groups, status, batch_id, staging_row_id)
                   VALUES (?, NULL, ?, ?, ?, ?, ?, 'placed', ?, ?)""",
                (wh_id, new_chemical_id, chem_name, cas_number, qty, groups_json, batch_id, staging_id)
            )
            if cursor.rowcount:
                logger.info(
                    "Added new placement to warehouse %d: %s (%s kg, batch=%s)",
                    wh_id, chem_name, qty, batch_id[:8]
                )
        return

    # If old == new, nothing to propagate
    if old_chemical_id == new_chemical_id:
        return

    # Update the exact placement row identified by (batch_id, staging_row_id).
    if staging_id:
        cursor.execute(
            """UPDATE chemical_placements
               SET chemical_id = ?, chemical_name = ?, cas_number = ?, reactive_groups = ?
               WHERE batch_id = ? AND staging_row_id = ?""",
            (new_chemical_id, chem_name, cas_number, groups_json, batch_id, staging_id)
        )
    else:
        # Fallback for legacy rows without staging_row_id (pre-migration data).
        cursor.execute(
            """UPDATE chemical_placements
               SET chemical_id = ?, chemical_name = ?, cas_number = ?, reactive_groups = ?
               WHERE chemical_id = ? AND batch_id = ?""",
            (new_chemical_id, chem_name, cas_number, groups_json, old_chemical_id, batch_id)
        )
    if cursor.rowcount > 0:
        logger.info(
            "Propagated chemical change to warehouse: %d placement(s) updated "
            "(old_id=%s -> new_id=%s, batch=%s)",
            cursor.rowcount, old_chemical_id, new_chemical_id, batch_id[:8]
        )


def _propagate_quantity_to_warehouse(cursor, batch_id, staging_id, new_quantity, new_unit):
    """Update quantity_kg in chemical_placements when inventory quantity changes.

    Fix 1.2: uses (batch_id, staging_row_id) — the exact placement row —
    instead of a broken string-prefix match that returned zero rows.
    """
    if new_quantity is None or staging_id is None:
        return

    CONTAINER_KG = {
        'drum': 200, 'drums': 200, 'cylinder': 50, 'cylinders': 50,
        'bottle': 2, 'bottles': 2, 'jug': 4, 'jugs': 4,
        'container': 10, 'containers': 10, 'tank': 500, 'tanks': 500,
        'pail': 20, 'pails': 20, 'bag': 25, 'bags': 25,
        'sack': 50, 'sacks': 50, 'tote': 1000, 'totes': 1000,
        'keg': 60, 'kegs': 60,
    }
    try:
        qty = float(new_quantity)
        unit_lower = (new_unit or '').lower()
        if unit_lower in ('g', 'grams', 'gr'):
            qty /= 1000.0
        elif unit_lower in ('lb', 'lbs', 'pounds'):
            qty *= 0.453592
        elif unit_lower in ('oz', 'ounces'):
            qty *= 0.0283495
        elif unit_lower in ('ton', 'tons'):
            qty *= 907.185
        elif unit_lower in ('mt', 'metric ton', 'metric tons', 'tonnes'):
            qty *= 1000.0
        elif unit_lower in CONTAINER_KG:
            qty *= CONTAINER_KG[unit_lower]
    except (ValueError, TypeError):
        qty = None

    if qty is None:
        return

    cursor.execute(
        """UPDATE chemical_placements
           SET quantity_kg = ?
           WHERE batch_id = ? AND staging_row_id = ?""",
        (qty, batch_id, staging_id)
    )
    if cursor.rowcount > 0:
        logger.info(
            "Propagated quantity change to warehouse: %d placement(s) updated "
            "(staging_row_id=%s, qty=%.1f kg, batch=%s)",
            cursor.rowcount, staging_id, qty, batch_id[:8]
        )
    else:
        logger.debug(
            "No warehouse placement found for staging_row_id=%s batch=%s — "
            "row may not be imported to warehouse yet.",
            staging_id, batch_id[:8]
        )


@inventory_actions_bp.route('/api/inventory/edit', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
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

        user_db = _get_db_path()
        conn = get_safe_connection(user_db)
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
        # Capture OLD chemical_id before any changes
        old_chem_id = row['chemical_id'] if 'chemical_id' in row.keys() else None

        if selected_chemical_id:
            try:
                selected_chemical_id = int(selected_chemical_id)
            except (TypeError, ValueError):
                conn.close()
                return jsonify({'error': 'chemical_id must be integer'}), 400

            # Fix 1.3: block identity change if this staging row is already in the warehouse.
            if selected_chemical_id != old_chem_id:
                cursor.execute(
                    "SELECT id FROM chemical_placements WHERE batch_id = ? AND staging_row_id = ? LIMIT 1",
                    (batch_id, staging_id)
                )
                if cursor.fetchone():
                    conn.close()
                    return jsonify({
                        'error': (
                            'Cannot change the chemical identity of a row that is already '
                            'placed in the warehouse. Please remove it from the warehouse first.'
                        ),
                        'code': 'PLACED_IN_WAREHOUSE'
                    }), 400

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

        # ── Propagate to warehouse if batch was already imported ──
        # Capture old chemical_id BEFORE the staging update was committed
        if new_chemical_id and new_chemical_id != old_chem_id:
            _propagate_to_warehouse(cursor, batch_id, old_chem_id, new_chemical_id, chem, staging_id)
            conn.commit()

        # ── Propagate quantity changes to warehouse ──
        if quantity not in (None, ''):
            _propagate_quantity_to_warehouse(cursor, batch_id, staging_id, quantity, unit)
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

        # Activity log
        _edited_name = ''
        try:
            _cleaned_tmp = json.loads(updated['cleaned_data']) if updated['cleaned_data'] else {}
            _edited_name = _cleaned_tmp.get('name', '')
        except Exception:
            pass
        _edit_title = f'Chemical record edited - {_edited_name}' if _edited_name else f'Chemical record edited - Row {updated["row_index"]}'
        log_event(
            db_path=_get_db_path(),
            event_type='manual_edit',
            category='edit',
            severity='info',
            title=_edit_title,
            detail=f'Batch: {batch_id[:8]}... | Staging row: {staging_id}',
            user_id=getattr(g, 'user', {}).get('id') if hasattr(g, 'user') and g.user else 'human',
            entity_type='batch',
            entity_id=batch_id,
            entity_name=_edited_name,
            meta={'staging_id': staging_id, 'chemical_id': new_chemical_id},
        )

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
@login_required
@viewer_readonly
@csrf_protect
def delete_inventory_row(staging_id):
    """Delete a staged row after frontend confirmation modal."""
    try:
        batch_id = (request.args.get('batch_id') or '').strip()
        if not batch_id:
            return jsonify({'error': 'batch_id query param is required'}), 400

        user_db = _get_db_path()
        conn = get_safe_connection(user_db)
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
        cursor.execute(
            "DELETE FROM chemical_placements WHERE batch_id = ? AND staging_row_id = ?",
            (batch_id, staging_id)
        )
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

        cursor.execute(
            "UPDATE inventory_batches SET total_rows = CASE WHEN total_rows > 0 THEN total_rows - 1 ELSE 0 END WHERE id = ?",
            (batch_id,)
        )

        conn.commit()
        conn.close()

        _uid = g.user.get('id') if (hasattr(g, 'user') and g.user) else None
        log_event(
            db_path=user_db,
            event_type='manual_delete',
            category='edit',
            severity='info',
            title=f"Chemical record deleted - {cleaned.get('name', 'Row ' + str(row['row_index']))}",
            detail=f"Batch: {batch_id[:8]}... | Staging row: {staging_id}",
            user_id=_uid,
            entity_type='batch',
            entity_id=batch_id,
            entity_name=cleaned.get('name'),
            meta={'staging_id': staging_id},
        )

        logger.info("[Batch %s] Row %s deleted (staging_id=%s)", batch_id[:8], row['row_index'], staging_id)
        return jsonify({'success': True, 'deleted_staging_id': staging_id})

    except Exception as e:
        logger.error("delete_inventory_row failed: %s", e, exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@inventory_actions_bp.route('/api/inventory/add', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
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

        user_db = _get_db_path()
        conn = get_safe_connection(user_db)
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

        cursor.execute(
            "UPDATE inventory_batches SET total_rows = total_rows + 1 WHERE id = ?",
            (batch_id,)
        )
        # Propagate manually added chemical to warehouse if the batch has already been imported
        _propagate_to_warehouse(cursor, batch_id, None, chemical_id, chem, staging_id)

        conn.commit()

        _uid = g.user.get('id') if (hasattr(g, 'user') and g.user) else None
        log_event(
            db_path=user_db,
            event_type='manual_add',
            category='edit',
            severity='info',
            title=f"Manual chemical added - {chem['name']}",
            detail=f"Batch: {batch_id[:8]}... | Row index: {next_row_index} | Staging ID: {staging_id}",
            user_id=_uid,
            entity_type='batch',
            entity_id=batch_id,
            entity_name=chem['name'],
            meta={'staging_id': staging_id, 'chemical_id': chemical_id},
        )

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
