#!/usr/bin/env python3
"""
EU CLP Compliance — ECHA Annex VI ATP23 ETL Script
====================================================
Reads the official ECHA CLP Annex VI Excel file and ingests it
into the `eu_clp_hazards` table in `chemicals.db`.

ZERO-LIABILITY RULE:
    All hazard text is transferred VERBATIM from the source file.
    No LLM processing, summarization, or inference is performed
    on any chemical hazard data.

OFFLINE-FIRST:
    Uses local Pandas + SQLite3 only. No web requests.

Prerequisites:
    1. Run `python scripts/setup_eu_db.py` first to create the table.
    2. The ECHA CLP Excel file must exist at the hardcoded path.

Usage:
    python scripts/import_echa_clp.py
"""

import os
import sys
import sqlite3
import time
import logging

import pandas as pd

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("import_echa_clp")

# ── Hardcoded paths ──────────────────────────────────────
EXCEL_PATH = r"C:\Users\BEHNOOD\Desktop\CAMEOO\CAMEO-new\backend\data\annex_vi_clp_table_atp23_en.xlsx"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(BACKEND_DIR, "data", "chemicals.db")

# ── Column mapping: Excel header → DB column ─────────────
# Keys   = exact Excel column names (at header row 2)
# Values = target eu_clp_hazards column names
COLUMN_MAP = {
    "Index No":                                      "index_number",
    "CAS No":                                        "cas_number",
    "EC No":                                         "ec_number",
    "Chemical Name":                                 "chemical_name_eu",
    "Hazard Class and Category Code(s)":             "classification",
    "Classification Hazard Statement Code(s)":       "hazard_statement_codes",
    "Labelling Pictogram, Signal Word Code(s)":      "pictogram_signal_codes",
    "Labelling Hazard Statement Code(s)":            "labelling_hazard_codes",
    "Labelling Suppl. Hazard Statement Code(s)":     "suppl_hazard_codes",
    "M, SCL, ATE":                                   "specific_conc_limits",
    "Notes":                                         "notes",
}

# DB columns in insertion order (must match the INSERT statement)
DB_COLUMNS = [
    "index_number",
    "cas_number",
    "ec_number",
    "chemical_name_eu",
    "classification",
    "hazard_statement_codes",
    "pictogram_signal_codes",
    "labelling_hazard_codes",
    "suppl_hazard_codes",
    "specific_conc_limits",
    "notes",
]


def load_and_clean(excel_path: str) -> pd.DataFrame:
    """
    Load the ECHA CLP ATP23 Excel file and clean CAS numbers.

    Steps:
      1. Read with openpyxl, header at row 2 (0-indexed), selecting
         only the 11 columns we need.
      2. Rename columns to DB-friendly names.
      3. Strip whitespace from CAS numbers.
      4. Drop rows where CAS is null/empty (cannot join without CAS).
      5. Convert all NaN to None for SQLite compatibility.

    Returns a cleaned DataFrame ready for insertion.
    """
    logger.info("Loading Excel file: %s", excel_path)
    t0 = time.time()

    df = pd.read_excel(
        excel_path,
        engine="openpyxl",
        header=2,                         # Actual headers are on row 3 (0-indexed: 2)
        usecols=list(COLUMN_MAP.keys()),  # Only read the 11 columns we need
        dtype=str,                        # Read everything as string to preserve verbatim
    )

    elapsed = time.time() - t0
    logger.info("Excel loaded: %d rows x %d cols in %.1fs", len(df), len(df.columns), elapsed)

    # Rename to DB column names
    df.rename(columns=COLUMN_MAP, inplace=True)

    # ── Clean CAS column ──
    # Strip whitespace
    df["cas_number"] = df["cas_number"].str.strip()

    # Drop rows without a CAS number (they cannot be joined)
    before = len(df)
    df.dropna(subset=["cas_number"], inplace=True)
    df = df[df["cas_number"] != ""]
    after = len(df)
    dropped = before - after
    if dropped > 0:
        logger.warning("Dropped %d rows with missing/empty CAS number", dropped)

    # Strip whitespace from all string columns
    for col in df.columns:
        df[col] = df[col].str.strip()

    # Replace NaN with None (SQLite-friendly NULL)
    df = df.where(pd.notna(df), None)

    logger.info("Cleaned DataFrame: %d rows ready for insertion", len(df))
    return df


