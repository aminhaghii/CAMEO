import os
import json
import sqlite3
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, current_app, g

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility, COMPATIBILITY_MAP

logger = logging.getLogger(__name__)

warehouse_bp = Blueprint('warehouse', __name__)

ADMIN_OVERRIDE_ROLES = {'company_admin', 'super_admin'}
SECTION_BLOCKING_COMPATIBILITIES = {
    Compatibility.INCOMPATIBLE,
    Compatibility.CAUTION,
    Compatibility.NO_DATA,
}

def _get_db_path():
    return getattr(g, 'tenant_db_path', None) or current_app.config['USER_DB_PATH']

def _get_db_connection():
    db_path = _get_db_path()
    from etl.pipeline import init_inventory_tables
    init_inventory_tables(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

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
        f"SELECT * FROM chemical_placements WHERE warehouse_id IN ({warehouse_placeholders}) AND status = 'placed'",
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
        chem_ids = [row['chemical_id'] for row in occupants]
        analysis = engine.analyze(chem_ids, include_water_check=True, save_audit=False)
        if analysis.overall_compatibility == Compatibility.INCOMPATIBLE:
            critical_desc = "; ".join(
                f"{pair['chemicals'][0]} & {pair['chemicals'][1]}"
                for pair in analysis.critical_pairs
            )
            return False, 409, {
                'error': f"Safety Block: Incompatible chemicals in section ({critical_desc})",
                'code': 'INCOMPATIBLE'
            }
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
    conn = sqlite3.connect(chemicals_db)
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
def get_warehouse_data():
    """Get warehouse sections, placements, inventory, and compatibility rules."""
    try:
        warehouse_id = request.args.get('warehouse_id')
        
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        if not warehouse_id:
            cursor.execute("SELECT id FROM warehouses ORDER BY name LIMIT 1")
            row = cursor.fetchone()
            if row:
                warehouse_id = row['id']
            else:
                cursor.execute("INSERT INTO warehouses (name) VALUES ('Main Warehouse')")
                conn.commit()
                warehouse_id = cursor.lastrowid
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
        
        # 1. Fetch sections
        cursor.execute("SELECT * FROM warehouse_sections WHERE warehouse_id = ? ORDER BY position_index", (warehouse_id,))
        sections_rows = cursor.fetchall()
        
        sections = []
        for s in sections_rows:
            sections.append({
                'id': s['id'],
                'name': s['name'],
                'position_index': s['position_index'],
                'color': s['color'] or 'slate',
                'chemicals': []
            })
            
        # 2. Fetch placements (both placed and unplaced)
        cursor.execute("SELECT * FROM chemical_placements WHERE warehouse_id = ?", (warehouse_id,))
        placements_rows = cursor.fetchall()
        
        inventory_pool = []
        all_unique_groups = set()
        chem_groups_map = {}
        
        for p in placements_rows:
            groups = []
            try:
                groups = json.loads(p['reactive_groups']) if p['reactive_groups'] else []
            except Exception:
                pass
            
            for g_id in groups:
                all_unique_groups.add(g_id)
                
            chem_groups_map[str(p['chemical_id'])] = groups
            
            placement_obj = {
                'id': p['id'],
                'section_id': p['section_id'],
                'chemical_id': p['chemical_id'],
                'chemical_name': p['chemical_name'],
                'cas_number': p['cas_number'] or '',
                'quantity_kg': p['quantity_kg'] or 1.0,
                'reactive_groups': groups,
                'status': p['status'] or 'placed',
                'placed_by': p['placed_by'] or 'human',
                'placed_at': p['placed_at']
            }
            
            if p['section_id'] is None:
                inventory_pool.append(placement_obj)
            else:
                # Add to corresponding section
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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/sections/init', methods=['POST'])
def init_sections():
    """Initialize warehouse sections."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/sections/update', methods=['POST'])
def update_section():
    """Rename a warehouse section."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/placements/move', methods=['POST'])
def move_placement():
    """Move a chemical placement to another section or to the sidebar pool (section_id=None)."""
    conn = None
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
def remove_placement(placement_id):
    """Delete a placement completely from the warehouse database."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        conn.close()
        return jsonify({'success': True, 'message': 'Placement removed completely'})
    except Exception as e:
        logger.error(f"Remove placement failed: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

@warehouse_bp.route('/api/warehouse/add_from_batch', methods=['POST'])
def add_from_batch():
    """Import finalized batch rows to the warehouse available pool."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

        data = request.get_json(silent=True) or {}
        batch_id = data.get('batch_id')
        warehouse_id = data.get('warehouse_id')
        warehouse_name = (data.get('warehouse_name') or 'Default Warehouse').strip()
        
        if not batch_id:
            return jsonify({'error': 'batch_id is required'}), 400
            
        conn = _get_db_connection()
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
            "SELECT chemical_id, cleaned_data FROM inventory_staging WHERE batch_id = ? AND match_status = 'MATCHED'",
            (batch_id,)
        )
        rows = cursor.fetchall()
        
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
            try:
                qty = float(qty_str)
                if unit_str in ('g', 'grams', 'gr'):
                    qty /= 1000.0
                elif unit_str in ('lb', 'lbs', 'pounds'):
                    qty *= 0.453592
            except Exception:
                qty = 1.0
                
            groups = _get_chem_groups(chem_id)
            groups_json = json.dumps(groups)
            
            # Check if this chemical (with same batch and name) already exists in warehouse to prevent duplicates
            cursor.execute(
                "SELECT id FROM chemical_placements WHERE chemical_id = ? AND placed_by = ? AND warehouse_id = ?",
                (chem_id, f"import:{batch_id}", warehouse_id)
            )
            if cursor.fetchone():
                continue # Skip duplicates from same batch import
                
            cursor.execute(
                """
                INSERT INTO chemical_placements 
                    (warehouse_id, section_id, chemical_id, chemical_name, cas_number, quantity_kg, reactive_groups, status, placed_by)
                VALUES (?, NULL, ?, ?, ?, ?, ?, 'placed', ?)
                """,
                (warehouse_id, chem_id, chem_name, cas_number, qty, groups_json, f"import:{batch_id}")
            )
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
        conn.close()
        
        return jsonify({
            'success': True, 
            'message': f'Successfully imported {imported_count} chemicals to the warehouse pool.'
        })
    except Exception as e:
        logger.error(f"Import batch to warehouse failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

def _groups_for_placement(placement):
    groups = placement.get('reactive_groups') or []
    try:
        return [int(g) for g in groups]
    except (TypeError, ValueError):
        return []

def _is_section_conflict(engine, placement_a, placement_b):
    pair_res = engine._analyze_pair(
        placement_a['chemical_id'],
        placement_b['chemical_id'],
        placement_a['chemical_name'],
        placement_b['chemical_name'],
        _groups_for_placement(placement_a),
        _groups_for_placement(placement_b),
    )
    return pair_res.compatibility in SECTION_BLOCKING_COMPATIBILITIES

def _build_conflict_graph(placements, engine):
    """Build a graph where edges mean two placements must not share a section."""
    adjacency = {p['placement_id']: set() for p in placements}
    by_id = {p['placement_id']: p for p in placements}
    for i, p_a in enumerate(placements):
        for p_b in placements[i + 1:]:
            if _is_section_conflict(engine, p_a, p_b):
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

    A conflict edge means two chemicals must not share a section under the
    warehouse fail-safe policy: INCOMPATIBLE, CAUTION, and NO_DATA are separated.
    """
    adjacency, _ = _build_conflict_graph(placements, engine)
    exact_result = _run_exact_coloring(sections, placements, adjacency)
    if exact_result is not None:
        return (*exact_result, True, adjacency)

    mapping, section_occupants, unplaced = _run_greedy_placement(sections, placements, adjacency)
    return mapping, section_occupants, unplaced, False, adjacency


@warehouse_bp.route('/api/warehouse/auto_arrange', methods=['POST'])
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
        
        # Get sections
        cursor.execute("SELECT id, name FROM warehouse_sections WHERE warehouse_id = ? ORDER BY position_index", (warehouse_id,))
        sections = [dict(r) for r in cursor.fetchall()]
        
        if not sections:
            conn.close()
            return jsonify({'error': 'No warehouse sections defined. Please initialize sections first.'}), 400
            
        # Get all placements (placed and unplaced)
        cursor.execute("SELECT id, chemical_id, chemical_name, reactive_groups FROM chemical_placements WHERE warehouse_id = ?", (warehouse_id,))
        placements = []
        for r in cursor.fetchall():
            groups = []
            try:
                groups = json.loads(r['reactive_groups']) if r['reactive_groups'] else []
            except Exception:
                pass
            placements.append({
                'placement_id': r['id'],
                'chemical_id': r['chemical_id'],
                'chemical_name': r['chemical_name'],
                'reactive_groups': groups
            })
            
        conn.close()
        
        if not placements:
            return jsonify({'error': 'No chemicals in the warehouse pool to arrange.'}), 400
            
        # Instantiate ReactivityEngine
        chemicals_db = current_app.config['CHEMICALS_DB_PATH']
        engine = ReactivityEngine(chemicals_db)
        
        # 1. Run fail-safe graph placement for actual sections
        suggested_mapping, section_occupants, unplaced_placements, exact_complete, adjacency = _run_matrix_placement(
            sections, placements, engine
        )
        
        # 2. Dynamic Section Suggester (Simulation Loop)
        recommendation = {
            'has_recommendation': False,
            'add_sections_needed': 0,
            'message': '',
            'can_auto_create': False,
            'virtual_layout': None,
        }
        
        total_placements = len(placements)
        if unplaced_placements:
            initial_unplaced_count = len(unplaced_placements)
            best_unplaced_count = initial_unplaced_count
            best_extra_sections = 0
            best_virtual_layout = None
            best_virtual_complete = False
            
            max_extra_sections = min(max(total_placements - len(sections), 0), 25)
            # Try enough virtual sections to avoid false "add N" promises.
            for extra_count in range(1, max_extra_sections + 1):
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
                
                sim_unplaced_count = len(sim_unplaced)
                if sim_unplaced_count < best_unplaced_count:
                    best_unplaced_count = sim_unplaced_count
                    best_extra_sections = extra_count
                    best_virtual_layout = sim_mapping
                    best_virtual_complete = sim_complete
                    
                if sim_complete and sim_unplaced_count == 0:
                    break
            
            if best_extra_sections > 0:
                recommendation['has_recommendation'] = True
                recommendation['add_sections_needed'] = best_extra_sections
                recommendation['can_auto_create'] = best_virtual_complete and best_unplaced_count == 0
                recommendation['virtual_layout'] = best_virtual_layout if recommendation['can_auto_create'] else None
                if recommendation['can_auto_create']:
                    recommendation['message'] = (
                        f"We left {initial_unplaced_count} chemicals unplaced to prevent hazards. "
                        f"Adding {best_extra_sections} more sections will allow a complete matrix-compatible layout."
                    )
                else:
                    placed_percentage = int(((total_placements - best_unplaced_count) / total_placements) * 100)
                    recommendation['message'] = (
                        f"We left {initial_unplaced_count} chemicals unplaced to prevent hazards. "
                        f"Adding {best_extra_sections} more sections may allow placement of "
                        f"{total_placements - best_unplaced_count} out of {total_placements} chemicals ({placed_percentage}%)."
                    )
            else:
                recommendation['message'] = (
                    f"We left {initial_unplaced_count} chemicals unplaced. "
                    f"No complete matrix-compatible layout was found with up to {max_extra_sections} extra sections."
                )
                    
        # 3. Calculate Confidence Score (Percentage of compatible pairs in actual placement)
        caution_count = 0
        total_pairs_checked = 0
        
        for s_id, occupants in section_occupants.items():
            if len(occupants) >= 2:
                ids = [o['chemical_id'] for o in occupants]
                analysis = engine.analyze(ids, include_water_check=True, save_audit=False)
                
                n_occ = len(occupants)
                total_pairs_checked += (n_occ * (n_occ - 1)) // 2
                
                # Find Caution / NO_DATA pairs
                for i in range(n_occ):
                    for j in range(i + 1, n_occ):
                        p_a = occupants[i]
                        p_b = occupants[j]
                        pair_analysis = engine._analyze_pair(
                            p_a['chemical_id'], p_b['chemical_id'],
                            p_a['chemical_name'], p_b['chemical_name'],
                            p_a['reactive_groups'], p_b['reactive_groups']
                        )
                        if pair_analysis.compatibility in (Compatibility.CAUTION, Compatibility.NO_DATA):
                            caution_count += 1
                            
        # Compute confidence score
        if total_pairs_checked > 0:
            compatibility_score = int(((total_pairs_checked - caution_count) / total_pairs_checked) * 100)
        else:
            compatibility_score = 100
            
        # Build human readable message
        if unplaced_placements:
            msg = (
                f"Auto-Arrange computed. Safety level: {compatibility_score}%. "
                f"We left {len(unplaced_placements)} chemicals unplaced because they conflict with all available sections."
            )
        else:
            msg = f"Auto-Arrange computed successfully. Safety level: {compatibility_score}%."
            
        return jsonify({
            'success': True,
            'suggested_layout': suggested_mapping,
            'confidence_score': compatibility_score,
            'recommendation': recommendation,
            'exact_complete': exact_complete,
            'message': msg
        })
    except Exception as e:
        logger.error(f"Auto-Arrange failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/layout/save', methods=['POST'])
def save_layout():
    """Save the proposed layout mappings."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'success': True, 'message': 'Layout saved successfully.'})
    except Exception as e:
        logger.error(f"Save layout failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/recommendation/apply', methods=['POST'])
def apply_recommended_layout():
    """Create recommended extra sections and persist a complete virtual layout atomically."""
    conn = None
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/list', methods=['GET'])
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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/create', methods=['POST'])
def create_warehouse():
    """Create a new warehouse and seed 10 default sections."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/rename', methods=['POST'])
def rename_warehouse():
    """Rename an existing warehouse."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/delete/<int:warehouse_id>', methods=['DELETE'])
def delete_warehouse(warehouse_id):
    """Delete a warehouse and all its sections and placements."""
    try:
        access_error = _write_access_error()
        if access_error:
            return access_error

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
        return jsonify({'error': str(e)}), 500
