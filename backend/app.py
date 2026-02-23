import os
import re
import secrets
import sqlite3
import logging
import difflib
from pathlib import Path
from flask import Flask, jsonify, request, render_template, g
from flask_cors import CORS

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility, COMPATIBILITY_MAP
from auth.models import init_auth_db, seed_default_company_and_admin, get_auth_db_connection
from auth.security import hash_password, validate_session, generate_csrf_token

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, supports_credentials=True)  # Enable CORS with credentials for auth cookies

# ═══════════════════════════════════════════════════════
#  Security Configuration
# ═══════════════════════════════════════════════════════
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CHEMICALS_DB_PATH = os.path.join(DATA_DIR, 'chemicals.db')
USER_DB_PATH = os.path.join(DATA_DIR, 'user.db')  # Legacy fallback
AUTH_DB_PATH = os.path.join(DATA_DIR, 'global_auth.db')

# Store paths in app config for blueprints
app.config['CHEMICALS_DB_PATH'] = CHEMICALS_DB_PATH
app.config['USER_DB_PATH'] = USER_DB_PATH
app.config['AUTH_DB_PATH'] = AUTH_DB_PATH
app.config['DATA_DIR'] = DATA_DIR

# Initialize Reactivity Engine
reactivity_engine = ReactivityEngine(CHEMICALS_DB_PATH)

# ═══════════════════════════════════════════════════════
#  Initialize Auth Database
# ═══════════════════════════════════════════════════════
os.makedirs(DATA_DIR, exist_ok=True)
init_auth_db(AUTH_DB_PATH)
default_pw_hash = hash_password('Admin@123')  # Default password for seeded users
seed_default_company_and_admin(AUTH_DB_PATH, default_pw_hash)
logger.info("Auth database initialized")

# ═══════════════════════════════════════════════════════
#  Register Blueprints
# ═══════════════════════════════════════════════════════
from routes.inventory import inventory_bp
from routes.inventory_actions import inventory_actions_bp
from routes.inventory_analysis import inventory_analysis_bp
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.compliance import compliance_bp

