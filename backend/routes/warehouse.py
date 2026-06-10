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

def _get_db_path():
    return getattr(g, 'tenant_db_path', None) or current_app.config['USER_DB_PATH']

def _get_db_connection():
    db_path = _get_db_path()
    from etl.pipeline import init_inventory_tables
    init_inventory_tables(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

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
    chemicals_db = current_app.config['CHEMICALS_DB_PATH']
    conn = sqlite3.connect(chemicals_db)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Generate placeholders
    placeholders = ",".join(["?"] * len(group_ids))
    cursor.execute(f"""
        SELECT react1, react2, pair_compatibility
        FROM reactivity
        WHERE react1 IN ({placeholders}) AND react2 IN ({placeholders})
    """, group_ids + group_ids)
    
    rules = {}
    for row in cursor.fetchall():
        g1, g2 = row['react1'], row['react2']
        compat = row['pair_compatibility']
        rules[f"{g1}|{g2}"] = compat
        rules[f"{g2}|{g1}"] = compat
    conn.close()
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
        data = request.get_json(silent=True) or {}
        placement_id = data.get('placement_id')
        section_id = data.get('section_id') # Can be None/null to move back to sidebar pool
        
        if placement_id is None:
            return jsonify({'error': 'placement_id is required'}), 400
            
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
            # Verify target section exists
            cursor.execute("SELECT name FROM warehouse_sections WHERE id = ?", (section_id,))
            sec = cursor.fetchone()
            if not sec:
                conn.rollback()
                conn.close()
                return jsonify({'error': 'Target section not found'}), 404
                
            # Perform safety checks
            cursor.execute(
                "SELECT chemical_id, chemical_name FROM chemical_placements WHERE section_id = ? AND status = 'placed' AND id != ?",
                (section_id, placement_id)
            )
            existing = cursor.fetchall()
            
            if existing:
                chemicals_db = current_app.config['CHEMICALS_DB_PATH']
                engine = ReactivityEngine(chemicals_db)
                chem_ids = [r['chemical_id'] for r in existing] + [chem_id]
                
                analysis = engine.analyze(chem_ids, include_water_check=True)
                overall = analysis.overall_compatibility
                
                # Stop Hard: Block INCOMPATIBLE completely
                if overall == Compatibility.INCOMPATIBLE:
                    conn.rollback()
                    conn.close()
                    critical_desc = "; ".join([f"{p['chemicals'][0]} & {p['chemicals'][1]}" for p in analysis.critical_pairs])
                    return jsonify({
                        'error': f"Safety Block: Incompatible chemicals in section ({critical_desc})",
                        'code': 'INCOMPATIBLE'
                    }), 409
                    
                # Admin Override for CAUTION / NO_DATA
                if overall in (Compatibility.CAUTION, Compatibility.NO_DATA):
                    role = g.user.get('role') if (hasattr(g, 'user') and g.user) else 'admin'
                    if role not in ('admin', 'super_admin'):
                        conn.rollback()
                        conn.close()
                        return jsonify({
                            'error': "Access Denied: Placement requires CAUTION warning override (Admin privileges required)",
                            'code': 'CAUTION_REQUIRES_ADMIN'
                        }), 403
                        
            # Move placement to the section
            cursor.execute("UPDATE chemical_placements SET section_id = ? WHERE id = ?", (section_id, placement_id))
            action = 'place_chemical'
            out_data = json.dumps({'section_id': section_id, 'section_name': sec['name']})
        else:
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
                g.user.get('username') if (hasattr(g, 'user') and g.user) else 'human'
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
                g.user.get('username') if (hasattr(g, 'user') and g.user) else 'human'
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
                g.user.get('username') if (hasattr(g, 'user') and g.user) else 'human'
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

def _run_greedy_placement(sections, placements, engine, chem_groups_map):
    """
    Distributes placements into sections greedily by sorting them by reactive groups count descending.
    Avoids putting INCOMPATIBLE chemicals in the same section.
    If a chemical cannot fit in any section safely, its suggested section ID is None.
    """
    # Sort placements by complexity (number of reactive groups) descending
    sorted_placements = sorted(placements, key=lambda p: len(p['reactive_groups']), reverse=True)
    
    # Initialize occupants tracker
    section_occupants = {s['id']: [] for s in sections}
    suggested_mapping = {}
    unplaced_placements = []
    
    section_index = 0
    num_sections = len(sections)
    
    for p in sorted_placements:
        placed = False
        p_id = p['chemical_id']
        p_name = p['chemical_name']
        p_groups = chem_groups_map.get(str(p_id), [])
        
        # Try to place in one of the sections, starting from the current section_index
        for attempt in range(num_sections):
            target_sec = sections[(section_index + attempt) % num_sections]
            target_id = target_sec['id']
            
            # Check compatibility of p with all current occupants of target_id
            incompatible = False
            for o in section_occupants[target_id]:
                o_id = o['chemical_id']
                o_name = o['chemical_name']
                o_groups = chem_groups_map.get(str(o_id), [])
                
                # Perform the fast pair-wise check
                pair_res = engine._analyze_pair(p_id, o_id, p_name, o_name, p_groups, o_groups)
                if pair_res.compatibility == Compatibility.INCOMPATIBLE:
                    incompatible = True
                    break
            
            if not incompatible:
                # Place chemical in this section
                suggested_mapping[p['placement_id']] = target_id
                section_occupants[target_id].append(p)
                placed = True
                # Update section index pointer to the next section
                section_index = (section_index + attempt + 1) % num_sections
                break
        
        if not placed:
            # Cannot fit anywhere safely - set to None
            suggested_mapping[p['placement_id']] = None
            unplaced_placements.append(p)
            
    return suggested_mapping, section_occupants, unplaced_placements


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
        
        # Build chem_groups_map mapping chemical_id -> list of reactive group IDs
        chem_groups_map = {str(p['chemical_id']): p['reactive_groups'] for p in placements}
        
        # 1. Run greedy placement for actual sections
        suggested_mapping, section_occupants, unplaced_placements = _run_greedy_placement(
            sections, placements, engine, chem_groups_map
        )
        
        # 2. Dynamic Section Suggester (Simulation Loop)
        recommendation = {
            'has_recommendation': False,
            'add_sections_needed': 0,
            'message': ''
        }
        
        total_placements = len(placements)
        if unplaced_placements:
            initial_unplaced_count = len(unplaced_placements)
            best_unplaced_count = initial_unplaced_count
            best_extra_sections = 0
            
            # Try adding +1, +2, +3 virtual sections
            for extra_count in range(1, 4):
                sim_sections = list(sections)
                for i in range(1, extra_count + 1):
                    sim_sections.append({
                        'id': f'virtual_{i}',
                        'name': f'Virtual Section {i}'
                    })
                
                sim_mapping, sim_occupants, sim_unplaced = _run_greedy_placement(
                    sim_sections, placements, engine, chem_groups_map
                )
                
                sim_unplaced_count = len(sim_unplaced)
                if sim_unplaced_count < best_unplaced_count:
                    best_unplaced_count = sim_unplaced_count
                    best_extra_sections = extra_count
                    
                if sim_unplaced_count == 0:
                    break
            
            if best_extra_sections > 0:
                recommendation['has_recommendation'] = True
                recommendation['add_sections_needed'] = best_extra_sections
                if best_unplaced_count == 0:
                    recommendation['message'] = (
                        f"We left {initial_unplaced_count} chemicals unplaced to prevent hazards. "
                        f"Adding {best_extra_sections} more sections will allow 100% safe placement for all inventory."
                    )
                else:
                    placed_percentage = int(((total_placements - best_unplaced_count) / total_placements) * 100)
                    recommendation['message'] = (
                        f"We left {initial_unplaced_count} chemicals unplaced to prevent hazards. "
                        f"Adding {best_extra_sections} more sections will allow safe placement of "
                        f"{total_placements - best_unplaced_count} out of {total_placements} chemicals ({placed_percentage}%)."
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
                f"We left {len(unplaced_placements)} incompatible chemicals unplaced to prevent safety hazards."
            )
        else:
            msg = f"Auto-Arrange computed successfully. Safety level: {compatibility_score}%."
            
        return jsonify({
            'success': True,
            'suggested_layout': suggested_mapping,
            'confidence_score': compatibility_score,
            'recommendation': recommendation,
            'message': msg
        })
    except Exception as e:
        logger.error(f"Auto-Arrange failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@warehouse_bp.route('/api/warehouse/layout/save', methods=['POST'])
def save_layout():
    """Save the proposed layout mappings."""
    try:
        data = request.get_json(silent=True) or {}
        layout = data.get('layout') # Dict mapping placement_id -> section_id
        
        if not layout:
            return jsonify({'error': 'layout mapping is required'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        # Update each placement
        for p_id_str, sec_id in layout.items():
            try:
                p_id = int(p_id_str)
                s_id = int(sec_id) if sec_id is not None else None
                cursor.execute("UPDATE chemical_placements SET section_id = ? WHERE id = ?", (s_id, p_id))
            except (ValueError, TypeError):
                continue
                
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
                json.dumps({'layout_size': len(layout)}),
                json.dumps({'saved': True}),
                g.user.get('username') if (hasattr(g, 'user') and g.user) else 'human'
            )
        )
        
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Layout saved successfully.'})
    except Exception as e:
        logger.error(f"Save layout failed: {e}", exc_info=True)
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
        data = request.get_json(silent=True) or {}
        warehouse_id = data.get('warehouse_id')
        name = (data.get('name') or '').strip()
        
        if not warehouse_id or not name:
            return jsonify({'error': 'warehouse_id and name are required'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE warehouses SET name = ? WHERE id = ?", (name, warehouse_id))
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
