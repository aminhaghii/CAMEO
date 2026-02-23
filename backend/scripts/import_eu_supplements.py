#!/usr/bin/env python3
"""
EU Compliance — SVHC & Restrictions ETL Script
================================================
Parses the official ECHA SVHC Candidate List and Restrictions List
Excel files and ingests them into `chemicals.db`.

ZERO-LIABILITY RULE:
    All text is transferred VERBATIM. No summarization or inference.

Creates TWO new tables:
    - eu_svhc        (SVHC Candidate List for Authorisation)
    - eu_restrictions (Annex XVII Restrictions)

Does NOT alter any existing core tables.

Usage:
    python scripts/import_eu_supplements.py
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
logger = logging.getLogger("import_eu_supplements")

# ── Paths ────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(BACKEND_DIR, "data", "chemicals.db")

SVHC_PATH = os.path.join(
    BACKEND_DIR, "data",
    "candidate-list-of-svhc-for-authorisation-export.xlsx",
)
RESTRICTIONS_PATH = os.path.join(
    BACKEND_DIR, "data",
    "restriction-list-export.xlsx",
)


# ═══════════════════════════════════════════════════════════
#  STEP 1: Schema Creation
# ═══════════════════════════════════════════════════════════

def create_tables(db_path: str) -> None:
    """Create eu_svhc and eu_restrictions tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ── SVHC Candidate List ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eu_svhc (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            substance_name       TEXT,
            ec_number            TEXT,
            cas_number           TEXT,
            reason_for_inclusion TEXT,
            date_of_inclusion    TEXT,
            remarks              TEXT,
            UNIQUE(cas_number, ec_number, substance_name)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_svhc_cas
        ON eu_svhc(cas_number)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_svhc_ec
        ON eu_svhc(ec_number)
    """)

    # ── Restrictions List (Annex XVII) ──
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eu_restrictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            substance_name   TEXT,
            ec_number        TEXT,
            cas_number       TEXT,
            entry_number     TEXT,
            conditions       TEXT,
            remarks          TEXT,
            UNIQUE(cas_number, ec_number, entry_number)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_restrictions_cas
        ON eu_restrictions(cas_number)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_restrictions_ec
        ON eu_restrictions(ec_number)
    """)

    conn.commit()
    conn.close()
    logger.info("Tables `eu_svhc` and `eu_restrictions` created/verified.")


# ═══════════════════════════════════════════════════════════
#  STEP 2: Cleaning Utilities
# ═══════════════════════════════════════════════════════════

def clean_identifier(value) -> str | None:
    """
    Clean a CAS or EC number.
    - Strip whitespace.
    - Convert '-' (ECHA placeholder for missing) to None.
    - Convert NaN to None.
    """
    if pd.isna(value):
        return None
    s = str(value).strip()
    if s in ("-", "", "—", "–"):
        return None
    return s


def clean_text(value) -> str | None:
    """Clean a text field: strip whitespace, convert NaN/empty to None."""
    if pd.isna(value):
        return None
    s = str(value).strip()
    return s if s else None


# ═══════════════════════════════════════════════════════════
#  STEP 3: SVHC ETL
# ═══════════════════════════════════════════════════════════

# The SVHC Excel has:
#   Row 0: Note about group entries
#   Row 1: Blank row
#   Row 2: Actual headers (Substance name, Description, EC No., CAS No., ...)
# So we use header=2 to skip the note+blank rows.
# But inspection showed header=1 picks up the note row. Let's use skiprows
# to handle this robustly.

SVHC_EXCEL_COLUMNS = {
    # Excel column name (at detected header) → DB column name
    "Substance name":       "substance_name",
    "EC No.":               "ec_number",
    "CAS No.":              "cas_number",
    "Reason for inclusion": "reason_for_inclusion",
    "Date of inclusion":    "date_of_inclusion",
    "Remarks":              "remarks",
}


