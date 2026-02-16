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

import os
import re
import logging
import sqlite3
from functools import lru_cache
from typing import Any

import pandas as pd

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

# Feature flag for Phase 1.7 rollback safety
ENABLE_DEEP_CONTENT_ANALYSIS = os.getenv('ENABLE_DEEP_CONTENT_ANALYSIS', 'true').strip().lower() in {
    '1', 'true', 'yes', 'on'
}

# Deep-content analysis helpers
_CHEMICAL_KEYWORDS = {
    'acid', 'oxide', 'chloride', 'hydroxide', 'carbonate', 'sulfate', 'sulphate',
    'nitrate', 'nitrite', 'peroxide', 'amine', 'ketone', 'aldehyde', 'benzene',
    'ethanol', 'methanol', 'acetone', 'ammonia', 'sodium', 'potassium', 'calcium',
    'magnesium', 'chromium', 'sulfur', 'sulphur', 'nitrogen',
}

_CHEMICAL_SUFFIXES = (
    'ane', 'ene', 'yne', 'ol', 'ate', 'ide', 'ine', 'one',
    'ic acid', 'ous acid',
)

_CHEMICAL_PREFIXES = (
    'di', 'tri', 'tetra', 'poly', 'iso', 'methyl', 'ethyl', 'propyl', 'butyl',
)

_ELEMENT_SYMBOLS = {
    'H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne',
    'Na', 'Mg', 'Al', 'Si', 'P', 'S', 'Cl', 'Ar', 'K', 'Ca',
    'Sc', 'Ti', 'V', 'Cr', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn',
    'Ga', 'Ge', 'As', 'Se', 'Br', 'Kr', 'Rb', 'Sr', 'Y', 'Zr',
    'Nb', 'Mo', 'Ru', 'Rh', 'Pd', 'Ag', 'Cd', 'In', 'Sn', 'Sb',
    'Te', 'I', 'Xe', 'Cs', 'Ba', 'La', 'Ce', 'Pr', 'Nd', 'Sm',
    'Eu', 'Gd', 'Tb', 'Dy', 'Ho', 'Er', 'Tm', 'Yb', 'Lu', 'Hf',
    'Ta', 'W', 'Re', 'Os', 'Ir', 'Pt', 'Au', 'Hg', 'Tl', 'Pb',
    'Bi', 'Th', 'U',
}

_SUPPLIER_TOKENS = (
    'corp', 'inc', 'ltd', 'co', 'llc', 'gmbh', 'industries', 'industrial',
    'chemical', 'chem', 'pharma', 'laboratory', 'lab', 'supply', 'trading',
)

_LOCATION_TOKENS = (
    'storage', 'room', 'area', 'cabinet', 'warehouse', 'zone', 'bin',
    'shelf', 'building', 'tank', 'bay', 'site',
)

_DATE_VALUE_REGEXES = [
    re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$'),
    re.compile(r'^\d{1,2}/\d{1,2}/\d{2,4}$'),
    re.compile(r'^\d{1,2}-[A-Za-z]{3}-\d{2,4}$'),
]

_QUANTITY_WITH_UNIT_REGEX = re.compile(r'^\s*~?\s*[\d,]+(?:\.\d+)?\s*[a-zA-Z]+\s*$')
_QUANTITY_RANGE_REGEX = re.compile(r'^\s*[\d,]+(?:\.\d+)?\s*-\s*[\d,]+(?:\.\d+)?(?:\s*[a-zA-Z]+)?\s*$')
_PURE_NUMERIC_REGEX = re.compile(r'^\s*[\d,]+(?:\.\d+)?\s*$')

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


