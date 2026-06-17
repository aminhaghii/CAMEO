import os
import json
import sqlite3
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app, g

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility, COMPATIBILITY_MAP, WATER_GROUP_ID
from auth.decorators import login_required, csrf_protect, viewer_readonly
from db_utils import get_safe_connection
from activity_logger import log_event

logger = logging.getLogger(__name__)

warehouse_bp = Blueprint('warehouse', __name__)


@warehouse_bp.before_request
def _enforce_tenant_context():
    """Fail-closed guard: reject ALL warehouse requests without a tenant DB context.

    Runs before every route handler in this blueprint, so abort(403) here
    bypasses the route's own except-Exception block and produces a clean 403.
    """
    tenant_db = getattr(g, 'tenant_db_path', None)
    if not tenant_db:
        return jsonify({
            'error': 'Tenant context required. Super Admins cannot access tenant-specific routes directly.',
            'code': 'NO_TENANT_CONTEXT'
        }), 403

ADMIN_OVERRIDE_ROLES = {'company_admin', 'super_admin'}

# Hard conflict on manual save/move: always blocked, no override possible.
# Only INCOMPATIBLE (red) is a true hard block here; CAUTION/NO_DATA remain
# admin-overridable (see _validate_layout_update).
SECTION_CONFLICT_COMPATIBILITIES = {
    Compatibility.INCOMPATIBLE,
}

# Conflict edges for the auto-arrange solver. Policy: the algorithm isolates
# both INCOMPATIBLE (red) and NO_DATA (orange/unknown) by default — unknowns are
# kept apart for safety. CAUTION (yellow) is allowed to co-locate.
AUTO_ARRANGE_CONFLICT_COMPATIBILITIES = {
    Compatibility.INCOMPATIBLE,
    Compatibility.NO_DATA,
}

def _get_db_path():
    # before_request already guaranteed tenant_db_path is set; this is a defensive fallback.
    tenant_db = getattr(g, 'tenant_db_path', None)
    if not tenant_db:
        from flask import abort
        abort(403, description="Tenant context required.")
    return tenant_db

def _get_db_connection():
    db_path = _get_db_path()
    from etl.pipeline import init_inventory_tables
    init_inventory_tables(db_path)
    return get_safe_connection(db_path)

def _current_role() -> str:
    return g.user.get('role', '') if (hasattr(g, 'user') and g.user) else ''

def _current_user_id() -> str:
    if not (hasattr(g, 'user') and g.user):
        return 'system'
    return str(g.user.get('email') or g.user.get('id') or 'unknown')

def _write_access_error():
    if _current_role() == 'viewer':
        return jsonify({'error': 'Viewer role is read-only', 'code': 'READ_ONLY_ROLE'}), 403
    return None

def _parse_layout_mapping(layout: dict) -> dict:
    """Return {placement_id: section_id_or_none}; reject malformed payloads."""
    if not isinstance(layout, dict) or not layout:
        raise ValueError('layout mapping is required')
    parsed = {}
    for p_id_raw, sec_id_raw in layout.items():
        try:
            p_id = int(p_id_raw)
        except (TypeError, ValueError):
            raise ValueError(f'invalid placement id: {p_id_raw}')
        if sec_id_raw in (None, ''):
            parsed[p_id] = None
            continue
        try:
            parsed[p_id] = int(sec_id_raw)
        except (TypeError, ValueError):
            raise ValueError(f'invalid section id for placement {p_id}: {sec_id_raw}')
    return parsed

def _validate_layout_update(conn, updates: dict, actor_role: str):
    """
    Validate the final section state before persisting a move/layout.

    Safety policy:
    - INCOMPATIBLE pairs are always blocked.
    - CAUTION/NO_DATA pairs require company_admin or super_admin override.
    - Section targets must belong to the same warehouse as their placement.
    """
    cursor = conn.cursor()
    placement_ids = list(updates.keys())
    placeholders = ",".join(["?"] * len(placement_ids))
    cursor.execute(
        f"SELECT * FROM chemical_placements WHERE id IN ({placeholders})",
        placement_ids
    )
    moving = {row['id']: row for row in cursor.fetchall()}
    missing = sorted(set(placement_ids) - set(moving.keys()))
    if missing:
        return False, 404, {'error': f'Placement not found: {missing[0]}'}

    target_section_ids = sorted({sid for sid in updates.values() if sid is not None})
    sections_by_id = {}
    if target_section_ids:
        section_placeholders = ",".join(["?"] * len(target_section_ids))
        cursor.execute(
            f"SELECT id, name, warehouse_id FROM warehouse_sections WHERE id IN ({section_placeholders})",
            target_section_ids
        )
        sections_by_id = {row['id']: row for row in cursor.fetchall()}
        missing_sections = sorted(set(target_section_ids) - set(sections_by_id.keys()))
        if missing_sections:
            return False, 404, {'error': f'Target section not found: {missing_sections[0]}'}

    affected_warehouse_ids = sorted({row['warehouse_id'] for row in moving.values()})
    for p_id, target_section_id in updates.items():
        if target_section_id is None:
            continue
        placement_warehouse_id = moving[p_id]['warehouse_id']
        target_warehouse_id = sections_by_id[target_section_id]['warehouse_id']
        if target_warehouse_id != placement_warehouse_id:
            return False, 409, {
                'error': 'Target section belongs to a different warehouse',
                'code': 'WAREHOUSE_MISMATCH'
            }

    warehouse_placeholders = ",".join(["?"] * len(affected_warehouse_ids))
    cursor.execute(
        f"SELECT * FROM chemical_placements WHERE warehouse_id IN ({warehouse_placeholders})",
        affected_warehouse_ids
    )
    final_by_section = {}
    for row in cursor.fetchall():
        final_section_id = updates.get(row['id'], row['section_id'])
        if final_section_id is None:
            continue
        final_by_section.setdefault(final_section_id, []).append(row)

    engine = ReactivityEngine(current_app.config['CHEMICALS_DB_PATH'])
    caution_sections = []
    for section_id, occupants in final_by_section.items():
        if len(occupants) < 2:
            continue
        # Double check all pairs using the precise section conflict rules (including water reactivity)
        for i, p_a in enumerate(occupants):
            for p_b in occupants[i + 1:]:
                if _is_section_conflict(engine, p_a, p_b):
                    return False, 409, {
                        'error': f"Safety Block: Incompatible chemicals in section ({p_a['chemical_name']} & {p_b['chemical_name']})",
                        'code': 'INCOMPATIBLE'
                    }

        chem_ids = [row['chemical_id'] for row in occupants]
        analysis = engine.analyze(chem_ids, include_water_check=True, save_audit=False)
        if analysis.overall_compatibility in (Compatibility.CAUTION, Compatibility.NO_DATA):
            caution_sections.append(section_id)

    if caution_sections and actor_role not in ADMIN_OVERRIDE_ROLES:
        return False, 403, {
            'error': 'Access Denied: Placement requires CAUTION/NO_DATA override',
            'code': 'CAUTION_REQUIRES_ADMIN',
            'sections': caution_sections
        }

    return True, 200, {'success': True}

