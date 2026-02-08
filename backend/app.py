import os
import re
import sqlite3
import logging
import difflib
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
    """
    Industrial-grade omni-search endpoint.
    Searches: Name, CAS, UN, Formula, Synonyms
    Returns: Ranked results with match context and NFPA safety data.
    """
    query = request.args.get('q', '').strip()
    if not query or len(query) < 2:
        return jsonify({'items': [], 'total': 0, 'query': query})

    try:
        conn = get_chemicals_db_connection()
        cursor = conn.cursor()

        q_upper = query.upper()
        like_term = f'%{query}%'

        # ── Step 1: Omni-search via LEFT JOINs (union of matches) ──
        # unna_id is INTEGER, so we CAST it to TEXT for LIKE matching.
        # We also strip "UN" prefix if user typed e.g. "UN1090".
        un_query = re.sub(r'^UN\s*', '', query, flags=re.IGNORECASE)
        un_like = f'%{un_query}%'

        sql = """
            SELECT DISTINCT
                c.id, c.name, c.synonyms, c.formulas,
                c.nfpa_health, c.nfpa_flam, c.nfpa_react, c.nfpa_special
            FROM chemicals c
            LEFT JOIN chemical_cas cc ON c.id = cc.chem_id
            LEFT JOIN chemical_unna cu ON c.id = cu.chem_id
            WHERE c.name LIKE ?
               OR c.synonyms LIKE ?
               OR c.formulas LIKE ?
               OR cc.cas_id LIKE ?
               OR CAST(cu.unna_id AS TEXT) LIKE ?
            LIMIT 200
        """
        cursor.execute(sql, (like_term, like_term, like_term, like_term, un_like))
        # Convert to plain dicts immediately (Row objects may not survive conn.close)
        rows = [dict(r) for r in cursor.fetchall()]

        # ── Step 2: Collect CAS / UN per matched chemical ──
        chem_ids = list({row['id'] for row in rows})
        cas_map = {}
        un_map = {}
        if chem_ids:
            ph = ','.join('?' * len(chem_ids))
            cursor.execute(
                f"SELECT chem_id, cas_id FROM chemical_cas WHERE chem_id IN ({ph}) ORDER BY sort",
                chem_ids
            )
            for r in cursor.fetchall():
                cas_map.setdefault(r['chem_id'], []).append(str(r['cas_id']))

            cursor.execute(
                f"SELECT chem_id, unna_id FROM chemical_unna WHERE chem_id IN ({ph}) ORDER BY sort",
                chem_ids
            )
            for r in cursor.fetchall():
                un_map.setdefault(r['chem_id'], []).append(str(r['unna_id']))

        conn.close()

        # ── Step 3: Python-side scoring & match labeling ──
        scored = []
        seen_ids = set()
        un_query_upper = un_query.upper()

        for row in rows:
            cid = row['id']
            if cid in seen_ids:
                continue
            seen_ids.add(cid)

            name = row['name'] or ''
            synonyms_raw = row['synonyms'] or ''
            formula = row['formulas'] or ''
            cas_list = cas_map.get(cid, [])
            un_list = un_map.get(cid, [])

            name_upper = name.upper()

            score = 0
            match_type = 'Name'
            matched_text = name

            # 1. Exact name match
            if name_upper == q_upper:
                score = 1000
                match_type = 'Name'
                matched_text = name
            # 2. Name starts with query
            elif name_upper.startswith(q_upper):
                score = 900
                match_type = 'Name'
                matched_text = name
            # 3. CAS match
            elif any(q_upper in cas.upper() for cas in cas_list):
                matching_cas = next(cas for cas in cas_list if q_upper in cas.upper())
                score = 950 if matching_cas.upper() == q_upper else 850
                match_type = 'CAS'
                matched_text = matching_cas
            # 4. Formula match
            elif formula and q_upper in formula.upper():
                if formula.upper() == q_upper:
                    score = 800
                elif formula.upper().startswith(q_upper):
                    score = 750
                else:
                    score = 700
                match_type = 'Formula'
                matched_text = formula
            # 5. UN/NA match (compare with stripped query)
            elif any(un_query_upper in un for un in un_list):
                matching_un = next(un for un in un_list if un_query_upper in un)
                score = 650
                match_type = 'UN'
                matched_text = f'UN{matching_un}'
            # 6. Name contains (not prefix)
            elif q_upper in name_upper:
                score = 600
                match_type = 'Name'
                matched_text = name
            # 7. Synonym match
            elif q_upper in synonyms_raw.upper():
                syn_tokens = [s.strip() for s in synonyms_raw.split('|') if s.strip()]
                best_syn = None
                best_syn_score = 0
                for syn in syn_tokens:
                    syn_upper = syn.upper()
                    if syn_upper == q_upper:
                        best_syn, best_syn_score = syn, 550
                        break
                    elif syn_upper.startswith(q_upper) and best_syn_score < 500:
                        best_syn, best_syn_score = syn, 500
                    elif q_upper in syn_upper and best_syn_score < 450:
                        best_syn, best_syn_score = syn, 450
                if best_syn:
                    score = best_syn_score
                    match_type = 'Synonym'
                    matched_text = best_syn
                else:
                    score = 400
                    match_type = 'Synonym'
                    matched_text = name

            # Tiebreak: shorter names rank higher (more specific)
            score -= len(name) * 0.01

            scored.append({
                'id': cid,
                'name': name,
                'formula': formula,
                'cas': cas_list,
                'un': [f'UN{u}' for u in un_list],
                'match_type': match_type,
                'matched_text': matched_text,
                'nfpa': {
                    'h': row['nfpa_health'],
                    'f': row['nfpa_flam'],
                    'r': row['nfpa_react'],
                    's': row['nfpa_special']
                },
                '_score': score
            })

        # ── Step 4: Sort by score desc, limit 20 ──
        scored.sort(key=lambda x: x['_score'], reverse=True)
        top = scored[:20]
        for item in top:
            del item['_score']

        return jsonify({'items': top, 'total': len(top), 'query': query})

    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        return jsonify({'items': [], 'total': 0, 'error': str(e), 'query': query}), 500

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


