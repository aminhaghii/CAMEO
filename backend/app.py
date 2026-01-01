import os
import sqlite3
import logging
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility, COMPATIBILITY_MAP

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CHEMICALS_DB_PATH = os.path.join(DATA_DIR, 'chemicals.db')
USER_DB_PATH = os.path.join(DATA_DIR, 'user.db')

# Initialize Reactivity Engine
reactivity_engine = ReactivityEngine(CHEMICALS_DB_PATH)

def get_chemicals_db_connection():
    conn = sqlite3.connect(CHEMICALS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_db_connection():
    # Initialize user.db if it doesn't exist
    if not os.path.exists(USER_DB_PATH):
        init_user_db()
    
    conn = sqlite3.connect(USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_user_db():
    conn = sqlite3.connect(USER_DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chemical_id INTEGER NOT NULL,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            note TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Ensure user db is initialized on startup
if not os.path.exists(USER_DB_PATH):
    init_user_db()

@app.route('/api/search', methods=['GET'])
def search():
    query = request.args.get('q', '')
    if not query:
        return jsonify({'items': [], 'total': 0})

    try:
        conn = get_chemicals_db_connection()
        cursor = conn.cursor()
        
        sql = """
            SELECT id, name, synonyms 
            FROM chemicals 
            WHERE name LIKE ? OR synonyms LIKE ?
        """
        search_term = f'%{query}%'
        cursor.execute(sql, (search_term, search_term))
        rows = cursor.fetchall()
        
        items = []
        for row in rows:
            items.append({
                'id': row['id'],
                'name': row['name'],
                'synonyms': row['synonyms']
            })
            
        conn.close()
        return jsonify({'items': items, 'total': len(items)})
    except Exception as e:
        print(f"Search error: {e}")
        return jsonify({'items': [], 'total': 0, 'error': str(e)}), 500

@app.route('/api/chemical/<int:chemical_id>', methods=['GET'])
def get_chemical(chemical_id):
    try:
        conn = get_chemicals_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM chemicals WHERE id = ?', (chemical_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            # Convert Row object to dict
            return jsonify(dict(row))
        else:
            return jsonify(None)
    except Exception as e:
        print(f"Get chemical error: {e}")
        return jsonify(None), 500

@app.route('/api/favorites', methods=['GET'])
def get_favorites():
    try:
        conn = get_user_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM favorites ORDER BY added_at DESC')
        rows = cursor.fetchall()
        
        favorites = []
        for row in rows:
            favorites.append({
                'id': row['id'],
                'chemical_id': row['chemical_id'],
                'added_at': row['added_at'],
                'note': row['note']
            })
            
        conn.close()
        return jsonify(favorites)
    except Exception as e:
        print(f"Get favorites error: {e}")
        return jsonify([]), 500

@app.route('/api/favorites', methods=['POST'])
def add_favorite():
    try:
        data = request.json
        # Support both snake_case and camelCase
        chemical_id = data.get('chemical_id') or data.get('chemicalId')
        note = data.get('note')
        
        conn = get_user_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('INSERT INTO favorites (chemical_id, note) VALUES (?, ?)', (chemical_id, note))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Add favorite error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/favorites/<int:chemical_id>', methods=['DELETE'])
def remove_favorite(chemical_id):
    try:
        conn = get_user_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM favorites WHERE chemical_id = ?', (chemical_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Remove favorite error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/')
def index():
    """Render the main page"""
    return render_template('mixer.html')

@app.route('/mixer')
def mixer_page():
    """Render the chemical mixer UI"""
    return render_template('mixer.html')


@app.route('/api/analyze', methods=['POST'])
def analyze_chemicals():
    """
    ═══════════════════════════════════════════════════════════
    POST /api/analyze
    Analyze chemical compatibility - SAFETY-CRITICAL ENDPOINT
    ═══════════════════════════════════════════════════════════
    
    Request Body:
    {
        "chemical_ids": [1, 5, 23],
        "options": {
            "include_water_check": true
        }
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'chemical_ids' not in data:
            return jsonify({
                'success': False,
                'error': {
                    'code': 'MISSING_CHEMICAL_IDS',
                    'message': 'Chemical IDs list is required'
                }
            }), 400
        
        chemical_ids = data['chemical_ids']
        options = data.get('options', {})
        
        if len(chemical_ids) < 2:
            return jsonify({
                'success': False,
                'error': {
                    'code': 'INSUFFICIENT_CHEMICALS',
                    'message': 'At least 2 chemicals required for analysis'
                }
            }), 400
        
        # Run analysis
        result = reactivity_engine.analyze(
            chemical_ids=chemical_ids,
            include_water_check=options.get('include_water_check', True)
        )
        
        # Convert to JSON-friendly format
        compat_info = COMPATIBILITY_MAP[result.overall_compatibility]
        
        response = {
            'success': True,
            'data': {
                'meta': {
                    'timestamp': result.timestamp,
                    'chemical_count': result.chemical_count,
                    'audit_id': result.audit_id
                },
                'overall': {
                    'compatibility': result.overall_compatibility.value,
                    'label': compat_info.label_en,
                    'color': compat_info.color_hex,
                    'action': compat_info.action_required
                },
                'chemicals': result.chemicals,
                'matrix': [
                    [
                        {
                            'compatibility': cell.compatibility.value if cell else None,
                            'hazards': cell.hazards if cell else [],
                            'gases': cell.gas_products if cell else [],
                            'color': COMPATIBILITY_MAP[cell.compatibility].color_hex if cell else '#6B7280'
                        }
                        for cell in row
                    ]
                    for row in result.matrix
                ],
                'critical_pairs': result.critical_pairs,
                'warnings': result.warnings
            }
        }
        
        return jsonify(response)
    
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': {
                'code': 'VALIDATION_ERROR',
                'message': str(e)
            }
        }), 400
    
    except Exception as e:
        logger.error(f"Analysis error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': {
                'code': 'INTERNAL_ERROR',
                'message': 'Internal server error'
            }
        }), 500


@app.route('/api/reactivity/stats', methods=['GET'])
def get_reactivity_stats():
    """Get reactivity database statistics"""
    try:
        stats = reactivity_engine.get_statistics()
        return jsonify({'success': True, 'data': stats})
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/reactive-groups', methods=['GET'])
def get_reactive_groups():
    """Get list of all reactive groups"""
    try:
        conn = get_chemicals_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, description FROM reacts ORDER BY id")
        rows = cursor.fetchall()
        conn.close()
        
        groups = [{'id': r['id'], 'name': r['name'], 'description': r['description']} for r in rows]
        return jsonify({'success': True, 'data': groups})
    except Exception as e:
        logger.error(f"Get groups error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)
