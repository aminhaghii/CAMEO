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
            
        # Delete only placements and sections belonging to this warehouse
        cursor.execute("DELETE FROM chemical_placements WHERE warehouse_id = ?", (warehouse_id,))
        cursor.execute("DELETE FROM warehouse_sections WHERE warehouse_id = ?", (warehouse_id,))
        
        for i in range(1, count + 1):
            cursor.execute(
                "INSERT INTO warehouse_sections (warehouse_id, name, position_index, color) VALUES (?, ?, ?, ?)",
                (warehouse_id, f"Section {i}", i - 1, 'slate')
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
    try:
        data = request.get_json(silent=True) or {}
        placement_id = data.get('placement_id')
        section_id = data.get('section_id') # Can be None/null to move back to sidebar pool
        
        if placement_id is None:
            return jsonify({'error': 'placement_id is required'}), 400
            
        conn = _get_db_connection()
        cursor = conn.cursor()
        
        # Verify placement exists
        cursor.execute("SELECT * FROM chemical_placements WHERE id = ?", (placement_id,))
        placement = cursor.fetchone()
        if not placement:
            conn.close()
            return jsonify({'error': 'Placement not found'}), 404
            
        chem_id = placement['chemical_id']
        chem_name = placement['chemical_name']
        
        if section_id is not None:
            # Verify target section exists
            cursor.execute("SELECT name FROM warehouse_sections WHERE id = ?", (section_id,))
            sec = cursor.fetchone()
            if not sec:
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
                
                analysis = engine.analyze(chem_ids, include_water_check=False)
                overall = analysis.overall_compatibility
                
                # Stop Hard: Block INCOMPATIBLE completely
                if overall == Compatibility.INCOMPATIBLE:
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

@warehouse_bp.route('/api/warehouse/auto_arrange', methods=['POST'])
def auto_arrange():
    """
    Greedy Auto-Arrange Algorithm:
    Clustered reactive groups are distributed proportionally across Sections.
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
            
        # ── Greedy Grouping Algorithm ──
        # 1. Cluster placements by their first reactive group (or a representative group)
        clusters = {}
        no_data_placements = []
        
        for p in placements:
            grps = p['reactive_groups']
            if not grps:
                no_data_placements.append(p)
                continue
                
            # Use the first group as primary cluster key
            primary_grp = grps[0]
            if primary_grp not in clusters:
                clusters[primary_grp] = []
            clusters[primary_grp].append(p)
            
        # Sort clusters by size (largest first)
        sorted_clusters = sorted(clusters.items(), key=lambda x: len(x[1]), reverse=True)
        
        # 2. Assign clusters to sections proportionally, avoiding INCOMPATIBLE conflicts
        # Initialize empty section occupants tracker
        section_occupants = {s['id']: [] for s in sections}
        
        # Find Quarantine section: we nominate the last section or one with name "quarantine"
        quarantine_section = sections[-1]
        for s in sections:
            if 'quarantine' in s['name'].lower():
                quarantine_section = s
                break
                
        # Move NO_DATA chemicals to Quarantine
        suggested_mapping = {}
        for p in no_data_placements:
            suggested_mapping[p['placement_id']] = quarantine_section['id']
            section_occupants[quarantine_section['id']].append(p)
            
        # Instantiate ReactivityEngine
        chemicals_db = current_app.config['CHEMICALS_DB_PATH']
        engine = ReactivityEngine(chemicals_db)
        
        # Distribute clusters
        section_index = 0
        num_sections = len(sections)
        
        for grp, cluster_placements in sorted_clusters:
            # We want to place the whole cluster in a section if possible
            # We try sections starting from the current section_index
            for attempt in range(num_sections):
                target_sec = sections[(section_index + attempt) % num_sections]
                target_id = target_sec['id']
                
                # Check if adding this cluster to target section causes any INCOMPATIBLE reaction
                current_chem_ids = [o['chemical_id'] for o in section_occupants[target_id]]
                cluster_chem_ids = [p['chemical_id'] for p in cluster_placements]
                
                incompatible_found = False
                # Try placing one by one and check
                test_list = list(current_chem_ids)
                for c_id in cluster_chem_ids:
                    test_list.append(c_id)
                    if len(test_list) >= 2:
                        analysis = engine.analyze(test_list, include_water_check=False)
                        if analysis.overall_compatibility == Compatibility.INCOMPATIBLE:
                            incompatible_found = True
                            break
                            
                if not incompatible_found:
                    # Successfully found a safe section for this cluster!
                    for p in cluster_placements:
                        suggested_mapping[p['placement_id']] = target_id
                        section_occupants[target_id].append(p)
                    # Advance target section pointer
                    section_index = (section_index + attempt + 1) % num_sections
                    break
            else:
                # If no section is compatible for the whole cluster, distribute items individually
                for p in cluster_placements:
                    placed = False
                    for s in sections:
                        s_id = s['id']
                        current_chem_ids = [o['chemical_id'] for o in section_occupants[s_id]]
                        test_list = current_chem_ids + [p['chemical_id']]
                        
                        if len(test_list) < 2:
                            suggested_mapping[p['placement_id']] = s_id
                            section_occupants[s_id].append(p)
                            placed = True
                            break
                            
                        analysis = engine.analyze(test_list, include_water_check=False)
                        if analysis.overall_compatibility != Compatibility.INCOMPATIBLE:
                            suggested_mapping[p['placement_id']] = s_id
                            section_occupants[s_id].append(p)
                            placed = True
                            break
                            
                    if not placed:
                        # Fallback to Quarantine section if absolutely incompatible everywhere
                        suggested_mapping[p['placement_id']] = quarantine_section['id']
                        section_occupants[quarantine_section['id']].append(p)
                        
        # 3. Calculate Confidence Score (Percentage of compatible pairs)
        total_placements = len(placements)
        caution_count = 0
        total_pairs_checked = 0
        incompat_resolved_flag = True
        
        for s_id, occupants in section_occupants.items():
            if len(occupants) >= 2:
                ids = [o['chemical_id'] for o in occupants]
                analysis = engine.analyze(ids, include_water_check=False)
                if analysis.overall_compatibility == Compatibility.INCOMPATIBLE:
                    incompat_resolved_flag = False
                
                # Count pairs
                n_occ = len(occupants)
                total_pairs_checked += (n_occ * (n_occ - 1)) // 2
                
                # Find Caution pairs
                for i in range(n_occ):
                    for j in range(i + 1, n_occ):
                        pair_analysis = engine._analyze_pair(
                            occupants[i]['chemical_id'], occupants[j]['chemical_id'],
                            occupants[i]['chemical_name'], occupants[j]['chemical_name'],
                            occupants[i]['reactive_groups'], occupants[j]['reactive_groups']
                        )
                        if pair_analysis.compatibility in (Compatibility.CAUTION, Compatibility.NO_DATA):
                            caution_count += 1
                            
        # Compute confidence score
        if total_pairs_checked > 0:
            compatibility_score = int(((total_pairs_checked - caution_count) / total_pairs_checked) * 100)
        else:
            compatibility_score = 100
            
        return jsonify({
            'success': True,
            'suggested_layout': suggested_mapping,
            'confidence_score': compatibility_score,
            'message': f"Auto-Arrange computed successfully. Safety level: {compatibility_score}%."
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
