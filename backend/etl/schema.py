"""
schema.py — Layer 2: Intelligent Column Mapping Engine (ETL v4).

Responsibilities:
  - Detect semantic type of each column using multi-strategy voting
  - Strategy 1: Definitive rules (CAS regex, date parsing, currency)
  - Strategy 2: Keyword matching (multi-language: EN, FA, FR, ES)
  - Strategy 3: Content analysis (text/numeric ratio, uniqueness, patterns)
  - Strategy 4: Voting & decision logic with confidence scores
  - Unit normalization

Semantic Types:
  chemical_name, cas_number, product_code, supplier, batch_number,
  quantity, unit, price, date, quality_standard, purity, storage_location,
  formula, un_number, notes, row_number, unknown
"""

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  Semantic type definitions
# ═══════════════════════════════════════════════════════

SEMANTIC_TYPES = [
    'name', 'cas', 'product_code', 'supplier', 'batch_number',
    'quantity', 'unit', 'price', 'date', 'quality_standard',
    'purity', 'location', 'formula', 'un_number', 'notes',
    'row_number', 'unknown',
]

# ═══════════════════════════════════════════════════════
#  Strategy 1: Keyword matching (multi-language)
# ═══════════════════════════════════════════════════════

COLUMN_KEYWORDS = {
    'name': {
        'en': ['name', 'chemical', 'chemical name', 'chem name', 'product',
               'product name', 'material', 'substance', 'item', 'material name',
               'raw material', 'description of goods', 'commodity'],
        'fa': ['نام ماده', 'نام کالا', 'نام', 'ماده شیمیایی', 'نام محصول',
               'شرح کالا', 'نام ماده اولیه', 'ماده'],
        'fr': ['nom', 'matière', 'produit', 'substance'],
        'es': ['nombre', 'material', 'producto', 'sustancia'],
    },
    'cas': {
        'en': ['cas', 'cas number', 'cas no', 'cas no.', 'cas#', 'cas_id',
               'casrn', 'cas registry', 'cas rn', 'cas_number'],
        'fa': ['کس نامبر', 'شماره کس', 'شماره ثبت'],
    },
    'product_code': {
        'en': ['code', 'product code', 'item code', 'sku', 'part number',
               'part no', 'catalog', 'catalogue', 'ref', 'reference',
               'item no', 'item number', 'material code', 'article'],
        'fa': ['کد', 'کد کالا', 'کد محصول', 'شماره فنی', 'کد ماده'],
    },
    'supplier': {
        'en': ['supplier', 'vendor', 'source', 'manufacturer', 'company',
               'producer', 'brand', 'maker', 'distributor', 'origin'],
        'fa': ['تامین کننده', 'تامین', 'عرضه کننده', 'شرکت', 'منبع',
               'تولید کننده', 'سازنده', 'برند'],
    },
    'batch_number': {
        'en': ['batch', 'batch number', 'batch no', 'lot', 'lot number',
               'lot no', 'lot#', 'batch#', 'serial'],
        'fa': ['بچ', 'شماره بچ', 'شماره سری', 'لات'],
    },
    'quantity': {
        'en': ['quantity', 'qty', 'amount', 'amt', 'count', 'volume', 'mass',
               'weight', 'number', 'stock', 'balance', 'on hand'],
        'fa': ['مقدار', 'تعداد', 'حجم', 'وزن', 'موجودی'],
    },
    'unit': {
        'en': ['unit', 'units', 'uom', 'unit of measure', 'measure',
               'unit of measurement'],
        'fa': ['واحد', 'واحد اندازه گیری', 'واحد سنجش'],
    },
    'price': {
        'en': ['price', 'cost', 'value', 'unit price', 'total price',
               'amount', 'fee', 'rate'],
        'fa': ['قیمت', 'بها', 'ارزش', 'مبلغ', 'فی'],
    },
    'date': {
        'en': ['date', 'expiry', 'expiry date', 'exp date', 'manufacture date',
               'mfg date', 'received', 'received date', 'delivery date',
               'production date', 'best before'],
        'fa': ['تاریخ', 'تاریخ انقضا', 'تاریخ تولید', 'تاریخ دریافت',
               'تاریخ ورود'],
    },
    'quality_standard': {
        'en': ['grade', 'standard', 'quality', 'spec', 'specification',
               'purity grade', 'reagent grade'],
        'fa': ['استاندارد', 'درجه', 'کیفیت', 'گرید'],
    },
    'purity': {
        'en': ['purity', 'assay', 'concentration', 'conc', 'strength', '%'],
        'fa': ['خلوص', 'غلظت', 'درصد خلوص'],
    },
    'location': {
        'en': ['location', 'loc', 'storage', 'zone', 'area', 'building',
               'room', 'cabinet', 'shelf', 'warehouse', 'site', 'bin'],
        'fa': ['محل', 'انبار', 'مکان', 'قفسه', 'سایت'],
    },
    'formula': {
        'en': ['formula', 'molecular formula', 'chemical formula',
               'mol formula', 'chem formula'],
        'fa': ['فرمول', 'فرمول شیمیایی', 'فرمول مولکولی'],
    },
    'un_number': {
        'en': ['un', 'un number', 'un no', 'un no.', 'un#', 'unna',
               'un/na', 'na number', 'dot number', 'hazmat'],
        'fa': ['شماره un', 'یو ان'],
    },
    'notes': {
        'en': ['notes', 'note', 'remark', 'remarks', 'comment', 'comments',
               'description', 'memo', 'observation', 'info'],
        'fa': ['توضیحات', 'یادداشت', 'ملاحظات'],
    },
    'row_number': {
        'en': ['no', 'no.', 'row', 'sr', 'sr.', 'sn', 's.n.', '#', 'id',
               'serial', 'index', 'seq', 'line'],
        'fa': ['ردیف', 'شماره', 'ش'],
    },
}

