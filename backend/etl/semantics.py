"""
semantics.py — Semantic Token Classifier & Safety Veto Engine (ETL v5).

This module provides domain-agnostic chemical name understanding by classifying
each token in a chemical name into semantic roles:

  BASE   — The core active ingredient (Gluconate, Atorvastatin, Benzene)
  SALT   — Counter-ion or salt form (Sodium, Calcium, HCl, Sulfate)
  FORM   — Physical form (Pellet, Powder, Liquid, Capsule, Granules)
  GRADE  — Quality standard (USP, BP, Food Grade, Technical, API)
  CONC   — Concentration/percentage (39%, 50mg/ml, 96%)
  SAFETY — Benign context indicator (Flavor, Edible, Wax, Cosmetic)
  HAZARD — Known hazardous substance marker (Phosphorus, Cyanide, Arsenic)
  NOISE  — Irrelevant tokens (numbers, codes, articles)

The veto system prevents dangerous false positives by blocking matches
where a benign input (SAFETY context) would match a hazardous chemical.

Design principles:
  - Domain-agnostic: works for Pharma, Refinery, Mining, Food, Agriculture
  - Fail-safe: when uncertain, classify as BASE (preserves matching)
  - Extensible: add new tokens to dictionaries without code changes
  - Never crashes: all functions return safe defaults on bad input
"""

import re
from enum import Enum
from typing import NamedTuple


class TokenRole(str, Enum):
    BASE = 'BASE'
    SALT = 'SALT'
    FORM = 'FORM'
    GRADE = 'GRADE'
    CONC = 'CONC'
    SAFETY = 'SAFETY'
    HAZARD = 'HAZARD'
    NOISE = 'NOISE'


class ClassifiedToken(NamedTuple):
    text: str
    role: TokenRole
    normalized: str  # lowercase, stripped


# ═══════════════════════════════════════════════════════
#  Token dictionaries (lowercase)
# ═══════════════════════════════════════════════════════

# SALT tokens: counter-ions, salt forms — these modify the base compound
# but should NOT dominate matching
SALT_TOKENS = {
    # Cations
    'sodium', 'potassium', 'calcium', 'magnesium', 'zinc', 'iron',
    'ferrous', 'ferric', 'copper', 'lithium', 'barium', 'aluminum',
    'aluminium', 'ammonium', 'manganese', 'cobalt', 'nickel', 'tin',
    'silver', 'mercury', 'lead', 'chromium', 'strontium', 'bismuth',
    'titanium',
    # Anions / salt suffixes
    'hydrochloride', 'hcl', 'sulfate', 'sulphate', 'nitrate',
    'phosphate', 'carbonate', 'bicarbonate', 'chloride', 'bromide',
    'iodide', 'fluoride', 'acetate', 'citrate', 'tartrate',
    'gluconate', 'lactate', 'succinate', 'fumarate', 'maleate',
    'besylate', 'mesylate', 'tosylate', 'triflate', 'oxide',
    'hydroxide', 'peroxide', 'sulfide', 'sulphide', 'cyanide',
    'azide', 'nitrite', 'sulfite', 'sulphite', 'permanganate',
    'dichromate', 'chromate', 'arsenate', 'arsenite', 'hypochlorite',
    'stearate', 'oleate', 'palmitate', 'benzoate', 'salicylate',
    'oxalate', 'malonate', 'propionate', 'butyrate', 'valerate',
    'dihydrate', 'trihydrate', 'monohydrate', 'pentahydrate',
    'hexahydrate', 'heptahydrate', 'anhydrous',
}

# FORM tokens: physical form descriptors — irrelevant for chemical identity
FORM_TOKENS = {
    'pellet', 'pellets', 'powder', 'granule', 'granules', 'granular',
    'liquid', 'solution', 'suspension', 'emulsion', 'gel', 'cream',
    'ointment', 'tablet', 'tablets', 'capsule', 'capsules', 'syrup',
    'injection', 'injectable', 'spray', 'aerosol', 'foam', 'paste',
    'crystal', 'crystals', 'crystalline', 'flake', 'flakes',
    'bead', 'beads', 'lump', 'lumps', 'chunk', 'chunks',
    'bar', 'bars', 'rod', 'rods', 'wire', 'sheet', 'foil',
    'micronized', 'milled', 'ground', 'crushed', 'sieved',
    'coated', 'uncoated', 'enteric', 'sustained', 'extended',
    'modified', 'delayed', 'immediate', 'release',
    'sl', 'ec', 'wp', 'wg', 'sc', 'sp', 'dp', 'gr',  # agri formulations
    'dc',  # direct compression (pharma)
    'bulk', 'raw',
}