def _get_chem_groups(chemical_id: int) -> list:
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn = get_safe_connection(chemicals_db, readonly=True)
    cursor = conn.cursor()
    cursor.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (chemical_id,))
    groups = [r[0] for r in cursor.fetchall()]
    conn.close()
    return groups

def _get_compatibility_rules(group_ids: list) -> dict:
    """Fetch compatibility rules between the given list of group IDs."""
    if not group_ids:
        return {}
    engine = ReactivityEngine(current_app.config['CHEMICALS_DB_PATH'])
    rules = {}
    unique_group_ids = sorted({int(g) for g in group_ids})
    for g1 in unique_group_ids:
        for g2 in unique_group_ids:
            rule = engine._get_rule(g1, g2)
            rules[f"{g1}|{g2}"] = rule['compatibility'].value
    return rules

@warehouse_bp.route('/api/warehouse/data', methods=['GET'])
@login_required
def get_warehouse_data():
    """Get warehouse sections, placements, inventory, and compatibility rules."""
    try:
        warehouse_id = request.args.get('warehouse_id')
        
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        # Determine current warehouse scope
        if not warehouse_id:
            cursor.execute("SELECT id FROM warehouses ORDER BY name LIMIT 1")
            row = cursor.fetchone()
            if row:
                warehouse_id = row['id']
            else:
                conn.close()
                return jsonify({
                    'success': True,
                    'warehouse_id': None,
                    'sections': [],
                    'inventory': [],
                    'chemical_groups': {},
                    'reactive_rules': {}
                })
        else:
            try:
                warehouse_id = int(warehouse_id)
            except ValueError:
                conn.close()
                return jsonify({'error': 'warehouse_id must be integer'}), 400

        # 1. Fetch sections
        cursor.execute(
            "SELECT id, name, position_index, color FROM warehouse_sections WHERE warehouse_id = ? ORDER BY position_index",
            (warehouse_id,)
        )
        sections = [
            {
                'id': row['id'],
                'name': row['name'],
                'position_index': row['position_index'],
                'color': row['color'] or 'slate',
                'chemicals': []
            }
            for row in cursor.fetchall()
        ]
        
        # 2. Fetch placements
        cursor.execute(
            "SELECT id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups, section_id, status FROM chemical_placements WHERE warehouse_id = ?",
            (warehouse_id,)
        )
        placements = cursor.fetchall()
        
        inventory_pool = []
        chem_groups_map = {}
        all_unique_groups = set()
        
        for p in placements:
            try:
                groups = json.loads(p['reactive_groups']) if p['reactive_groups'] else []
            except Exception:
                groups = []
            
            chem_id = p['chemical_id']
            chem_groups_map[chem_id] = groups
            all_unique_groups.update(groups)
            
            is_wr = _is_water_reactive(chem_id, current_app.config['CHEMICALS_DB_PATH'])
            
            placement_obj = {
                'id': p['id'],
                'placement_id': p['id'],
                'chemical_id': chem_id,
                'chemical_name': p['chemical_name'],
                'cas_number': p['cas_number'],
                'quantity_kg': p['quantity_kg'],
                'reactive_groups': groups,
                'water_reactive': is_wr,
                'status': p['status']
            }
            
            if p['section_id'] is None:
                inventory_pool.append(placement_obj)
            else:
                for sec in sections:
                    if sec['id'] == p['section_id']:
                        sec['chemicals'].append(placement_obj)
                        break
                        
        # 3. Fetch compatibility rules for all groups currently in the warehouse
        rules = _get_compatibility_rules(list(all_unique_groups))
        
        conn.close()
        
        return jsonify({
            'success': True,
            'warehouse_id': warehouse_id,
            'sections': sections,
            'inventory': inventory_pool,
            'chemical_groups': chem_groups_map,
            'reactive_rules': rules
        })
    except Exception as e:
        logger.error(f"Failed to fetch warehouse data: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/sections/init', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def init_sections():
    """Initialize warehouse sections."""
    try:
        data = request.get_json(silent=True) or {}
        count = data.get('count', 10)
        warehouse_id = data.get('warehouse_id')
        
        try:
            count = int(count)
        except (ValueError, TypeError):
            return jsonify({'error': 'count must be integer'}), 400
            
        if count <= 0 or count > 50:
            return jsonify({'error': 'count must be between 1 and 50'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        if not warehouse_id:
            cursor.execute("SELECT id FROM warehouses ORDER BY name LIMIT 1")
            row = cursor.fetchone()
            if row:
                warehouse_id = row['id']
            else:
                conn.close()
                return jsonify({'error': 'No warehouses exist.'}), 400
        else:
            try:
                warehouse_id = int(warehouse_id)
            except ValueError:
                conn.close()
                return jsonify({'error': 'warehouse_id must be integer'}), 400
            
        # 1. Fetch current sections in this warehouse, sorted by position_index
        cursor.execute(
            "SELECT id, name, position_index FROM warehouse_sections WHERE warehouse_id = ? ORDER BY position_index",
            (warehouse_id,)
        )
        current_sections = [dict(r) for r in cursor.fetchall()]
        current_count = len(current_sections)
        
        if current_count == 0:
            # First time initialization: just insert count sections
            for i in range(1, count + 1):
                cursor.execute(
                    "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                    (warehouse_id, f"Section {i}", i - 1, 'slate')
                )
        elif count > current_count:
            # We are ADDING sections. Keep all existing ones, and add new ones.
            max_pos = max(s['position_index'] for s in current_sections) if current_sections else -1
            for i in range(current_count + 1, count + 1):
                pos = max_pos + (i - current_count)
                cursor.execute(
                    "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                    (warehouse_id, f"Section {i}", pos, 'slate')
                )
        elif count < current_count:
            # We are REMOVING sections. Keep the first 'count' sections, delete the rest.
            sections_to_delete = current_sections[count:]
            delete_ids = [s['id'] for s in sections_to_delete]
            placeholders = ",".join(["?"] * len(delete_ids))
            
            # Return placed chemicals in deleted sections to the available pool
            cursor.execute(
                f"UPDATE chemical_placements SET section_id = NULL WHERE section_id IN ({placeholders})",
                delete_ids
            )
            # Delete the sections
            cursor.execute(
                f"DELETE FROM warehouse_sections WHERE id IN ({placeholders})",
                delete_ids
            )
            
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': f'{count} sections initialized'})
    except Exception as e:
        logger.error(f"Init sections failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/sections/update', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def update_section():
    """Rename a warehouse section."""
    try:
        data = request.get_json(silent=True) or {}
        section_id = data.get('section_id')
        name = (data.get('name') or '').strip()
        
        if not section_id or not name:
            return jsonify({'error': 'section_id and name are required'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("UPDATE warehouse_sections SET name = ? WHERE id = ?", (name, section_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Section renamed successfully'})
    except Exception as e:
        logger.error(f"Update section failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/placements/move', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def move_placement():
    """Move a chemical placement to another section or to the sidebar pool (section_id=None)."""
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        placement_id = data.get('placement_id')
        section_id = data.get('section_id') # Can be None/null to move back to sidebar pool
        
        if placement_id is None:
            return jsonify({'error': 'placement_id is required'}), 400
        try:
            placement_id = int(placement_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'placement_id must be integer'}), 400
            
        conn = _get_db_connection()
        conn.execute('BEGIN EXCLUSIVE')
        cursor = conn.cursor()
        
        # Verify placement exists
        cursor.execute("SELECT * FROM chemical_placements WHERE id = ?", (placement_id,))
        placement = cursor.fetchone()
        if not placement:
            conn.rollback()
            conn.close()
            return jsonify({'error': 'Placement not found'}), 404
            
        chem_id = placement['chemical_id']
        chem_name = placement['chemical_name']
        
        if section_id is not None:
            try:
                section_id = int(section_id)
            except (TypeError, ValueError):
                conn.rollback()
                conn.close()
                return jsonify({'error': 'section_id must be integer or null'}), 400

            ok, status_code, payload = _validate_layout_update(conn, {placement_id: section_id}, _current_role())
            if not ok:
                conn.rollback()
                conn.close()
                return jsonify(payload), status_code

            # Fetch target section after validation for audit output.
            cursor.execute("SELECT name FROM warehouse_sections WHERE id = ?", (section_id,))
            sec = cursor.fetchone()

            # Move placement to the section
            cursor.execute("UPDATE chemical_placements SET section_id = ? WHERE id = ?", (section_id, placement_id))
            action = 'place_chemical'
            out_data = json.dumps({'section_id': section_id, 'section_name': sec['name']})
        else:
            ok, status_code, payload = _validate_layout_update(conn, {placement_id: None}, _current_role())
            if not ok:
                conn.rollback()
                conn.close()
                return jsonify(payload), status_code

            # Move placement back to sidebar pool (unplaced)
            cursor.execute("UPDATE chemical_placements SET section_id = NULL WHERE id = ?", (placement_id,))
            action = 'unplace_chemical'
            out_data = json.dumps({'section_id': None})
            
        # Log to audit trail
        cursor.execute(
            """
            INSERT INTO audit_trail (batch_id, row_index, action, input_data, output_data, confidence, method, user_id)
            VALUES (?, ?, ?, ?, ?, 1.0, 'warehouse_placement', ?)
            """,
            (
                'warehouse',
                None,
                action,
                json.dumps({'chemical_id': chem_id, 'chemical_name': chem_name}),
                out_data,
                _current_user_id()
            )
        )
        
        conn.commit()
        # Activity log
        log_event(
            db_path=_get_db_path(),
            event_type=action,
            category='warehouse',
            severity='info',
            title='Chemical moved in warehouse' if action == 'place_chemical' else 'Chemical removed from section',
            detail=f"{chem_name} — {action.replace('_', ' ')}",
            user_id=_current_user_id(),
            entity_type='chemical',
            entity_id=str(chem_id),
            entity_name=chem_name,
            meta={'action': action},
        )
        conn.close()
        return jsonify({'success': True, 'message': 'Chemical moved successfully'})
    except Exception as e:
        logger.error(f"Move placement failed: {e}", exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/placements/remove/<int:placement_id>', methods=['DELETE'])
@login_required
@viewer_readonly
@csrf_protect
def remove_placement(placement_id):
    """Delete a placement completely from the warehouse database."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT chemical_id, chemical_name FROM chemical_placements WHERE id = ?", (placement_id,))
        p = cursor.fetchone()
        if not p:
            conn.close()
            return jsonify({'error': 'Placement not found'}), 404
            
        cursor.execute("DELETE FROM chemical_placements WHERE id = ?", (placement_id,))
        
        # Log to audit trail
        cursor.execute(
            """
            INSERT INTO audit_trail (batch_id, row_index, action, input_data, output_data, confidence, method, user_id)
            VALUES (?, ?, ?, ?, ?, 1.0, 'warehouse_placement', ?)
            """,
            (
                'warehouse',
                None,
                'remove_chemical',
                json.dumps({'chemical_id': p['chemical_id'], 'chemical_name': p['chemical_name']}),
                json.dumps({'deleted': True}),
                _current_user_id()
            )
        )
        
        conn.commit()
        
        log_event(
            db_path=_get_db_path(),
            event_type='remove_chemical',
            category='warehouse',
            severity='info',
            title=f"Chemical removed from warehouse — {p['chemical_name']}",
            detail=f"Placement ID: {placement_id} deleted completely",
            user_id=_current_user_id(),
            entity_type='chemical',
            entity_id=str(p['chemical_id']),
            entity_name=p['chemical_name'],
            meta={'placement_id': placement_id},
        )
        
        conn.close()
        return jsonify({'success': True, 'message': 'Placement removed completely'})
    except Exception as e:
        logger.error(f"Remove placement failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/add_from_batch', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def add_from_batch():
    """Import finalized batch rows to the warehouse available pool."""
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        batch_id = data.get('batch_id')
        warehouse_id = data.get('warehouse_id')
        warehouse_name = (data.get('warehouse_name') or 'Default Warehouse').strip()
        
        if not batch_id:
            return jsonify({'error': 'batch_id is required'}), 400
            
        conn = _get_db_connection()
        conn.execute('BEGIN EXCLUSIVE')
        cursor = conn.cursor()
        
        if not warehouse_id:
            if warehouse_name:
                cursor.execute("SELECT id FROM warehouses WHERE name = ?", (warehouse_name,))
                row = cursor.fetchone()
                if row:
                    warehouse_id = row['id']
                else:
                    cursor.execute("INSERT INTO warehouses (name) VALUES (?)", (warehouse_name,))
                    warehouse_id = cursor.lastrowid
                    for i in range(1, 11):
                        cursor.execute(
                            "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                            (warehouse_id, f"Section {i}", i - 1, 'slate')
                        )
            else:
                cursor.execute("SELECT id FROM warehouses ORDER BY name LIMIT 1")
                row = cursor.fetchone()
                if row:
                    warehouse_id = row['id']
                else:
                    cursor.execute("INSERT INTO warehouses (name) VALUES ('Main Warehouse')")
                    warehouse_id = cursor.lastrowid
                    for i in range(1, 11):
                        cursor.execute(
                            "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                            (warehouse_id, f"Section {i}", i - 1, 'slate')
                        )
        else:
            try:
                warehouse_id = int(warehouse_id)
            except ValueError:
                conn.close()
                return jsonify({'error': 'warehouse_id must be integer'}), 400
            cursor.execute("SELECT id FROM warehouses WHERE id = ?", (warehouse_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({'error': 'warehouse_id not found'}), 404
            
        # Fetch matching rows from staging
        cursor.execute(
            "SELECT id, chemical_id, cleaned_data FROM inventory_staging WHERE batch_id = ? AND match_status = 'MATCHED'",
            (batch_id,)
        )
        rows = cursor.fetchall()

        # Count total and skipped rows for reporting
        cursor.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN match_status = 'MATCHED' THEN 1 ELSE 0 END) as matched FROM inventory_staging WHERE batch_id = ?",
            (batch_id,)
        )
        count_row = cursor.fetchone()
        total_rows = count_row['total'] if count_row else 0
        matched_rows = count_row['matched'] if count_row else 0
        skipped_count = total_rows - matched_rows

        if not rows:
            conn.close()
            return jsonify({'error': 'No matched chemicals found in this batch'}), 400
            
        imported_count = 0
        for r in rows:
            chem_id = r['chemical_id']
            if not chem_id:
                continue
            cleaned = {}
            try:
                cleaned = json.loads(r['cleaned_data']) if r['cleaned_data'] else {}
            except Exception:
                pass
                
            chem_name = cleaned.get('name', '')
            cas_number = cleaned.get('cas', '')
            qty_str = cleaned.get('quantity', '1.0')
            unit_str = cleaned.get('unit', 'kg').lower()
            
            # Parse quantity to kg
            CONTAINER_KG = {
                'drum': 200, 'drums': 200,
                'cylinder': 50, 'cylinders': 50,
                'bottle': 2, 'bottles': 2,
                'jug': 4, 'jugs': 4,
                'container': 10, 'containers': 10,
                'tank': 500, 'tanks': 500,
                'pail': 20, 'pails': 20,
                'bag': 25, 'bags': 25,
                'sack': 50, 'sacks': 50,
                'tote': 1000, 'totes': 1000,
                'keg': 60, 'kegs': 60,
            }
            try:
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
            except (ValueError, TypeError):
                qty = None
                
            groups = _get_chem_groups(chem_id)
            groups_json = json.dumps(groups)
            
            staging_row_id = r['id']
            # Fix 1.4: INSERT OR IGNORE relies on UNIQUE(warehouse_id, batch_id, staging_row_id)
            # to atomically prevent duplicates — no separate SELECT needed.
            cursor.execute(
                """
                INSERT OR IGNORE INTO chemical_placements
                    (warehouse_id, section_id, chemical_id, chemical_name, cas_number,
                     quantity_kg, reactive_groups, status, batch_id, staging_row_id)
                VALUES (?, NULL, ?, ?, ?, ?, ?, 'placed', ?, ?)
                """,
                (warehouse_id, chem_id, chem_name, cas_number, qty,
                 groups_json, batch_id, staging_row_id)
            )
            if cursor.rowcount:
                imported_count += 1
            
        # Log to audit trail
        cursor.execute(
            """
            INSERT INTO audit_trail (batch_id, row_index, action, input_data, output_data, confidence, method, user_id)
            VALUES (?, ?, ?, ?, ?, 1.0, 'warehouse_import', ?)
            """,
            (
                batch_id,
                None,
                'import_batch_to_warehouse',
                json.dumps({'warehouse_name': warehouse_name}),
                json.dumps({'chemicals_imported': imported_count}),
                _current_user_id()
            )
        )
        
        conn.commit()
        # Activity log
        log_event(
            db_path=_get_db_path(),
            event_type='import_batch_to_warehouse',
            category='import',
            severity='info',
            title=f'Batch imported to warehouse — {imported_count} chemicals',
            detail=f"Warehouse: {warehouse_name} | Imported: {imported_count} | Skipped: {skipped_count}",
            user_id=_current_user_id(),
            entity_type='batch',
            entity_id=str(batch_id),
            entity_name=warehouse_name,
            meta={'imported_count': imported_count, 'skipped_count': skipped_count, 'warehouse_id': warehouse_id},
        )
        conn.close()
        
        return jsonify({
            'success': True, 
            'warehouse_id': warehouse_id,
            'imported_count': imported_count,
            'skipped_count': skipped_count,
            'message': f'Successfully imported {imported_count} chemicals to the warehouse pool.'
                       + (f' {skipped_count} rows skipped (not yet matched).' if skipped_count > 0 else '')
        })
    except Exception as e:
        logger.error(f"Import batch to warehouse failed: {e}", exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        return jsonify({'error': 'Internal server error'}), 500

def _groups_for_placement(placement):
    if hasattr(placement, 'get'):
        groups = placement.get('reactive_groups') or []
    else:
        try:
            groups = placement['reactive_groups'] or []
        except (KeyError, TypeError, IndexError):
            groups = []
    if isinstance(groups, str):
        try:
            groups = json.loads(groups)
        except Exception:
            groups = []
    try:
        return [int(g) for g in groups]
    except (TypeError, ValueError):
        return []

def _is_water_reactive(chemical_id, chemicals_db_path):
    """Check if a chemical has a water-reactive self-hazard in special_hazards."""
    try:
        conn = get_safe_connection(chemicals_db_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT special_hazards FROM chemicals WHERE id = ?", (chemical_id,))
        row = cur.fetchone()
        conn.close()
        if row and row['special_hazards']:
            special = row['special_hazards'].lower()
            return 'water reactive' in special or 'water-reactive' in special
    except Exception:
        pass
    return False


def _has_water_group(placement):
    """Check if a chemical belongs to the Water reactive group (104)."""
    groups = _groups_for_placement(placement)
    return WATER_GROUP_ID in groups


def _is_section_conflict(engine, placement_a, placement_b, conflict_set=SECTION_CONFLICT_COMPATIBILITIES):
    pair_res = engine._analyze_pair(
        placement_a['chemical_id'],
        placement_b['chemical_id'],
        placement_a['chemical_name'],
        placement_b['chemical_name'],
        _groups_for_placement(placement_a),
        _groups_for_placement(placement_b),
    )
    if pair_res.compatibility in conflict_set:
        return True

    # Water-reactive self-hazard check:
    # If one chemical is water-reactive AND the other has water group, block.
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    a_water_reactive = _is_water_reactive(placement_a['chemical_id'], chemicals_db)
    b_water_reactive = _is_water_reactive(placement_b['chemical_id'], chemicals_db)
    a_has_water = _has_water_group(placement_a)
    b_has_water = _has_water_group(placement_b)

    if (a_water_reactive and b_has_water) or (b_water_reactive and a_has_water):
        return True

    return False

def _build_conflict_graph(placements, engine):
    """Build a graph where edges mean two placements must not share a section.

    Auto-arrange isolates INCOMPATIBLE and NO_DATA pairs (plus water-reactive
    hazards); CAUTION pairs are allowed to share a section.
    """
    adjacency = {p['placement_id']: set() for p in placements}
    by_id = {p['placement_id']: p for p in placements}
    for i, p_a in enumerate(placements):
        for p_b in placements[i + 1:]:
            if _is_section_conflict(engine, p_a, p_b, conflict_set=AUTO_ARRANGE_CONFLICT_COMPATIBILITIES):
                adjacency[p_a['placement_id']].add(p_b['placement_id'])
                adjacency[p_b['placement_id']].add(p_a['placement_id'])
    return adjacency, by_id

def _build_occupants_from_mapping(sections, placements, mapping):
    section_occupants = {s['id']: [] for s in sections}
    by_id = {p['placement_id']: p for p in placements}
    for placement_id, section_id in mapping.items():
        if section_id in section_occupants:
            section_occupants[section_id].append(by_id[placement_id])
    return section_occupants

def _run_greedy_placement(sections, placements, adjacency):
    """Deterministic partial fallback when exact graph coloring cannot finish."""
    ordered = sorted(
        placements,
        key=lambda p: (-len(adjacency[p['placement_id']]), -len(_groups_for_placement(p)), str(p['chemical_name'] or ''), p['placement_id'])
    )
    section_occupants = {s['id']: [] for s in sections}
    mapping = {}
    unplaced = []
    for placement in ordered:
        placement_id = placement['placement_id']
        candidate_sections = sorted(sections, key=lambda s: (len(section_occupants[s['id']]), str(s['id'])))
        target_id = None
        for section in candidate_sections:
            section_id = section['id']
            if all(occupant['placement_id'] not in adjacency[placement_id] for occupant in section_occupants[section_id]):
                target_id = section_id
                break
        mapping[placement_id] = target_id
        if target_id is None:
            unplaced.append(placement)
        else:
            section_occupants[target_id].append(placement)
    return mapping, section_occupants, unplaced

def _run_exact_coloring(sections, placements, adjacency, max_nodes=200000):
    """DSATUR-style exact coloring into the provided warehouse sections."""
    if not placements:
        return {}, {s['id']: [] for s in sections}, []
    if not sections:
        return None

    placement_by_id = {p['placement_id']: p for p in placements}
    placement_ids = list(placement_by_id.keys())
    section_ids = [s['id'] for s in sections]
    mapping = {}
    section_occupants = {section_id: [] for section_id in section_ids}
    visits = 0

    def pick_next():
        unassigned = [pid for pid in placement_ids if pid not in mapping]
        return max(
            unassigned,
            key=lambda pid: (
                len({mapping[n] for n in adjacency[pid] if n in mapping}),
                len(adjacency[pid]),
                len(_groups_for_placement(placement_by_id[pid])),
                str(placement_by_id[pid]['chemical_name']),
            )
        )

    def section_candidates(pid):
        blocked = {mapping[n] for n in adjacency[pid] if n in mapping}
        candidates = [sid for sid in section_ids if sid not in blocked]
        return sorted(candidates, key=lambda sid: (len(section_occupants[sid]), section_ids.index(sid)))

    def search():
        nonlocal visits
        visits += 1
        if visits > max_nodes:
            return False
        if len(mapping) == len(placement_ids):
            return True

        pid = pick_next()
        tried_empty_slot = False
        for sid in section_candidates(pid):
            # Empty sections are symmetrical; trying the first empty one is enough.
            if not section_occupants[sid]:
                if tried_empty_slot:
                    continue
                tried_empty_slot = True

            mapping[pid] = sid
            section_occupants[sid].append(placement_by_id[pid])
            if search():
                return True
            section_occupants[sid].pop()
            del mapping[pid]
        return False

    if not search():
        return None
    return dict(mapping), section_occupants, []

def _run_matrix_placement(sections, placements, engine):
    """
    Place chemicals by solving a conflict graph.

    A conflict edge means two chemicals must not share a section.
    Only INCOMPATIBLE pairs create conflict edges.
    CAUTION/NO_DATA pairs are allowed together (user override available via validation).
    """
    adjacency, _ = _build_conflict_graph(placements, engine)
    exact_result = _run_exact_coloring(sections, placements, adjacency)
    if exact_result is not None:
        return (*exact_result, True, adjacency)

    mapping, section_occupants, unplaced = _run_greedy_placement(sections, placements, adjacency)
    return mapping, section_occupants, unplaced, False, adjacency


@warehouse_bp.route('/api/warehouse/auto_arrange', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def auto_arrange():
    """
    Greedy Auto-Arrange Algorithm:
    Placements sorted by reactive group complexity descending are distributed
    proportionally across Sections, avoiding incompatible conflicts.
    Runs virtual section recommendation simulation if needed.
    """
    try:
        data = request.get_json(silent=True) or {}
        warehouse_id = data.get('warehouse_id')
        
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        if not warehouse_id:
            cursor.execute("SELECT id FROM warehouses ORDER BY name LIMIT 1")
            row = cursor.fetchone()
            if row:
                warehouse_id = row['id']
            else:
                conn.close()
                return jsonify({'error': 'No warehouses exist.'}), 400
        else:
            try:
                warehouse_id = int(warehouse_id)
            except ValueError:
                conn.close()
                return jsonify({'error': 'warehouse_id must be integer'}), 400

        # Fetch sections
        cursor.execute("SELECT id, name FROM warehouse_sections WHERE warehouse_id = ? ORDER BY position_index", (warehouse_id,))
        sections = [dict(r) for r in cursor.fetchall()]
        
        # Fetch placements
        cursor.execute("SELECT id as placement_id, chemical_id, chemical_name, reactive_groups FROM chemical_placements WHERE warehouse_id = ?", (warehouse_id,))
        placements = []
        for r in cursor.fetchall():
            groups = []
            try:
                groups = json.loads(r['reactive_groups']) if r['reactive_groups'] else []
            except Exception:
                pass
            placements.append({
                'placement_id': r['placement_id'],
                'chemical_id': r['chemical_id'],
                'chemical_name': r['chemical_name'],
                'reactive_groups': groups
            })
        
        conn.close()

        if not sections:
            return jsonify({'error': 'No sections configured in this warehouse'}), 400
        if not placements:
            return jsonify({'error': 'No chemical placements in this warehouse'}), 400

        # Run analysis
        engine = ReactivityEngine(current_app.config['CHEMICALS_DB_PATH'])
        
        # O(N^2) Placement Solver
        mapping, section_occupants, unplaced, exact_complete, adjacency = _run_matrix_placement(sections, placements, engine)
        
        total_placements = len(placements)
        unplaced_count = len(unplaced)
        placed_count = total_placements - unplaced_count
        
        # Construct summary warnings
        warnings = []
        if unplaced_count > 0:
            warnings.append(
                f"We left {unplaced_count} chemicals unplaced in the available pool "
                f"because they are incompatible with all existing sections."
            )
            
        msg = f"Auto-arrangement proposed: {placed_count} of {total_placements} chemicals placed."
        if unplaced_count == 0:
            msg = "Fail-safe arrangement successful: all chemicals placed safely."

        # ── Step 5: Simulation recommendations ──
        recommendation = {
            'has_recommendation': False,
            'add_sections_needed': 0,
            'can_auto_create': False,
            'virtual_layout': None,
            'message': None
        }

        initial_unplaced_count = unplaced_count
        if initial_unplaced_count > 0:
            best_extra_sections = 0
            best_virtual_layout = None
            best_virtual_complete = False
            best_unplaced_count = initial_unplaced_count
            
            for extra_count in range(1, 11):
                sim_sections = list(sections)
                for i in range(1, extra_count + 1):
                    sim_sections.append({
                        'id': f'virtual_{i}',
                        'name': f'Virtual Section {i}'
                    })
                
                sim_exact = _run_exact_coloring(sim_sections, placements, adjacency)
                if sim_exact is not None:
                    sim_mapping, sim_occupants, sim_unplaced = sim_exact
                    sim_complete = True
                else:
                    sim_mapping, sim_occupants, sim_unplaced = _run_greedy_placement(sim_sections, placements, adjacency)
                    sim_complete = False
                
                if len(sim_unplaced) < best_unplaced_count:
                    best_unplaced_count = len(sim_unplaced)
                    best_extra_sections = extra_count
                    best_virtual_layout = sim_mapping
                    best_virtual_complete = sim_complete
                    
                if sim_complete and len(sim_unplaced) == 0:
                    break
            
            if best_extra_sections > 0:
                recommendation['has_recommendation'] = True
                recommendation['add_sections_needed'] = best_extra_sections
                recommendation['can_auto_create'] = True
                recommendation['virtual_layout'] = best_virtual_layout
                placed_percentage = int(((total_placements - best_unplaced_count) / total_placements) * 100)
                recommendation['message'] = (
                    f"We left {initial_unplaced_count} chemicals unplaced to prevent hazards. "
                    f"Adding {best_extra_sections} more sections may allow placement of "
                    f"{total_placements - best_unplaced_count} out of {total_placements} chemicals ({placed_percentage}%)."
                )

        # O(N^2) confidence score loop
        # Map proposed layout to count caution sections
        final_by_section = {}
        for placement in placements:
            p_id = placement['placement_id']
            target_sec_id = mapping.get(p_id)
            if target_sec_id is not None:
                final_by_section.setdefault(target_sec_id, []).append(placement)
        
        # Calculate overall warehouse safety index
        total_sections = len(sections)
        hazard_free_sections = total_sections
        caution_sections_count = 0
        incompatible_sections_count = 0
        requires_admin_override = False

        # Pre-load DB rules inside reactivity engine for caching performance (A-1/A-4)
        for section_id, occupants in final_by_section.items():
            if len(occupants) < 2:
                continue
            chem_ids = [o['chemical_id'] for o in occupants]
            analysis = engine.analyze(chem_ids, include_water_check=True, save_audit=False)
            if analysis.overall_compatibility == Compatibility.INCOMPATIBLE:
                incompatible_sections_count += 1
                hazard_free_sections -= 1
            elif analysis.overall_compatibility in (Compatibility.CAUTION, Compatibility.NO_DATA):
                caution_sections_count += 1
                hazard_free_sections -= 1
                requires_admin_override = True
                
        safety_score = 100
        if total_sections > 0:
            # Formula: (hazard_free_sections / total_sections) * 100
            safety_score = int((hazard_free_sections / total_sections) * 100)

        return jsonify({
            'success': True,
            'layout': mapping,
            'suggested_layout': mapping,
            'unplaced': [p['placement_id'] for p in unplaced],
            'safety_score': safety_score,
            'confidence_score': safety_score,
            'requires_admin_override': requires_admin_override,
            'caution_sections': caution_sections_count,
            'warnings': warnings,
            'recommendation': recommendation,
            'exact_complete': exact_complete,
            'message': msg
        })
    except Exception as e:
        logger.error(f"Auto-Arrange failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/layout/save', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def save_layout():
    """Save the proposed layout mappings."""
    try:
        data = request.get_json(silent=True) or {}
        layout = data.get('layout') # Dict mapping placement_id -> section_id
        
        try:
            parsed_layout = _parse_layout_mapping(layout)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
            
        conn = _get_db_connection()
        conn.execute('BEGIN EXCLUSIVE')
        cursor = conn.cursor()

        ok, status_code, payload = _validate_layout_update(conn, parsed_layout, _current_role())
        if not ok:
            conn.rollback()
            conn.close()
            return jsonify(payload), status_code
        
        # Update each placement
        for p_id, sec_id in parsed_layout.items():
            cursor.execute("UPDATE chemical_placements SET section_id = ? WHERE id = ?", (sec_id, p_id))
                
        # Log to audit trail
        cursor.execute(
            """
            INSERT INTO audit_trail (batch_id, row_index, action, input_data, output_data, confidence, method, user_id)
            VALUES (?, ?, ?, ?, ?, 1.0, 'warehouse_auto_arrange', ?)
            """,
            (
                'warehouse',
                None,
                'save_auto_arrange_layout',
                json.dumps({'layout_size': len(parsed_layout)}),
                json.dumps({'saved': True}),
                _current_user_id()
            )
        )
        
        conn.commit()
        conn.close()

        log_event(
            db_path=_get_db_path(),
            event_type='save_auto_arrange_layout',
            category='warehouse',
            severity='info',
            title='AI auto-arrange layout saved',
            detail=f"Applied auto-arranged positions for {len(parsed_layout)} chemical placements",
            user_id=_current_user_id(),
            entity_type='warehouse',
            entity_name='Warehouse Layout',
            meta={'layout_size': len(parsed_layout)},
        )
        
        return jsonify({'success': True, 'message': 'Layout saved successfully.'})
    except Exception as e:
        logger.error(f"Save layout failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/recommendation/apply', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def apply_recommended_layout():
    """Create recommended extra sections and persist a complete virtual layout atomically."""
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        warehouse_id = data.get('warehouse_id')
        extra_sections = data.get('extra_sections')
        virtual_layout = data.get('virtual_layout')

        try:
            warehouse_id = int(warehouse_id)
            extra_sections = int(extra_sections)
        except (TypeError, ValueError):
            return jsonify({'error': 'warehouse_id and extra_sections must be integers'}), 400
        if extra_sections < 1 or extra_sections > 100:
            return jsonify({'error': 'extra_sections must be between 1 and 100'}), 400
        if not isinstance(virtual_layout, dict) or not virtual_layout:
            return jsonify({'error': 'virtual_layout mapping is required'}), 400

        conn = _get_db_connection()
        conn.execute('BEGIN EXCLUSIVE')
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM warehouses WHERE id = ?", (warehouse_id,))
        if not cursor.fetchone():
            conn.rollback()
            conn.close()
            return jsonify({'error': 'warehouse_id not found'}), 404

        cursor.execute(
            "SELECT COALESCE(MAX(position_index), -1) FROM warehouse_sections WHERE warehouse_id = ?",
            (warehouse_id,)
        )
        next_position = cursor.fetchone()[0] + 1
        new_section_ids = []
        for i in range(extra_sections):
            cursor.execute(
                "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                (warehouse_id, f"Section {next_position + i + 1}", next_position + i, 'slate')
            )
            new_section_ids.append(cursor.lastrowid)

        parsed_layout = {}
        for p_id_raw, sec_id_raw in virtual_layout.items():
            try:
                p_id = int(p_id_raw)
            except (TypeError, ValueError):
                conn.rollback()
                conn.close()
                return jsonify({'error': f'invalid placement id: {p_id_raw}'}), 400

            if sec_id_raw in (None, ''):
                parsed_layout[p_id] = None
            elif isinstance(sec_id_raw, str) and sec_id_raw.startswith('virtual_'):
                try:
                    virtual_index = int(sec_id_raw.split('_', 1)[1]) - 1
                    parsed_layout[p_id] = new_section_ids[virtual_index]
                except (ValueError, IndexError):
                    conn.rollback()
                    conn.close()
                    return jsonify({'error': f'invalid virtual section id: {sec_id_raw}'}), 400
            else:
                try:
                    parsed_layout[p_id] = int(sec_id_raw)
                except (TypeError, ValueError):
                    conn.rollback()
                    conn.close()
                    return jsonify({'error': f'invalid section id: {sec_id_raw}'}), 400

        ok, status_code, payload = _validate_layout_update(conn, parsed_layout, _current_role())
        if not ok:
            conn.rollback()
            conn.close()
            return jsonify(payload), status_code

        for p_id, sec_id in parsed_layout.items():
            cursor.execute("UPDATE chemical_placements SET section_id = ? WHERE id = ?", (sec_id, p_id))

        cursor.execute(
            """
            INSERT INTO audit_trail (batch_id, row_index, action, input_data, output_data, confidence, method, user_id)
            VALUES (?, ?, ?, ?, ?, 1.0, 'warehouse_auto_arrange', ?)
            """,
            (
                'warehouse',
                None,
                'apply_recommended_layout',
                json.dumps({'warehouse_id': warehouse_id, 'extra_sections': extra_sections}),
                json.dumps({'saved': True, 'new_section_ids': new_section_ids}),
                _current_user_id()
            )
        )

        conn.commit()
        conn.close()

        log_event(
            db_path=_get_db_path(),
            event_type='apply_recommended_layout',
            category='warehouse',
            severity='info',
            title='Proximity recommendations applied',
            detail=f"Created {extra_sections} new sections and placed {len(parsed_layout)} chemical placements",
            user_id=_current_user_id(),
            entity_type='warehouse',
            entity_id=str(warehouse_id),
            entity_name='Warehouse Layout Recommendation',
            meta={'warehouse_id': warehouse_id, 'extra_sections': extra_sections, 'new_section_ids': new_section_ids},
        )

        return jsonify({
            'success': True,
            'message': f'Created {extra_sections} sections and applied the recommended layout.',
            'new_section_ids': new_section_ids,
        })
    except Exception as e:
        logger.error(f"Apply recommended layout failed: {e}", exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/list', methods=['GET'])
@login_required
def list_warehouses():
    """List all warehouses."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, created_at FROM warehouses ORDER BY name")
        rows = cursor.fetchall()
        conn.close()
        return jsonify({
            'success': True,
            'warehouses': [dict(r) for r in rows]
        })
    except Exception as e:
        logger.error(f"List warehouses failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/create', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def create_warehouse():
    """Create a new warehouse and seed 10 default sections."""
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'error': 'Warehouse name is required'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        # Insert new warehouse
        cursor.execute("INSERT INTO warehouses (name) VALUES (?)", (name,))
        warehouse_id = cursor.lastrowid
        
        # Create 10 default sections
        for i in range(1, 11):
            cursor.execute(
                "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                (warehouse_id, f"Section {i}", i - 1, 'slate')
            )
            
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'warehouse_id': warehouse_id,
            'name': name,
            'message': f"Warehouse '{name}' created with 10 default sections."
        })
    except Exception as e:
        logger.error(f"Create warehouse failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/rename', methods=['POST'])
@login_required
@viewer_readonly
@csrf_protect
def rename_warehouse():
    """Rename an existing warehouse."""
    try:
        data = request.get_json(silent=True) or {}
        warehouse_id = data.get('warehouse_id')
        name = (data.get('name') or '').strip()
        
        if not warehouse_id or not name:
            return jsonify({'error': 'warehouse_id and name are required'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE warehouses SET name = ? WHERE id = ?", (name, warehouse_id))
        if cursor.rowcount == 0:
            conn.rollback()
            conn.close()
            return jsonify({'error': 'Warehouse not found'}), 404
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f"Warehouse renamed to '{name}' successfully."
        })
    except Exception as e:
        logger.error(f"Rename warehouse failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/delete/<int:warehouse_id>', methods=['DELETE'])
@login_required
@viewer_readonly
@csrf_protect
def delete_warehouse(warehouse_id):
    """Delete a warehouse and all its sections and placements."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        # Check if it's the last warehouse
        cursor.execute("SELECT COUNT(*) FROM warehouses")
        count = cursor.fetchone()[0]
        if count <= 1:
            conn.close()
            return jsonify({'error': 'Cannot delete the only warehouse. At least one warehouse must exist.'}), 400
            
        # Delete warehouse - manually cascading sections and placements to be safe
        cursor.execute("DELETE FROM chemical_placements WHERE warehouse_id = ?", (warehouse_id,))
        cursor.execute("DELETE FROM warehouse_sections WHERE warehouse_id = ?", (warehouse_id,))
        cursor.execute("DELETE FROM warehouses WHERE id = ?", (warehouse_id,))
        
        # Determine the fallback warehouse ID to return
        cursor.execute("SELECT id FROM warehouses ORDER BY name LIMIT 1")
        next_warehouse = cursor.fetchone()
        next_id = next_warehouse['id'] if next_warehouse else None
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'next_warehouse_id': next_id,
            'message': 'Warehouse deleted successfully.'
        })
    except Exception as e:
        logger.error(f"Delete warehouse failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500
