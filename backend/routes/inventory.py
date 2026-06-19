"""
inventory.py — Flask Blueprint for inventory ingestion API (ETL v4).
Routes: upload, status polling, review rows, confirm match, search chemicals,
        column mapping, review queue, learning feedback, admin page.
"""

import os
import re
import json
import hashlib
import sqlite3
import logging
from auth.decorators import login_required, csrf_protect, viewer_readonly, role_required
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, render_template, current_app, g
from db_utils import get_safe_connection
from activity_logger import log_event

from etl.pipeline import (
    init_inventory_tables, create_batch, get_batch_status,
    run_async, confirm_row, get_review_rows
)

logger = logging.getLogger(__name__)

inventory_bp = Blueprint('inventory', __name__)


@inventory_bp.before_request
def _enforce_tenant_context():
    """Fail-closed guard: reject ALL inventory requests without a tenant DB context."""
    # Auth-exempt paths (upload status polling, etc.) don't set g.user — skip those.
    if not getattr(g, 'user', None):
        return None
    tenant_db = getattr(g, 'tenant_db_path', None)
    if not tenant_db:
        return jsonify({
            'error': 'Tenant context required. Super Admins cannot access tenant-specific routes directly.',
            'code': 'NO_TENANT_CONTEXT'
        }), 403


UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'json', 'txt', 'tsv'}


def _get_db_path() -> str:
    tenant_db = getattr(g, 'tenant_db_path', None)
    if not tenant_db:
        from flask import abort
        abort(403, description="Tenant context required. Super Admins cannot access tenant-specific routes directly.")
    return tenant_db


def _propagate_to_warehouse(cursor, batch_id, old_chemical_id, new_chemical_id, chem, staging_id=None):
    """Update chemical_placements if this batch was already imported to warehouse.
    old_chemical_id MUST be captured BEFORE the staging update."""
    if not chem:
        return

    chemicals_db = current_app.config['CHEMICALS_DB_PATH']

    # Get new chemical info
    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cur = conn_chem.cursor()
    cur.execute("SELECT name FROM chemicals WHERE id = ?", (new_chemical_id,))
    name_row = cur.fetchone()
    chem_name = name_row['name'] if name_row else chem.get('name', '')

    cur.execute("SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1", (new_chemical_id,))
    cas_row = cur.fetchone()
    cas_number = cas_row['cas_id'] if cas_row else ''
    conn_chem.close()

    # Get reactive groups
    conn_g = get_safe_connection(chemicals_db, readonly=True)
    cur_g = conn_g.cursor()
    cur_g.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (new_chemical_id,))
    groups = [r[0] for r in cur_g.fetchall()]
    conn_g.close()
    groups_json = json.dumps(groups)

    # If old_chemical_id is None, the staging row was previously unmatched and
    # now has a chemical. If the batch was already imported, INSERT a new placement.
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
                cleaned_data = json.loads(st_row['cleaned_data'])
                qty_str = cleaned_data.get('quantity', '1.0')
                unit_str = cleaned_data.get('unit', 'kg').lower()
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
            except Exception:
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

    if old_chemical_id == new_chemical_id:
        return

    # Identity changed — update the exact placement row identified by (batch_id, staging_row_id).
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
            "Propagated to warehouse: %d placement(s) updated "
            "(old_id=%s -> new_id=%s, batch=%s)",
            cursor.rowcount, old_chemical_id, new_chemical_id, batch_id[:8]
        )