def _is_missing_value(value: Any) -> bool:
    """NA-safe emptiness check that never evaluates pandas.NA as bool."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except TypeError:
        # Some objects raise on pd.isna; treat as non-missing and continue
        pass

    text = str(value).strip()
    return text == '' or text.lower() in {'nan', 'none', '<na>'}


def _normalize_text(value: Any) -> str:
    """Normalize text for matching/comparison."""
    if _is_missing_value(value):
        return ''
    text = str(value).strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _normalize_name_key(value: Any) -> str:
    """Aggressive name normalization for cross-language/format matching."""
    text = _normalize_text(value).lower()
    if not text:
        return ''
    text = re.sub(r'[^a-z0-9\u0600-\u06FF]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _series_to_values(series: pd.Series, sample_size: int = 100) -> list[str]:
    """Convert a pandas Series to cleaned string values (NA-safe)."""
    values: list[str] = []
    for raw in series.head(sample_size).tolist():
        norm = _normalize_text(raw)
        if norm:
            values.append(norm)
    return values


def _get_chemicals_db_path() -> str:
    """Resolve chemicals.db path using env override first, then default backend/data."""
    env_path = os.getenv('CHEMICALS_DB_PATH')
    if env_path and os.path.exists(env_path):
        return env_path
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'chemicals.db')


def _split_synonyms(raw: str) -> list[str]:
    """Split synonyms text into tokens."""
    if not raw:
        return []
    return [t.strip() for t in re.split(r'[|;,\n]+', raw) if t and t.strip()]


@lru_cache(maxsize=2)
def _load_cameo_indexes(db_path: str) -> tuple[set[str], set[str]]:
    """
    Load chemical name/synonym and UN indexes for cross-validation.
    Cached for performance.
    """
    names: set[str] = set()
    un_numbers: set[str] = set()

    if not db_path or not os.path.exists(db_path):
        logger.warning(f"CAMEO db not found for deep content analysis: {db_path}")
        return names, un_numbers

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT name, synonyms FROM chemicals")
        for name, synonyms in cursor.fetchall():
            norm_name = _normalize_text(name).lower()
            if norm_name:
                names.add(norm_name)
                names.add(_normalize_name_key(norm_name))
            for syn in _split_synonyms(synonyms or ''):
                norm_syn = _normalize_text(syn).lower()
                if norm_syn:
                    names.add(norm_syn)
                    names.add(_normalize_name_key(norm_syn))

        try:
            cursor.execute("SELECT unna_id FROM chemical_unna")
            for (un_value,) in cursor.fetchall():
                normalized = _normalize_text(un_value).upper().replace('UN', '').strip()
                if normalized.isdigit():
                    un_numbers.add(normalized)
        except sqlite3.Error:
            # Optional table across environments
            pass

    except Exception as exc:
        logger.warning(f"Failed loading CAMEO indexes: {exc}")
    finally:
        if conn:
            conn.close()

    return names, un_numbers


def _cameo_name_index() -> set[str]:
    """Name/synonym index from CAMEO db."""
    names, _ = _load_cameo_indexes(_get_chemicals_db_path())
    return names


def _cameo_un_index() -> set[str]:
    """UN number index from CAMEO db."""
    _, un_numbers = _load_cameo_indexes(_get_chemicals_db_path())
    return un_numbers


@lru_cache(maxsize=2)
def _load_cameo_name_cas_pairs(db_path: str) -> set[tuple[str, str]]:
    """Load normalized (chemical_name, cas_id) pairs for validation checks."""
    pairs: set[tuple[str, str]] = set()
    if not db_path or not os.path.exists(db_path):
        return pairs

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT c.name, cc.cas_id
            FROM chemicals c
            JOIN chemical_cas cc ON c.id = cc.chem_id
            WHERE cc.cas_id IS NOT NULL AND cc.cas_id != ''
            """
        )
        for name, cas_id in cursor.fetchall():
            n = _normalize_name_key(name)
            c = _normalize_text(cas_id)
            if n and c:
                pairs.add((n, c))
    except Exception as exc:
        logger.warning(f"Failed loading CAMEO name-cas pairs: {exc}")
    finally:
        if conn:
            conn.close()

    return pairs


def _definitive_check_column(col_name: str, sample_values: list[str]) -> tuple[str | None, int]:
    """
    Apply definitive rules to classify a column.
    Returns (semantic_type, confidence) or (None, 0) if no definitive match.
    """
    non_empty = [_normalize_text(v) for v in sample_values if _normalize_text(v)]
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
    non_empty = [_normalize_text(v) for v in sample_values if _normalize_text(v)]
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