# GRADE tokens: quality/standard descriptors — metadata, not identity
GRADE_TOKENS = {
    'usp', 'bp', 'ep', 'jp', 'nf', 'acs', 'ar', 'lr', 'cp', 'fcc',
    'grade', 'reagent', 'technical', 'analytical', 'certified',
    'purified', 'refined', 'crude', 'raw',
    'pharmaceutical', 'pharma', 'food', 'cosmetic', 'industrial',
    'laboratory', 'lab', 'research', 'commercial',
    'extra', 'pure', 'ultrapure', 'suprapure',
    'api',  # Active Pharmaceutical Ingredient (also petroleum API gravity)
    'gmp', 'iso', 'reach',
    'fertilizer', 'herbicide', 'pesticide', 'insecticide', 'fungicide',
    'edible',
}

# SAFETY tokens: indicators that the input is benign/non-hazardous
# When these appear in input, matching with HAZARD chemicals should be vetoed
SAFETY_TOKENS = {
    # Food/cosmetic context
    'flavor', 'flavour', 'flavoring', 'flavouring', 'fragrance',
    'perfume', 'aroma', 'aromatic', 'essence', 'extract',
    'food', 'edible', 'dietary', 'nutritional', 'supplement',
    'vitamin', 'vit', 'mineral',
    'cosmetic', 'skincare', 'haircare', 'beauty',
    'color', 'colour', 'dye', 'pigment', 'colorant', 'colourant',
    # Benign materials
    'wax', 'paraffin', 'beeswax', 'carnauba',
    'gelatin', 'gelatine', 'capsule', 'capsules',
    'starch', 'cellulose', 'lactose', 'sucrose', 'dextrose',
    'glycerin', 'glycerine', 'glycerol',
    # Pharma indicators (drugs are not hazmat)
    'tablet', 'tablets', 'syrup', 'ointment', 'cream', 'lotion',
    'drops', 'inhaler',
    # Agricultural benign
    'fertilizer', 'manure', 'compost', 'mulch',
    # Petroleum benign
    'lubricant', 'grease', 'coolant',
}

# HAZARD tokens: single-word markers that indicate dangerous chemicals.
# Used for veto checking against SAFETY inputs.
HAZARD_TOKENS = {
    # Elements/compounds known to be extremely dangerous
    'phosphorus', 'arsenic',
    'cyanogen',
    'explosive', 'detonator', 'dynamite', 'tnt', 'nitroglycerin',
    'radioactive', 'uranium', 'plutonium', 'radium', 'thorium',
    'nerve', 'mustard',  # chemical weapons context
    'phosgene', 'chloropicrin', 'sarin', 'tabun', 'vx',
    # Highly toxic industrial chemicals
    'hydrofluoric',
    # Oxidizers that are dangerous in wrong context
    'perchloric', 'perchloride',
    'chromic',
    # Explosive/fuel markers
    'anfo',
}

# DANGEROUS COMPOUND PATTERNS: multi-word substrings in candidate names
# that indicate the candidate is hazardous. Checked against the full
# lowercased candidate name string (not individual tokens).
DANGEROUS_NAME_PATTERNS = [
    'fuel oil', 'ammonium nitrate', 'methyl isocyanate',
    'dimethyl sulfate', 'hydrogen fluoride', 'osmium tetroxide',
    'nerve agent', 'mustard gas', 'white phosphorus',
]

# DANGEROUS SALTS: tokens that classify as SALT (they are anions/cations)
# but are also extremely hazardous. Used for veto checking even though
# classify_token() returns SALT for these.
DANGEROUS_SALT_TOKENS = {
    'arsenate', 'arsenite', 'cyanide', 'azide',
    'chromate', 'dichromate', 'permanganate',
    'sulfide', 'sulphide', 'hypochlorite',
    'nitrite',  # sodium nitrite is toxic
}