@app.route('/chemical/<int:chemical_id>')
def chemical_detail_page(chemical_id):
    """Render rich detail workspace for a single chemical"""
    try:
        conn = get_chemicals_db_connection()
        cursor = conn.cursor()
        
        # Get main chemical data
        cursor.execute('SELECT * FROM chemicals WHERE id = ?', (chemical_id,))
        row = cursor.fetchone()
        chemical = dict(row) if row else None
        
        if chemical:
            # Get CAS numbers
            cursor.execute('SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort', (chemical_id,))
            cas_rows = cursor.fetchall()
            chemical['cas_numbers'] = [r['cas_id'] for r in cas_rows] if cas_rows else []
            
            # Get UN/NA numbers
            cursor.execute('SELECT unna_id FROM chemical_unna WHERE chem_id = ? ORDER BY sort', (chemical_id,))
            unna_rows = cursor.fetchall()
            chemical['un_numbers'] = [r['unna_id'] for r in unna_rows] if unna_rows else []
            
            # Get ICSC info
            cursor.execute('SELECT icsc, icsc_name FROM chemical_icsc WHERE chem_id = ? ORDER BY sort', (chemical_id,))
            icsc_rows = cursor.fetchall()
            chemical['icsc_codes'] = [{'code': r['icsc'], 'name': r['icsc_name']} for r in icsc_rows] if icsc_rows else []
            
            # Get reactive groups
            cursor.execute('''
                SELECT rg.id, rg.name, rg.description 
                FROM reacts rg 
                JOIN mm_chemical_react crg ON rg.id = crg.react_id 
                WHERE crg.chem_id = ?
            ''', (chemical_id,))
            group_rows = cursor.fetchall()
            chemical['reactive_groups'] = [dict(r) for r in group_rows] if group_rows else []
            
            # Extract ERG guide number from isolation field if present
            erg_match = None
            if chemical.get('isolation'):
                import re
                match = re.search(r'Guide\s+(\d+)', chemical['isolation'])
                if match:
                    erg_match = match.group(1)
            chemical['erg_guide'] = erg_match
        
        conn.close()
    except Exception as e:
        logger.error(f"Chemical detail error: {e}")
        chemical = None

    return render_template('chemical_detail.html', chemical=chemical)

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
