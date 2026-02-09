"""
clean.py — Advanced data cleaning & validation (ETL v2).
- UTF-8 encoding fix, invisible char removal, NaN normalization
- CAS regex scanning from ALL columns (Gold Standard)
- CAS checksum validation
- Unit normalization, row-level quality scoring
"""

import re
import logging
import unicodedata
from typing import Any

from etl.schema import normalize_unit

logger = logging.getLogger(__name__)

# ── Regex for CAS numbers (e.g. 67-64-1, 7664-93-9) ──
CAS_REGEX = re.compile(r'\b(\d{2,7}-\d{2}-\d)\b')

# ── Values treated as None/NaN ──
NULL_STRINGS = {'nan', 'none', 'null', 'n/a', 'na', '-', '--', '—', '', 'undefined', 'nil'}


# ═══════════════════════════════════════════════════════
#  Pre-processing: clean raw string values
# ═══════════════════════════════════════════════════════

def sanitize_string(value: Any) -> str | None:
    """
    Clean a single cell value:
    1. Convert to string
    2. Fix encoding issues (force UTF-8 safe)
    3. Remove invisible characters (ZWSP, NBSP, tabs, etc.)
    4. Normalize NaN/None/null to Python None
    """
    if value is None:
        return None

    s = str(value)

    # Remove BOM and invisible Unicode chars
    s = s.replace('\ufeff', '')          # BOM
    s = s.replace('\xa0', ' ')           # Non-breaking space → regular space
    s = s.replace('\u200b', '')          # Zero-width space
    s = s.replace('\u200c', '')          # Zero-width non-joiner
    s = s.replace('\u200d', '')          # Zero-width joiner
    s = s.replace('\t', ' ')            # Tab → space

    # Normalize Unicode (NFC form — compose characters)
    s = unicodedata.normalize('NFC', s)

    # Strip whitespace
    s = s.strip()

    # Check if it's a null-like value
    if s.lower() in NULL_STRINGS:
        return None

    return s


def sanitize_row(row: dict) -> dict:
    """Apply sanitize_string to every value in a row dict."""
    return {k: sanitize_string(v) for k, v in row.items()}


# ═══════════════════════════════════════════════════════
#  CAS Number utilities
# ═══════════════════════════════════════════════════════

def validate_cas(cas_string: str) -> tuple[bool, str]:
    """
    Validate a CAS Registry Number using the checksum algorithm.
    Format: XXXXXXX-YY-Z where Z is the check digit.

    Returns:
        (is_valid, cleaned_cas_or_error_message)
    """
    if not cas_string or not cas_string.strip():
        return False, "Empty CAS"

    cas = cas_string.strip().replace(' ', '')

    # Must match pattern: 2-7 digits, dash, 2 digits, dash, 1 digit
    if not re.match(r'^\d{2,7}-\d{2}-\d$', cas):
        return False, f"Invalid format: {cas_string}"

    digits_only = cas.replace('-', '')
    check_digit = int(digits_only[-1])
    body = digits_only[:-1]

    # Checksum: sum of (position * digit) from right to left, mod 10
    total = sum((i + 1) * int(d) for i, d in enumerate(reversed(body)))

    if total % 10 == check_digit:
        return True, cas
    else:
        return False, f"Checksum failed: {cas}"


def scan_cas_from_all_columns(row: dict) -> str | None:
    """
    Scan ALL columns in a row for CAS-like patterns using regex.
    Returns the first valid CAS found (checksum-verified), or None.
    This is the Gold Standard — CAS numbers are unique identifiers.
    """
    # Priority: check the 'cas' column first
    cas_col = row.get('cas')
    if cas_col:
        is_valid, result = validate_cas(cas_col)
        if is_valid:
            return result

    # Scan every other column for CAS patterns
    for key, value in row.items():
        if key == 'cas' or not value:
            continue
        matches = CAS_REGEX.findall(str(value))
        for candidate in matches:
            is_valid, result = validate_cas(candidate)
            if is_valid:
                logger.debug(f"CAS found in column '{key}': {result}")
                return result

    return None


def normalize_cas_for_comparison(cas: str) -> str:
    """Strip dashes and spaces for loose comparison."""
    return re.sub(r'[\s\-]', '', cas)


# ═══════════════════════════════════════════════════════
#  Quantity & Unit cleaning
# ═══════════════════════════════════════════════════════

