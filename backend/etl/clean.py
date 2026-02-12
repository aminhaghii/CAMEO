"""
clean.py — Layer 3: Smart Data Cleaning & Validation (ETL v4).

Responsibilities:
  - UTF-8 encoding fix, invisible char removal, NaN normalization
  - Persian/Arabic numeral conversion (۰-۹ → 0-9)
  - CAS regex scanning from ALL columns (Gold Standard)
  - CAS checksum validation
  - Unit normalization, quantity parsing
  - Date parsing (Gregorian + Jalali/Shamsi)
  - Supplier, batch, purity, price cleaning
  - Row-level quality scoring with weighted factors

Design principles:
  - Type-specific cleaning per semantic column type
  - Never crash on bad data — flag issues for review
  - Quality score reflects data completeness and validity
"""

import re
import logging
import unicodedata
from typing import Any

from etl.schema import normalize_unit
from etl.semantics import is_likely_product_code, is_plausible_cas

logger = logging.getLogger(__name__)

# ── Regex for CAS numbers (e.g. 67-64-1, 7664-93-9) ──
CAS_REGEX = re.compile(r'\b(\d{2,7}-\d{2}-\d)\b')

# ── Regex for CAS as pure digits (e.g. 7664939 → 7664-93-9) ──
CAS_DIGITS_REGEX = re.compile(r'\b(\d{5,10})\b')

# ── Values treated as None/NaN ──
NULL_STRINGS = {'nan', 'none', 'null', 'n/a', 'na', '-', '--', '—', '', 'undefined', 'nil',
                'نامشخص', 'ندارد', 'خالی', 'بدون'}

# ── Unicode subscript/superscript → ASCII digit mapping ──
_SUBSCRIPT_MAP = str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789')
_SUPERSCRIPT_MAP = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')

# ── Persian/Arabic numeral → ASCII digit mapping ──
_PERSIAN_DIGIT_MAP = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')

# ── Date patterns ──
_DATE_PATTERNS = [
    # ISO: 2024-01-15
    re.compile(r'^(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})$'),
    # US: 01/15/2024
    re.compile(r'^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$'),
    # Compact: 20240115
    re.compile(r'^(\d{4})(\d{2})(\d{2})$'),
    # Jalali: 1402/10/25
    re.compile(r'^(1[34]\d{2})[/\-\.](\d{1,2})[/\-\.](\d{1,2})$'),
]


# ═══════════════════════════════════════════════════════
#  Pre-processing: clean raw string values
# ═══════════════════════════════════════════════════════

def convert_persian_digits(s: str) -> str:
    """Convert Persian/Arabic numerals (۰-۹, ٠-٩) to ASCII digits (0-9)."""
    return s.translate(_PERSIAN_DIGIT_MAP)


def sanitize_string(value: Any) -> str | None:
    """
    Clean a single cell value:
    1. Convert to string
    2. Fix encoding issues (force UTF-8 safe)
    3. Remove invisible characters (ZWSP, NBSP, tabs, etc.)
    4. Convert Persian/Arabic numerals to ASCII
    5. Normalize NaN/None/null to Python None
    """
    if value is None:
        return None

    s = str(value)

    # Remove BOM and invisible Unicode chars
    s = s.replace('\ufeff', '')          # BOM
    s = s.replace('\xa0', ' ')           # Non-breaking space → regular space
    s = s.replace('\u200b', '')          # Zero-width space
    s = s.replace('\u200c', '')          # Zero-width non-joiner (keep for Farsi? remove for data)
    s = s.replace('\u200d', '')          # Zero-width joiner
    s = s.replace('\t', ' ')            # Tab → space

    # Normalize Unicode (NFC form — compose characters)
    s = unicodedata.normalize('NFC', s)

    # Convert Persian/Arabic numerals to ASCII
    s = convert_persian_digits(s)

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
        val_str = str(value)
        # Try standard CAS format first (XX-XX-X)
        matches = CAS_REGEX.findall(val_str)
        for candidate in matches:
            is_valid, result = validate_cas(candidate)
            if is_valid:
                logger.debug(f"CAS found in column '{key}': {result}")
                return result
        # Try digit-only CAS (e.g. 7664939)
        digit_matches = CAS_DIGITS_REGEX.findall(val_str)
        for digits in digit_matches:
            reconstructed = reconstruct_cas_from_digits(digits)
            if reconstructed:
                logger.debug(f"CAS reconstructed from digits in column '{key}': {digits} → {reconstructed}")
                return reconstructed

    return None


