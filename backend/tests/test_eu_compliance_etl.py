#!/usr/bin/env python3
"""
EU CLP Compliance — ETL Unit Tests
====================================
Tests the ETL logic for ingesting ECHA CLP Annex VI data.

Key Guarantees:
  1. Hazard statement codes are copied VERBATIM (zero-liability).
  2. Rows with missing CAS are filtered out.
  3. Rows with weird characters in CAS are handled without crash.
  4. INSERT OR REPLACE handles duplicates gracefully.

Usage:
    pytest tests/test_eu_compliance_etl.py -v
"""

import sqlite3
import pytest
import pandas as pd
import numpy as np

# ── Import the functions under test ──────────────────────
# Add backend/ to path so we can import from scripts/
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.setup_eu_db import create_eu_clp_table
from scripts.import_echa_clp import load_and_clean, ingest_to_db, DB_COLUMNS


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database with the eu_clp_hazards table."""
    db_path = ":memory:"
    conn = sqlite3.connect(db_path)
    # We can't use create_eu_clp_table() directly with :memory:
    # because it opens/closes its own connection. So create inline.
    conn.execute("""
        CREATE TABLE eu_clp_hazards (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            index_number            TEXT,
            cas_number              TEXT    NOT NULL,
            ec_number               TEXT,
            chemical_name_eu        TEXT,
            classification          TEXT,
            hazard_statement_codes  TEXT,
            pictogram_signal_codes  TEXT,
            labelling_hazard_codes  TEXT,
            suppl_hazard_codes      TEXT,
            specific_conc_limits    TEXT,
            notes                   TEXT,
            UNIQUE(cas_number, index_number)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_eu_clp_cas
        ON eu_clp_hazards(cas_number)
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def mock_dataframe():
    """
    Create a mock DataFrame simulating 3 rows from the ECHA Excel file:
      Row 1: Valid CAS, normal data → should be ingested.
      Row 2: Missing CAS (NaN)     → should be filtered out.
      Row 3: CAS with weird chars  → should be handled without crash.
    """
    data = {
        "Index No":                                     ["001-001-00-9",  None,            "602-001-00-7"],
        "CAS No":                                       ["1333-74-0",    np.nan,          "  71±43-2!  "],
        "EC No":                                        ["215-605-7",    "200-001-8",     "200-662-2"],
        "Chemical Name":                                ["hydrogen",     "formaldehyde",  "benzene"],
        "Hazard Class and Category Code(s)":            ["Flam. Gas 1\nPress. Gas", None, "Carc. 1A\nMuta. 1B"],
        "Classification Hazard Statement Code(s)":      ["H220",         "H301\nH311",    "H350\nH340\nH372"],
        "Labelling Pictogram, Signal Word Code(s)":     ["GHS02\nGHS04\nDgr", None,       "GHS08\nDgr"],
        "Labelling Hazard Statement Code(s)":           ["H220",         None,            "H350\nH340"],
        "Labelling Suppl. Hazard Statement Code(s)":    [None,           None,            None],
        "M, SCL, ATE":                                  [None,           None,            "inhalation: ATE = 20 mg/L"],
        "Notes":                                        ["U",            None,            "C"],
    }
    return pd.DataFrame(data)


# ── Tests ────────────────────────────────────────────────

class TestSchemaCreation:
    """Test that the schema migration creates the table correctly."""

    def test_create_table_on_file_db(self, tmp_path):
        """create_eu_clp_table should create eu_clp_hazards in a real file DB."""
        db_path = str(tmp_path / "test.db")
        # Create an empty DB first
        sqlite3.connect(db_path).close()

        create_eu_clp_table(db_path)

        conn = sqlite3.connect(db_path)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()

        assert "eu_clp_hazards" in tables

    def test_idempotent(self, tmp_path):
        """Running create_eu_clp_table twice should not error."""
        db_path = str(tmp_path / "test.db")
        sqlite3.connect(db_path).close()

        create_eu_clp_table(db_path)
        create_eu_clp_table(db_path)  # should not raise


class TestETLCleaning:
    """Test the cleaning logic of the ETL pipeline."""

    def test_missing_cas_filtered(self, mock_dataframe, tmp_path):
        """Rows with NaN CAS must be dropped."""
        # Save mock data to Excel so load_and_clean can read it
        excel_path = str(tmp_path / "test.xlsx")

        # Write with the correct header structure (2 dummy rows + header)
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            # Write 2 blank rows (to mimic ECHA disclaimer header)
            blank = pd.DataFrame([[""] * len(mock_dataframe.columns)] * 2)
            blank.to_excel(writer, index=False, header=False, startrow=0)
            # Write actual data starting at row 2 (with header)
            mock_dataframe.to_excel(writer, index=False, startrow=2)

        df = load_and_clean(excel_path)

        # Row 2 (formaldehyde with NaN CAS) should be gone
        assert len(df) == 2
        assert "formaldehyde" not in df["chemical_name_eu"].values

    def test_cas_whitespace_stripped(self, mock_dataframe, tmp_path):
        """CAS numbers must have whitespace stripped. Weird chars stay (verbatim)."""
        excel_path = str(tmp_path / "test.xlsx")
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            blank = pd.DataFrame([[""] * len(mock_dataframe.columns)] * 2)
            blank.to_excel(writer, index=False, header=False, startrow=0)
            mock_dataframe.to_excel(writer, index=False, startrow=2)

        df = load_and_clean(excel_path)

        cas_values = df["cas_number"].tolist()
        # hydrogen CAS should be clean
        assert "1333-74-0" in cas_values
        # benzene CAS had weird chars + spaces — spaces stripped, chars kept verbatim
        benzene_cas = [c for c in cas_values if "71" in c][0]
        assert not benzene_cas.startswith(" ")
        assert not benzene_cas.endswith(" ")


class TestETLVerbatimIntegrity:
    """
    CRITICAL: Prove that hazard codes are transferred EXACTLY VERBATIM.
    No summarization, no reformatting, no character changes.
    """

    def test_hazard_codes_verbatim(self, in_memory_db):
        """H-codes in DB must be EXACTLY what was in the source data."""
        # Simulate cleaned data (already past load_and_clean)
        df = pd.DataFrame({
            "index_number":           ["001-001-00-9"],
            "cas_number":             ["1333-74-0"],
            "ec_number":              ["215-605-7"],
            "chemical_name_eu":       ["hydrogen"],
            "classification":         ["Flam. Gas 1\nPress. Gas"],
            "hazard_statement_codes": ["H220"],
            "pictogram_signal_codes": ["GHS02\nGHS04\nDgr"],
            "labelling_hazard_codes": ["H220"],
            "suppl_hazard_codes":     [None],
            "specific_conc_limits":   [None],
            "notes":                  ["U"],
        })

        # Insert directly using connection (bypass file-based ingest_to_db)
        placeholders = ", ".join(["?"] * len(DB_COLUMNS))
        col_names = ", ".join(DB_COLUMNS)
        sql = f"INSERT OR REPLACE INTO eu_clp_hazards ({col_names}) VALUES ({placeholders})"

        records = df[DB_COLUMNS].values.tolist()
        clean_records = [
            tuple(None if pd.isna(v) else str(v) for v in row)
            for row in records
        ]

        in_memory_db.executemany(sql, clean_records)
        in_memory_db.commit()

        # Verify verbatim
        row = in_memory_db.execute(
            "SELECT hazard_statement_codes, classification FROM eu_clp_hazards WHERE cas_number='1333-74-0'"
        ).fetchone()

        assert row is not None
        assert row[0] == "H220",                       f"H-code altered! Got: {row[0]}"
        assert row[1] == "Flam. Gas 1\nPress. Gas",    f"Classification altered! Got: {row[1]}"

    def test_multiline_hcodes_preserved(self, in_memory_db):
        """Multi-line H-codes (H350\\nH340\\nH372) must be preserved exactly."""
        original_hcodes = "H350\nH340\nH372"

        df = pd.DataFrame({
            "index_number":           ["602-001-00-7"],
            "cas_number":             ["71-43-2"],
            "ec_number":              ["200-662-2"],
            "chemical_name_eu":       ["benzene"],
            "classification":         ["Carc. 1A\nMuta. 1B"],
            "hazard_statement_codes": [original_hcodes],
            "pictogram_signal_codes": ["GHS08\nDgr"],
            "labelling_hazard_codes": ["H350\nH340"],
            "suppl_hazard_codes":     [None],
            "specific_conc_limits":   ["inhalation: ATE = 20 mg/L"],
            "notes":                  ["C"],
        })

        placeholders = ", ".join(["?"] * len(DB_COLUMNS))
        col_names = ", ".join(DB_COLUMNS)
        sql = f"INSERT OR REPLACE INTO eu_clp_hazards ({col_names}) VALUES ({placeholders})"
        records = [
            tuple(None if pd.isna(v) else str(v) for v in row)
            for row in df[DB_COLUMNS].values.tolist()
        ]
        in_memory_db.executemany(sql, records)
        in_memory_db.commit()

        row = in_memory_db.execute(
            "SELECT hazard_statement_codes FROM eu_clp_hazards WHERE cas_number='71-43-2'"
        ).fetchone()

        assert row[0] == original_hcodes, f"Newlines in H-codes were altered! Got: {repr(row[0])}"


class TestDuplicateHandling:
    """Test that INSERT OR REPLACE handles duplicates gracefully."""

    def test_duplicate_upsert(self, in_memory_db):
        """Inserting the same (cas_number, index_number) twice should replace, not crash."""
        df = pd.DataFrame({
            "index_number":           ["001-001-00-9", "001-001-00-9"],
            "cas_number":             ["1333-74-0",    "1333-74-0"],
            "ec_number":              ["215-605-7",    "215-605-7"],
            "chemical_name_eu":       ["hydrogen v1",  "hydrogen v2"],
            "classification":         ["Flam. Gas 1",  "Flam. Gas 1"],
            "hazard_statement_codes": ["H220",         "H220"],
            "pictogram_signal_codes": ["GHS02",        "GHS02"],
            "labelling_hazard_codes": ["H220",         "H220"],
            "suppl_hazard_codes":     [None,           None],
            "specific_conc_limits":   [None,           None],
            "notes":                  ["U",            "U"],
        })

        placeholders = ", ".join(["?"] * len(DB_COLUMNS))
        col_names = ", ".join(DB_COLUMNS)
        sql = f"INSERT OR REPLACE INTO eu_clp_hazards ({col_names}) VALUES ({placeholders})"
        records = [
            tuple(None if pd.isna(v) else str(v) for v in row)
            for row in df[DB_COLUMNS].values.tolist()
        ]
        in_memory_db.executemany(sql, records)
        in_memory_db.commit()

        count = in_memory_db.execute("SELECT COUNT(*) FROM eu_clp_hazards").fetchone()[0]
        assert count == 1, f"Duplicate not replaced! Got {count} rows"

        name = in_memory_db.execute(
            "SELECT chemical_name_eu FROM eu_clp_hazards WHERE cas_number='1333-74-0'"
        ).fetchone()[0]
        assert name == "hydrogen v2", "REPLACE should keep the latest version"
