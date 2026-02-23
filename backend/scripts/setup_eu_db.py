#!/usr/bin/env python3
"""
EU CLP Compliance — Database Schema Migration
===============================================
Creates the `eu_clp_hazards` table in `chemicals.db`.

This table stores the official ECHA CLP Annex VI (ATP23) classification data
for each substance identified by CAS number.

STRICT RULE: This script NEVER modifies existing tables.
It only creates new infrastructure for EU compliance data.

Usage:
    python scripts/setup_eu_db.py
"""

import os
import sys
import sqlite3
import logging

# ── Logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("setup_eu_db")

# ── Resolve database path ────────────────────────────────
# Works whether called from backend/ or project root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(SCRIPT_DIR)  # backend/
DB_PATH = os.path.join(BACKEND_DIR, "data", "chemicals.db")


def create_eu_clp_table(db_path: str) -> None:
    """
    Create the `eu_clp_hazards` table if it does not exist.

    Schema (11 columns):
    ─────────────────────────────────────────────────────────
    id                    INTEGER  PRIMARY KEY AUTOINCREMENT
    index_number          TEXT     Annex VI index (e.g. 001-001-00-9)
    cas_number            TEXT     CAS registry number (join key)
    ec_number             TEXT     EC / EINECS / ELINCS number
    chemical_name_eu      TEXT     Official EU legal substance name
    classification        TEXT     Hazard Class & Category codes
    hazard_statement_codes TEXT    H-code(s), e.g. H220, H260
    pictogram_signal_codes TEXT    GHS pictogram + signal word codes
    labelling_hazard_codes TEXT    Labelling H-codes
    suppl_hazard_codes    TEXT     Supplemental EUH codes
    specific_conc_limits  TEXT     M-factors, SCL, ATE values
    notes                 TEXT     Regulatory notes (A–U flags)
    ─────────────────────────────────────────────────────────

    Constraints:
      - UNIQUE(cas_number, index_number) prevents duplicate entries
        for the same substance under the same Annex VI index.
      - INDEX on cas_number for fast JOINs with chemical_cas.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eu_clp_hazards (
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

    # Index on cas_number for fast lookups and JOINs
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_eu_clp_cas
        ON eu_clp_hazards(cas_number)
    """)

    conn.commit()
    conn.close()
    logger.info("Table `eu_clp_hazards` created/verified in: %s", db_path)


def main() -> None:
    if not os.path.exists(DB_PATH):
        logger.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    # Safety: verify existing tables are NOT touched
    conn = sqlite3.connect(DB_PATH)
    existing_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    logger.info("Existing tables (%d): %s", len(existing_tables), ", ".join(sorted(existing_tables)))

    create_eu_clp_table(DB_PATH)

    # Post-check: confirm no existing tables were altered
    conn = sqlite3.connect(DB_PATH)
    new_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    added = new_tables - existing_tables
    removed = existing_tables - new_tables
    if removed:
        logger.error("ALERT: Tables were removed: %s", removed)
        sys.exit(1)

    if added:
        logger.info("New tables added: %s", ", ".join(added))
    else:
        logger.info("No new tables added (already existed).")

    logger.info("Schema migration complete. Existing tables untouched.")


if __name__ == "__main__":
    main()