def ingest_to_db(df: pd.DataFrame, db_path: str) -> int:
    """
    Batch-insert the cleaned DataFrame into eu_clp_hazards.

    Uses INSERT OR REPLACE to handle potential duplicates gracefully
    (based on UNIQUE(cas_number, index_number) constraint).

    Returns the number of rows successfully inserted.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Verify table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='eu_clp_hazards'"
    )
    if not cursor.fetchone():
        conn.close()
        raise RuntimeError(
            "Table `eu_clp_hazards` does not exist. "
            "Run `python scripts/setup_eu_db.py` first."
        )

    # Build parameterized INSERT
    placeholders = ", ".join(["?"] * len(DB_COLUMNS))
    col_names = ", ".join(DB_COLUMNS)
    sql = f"INSERT OR REPLACE INTO eu_clp_hazards ({col_names}) VALUES ({placeholders})"

    # Convert DataFrame to list of tuples (in correct column order)
    records = df[DB_COLUMNS].values.tolist()

    # Convert numpy types to native Python (sqlite3 compatibility)
    clean_records = []
    for row in records:
        clean_records.append(
            tuple(None if pd.isna(v) else str(v) for v in row)
        )

    logger.info("Inserting %d records into eu_clp_hazards...", len(clean_records))
    t0 = time.time()

    cursor.executemany(sql, clean_records)
    conn.commit()

    elapsed = time.time() - t0
    inserted = cursor.rowcount if cursor.rowcount > 0 else len(clean_records)

    conn.close()
    logger.info("Batch insert completed in %.2fs", elapsed)

    return inserted


def verify_import(db_path: str) -> None:
    """Post-import verification: count rows and show sample data."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM eu_clp_hazards")
    total = cursor.fetchone()[0]
    logger.info("Verification: %d total records in eu_clp_hazards", total)

    # Count how many CAS numbers overlap with existing CAMEO data
    cursor.execute("""
        SELECT COUNT(DISTINCT e.cas_number)
        FROM eu_clp_hazards e
        INNER JOIN chemical_cas c ON e.cas_number = c.cas_id
    """)
    overlap = cursor.fetchone()[0]
    logger.info(
        "CAS overlap with CAMEO chemicals: %d / %d (%.1f%%)",
        overlap, total, (overlap / total * 100) if total > 0 else 0,
    )

    # Show 3 sample records
    cursor.execute(
        "SELECT cas_number, chemical_name_eu, hazard_statement_codes "
        "FROM eu_clp_hazards LIMIT 3"
    )
    logger.info("Sample records:")
    for row in cursor.fetchall():
        logger.info("  CAS=%s | Name=%s | H-Codes=%s", row[0], row[1], row[2])

    conn.close()


def main() -> None:
    # ── Pre-flight checks ──
    if not os.path.exists(EXCEL_PATH):
        logger.error("Excel file not found: %s", EXCEL_PATH)
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        logger.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    # ── ETL Pipeline ──
    logger.info("=" * 60)
    logger.info("ECHA CLP Annex VI ATP23 — ETL Pipeline")
    logger.info("=" * 60)

    # Step 1: Load & Clean
    df = load_and_clean(EXCEL_PATH)

    # Step 2: Ingest
    count = ingest_to_db(df, DB_PATH)
    logger.info("Successfully ingested %d records from ATP23", count)

    # Step 3: Verify
    verify_import(DB_PATH)

    logger.info("=" * 60)
    logger.info("ETL COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