def _suggested_action(confidence: int, semantic_type: str) -> str:
    """Map confidence bands to UI action hints."""
    if semantic_type == 'unknown' or confidence < 50:
        return 'manual_map'
    if confidence >= 80:
        return 'accept'
    if confidence >= 65:
        return 'review'
    return 'confirm'


def detect_cas_column(values: list[str]) -> tuple[bool, int]:
    """Check if column contains CAS numbers."""
    if not values:
        return False, 0

    valid = 0
    for raw in values:
        v = _normalize_text(raw)
        if not v or not CAS_REGEX.match(v):
            continue
        # Guard against date-like strings such as 2025-10-1
        parts = v.split('-')
        if len(parts[0]) == 4 and parts[0].isdigit() and parts[1].isdigit():
            year = int(parts[0])
            month = int(parts[1])
            if 1900 <= year <= 2100 and 1 <= month <= 12:
                continue
        valid += 1

    ratio = valid / max(len(values), 1)
    if ratio >= 0.70:
        return True, 95
    if ratio >= 0.50:
        return True, 70
    return False, int(ratio * 100)


def detect_chemical_names(values: list[str], cameo_db_connection=None) -> tuple[bool, int]:
    """Check if column contains chemical names by CAMEO lookup."""
    if not values:
        return False, 0

    cameo_index = cameo_db_connection if isinstance(cameo_db_connection, set) else _cameo_name_index()
    db_hits = 0
    pattern_hits = 0
    supplier_like = 0
    alpha_like = 0

    for raw in values:
        val = _normalize_text(raw)
        if not val:
            continue
        low = val.lower()
        key = _normalize_name_key(val)

        if re.search(r'[a-zA-Z\u0600-\u06FF]', val):
            alpha_like += 1

        if low in cameo_index or key in cameo_index:
            db_hits += 1

        if any(tok in low for tok in _CHEMICAL_KEYWORDS):
            pattern_hits += 1
        else:
            words = low.split()
            if any(w.endswith(_CHEMICAL_SUFFIXES) for w in words) or any(w.startswith(_CHEMICAL_PREFIXES) for w in words):
                pattern_hits += 1

        if any(tok in low for tok in _SUPPLIER_TOKENS):
            supplier_like += 1

    total = max(len(values), 1)
    db_ratio = db_hits / total
    pattern_ratio = pattern_hits / total
    supplier_ratio = supplier_like / total
    alpha_ratio = alpha_like / total

    # Guard: mostly numeric columns are not chemical names.
    if alpha_ratio < 0.30:
        return False, 20

    if db_ratio < 0.30 and supplier_ratio >= 0.50:
        return False, 40

    if db_ratio >= 0.50 and pattern_ratio >= 0.30:
        return True, 90
    if db_ratio >= 0.50:
        return True, 85
    if db_ratio >= 0.30:
        return True, 70
    if pattern_ratio >= 0.60:
        return True, 65
    return False, int(max(db_ratio, pattern_ratio) * 100)


def detect_formula_column(values: list[str]) -> tuple[bool, int]:
    """Check if column contains chemical formulas."""
    if not values:
        return False, 0

    formula_regex = re.compile(r'^[A-Z][A-Za-z0-9()]{1,29}$')
    valid = 0

    for raw in values:
        value = _normalize_text(raw).replace(' ', '')
        if len(value) < 2 or len(value) > 30:
            continue
        if not formula_regex.match(value):
            continue

        elements = re.findall(r'[A-Z][a-z]?', value)
        if elements and all(e in _ELEMENT_SYMBOLS for e in elements):
            valid += 1

    ratio = valid / max(len(values), 1)
    if ratio >= 0.80:
        return True, 90
    if ratio >= 0.60:
        return True, 70
    return False, int(ratio * 100)


