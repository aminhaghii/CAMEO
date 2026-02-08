"""
ingest.py — File readers with auto-detection.
Supports CSV, XLSX, XLS, and JSON inventory files.
"""

import os
import logging
import pandas as pd

from etl.schema import normalize_columns

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {'.csv', '.xlsx', '.xls', '.json'}


def read_file(filepath: str) -> pd.DataFrame:
    """
    Auto-detect file type and read into a DataFrame.
    Normalizes column names to canonical form.
    Raises ValueError for unsupported formats.
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {SUPPORTED_EXTENSIONS}")

    logger.info(f"Reading file: {filepath} (type: {ext})")

    if ext == '.csv':
        df = _read_csv(filepath)
    elif ext in ('.xlsx', '.xls'):
        df = _read_excel(filepath)
    elif ext == '.json':
        df = _read_json(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    if df.empty:
        raise ValueError("File is empty or contains no data rows.")

    # Normalize column names
    col_map = normalize_columns(list(df.columns))
    df = df.rename(columns=col_map)

    logger.info(f"Ingested {len(df)} rows, columns: {list(df.columns)}")
    return df


def _read_csv(filepath: str) -> pd.DataFrame:
    """Read CSV with encoding detection fallback."""
    for encoding in ['utf-8', 'latin-1', 'cp1252']:
        try:
            df = pd.read_csv(filepath, encoding=encoding, dtype=str, keep_default_na=False)
            return df.dropna(how='all')
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode CSV file with any supported encoding.")


def _read_excel(filepath: str) -> pd.DataFrame:
    """Read first sheet of Excel file."""
    df = pd.read_excel(filepath, dtype=str, keep_default_na=False, engine='openpyxl')
    return df.dropna(how='all')


def _read_json(filepath: str) -> pd.DataFrame:
    """Read JSON array of objects."""
    df = pd.read_json(filepath, dtype=str)
    return df.dropna(how='all')