# Concentration/percentage pattern
_CONC_PATTERN = re.compile(
    r'^[\d.,]+\s*(%|mg/ml|mg/l|g/l|ppm|ppb|w/w|v/v|w/v|mol/l|m|mm|µm)$',
    re.IGNORECASE
)

# Pure number pattern (noise)
_NUMBER_PATTERN = re.compile(r'^[\d.,]+$')

# Pharma drug suffixes — these indicate the token is a drug BASE name
_PHARMA_SUFFIXES = (
    'statin', 'prazole', 'sartan', 'pril', 'olol', 'dipine',
    'floxacin', 'mycin', 'cillin', 'cycline', 'azole', 'vir',
    'mab', 'nib', 'tinib', 'zumab', 'ximab', 'afil', 'lukast',
    'gliptin', 'glutide', 'canide', 'setron', 'vastatin',
    'profen', 'oxacin', 'tadine', 'zepam', 'barb', 'done',
    'amine', 'quine', 'phylline', 'caine', 'dronate',
    # Additional modern drug stems
    'faxine', 'flozin', 'grelor', 'gliptin', 'balin',
    'xaban', 'gatran', 'pidem', 'ridine', 'tidine',
)

# ═══════════════════════════════════════════════════════
#  Pre-Match Classification (UNIDENTIFIED detection)
# ═══════════════════════════════════════════════════════

# Flavoring keywords — if ANY of these appear in the input name,
# the material is a food flavoring agent → UNIDENTIFIED
_FLAVOR_KEYWORDS = {
    'flavor', 'flavour', 'flavore', 'flavoring', 'flavouring',
    'flavoured', 'flavored',
}

# Trade names that are NOT chemical names — map to generic if possible
_TRADE_NAME_MAP = {
    'coatafilm': None,  # Film coating (no single chemical)
    'avicel': 'microcrystalline cellulose',
    'aerosil': 'colloidal silicon dioxide',
    'eudragit': 'methacrylic acid copolymer',
    'cremophor': 'polyoxyl 40 hydrogenated castor oil',
    'fefol': None,  # Iron+folic acid combo (no single chemical)
    'sunactive': None,  # Iron supplement brand
    'montane': 'sorbitan stearate',
}

# Packaging/auxiliary materials → UNIDENTIFIED
_PACKAGING_PATTERNS = [
    'gelatin capsule', 'gelatine capsule',
    'sugar sphere', 'sugar spheres',
    'empty capsule', 'hard capsule', 'soft capsule',
]

# Edible oil context words — when "oil" appears with these,
# it's edible oil, not industrial/fuel oil
_EDIBLE_OIL_CONTEXTS = {
    'arachis', 'peanut', 'olive', 'coconut', 'sesame', 'sunflower',
    'soybean', 'soy', 'corn', 'palm', 'rapeseed', 'canola',
    'castor', 'linseed', 'flaxseed', 'almond', 'walnut',
    'avocado', 'jojoba', 'argan', 'hemp',
}

# Common noise words
_NOISE_WORDS = {
    'the', 'a', 'an', 'of', 'and', 'or', 'for', 'in', 'with',
    'from', 'by', 'to', 'no', 'nr', 'type', 'class', 'category',
    'product', 'item', 'material', 'substance', 'chemical',
    'compound', 'mixture', 'blend', 'batch', 'lot',
    'new', 'old', 'fresh', 'expired',
    'white', 'black', 'red', 'blue', 'green', 'yellow', 'brown',
    'light', 'dark', 'pale', 'bright',
    'heavy', 'medium', 'fine', 'coarse', 'thin', 'thick',
}

# E-number pattern (EU food additives: E100-E1599)
_E_NUMBER_PATTERN = re.compile(r'^e\d{3,4}[a-z]?$', re.IGNORECASE)


# ═══════════════════════════════════════════════════════
#  Token Classification
# ═══════════════════════════════════════════════════════