def reconstruct_cas_from_digits(digits: str) -> str | None:
    """
    Try to reconstruct a CAS number from a pure digit string.
    CAS format: XXXXXXX-YY-Z (last digit is check, second-to-last 2 digits are group).
    E.g. 7664939 → 7664-93-9

    STRICT: Rejects likely product codes (8+ digits with sequential patterns).
    """
    if not digits.isdigit() or len(digits) < 5 or len(digits) > 10:
        return None

    # Reject likely product codes BEFORE attempting reconstruction
    if is_likely_product_code(digits):
        logger.debug(f"Rejected CAS reconstruction: '{digits}' looks like a product code")
        return None

    check = digits[-1]
    group = digits[-3:-1]
    body = digits[:-3]
    if not body:
        return None
    candidate = f"{body}-{group}-{check}"

    # Use strict plausibility check (format + length + checksum)
    if not is_plausible_cas(candidate):
        return None

    is_valid, result = validate_cas(candidate)
    return result if is_valid else None


def normalize_formula(raw: str) -> str:
    """
    Normalize a chemical formula:
    - Convert Unicode subscripts/superscripts to ASCII digits (H₂SO₄ → H2SO4)
    - Fix OCR/typo: digit 0 after a letter → letter O (H202 → H2O2)
    - Strip whitespace
    """
    if not raw:
        return ''
    s = raw.translate(_SUBSCRIPT_MAP)
    s = s.translate(_SUPERSCRIPT_MAP)
    s = s.strip()
    # Fix digit-zero used instead of letter-O in formulas:
    # Pattern: letter followed by digits then 0 then digit → the 0 is likely O
    # E.g. H202 → H2O2, C2H60 → C2H6O, Na2C03 → Na2CO3
    s = re.sub(r'(?<=[A-Za-z])(\d*)0(\d)', lambda m: m.group(1) + 'O' + m.group(2), s)
    return s


def normalize_cas_for_comparison(cas: str) -> str:
    """Strip dashes and spaces for loose comparison."""
    return re.sub(r'[\s\-]', '', cas)


# ═══════════════════════════════════════════════════════
#  Quantity & Unit cleaning
# ═══════════════════════════════════════════════════════

