"""
inventory.py — Flask Blueprint for inventory ingestion API.
Routes: upload, status polling, admin page.
"""

import os
import logging
from flask import Blueprint, request, jsonify, render_template, current_app

from etl.pipeline import init_inventory_tables, create_batch, get_batch_status, run_async

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