# Build flat reverse lookup for quick matching
_KEYWORD_REVERSE = {}
for stype, lang_dict in COLUMN_KEYWORDS.items():
    for lang, keywords in lang_dict.items():
        for kw in keywords:
            _KEYWORD_REVERSE[kw.lower().strip()] = stype


# ═══════════════════════════════════════════════════════
#  Strategy 2: Definitive rules (regex-based)
# ═══════════════════════════════════════════════════════

CAS_REGEX = re.compile(r'^\d{2,7}-\d{2}-\d$')
# Date regex: ONLY match values with separators (/, -, .)
# Do NOT match pure digit strings like YYYYMMDD — those are too ambiguous
# and frequently collide with product codes (e.g. 1121120011)
DATE_REGEX = re.compile(
    r'^(\d{1,4}[/\-\.]\d{1,2}[/\-\.]\d{1,4})$'
)
CURRENCY_REGEX = re.compile(r'[\$€£¥﷼]|ریال|تومان|IRR|USD|EUR')
FORMULA_REGEX = re.compile(r'^[A-Z][a-z]?\d*([A-Z][a-z]?\d*)*$')
UN_NUMBER_REGEX = re.compile(r'^(UN\s*)?\d{4}$', re.IGNORECASE)
PURITY_REGEX = re.compile(r'\d+(\.\d+)?\s*%')


def _definitive_check_column(col_name: str, sample_values: list[str]) -> tuple[str | None, int]:
    """
    Apply definitive rules to classify a column.
    Returns (semantic_type, confidence) or (None, 0) if no definitive match.
    """
    non_empty = [v for v in sample_values if v and v.strip()]
    if not non_empty:
        return None, 0

    total = len(non_empty)

    # CAS Number: >60% of values match CAS pattern
    cas_matches = sum(1 for v in non_empty if CAS_REGEX.match(v.strip()))
    if cas_matches / total > 0.6:
        return 'cas', 100

    # Date: >60% of values look like dates (must have separators like / - .)
    # Extra guard: if most values are pure long integers, it's a product code, not a date
    pure_long_ints = sum(1 for v in non_empty if v.strip().isdigit() and len(v.strip()) > 6)
    if pure_long_ints / total < 0.3:
        date_matches = sum(1 for v in non_empty if DATE_REGEX.match(v.strip()))
        if date_matches / total > 0.6:
            return 'date', 95

    # Price: currency symbols present
    currency_matches = sum(1 for v in non_empty if CURRENCY_REGEX.search(v))
    if currency_matches / total > 0.3:
        return 'price', 90

    # UN Number: 4-digit codes with optional UN prefix
    un_matches = sum(1 for v in non_empty if UN_NUMBER_REGEX.match(v.strip()))
    if un_matches / total > 0.5:
        return 'un_number', 90

    # Formula: chemical formula pattern
    formula_matches = sum(1 for v in non_empty if FORMULA_REGEX.match(v.strip()))
    if formula_matches / total > 0.5:
        return 'formula', 85

    # Purity: percentage values
    purity_matches = sum(1 for v in non_empty if PURITY_REGEX.search(v))
    if purity_matches / total > 0.5:
        return 'purity', 85

    # Row number: sequential integers starting from 1
    try:
        nums = [int(v.strip()) for v in non_empty[:20] if v.strip().isdigit()]
        if len(nums) >= 5:
            diffs = [nums[i+1] - nums[i] for i in range(len(nums)-1)]
            if all(d == 1 for d in diffs) and nums[0] in (1, 0):
                return 'row_number', 95
    except (ValueError, IndexError):
        pass

    return None, 0


