"""
inventory_analysis.py — Batch inventory compatibility analysis APIs (Phase 2).
Uses ReactivityEngine without modifying safety-critical logic.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from io import BytesIO

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility

logger = logging.getLogger(__name__)

inventory_analysis_bp = Blueprint('inventory_analysis', __name__)

# NOAA-Standard Compatibility Colors (3 categories only)
_COMPAT_COLORS = {
    'Compatible': '#10B981',
    'Caution': '#F59E0B',
    'Incompatible': '#EF4444',
}


def _noaa_status(pair_result):
    """Map engine Compatibility enum to NOAA standard label."""
    c = pair_result.compatibility
    if c == Compatibility.INCOMPATIBLE:
        return 'Incompatible'
    if c in (Compatibility.CAUTION, Compatibility.NO_DATA):
        return 'Caution'
    return 'Compatible'


def _build_analysis_summary(matrix_result):
    """
    Build summary counts, pair details, individual alerts,
    and unique hazard/gas lists from the analysis matrix.
    """
    counts = {
        'compatible_pairs': 0,
        'caution_pairs': 0,
        'incompatible_pairs': 0,
    }
    pair_details = []
    individual_alerts = []
    all_hazards = set()
    all_gases = set()

    n = len(matrix_result.chemicals)

    # ── Diagonal: individual self-hazards ──
    for i in range(n):
        diag = matrix_result.matrix[i][i]
        if diag and diag.hazards:
            for idx, h in enumerate(diag.hazards):
                individual_alerts.append({
                    'chemical_id': diag.chem_a_id,
                    'chemical_name': diag.chem_a_name,
                    'hazard': h,
                    'explanation': diag.notes[idx] if diag.notes and idx < len(diag.notes) else '; '.join(diag.notes or []),
                })

    # ── Upper triangle: pairwise ──
    for i in range(n):
        for j in range(i + 1, n):
            pair = matrix_result.matrix[i][j]
            if not pair:
                continue
            status = _noaa_status(pair)

            if status == 'Compatible':
                counts['compatible_pairs'] += 1
            elif status == 'Caution':
                counts['caution_pairs'] += 1
            elif status == 'Incompatible':
                counts['incompatible_pairs'] += 1

            # Collect unique hazards and gases across all pairs
            for h in (pair.hazards or []):
                all_hazards.add(h)
            for g in (pair.gas_products or []):
                all_gases.add(g)

            pair_details.append({
                'chemical_a_id': pair.chem_a_id,
                'chemical_a_name': pair.chem_a_name,
                'chemical_b_id': pair.chem_b_id,
                'chemical_b_name': pair.chem_b_name,
                'compatibility': pair.compatibility.value,
                'status': status,
                'hazards': pair.hazards or [],
                'gases': pair.gas_products or [],
                'explanation': '; '.join(pair.notes or []) or 'Based on reactive group compatibility matrix',
            })

    return (
        counts,
        pair_details,
        individual_alerts,
        sorted(all_hazards),
        sorted(all_gases),
    )


def _analyze_storage_proximity(inventory_rows, pair_details):
    """Detect dangerous chemicals stored in same location."""
    warnings = []
    chemical_locations = {}

    for row in inventory_rows:
        chem_id = row['chemical_id']
        location = (row.get('location') or '').strip()
        if not location:
            continue
        chemical_locations.setdefault(chem_id, set()).add(location)

    for pair in pair_details:
        if pair.get('status') != 'Incompatible':
            continue

        loc_a = chemical_locations.get(pair['chemical_a_id'], set())
        loc_b = chemical_locations.get(pair['chemical_b_id'], set())
        overlap = sorted(list(loc_a.intersection(loc_b)))

        if overlap:
            warnings.append({
                'chemical_a': pair['chemical_a_name'],
                'chemical_b': pair['chemical_b_name'],
                'status': pair.get('status'),
                'locations': overlap,
                'message': f"Both chemicals are stored together at: {', '.join(overlap)}",
            })

    return warnings


def _fetch_inventory_rows_for_analysis(user_db_path: str, batch_id: str):
    """Fetch matched rows and unresolved counts for a batch."""
    conn = sqlite3.connect(user_db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, row_index, chemical_id, match_status, cleaned_data
        FROM inventory_staging
        WHERE batch_id = ?
        ORDER BY row_index
        """,
        (batch_id,)
    )
    rows = cursor.fetchall()
    conn.close()

    parsed = []
    unresolved = 0
    for row in rows:
        status = row['match_status']
        cleaned = {}
        try:
            cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
        except (json.JSONDecodeError, TypeError):
            cleaned = {}

        if status in ('REVIEW_REQUIRED', 'UNIDENTIFIED'):
            unresolved += 1

        parsed.append({
            'staging_id': row['id'],
            'row_index': row['row_index'],
            'chemical_id': row['chemical_id'],
            'match_status': status,
            'name': cleaned.get('name', ''),
            'quantity': cleaned.get('quantity', ''),
            'unit': cleaned.get('unit', ''),
            'location': cleaned.get('location', ''),
            'notes': cleaned.get('notes', ''),
        })

    matched = [r for r in parsed if r['match_status'] == 'MATCHED' and r['chemical_id']]
    return matched, unresolved