def detect_supplier_column(values: list[str]) -> tuple[bool, int]:
    """Check if column contains supplier/company names."""
    if not values:
        return False, 0

    company_regex = re.compile(r'\b(corp|inc|ltd|co|llc|gmbh|industries)\b', re.IGNORECASE)
    hits = 0
    for raw in values:
        v = _normalize_text(raw).lower()
        if not v:
            continue
        if company_regex.search(v) or any(tok in v for tok in _SUPPLIER_TOKENS):
            hits += 1

    ratio = hits / max(len(values), 1)
    if ratio >= 0.60:
        return True, 80
    if ratio >= 0.40:
        return True, 65
    return False, int(ratio * 100)


def _detect_un_column(values: list[str]) -> tuple[bool, int]:
    """Detect UN number columns with optional CAMEO cross-check."""
    if not values:
        return False, 0

    format_hits = 0
    cameo_hits = 0
    cameo_un = _cameo_un_index()

    for raw in values:
        v = _normalize_text(raw).upper()
        if not v:
            continue
        if UN_NUMBER_REGEX.match(v):
            format_hits += 1
            normalized = v.replace('UN', '').strip()
            if normalized in cameo_un:
                cameo_hits += 1

    total = max(len(values), 1)
    fmt_ratio = format_hits / total
    cameo_ratio = cameo_hits / max(format_hits, 1)

    if fmt_ratio >= 0.80 and cameo_ratio >= 0.50:
        return True, 90
    if fmt_ratio >= 0.70:
        return True, 70
    return False, int(fmt_ratio * 100)


def _detect_quantity_column(values: list[str]) -> tuple[bool, int]:
    """Detect quantity-like columns (numeric, ranges, numeric+unit)."""
    if not values:
        return False, 0

    with_unit = sum(1 for v in values if _QUANTITY_WITH_UNIT_REGEX.match(_normalize_text(v)))
    ranges = sum(1 for v in values if _QUANTITY_RANGE_REGEX.match(_normalize_text(v)))
    pure_numeric = sum(1 for v in values if _PURE_NUMERIC_REGEX.match(_normalize_text(v)))

    total = max(len(values), 1)
    ratio_with_unit = (with_unit + ranges) / total
    ratio_numeric = pure_numeric / total

    if ratio_with_unit >= 0.70:
        return True, 85
    if ratio_numeric >= 0.80:
        return True, 75
    if ratio_numeric >= 0.50:
        return True, 60
    return False, int(max(ratio_with_unit, ratio_numeric) * 100)


def _detect_location_column(values: list[str]) -> tuple[bool, int]:
    """Detect storage/location columns."""
    if not values:
        return False, 0

    pattern = re.compile(r'\b(room|area|cabinet|zone|building|shelf|bin|warehouse|tank|bay)\b', re.IGNORECASE)
    hits = 0
    normalized = []

    for raw in values:
        v = _normalize_text(raw)
        if not v:
            continue
        normalized.append(v.lower())
        if pattern.search(v) or any(tok in v.lower() for tok in _LOCATION_TOKENS):
            hits += 1

    total = max(len(values), 1)
    ratio = hits / total
    unique_ratio = len(set(normalized)) / max(len(normalized), 1)

    if ratio >= 0.50:
        return True, 85 if ratio >= 0.70 else 75
    if ratio >= 0.35 and unique_ratio < 0.6:
        return True, 65
    return False, int(ratio * 100)


def _detect_date_column(values: list[str]) -> tuple[bool, int]:
    """Detect date-like columns while avoiding product-code collisions."""
    if not values:
        return False, 0

    long_ints = sum(1 for v in values if _normalize_text(v).isdigit() and len(_normalize_text(v)) > 6)
    if long_ints / max(len(values), 1) > 0.5:
        return False, 20

    matches = 0
    for raw in values:
        v = _normalize_text(raw)
        if not v:
            continue
        if any(rx.match(v) for rx in _DATE_VALUE_REGEXES):
            matches += 1
            continue
        try:
            parsed = pd.to_datetime(v, errors='coerce')
            if not pd.isna(parsed):
                matches += 1
        except Exception:
            pass

    ratio = matches / max(len(values), 1)
    if ratio >= 0.70:
        return True, 85
    return False, int(ratio * 100)