# ═══════════════════════════════════════════════════════
#  Strategy 3: Content analysis
# ═══════════════════════════════════════════════════════

def _analyze_content(sample_values: list[str]) -> dict:
    """
    Analyze column content to infer semantic type.
    Returns a feature dict used for scoring.
    """
    non_empty = [v for v in sample_values if v and v.strip()]
    total = len(non_empty) if non_empty else 1

    # Text vs numeric ratio
    numeric_count = 0
    text_count = 0
    for v in non_empty:
        v = v.strip().replace(',', '').replace(' ', '')
        try:
            float(v)
            numeric_count += 1
        except ValueError:
            text_count += 1

    # Average length
    avg_len = sum(len(v.strip()) for v in non_empty) / total if non_empty else 0

    # Unique ratio
    unique_vals = set(v.strip().lower() for v in non_empty)
    unique_ratio = len(unique_vals) / total if total > 0 else 0

    # Contains Persian/Arabic
    has_persian = any(re.search(r'[\u0600-\u06FF]', v) for v in non_empty)

    return {
        'text_ratio': text_count / total,
        'numeric_ratio': numeric_count / total,
        'avg_length': avg_len,
        'unique_ratio': unique_ratio,
        'has_persian': has_persian,
        'total_non_empty': len(non_empty),
    }


def _content_score(features: dict) -> dict[str, float]:
    """
    Score each semantic type based on content features.
    Returns {semantic_type: score (0-100)}.
    """
    scores = {}

    # chemical_name: mostly text, medium-long, high uniqueness
    if features['text_ratio'] > 0.7 and features['avg_length'] > 5 and features['unique_ratio'] > 0.3:
        scores['name'] = 60 + features['unique_ratio'] * 20
    else:
        scores['name'] = features['text_ratio'] * 30

    # supplier: mostly text, low uniqueness (few suppliers repeated)
    if features['text_ratio'] > 0.8 and features['unique_ratio'] < 0.4:
        scores['supplier'] = 70
    elif features['text_ratio'] > 0.6 and features['unique_ratio'] < 0.5:
        scores['supplier'] = 50
    else:
        scores['supplier'] = 10

    # quantity: mostly numeric
    if features['numeric_ratio'] > 0.7:
        scores['quantity'] = 70 + features['numeric_ratio'] * 20
    else:
        scores['quantity'] = features['numeric_ratio'] * 30

    # unit: mostly text, very short, very low uniqueness
    if features['text_ratio'] > 0.7 and features['avg_length'] < 8 and features['unique_ratio'] < 0.2:
        scores['unit'] = 70
    else:
        scores['unit'] = 10

    # price: mostly numeric
    scores['price'] = features['numeric_ratio'] * 40

    # date: mixed (dates are text but structured)
    scores['date'] = 20  # Low default, definitive rules handle this

    # notes: long text, high uniqueness
    if features['text_ratio'] > 0.8 and features['avg_length'] > 20:
        scores['notes'] = 60
    else:
        scores['notes'] = 10

    # row_number: pure numeric, sequential, low uniqueness
    if features['numeric_ratio'] > 0.9 and features['avg_length'] < 5:
        scores['row_number'] = 50
    else:
        scores['row_number'] = 5

    # product_code: mixed, medium uniqueness
    if features['unique_ratio'] > 0.5 and features['avg_length'] < 15:
        scores['product_code'] = 40
    else:
        scores['product_code'] = 10

    # batch_number: mixed, high uniqueness, medium length
    if features['unique_ratio'] > 0.7 and 3 < features['avg_length'] < 20:
        scores['batch_number'] = 40
    else:
        scores['batch_number'] = 10

    # location: text, low uniqueness
    if features['text_ratio'] > 0.6 and features['unique_ratio'] < 0.3:
        scores['location'] = 50
    else:
        scores['location'] = 10

    return scores


# ═══════════════════════════════════════════════════════
#  Main Column Mapping Engine
# ═══════════════════════════════════════════════════════