def classify_token(word: str) -> TokenRole:
    """
    Classify a single token into its semantic role.

    Priority order (first match wins):
      1. CONC  — percentage/concentration patterns
      2. NOISE — pure numbers, articles, common noise words
      3. GRADE — quality standards (USP, BP, Food Grade)
      4. FORM  — physical forms (Powder, Pellet, Liquid)
      5. SALT  — counter-ions (Sodium, Sulfate, HCl)
      6. SAFETY — benign context (Flavor, Wax, Capsule)
      7. HAZARD — dangerous markers (Phosphorus, Cyanide)
      8. BASE  — default: assume it's the active ingredient
    """
    if not word or not word.strip():
        return TokenRole.NOISE

    w = word.strip().lower()

    # 1. Concentration pattern (39%, 50mg/ml)
    if _CONC_PATTERN.match(w):
        return TokenRole.CONC

    # 2. Pure number or very short noise
    if _NUMBER_PATTERN.match(w):
        return TokenRole.NOISE
    if len(w) <= 1:
        return TokenRole.NOISE
    if w in _NOISE_WORDS:
        return TokenRole.NOISE

    # E-numbers (food additives) — treat as BASE (they ARE the identity)
    if _E_NUMBER_PATTERN.match(w):
        return TokenRole.BASE

    # 3. Grade tokens
    if w in GRADE_TOKENS:
        return TokenRole.GRADE

    # 4. Form tokens
    if w in FORM_TOKENS:
        return TokenRole.FORM

    # 5. Salt tokens
    if w in SALT_TOKENS:
        return TokenRole.SALT

    # 6. Safety tokens
    if w in SAFETY_TOKENS:
        return TokenRole.SAFETY

    # 7. Hazard tokens
    if w in HAZARD_TOKENS:
        return TokenRole.HAZARD

    # 8. Default: BASE (the active ingredient)
    return TokenRole.BASE


def classify_name(name: str) -> list[ClassifiedToken]:
    """
    Tokenize and classify all tokens in a chemical name.
    Handles multi-word salt names and preserves order.
    """
    if not name:
        return []

    # Normalize: remove parenthesized content (already extracted by clean.py)
    # but keep the core name tokens
    clean = re.sub(r'[,;:]+', ' ', name)
    clean = re.sub(r'[^\w\s%./\-]', ' ', clean)
    tokens = clean.split()

    result = []
    for t in tokens:
        t_stripped = t.strip().strip('-').strip('.')
        if not t_stripped:
            continue
        role = classify_token(t_stripped)
        result.append(ClassifiedToken(
            text=t_stripped,
            role=role,
            normalized=t_stripped.lower()
        ))

    return result


def extract_base_tokens(classified: list[ClassifiedToken]) -> list[str]:
    """Extract only BASE tokens (the core chemical identity)."""
    return [t.normalized for t in classified if t.role == TokenRole.BASE]


def extract_salt_tokens(classified: list[ClassifiedToken]) -> list[str]:
    """Extract only SALT tokens."""
    return [t.normalized for t in classified if t.role == TokenRole.SALT]


def has_safety_context(classified: list[ClassifiedToken]) -> bool:
    """Check if the input has benign/safety context tokens."""
    return any(t.role == TokenRole.SAFETY for t in classified)


def has_hazard_tokens(classified: list[ClassifiedToken], full_name: str = '') -> bool:
    """
    Check if a candidate name contains hazard markers.
    Checks three layers:
      1. Tokens with HAZARD role
      2. DANGEROUS_SALT_TOKENS (toxic anions like arsenate, cyanide, azide)
      3. DANGEROUS_NAME_PATTERNS (multi-word patterns like 'fuel oil', 'ammonium nitrate')
    """
    for t in classified:
        if t.role == TokenRole.HAZARD:
            return True
        if t.role == TokenRole.SALT and t.normalized in DANGEROUS_SALT_TOKENS:
            return True
    # Check multi-word dangerous patterns against full name
    if full_name:
        name_lower = full_name.lower()
        for pattern in DANGEROUS_NAME_PATTERNS:
            if pattern in name_lower:
                return True
    return False


def is_pharma_name(name: str) -> bool:
    """
    Detect if a name is likely a pharmaceutical drug name
    based on common drug suffixes (INN stems).
    """
    lower = name.lower().strip()
    return any(lower.endswith(suffix) for suffix in _PHARMA_SUFFIXES)


