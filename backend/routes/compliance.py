"""
EU Compliance — Flask Blueprint Route (v3 — 3-Sheet Unified)
==============================================================
Endpoints:
    POST /api/compliance/export
        Body: {"batch_id": "xxx"}   → 3-sheet unified report
        Body: {"cas_numbers": [...]} → single-sheet report
        Returns: Excel file download

    GET /compliance
        Renders the compliance report page (UI).
"""

import os
import json
import sqlite3
import logging
from datetime import datetime

from flask import (
    Blueprint, request, jsonify, send_file,
    current_app, render_template,
)

logger = logging.getLogger("compliance")

compliance_bp = Blueprint("compliance", __name__)


@compliance_bp.route("/compliance")
def compliance_page():
    """Render the compliance report page."""
    return render_template("compliance.html")


@compliance_bp.route("/api/compliance/export", methods=["POST"])
def export_compliance_report():
    """
    Generate and download an EU REACH/CLP compliance Excel report.

    Mode 1 — CAS-only (single-sheet):
        {"cas_numbers": ["67-64-1", ...]}

    Mode 2 — Batch unified (3-sheet):
        {"batch_id": "xxxxxxxx-xxxx-..."}
        Fetches full analysis + inventory, produces 3-sheet workbook.

    Returns: Excel file download
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    batch_id = data.get("batch_id", "").strip()
    cas_numbers = data.get("cas_numbers", [])
    custom_title = data.get("title", None)

    db_path = current_app.config.get(
        "CHEMICALS_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "chemicals.db"),
    )

    try:
        from logic.excel_generator import ComplianceExcelGenerator, query_eu_compliance

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
        os.makedirs(output_dir, exist_ok=True)

        generator = ComplianceExcelGenerator(db_path)

        # ── Mode 2: Batch unified report (3 sheets) ──
        if batch_id:
            logger.info("3-sheet unified export for batch %s", batch_id[:8])

            user_db = current_app.config["USER_DB_PATH"]
            conn = sqlite3.connect(user_db)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Fetch analysis payload
            cur.execute(
                "SELECT risk_matrix_json FROM analysis_results WHERE batch_id = ? ORDER BY id DESC LIMIT 1",
                (batch_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.close()
                return jsonify({"error": "Analysis not found for this batch"}), 404

            payload = json.loads(row["risk_matrix_json"]) if row["risk_matrix_json"] else {}

            # Extract chemicals & CAS numbers
            chemicals = payload.get("chemicals", [])
            cas_list = []
            for chem in chemicals:
                for cas in chem.get("cas_ids", []):
                    cas_list.append(cas)

            # Get EU compliance data
            eu_data = query_eu_compliance(db_path, cas_list) if cas_list else []

            # Fetch inventory rows for quantities AND original filename
            cur.execute(
                "SELECT filename, created_at FROM inventory_batches WHERE id = ?",
                (batch_id,)
            )
            batch_row = cur.fetchone()
            original_filename = batch_row["filename"] if batch_row else "Unknown_File"
            
            # Clean original filename (remove extension and spaces)
            import re
            base_name = os.path.splitext(original_filename)[0]
            clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', base_name).strip('_')
            
            cur.execute(
                "SELECT cleaned_data FROM inventory_staging WHERE batch_id = ? AND match_status = 'MATCHED'",
                (batch_id,)
            )
            inv_rows = cur.fetchall()
            conn.close()

            qty_map = {}
            for ir in inv_rows:
                try:
                    cd = json.loads(ir["cleaned_data"]) if ir["cleaned_data"] else {}
                    cas_val = cd.get("cas", "")
                    qty = cd.get("quantity", "")
                    unit = cd.get("unit", "")
                    if cas_val:
                        qty_map[cas_val] = f"{qty} {unit}".strip()
                except (json.JSONDecodeError, TypeError):
                    pass

            for rec in eu_data:
                rec["quantity"] = qty_map.get(rec["cas_number"], "")

            # Extract reactivity data
            pair_details = payload.get("pair_details", [])
            matrix_data = payload.get("matrix", [])

            # Format: ANALYSE_{clean_name}_{timestamp}_{batch_id}.xlsx
            filename = f"ANALYSE_{clean_name}_{timestamp}_{batch_id[:8]}.xlsx"
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, filename)

            generator.generate_unified(
                inventory_data=eu_data,
                chemicals=chemicals,
                reactivity_pairs=pair_details,
                matrix_data=matrix_data,
                output_path=output_path,
                report_title=custom_title,
            )

            return send_file(
                output_path,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                as_attachment=True,
                download_name=filename,
            )

        # ── Mode 1: CAS-only single-sheet ──
        if not cas_numbers or not isinstance(cas_numbers, list):
            return jsonify({"error": "Provide 'batch_id' or 'cas_numbers' list"}), 400

        cas_numbers = [str(c).strip() for c in cas_numbers if c]
        if not cas_numbers:
            return jsonify({"error": "No valid CAS numbers provided"}), 400

        logger.info("CAS-only export for %d CAS numbers", len(cas_numbers))
        filename = f"SAFEWARE_Compliance_Report_{timestamp}.xlsx"
        output_path = os.path.join(output_dir, filename)

        generator.generate(
            cas_numbers=cas_numbers,
            output_path=output_path,
            report_title=custom_title,
        )

        return send_file(
            output_path,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=filename,
        )

    except FileNotFoundError as e:
        logger.error("Database not found: %s", e)
        return jsonify({"error": "Database not available"}), 500
    except Exception as e:
        logger.error("Report generation failed: %s", e, exc_info=True)
        return jsonify({"error": f"Report generation failed: {str(e)}"}), 500