def map_columns(df) -> dict:
    """
    Layer 2: Intelligent Column Mapping.

    Analyzes each column using multi-strategy voting:
      1. Definitive rules (regex patterns)
      2. Keyword matching (header name)
      3. Content analysis (sample values)

    Returns:
        {
            'column_mapping': {
                'Original Col Name': {
                    'semantic_type': 'name',
                    'confidence': 95,
                    'method': 'keyword_match',
                },
                ...
            },
            'canonical_rename': { 'Original Col Name': 'name', ... },
            'critical_fields_found': ['name', 'cas', ...],
            'missing_fields': ['cas', ...],
            'review_required': ['Ambiguous Col', ...],
            'warnings': [...],
        }
    """
    warnings = []
    column_mapping = {}
    assigned_types = {}  # semantic_type → (col_name, confidence)

    columns = list(df.columns)
    sample_size = min(100, len(df))

    for col in columns:
        sample_values = df[col].astype(str).head(sample_size).tolist()
        col_clean = re.sub(r'[^\w\s]', '', str(col)).strip().lower()
        col_clean = re.sub(r'\s+', ' ', col_clean)

        # ── Strategy 1: Definitive rules ──
        def_type, def_conf = _definitive_check_column(col, sample_values)
        if def_type and def_conf >= 90:
            column_mapping[col] = {
                'semantic_type': def_type,
                'confidence': def_conf,
                'method': 'definitive_rule',
            }
            _assign_type(assigned_types, def_type, col, def_conf)
            continue

        # ── Strategy 2: Keyword matching ──
        kw_type = _KEYWORD_REVERSE.get(col_clean)
        kw_conf = 0
        if kw_type:
            kw_conf = 90
        else:
            # Partial keyword match
            kw_type, kw_conf = _partial_keyword_match(col_clean)

        # ── Strategy 3: Content analysis ──
        features = _analyze_content(sample_values)
        content_scores = _content_score(features)

        # ── Voting: combine strategies ──
        final_scores = {}
        for stype in SEMANTIC_TYPES:
            score = 0.0
            # Keyword weight: 0.5
            if kw_type == stype:
                score += kw_conf * 0.5
            # Content weight: 0.35
            score += content_scores.get(stype, 0) * 0.35
            # Definitive weight: 0.15 (if partial match)
            if def_type == stype:
                score += def_conf * 0.15
            final_scores[stype] = score

        # Pick best
        best_type = max(final_scores, key=final_scores.get)
        best_score = final_scores[best_type]

        # Determine confidence and method
        if best_score >= 70:
            confidence = min(int(best_score), 100)
            method = 'keyword_match' if kw_type == best_type else 'content_analysis'
        elif best_score >= 40:
            confidence = int(best_score)
            method = 'low_confidence'
            warnings.append(f"Column '{col}' mapped to '{best_type}' with low confidence ({confidence}%)")
        else:
            best_type = 'unknown'
            confidence = int(best_score)
            method = 'unresolved'

        column_mapping[col] = {
            'semantic_type': best_type,
            'confidence': confidence,
            'method': method,
        }
        if best_type != 'unknown':
            _assign_type(assigned_types, best_type, col, confidence)

    # ── Resolve conflicts: multiple columns mapped to same type ──
    _resolve_conflicts(column_mapping, assigned_types, df, warnings)

    # ── Build canonical rename map ──
    canonical_rename = {}
    for col, info in column_mapping.items():
        canonical_rename[col] = info['semantic_type']

    # ── Check critical fields ──
    found_types = set(info['semantic_type'] for info in column_mapping.values())
    critical = ['name']
    important = ['cas', 'quantity', 'supplier']

    critical_found = [t for t in critical if t in found_types]
    missing = [t for t in critical + important if t not in found_types]
    review_cols = [col for col, info in column_mapping.items()
                   if info['confidence'] < 60 or info['semantic_type'] == 'unknown']

    if 'name' not in found_types:
        warnings.append("CRITICAL: No chemical name column detected!")

    return {
        'column_mapping': column_mapping,
        'canonical_rename': canonical_rename,
        'critical_fields_found': critical_found,
        'missing_fields': missing,
        'review_required': review_cols,
        'warnings': warnings,
    }


def _assign_type(assigned: dict, stype: str, col: str, conf: int):
    """Track which column is assigned to which type (for conflict resolution)."""
    existing = assigned.get(stype)
    if not existing or conf > existing[1]:
        assigned[stype] = (col, conf)


