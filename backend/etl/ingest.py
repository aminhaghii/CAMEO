"""
ingest.py — Layer 1: Smart File Ingestion Engine (ETL v4).

Responsibilities:
  - Open and read ANY file (Excel/CSV/TXT/JSON) without crashing
  - Detect encoding (CSV/TXT) with multi-strategy fallback
  - Handle multi-sheet Excel with smart sheet selection
  - Detect merged cells and multi-row headers
  - Smart header detection with rule-based scoring
  - Return raw_dataframe + metadata + confidence + warnings

Design principles:
  - Fail-Open: never crash, flag issues for review
  - Sequential Fallback: try simple → complex strategies
  - Confidence-Based: every decision has a score
"""

import os
import re
import logging
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {'.csv', '.xlsx', '.xls', '.json', '.txt', '.tsv'}

# ── Keywords that suggest a row is a header ──
HEADER_KEYWORDS_EN = {
    'name', 'chemical', 'product', 'material', 'substance', 'item',
    'cas', 'cas number', 'cas no', 'code', 'product code',
    'quantity', 'qty', 'amount', 'unit', 'uom',
    'supplier', 'vendor', 'manufacturer', 'source', 'company',
    'location', 'storage', 'warehouse', 'zone',
    'date', 'expiry', 'batch', 'lot', 'purity', 'grade',
    'formula', 'price', 'cost', 'weight', 'volume',
    'description', 'notes', 'remark', 'comment',
    'un', 'un number', 'hazard', 'class',
    'no', 'row', 'sr', 'sn', 'number', '#', 'id',
}

HEADER_KEYWORDS_FA = {
    'نام', 'ماده', 'کالا', 'محصول', 'شیمیایی',
    'کد', 'شماره', 'مقدار', 'تعداد', 'واحد',
    'تامین', 'تهیه', 'شرکت', 'منبع',
    'انبار', 'محل', 'مکان',
    'تاریخ', 'بچ', 'خلوص', 'درجه',
    'فرمول', 'قیمت', 'وزن', 'حجم',
    'توضیحات', 'ردیف', 'شناسه',
}

ALL_HEADER_KEYWORDS = HEADER_KEYWORDS_EN | HEADER_KEYWORDS_FA

# Column name aliases for mapping
COLUMN_ALIASES = {
    'name': [
        'chemical', 'chemical name', 'name', 'material', 'substance', 'compound', 
        'product', 'item', 'chemical_name', 'chemicalname', 'chem_name', 'chemname'
    ],
    'cas': [
        'cas', 'cas rn', 'cas number', 'cas no', 'cas#', 'casnumber', 
        'cas_number', 'casrn', 'cas_rn', 'casnumber', 'cas id', 'casid'
    ],
    'un': [
        'un', 'un number', 'un no', 'unna', 'un_number', 'unno', 'un#'
    ],
    'formula': [
        'formula', 'molecular formula', 'chem formula', 'chemical formula',
        'chemformula', 'chemicalformula'
    ],
    'quantity': [
        'quantity', 'qty', 'amount', 'volume', 'weight', 'mass'
    ],
    'unit': [
        'unit', 'uom', 'measure', 'measurement'
    ],
    'location': [
        'location', 'storage', 'warehouse', 'zone', 'area'
    ],
}

# Sheet name patterns that suggest inventory data
INVENTORY_SHEET_PATTERNS = [
    re.compile(r'inventor', re.IGNORECASE),
    re.compile(r'raw\s*mat', re.IGNORECASE),
    re.compile(r'chemical', re.IGNORECASE),
    re.compile(r'material', re.IGNORECASE),
    re.compile(r'stock', re.IGNORECASE),
    re.compile(r'product', re.IGNORECASE),
    re.compile(r'موجودی', re.IGNORECASE),
    re.compile(r'مواد', re.IGNORECASE),
    re.compile(r'ماده', re.IGNORECASE),
    re.compile(r'انبار', re.IGNORECASE),
    re.compile(r'کالا', re.IGNORECASE),
    re.compile(r'data', re.IGNORECASE),
]


# ═══════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════