def _persist_user_inventory_snapshot(user_db_path: str, batch_id: str, inventory_rows: list):
    """Persist finalized batch rows into user_inventories table for historical retrieval."""
    conn = sqlite3.connect(user_db_path)
    cursor = conn.cursor()

    # Keep latest snapshot for this batch by replacing previous rows
    cursor.execute("DELETE FROM user_inventories WHERE batch_id = ?", (batch_id,))

    for row in inventory_rows:
        cursor.execute(
            """
            INSERT INTO user_inventories
                (batch_id, chemical_id, quantity, unit, storage_location, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                int(row['chemical_id']),
                str(row.get('quantity') or ''),
                str(row.get('unit') or ''),
                str(row.get('location') or ''),
                str(row.get('notes') or ''),
            )
        )

    conn.commit()
    conn.close()


@inventory_analysis_bp.route('/api/inventory/analyze', methods=['POST'])
def analyze_inventory():
    """Analyze all confirmed chemicals in a batch with ReactivityEngine."""
    try:
        data = request.get_json(silent=True) or {}
        batch_id = (data.get('batch_id') or '').strip()
        if not batch_id:
            return jsonify({'error': 'batch_id is required'}), 400

        user_db = current_app.config['USER_DB_PATH']
        chemicals_db = current_app.config['CHEMICALS_DB_PATH']

        inventory_rows, unresolved_count = _fetch_inventory_rows_for_analysis(user_db, batch_id)

        if unresolved_count > 0:
            return jsonify({
                'error': 'All rows must be confirmed before analysis',
                'unresolved_count': unresolved_count,
            }), 400

        if not inventory_rows:
            return jsonify({'error': 'No chemicals to analyze'}), 400

        unique_chemical_ids = sorted({int(r['chemical_id']) for r in inventory_rows if r['chemical_id']})
        if len(unique_chemical_ids) < 2:
            return jsonify({'error': 'At least 2 confirmed chemicals are required'}), 400

        logger.info("[Batch %s] Starting inventory analysis for %s unique chemicals", batch_id[:8], len(unique_chemical_ids))

        # Persist finalized user-managed inventory snapshot
        _persist_user_inventory_snapshot(user_db, batch_id, inventory_rows)

        engine = ReactivityEngine(chemicals_db)
        analysis = engine.analyze(unique_chemical_ids, include_water_check=True)

        counts, pair_details, individual_alerts, unique_hazards, unique_gases = _build_analysis_summary(analysis)
        location_warnings = _analyze_storage_proximity(inventory_rows, pair_details)

        # ── Enrich chemicals with CAS numbers ──
        chem_conn = sqlite3.connect(chemicals_db)
        chem_conn.row_factory = sqlite3.Row
        for chem in analysis.chemicals:
            cas_rows = chem_conn.execute(
                'SELECT cas_id FROM chemical_cas WHERE chem_id = ? ORDER BY sort', (chem['id'],)
            ).fetchall()
            chem['cas_ids'] = [r['cas_id'] for r in cas_rows]
        chem_conn.close()

        # ── Build matrix for JSON (NOAA 3-color) ──
        matrix_rows = []
        n = len(analysis.chemicals)
        for i in range(n):
            row_cells = []
            for j in range(n):
                pair = analysis.matrix[i][j]
                if not pair:
                    row_cells.append({
                        'status': 'Compatible',
                        'compatibility': Compatibility.COMPATIBLE.value,
                        'hazards': [],
                        'gases': [],
                        'color': _COMPAT_COLORS['Compatible'],
                    })
                    continue

                status = _noaa_status(pair)
                row_cells.append({
                    'status': status,
                    'compatibility': pair.compatibility.value,
                    'hazards': pair.hazards or [],
                    'gases': pair.gas_products or [],
                    'color': _COMPAT_COLORS[status],
                })
            matrix_rows.append(row_cells)

        analysis_payload = {
            'batch_id': batch_id,
            'analyzed_at': datetime.now(timezone.utc).isoformat(),
            'total_chemicals': len(unique_chemical_ids),
            'summary': {
                'compatible_pairs': counts['compatible_pairs'],
                'caution_pairs': counts['caution_pairs'],
                'incompatible_pairs': counts['incompatible_pairs'],
                'storage_warnings': len(location_warnings),
            },
            'chemicals': analysis.chemicals,
            'matrix': matrix_rows,
            'pair_details': pair_details,
            'individual_alerts': individual_alerts,
            'unique_hazards': unique_hazards,
            'unique_gases': unique_gases,
            'location_warnings': location_warnings,
            'engine_warnings': analysis.warnings,
        }

        conn = sqlite3.connect(user_db)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO analysis_results
                (batch_id, analysis_date, total_chemicals, dangerous_pairs, storage_warnings, risk_matrix_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                datetime.now(timezone.utc).isoformat(),
                len(unique_chemical_ids),
                counts['incompatible_pairs'],
                len(location_warnings),
                json.dumps(analysis_payload, default=str),
            )
        )
        analysis_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info("[Batch %s] Analysis complete (analysis_id=%s)", batch_id[:8], analysis_id)
        return jsonify({
            'status': 'success',
            'analysis_id': analysis_id,
            'batch_id': batch_id,
            'summary': analysis_payload['summary'],
        })

    except Exception as e:
        logger.error("analyze_inventory failed: %s", e, exc_info=True)
        return jsonify({'error': 'Internal analysis error'}), 500


@inventory_analysis_bp.route('/inventory/analysis/<batch_id>')
def inventory_analysis_page(batch_id):
    """Render analysis results page."""
    return render_template('inventory_analysis.html', batch_id=batch_id)


@inventory_analysis_bp.route('/api/inventory/analysis/<batch_id>')
def get_inventory_analysis(batch_id):
    """Fetch latest saved analysis payload for a batch, enriched with EU data."""
    try:
        user_db = current_app.config['USER_DB_PATH']
        conn = sqlite3.connect(user_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, analysis_date, risk_matrix_json
            FROM analysis_results
            WHERE batch_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (batch_id,)
        )
        row = cursor.fetchone()

        # ── Fetch original filename from inventory_batches ──
        cursor.execute(
            "SELECT filename, created_at FROM inventory_batches WHERE id = ?",
            (batch_id,)
        )
        batch_row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Analysis not found'}), 404

        payload = json.loads(row['risk_matrix_json']) if row['risk_matrix_json'] else {}
        payload['analysis_id'] = row['id']
        payload['analysis_date'] = row['analysis_date']
        
        if batch_row:
            payload['original_filename'] = batch_row['filename']
            payload['upload_date'] = batch_row['created_at']
        else:
            payload['original_filename'] = 'Unknown_File'
            payload['upload_date'] = payload['analysis_date']

        # ── EU Enrichment ──
        try:
            from logic.excel_generator import query_eu_compliance
            chemicals_db = current_app.config['CHEMICALS_DB_PATH']
            chemicals = payload.get('chemicals', [])

            # Collect all CAS numbers
            cas_list = []
            for chem in chemicals:
                for cas in chem.get('cas_ids', []):
                    cas_list.append(cas)

            if cas_list:
                eu_data = query_eu_compliance(chemicals_db, cas_list)
                eu_map = {d['cas_number']: d for d in eu_data}

                # Inject EU data into each chemical
                for chem in chemicals:
                    cas_ids = chem.get('cas_ids', [])
                    chem['eu_data'] = None
                    if cas_ids:
                        for cas in cas_ids:
                            eu = eu_map.get(cas, {})
                            hcodes = str(eu.get('eu_hcodes', ''))
                            ec = str(eu.get('ec_number', ''))
                            svhc = str(eu.get('svhc_status', ''))
                            # If any of these fields have actual data, use this record
                            if ('Not explicitly listed' not in hcodes) or ec or ('Not explicitly listed' not in svhc):
                                chem['eu_hcodes'] = eu.get('eu_hcodes', '')
                                chem['svhc_status'] = eu.get('svhc_status', '')
                                chem['restrictions'] = eu.get('restrictions', '')
                                chem['ec_number'] = ec
                                chem['eu_data'] = eu
                                break
                        # Fallback to the first one if all are empty
                        if not chem['eu_data']:
                            eu = eu_map.get(cas_ids[0], {})
                            chem['eu_hcodes'] = eu.get('eu_hcodes', '')
                            chem['svhc_status'] = eu.get('svhc_status', '')
                            chem['restrictions'] = eu.get('restrictions', '')
                            chem['ec_number'] = eu.get('ec_number', '')
                            chem['eu_data'] = eu

                payload['chemicals'] = chemicals
        except Exception as eu_err:
            logger.warning("EU enrichment failed (non-fatal): %s", eu_err)

        return jsonify(payload)

    except Exception as e:
        logger.error("get_inventory_analysis failed: %s", e, exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@inventory_analysis_bp.route('/api/inventory/analysis/<batch_id>/export/excel')
def export_inventory_analysis_excel(batch_id):
    """Export analysis payload to XLSX."""
    try:
        try:
            import pandas as pd
        except Exception:
            return jsonify({'error': 'Excel export requires pandas/openpyxl'}), 501

        user_db = current_app.config['USER_DB_PATH']
        conn = sqlite3.connect(user_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT risk_matrix_json FROM analysis_results WHERE batch_id = ? ORDER BY id DESC LIMIT 1",
            (batch_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Analysis not found'}), 404

        payload = json.loads(row['risk_matrix_json']) if row['risk_matrix_json'] else {}

        summary_df = pd.DataFrame([payload.get('summary', {})])
        pairs_df = pd.DataFrame(payload.get('pair_details', []))
        warnings_df = pd.DataFrame(payload.get('location_warnings', []))

        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name='Summary', index=False)
            pairs_df.to_excel(writer, sheet_name='Pairs', index=False)
            warnings_df.to_excel(writer, sheet_name='StorageWarnings', index=False)

        output.seek(0)
        filename = f"inventory_analysis_{batch_id[:8]}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    except Exception as e:
        logger.error("export_inventory_analysis_excel failed: %s", e, exc_info=True)
        return jsonify({'error': 'Excel export failed'}), 500


@inventory_analysis_bp.route('/api/inventory/analysis/<batch_id>/export/pdf')
def export_inventory_analysis_pdf(batch_id):
    """Export analysis summary to PDF (requires reportlab)."""
    try:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
        except Exception:
            return jsonify({'error': 'PDF export requires reportlab. Install with: pip install reportlab'}), 501

        user_db = current_app.config['USER_DB_PATH']
        conn = sqlite3.connect(user_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT risk_matrix_json FROM analysis_results WHERE batch_id = ? ORDER BY id DESC LIMIT 1",
            (batch_id,)
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Analysis not found'}), 404

        payload = json.loads(row['risk_matrix_json']) if row['risk_matrix_json'] else {}
        summary = payload.get('summary', {})

        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=A4)
        width, height = A4

        y = height - 50
        c.setFont('Helvetica-Bold', 16)
        c.drawString(50, y, 'SAFEWARE Inventory Analysis Report')
        y -= 24
        c.setFont('Helvetica', 10)
        c.drawString(50, y, f"Batch: {batch_id}")
        y -= 16
        c.drawString(50, y, f"Generated: {datetime.now(timezone.utc).isoformat()} UTC")
        y -= 24

        c.setFont('Helvetica-Bold', 12)
        c.drawString(50, y, 'Summary')
        y -= 18
        c.setFont('Helvetica', 10)
        for key in ('safe_pairs', 'caution_pairs', 'dangerous_pairs', 'explosive_pairs', 'storage_warnings'):
            c.drawString(60, y, f"{key.replace('_', ' ').title()}: {summary.get(key, 0)}")
            y -= 14

        y -= 8
        c.setFont('Helvetica-Bold', 12)
        c.drawString(50, y, 'Top Storage Warnings')
        y -= 18
        c.setFont('Helvetica', 9)
        warnings = payload.get('location_warnings', [])[:15]
        if not warnings:
            c.drawString(60, y, 'No storage proximity warnings found.')
            y -= 14
        else:
            for w in warnings:
                line = f"- {w.get('chemical_a')} + {w.get('chemical_b')} ({w.get('risk_level')}) @ {', '.join(w.get('locations', []))}"
                if y < 60:
                    c.showPage()
                    y = height - 50
                    c.setFont('Helvetica', 9)
                c.drawString(60, y, line[:110])
                y -= 13

        c.showPage()
        c.save()
        pdf_buffer.seek(0)

        filename = f"inventory_analysis_{batch_id[:8]}.pdf"
        return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')

    except Exception as e:
        logger.error("export_inventory_analysis_pdf failed: %s", e, exc_info=True)
        return jsonify({'error': 'PDF export failed'}), 500