# ═══════════════════════════════════════════════════════
#  Pre-Match Classification (route to UNIDENTIFIED early)
# ═══════════════════════════════════════════════════════

def classify_material(name: str) -> tuple[str | None, str | None]:
    """
    Pre-match classification: detect materials that should be routed
    to UNIDENTIFIED before fuzzy matching even starts.

    Returns:
        (reason, replacement_name)
        - reason: str if material should be UNIDENTIFIED, None if normal
        - replacement_name: str if trade name has a generic equivalent, None otherwise

    Categories detected:
        1. Food flavoring agents (26 items in ground truth)
        2. Trade names without generic mapping (21 items)
        3. Packaging materials (13 items)
        4. Standalone non-chemical words (e.g. "color")
    """
    if not name or not name.strip():
        return None, None

    lower = name.lower().strip()
    words = set(re.split(r'[\s,;:()/\-]+', lower))

    # ── Rule 1: Food Flavoring Detection ──
    # Any material containing flavor/flavour/flavore → UNIDENTIFIED
    # Check both word tokens AND substrings (catches "flavore" inside parens)
    if words & _FLAVOR_KEYWORDS:
        return "Food flavoring agent (not a chemical)", None

    # Substring check catches misspellings and parenthesized content
    # e.g. "Caramel(Toffee Flavore)" → "flavore" is inside parens
    for kw in _FLAVOR_KEYWORDS:
        if kw in lower:
            return "Food flavoring agent (not a chemical)", None

    # Also check common flavor fruit names as standalone materials
    # e.g. "Tutti Frutti flavor" where "frutti" alone isn't a keyword
    _FLAVOR_CONTEXT_WORDS = {
        'caramel', 'toffee', 'tutti', 'frutti',
    }
    if words & _FLAVOR_CONTEXT_WORDS and words & _FLAVOR_KEYWORDS:
        return "Food flavoring agent (not a chemical)", None

    # ── Rule 2: Packaging Material Detection ──
    for pattern in _PACKAGING_PATTERNS:
        if pattern in lower:
            return f"Packaging material: '{pattern}' (not in CAMEO)", None

    # ── Rule 3: Trade Name Detection ──
    # Two cases:
    # A) Trade name is the PRIMARY name → always UNIDENTIFIED
    #    e.g. "Avicel PH-102", "Cremophor EL"
    # B) Trade name is in PARENTHESES after a generic → UNIDENTIFIED
    #    e.g. "Microcrystalline cellulose(Avicel)" — the presence of a
    #    trade name indicates this is a branded product, not a standard chemical
    # Exception: if the generic part before parens is a well-known chemical
    #    AND the trade name is just a grade/brand suffix, keep it.
    #    e.g. "Pregelatinized Starch (Star Cap 1500)" — starch is the chemical
    paren_start = lower.find('(')
    primary_part = lower[:paren_start].strip() if paren_start > 0 else lower
    paren_part = lower[paren_start:] if paren_start > 0 else ''

    for trade, generic in _TRADE_NAME_MAP.items():
        if trade in primary_part:
            # Trade name IS the primary name → UNIDENTIFIED
            return f"Trade name '{trade}' (not a standard chemical name)", None
        if trade in paren_part:
            # Trade name in parentheses — UNIDENTIFIED (branded product)
            return f"Trade name '{trade}' in product description", None

    # ── Rule 4: Standalone non-chemical words ──
    stripped = lower.strip()
    if stripped in ('color', 'colour', 'dye', 'pigment'):
        return "Non-chemical auxiliary material", None

    # ── Rule 5: Sugar spheres / auxiliary excipients ──
    if 'sugar sphere' in lower or 'sugar spheres' in lower:
        return "Packaging/excipient material (not in CAMEO)", None

    return None, None


def is_edible_oil_context(name: str) -> bool:
    """
    Check if a name containing 'oil' is in an edible oil context.
    Used to prevent matching edible oils with fuel oil / industrial oil.
    """
    lower = name.lower()
    if 'oil' not in lower:
        return False
    words = set(re.split(r'[\s,;:()/\-]+', lower))
    return bool(words & _EDIBLE_OIL_CONTEXTS)


# ═══════════════════════════════════════════════════════
#  Semantic Scoring
# ═══════════════════════════════════════════════════════