def load_svhc(excel_path: str) -> pd.DataFrame:
    """Load and clean the SVHC Candidate List Excel file."""
    logger.info("Loading SVHC file: %s", excel_path)
    t0 = time.time()

    # Read all rows, skip the two header lines (note + blank) to find real headers
    # From inspection: real headers are in the 3rd logical row (index 2).
    # We read with header=None and manually find the header row.
    df_raw = pd.read_excel(excel_path, engine="openpyxl", header=None, dtype=str)

    # Find the row containing "Substance name" — that's the header
    header_row = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row.values if pd.notna(v)]
        if any("Substance name" in v for v in vals):
            header_row = i
            break

    if header_row is None:
        raise RuntimeError("Could not find 'Substance name' header in SVHC file")

    # Re-read with correct header
    df = pd.read_excel(excel_path, engine="openpyxl", header=header_row, dtype=str)

    elapsed = time.time() - t0
    logger.info("SVHC loaded: %d rows x %d cols in %.1fs", len(df), len(df.columns), elapsed)

    # Extract only needed columns (handle missing gracefully)
    available = {c: SVHC_EXCEL_COLUMNS[c] for c in SVHC_EXCEL_COLUMNS if c in df.columns}
    missing = set(SVHC_EXCEL_COLUMNS.keys()) - set(available.keys())
    if missing:
        logger.warning("SVHC columns not found in Excel: %s", missing)

    df = df[list(available.keys())].rename(columns=available)

    # Clean identifiers
    if "cas_number" in df.columns:
        df["cas_number"] = df["cas_number"].apply(clean_identifier)
    if "ec_number" in df.columns:
        df["ec_number"] = df["ec_number"].apply(clean_identifier)

    # Keep rows where CAS OR EC is present (fallback strategy)
    before = len(df)
    mask = df["cas_number"].notna() | df["ec_number"].notna()
    df = df[mask].copy()
    dropped = before - len(df)
    if dropped > 0:
        logger.warning("SVHC: dropped %d rows with no CAS and no EC", dropped)

    # Clean all text columns
    for col in df.columns:
        if col not in ("cas_number", "ec_number"):
            df[col] = df[col].apply(clean_text)

    # Replace remaining NaN with None
    df = df.where(pd.notna(df), None)

    logger.info("SVHC cleaned: %d rows ready", len(df))
    return df


def ingest_svhc(df: pd.DataFrame, db_path: str) -> int:
    """Batch-insert SVHC data into eu_svhc table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    db_cols = [c for c in ["substance_name", "ec_number", "cas_number",
                           "reason_for_inclusion", "date_of_inclusion", "remarks"]
               if c in df.columns]

    placeholders = ", ".join(["?"] * len(db_cols))
    col_names = ", ".join(db_cols)
    sql = f"INSERT OR REPLACE INTO eu_svhc ({col_names}) VALUES ({placeholders})"

    records = []
    for _, row in df.iterrows():
        records.append(tuple(
            None if pd.isna(row.get(c)) else str(row[c]) for c in db_cols
        ))

    logger.info("Inserting %d SVHC records...", len(records))
    cursor.executemany(sql, records)
    conn.commit()
    count = len(records)
    conn.close()
    return count


# ═══════════════════════════════════════════════════════════
#  STEP 4: Restrictions ETL
# ═══════════════════════════════════════════════════════════

RESTRICTIONS_EXCEL_COLUMNS = {
    "Substance name": "substance_name",
    "EC No.":         "ec_number",
    "CAS No.":        "cas_number",
    "Entry no.":      "entry_number",
    "Conditions":     "conditions",
    "Remarks":        "remarks",
}


def load_restrictions(excel_path: str) -> pd.DataFrame:
    """Load and clean the Restrictions List Excel file."""
    logger.info("Loading Restrictions file: %s", excel_path)
    t0 = time.time()

    df_raw = pd.read_excel(excel_path, engine="openpyxl", header=None, dtype=str)

    # Find header row
    header_row = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip() for v in row.values if pd.notna(v)]
        if any("Substance name" in v for v in vals):
            header_row = i
            break

    if header_row is None:
        raise RuntimeError("Could not find 'Substance name' header in Restrictions file")

    df = pd.read_excel(excel_path, engine="openpyxl", header=header_row, dtype=str)

    elapsed = time.time() - t0
    logger.info("Restrictions loaded: %d rows x %d cols in %.1fs", len(df), len(df.columns), elapsed)

    available = {c: RESTRICTIONS_EXCEL_COLUMNS[c] for c in RESTRICTIONS_EXCEL_COLUMNS if c in df.columns}
    missing = set(RESTRICTIONS_EXCEL_COLUMNS.keys()) - set(available.keys())
    if missing:
        logger.warning("Restrictions columns not found: %s", missing)

    df = df[list(available.keys())].rename(columns=available)

    # Clean identifiers
    if "cas_number" in df.columns:
        df["cas_number"] = df["cas_number"].apply(clean_identifier)
    if "ec_number" in df.columns:
        df["ec_number"] = df["ec_number"].apply(clean_identifier)

    # Keep rows where CAS OR EC is present
    before = len(df)
    mask = df["cas_number"].notna() | df["ec_number"].notna()
    df = df[mask].copy()
    dropped = before - len(df)
    if dropped > 0:
        logger.warning("Restrictions: dropped %d rows with no CAS and no EC", dropped)

    for col in df.columns:
        if col not in ("cas_number", "ec_number"):
            df[col] = df[col].apply(clean_text)

    df = df.where(pd.notna(df), None)

    logger.info("Restrictions cleaned: %d rows ready", len(df))
    return df


def ingest_restrictions(df: pd.DataFrame, db_path: str) -> int:
    """Batch-insert Restrictions data into eu_restrictions table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    db_cols = [c for c in ["substance_name", "ec_number", "cas_number",
                           "entry_number", "conditions", "remarks"]
               if c in df.columns]

    placeholders = ", ".join(["?"] * len(db_cols))
    col_names = ", ".join(db_cols)
    sql = f"INSERT OR REPLACE INTO eu_restrictions ({col_names}) VALUES ({placeholders})"

    records = []
    for _, row in df.iterrows():
        records.append(tuple(
            None if pd.isna(row.get(c)) else str(row[c]) for c in db_cols
        ))

    logger.info("Inserting %d Restrictions records...", len(records))
    cursor.executemany(sql, records)
    conn.commit()
    count = len(records)
    conn.close()
    return count