def clean_quantity(raw_qty: str | None, raw_unit: str | None) -> dict[str, Any]:
    """
    Parse and normalize quantity + unit.
    Returns dict with normalized values and any issues.
    """
    issues = []
    result = {
        'raw_quantity': raw_qty,
        'raw_unit': raw_unit,
        'quantity': None,
        'unit': None,
        'normalized_quantity': None,
    }

    # Parse quantity
    if raw_qty:
        try:
            result['quantity'] = float(raw_qty.strip().replace(',', ''))
        except ValueError:
            issues.append(f"Non-numeric quantity: {raw_qty}")

    # Normalize unit
    if raw_unit:
        canonical_unit, multiplier = normalize_unit(raw_unit)
        result['unit'] = canonical_unit
        if canonical_unit == 'unknown':
            issues.append(f"Unknown unit: {raw_unit}")
        elif result['quantity'] is not None:
            result['normalized_quantity'] = result['quantity'] * multiplier
    else:
        issues.append("Missing unit")

    result['issues'] = issues
    return result


# ═══════════════════════════════════════════════════════
#  Row-level validation (main entry point)
# ═══════════════════════════════════════════════════════

def validate_row(row: dict) -> dict:
    """
    Validate and clean a single inventory row (ETL v2).

    Input row keys (canonical): name, cas, quantity, unit, location, un_number, formula

    Returns:
        {
            'cleaned': { ... normalized fields ... },
            'issues': [ ... list of problems ... ],
            'quality_score': 0-100,
            'cas_scanned': str or None   # CAS found via regex scan
        }
    """
    # ── Step 0: Sanitize all values ──
    row = sanitize_row(row)

    issues = []
    cleaned = {}
    score = 100  # Start perfect, deduct for issues

    # ── Step 1: CAS scan from ALL columns (Gold Standard) ──
    cas_scanned = scan_cas_from_all_columns(row)
    cleaned['cas_scanned'] = cas_scanned

    # ── Name ──
    name = (row.get('name') or '').strip()
    cleaned['name'] = name
    if not name:
        issues.append("Missing chemical name")
        score -= 20

    # ── CAS (explicit column) ──
    cas_raw = (row.get('cas') or '').strip() if row.get('cas') else ''
    cleaned['cas_raw'] = cas_raw
    if cas_raw:
        is_valid, cas_result = validate_cas(cas_raw)
        cleaned['cas'] = cas_result if is_valid else None
        cleaned['cas_valid'] = is_valid
        if not is_valid:
            issues.append(f"Invalid CAS: {cas_result}")
            score -= 15
    else:
        cleaned['cas'] = None
        cleaned['cas_valid'] = False
        score -= 5

    # If CAS column was invalid but scan found a valid CAS elsewhere, use it
    if not cleaned['cas_valid'] and cas_scanned:
        cleaned['cas'] = cas_scanned
        cleaned['cas_valid'] = True
        issues.append(f"CAS recovered from scan: {cas_scanned}")
        score = min(score + 10, 100)  # Partial recovery

    # ── Quantity & Unit ──
    qty_result = clean_quantity(
        row.get('quantity') or '',
        row.get('unit') or ''
    )
    cleaned['quantity'] = qty_result['quantity']
    cleaned['unit'] = qty_result['unit']
    cleaned['normalized_quantity'] = qty_result['normalized_quantity']
    issues.extend(qty_result['issues'])
    score -= len(qty_result['issues']) * 5

    # ── Location ──
    location = (row.get('location') or '').strip()
    cleaned['location'] = location
    if not location:
        issues.append("Missing location")
        score -= 5

    # ── UN Number ──
    un_raw = (row.get('un_number') or '').strip() if row.get('un_number') else ''
    if un_raw:
        un_clean = re.sub(r'^UN\s*', '', un_raw, flags=re.IGNORECASE).strip()
        if un_clean.isdigit():
            cleaned['un_number'] = int(un_clean)
        else:
            cleaned['un_number'] = None
            issues.append(f"Invalid UN number: {un_raw}")
            score -= 5
    else:
        cleaned['un_number'] = None

    # ── Formula ──
    formula = (row.get('formula') or '').strip() if row.get('formula') else ''
    cleaned['formula'] = formula if formula else None

    # Clamp score
    score = max(0, min(100, score))

    return {
        'cleaned': cleaned,
        'issues': issues,
        'quality_score': score,
        'cas_scanned': cas_scanned,
    }