def _propagate_quantity_to_warehouse(cursor, batch_id, staging_id, new_quantity, new_unit):
    """Update quantity_kg in chemical_placements when inventory quantity changes.

    Fix 1.2: matches by (batch_id, staging_row_id) — the exact placement row.
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
            "Propagated quantity to warehouse: %d placement(s) updated "
            "(staging_row_id=%s, qty=%.1f kg, batch=%s)",
            cursor.rowcount, staging_id, qty, batch_id[:8]
        )


@inventory_bp.route('/api/inventory/batches', methods=['GET'])
@login_required
def list_inventory_batches():
    """List all inventory batches for the inventory management page."""
    try:
        user_db = _get_db_path()
        if not os.path.exists(user_db):
            return jsonify({'batches': []})
        conn = get_safe_connection(user_db, readonly=True)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, filename, status, created_at, total_rows
            FROM inventory_batches
            ORDER BY created_at DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        conn.close()
        return jsonify({'batches': [dict(r) for r in rows]})
    except Exception as e:
        logger.error(f"Batches list error: {e}", exc_info=True)
        return jsonify({'batches': []})


@inventory_bp.route('/api/inventory/batches/create', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def create_manual_batch():
    """Create a new manual inventory batch with a custom name."""
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Name is required'}), 400

        user_db = _get_db_path()
        init_inventory_tables(user_db)

        import uuid
        batch_id = f"manual-{uuid.uuid4()}"

        conn = get_safe_connection(user_db)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO inventory_batches (id, filename, status, total_rows, processed, created_at, completed_at)
            VALUES (?, ?, 'completed', 0, 0, ?, ?)
            """,
            (batch_id, name, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

        logger.info(f"Created manual inventory batch: {batch_id} - {name}")
        return jsonify({
            'success': True,
            'batch': {
                'id': batch_id,
                'filename': name,
                'status': 'completed',
                'total_rows': 0,
                'created_at': datetime.now(timezone.utc).isoformat()
            }
        })
    except Exception as e:
        logger.error(f"Failed to create manual batch: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@inventory_bp.route('/api/inventory/batches/delete/<batch_id>', methods=['DELETE'])
@login_required
@viewer_readonly
@csrf_protect
def delete_inventory_batch(batch_id):
    """Delete a batch and all its associated staging rows, review queue, and audit trail."""
    try:
        user_db = _get_db_path()
        conn = get_safe_connection(user_db)
        cursor = conn.cursor()

        # Check if exists
        cursor.execute("SELECT 1 FROM inventory_batches WHERE id = ?", (batch_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'error': 'Batch not found'}), 404

        # Delete related data — explicit placement delete before batch row (Fix 1.1:
        # guarantees cascade even on connections where FK pragma is off at startup).
        cursor.execute("DELETE FROM chemical_placements WHERE batch_id = ?", (batch_id,))
        cursor.execute("DELETE FROM review_queue WHERE batch_id = ?", (batch_id,))
        cursor.execute("UPDATE audit_trail SET is_deleted = 1 WHERE batch_id = ?", (batch_id,))
        cursor.execute("DELETE FROM inventory_staging WHERE batch_id = ?", (batch_id,))
        cursor.execute("DELETE FROM inventory_batches WHERE id = ?", (batch_id,))
        conn.commit()
        conn.close()
 
        _uid = g.user.get('id') if (hasattr(g, 'user') and g.user) else None
        log_event(
            db_path=user_db,
            event_type='delete_batch',
            category='edit',
            severity='warning',
            title=f"Batch deleted: {batch_id[:8]}",
            detail=f"All staging rows and review queue associated with the batch were deleted.",
            user_id=_uid,
            entity_type='batch',
            entity_id=batch_id,
            meta={'batch_id': batch_id},
        )

        logger.info(f"Deleted inventory batch: {batch_id}")
        return jsonify({'success': True, 'deleted_batch_id': batch_id})
    except Exception as e:
        logger.error(f"Failed to delete batch {batch_id}: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


def _allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _row_version_hash(row: sqlite3.Row) -> str:
    """Generate a deterministic version hash for optimistic locking in row edits."""
    payload = f"{row['id']}|{row['cleaned_data'] or ''}|{row['match_status'] or ''}|{row['chemical_id'] or ''}|{row['quality_score'] or ''}|{row['confidence'] or ''}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


@inventory_bp.route('/admin/import')
@login_required
@role_required('operator', 'company_admin', 'super_admin')
def admin_import_page():
    """Render the inventory import UI."""
    return render_template('admin_import.html')


@inventory_bp.route('/api/inventory/upload', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def upload_inventory():
    """
    Accept a file upload, create a batch, start processing in background.
    Returns: { batch_id: str }
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    if not _allowed_file(file.filename):
        return jsonify({'error': f'Unsupported file type. Allowed: {ALLOWED_EXTENSIONS}'}), 400

    # Ensure upload directory exists
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # Save file
    import uuid
    from werkzeug.utils import secure_filename
    company_id = g.user.get('company_id', 'unknown') if (hasattr(g, 'user') and g.user) else 'unknown'
    prefix = f"{company_id}_{uuid.uuid4().hex[:8]}_"
    safe_name = prefix + secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    file.save(filepath)

    # Get DB paths from app config
    user_db = _get_db_path()
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']

    # Ensure tables exist
    init_inventory_tables(user_db)

    # Create batch or reuse existing
    batch_id = request.args.get('batch_id') or request.form.get('batch_id')
    if batch_id:
        conn = get_safe_connection(user_db)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM inventory_batches WHERE id = ?", (batch_id,))
        exists = cursor.fetchone()
        if not exists:
            conn.close()
            return jsonify({'error': 'Target inventory list not found'}), 404

        # Reset existing batch record status and clear old rows
        cursor.execute(
            """
            UPDATE inventory_batches
            SET status = 'pending', total_rows = 0, processed = 0,
                completed_at = NULL, summary_json = NULL, error_msg = NULL,
                ingestion_meta = NULL, column_mapping = NULL
            WHERE id = ?
            """,
            (batch_id,)
        )
        cursor.execute("DELETE FROM review_queue WHERE batch_id = ?", (batch_id,))
        cursor.execute("DELETE FROM audit_trail WHERE batch_id = ?", (batch_id,))
        cursor.execute("DELETE FROM inventory_staging WHERE batch_id = ?", (batch_id,))
        conn.commit()
        conn.close()
        logger.info(f"Reusing existing batch {batch_id[:8]} for file: {file.filename}")
    else:
        # Create batch
        batch_id = create_batch(user_db, file.filename)
        logger.info(f"Created batch {batch_id[:8]} for file: {file.filename}")

    # Start pipeline in background thread
    run_async(user_db, chemicals_db, batch_id, filepath)

    # Activity log
    _uid = g.user.get('id') if (hasattr(g, 'user') and g.user) else None
    log_event(
        db_path=user_db,
        event_type='file_upload',
        category='import',
        severity='info',
        title=f'File uploaded — {file.filename}',
        detail=f'Batch processing started | Batch: {batch_id[:8]}…',
        user_id=_uid,
        entity_type='batch',
        entity_id=batch_id,
        entity_name=file.filename,
        meta={'filename': file.filename, 'batch_id': batch_id},
    )

    return jsonify({'batch_id': batch_id, 'filename': file.filename})



@inventory_bp.route('/api/inventory/status/<batch_id>')
@login_required
def inventory_status(batch_id):
    """Poll batch processing status."""
    user_db = _get_db_path()
    status = get_batch_status(user_db, batch_id)
    return jsonify(status)


@inventory_bp.route('/api/inventory/rows/<batch_id>')
@login_required
def inventory_rows(batch_id):
    """Get all staging rows for interactive inventory management UI."""
    user_db = _get_db_path()
    conn = get_safe_connection(user_db, readonly=True)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, row_index, cleaned_data, raw_data, match_status,
               chemical_id, confidence, quality_score, issues
        FROM inventory_staging
        WHERE batch_id = ?
        ORDER BY row_index
        """,
        (batch_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    payload = []
    for row in rows:
        cleaned = {}
        raw = {}
        issues = []
        try:
            cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
            raw = json.loads(row['raw_data']) if row['raw_data'] else {}
            issues = json.loads(row['issues']) if row['issues'] else []
        except (json.JSONDecodeError, TypeError):
            pass

        payload.append({
            'staging_id': row['id'],
            'row_index': row['row_index'],
            'chemical_id': row['chemical_id'],
            'name': cleaned.get('name') or raw.get('name', ''),
            'cas': cleaned.get('cas') or raw.get('cas', ''),
            'quantity': cleaned.get('quantity') or raw.get('quantity', ''),
            'unit': cleaned.get('unit') or raw.get('unit', ''),
            'location': cleaned.get('location') or raw.get('location', ''),
            'notes': cleaned.get('notes') or raw.get('notes', ''),
            'match_status': row['match_status'],
            'confidence': row['confidence'],
            'quality_score': row['quality_score'],
            'issues': issues,
            'row_version': _row_version_hash(row),
        })

    return jsonify({'rows': payload, 'count': len(payload)})


@inventory_bp.route('/api/inventory/review/<batch_id>')
@login_required
def review_rows(batch_id):
    """Get all rows that need human review (REVIEW_REQUIRED + UNIDENTIFIED)."""
    user_db = _get_db_path()
    rows = get_review_rows(user_db, batch_id)
    return jsonify({'rows': rows, 'count': len(rows)})


@inventory_bp.route('/api/inventory/confirm', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def confirm_match():
    """
    Human-in-the-loop: confirm a row's chemical match.
    Body: { staging_id: int, chemical_id: int, chemical_name: str }
    Also resolves any pending review_queue entry and records audit trail + learning data.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400

    staging_id = data.get('staging_id')
    chemical_id = data.get('chemical_id')
    chemical_name = data.get('chemical_name', '')

    if not staging_id or not chemical_id:
        return jsonify({'error': 'staging_id and chemical_id are required'}), 400

    # Anti-Hallucination: verify chemical_id exists in chemicals.db
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cursor_chem = conn_chem.cursor()
    cursor_chem.execute("SELECT id, name FROM chemicals WHERE id = ?", (chemical_id,))
    chem = cursor_chem.fetchone()
    conn_chem.close()

    if not chem:
        return jsonify({'error': f'chemical_id {chemical_id} does not exist in database'}), 400

    user_db = _get_db_path()
    conn = get_safe_connection(user_db)
    cursor = conn.cursor()

    # Fetch current cleaned_data and old chemical_id to update name/cas
    cursor.execute("SELECT cleaned_data, chemical_id FROM inventory_staging WHERE id = ?", (staging_id,))
    staging_row = cursor.fetchone()
    cleaned = {}
    old_chemical_id = None
    if staging_row:
        old_chemical_id = staging_row['chemical_id']
        if staging_row['cleaned_data']:
            try:
                cleaned = json.loads(staging_row['cleaned_data'])
            except (json.JSONDecodeError, TypeError):
                pass

    # Update cleaned_data with confirmed chemical info
    cleaned['name'] = chem['name']
    # Also update CAS if available
    conn_c = get_safe_connection(chemicals_db, readonly=True)
    cur_c = conn_c.cursor()
    cur_c.execute("SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1", (chemical_id,))
    cas_row = cur_c.fetchone()
    if cas_row:
        cleaned['cas'] = cas_row['cas_id']
        cleaned['cas_valid'] = True
    conn_c.close()

    # Update staging row
    cursor.execute("""
        UPDATE inventory_staging
        SET chemical_id = ?, match_status = 'MATCHED',
            match_method = 'manual_confirm', confidence = 1.0,
            cleaned_data = ?
        WHERE id = ?
    """, (chemical_id, json.dumps(cleaned), staging_id))
    success = cursor.rowcount > 0

    if not success:
        conn.close()
        return jsonify({'error': 'Row not found'}), 404

    # Resolve any pending review_queue entry for this staging row
    cursor.execute("SELECT id, batch_id, input_data FROM review_queue WHERE staging_id = ? AND status = 'pending'", (staging_id,))
    rq = cursor.fetchone()
    batch_id = None
    input_data = '{}'
    if rq:
        batch_id = rq['batch_id']
        input_data = rq['input_data'] or '{}'
        cursor.execute("""
            UPDATE review_queue
            SET status = 'resolved', resolution = ?, resolution_timestamp = ?
            WHERE id = ?
        """, (json.dumps({'chemical_id': chemical_id, 'chemical_name': chem['name']}),
              datetime.utcnow().isoformat(), rq['id']))

    # If no batch_id from review_queue, get it from staging
    if not batch_id:
        cursor.execute("SELECT batch_id, raw_data FROM inventory_staging WHERE id = ?", (staging_id,))
        staging_row2 = cursor.fetchone()
        if staging_row2:
            batch_id = staging_row2['batch_id']
            input_data = staging_row2['raw_data'] or '{}'

    # Store in learning_data for future improvement
    cursor.execute("""
        INSERT INTO learning_data
            (input_pattern, context, correct_chemical_id, corrected_by)
        VALUES (?, ?, ?, 'manual_confirm')
    """, (input_data, json.dumps({'batch_id': batch_id}), chemical_id))

    # Audit trail
    cursor.execute("""
        INSERT INTO audit_trail
            (batch_id, row_index, action, input_data, output_data,
             confidence, method, timestamp, user_id)
        VALUES (?, (SELECT row_index FROM inventory_staging WHERE id = ?),
                'manual_confirm', ?, ?, 1.0, 'manual_confirm', ?, 'human')
    """, (batch_id, staging_id, input_data,
          json.dumps({'chemical_id': chemical_id, 'chemical_name': chem['name']}),
          datetime.utcnow().isoformat()))

    # Propagate to warehouse if batch was already imported
    _propagate_to_warehouse(cursor, batch_id, old_chemical_id, chemical_id, chem, staging_id)
    conn.commit()
    conn.close()
 
    _uid = g.user.get('id') if (hasattr(g, 'user') and g.user) else None
    log_event(
        db_path=user_db,
        event_type='manual_confirm',
        category='analysis',
        severity='info',
        title=f"Chemical match confirmed — {chem['name']}",
        detail=f"Staging row {staging_id} match confirmed manually to {chem['name']} (ID: {chemical_id})",
        user_id=_uid,
        entity_type='chemical',
        entity_id=str(chemical_id),
        entity_name=chem['name'],
        meta={'staging_id': staging_id, 'batch_id': batch_id, 'chemical_id': chemical_id},
    )

    return jsonify({'success': True, 'chemical_name': chem['name']})


@inventory_bp.route('/api/inventory/search_chemicals')
@login_required
def search_chemicals_for_linking():
    """
    Search chemicals.db for manual linking.
    Used when a row is UNIDENTIFIED and user wants to manually find a match.
    Query param: q (search term)
    """
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify({'results': []})

    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn = get_safe_connection(chemicals_db, readonly=True)
    cursor = conn.cursor()

    like_term = f'%{query}%'
    starts_with = f'{query}%'
    cursor.execute("""
        SELECT DISTINCT c.id, c.name, c.formulas
             , (SELECT cas_id FROM chemical_cas cc2 WHERE cc2.chem_id = c.id ORDER BY sort LIMIT 1) AS cas_id
        FROM chemicals c
        LEFT JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE c.name LIKE ?
           OR c.synonyms LIKE ?
           OR c.formulas LIKE ?
           OR cc.cas_id LIKE ?
        ORDER BY 
           CASE WHEN LOWER(c.name) = LOWER(?) THEN 0
                WHEN c.name LIKE ? THEN 1
                ELSE 2
           END,
           LENGTH(c.name) ASC
        LIMIT 20
    """, (like_term, like_term, like_term, like_term, query, starts_with))

    results = []
    for row in cursor.fetchall():
        results.append({
            'chemical_id': row['id'],
            'chemical_name': row['name'],
            'formula': row['formulas'] or '',
            'cas': row['cas_id'] or '',
        })

    conn.close()
    return jsonify({'results': results})


# ═══════════════════════════════════════════════════════
#  Layer 2: Column Mapping API
# ═══════════════════════════════════════════════════════

@inventory_bp.route('/api/inventory/column_mapping/<batch_id>')
@login_required
def get_column_mapping(batch_id):
    """
    Get the column mapping result for a batch.
    Returns the full Layer 2 analysis including confidence scores.
    """
    user_db = _get_db_path()
    conn = get_safe_connection(user_db, readonly=True)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT column_mapping, ingestion_meta FROM inventory_batches WHERE id = ?",
        (batch_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Batch not found'}), 404

    result = {}
    try:
        if row['column_mapping']:
            result['column_mapping'] = json.loads(row['column_mapping'])
    except (json.JSONDecodeError, TypeError):
        result['column_mapping'] = None

    try:
        if row['ingestion_meta']:
            result['ingestion_meta'] = json.loads(row['ingestion_meta'])
    except (json.JSONDecodeError, TypeError):
        result['ingestion_meta'] = None

    return jsonify(result)


# ═══════════════════════════════════════════════════════
#  Layer 5: Review Queue API
# ═══════════════════════════════════════════════════════

@inventory_bp.route('/api/inventory/review_queue/<batch_id>')
@login_required
def get_review_queue(batch_id):
    """
    Get prioritized review queue for a batch.
    Returns rows sorted by priority (critical → high → medium → low).
    """
    user_db = _get_db_path()
    conn = get_safe_connection(user_db, readonly=True)
    cursor = conn.cursor()

    priority_order = "CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5 END"
    cursor.execute(f"""
        SELECT rq.id, rq.staging_id, rq.priority, rq.status,
               rq.input_data, rq.candidates,
               ist.row_index, ist.match_status, ist.confidence, ist.quality_score,
               ist.raw_data, ist.cleaned_data, ist.issues
        FROM review_queue rq
        JOIN inventory_staging ist ON rq.staging_id = ist.id
        WHERE rq.batch_id = ? AND rq.status = 'pending'
        ORDER BY {priority_order}, ist.row_index
    """, (batch_id,))

    rows = cursor.fetchall()
    conn.close()

    queue = []
    for row in rows:
        item = {
            'queue_id': row['id'],
            'staging_id': row['staging_id'],
            'priority': row['priority'],
            'row_index': row['row_index'],
            'match_status': row['match_status'],
            'confidence': row['confidence'],
            'quality_score': row['quality_score'],
        }
        try:
            item['input_data'] = json.loads(row['input_data']) if row['input_data'] else {}
            item['candidates'] = json.loads(row['candidates']) if row['candidates'] else []
            item['raw_data'] = json.loads(row['raw_data']) if row['raw_data'] else {}
            item['cleaned_data'] = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
            item['issues'] = json.loads(row['issues']) if row['issues'] else []
        except (json.JSONDecodeError, TypeError):
            pass
        queue.append(item)

    return jsonify({
        'queue': queue,
        'total': len(queue),
        'by_priority': {
            'critical': sum(1 for q in queue if q['priority'] == 'critical'),
            'high': sum(1 for q in queue if q['priority'] == 'high'),
            'medium': sum(1 for q in queue if q['priority'] == 'medium'),
            'low': sum(1 for q in queue if q['priority'] == 'low'),
        }
    })


@inventory_bp.route('/api/inventory/resolve_review', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def resolve_review():
    """
    Resolve a review queue item.
    Body: { queue_id: int, chemical_id: int, chemical_name: str }
    Also stores the correction in learning_data for future improvement.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400

    queue_id = data.get('queue_id')
    chemical_id = data.get('chemical_id')

    if not queue_id or not chemical_id:
        return jsonify({'error': 'queue_id and chemical_id are required'}), 400

    # Verify chemical exists
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn_chem = get_safe_connection(chemicals_db, readonly=True)
    cursor_chem = conn_chem.cursor()
    cursor_chem.execute("SELECT id, name FROM chemicals WHERE id = ?", (chemical_id,))
    chem = cursor_chem.fetchone()
    conn_chem.close()

    if not chem:
        return jsonify({'error': f'chemical_id {chemical_id} not found'}), 400

    user_db = _get_db_path()
    conn = get_safe_connection(user_db)
    cursor = conn.cursor()

    # Get review queue item
    cursor.execute("SELECT staging_id, input_data, batch_id FROM review_queue WHERE id = ?", (queue_id,))
    rq = cursor.fetchone()
    if not rq:
        conn.close()
        return jsonify({'error': 'Review queue item not found'}), 404

    staging_id = rq['staging_id']
    batch_id = rq['batch_id']

    # Fetch current cleaned_data and OLD chemical_id to update name/cas
    cursor.execute("SELECT cleaned_data, chemical_id FROM inventory_staging WHERE id = ?", (staging_id,))
    staging_row = cursor.fetchone()
    cleaned = {}
    old_chemical_id = None
    if staging_row:
        old_chemical_id = staging_row['chemical_id']
        if staging_row['cleaned_data']:
            try:
                cleaned = json.loads(staging_row['cleaned_data'])
            except (json.JSONDecodeError, TypeError):
                pass

    # Update cleaned_data with confirmed chemical info
    cleaned['name'] = chem['name']
    # Also update CAS if available from chemicals.db (conn_chem was already closed above)
    conn_chem2 = get_safe_connection(chemicals_db, readonly=True)
    cursor_chem2 = conn_chem2.cursor()
    cursor_chem2.execute(
        "SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort LIMIT 1",
        (chemical_id,)
    )
    cas_row = cursor_chem2.fetchone()
    if cas_row:
        cleaned['cas'] = cas_row['cas_id']
        cleaned['cas_valid'] = True
    conn_chem2.close()

    # Update staging row with confirmed chemical and refreshed cleaned_data
    cursor.execute("""
        UPDATE inventory_staging
        SET chemical_id = ?, match_status = 'MATCHED',
            match_method = 'manual_review', confidence = 1.0,
            cleaned_data = ?
        WHERE id = ?
    """, (chemical_id, json.dumps(cleaned), staging_id))

    # Propagate to warehouse if batch was already imported
    _propagate_to_warehouse(cursor, batch_id, old_chemical_id, chemical_id, chem, staging_id)

    # Mark review queue item as resolved
    cursor.execute("""
        UPDATE review_queue
        SET status = 'resolved', resolution = ?, resolution_timestamp = ?
        WHERE id = ?
    """, (json.dumps({'chemical_id': chemical_id, 'chemical_name': chem['name']}),
          datetime.utcnow().isoformat(), queue_id))

    # Store in learning_data for future improvement
    input_data = rq['input_data'] or '{}'
    cursor.execute("""
        INSERT INTO learning_data
            (input_pattern, context, correct_chemical_id, corrected_by)
        VALUES (?, ?, ?, 'human_review')
    """, (input_data, json.dumps({'batch_id': batch_id}), chemical_id))

    # Audit trail
    cursor.execute("""
        INSERT INTO audit_trail
            (batch_id, row_index, action, input_data, output_data,
             confidence, method, timestamp, user_id)
        VALUES (?, (SELECT row_index FROM inventory_staging WHERE id = ?),
                'manual_review', ?, ?, 1.0, 'manual_review', ?, 'human')
    """, (batch_id, staging_id, input_data,
          json.dumps({'chemical_id': chemical_id, 'chemical_name': chem['name']}),
          datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
 
    _uid = g.user.get('id') if (hasattr(g, 'user') and g.user) else None
    log_event(
        db_path=user_db,
        event_type='manual_review',
        category='analysis',
        severity='info',
        title=f"Manual review resolved — {chem['name']}",
        detail=f"Staging row {staging_id} resolved from review queue item {queue_id} to {chem['name']} (ID: {chemical_id})",
        user_id=_uid,
        entity_type='chemical',
        entity_id=str(chemical_id),
        entity_name=chem['name'],
        meta={'queue_id': queue_id, 'staging_id': staging_id, 'batch_id': batch_id},
    )

    return jsonify({
        'success': True,
        'chemical_id': chemical_id,
        'chemical_name': chem['name'],
    })


# ═══════════════════════════════════════════════════════
#  Layer 5: Audit Trail API
# ═══════════════════════════════════════════════════════

@inventory_bp.route('/api/inventory/audit/<batch_id>')
@login_required
def get_audit_trail(batch_id):
    """Get audit trail for a batch."""
    user_db = _get_db_path()
    conn = get_safe_connection(user_db, readonly=True)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, row_index, action, input_data, output_data,
               confidence, method, timestamp, user_id
        FROM audit_trail
        WHERE batch_id = ? AND (is_deleted IS NULL OR is_deleted = 0)
        ORDER BY timestamp DESC
        LIMIT 500
    """, (batch_id,))

    rows = cursor.fetchall()
    conn.close()

    trail = []
    for row in rows:
        item = dict(row)
        try:
            item['input_data'] = json.loads(item['input_data']) if item['input_data'] else {}
            item['output_data'] = json.loads(item['output_data']) if item['output_data'] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        trail.append(item)

    return jsonify({'audit_trail': trail, 'total': len(trail)})