def read_file(filepath: str) -> pd.DataFrame:
    """
    Smart file ingestion with fallback strategies.
    Returns a clean DataFrame with detected headers.
    Never crashes — returns empty DataFrame + warnings on failure.

    Also sets attributes on the returned DataFrame:
      df.attrs['ingestion_metadata'] = { ... }
    """
    result = smart_ingest(filepath)

    df = result['raw_dataframe']

    # IMPORTANT: Remove raw_dataframe from metadata before attaching to attrs.
    # Storing a DataFrame inside df.attrs causes infinite recursion on deepcopy
    # (pandas deepcopies attrs during operations like dropna/rename/copy).
    meta_safe = {k: v for k, v in result.items() if k != 'raw_dataframe'}

    if df is None or df.empty:
        empty = pd.DataFrame()
        empty.attrs['ingestion_metadata'] = meta_safe
        return empty

    df.attrs['ingestion_metadata'] = meta_safe
    return df


def smart_ingest(filepath: str) -> dict:
    """
    Full Layer 1 ingestion pipeline.

    Returns:
        {
            'status': 'success' | 'partial' | 'failed',
            'raw_dataframe': DataFrame or None,
            'metadata': { file_type, encoding, sheet_name, header_row_index, ... },
            'confidence': { overall, header_detection, encoding_detection },
            'warnings': [ ... ],
        }
    """
    warnings = []
    metadata = {
        'file_type': None,
        'encoding': None,
        'language_hints': [],
        'sheet_name': None,
        'header_row_index': 0,
        'total_rows': 0,
        'total_columns': 0,
        'original_filename': os.path.basename(filepath),
    }
    confidence = {
        'overall': 0,
        'header_detection': 0,
        'encoding_detection': 100,
    }

    # ── Step 1.1: File Type Detection ──
    ext = _detect_file_type(filepath)
    metadata['file_type'] = ext

    if ext not in SUPPORTED_EXTENSIONS:
        warnings.append(f"Unsupported file type: {ext}")
        return {
            'status': 'failed',
            'raw_dataframe': None,
            'metadata': metadata,
            'confidence': confidence,
            'warnings': warnings,
        }

    # ── Step 1.2–1.6: Read file based on type ──
    # Note: Embedded objects (images, charts, shapes) are automatically ignored
    # by pandas/openpyxl. Only cell data is processed.
    logger.info("Note: Embedded images, charts, and shapes in Excel files are ignored during import.")
    
    try:
        if ext == '.csv':
            df, enc_conf, enc_warnings = _read_csv_smart(filepath)
            confidence['encoding_detection'] = enc_conf
            warnings.extend(enc_warnings)
        elif ext in ('.txt', '.tsv'):
            df, enc_conf, enc_warnings = _read_text_smart(filepath, ext)
            confidence['encoding_detection'] = enc_conf
            warnings.extend(enc_warnings)
        elif ext in ('.xlsx', '.xls'):
            df, sheet_info, sheet_warnings = _read_excel_smart(filepath, ext)
            metadata['sheet_name'] = sheet_info.get('selected_sheet')
            metadata['sheets_info'] = sheet_info
            warnings.extend(sheet_warnings)
        elif ext == '.json':
            df = _read_json_safe(filepath)
        else:
            df = pd.DataFrame()
            warnings.append(f"No reader for {ext}")
    except Exception as e:
        logger.error(f"File read error: {e}", exc_info=True)
        warnings.append(f"File read error: {str(e)}")
        return {
            'status': 'failed',
            'raw_dataframe': None,
            'metadata': metadata,
            'confidence': confidence,
            'warnings': warnings,
        }

    if df is None or df.empty:
        warnings.append("File is empty or contains no data rows")
        return {
            'status': 'failed',
            'raw_dataframe': None,
            'metadata': metadata,
            'confidence': confidence,
            'warnings': warnings,
        }

    # ═══════════════════════════════════════════════════════
    #  PHASE 1.6: Data Quality Pipeline (Correct Order)
    # ═══════════════════════════════════════════════════════
    
    # Step 1: Handle merged cells (forward-fill with limits)
    df = _handle_merged_cells(df)
    
    # Step 2: Fix Excel date corruption (all variants)
    df = _fix_excel_date_corruption(df)
    
    # Step 3: Normalize multi-line cell content
    df = _normalize_cell_content(df)
    
    # Step 4: Remove empty rows (must be before header detection)
    df = _remove_empty_rows(df)
    
    # Step 5: Header Detection (BEFORE structure analysis)
    # CRITICAL: Detect header BEFORE flattening merged cells
    header_idx, header_conf, header_warnings = _detect_header_row(df)
    confidence['header_detection'] = header_conf
    metadata['header_row_index'] = header_idx
    warnings.extend(header_warnings)

    # Apply detected header
    if header_idx >= 0:
        new_headers = df.iloc[header_idx].astype(str).tolist()
        df = df.iloc[header_idx + 1:].reset_index(drop=True)
        df.columns = new_headers
    else:
        # No header detected - use first row as header
        df.columns = df.iloc[0].astype(str).tolist()
        df = df.iloc[1:].reset_index(drop=True)

    # ── Step 1.4: Structure Analysis — flatten merged cells ──
    df = _flatten_structure(df)

    # ── Step 1.6: Data Extraction — clean up ──
    # Drop fully empty rows and columns
    df = df.dropna(how='all').reset_index(drop=True)
    df = df.dropna(axis=1, how='all')

    # Clean column names
    df.columns = [_clean_column_name(c) for c in df.columns]

    # Remove duplicate column names (keep first)
    seen = {}
    new_cols = []
    for c in df.columns:
        if c in seen:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            new_cols.append(c)
    df.columns = new_cols

    # Detect language hints
    metadata['language_hints'] = _detect_language_hints(df)

    metadata['total_rows'] = len(df)
    metadata['total_columns'] = len(df.columns)

    # Overall confidence
    confidence['overall'] = int(
        confidence['header_detection'] * 0.5 +
        confidence['encoding_detection'] * 0.3 +
        (90 if len(df) > 0 else 0) * 0.2
    )

    status = 'success' if confidence['overall'] >= 70 else 'partial'
    if len(warnings) > 3:
        status = 'partial'

    logger.info(
        f"Ingested {metadata['total_rows']} rows, {metadata['total_columns']} cols "
        f"(header row {header_idx}, confidence {confidence['overall']}%)"
    )

    return {
        'status': status,
        'raw_dataframe': df,
        'metadata': metadata,
        'confidence': confidence,
        'warnings': warnings,
    }