app.register_blueprint(inventory_bp)
app.register_blueprint(inventory_actions_bp)
app.register_blueprint(inventory_analysis_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(compliance_bp)


# ═══════════════════════════════════════════════════════
#  Tenant Router (before_request middleware)
# ═══════════════════════════════════════════════════════

# Paths that bypass authentication
AUTH_EXEMPT_PREFIXES = (
    '/auth/',
    '/api/auth/',
    '/static/',
    '/api/compliance/',   # EU compliance export — RBAC applied at route level
    '/compliance',        # Compliance UI page
)


@app.before_request
def tenant_router():
    """
    Multi-tenant request middleware.

    For every request:
    1. Check if path is auth-exempt
    2. Validate session cookie
    3. Load user data into g.user
    4. Set g.tenant_db_path for tenant isolation
    5. Redirect to login if unauthenticated

    Super Admin blind spot: g.tenant_db_path = None
    """
    g.user = None
    g.tenant_db_path = None

    # Skip auth for exempt paths
    for prefix in AUTH_EXEMPT_PREFIXES:
        if request.path.startswith(prefix):
            return None

    # Validate session
    session_id = request.cookies.get('session_id')
    if not session_id:
        # Not authenticated — redirect to login
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required', 'code': 'AUTH_REQUIRED'}), 401
        return redirect('/auth/login')

    user = validate_session(session_id, AUTH_DB_PATH)
    if not user:
        # Invalid/expired session
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Session expired', 'code': 'SESSION_EXPIRED'}), 401
        response = redirect('/auth/login')
        response.delete_cookie('session_id', path='/')
        return response

    # Set user in request context
    g.user = user

    # ── Tenant Routing ──
    if user['role'] == 'super_admin':
        # 🔴 BLIND SPOT: Super Admin has NO access to tenant data
        g.tenant_db_path = None
    else:
        # Build tenant DB path: data/{company_id}_user.db
        company_id = user['company_id']
        tenant_filename = f"{company_id}_user.db"
        g.tenant_db_path = os.path.join(DATA_DIR, tenant_filename)

        # Initialize tenant DB if it doesn't exist
        if not os.path.exists(g.tenant_db_path):
            _init_tenant_db(g.tenant_db_path)

    return None  # Continue to the route handler


def _init_tenant_db(tenant_path: str):
    """Initialize a tenant's user.db with required tables."""
    from etl.pipeline import init_inventory_tables
    init_inventory_tables(tenant_path)
    # Also create favorites table
    conn = sqlite3.connect(tenant_path)
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
    logger.info(f"Tenant database initialized: {tenant_path}")


@app.context_processor
def inject_user():
    """Make user data available in all Jinja templates."""
    return {
        'current_user': g.get('user', None),
    }


from flask import redirect

def get_chemicals_db_connection():
    conn = sqlite3.connect(CHEMICALS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_db_connection():
    """
    Get connection to the current tenant's user database.
    Uses g.tenant_db_path for multi-tenant isolation.
    Falls back to legacy USER_DB_PATH if no tenant context.
    """
    db_path = getattr(g, 'tenant_db_path', None) or USER_DB_PATH

    if not os.path.exists(db_path):
        _init_tenant_db(db_path)

    conn = sqlite3.connect(db_path)
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


def init_inventory_tables():
    """Initialize Phase 2 inventory persistence tables in user.db."""
    try:
        sql_path = Path(BASE_DIR) / 'scripts' / 'create_inventory_tables.sql'
        if not sql_path.exists():
            logger.warning("Phase 2 SQL file not found: %s", sql_path)
            return

        conn = sqlite3.connect(USER_DB_PATH)
        with sql_path.open('r', encoding='utf-8') as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()
        logger.info("Phase 2 inventory tables initialized")
    except Exception as e:
        logger.error("Failed to initialize Phase 2 inventory tables: %s", e, exc_info=True)

# Ensure user db is initialized on startup
if not os.path.exists(USER_DB_PATH):
    init_user_db()

init_inventory_tables()

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
    return render_template('dashboard.html')

@app.route('/dashboard')
def dashboard_page():
    """Render the dashboard"""
    return render_template('dashboard.html')

@app.route('/inventory')
def inventory_page():
    """Render the inventory management page"""
    return render_template('inventory.html')

@app.route('/mixer')
def mixer_page():
    """Render the chemical mixer UI (Matrix Analysis)"""
    return render_template('mixer.html')

@app.route('/warehouse')
def warehouse_page():
    """Render the warehouse overview page"""
    return render_template('warehouse.html')

@app.route('/logs')
def logs_page():
    """Render the activity logs page"""
    return render_template('logs.html')


@app.route('/api/inventory/batches', methods=['GET'])
def list_inventory_batches():
    """List all uploaded inventory batches for the inventory management page."""
    try:
        if not os.path.exists(USER_DB_PATH):
            return jsonify({'batches': []})
        conn = sqlite3.connect(USER_DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, filename, status, created_at, total_rows, matched_rows
            FROM inventory_batches
            ORDER BY created_at DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        conn.close()
        return jsonify({'batches': [dict(r) for r in rows]})
    except Exception as e:
        logger.error(f"Batches list error: {e}")
        return jsonify({'batches': []})


@app.route('/api/matrix/data', methods=['GET'])
def matrix_data():
    """
    Return compatibility matrix data as pure JSON for JS virtual-scroll rendering.
    Supports: optional ?ids=1,2,3  or ?limit=N (default 50) for exploration.
    """
    try:
        limit = min(int(request.args.get('limit', 50)), 500)
        ids_param = request.args.get('ids', '')

        conn = get_chemicals_db_connection()
        cursor = conn.cursor()

        if ids_param:
            id_list = [int(x) for x in ids_param.split(',') if x.strip().isdigit()]
            if not id_list:
                return jsonify({'chemicals': [], 'matrix': [], 'total': 0})
            placeholders = ','.join('?' * len(id_list))
            cursor.execute(
                f"SELECT id, name, formula, cas_number FROM chemicals WHERE id IN ({placeholders})",
                id_list
            )
        else:
            cursor.execute(
                "SELECT id, name, formula, cas_number FROM chemicals LIMIT ?", (limit,)
            )

        chemicals = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not chemicals:
            return jsonify({'chemicals': [], 'matrix': [], 'total': 0})

        # Build matrix via reactivity engine
        chem_ids = [c['id'] for c in chemicals]
        analysis = reactivity_engine.analyze(chemical_ids=chem_ids)

        # Flatten full matrix as list-of-lists of status strings
        # Format per cell: {"status": "C"|"I"|"IC"|"N", "label": str}
        n = len(chemicals)
        matrix_rows = []
        detail_map = {}
        if analysis.get('matrix'):
            for pair_key, pair_data in analysis['matrix'].items():
                detail_map[pair_key] = pair_data

        for i, chem_i in enumerate(chemicals):
            row = []
            for j, chem_j in enumerate(chemicals):
                if i == j:
                    row.append({'status': 'SELF', 'label': '—'})
                elif j > i:
                    row.append({'status': 'UPPER', 'label': ''})
                else:
                    key = f"{min(chem_i['id'], chem_j['id'])}-{max(chem_i['id'], chem_j['id'])}"
                    pair = detail_map.get(key, {})
                    status = pair.get('compatibility', 'UNKNOWN')
                    # Normalise status strings from engine
                    if hasattr(status, 'value'):
                        status = status.value
                    status_str = str(status).upper()
                    if 'INCOMPATIBLE' in status_str:
                        label = 'I'
                    elif 'CAUTION' in status_str:
                        label = 'C!'
                    elif 'COMPATIBLE' in status_str:
                        label = 'C'
                    else:
                        label = '?'
                    row.append({'status': status_str, 'label': label, 'key': key})
            matrix_rows.append(row)

        return jsonify({
            'chemicals': chemicals,
            'matrix': matrix_rows,
            'total': len(chemicals),
            'critical_pairs': analysis.get('critical_pairs', []),
        })
    except Exception as e:
        logger.error(f"Matrix data error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'chemicals': [], 'matrix': [], 'total': 0}), 500


@app.route('/api/dashboard/stats', methods=['GET'])
def dashboard_stats():
    """Get dashboard KPI stats"""
    try:
        conn = get_chemicals_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM chemicals")
        total_chemicals = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM reacts")
        total_groups = cursor.fetchone()['total']

        # Count total possible pairs
        n = total_chemicals
        total_pairs = n * (n - 1) // 2 if n > 1 else 0

        conn.close()
        return jsonify({
            'success': True,
            'data': {
                'total_chemicals': total_chemicals,
                'total_reactive_groups': total_groups,
                'total_pairs': total_pairs,
                # Simulated stats for demo (will be real when matrix computed)
                'safe_pairs': int(total_pairs * 0.60),
                'caution_pairs': int(total_pairs * 0.25),
                'critical_pairs': int(total_pairs * 0.15),
            }
        })
    except Exception as e:
        logger.error(f"Dashboard stats error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/warehouse', methods=['GET'])
def get_warehouses():
    """Get warehouse overview data"""
    try:
        user_db = USER_DB_PATH
        if os.path.exists(user_db):
            conn = sqlite3.connect(user_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Get distinct locations from inventory staging
            cursor.execute("""
                SELECT DISTINCT
                    json_extract(cleaned_data, '$.location') as location,
                    COUNT(*) as chemical_count,
                    SUM(CASE WHEN match_status = 'MATCHED' THEN 1 ELSE 0 END) as matched,
                    MAX(created_at) as last_updated
                FROM inventory_staging
                WHERE json_extract(cleaned_data, '$.location') IS NOT NULL
                  AND json_extract(cleaned_data, '$.location') != ''
                GROUP BY location
                ORDER BY chemical_count DESC
                LIMIT 20
            """)
            rows = cursor.fetchall()
            conn.close()

            warehouses = []
            for r in rows:
                loc = r['location'] or 'Unknown'
                matched = r['matched'] or 0
                total = r['chemical_count'] or 1
                safety_pct = int((matched / total) * 100)
                warehouses.append({
                    'name': loc,
                    'chemical_count': total,
                    'matched': matched,
                    'safety_pct': safety_pct,
                    'status': 'safe' if safety_pct > 80 else ('warning' if safety_pct > 50 else 'danger'),
                    'last_updated': r['last_updated'] or 'N/A',
                })

            if warehouses:
                return jsonify({'success': True, 'data': warehouses})

        # Return demo data if no real data
        demo = [
            {'name': 'Acid Storage A', 'chemical_count': 24, 'matched': 22, 'safety_pct': 92, 'status': 'safe', 'last_updated': '2025-02-18'},
            {'name': 'Flammables Shed B', 'chemical_count': 18, 'matched': 12, 'safety_pct': 67, 'status': 'warning', 'last_updated': '2025-02-17'},
            {'name': 'Tank Farm C', 'chemical_count': 32, 'matched': 30, 'safety_pct': 94, 'status': 'safe', 'last_updated': '2025-02-19'},
            {'name': 'Lab Storage D', 'chemical_count': 56, 'matched': 40, 'safety_pct': 71, 'status': 'warning', 'last_updated': '2025-02-15'},
            {'name': 'Oxidizer Vault E', 'chemical_count': 12, 'matched': 5, 'safety_pct': 42, 'status': 'danger', 'last_updated': '2025-02-10'},
            {'name': 'General Storage F', 'chemical_count': 44, 'matched': 44, 'safety_pct': 100, 'status': 'safe', 'last_updated': '2025-02-19'},
        ]
        return jsonify({'success': True, 'data': demo})
    except Exception as e:
        logger.error(f"Warehouse error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/logs', methods=['GET'])
def get_activity_logs():
    """Get activity log entries"""
    try:
        user_db = USER_DB_PATH
        logs = []
        if os.path.exists(user_db):
            conn = sqlite3.connect(user_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get audit trail
            try:
                cursor.execute("""
                    SELECT at.timestamp, at.action, at.method,
                           ib.filename, at.confidence, at.batch_id
                    FROM audit_trail at
                    LEFT JOIN inventory_batches ib ON at.batch_id = ib.id
                    ORDER BY at.timestamp DESC
                    LIMIT 50
                """)
                for row in cursor.fetchall():
                    logs.append({
                        'id': len(logs) + 1,
                        'type': row['action'] or 'unknown',
                        'title': _log_title(row['action'], row['filename']),
                        'detail': f"Method: {row['method'] or 'N/A'} | Confidence: {int((row['confidence'] or 0)*100)}%",
                        'timestamp': row['timestamp'] or '',
                        'user': 'Admin',
                        'category': _log_category(row['action']),
                    })
            except Exception:
                pass  # Table may not exist yet

            # Get batch events
            try:
                cursor.execute("""
                    SELECT id, filename, status, created_at, total_rows, matched_rows
                    FROM inventory_batches
                    ORDER BY created_at DESC
                    LIMIT 20
                """)
                for row in cursor.fetchall():
                    logs.append({
                        'id': len(logs) + 1,
                        'type': 'upload',
                        'title': f"File uploaded: {row['filename'] or 'unknown'}",
                        'detail': f"Status: {row['status']} | {row['matched_rows'] or 0}/{row['total_rows'] or 0} rows matched",
                        'timestamp': row['created_at'] or '',
                        'user': 'Admin',
                        'category': 'import',
                    })
            except Exception:
                pass

            conn.close()

        if not logs:
            # Demo data
            from datetime import datetime, timedelta
            now = datetime.utcnow()
            logs = [
                {'id':1,'type':'upload','title':'File uploaded: inventory_q1.xlsx','detail':'287 rows imported, 264 matched','timestamp':(now - timedelta(hours=2)).isoformat(),'user':'Admin','category':'import'},
                {'id':2,'type':'analysis','title':'Compatibility analysis started','detail':'Analyzing 287 chemicals for reactive pairs','timestamp':(now - timedelta(hours=2, minutes=5)).isoformat(),'user':'System','category':'analysis'},
                {'id':3,'type':'alert','title':'Critical incompatibility found','detail':'HNO3 + Acetone: Explosion risk detected','timestamp':(now - timedelta(hours=2, minutes=6)).isoformat(),'user':'System','category':'alert'},
                {'id':4,'type':'edit','title':'Chemical edited: Acetone','detail':'Location updated to Flammables Shed B','timestamp':(now - timedelta(days=1)).isoformat(),'user':'Admin','category':'edit'},
                {'id':5,'type':'upload','title':'File uploaded: chemicals_backup.csv','detail':'52 rows imported, 50 matched','timestamp':(now - timedelta(days=2)).isoformat(),'user':'Admin','category':'import'},
                {'id':6,'type':'delete','title':'Chemical removed: Ethyl acetate','detail':'Removed from Lab Storage D by Admin','timestamp':(now - timedelta(days=3)).isoformat(),'user':'Admin','category':'edit'},
            ]

        # Sort by timestamp desc
        logs.sort(key=lambda x: x['timestamp'], reverse=True)
        return jsonify({'success': True, 'data': logs})
    except Exception as e:
        logger.error(f"Logs error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def _log_title(action, filename):
    mapping = {
        'upload': f"File uploaded: {filename or 'unknown'}",
        'match': 'Chemical matched automatically',
        'manual_review': 'Manual review completed',
        'column_map': 'Column mapping detected',
    }
    return mapping.get(action, action or 'System event')


def _log_category(action):
    if action in ('upload',): return 'import'
    if action in ('match', 'manual_review'): return 'analysis'
    return 'system'


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