def _partial_keyword_match(col_clean: str) -> tuple[str | None, int]:
    """Try partial/substring keyword matching."""
    best_type = None
    best_score = 0

    for stype, lang_dict in COLUMN_KEYWORDS.items():
        for lang, keywords in lang_dict.items():
            for kw in keywords:
                kw_lower = kw.lower()
                if len(kw_lower) >= 3 and kw_lower in col_clean:
                    score = 75  # Partial match
                    if score > best_score:
                        best_score = score
                        best_type = stype
                elif len(col_clean) >= 3 and col_clean in kw_lower:
                    score = 65
                    if score > best_score:
                        best_score = score
                        best_type = stype

    return best_type, best_score


def _resolve_conflicts(column_mapping: dict, assigned: dict, df, warnings: list):
    """
    Resolve cases where multiple columns map to the same semantic type.
    Keep the one with highest confidence; downgrade others.
    """
    type_to_cols = {}
    for col, info in column_mapping.items():
        stype = info['semantic_type']
        if stype == 'unknown':
            continue
        type_to_cols.setdefault(stype, []).append((col, info['confidence']))

    for stype, cols in type_to_cols.items():
        if len(cols) <= 1:
            continue

        # Sort by confidence descending
        cols.sort(key=lambda x: x[1], reverse=True)
        winner = cols[0][0]

        for col, conf in cols[1:]:
            # Downgrade to 'unknown' or a secondary type
            warnings.append(
                f"Conflict: '{col}' also maps to '{stype}' "
                f"(kept '{winner}', downgraded '{col}')"
            )
            column_mapping[col]['semantic_type'] = 'unknown'
            column_mapping[col]['confidence'] = max(conf - 30, 0)
            column_mapping[col]['method'] = 'conflict_downgraded'


# ═══════════════════════════════════════════════════════
#  Backward-compatible functions
# ═══════════════════════════════════════════════════════

# ── Canonical column names → list of known variations (legacy) ──
COLUMN_MAP = {}
for stype, lang_dict in COLUMN_KEYWORDS.items():
    all_kws = []
    for lang, keywords in lang_dict.items():
        all_kws.extend(keywords)
    COLUMN_MAP[stype] = all_kws

# ── Build reverse lookup: variation → canonical name ──
_REVERSE_MAP = {}
for canonical, variations in COLUMN_MAP.items():
    for v in variations:
        _REVERSE_MAP[v.lower().strip()] = canonical


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


# ═══════════════════════════════════════════════════════
#  Unit normalization
# ═══════════════════════════════════════════════════════

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
    'tonne':     ('kg', 1000.0),
    'tonnes':    ('kg', 1000.0),
    # Generic
    'container': ('container', 1.0),
    'containers': ('container', 1.0),
    'drum':      ('drum', 1.0),
    'drums':     ('drum', 1.0),
    'bottle':    ('bottle', 1.0),
    'bottles':   ('bottle', 1.0),
    'each':      ('each', 1.0),
    'ea':        ('each', 1.0),
    'pcs':       ('each', 1.0),
    'piece':     ('each', 1.0),
    'pieces':    ('each', 1.0),
    'bag':       ('bag', 1.0),
    'bags':      ('bag', 1.0),
    'box':       ('box', 1.0),
    'boxes':     ('box', 1.0),
    'pail':      ('pail', 1.0),
    'pails':     ('pail', 1.0),
    'cylinder':  ('cylinder', 1.0),
    'cylinders': ('cylinder', 1.0),
    # Persian/Farsi units
    'لیتر':      ('L', 1.0),
    'کیلو':      ('kg', 1.0),
    'کیلوگرم':   ('kg', 1.0),
    'گرم':       ('kg', 0.001),
    'تن':        ('kg', 1000.0),
    'سیلندر':    ('cylinder', 1.0),
    'بشکه':      ('drum', 1.0),
    'عدد':       ('each', 1.0),
    'بسته':      ('bag', 1.0),
    'جعبه':      ('box', 1.0),
    'سطل':       ('pail', 1.0),
}


def normalize_unit(raw_unit: str) -> tuple[str, float]:
    """
    Normalize a unit string.
    Returns (canonical_unit, multiplier) or ('unknown', 1.0) if not recognized.
    """
    if not raw_unit:
        return ('unknown', 1.0)
    key = raw_unit.strip().lower()
    return UNIT_NORMALIZATION.get(key, ('unknown', 1.0))