def clean_quantity(raw_qty: str | None, raw_unit: str | None,
                   unit_column_exists: bool = True) -> dict[str, Any]:
    """
    Parse and normalize quantity + unit.
    Returns dict with normalized values and any issues.
    Only penalizes missing unit if the unit column actually exists in the file.
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
    elif unit_column_exists:
        issues.append("Missing unit")

    result['issues'] = issues
    return result


# ═══════════════════════════════════════════════════════
#  Row-level validation (main entry point)
# ═══════════════════════════════════════════════════════

def validate_row(row: dict, available_columns: set | None = None) -> dict:
    """
    Validate and clean a single inventory row (ETL v2).

    Input row keys (canonical): name, cas, quantity, unit, location, un_number, formula

    Args:
        row: dict of column_name → value
        available_columns: set of canonical column names that exist in the file.
            If provided, missing-field penalties are only applied for columns
            that actually exist. If None, all penalties apply (backward compat).

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

    # Determine which optional columns exist in the file
    _has_col = lambda c: (available_columns is None) or (c in available_columns)

    issues = []
    cleaned = {}
    score = 100  # Start perfect, deduct for issues

    # ── Step 1: CAS scan from ALL columns (Gold Standard) ──
    cas_scanned = scan_cas_from_all_columns(row)
    cleaned['cas_scanned'] = cas_scanned

    # ── Name: smart cleaning (extract parenthesized info, remove stopwords) ──
    name = (row.get('name') or '').strip()
    cleaned['name_raw'] = name  # Preserve original name for pre-match classification
    name_extra = _extract_name_extras(name)
    cleaned['name'] = name_extra['clean_name']
    # Store extracted purity/notes from parentheses
    if name_extra.get('purity_hint'):
        cleaned.setdefault('notes', '')
        cleaned['notes'] = name_extra['purity_hint']
    name = name_extra['clean_name']
    if not name:
        issues.append("Missing chemical name")
        score -= 20

    # ── CAS (explicit column) ──
    cas_raw = (row.get('cas') or '').strip() if row.get('cas') else ''
    cleaned['cas_raw'] = cas_raw
    if cas_raw:
        is_valid, cas_result = validate_cas(cas_raw)
        if not is_valid:
            # Try reconstructing from pure digits (e.g. 7664939 → 7664-93-9)
            reconstructed = reconstruct_cas_from_digits(cas_raw.replace('-', '').replace(' ', ''))
            if reconstructed:
                is_valid = True
                cas_result = reconstructed
                issues.append(f"CAS reconstructed from digits: {cas_raw} → {reconstructed}")
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
        row.get('unit') or '',
        unit_column_exists=_has_col('unit'),
    )
    cleaned['quantity'] = qty_result['quantity']
    cleaned['unit'] = qty_result['unit']
    cleaned['normalized_quantity'] = qty_result['normalized_quantity']
    issues.extend(qty_result['issues'])
    score -= len(qty_result['issues']) * 5

    # ── Location ──
    location = (row.get('location') or '').strip()
    cleaned['location'] = location
    if not location and _has_col('location'):
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
    formula_raw = (row.get('formula') or '').strip() if row.get('formula') else ''
    formula = normalize_formula(formula_raw) if formula_raw else None
    cleaned['formula'] = formula

    # ── Supplier (Layer 3 v4) ──
    supplier = _clean_supplier(row.get('supplier'))
    cleaned['supplier'] = supplier

    # ── Batch Number (Layer 3 v4) ──
    batch = _clean_batch_number(row.get('batch_number'))
    cleaned['batch_number'] = batch

    # ── Purity (Layer 3 v4) ──
    purity_result = _clean_purity(row.get('purity'))
    cleaned['purity'] = purity_result['value']
    cleaned['purity_unit'] = purity_result['unit']
    issues.extend(purity_result.get('issues', []))

    # ── Price (Layer 3 v4) ──
    price_result = _clean_price(row.get('price'))
    cleaned['price'] = price_result['value']
    cleaned['price_currency'] = price_result['currency']
    issues.extend(price_result.get('issues', []))

    # ── Date (Layer 3 v4) ──
    date_result = _clean_date(row.get('date'))
    cleaned['date'] = date_result['value']
    cleaned['date_type'] = date_result.get('type')
    issues.extend(date_result.get('issues', []))

    # ── Product Code (Layer 3 v4) ──
    cleaned['product_code'] = (row.get('product_code') or '').strip() or None

    # ── Quality Standard (Layer 3 v4) ──
    cleaned['quality_standard'] = (row.get('quality_standard') or '').strip() or None

    # ── Notes (Layer 3 v4) ──
    cleaned['notes'] = (row.get('notes') or '').strip() or None

    # ── Quality Score: weighted calculation ──
    # Core fields (name, CAS) are worth more than optional fields
    score = _calculate_quality_score(cleaned, issues, available_columns)

    # Clamp score
    score = max(0, min(100, score))

    return {
        'cleaned': cleaned,
        'issues': issues,
        'quality_score': score,
        'cas_scanned': cas_scanned,
    }


# ═══════════════════════════════════════════════════════
#  Smart Name Cleaning (ETL v4.1)
# ═══════════════════════════════════════════════════════

# Industrial stopwords to strip from chemical names before matching
_NAME_STOPWORDS = re.compile(
    r'\b(USP|BP|EP|JP|NF|ACS|AR|GR|LR|CP|FCC|Ph\.?\s*Eur|'
    r'Grade|Powder|Micronized|Fine|Granular|Anhydrous|'
    r'Reagent|Technical|Analytical|Extra\s*Pure|'
    r'Pharma|Pharmaceutical|Food\s*Grade|Industrial)\b',
    re.IGNORECASE
)