def _detect_notes_column(values: list[str]) -> tuple[bool, int]:
    """Catch-all notes/description detector."""
    if not values:
        return False, 0

    normalized = [_normalize_text(v) for v in values if _normalize_text(v)]
    if not normalized:
        return False, 0

    avg_len = sum(len(v) for v in normalized) / len(normalized)
    unique_ratio = len(set(v.lower() for v in normalized)) / len(normalized)
    sentence_like = sum(1 for v in normalized if (' ' in v and len(v) > 15)) / len(normalized)

    if avg_len > 15 and unique_ratio > 0.7 and sentence_like > 0.5:
        return True, 60
    if avg_len > 12 and sentence_like > 0.4:
        return True, 50
    return False, int(sentence_like * 100)


def deep_content_analysis(column_data: pd.Series, col_position: int) -> tuple[str, int]:
    """
    Infer column type purely from data patterns.

    Args:
        column_data: Pandas Series of column values
        col_position: 0-based index of column position

    Returns:
        (semantic_type, confidence_percent)
    """
    values = _series_to_values(column_data, sample_size=100)
    if not values:
        return 'unknown', 0

    is_cas, cas_conf = detect_cas_column(values)
    if is_cas:
        return 'cas', cas_conf

    is_name, name_conf = detect_chemical_names(values, _cameo_name_index())
    if is_name:
        return 'name', name_conf

    is_formula, formula_conf = detect_formula_column(values)
    if is_formula:
        return 'formula', formula_conf

    is_un, un_conf = _detect_un_column(values)
    if is_un:
        return 'un_number', un_conf

    is_qty, qty_conf = _detect_quantity_column(values)
    if is_qty:
        return 'quantity', qty_conf

    is_location, loc_conf = _detect_location_column(values)
    if is_location:
        return 'location', loc_conf

    is_supplier, supplier_conf = detect_supplier_column(values)
    if is_supplier:
        return 'supplier', supplier_conf

    is_date, date_conf = _detect_date_column(values)
    if is_date:
        return 'date', date_conf

    is_notes, notes_conf = _detect_notes_column(values)
    if is_notes:
        return 'notes', notes_conf

    # Position-biased fallback
    if col_position == 0:
        return 'name', 45
    if col_position == 1:
        return 'cas', 40
    return 'unknown', 30


def _resolve_weak_signals(keyword_type: str | None, keyword_conf: int,
                          inferred_type: str | None, inferred_conf: int) -> tuple[str, int, str]:
    """Resolve weak keyword + weak content signals."""
    if keyword_type and inferred_type and keyword_type == inferred_type:
        combined = min(95, int(keyword_conf * 0.4 + inferred_conf * 0.6 + 5))
        return keyword_type, combined, 'Keyword and content signals agree'

    if inferred_type and inferred_type != 'unknown' and inferred_conf >= keyword_conf + 10:
        return inferred_type, max(50, inferred_conf - 5), 'Content signal stronger than keyword hint'

    if keyword_type:
        return keyword_type, max(50, min(79, keyword_conf)), 'Using weak keyword signal (content inconclusive)'

    if inferred_type and inferred_type != 'unknown':
        return inferred_type, max(50, inferred_conf), 'Using content-only weak signal'

    return 'unknown', max(keyword_conf, inferred_conf), 'No reliable signal detected'


def _position_bonus_for_type(semantic_type: str, position_1_based: int, total_cols: int) -> int:
    """Position-based tiebreaker bonus per semantic type."""
    expected = {
        'name': [1],
        'product_code': [1, 2],
        'row_number': [1],
        'cas': [2],
        'formula': [3],
        'un_number': [3],
        'quantity': [3, 4],
        'unit': [4],
        'location': [5],
        'supplier': [6],
        'date': [6],
        'notes': [6],
    }

    if semantic_type not in expected:
        return 0

    slots = expected[semantic_type]
    if semantic_type in {'supplier', 'date', 'notes'} and position_1_based >= min(6, total_cols):
        return 10

    if position_1_based in slots:
        return 10
    if any(abs(position_1_based - slot) <= 1 for slot in slots):
        return 5
    return 0