# ═══════════════════════════════════════════════════════
#  Step 1.1: File Type Detection
# ═══════════════════════════════════════════════════════

def _detect_file_type(filepath: str) -> str:
    """Detect file type from extension, with magic-byte fallback."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in SUPPORTED_EXTENSIONS:
        return ext

    # Fallback: try magic bytes
    try:
        with open(filepath, 'rb') as f:
            header = f.read(8)
        if header[:4] == b'PK\x03\x04':
            return '.xlsx'
        if header[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
            return '.xls'
        if header[:1] in (b'{', b'['):
            return '.json'
        # Assume text/CSV
        return '.csv'
    except Exception:
        return ext or '.csv'


# ═══════════════════════════════════════════════════════
#  Step 1.2: CSV/Text Reading with Encoding Detection
# ═══════════════════════════════════════════════════════

_ENCODINGS_TO_TRY = ['utf-8-sig', 'utf-8', 'cp1256', 'cp1252', 'latin-1', 'iso-8859-1']


def _read_csv_smart(filepath: str) -> tuple[pd.DataFrame, int, list[str]]:
    """
    Read CSV with multi-encoding fallback.
    Returns (DataFrame, encoding_confidence, warnings).
    """
    warnings = []
    file_size = os.path.getsize(filepath)

    # Try chardet first for best guess
    detected_enc = None
    try:
        import chardet
        with open(filepath, 'rb') as f:
            raw = f.read(min(file_size, 100_000))
        result = chardet.detect(raw)
        if result and result.get('confidence', 0) > 0.7:
            detected_enc = result['encoding']
    except ImportError:
        pass
    except Exception:
        pass

    # Build encoding list: detected first, then fallbacks
    encodings = []
    if detected_enc:
        encodings.append(detected_enc)
    encodings.extend(e for e in _ENCODINGS_TO_TRY if e.lower() != (detected_enc or '').lower())

    for enc in encodings:
        try:
            df = pd.read_csv(filepath, encoding=enc, dtype=str, keep_default_na=False,
                             on_bad_lines='warn')
            df = df.dropna(how='all')
            if not df.empty:
                conf = 100 if enc == (detected_enc or 'utf-8') else 80
                return df, conf, warnings
        except UnicodeDecodeError:
            continue
        except Exception as e:
            warnings.append(f"CSV parse error with {enc}: {str(e)[:100]}")
            continue

    # Last resort: read with errors='ignore'
    try:
        df = pd.read_csv(filepath, encoding='utf-8', dtype=str, keep_default_na=False,
                         on_bad_lines='skip', encoding_errors='ignore')
        df = df.dropna(how='all')
        warnings.append("File read with encoding errors ignored — some characters may be lost")
        return df, 40, warnings
    except Exception as e:
        warnings.append(f"All CSV read attempts failed: {str(e)[:200]}")
        return pd.DataFrame(), 0, warnings


def _read_text_smart(filepath: str, ext: str) -> tuple[pd.DataFrame, int, list[str]]:
    """Read TXT/TSV files with delimiter detection."""
    warnings = []
    sep = '\t' if ext == '.tsv' else None  # None = auto-detect

    # Try chardet
    detected_enc = 'utf-8'
    try:
        import chardet
        with open(filepath, 'rb') as f:
            raw = f.read(50_000)
        result = chardet.detect(raw)
        if result and result.get('confidence', 0) > 0.7:
            detected_enc = result['encoding']
    except (ImportError, Exception):
        pass

    for enc in [detected_enc] + _ENCODINGS_TO_TRY:
        try:
            df = pd.read_csv(filepath, encoding=enc, dtype=str, keep_default_na=False,
                             sep=sep, engine='python', on_bad_lines='warn')
            df = df.dropna(how='all')
            if not df.empty:
                return df, 80, warnings
        except Exception:
            continue

    warnings.append("Could not parse text file")
    return pd.DataFrame(), 0, warnings


# ═══════════════════════════════════════════════════════
#  Step 1.3: Excel Reading with Smart Sheet Selection
# ═══════════════════════════════════════════════════════

def _read_excel_smart(filepath: str, ext: str) -> tuple[pd.DataFrame, dict, list[str]]:
    """
    Read Excel with smart sheet selection.
    Returns (DataFrame, sheet_info, warnings).
    """
    warnings = []
    sheet_info = {'sheets': [], 'selected_sheet': None, 'selection_method': None}

    engine = 'openpyxl' if ext == '.xlsx' else 'xlrd'

    # Get sheet names
    try:
        xls = pd.ExcelFile(filepath, engine=engine)
        sheet_names = xls.sheet_names
    except Exception as e:
        # Fallback: try other engine
        try:
            alt_engine = 'xlrd' if engine == 'openpyxl' else 'openpyxl'
            xls = pd.ExcelFile(filepath, engine=alt_engine)
            sheet_names = xls.sheet_names
            engine = alt_engine
            warnings.append(f"Used fallback engine: {alt_engine}")
        except Exception as e2:
            warnings.append(f"Cannot read Excel file: {str(e2)[:200]}")
            return pd.DataFrame(), sheet_info, warnings

    sheet_info['sheets'] = sheet_names

    if not sheet_names:
        warnings.append("Excel file has no sheets")
        return pd.DataFrame(), sheet_info, warnings

    # Single sheet: use it
    if len(sheet_names) == 1:
        selected = sheet_names[0]
        sheet_info['selected_sheet'] = selected
        sheet_info['selection_method'] = 'single_sheet'
    else:
        # Multi-sheet: smart selection
        selected = _select_best_sheet(xls, sheet_names, engine)
        sheet_info['selected_sheet'] = selected['name']
        sheet_info['selection_method'] = selected['method']
        if len(sheet_names) > 1:
            warnings.append(
                f"Multiple sheets found ({len(sheet_names)}), "
                f"selected '{selected['name']}' via {selected['method']}"
            )

    # Read selected sheet (header=None to do our own header detection)
    try:
        df = pd.read_excel(
            filepath,
            sheet_name=sheet_info['selected_sheet'],
            dtype=str,
            keep_default_na=False,
            header=None,
            engine=engine,
        )
        df = df.dropna(how='all')
        # CRITICAL: Fix Excel date corruption BEFORE any other processing
        df = _fix_excel_date_corruption(df)
    except Exception as e:
        warnings.append(f"Error reading sheet '{sheet_info['selected_sheet']}': {str(e)[:200]}")
        return pd.DataFrame(), sheet_info, warnings

    return df, sheet_info, warnings


def _select_best_sheet(xls: pd.ExcelFile, sheet_names: list[str], engine: str) -> dict:
    """
    Select the best sheet from a multi-sheet Excel file.
    Strategy: count rows per sheet first, then prefer name-pattern match
    among non-empty sheets, then fall back to most-data sheet.
    Never selects an empty sheet if a non-empty one exists.
    """
    # Step 1: Count rows in each sheet (sample first 300 rows)
    sheet_row_counts = {}
    for name in sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=name, dtype=str, keep_default_na=False,
                               header=None, nrows=300, engine=engine)
            df = df.dropna(how='all')
            # Also drop columns that are all empty
            df = df.dropna(axis=1, how='all')
            sheet_row_counts[name] = len(df)
        except Exception:
            sheet_row_counts[name] = 0

    # Non-empty sheets only
    non_empty = [n for n in sheet_names if sheet_row_counts.get(n, 0) > 0]

    if not non_empty:
        return {'name': sheet_names[0], 'method': 'first_sheet'}

    # Step 2: Among non-empty sheets, try name pattern matching
    for pattern in INVENTORY_SHEET_PATTERNS:
        for name in non_empty:
            if pattern.search(name):
                return {'name': name, 'method': 'name_pattern'}

    # Step 3: Most data rows among non-empty sheets
    best_name = max(non_empty, key=lambda n: sheet_row_counts[n])
    return {'name': best_name, 'method': 'most_data'}


# ═══════════════════════════════════════════════════════
#  JSON Reading
# ═══════════════════════════════════════════════════════

def _read_json_safe(filepath: str) -> pd.DataFrame:
    """Read JSON with error handling."""
    try:
        df = pd.read_json(filepath, dtype=str)
        return df.dropna(how='all')
    except ValueError:
        # Try reading as line-delimited JSON
        try:
            df = pd.read_json(filepath, dtype=str, lines=True)
            return df.dropna(how='all')
        except Exception:
            pass
    except Exception:
        pass
    return pd.DataFrame()


# ═══════════════════════════════════════════════════════
#  Step 1.4: Structure Analysis
# ═══════════════════════════════════════════════════════

def _remove_empty_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove completely empty rows from DataFrame.
    
    Empty row definition:
    - All cells are null/NaN, OR
    - All cells contain only whitespace (spaces, tabs, newlines), OR
    - All cells are empty strings after strip()
    
    Returns cleaned DataFrame with removed rows logged.
    """
    if df.empty:
        return df
    
    # Function to check if a single value is "empty"
    def is_empty_value(val):
        # Use pandas.isna() with explicit check to avoid boolean ambiguity warning
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return True
        if isinstance(val, str):
            return val.strip() == ''
        return False
    
    # Check each row
    empty_mask = df.apply(lambda row: all(is_empty_value(val) for val in row), axis=1)
    
    empty_count = empty_mask.sum()
    total_count = len(df)
    
    if empty_count > 0:
        logger.info(f"Removed {empty_count} empty rows from {total_count} total rows")
        df = df[~empty_mask].reset_index(drop=True)
    
    return df


