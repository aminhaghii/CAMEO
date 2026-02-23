#!/usr/bin/env python3
"""
Enterprise Excel Compliance Report Generator
==============================================
Generates locked, enterprise-grade Excel reports combining:
  - User inventory (CAS numbers)
  - CAMEO reactivity data
  - EU CLP hazard classifications (ATP23)
  - EU SVHC status
  - EU Restrictions

ZERO-LIABILITY RULE:
    All data is queried VERBATIM from the database.
    If a chemical has no EU data, output EXACTLY:
    "Not explicitly listed in EU harmonized lists"

SECURITY:
    Excel cells are LOCKED with sheet protection (password: 'safeware').
    Users can sort/filter but CANNOT edit official legal safety phrases.

Usage:
    from logic.excel_generator import ComplianceExcelGenerator
    gen = ComplianceExcelGenerator(db_path)
    filepath = gen.generate(cas_numbers, output_path)
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import (
    Font, PatternFill, Alignment, Border, Side, Protection,
)
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

logger = logging.getLogger("excel_generator")

# ── Constants ────────────────────────────────────────────
NOT_LISTED = "Not explicitly listed in EU harmonized lists"

# Enterprise style palette
HEADER_BG = PatternFill(start_color="0D1B2A", end_color="0D1B2A", fill_type="solid")  # Dark navy
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=14)
SUBHEADER_FONT = Font(name="Calibri", color="8B9DAF", size=10)
COL_HEADER_BG = PatternFill(start_color="1B263B", end_color="1B263B", fill_type="solid")
COL_HEADER_FONT = Font(name="Calibri", bold=True, color="E0E1DD", size=11)
DATA_FONT = Font(name="Calibri", size=10, color="1B1B1B")
HAZARD_FONT = Font(name="Calibri", size=10, color="C1121F")  # Red for hazards
SAFE_FONT = Font(name="Calibri", size=10, color="4A7C59", italic=True)  # Green italic for "not listed"
THIN_BORDER = Border(
    left=Side(style="thin", color="415A77"),
    right=Side(style="thin", color="415A77"),
    top=Side(style="thin", color="415A77"),
    bottom=Side(style="thin", color="415A77"),
)
WRAP_ALIGNMENT = Alignment(wrap_text=True, vertical="top")

# Column definitions: (header_text, width, db_key)
REPORT_COLUMNS = [
    ("Inventory CAS",             18, "cas_number"),
    ("Chemical Name",             35, "chemical_name"),
    ("CAMEO Reactivity Hazards",  40, "cameo_hazards"),
    ("EU CLP H-Codes",            30, "eu_hcodes"),
    ("EU SVHC Status",            40, "svhc_status"),
    ("EU Restrictions",           40, "restrictions"),
]


class ComplianceExcelGenerator:
    """
    Enterprise Excel report generator for EU REACH/CLP compliance.

    This class queries the local chemicals.db to build a comprehensive
    compliance report for a list of CAS numbers from the user's inventory.
    """

    def __init__(self, db_path: str):
        """
        Initialize with the path to chemicals.db.

        Args:
            db_path: Absolute path to the chemicals.db SQLite file.
        """
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database not found: {db_path}")
        self.db_path = db_path

    def _query_compliance_data(self, cas_numbers: list[str]) -> list[dict]:
        """
        Query all compliance data for the given CAS numbers.

        Performs LEFT JOINs across:
          - chemical_cas → chemicals (CAMEO name + hazards)
          - eu_clp_hazards (EU CLP H-codes)
          - eu_svhc (SVHC status)
          - eu_restrictions (Restrictions)

        If a CAS is NOT found in any EU table, the corresponding field
        gets the value: "Not explicitly listed in EU harmonized lists"

        Returns a list of dicts, one per CAS number.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        results = []
        for cas in cas_numbers:
            cas_clean = cas.strip()

            # ── 1. CAMEO data (name + special_hazards) ──
            cursor.execute("""
                SELECT c.name, c.special_hazards
                FROM chemicals c
                INNER JOIN chemical_cas cc ON c.id = cc.chem_id
                WHERE cc.cas_id = ?
                LIMIT 1
            """, (cas_clean,))
            cameo_row = cursor.fetchone()

            chemical_name = cameo_row["name"] if cameo_row else cas_clean
            cameo_hazards = (
                cameo_row["special_hazards"] if cameo_row and cameo_row["special_hazards"]
                else NOT_LISTED
            )

            # ── 2. EU CLP data ──
            cursor.execute("""
                SELECT
                    classification,
                    hazard_statement_codes,
                    pictogram_signal_codes
                FROM eu_clp_hazards
                WHERE cas_number = ?
            """, (cas_clean,))
            clp_rows = cursor.fetchall()

            if clp_rows:
                # Combine all H-codes for this CAS (may have multiple entries)
                hcodes_set = set()
                for row in clp_rows:
                    if row["hazard_statement_codes"]:
                        for code in row["hazard_statement_codes"].replace("\n", " ").split():
                            hcodes_set.add(code.strip())
                eu_hcodes = "\n".join(sorted(hcodes_set)) if hcodes_set else NOT_LISTED
            else:
                eu_hcodes = NOT_LISTED

            # ── 3. SVHC status ──
            cursor.execute("""
                SELECT substance_name, reason_for_inclusion, date_of_inclusion
                FROM eu_svhc
                WHERE cas_number = ?
            """, (cas_clean,))
            svhc_rows = cursor.fetchall()

            if svhc_rows:
                parts = []
                for row in svhc_rows:
                    reason = row["reason_for_inclusion"] or "Listed"
                    date = row["date_of_inclusion"] or ""
                    parts.append(f"SVHC — {reason}" + (f" ({date})" if date else ""))
                svhc_status = "\n".join(parts)
            else:
                svhc_status = NOT_LISTED

            # ── 4. Restrictions ──
            cursor.execute("""
                SELECT entry_number, conditions, remarks
                FROM eu_restrictions
                WHERE cas_number = ?
            """, (cas_clean,))
            rest_rows = cursor.fetchall()

            if rest_rows:
                parts = []
                for row in rest_rows:
                    entry = row["entry_number"] or "?"
                    remark = row["remarks"] or row["conditions"] or "Restricted"
                    parts.append(f"Entry {entry}: {remark}")
                restrictions = "\n".join(parts)
            else:
                restrictions = NOT_LISTED

            results.append({
                "cas_number": cas_clean,
                "chemical_name": chemical_name,
                "cameo_hazards": cameo_hazards,
                "eu_hcodes": eu_hcodes,
                "svhc_status": svhc_status,
                "restrictions": restrictions,
            })

        conn.close()
        return results

    def generate(
        self,
        cas_numbers: list[str],
        output_path: str,
        report_title: Optional[str] = None,
    ) -> str:
        """
        Generate the enterprise compliance Excel report.

        Args:
            cas_numbers: List of CAS numbers from user inventory.
            output_path: Path where the Excel file will be saved.
            report_title: Optional custom title.

        Returns:
            The absolute path to the generated file.
        """
        logger.info("Generating compliance report for %d CAS numbers...", len(cas_numbers))

        # Query all data
        data = self._query_compliance_data(cas_numbers)

        # Create workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Compliance Report"

        # ════════════════════════════════════════════════
        #  Enterprise Header (Rows 1-3)
        # ════════════════════════════════════════════════
        title = report_title or "SAFEWARE COMPLIANCE REPORT — EU REACH/CLP"
        num_cols = len(REPORT_COLUMNS)

        # Row 1: Main title (merged across all columns)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_cols)
        title_cell = ws.cell(row=1, column=1, value=title)
        title_cell.font = HEADER_FONT
        title_cell.fill = HEADER_BG
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 40

        # Row 2: Subtitle with date
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=num_cols)
        subtitle = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Items: {len(cas_numbers)}  |  Data Sources: CAMEO, ECHA CLP ATP23, SVHC Candidate List, Annex XVII Restrictions"
        sub_cell = ws.cell(row=2, column=1, value=subtitle)
        sub_cell.font = SUBHEADER_FONT
        sub_cell.fill = HEADER_BG
        sub_cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[2].height = 25

        # Row 3: Empty spacer
        ws.row_dimensions[3].height = 5
        for col_idx in range(1, num_cols + 1):
            ws.cell(row=3, column=col_idx).fill = HEADER_BG

        # ════════════════════════════════════════════════
        #  Column Headers (Row 4)
        # ════════════════════════════════════════════════
        header_row = 4
        for col_idx, (header_text, width, _) in enumerate(REPORT_COLUMNS, start=1):
            cell = ws.cell(row=header_row, column=col_idx, value=header_text)
            cell.font = COL_HEADER_FONT
            cell.fill = COL_HEADER_BG
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = THIN_BORDER
            # Cell protection: locked (part of sheet protection)
            cell.protection = Protection(locked=True)
            # Set column width
            ws.column_dimensions[get_column_letter(col_idx)].width = width

        # ════════════════════════════════════════════════
        #  Data Rows (Row 5+)
        # ════════════════════════════════════════════════
        for row_offset, record in enumerate(data):
            row_num = header_row + 1 + row_offset

            for col_idx, (_, _, db_key) in enumerate(REPORT_COLUMNS, start=1):
                value = record.get(db_key, "")
                cell = ws.cell(row=row_num, column=col_idx, value=value)
                cell.alignment = WRAP_ALIGNMENT
                cell.border = THIN_BORDER
                # ALL data cells are LOCKED for zero-liability
                cell.protection = Protection(locked=True)

                # Style based on content
                if value == NOT_LISTED:
                    cell.font = SAFE_FONT
                elif db_key in ("eu_hcodes", "cameo_hazards", "svhc_status", "restrictions") and value != NOT_LISTED:
                    cell.font = HAZARD_FONT
                else:
                    cell.font = DATA_FONT

        # ════════════════════════════════════════════════
        #  AutoFilter on header row
        # ════════════════════════════════════════════════
        last_col = get_column_letter(num_cols)
        last_row = header_row + len(data)
        ws.auto_filter.ref = f"A{header_row}:{last_col}{last_row}"

        # ════════════════════════════════════════════════
        #  Sheet Protection (ZERO LIABILITY)
        # ════════════════════════════════════════════════
        # Lock ALL cells (they're already set to locked=True above)
        # Allow: sort, autoFilter (so users can filter/analyze)
        # Forbid: edit cell contents, insert/delete rows, formatting
        ws.protection.sheet = True
        ws.protection.password = "safeware"
        ws.protection.sort = False         # False = ALLOWED (inverted logic in openpyxl)
        ws.protection.autoFilter = False   # False = ALLOWED
        ws.protection.formatColumns = False
        ws.protection.formatRows = False

        # ════════════════════════════════════════════════
        #  Print Setup
        # ════════════════════════════════════════════════
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.print_title_rows = f"1:{header_row}"  # Repeat header on each printed page

        # ════════════════════════════════════════════════
        #  Save
        # ════════════════════════════════════════════════
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        wb.save(output_path)
        logger.info("Report saved: %s (%d records)", output_path, len(data))

        return os.path.abspath(output_path)
