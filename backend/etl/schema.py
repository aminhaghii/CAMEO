"""
schema.py — Canonical column mapping and unit normalization.
Maps messy CSV/Excel headers to standard field names.
"""

import re

# ── Canonical column names → list of known variations ──
COLUMN_MAP = {
    'name': [
        'name', 'chemical', 'chemical name', 'chem_name', 'chem name',
        'product', 'product name', 'material', 'substance', 'item',
    ],
    'cas': [
        'cas', 'cas_number', 'cas number', 'cas no', 'cas no.',
        'cas#', 'cas_id', 'casrn', 'cas registry', 'cas rn',
    ],
    'quantity': [
        'quantity', 'qty', 'amount', 'amt', 'count', 'volume', 'mass',
        'weight', 'number', 'num',
    ],
    'unit': [
        'unit', 'units', 'uom', 'unit of measure', 'measure',
        'unit_of_measure',
    ],
    'location': [
        'location', 'loc', 'storage', 'zone', 'area', 'building',
        'room', 'cabinet', 'shelf', 'warehouse', 'site',
    ],
    'un_number': [
        'un', 'un_number', 'un number', 'un no', 'un no.', 'un#',
        'unna', 'un/na', 'na number', 'dot number',
    ],
    'formula': [
        'formula', 'molecular formula', 'chem_formula', 'chemical formula',
        'mol formula',
    ],
}

# ── Build reverse lookup: variation → canonical name ──
_REVERSE_MAP = {}
for canonical, variations in COLUMN_MAP.items():
    for v in variations:
        _REVERSE_MAP[v.lower().strip()] = canonical


# ── Unit normalization table ──
# Maps raw unit string → (canonical_unit, multiplier_to_canonical)
UNIT_NORMALIZATION = {
    # Volume
    'gal':       ('gal', 1.0),
    'gallon':    ('gal', 1.0),
    'gallons':   ('gal', 1.0),
    'l':         ('L', 1.0),
    'liter':     ('L', 1.0),
    'liters':    ('L', 1.0),
    'litre':     ('L', 1.0),
    'litres':    ('L', 1.0),
    'ml':        ('L', 0.001),
    'milliliter': ('L', 0.001),
    'milliliters': ('L', 0.001),
    # Mass
    'kg':        ('kg', 1.0),
    'kilogram':  ('kg', 1.0),
    'kilograms': ('kg', 1.0),
    'g':         ('kg', 0.001),
    'gram':      ('kg', 0.001),
    'grams':     ('kg', 0.001),
    'lb':        ('lb', 1.0),
    'lbs':       ('lb', 1.0),
    'pound':     ('lb', 1.0),
    'pounds':    ('lb', 1.0),
    'oz':        ('lb', 0.0625),
    'ounce':     ('lb', 0.0625),
    'ounces':    ('lb', 0.0625),
    'ton':       ('kg', 907.185),
    'tons':      ('kg', 907.185),
    # Generic
    'container': ('container', 1.0),
    'containers': ('container', 1.0),
    'drum':      ('drum', 1.0),
    'drums':     ('drum', 1.0),
    'bottle':    ('bottle', 1.0),
    'bottles':   ('bottle', 1.0),
    'each':      ('each', 1.0),
    'ea':        ('each', 1.0),
}


def normalize_column_name(raw: str) -> str:
    """Map a raw column header to its canonical name, or return it cleaned."""
    cleaned = re.sub(r'[^\w\s]', '', raw).strip().lower()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return _REVERSE_MAP.get(cleaned, cleaned)


def normalize_columns(columns: list[str]) -> dict[str, str]:
    """
    Given a list of raw column headers, return a mapping: raw → canonical.
    Example: ['Chemical Name', 'CAS No.', 'Qty'] → {'Chemical Name': 'name', 'CAS No.': 'cas', 'Qty': 'quantity'}
    """
    mapping = {}
    for col in columns:
        mapping[col] = normalize_column_name(col)
    return mapping


def normalize_unit(raw_unit: str) -> tuple[str, float]:
    """
    Normalize a unit string.
    Returns (canonical_unit, multiplier) or ('unknown', 1.0) if not recognized.
    """
    if not raw_unit:
        return ('unknown', 1.0)
    key = raw_unit.strip().lower()
    return UNIT_NORMALIZATION.get(key, ('unknown', 1.0))