def apply_position_hints(ambiguous_mappings: dict, col_positions: dict) -> dict:
    """Use column position as tiebreaker."""
    if not ambiguous_mappings:
        return ambiguous_mappings

    total_cols = len(col_positions)

    # 1) Boost borderline confidence using position hints.
    for col, info in ambiguous_mappings.items():
        stype = info.get('semantic_type', 'unknown')
        if stype == 'unknown':
            continue
        pos = col_positions.get(col, 0) + 1
        bonus = _position_bonus_for_type(stype, pos, total_cols)
        if bonus > 0:
            info['confidence'] = min(100, int(info.get('confidence', 0)) + bonus)
            reason = info.get('reasoning', '')
            suffix = f" Position hint (+{bonus}) from column order."
            info['reasoning'] = (reason + suffix).strip()

    # 2) If duplicate semantic types remain, keep best (confidence + position).
    grouped: dict[str, list[tuple[str, int]]] = {}
    for col, info in ambiguous_mappings.items():
        stype = info.get('semantic_type', 'unknown')
        if stype == 'unknown':
            continue
        grouped.setdefault(stype, []).append((col, int(info.get('confidence', 0))))

    for stype, cols in grouped.items():
        if len(cols) <= 1:
            continue
        ranked = sorted(
            cols,
            key=lambda x: (
                x[1],
                _position_bonus_for_type(stype, col_positions.get(x[0], 0) + 1, total_cols),
                -col_positions.get(x[0], 0),
            ),
            reverse=True,
        )
        winner = ranked[0][0]
        for loser, loser_conf in ranked[1:]:
            ambiguous_mappings[loser]['semantic_type'] = 'unknown'
            ambiguous_mappings[loser]['confidence'] = max(loser_conf - 25, 0)
            ambiguous_mappings[loser]['method'] = 'position_hint_downgraded'
            ambiguous_mappings[loser]['reasoning'] = (
                f"Ambiguous with '{winner}' for semantic type '{stype}'. "
                f"Position hint favored '{winner}'."
            )
            ambiguous_mappings[loser]['suggested_action'] = 'manual_map'

    return ambiguous_mappings