def _handle_merged_cells(df: pd.DataFrame) -> pd.DataFrame:
    """
    Handle merged cells by forward-filling with limits.
    
    Strategy: Forward-fill within reasonable limits (max 3 consecutive fills)
    to prevent over-filling.
    
    Also handles header rows with gaps.
    """
    if df.empty:
        return df
    
    original_nan_count = df.isna().sum().sum()
    
    # Forward-fill with limit of 3 consecutive fills
    df = df.ffill(limit=3)
    
    new_nan_count = df.isna().sum().sum()
    
    # Sanity check: warn if excessive NaN remains
    total_cells = len(df) * len(df.columns)
    if new_nan_count > total_cells * 0.5:
        logger.warning(
            f"File may have excessive merged cells: {new_nan_count}/{total_cells} cells are NaN"
        )
    
    if original_nan_count != new_nan_count:
        filled_count = original_nan_count - new_nan_count
        logger.info(f"Filled {filled_count} cells from merged cell regions")
    
    return df


def _fix_excel_date_corruption(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fix Excel's automatic date conversion of CAS numbers.
    
    Handles 5 patterns:
    1. Timestamp format: YYYY-MM-DD HH:MM:SS → YYYY-MM-D
    2. Date-only with leading zero: YYYY-MM-DD → YYYY-MM-D (strip leading zero from check digit)
    3. Date-only format without time
    4. Scientific notation: 7.78306E+06 → 778306
    5. Distinguish CAS from actual dates (2-7 digits vs 4-digit year)
    """
    import re
    logger.info(f"Checking for datetime corruption in {len(df.columns)} columns")
    
    for col in df.columns:
        # Log column info for debugging
        sample = df[col].iloc[0] if len(df) > 0 else None
        logger.debug(f"  {col}: dtype={df[col].dtype}, sample={sample}")
        
        # Case 1: Column is datetime type
        if df[col].dtype == 'datetime64[ns]':
            logger.info(f"Converting datetime column '{col}' back to CAS format")
            # Convert back: "2001-02-03 00:00:00" → "2001-02-3"
            df[col] = df[col].apply(
                lambda x: f"{x.year}-{x.month:02d}-{x.day}" if pd.notna(x) else x
            )
        
        # Case 2: Column is string but contains timestamp pattern
        elif df[col].dtype == 'object':
            # Pattern 1: Timestamp format
            timestamp_pattern = re.compile(r'(\d{4,7}-\d{2}-\d{1,2})\s+\d{2}:\d{2}:\d{2}')
            
            # Pattern 2: Date-only with leading zero (CAS format)
            # Distinguish from actual dates: CAS starts with 2-7 digits, dates start with 4
            cas_date_pattern = re.compile(r'(\d{2,7}-\d{2}-)0(\d)$')
            
            # Pattern 3: Scientific notation
            sci_not_pattern = re.compile(r'(\d+)\.\d+E[+-]\d+')
            
            def clean_corruption(val):
                # Check for None or NaN (avoid boolean ambiguity warning)
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return val
                if not isinstance(val, str):
                    return val
                
                original = val.strip()
                
                # Pattern 1: Timestamp
                match = timestamp_pattern.match(original)
                if match:
                    cleaned = match.group(1)
                    logger.debug(f"Cleaned timestamp: '{original}' → '{cleaned}'")
                    return cleaned
                
                # Pattern 2: CAS with leading zero on check digit
                # Only apply if it looks like CAS (2-7 digits at start, not 4-digit year)
                match = cas_date_pattern.match(original)
                if match:
                    # Check if first part is 2-7 digits (CAS) vs 4 digits (year)
                    first_part = match.group(1).split('-')[0]
                    if len(first_part) in [2, 3, 4, 5, 6, 7]:
                        cleaned = f"{match.group(1)}{match.group(2)}"
                        logger.debug(f"Fixed CAS leading zero: '{original}' → '{cleaned}'")
                        return cleaned
                
                # Pattern 3: Scientific notation
                match = sci_not_pattern.match(original)
                if match:
                    cleaned = match.group(1)
                    logger.debug(f"Fixed scientific notation: '{original}' → '{cleaned}'")
                    return cleaned
                
                return original
            
            # Check if any values match corruption patterns
            has_corruption = (
                df[col].astype(str).str.match(timestamp_pattern).any() or
                df[col].astype(str).str.match(cas_date_pattern).any() or
                df[col].astype(str).str.match(sci_not_pattern).any()
            )
            
            if has_corruption:
                logger.info(f"Cleaning Excel date corruption in column '{col}'")
                df[col] = df[col].apply(clean_corruption)
    
    return df


def _normalize_cell_content(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize multi-line cell content.
    
    Replaces newlines, tabs, and multiple spaces with single spaces.
    Handles Alt+Enter in Excel cells.
    """
    if df.empty:
        return df
    
    def normalize_cell(val):
        # Check for None or NaN (avoid boolean ambiguity warning)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return val
        if not isinstance(val, str):
            return val
        
        # Replace newlines with space
        val = re.sub(r'\r\n|\r|\n', ' ', val)
        # Replace tabs with space
        val = re.sub(r'\t', ' ', val)
        # Collapse multiple spaces
        val = re.sub(r' +', ' ', val)
        # Strip whitespace
        val = val.strip()
        # If empty string, convert to NaN
        if val == '':
            return pd.NA
        return val
    
    # Apply to all cells
    for col in df.columns:
        if df[col].dtype == 'object':
            df[col] = df[col].apply(normalize_cell)
    
    logger.info("Normalized multi-line cell content")
    return df


def _flatten_structure(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flatten merged cells and multi-row structures.
    Forward-fill NaN values that result from merged cells.
    """
    # Forward-fill to handle merged cells (common in Excel)
    # Only fill the first few rows (likely header area)
    if len(df) > 2:
        header_area = df.iloc[:5].copy()
        header_area = header_area.ffill(axis=1)
        df.iloc[:5] = header_area

    return df


# ═══════════════════════════════════════════════════════
#  Step 1.5: Header Detection (Rule-Based Scoring)
# ═══════════════════════════════════════════════════════

def _detect_header_row(df: pd.DataFrame) -> tuple[int, int, list[str]]:
    """
    Detect which row is the header using multi-signal scoring.

    Scans rows 0–30 and scores each based on:
      - keyword_score: how many cells match known header keywords
      - density_score: percentage of non-empty cells
      - type_break_score: does the data type pattern change after this row?
      - uniqueness_score: are values in this row unique (headers usually are)?

    Returns (header_row_index, confidence_percent, warnings).
    """
    warnings = []
    max_scan = min(len(df), 30)

    if max_scan == 0:
        return 0, 50, ["Empty dataframe, using row 0 as header"]

    scores = []
    for i in range(max_scan):
        row_values = df.iloc[i].astype(str).tolist()
        score = _score_header_candidate(row_values, df, i)
        scores.append((i, score))

    # Sort by score descending
    scores.sort(key=lambda x: x[1], reverse=True)
    best_idx, best_score = scores[0]

    # Confidence mapping
    if best_score >= 80:
        confidence = 95
    elif best_score >= 60:
        confidence = 85
    elif best_score >= 40:
        confidence = 70
    elif best_score >= 20:
        confidence = 55
        warnings.append(f"Low header detection confidence ({confidence}%), selected row {best_idx}")
    else:
        confidence = 40
        best_idx = 0  # Fallback to row 0
        warnings.append("Could not confidently detect header, using row 0")

    # If best is row 0 and it was already used as header by pandas, no change needed
    if best_idx > 0:
        warnings.append(f"Header detected at row {best_idx} (score: {best_score:.0f})")

    return best_idx, confidence, warnings


def _score_header_candidate(row_values: list[str], df: pd.DataFrame, row_idx: int) -> float:
    """
    Score a candidate header row (0–100).
    """
    total_cols = len(row_values)
    if total_cols == 0:
        return 0

    score = 0.0

    # ── 1. Keyword Score (0–40) ──
    keyword_hits = 0
    for val in row_values:
        val_clean = val.strip().lower()
        # Check exact match
        if val_clean in ALL_HEADER_KEYWORDS:
            keyword_hits += 1
            continue
        # Check partial match (keyword is substring of cell)
        for kw in ALL_HEADER_KEYWORDS:
            if len(kw) >= 3 and kw in val_clean:
                keyword_hits += 0.5
                break

    keyword_ratio = keyword_hits / total_cols if total_cols > 0 else 0
    score += keyword_ratio * 40

    # ── 2. Density Score (0–20) ──
    non_empty = sum(1 for v in row_values if v.strip() and v.strip().lower() not in ('nan', 'none', ''))
    density = non_empty / total_cols if total_cols > 0 else 0
    score += density * 20

    # ── 3. Type Consistency Score (0–20) ──
    # Headers are typically text; data rows below should have different patterns
    if row_idx < len(df) - 2:
        # Check if rows below this candidate have more numeric values
        data_rows = df.iloc[row_idx + 1: min(row_idx + 6, len(df))]
        numeric_ratio_below = 0
        for _, data_row in data_rows.iterrows():
            nums = sum(1 for v in data_row.astype(str) if _is_numeric_like(v))
            numeric_ratio_below += nums / max(total_cols, 1)
        numeric_ratio_below /= max(len(data_rows), 1)

        # Current row should be mostly text (not numeric)
        current_numeric = sum(1 for v in row_values if _is_numeric_like(v))
        current_numeric_ratio = current_numeric / total_cols

        # Good header: low numeric in header, higher numeric in data
        if current_numeric_ratio < 0.3 and numeric_ratio_below > 0.1:
            score += 20
        elif current_numeric_ratio < 0.5:
            score += 10

    # ── 4. Uniqueness Score (0–10) ──
    non_empty_vals = [v.strip().lower() for v in row_values if v.strip()]
    if non_empty_vals:
        unique_ratio = len(set(non_empty_vals)) / len(non_empty_vals)
        score += unique_ratio * 10

    # ── 5. String Length Score (0–10) ──
    # Headers tend to be short-to-medium length strings
    avg_len = sum(len(v.strip()) for v in row_values if v.strip()) / max(non_empty, 1)
    if 2 <= avg_len <= 30:
        score += 10
    elif avg_len <= 50:
        score += 5

    # ── 6. Position Bonus (0–10) ──
    # Most files place headers in top rows.
    if row_idx == 0:
        score += 10
    elif row_idx <= 2:
        score += 5

    return min(score, 100.0)


def _is_numeric_like(val: str) -> bool:
    """Check if a string looks like a number."""
    val = val.strip()
    if not val or val.lower() in ('nan', 'none', ''):
        return False
    # Remove common numeric decorations
    cleaned = val.replace(',', '').replace(' ', '').replace('%', '').replace('$', '')
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


# ═══════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════

def _map_columns(df: pd.DataFrame) -> dict:
    """
    Map user column names to standard names.
    
    Returns dict mapping standard_name -> actual_column_name.
    """
    mapped = {}
    df_cols_lower = {col.lower().strip(): col for col in df.columns}
    
    for std_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            # Check for exact match first
            if alias in df_cols_lower:
                mapped[std_name] = df_cols_lower[alias]
                break
            # Check if alias is a substring of any column name
            for col_lower, col_actual in df_cols_lower.items():
                if alias in col_lower:
                    mapped[std_name] = col_actual
                    break
            if std_name in mapped:
                break
    
    return mapped


def _clean_column_name(col: Any) -> str:
    """Clean a column name: strip, collapse whitespace, handle None/numeric."""
    if col is None:
        return 'unnamed'
    s = str(col).strip()
    if not s or s.lower() in ('nan', 'none', 'unnamed'):
        return 'unnamed'
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s)
    return s


def _detect_language_hints(df: pd.DataFrame) -> list[str]:
    """Detect language hints from column names and sample data."""
    hints = set()
    text = ' '.join(str(c) for c in df.columns)

    # Check first 10 rows of data too
    for _, row in df.head(10).iterrows():
        # Handle NA values to avoid boolean ambiguity error
        text += ' ' + ' '.join(
            str(v) if not (v is None or (isinstance(v, float) and pd.isna(v))) else ''
            for v in row
        )

    # Persian/Arabic characters
    if re.search(r'[\u0600-\u06FF]', text):
        hints.add('fa')
    # Latin characters
    if re.search(r'[a-zA-Z]', text):
        hints.add('en')
    # CJK characters
    if re.search(r'[\u4e00-\u9fff]', text):
        hints.add('zh')

    return sorted(hints) if hints else ['en']
