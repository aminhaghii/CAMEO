"""
inventory.py — Flask Blueprint for inventory ingestion API (ETL v2).
Routes: upload, status polling, review rows, confirm match, search chemicals, admin page.
"""

import os
import re
import sqlite3
import logging
from flask import Blueprint, request, jsonify, render_template, current_app

from etl.pipeline import (
    init_inventory_tables, create_batch, get_batch_status,
    run_async, confirm_row, get_review_rows
)

logger = logging.getLogger(__name__)

inventory_bp = Blueprint('inventory', __name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls', 'json'}


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