def cross_validate_mapping(mapping: dict, df: pd.DataFrame) -> list[str]:
    """Validate detected columns make logical sense together."""
    warnings: list[str] = []

    # Select strongest column per semantic type.
    by_type: dict[str, tuple[str, int]] = {}
    for col, info in mapping.items():
        stype = info.get('semantic_type', 'unknown')
        conf = int(info.get('confidence', 0))
        if stype == 'unknown':
            continue
        if stype not in by_type or conf > by_type[stype][1]:
            by_type[stype] = (col, conf)

    if 'name' not in by_type and 'cas' not in by_type:
        warnings.append("CRITICAL: Neither Name nor CAS column detected.")

    if 'quantity' in by_type and 'unit' not in by_type:
        warnings.append("Quantity detected without Unit column.")
    if 'unit' in by_type and 'quantity' not in by_type:
        warnings.append("Unit detected without Quantity column.")

    if 'location' not in by_type:
        warnings.append("Location column not detected (recommended field).")

    # Name/CAS consistency check.
    if 'name' in by_type and 'cas' in by_type:
        name_col = by_type['name'][0]
        cas_col = by_type['cas'][0]
        sample = df[[name_col, cas_col]].head(10)
        cameo_pairs = _load_cameo_name_cas_pairs(_get_chemicals_db_path())

        name_like_cas = 0
        cas_like_cas = 0
        known_name = 0
        pair_matches = 0
        cameo_names = _cameo_name_index()

        for _, row in sample.iterrows():
            n = _normalize_text(row[name_col])
            c = _normalize_text(row[cas_col])

            if n and CAS_REGEX.match(n):
                name_like_cas += 1
            if c and CAS_REGEX.match(c):
                cas_like_cas += 1

            n_low = n.lower()
            n_key = _normalize_name_key(n)
            if n_low in cameo_names or n_key in cameo_names:
                known_name += 1
            if n_key and c and (n_key, c) in cameo_pairs:
                pair_matches += 1

        if name_like_cas > cas_like_cas:
            warnings.append("Possible Name/CAS column swap detected from sample patterns.")
        if cas_like_cas >= 5 and known_name < 3:
            warnings.append("Low Name↔CAS consistency in sampled rows; review mapping.")
        if len(sample) > 0 and pair_matches / len(sample) < 0.50 and known_name >= 3:
            warnings.append("Name-CAS sampled pairs have <50% CAMEO agreement; possible mapping/data mismatch.")

    # Formula sanity.
    if 'formula' in by_type:
        formula_col = by_type['formula'][0]
        formula_values = _series_to_values(df[formula_col], sample_size=50)
        is_formula, _ = detect_formula_column(formula_values)
        if formula_values and not is_formula:
            warnings.append("Formula column detected, but sampled values do not validate as formulas.")

    return warnings


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
    column_mapping: dict[str, dict[str, Any]] = {}
    assigned_types = {}  # semantic_type -> (col_name, confidence)

    columns = list(df.columns)
    sample_size = min(100, len(df))
    col_positions = {col: idx for idx, col in enumerate(columns)}

    for col in columns:
        series = df[col]
        sample_values = _series_to_values(series, sample_size=sample_size)
        sample_preview = sample_values[:3]

        col_clean = re.sub(r'[^\w\s]', '', str(col)).strip().lower()
        col_clean = re.sub(r'\s+', ' ', col_clean)

        # ── Step 1: Definitive rules ──
        def_type, def_conf = _definitive_check_column(col, sample_values)
        if def_type and def_conf >= 90:
            info = {
                'semantic_type': def_type,
                'confidence': int(def_conf),
                'method': 'definitive_rule',
                'reasoning': f"Definitive pattern detection matched '{def_type}' with high confidence.",
                'sample_values': sample_preview,
            }
            info['suggested_action'] = _suggested_action(info['confidence'], info['semantic_type'])
            column_mapping[col] = info
            _assign_type(assigned_types, def_type, col, def_conf)
            continue

        # ── Step 2: Keyword matching ──
        kw_type = _KEYWORD_REVERSE.get(col_clean)
        kw_conf = 0
        kw_reason = ''
        if kw_type:
            kw_conf = 90
            kw_reason = f"Exact keyword/header match for '{kw_type}'."
        else:
            kw_type, kw_conf = _partial_keyword_match(col_clean)
            if kw_type:
                kw_reason = f"Partial keyword/header hint for '{kw_type}'."

        if kw_type and kw_conf >= 80:
            info = {
                'semantic_type': kw_type,
                'confidence': int(kw_conf),
                'method': 'keyword_match',
                'reasoning': kw_reason,
                'sample_values': sample_preview,
            }
            info['suggested_action'] = _suggested_action(info['confidence'], info['semantic_type'])
            column_mapping[col] = info
            _assign_type(assigned_types, kw_type, col, kw_conf)
            continue

        # ── Step 3: Deep Content Analysis (Bidirectional Core) ──
        best_type = 'unknown'
        confidence = 0
        method = 'unresolved'
        reasoning = 'No reliable keyword or content signal.'

        if ENABLE_DEEP_CONTENT_ANALYSIS and kw_conf < 80:
            inferred_type, inferred_conf = deep_content_analysis(series, col_positions[col])

            if inferred_type != 'unknown' and inferred_conf >= 70:
                best_type = inferred_type
                confidence = int(inferred_conf)
                method = 'content_inferred'
                reasoning = (
                    f"Header confidence low ({kw_conf}%). Deep content analysis inferred '{inferred_type}' "
                    f"with {inferred_conf}% confidence."
                )

            elif kw_conf > 0:
                best_type, confidence, reasoning = _resolve_weak_signals(
                    kw_type, int(kw_conf), inferred_type, int(inferred_conf)
                )
                method = 'hybrid' if best_type != 'unknown' else 'unresolved'

            else:
                if inferred_type != 'unknown' and inferred_conf >= 50:
                    best_type = inferred_type
                    confidence = int(inferred_conf)
                    method = 'content_only'
                    reasoning = (
                        f"No usable header keywords. Content-only inference selected '{inferred_type}' "
                        f"with {inferred_conf}% confidence."
                    )
                else:
                    best_type = 'unknown'
                    confidence = int(inferred_conf)
                    method = 'unresolved'
                    reasoning = (
                        "Header recognition failed and content analysis confidence was below threshold."
                    )
        else:
            # Rollback-safe fallback to legacy voting
            features = _analyze_content(sample_values)
            content_scores = _content_score(features)
            final_scores = {}
            for stype in SEMANTIC_TYPES:
                score = 0.0
                if kw_type == stype:
                    score += kw_conf * 0.5
                score += content_scores.get(stype, 0) * 0.35
                if def_type == stype:
                    score += def_conf * 0.15
                final_scores[stype] = score

            voted_type = max(final_scores, key=final_scores.get)
            voted_score = int(final_scores[voted_type])
            if voted_score >= 70:
                best_type = voted_type
                confidence = min(voted_score, 100)
                method = 'legacy_vote'
                reasoning = f"Feature flag disabled: legacy weighted voting selected '{voted_type}'."
            elif voted_score >= 40:
                best_type = voted_type
                confidence = voted_score
                method = 'legacy_low_confidence'
                reasoning = f"Feature flag disabled: weak legacy vote for '{voted_type}'."
            else:
                best_type = 'unknown'
                confidence = voted_score
                method = 'unresolved'
                reasoning = 'Feature flag disabled and legacy voting was inconclusive.'

        info = {
            'semantic_type': best_type,
            'confidence': int(confidence),
            'method': method,
            'reasoning': reasoning,
            'sample_values': sample_preview,
        }
        info['suggested_action'] = _suggested_action(info['confidence'], info['semantic_type'])
        column_mapping[col] = info

        if best_type != 'unknown':
            _assign_type(assigned_types, best_type, col, int(confidence))
        elif confidence > 0:
            warnings.append(f"Column '{col}' unresolved (confidence {confidence}%).")

    # ── Position hints as tiebreaker ──
    column_mapping = apply_position_hints(column_mapping, col_positions)

    # ── Resolve conflicts: multiple columns mapped to same type ──
    _resolve_conflicts(column_mapping, assigned_types, df, warnings)

    # ── Cross-validation checks ──
    warnings.extend(cross_validate_mapping(column_mapping, df))

    # ── Build canonical rename map ──
    canonical_rename = {
        col: (info['semantic_type'] if info['semantic_type'] != 'unknown' else col)
        for col, info in column_mapping.items()
    }

    # ── Check critical fields ──
    found_types = set(info['semantic_type'] for info in column_mapping.values())
    critical = ['name']
    important = ['cas', 'quantity', 'supplier']

    critical_found = [t for t in critical if t in found_types]
    missing = [t for t in critical + important if t not in found_types]
    review_cols = [
        col for col, info in column_mapping.items()
        if info['confidence'] < 65 or info['semantic_type'] == 'unknown'
    ]

    if 'name' not in found_types and 'cas' not in found_types:
        warnings.append("CRITICAL: No Name or CAS column detected!")

    return {
        'column_mapping': column_mapping,
        'canonical_rename': canonical_rename,
        'critical_fields_found': critical_found,
        'missing_fields': missing,
        'review_required': review_cols,
        'warnings': warnings,
        'feature_flags': {
            'deep_content_analysis': ENABLE_DEEP_CONTENT_ANALYSIS,
        },
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
            column_mapping[col]['reasoning'] = (
                f"Conflict detected with '{winner}' for semantic type '{stype}'. "
                f"This column was downgraded for manual mapping."
            )
            column_mapping[col]['suggested_action'] = 'manual_map'


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
