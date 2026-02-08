"""
clean.py — Data validation and cleaning.
CAS checksum validation, unit normalization, row-level quality checks.
"""

import re
import logging
from typing import Any

from etl.schema import normalize_unit

logger = logging.getLogger(__name__)


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


def clean_quantity(raw_qty: str, raw_unit: str) -> dict[str, Any]:
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


def validate_row(row: dict) -> dict:
    """
    Validate and clean a single inventory row.
    
    Input row keys (canonical): name, cas, quantity, unit, location, un_number, formula
    
    Returns:
        {
            'cleaned': { ... normalized fields ... },
            'issues': [ ... list of problems ... ],
            'quality_score': 0-100
        }
    """
    issues = []
    cleaned = {}
    score = 100  # Start perfect, deduct for issues

    # ── Name ──
    name = (row.get('name') or '').strip()
    cleaned['name'] = name
    if not name:
        issues.append("Missing chemical name")
        score -= 20

    # ── CAS ──
    cas_raw = (row.get('cas') or '').strip()
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
        # Missing CAS is not fatal, just reduces score
        score -= 5

    # ── Quantity & Unit ──
    qty_result = clean_quantity(
        row.get('quantity', ''),
        row.get('unit', '')
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
    un_raw = (row.get('un_number') or '').strip()
    if un_raw:
        # Strip "UN" prefix if present
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
    formula = (row.get('formula') or '').strip()
    cleaned['formula'] = formula if formula else None

    # Clamp score
    score = max(0, min(100, score))

    return {
        'cleaned': cleaned,
        'issues': issues,
        'quality_score': score,
    }
