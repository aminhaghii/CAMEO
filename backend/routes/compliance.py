"""
EU Compliance — Flask Blueprint Route
=======================================
Provides an API endpoint for generating EU REACH/CLP compliance
Excel reports for a user's chemical inventory.

Endpoints:
    POST /api/compliance/export
        Body: {"cas_numbers": ["67-64-1", "1333-74-0", ...]}
        Returns: Excel file download

    GET /compliance
        Renders the compliance report page (UI).
"""

import os
import tempfile
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

    Request Body (JSON):
        {
            "cas_numbers": ["67-64-1", "1333-74-0", "71-43-2"],
            "title": "Optional custom report title"
        }

    Returns:
        Excel file (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)

    Error Responses:
        400: Missing or empty cas_numbers
        500: Generation error
    """
    # ── Parse request ──
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    cas_numbers = data.get("cas_numbers", [])
    if not cas_numbers or not isinstance(cas_numbers, list):
        return jsonify({
            "error": "Field 'cas_numbers' must be a non-empty list of CAS strings"
        }), 400

    # Sanitize: ensure all entries are strings
    cas_numbers = [str(c).strip() for c in cas_numbers if c]
    if not cas_numbers:
        return jsonify({"error": "No valid CAS numbers provided"}), 400

    custom_title = data.get("title", None)

    logger.info("Compliance export requested for %d CAS numbers", len(cas_numbers))

    try:
        # ── Import the generator (lazy import to avoid circular deps) ──
        from logic.excel_generator import ComplianceExcelGenerator

        # Resolve DB path
        db_path = current_app.config.get(
            "CHEMICALS_DB_PATH",
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "data", "chemicals.db",
            ),
        )

        # Generate to a temp file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"SAFEWARE_Compliance_Report_{timestamp}.xlsx"

        # Use the uploads directory (already exists in Flask app)
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "uploads",
        )
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, filename)

        generator = ComplianceExcelGenerator(db_path)
        abs_path = generator.generate(
            cas_numbers=cas_numbers,
            output_path=output_path,
            report_title=custom_title,
        )

        logger.info("Report generated: %s", abs_path)

        return send_file(
            abs_path,
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