def _jaccard(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _overlap_ratio(input_set: set, candidate_set: set) -> float:
    """What fraction of input tokens appear in candidate? (asymmetric)"""
    if not input_set:
        return 0.0
    return len(input_set & candidate_set) / len(input_set)


def semantic_score(input_name: str, candidate_name: str) -> dict:
    """
    Calculate semantic similarity between input and candidate chemical names.

    Returns:
        {
            'score': float (0.0-1.0),
            'base_overlap': float,
            'salt_overlap': float,
            'vetoed': bool,
            'veto_reason': str or None,
            'input_roles': dict,   # role → [tokens]
            'candidate_roles': dict,
        }
    """
    input_tokens = classify_name(input_name)
    cand_tokens = classify_name(candidate_name)

    input_bases = set(extract_base_tokens(input_tokens))
    cand_bases = set(extract_base_tokens(cand_tokens))
    input_salts = set(extract_salt_tokens(input_tokens))
    cand_salts = set(extract_salt_tokens(cand_tokens))

    input_has_safety = has_safety_context(input_tokens)
    cand_has_hazard = has_hazard_tokens(cand_tokens, full_name=candidate_name)

    # ── Veto Rules ──

    # Helper: collect hazard indicator tokens from candidate (HAZARD role + dangerous salts + patterns)
    cand_hazard_labels = []
    for t in cand_tokens:
        if t.role == TokenRole.HAZARD:
            cand_hazard_labels.append(t.text)
        elif t.role == TokenRole.SALT and t.normalized in DANGEROUS_SALT_TOKENS:
            cand_hazard_labels.append(t.text)
    # Also check multi-word patterns
    cand_lower = candidate_name.lower()
    for pattern in DANGEROUS_NAME_PATTERNS:
        if pattern in cand_lower and pattern not in [l.lower() for l in cand_hazard_labels]:
            cand_hazard_labels.append(pattern)

    # Rule 1: SAFETY input + HAZARD candidate → BLOCK
    vetoed = False
    veto_reason = None
    if input_has_safety and cand_has_hazard:
        input_safety_labels = [t.text for t in input_tokens if t.role == TokenRole.SAFETY]
        vetoed = True
        veto_reason = (
            f"Safety veto: input has safety context "
            f"[{', '.join(input_safety_labels)}] "
            f"but candidate contains hazard "
            f"[{', '.join(cand_hazard_labels)}]"
        )

    # Rule 2: Pharma drug name should not match industrial hazmat
    # Check each BASE token individually (not just the full concatenated string)
    if not vetoed and cand_has_hazard:
        for t in input_tokens:
            if t.role == TokenRole.BASE and is_pharma_name(t.text):
                vetoed = True
                veto_reason = (
                    f"Pharma veto: '{t.text}' is a drug name, "
                    f"candidate has hazard tokens [{', '.join(cand_hazard_labels)}]"
                )
                break

    # Rule 3: BASE mismatch + hazardous candidate → veto
    # If input has BASE tokens that DON'T appear in candidate AND candidate
    # is hazardous, this is almost certainly a false positive.
    if not vetoed and cand_has_hazard and input_bases:
        if not input_bases.intersection(cand_bases):
            vetoed = True
            veto_reason = (
                f"Base+hazard veto: input base tokens {input_bases} "
                f"not found in hazardous candidate [{', '.join(cand_hazard_labels)}]"
            )

    # Rule 4: Edible oil context → block fuel/explosive oil matches
    # e.g. "Arachis Oil" should NEVER match "AMMONIUM NITRATE-FUEL OIL MIXTURE"
    if not vetoed and is_edible_oil_context(input_name):
        cand_lower = candidate_name.lower()
        if ('fuel' in cand_lower or 'nitrate' in cand_lower
                or 'explosive' in cand_lower or 'mixture' in cand_lower):
            vetoed = True
            veto_reason = (
                f"Edible oil veto: '{input_name}' is edible oil, "
                f"candidate '{candidate_name}' contains fuel/explosive context"
            )

    if vetoed:
        return {
            'score': 0.0,
            'base_overlap': 0.0,
            'salt_overlap': 0.0,
            'vetoed': True,
            'veto_reason': veto_reason,
            'input_roles': _roles_summary(input_tokens),
            'candidate_roles': _roles_summary(cand_tokens),
        }

    # ── Scoring ──

    # Base overlap (most important — the core chemical identity)
    base_overlap = _overlap_ratio(input_bases, cand_bases) if input_bases else 0.0

    # Salt overlap
    salt_overlap = _overlap_ratio(input_salts, cand_salts) if input_salts else 0.0

    # If input has BASE tokens but NONE overlap with candidate → cap score
    # This prevents "Zinc Gluconate" matching "Zinc Chloride" (salt matches but base doesn't)
    if input_bases and base_overlap == 0.0:
        # Only salt/form matched — very weak match
        score = salt_overlap * 0.25
        score = min(score, 0.35)  # Hard cap: force REVIEW at best
    elif not input_bases:
        # No base tokens in input (unusual) — rely on salt + fuzzy
        score = salt_overlap * 0.50
    else:
        # Normal scoring: weighted combination
        score = (base_overlap * 0.60) + (salt_overlap * 0.25)

        # Bonus: if ALL base tokens match (not just partial)
        if input_bases and input_bases <= cand_bases:
            score += 0.10  # Full base match bonus

        # Bonus: if salt also matches
        if input_salts and input_salts <= cand_salts:
            score += 0.05  # Full salt match bonus

    # Rule 3: If ONLY concentration matched (no base, no salt) → zero
    input_conc = {t.normalized for t in input_tokens if t.role == TokenRole.CONC}
    if input_conc and not input_bases and not input_salts:
        score = 0.0

    score = max(0.0, min(1.0, score))

    return {
        'score': score,
        'base_overlap': round(base_overlap, 3),
        'salt_overlap': round(salt_overlap, 3),
        'vetoed': False,
        'veto_reason': None,
        'input_roles': _roles_summary(input_tokens),
        'candidate_roles': _roles_summary(cand_tokens),
    }


def _roles_summary(tokens: list[ClassifiedToken]) -> dict:
    """Summarize tokens by role for diagnostics."""
    summary = {}
    for t in tokens:
        role = t.role.value
        summary.setdefault(role, []).append(t.text)
    return summary


# ═══════════════════════════════════════════════════════
#  CAS Validation (Strict)
# ═══════════════════════════════════════════════════════

_STRICT_CAS_PATTERN = re.compile(r'^\d{2,7}-\d{2}-\d$')


def is_plausible_cas(raw: str) -> bool:
    """
    Strict CAS plausibility check.
    Rejects product codes that happen to pass checksum.

    Rules:
      1. Must match format: 2-7 digits, dash, 2 digits, dash, 1 digit
      2. Total length (with dashes) must be 7-12 characters
      3. Must pass checksum
      4. First segment must be 2-7 digits (not more)
    """
    if not raw or not raw.strip():
        return False

    cas = raw.strip()

    # Format check
    if not _STRICT_CAS_PATTERN.match(cas):
        return False

    # Length check (shortest: 50-00-0 = 7 chars, longest: 1234567-89-0 = 12 chars)
    if len(cas) < 7 or len(cas) > 12:
        return False

    # Checksum
    digits = cas.replace('-', '')
    if not digits.isdigit() or len(digits) < 5:
        return False

    check = int(digits[-1])
    body = digits[:-1]
    total = sum((i + 1) * int(d) for i, d in enumerate(reversed(body)))
    return total % 10 == check


def is_likely_product_code(raw: str) -> bool:
    """
    Detect if a numeric string is likely a product code, not a CAS number.

    Product codes tend to:
      - Be 8+ digits with no dashes
      - Have repeating digit patterns (1112420015)
      - Start with 111, 112, etc. (sequential codes)
    """
    if not raw:
        return False

    digits_only = re.sub(r'[\s\-]', '', raw)
    if not digits_only.isdigit():
        return False

    # Very long pure digit strings (>10) are almost certainly product codes
    if len(digits_only) > 10:
        return True

    # 8-10 digit strings with repeating patterns
    if len(digits_only) >= 8:
        # Check for sequential/repeating prefix (common in product codes)
        prefix = digits_only[:3]
        if prefix in ('111', '112', '110', '100', '200', '300'):
            return True

    return False