# ═══════════════════════════════════════════════════════════
#  STEP 5: Verification
# ═══════════════════════════════════════════════════════════

def verify(db_path: str) -> None:
    """Post-import verification."""
    conn = sqlite3.connect(db_path)

    for table in ("eu_svhc", "eu_restrictions"):
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

        # Count CAS overlap with CAMEO
        overlap = conn.execute(f"""
            SELECT COUNT(DISTINCT e.cas_number)
            FROM {table} e
            INNER JOIN chemical_cas c ON e.cas_number = c.cas_id
            WHERE e.cas_number IS NOT NULL
        """).fetchone()[0]

        logger.info(
            "%s: %d records, %d CAS overlap with CAMEO (%.1f%%)",
            table, total, overlap, (overlap / total * 100) if total > 0 else 0,
        )

        # Sample
        row = conn.execute(
            f"SELECT cas_number, substance_name FROM {table} WHERE cas_number IS NOT NULL LIMIT 1"
        ).fetchone()
        if row:
            logger.info("  Sample: CAS=%s | Name=%s", row[0], row[1])

    conn.close()


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main() -> None:
    # Pre-flight checks
    for label, path in [("Database", DB_PATH), ("SVHC file", SVHC_PATH), ("Restrictions file", RESTRICTIONS_PATH)]:
        if not os.path.exists(path):
            logger.error("%s not found: %s", label, path)
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("EU Supplementary Databases — ETL Pipeline")
    logger.info("=" * 60)

    # Create tables
    create_tables(DB_PATH)

    # SVHC
    df_svhc = load_svhc(SVHC_PATH)
    count_svhc = ingest_svhc(df_svhc, DB_PATH)
    logger.info("Successfully ingested %d SVHC records", count_svhc)

    # Restrictions
    df_rest = load_restrictions(RESTRICTIONS_PATH)
    count_rest = ingest_restrictions(df_rest, DB_PATH)
    logger.info("Successfully ingested %d Restrictions records", count_rest)

    # Verify
    verify(DB_PATH)

    logger.info("=" * 60)
    logger.info("ETL COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