# Pattern to extract parenthesized content like (96%), (Usp grade), (Micronized)
_PAREN_PATTERN = re.compile(r'\(([^)]+)\)')


def _extract_name_extras(raw_name: str) -> dict:
    """
    Smart chemical name cleaning:
    1. Extract text inside parentheses into purity_hint / notes
    2. Remove industrial stopwords from the core name
    3. Return both the cleaned name (for matching) and original (for display)

    Returns:
        {
            'clean_name': str,      # cleaned name for matching
            'original_name': str,   # original as-is
            'purity_hint': str,     # extracted purity/grade info (e.g. "96%", "Usp grade")
        }
    """
    if not raw_name or not raw_name.strip():
        return {'clean_name': '', 'original_name': '', 'purity_hint': ''}

    original = raw_name.strip()
    # Replace newlines with spaces
    name = original.replace('\n', ' ').replace('\r', ' ')

    # Extract parenthesized content
    paren_parts = _PAREN_PATTERN.findall(name)
    purity_hint = '; '.join(p.strip() for p in paren_parts if p.strip()) if paren_parts else ''

    # Remove parenthesized content from name for matching
    clean = _PAREN_PATTERN.sub('', name)

    # Remove industrial stopwords
    clean = _NAME_STOPWORDS.sub('', clean)

    # Collapse whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()

    # If cleaning removed everything, fall back to original
    if not clean:
        clean = re.sub(r'\s+', ' ', name).strip()

    return {
        'clean_name': clean,
        'original_name': original,
        'purity_hint': purity_hint,
    }


# ═══════════════════════════════════════════════════════
#  Type-specific cleaners (Layer 3 v4)
# ═══════════════════════════════════════════════════════

def _clean_supplier(raw: str | None) -> str | None:
    """Clean and normalize supplier name."""
    if not raw:
        return None
    s = sanitize_string(raw)
    if not s:
        return None
    # Normalize common abbreviations (remove trailing dots)
    s = re.sub(r'\bCo\.(?:\s|$)', 'Co ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bLtd\.(?:\s|$)', 'Ltd ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bInc\.(?:\s|$)', 'Inc ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bCorp\.(?:\s|$)', 'Corp ', s, flags=re.IGNORECASE)
    # Collapse multiple spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _clean_batch_number(raw: str | None) -> str | None:
    """Clean batch/lot number."""
    if not raw:
        return None
    s = sanitize_string(raw)
    if not s:
        return None
    # Remove common prefixes
    s = re.sub(r'^(Batch|Lot|B/N|L/N|BN|LN)\s*[:#]?\s*', '', s, flags=re.IGNORECASE)
    return s.strip() or None


def _clean_purity(raw: str | None) -> dict:
    """Parse purity/concentration value."""
    result = {'value': None, 'unit': '%', 'issues': []}
    if not raw:
        return result
    s = sanitize_string(raw)
    if not s:
        return result

    # Extract numeric value and unit
    match = re.search(r'([\d.,]+)\s*(%|ppm|ppb|mg/[lL]|g/[lL])?', s)
    if match:
        try:
            val = float(match.group(1).replace(',', ''))
            unit = match.group(2) or '%'
            # Sanity check for percentage
            if unit == '%' and val > 100:
                result['issues'].append(f"Purity > 100%: {val}")
            result['value'] = val
            result['unit'] = unit
        except ValueError:
            result['issues'].append(f"Non-numeric purity: {s}")
    else:
        # Could be text like "ACS Grade" — store as-is
        result['value'] = None
        result['unit'] = None

    return result


