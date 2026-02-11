"""
inventory.py — Flask Blueprint for inventory ingestion API (ETL v4).
Routes: upload, status polling, review rows, confirm match, search chemicals,
        column mapping, review queue, learning feedback, admin page.
"""

import os
import re
import json
import sqlite3
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, current_app

from etl.pipeline import (
    init_inventory_tables, create_batch, get_batch_status,
    run_async, confirm_row, get_review_rows
)

logger = logging.getLogger(__name__)

inventory_bp = Blueprint('inventory', __name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'json', 'txt', 'tsv'}


def _allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@inventory_bp.route('/admin/import')
def admin_import_page():
    """Render the inventory import UI."""
    return render_template('admin_import.html')


@inventory_bp.route('/api/inventory/upload', methods=['POST'])
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
    from werkzeug.utils import secure_filename
    safe_name = secure_filename(file.filename)
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    file.save(filepath)

    # Get DB paths from app config
    user_db = current_app.config['USER_DB_PATH']
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']

    # Ensure tables exist
    init_inventory_tables(user_db)

    # Create batch
    batch_id = create_batch(user_db, file.filename)
    logger.info(f"Created batch {batch_id[:8]} for file: {file.filename}")

    # Start pipeline in background thread
    run_async(user_db, chemicals_db, batch_id, filepath)

    return jsonify({'batch_id': batch_id, 'filename': file.filename})


@inventory_bp.route('/api/inventory/status/<batch_id>')
def inventory_status(batch_id):
    """Poll batch processing status."""
    user_db = current_app.config['USER_DB_PATH']
    status = get_batch_status(user_db, batch_id)
    return jsonify(status)


@inventory_bp.route('/api/inventory/review/<batch_id>')
def review_rows(batch_id):
    """Get all rows that need human review (REVIEW_REQUIRED + UNIDENTIFIED)."""
    user_db = current_app.config['USER_DB_PATH']
    rows = get_review_rows(user_db, batch_id)
    return jsonify({'rows': rows, 'count': len(rows)})


@inventory_bp.route('/api/inventory/confirm', methods=['POST'])
def confirm_match():
    """
    Human-in-the-loop: confirm a row's chemical match.
    Body: { staging_id: int, chemical_id: int, chemical_name: str }
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
    conn = sqlite3.connect(chemicals_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM chemicals WHERE id = ?", (chemical_id,))
    chem = cursor.fetchone()
    conn.close()

    if not chem:
        return jsonify({'error': f'chemical_id {chemical_id} does not exist in database'}), 400

    user_db = current_app.config['USER_DB_PATH']
    success = confirm_row(user_db, staging_id, chemical_id, chem['name'])

    if success:
        return jsonify({'success': True, 'chemical_name': chem['name']})
    else:
        return jsonify({'error': 'Row not found'}), 404


@inventory_bp.route('/api/inventory/search_chemicals')
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
    conn = sqlite3.connect(chemicals_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    like_term = f'%{query}%'
    cursor.execute("""
        SELECT DISTINCT c.id, c.name, c.formulas
        FROM chemicals c
        LEFT JOIN chemical_cas cc ON c.id = cc.chem_id
        WHERE c.name LIKE ?
           OR c.synonyms LIKE ?
           OR c.formulas LIKE ?
           OR cc.cas_id LIKE ?
        LIMIT 20
    """, (like_term, like_term, like_term, like_term))

    results = []
    for row in cursor.fetchall():
        results.append({
            'chemical_id': row['id'],
            'chemical_name': row['name'],
            'formula': row['formulas'] or '',
        })

    conn.close()
    return jsonify({'results': results})


# ═══════════════════════════════════════════════════════
#  Layer 2: Column Mapping API
# ═══════════════════════════════════════════════════════

@inventory_bp.route('/api/inventory/column_mapping/<batch_id>')
def get_column_mapping(batch_id):
    """
    Get the column mapping result for a batch.
    Returns the full Layer 2 analysis including confidence scores.
    """
    user_db = current_app.config['USER_DB_PATH']
    conn = sqlite3.connect(user_db)
    conn.row_factory = sqlite3.Row
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
def get_review_queue(batch_id):
    """
    Get prioritized review queue for a batch.
    Returns rows sorted by priority (critical → high → medium → low).
    """
    user_db = current_app.config['USER_DB_PATH']
    conn = sqlite3.connect(user_db)
    conn.row_factory = sqlite3.Row
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
    conn_chem = sqlite3.connect(chemicals_db)
    conn_chem.row_factory = sqlite3.Row
    cursor_chem = conn_chem.cursor()
    cursor_chem.execute("SELECT id, name FROM chemicals WHERE id = ?", (chemical_id,))
    chem = cursor_chem.fetchone()
    conn_chem.close()

    if not chem:
        return jsonify({'error': f'chemical_id {chemical_id} not found'}), 400

    user_db = current_app.config['USER_DB_PATH']
    conn = sqlite3.connect(user_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get review queue item
    cursor.execute("SELECT staging_id, input_data, batch_id FROM review_queue WHERE id = ?", (queue_id,))
    rq = cursor.fetchone()
    if not rq:
        conn.close()
        return jsonify({'error': 'Review queue item not found'}), 404

    staging_id = rq['staging_id']
    batch_id = rq['batch_id']

    # Update staging row
    cursor.execute("""
        UPDATE inventory_staging
        SET chemical_id = ?, match_status = 'MATCHED',
            match_method = 'manual_review', confidence = 1.0
        WHERE id = ?
    """, (chemical_id, staging_id))

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

    return jsonify({
        'success': True,
        'chemical_id': chemical_id,
        'chemical_name': chem['name'],
    })


# ═══════════════════════════════════════════════════════
#  Layer 5: Audit Trail API
# ═══════════════════════════════════════════════════════

@inventory_bp.route('/api/inventory/audit/<batch_id>')
def get_audit_trail(batch_id):
    """Get audit trail for a batch."""
    user_db = current_app.config['USER_DB_PATH']
    conn = sqlite3.connect(user_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, row_index, action, input_data, output_data,
               confidence, method, timestamp, user_id
        FROM audit_trail
        WHERE batch_id = ?
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
