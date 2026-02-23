#!/usr/bin/env python3
"""
EU Compliance — Excel Generator Tests
=======================================
Tests the ComplianceExcelGenerator for:
  1. File creation and validity
  2. "Not explicitly listed..." string present for missing CAS
  3. Verbatim data integrity for known CAS numbers
  4. Sheet protection is enabled

Usage:
    pytest tests/test_excel_generator.py -v
"""

import os
import sys
import sqlite3
import pytest

# Ensure backend/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openpyxl import load_workbook
from logic.excel_generator import ComplianceExcelGenerator, NOT_LISTED


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    """
    Create a minimal test database with CAMEO + EU tables
    populated with known test data.
    """
    db_path = str(tmp_path / "test_chemicals.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Minimal chemicals table
    cursor.execute("""
        CREATE TABLE chemicals (
            id INTEGER PRIMARY KEY,
            name TEXT,
            special_hazards TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO chemicals (id, name, special_hazards)
        VALUES (1, 'Acetone', 'Flammable liquid and vapor')
    """)
    cursor.execute("""
        INSERT INTO chemicals (id, name, special_hazards)
        VALUES (2, 'Hydrogen', 'Extremely flammable gas')
    """)

    # chemical_cas
    cursor.execute("""
        CREATE TABLE chemical_cas (
            chem_id INTEGER,
            cas_id TEXT,
            cas_nodash TEXT,
            sort INTEGER,
            annotation TEXT,
            PRIMARY KEY (chem_id, cas_id)
        )
    """)
    cursor.execute("INSERT INTO chemical_cas VALUES (1, '67-64-1', '67641', 0, NULL)")
    cursor.execute("INSERT INTO chemical_cas VALUES (2, '1333-74-0', '1333740', 0, NULL)")

    # eu_clp_hazards
    cursor.execute("""
        CREATE TABLE eu_clp_hazards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_number TEXT,
            cas_number TEXT NOT NULL,
            ec_number TEXT,
            chemical_name_eu TEXT,
            classification TEXT,
            hazard_statement_codes TEXT,
            pictogram_signal_codes TEXT,
            labelling_hazard_codes TEXT,
            suppl_hazard_codes TEXT,
            specific_conc_limits TEXT,
            notes TEXT,
            UNIQUE(cas_number, index_number)
        )
    """)
    cursor.execute("""
        INSERT INTO eu_clp_hazards
        (index_number, cas_number, ec_number, chemical_name_eu, classification, hazard_statement_codes)
        VALUES ('606-001-00-8', '67-64-1', '200-662-2', 'acetone', 'Flam. Liq. 2\nEye Irrit. 2', 'H225\nH319')
    """)
    cursor.execute("""
        INSERT INTO eu_clp_hazards
        (index_number, cas_number, ec_number, chemical_name_eu, classification, hazard_statement_codes)
        VALUES ('001-001-00-9', '1333-74-0', '215-605-7', 'hydrogen', 'Flam. Gas 1', 'H220')
    """)

    # eu_svhc (only Acetone is listed, for test purposes)
    cursor.execute("""
        CREATE TABLE eu_svhc (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            substance_name TEXT,
            ec_number TEXT,
            cas_number TEXT,
            reason_for_inclusion TEXT,
            date_of_inclusion TEXT,
            remarks TEXT,
            UNIQUE(cas_number, ec_number, substance_name)
        )
    """)
    # Intentionally NOT adding Hydrogen to SVHC → should get "Not listed"

    # eu_restrictions
    cursor.execute("""
        CREATE TABLE eu_restrictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            substance_name TEXT,
            ec_number TEXT,
            cas_number TEXT,
            entry_number TEXT,
            conditions TEXT,
            remarks TEXT,
            UNIQUE(cas_number, ec_number, entry_number)
        )
    """)
    # Intentionally empty → all should get "Not listed"

    conn.commit()
    conn.close()
    return db_path


# ── Tests ────────────────────────────────────────────────

class TestExcelGeneration:
    """Test that the Excel report is generated correctly."""

    def test_file_created(self, test_db, tmp_path):
        """generate() must produce a valid .xlsx file."""
        output = str(tmp_path / "report.xlsx")
        gen = ComplianceExcelGenerator(test_db)
        result = gen.generate(["67-64-1", "1333-74-0"], output)

        assert os.path.exists(result)
        assert result.endswith(".xlsx")
        # Verify it's a valid Excel file
        wb = load_workbook(result)
        assert wb.active is not None
        wb.close()

    def test_not_listed_for_missing_cas(self, test_db, tmp_path):
        """CAS not in any EU table must show the exact NOT_LISTED string."""
        output = str(tmp_path / "report.xlsx")
        gen = ComplianceExcelGenerator(test_db)
        gen.generate(
            ["67-64-1", "1333-74-0", "99999-99-9"],  # Last one is fake
            output,
        )

        wb = load_workbook(output)
        ws = wb.active

        # Find the row with the fake CAS
        found_not_listed = False
        for row in ws.iter_rows(min_row=5, values_only=True):
            if row[0] == "99999-99-9":
                # Columns 3-5 (EU H-Codes, SVHC, Restrictions) should be NOT_LISTED
                assert row[3] == NOT_LISTED, f"EU H-Codes should be NOT_LISTED, got: {row[3]}"
                assert row[4] == NOT_LISTED, f"SVHC should be NOT_LISTED, got: {row[4]}"
                assert row[5] == NOT_LISTED, f"Restrictions should be NOT_LISTED, got: {row[5]}"
                found_not_listed = True

        assert found_not_listed, "Fake CAS 99999-99-9 not found in report"
        wb.close()

    def test_verbatim_hcodes(self, test_db, tmp_path):
        """Known CAS must have verbatim H-codes from the database."""
        output = str(tmp_path / "report.xlsx")
        gen = ComplianceExcelGenerator(test_db)
        gen.generate(["67-64-1"], output)

        wb = load_workbook(output)
        ws = wb.active

        for row in ws.iter_rows(min_row=5, values_only=True):
            if row[0] == "67-64-1":
                hcodes = row[3]  # EU CLP H-Codes column
                assert "H225" in hcodes, f"H225 missing from acetone H-codes: {hcodes}"
                assert "H319" in hcodes, f"H319 missing from acetone H-codes: {hcodes}"
                break
        else:
            pytest.fail("CAS 67-64-1 not found in report")

        wb.close()

    def test_sheet_protection(self, test_db, tmp_path):
        """Sheet must be protected to prevent editing."""
        output = str(tmp_path / "report.xlsx")
        gen = ComplianceExcelGenerator(test_db)
        gen.generate(["67-64-1"], output)

        wb = load_workbook(output)
        ws = wb.active

        assert ws.protection.sheet is True, "Sheet protection not enabled!"
        wb.close()

    def test_enterprise_header(self, test_db, tmp_path):
        """Report must have the enterprise header with title."""
        output = str(tmp_path / "report.xlsx")
        gen = ComplianceExcelGenerator(test_db)
        gen.generate(["67-64-1"], output)

        wb = load_workbook(output)
        ws = wb.active

        title_cell = ws.cell(row=1, column=1).value
        assert "SAFEWARE" in title_cell.upper(), f"Title missing SAFEWARE: {title_cell}"
        assert "COMPLIANCE" in title_cell.upper(), f"Title missing COMPLIANCE: {title_cell}"
        wb.close()

    def test_empty_cas_list(self, test_db, tmp_path):
        """Empty CAS list should still produce a valid file (header only)."""
        output = str(tmp_path / "report.xlsx")
        gen = ComplianceExcelGenerator(test_db)
        gen.generate([], output)

        assert os.path.exists(output)
        wb = load_workbook(output)
        ws = wb.active
        # Should have header rows but no data rows
        assert ws.cell(row=1, column=1).value is not None
        wb.close()