def _clean_price(raw: str | None) -> dict:
    """Parse price/cost value with currency detection."""
    result = {'value': None, 'currency': None, 'issues': []}
    if not raw:
        return result
    s = sanitize_string(raw)
    if not s:
        return result

    # Detect currency
    currency = None
    if '$' in s or 'USD' in s.upper():
        currency = 'USD'
    elif '€' in s or 'EUR' in s.upper():
        currency = 'EUR'
    elif '£' in s or 'GBP' in s.upper():
        currency = 'GBP'
    elif '﷼' in s or 'ریال' in s or 'IRR' in s.upper():
        currency = 'IRR'
    elif 'تومان' in s:
        currency = 'IRR_TOMAN'

    # Extract numeric value
    cleaned_num = re.sub(r'[^\d.,]', '', s)
    if cleaned_num:
        try:
            # Handle comma as thousands separator
            cleaned_num = cleaned_num.replace(',', '')
            result['value'] = float(cleaned_num)
            result['currency'] = currency
        except ValueError:
            result['issues'].append(f"Non-numeric price: {s}")
    else:
        result['issues'].append(f"Could not parse price: {s}")

    return result


def _clean_date(raw: str | None) -> dict:
    """Parse date string (supports Gregorian and Jalali/Shamsi)."""
    result = {'value': None, 'type': None, 'issues': []}
    if not raw:
        return result
    s = sanitize_string(raw)
    if not s:
        return result

    # Try each date pattern
    for pattern in _DATE_PATTERNS:
        match = pattern.match(s)
        if match:
            groups = match.groups()
            year = int(groups[0])

            # Detect Jalali (years 1300-1499)
            if 1300 <= year <= 1499:
                result['value'] = s
                result['type'] = 'jalali'
                return result
            elif 1900 <= year <= 2100:
                result['value'] = s
                result['type'] = 'gregorian'
                return result
            elif len(groups[0]) <= 2:
                # Could be day-first format
                result['value'] = s
                result['type'] = 'gregorian'
                return result

    # If no pattern matched, store raw
    result['value'] = s
    result['type'] = 'unknown'
    result['issues'].append(f"Unrecognized date format: {s}")
    return result


def _calculate_quality_score(cleaned: dict, issues: list,
                             available_columns: set | None = None) -> int:
    """
    Calculate weighted quality score (0-100).
    Core fields are weighted more heavily than optional fields.
    Only includes optional fields in the denominator if they exist in the file.
    """
    _has = lambda c: (available_columns is None) or (c in available_columns)

    score = 0
    max_score = 0

    # ── Core fields (high weight) — always counted ──
    # Name: 30 points
    max_score += 30
    if cleaned.get('name'):
        score += 30

    # CAS: 25 points
    max_score += 25
    if cleaned.get('cas_valid'):
        score += 25
    elif cleaned.get('cas'):
        score += 10  # Has CAS but not validated

    # ── Important fields (medium weight) — only if column exists ──
    # Quantity
    if _has('quantity'):
        max_score += 15
        if cleaned.get('quantity') is not None:
            score += 15
        elif cleaned.get('normalized_quantity') is not None:
            score += 10

    # Unit
    if _has('unit'):
        max_score += 5
        if cleaned.get('unit') and cleaned['unit'] != 'unknown':
            score += 5

    # ── Optional fields (low weight) — only if column exists ──
    # Location
    if _has('location'):
        max_score += 5
        if cleaned.get('location'):
            score += 5

    # Supplier
    if _has('supplier'):
        max_score += 5
        if cleaned.get('supplier'):
            score += 5

    # Formula
    if _has('formula'):
        max_score += 5
        if cleaned.get('formula'):
            score += 5

    # Other optional fields: 5 points total
    optional_keys = [k for k in ['batch_number', 'purity', 'date', 'product_code'] if _has(k)]
    if optional_keys:
        max_score += 5
        optional_count = sum(1 for k in optional_keys if cleaned.get(k))
        score += min(optional_count * 2, 5)

    # ── Issue penalties ──
    critical_issues = sum(1 for i in issues if 'Invalid CAS' in i or 'Missing chemical name' in i)
    minor_issues = len(issues) - critical_issues
    score -= critical_issues * 10
    score -= minor_issues * 2

    # Normalize to 0-100
    if max_score > 0:
        normalized = int((score / max_score) * 100)
    else:
        normalized = 0

    return max(0, min(100, normalized))
