"""
match.py — Hybrid Multi-Signal Chemical Matching Engine (ETL v3).

Architecture:
  Each field (CAS, name, formula, UN, synonym) independently generates
  "signals" — candidate chemical IDs with per-signal confidence scores.
  A weighted fusion layer combines all signals, detects cross-field
  conflicts, and picks the best candidate with calibrated confidence.

Key features:
  - Independent per-field evaluation (no sequential waterfall — all fields vote)
  - Field-swap detection (CAS in name column, name in CAS column, etc.)
  - Cross-field conflict detection (name says X, CAS says Y → flag)
  - Fuzzy matching on name AND synonyms with WRatio (case-insensitive)
  - Formula matching (exact + normalized)
  - UN number matching
  - Weighted fusion with configurable signal weights
  - Probabilistic confidence calibration

Anti-Hallucination: NEVER creates new chemicals. Output is always
a chemical_id that EXISTS in chemicals.db, or None.

Statuses:
  MATCHED          — fused confidence ≥ 0.85
  REVIEW_REQUIRED  — fused confidence 0.60–0.85, or conflict detected
  UNIDENTIFIED     — fused confidence < 0.60
"""

import re
import logging
import sqlite3
from typing import Optional
from collections import defaultdict

from rapidfuzz import fuzz, process as rfprocess

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  Signal weights — how much each field contributes
# ═══════════════════════════════════════════════════════
SIGNAL_WEIGHTS = {
    'cas_exact':        1.00,   # CAS is gold standard
    'cas_scanned':      0.95,   # CAS found in non-CAS column
    'cas_from_name':    0.90,   # CAS pattern detected in name field (field swap)
    'name_exact':       0.90,   # Exact name match
    'name_synonym':     0.85,   # Exact synonym match
    'name_normalized':  0.80,   # Normalized string match
    'name_fuzzy':       0.70,   # Fuzzy name match (scaled by score)
    'formula_exact':    0.75,   # Exact formula match
    'formula_norm':     0.65,   # Normalized formula match
    'un_exact':         0.80,   # Exact UN number match
    'synonym_fuzzy':    0.65,   # Fuzzy synonym match (scaled by score)
}

# Thresholds for final status
THRESHOLD_MATCHED = 0.85
THRESHOLD_REVIEW  = 0.60

# CAS regex
CAS_REGEX = re.compile(r'\b(\d{2,7}-\d{2}-\d)\b')


def _normalize(s: str) -> str:
    """Remove all non-alphanumeric characters and lowercase."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _normalize_formula(f: str) -> str:
    """Normalize chemical formula: strip spaces, lowercase."""
    return re.sub(r'\s+', '', f).lower()


def _validate_cas_checksum(cas: str) -> bool:
    """Validate CAS checksum without cleaning — assumes format X-XX-X."""
    digits = cas.replace('-', '')
    if not digits.isdigit() or len(digits) < 5:
        return False
    check = int(digits[-1])
    body = digits[:-1]
    total = sum((i + 1) * int(d) for i, d in enumerate(reversed(body)))
    return total % 10 == check


class Signal:
    """A single matching signal from one field/method."""
    __slots__ = ('chemical_id', 'chemical_name', 'source', 'raw_score', 'weight', 'detail')

    def __init__(self, chemical_id: int, chemical_name: str, source: str,
                 raw_score: float, weight: float, detail: str = ''):
        self.chemical_id = chemical_id
        self.chemical_name = chemical_name
        self.source = source          # e.g. 'cas_exact', 'name_fuzzy'
        self.raw_score = raw_score    # 0.0–1.0 from the matching method
        self.weight = weight          # from SIGNAL_WEIGHTS
        self.detail = detail          # human-readable explanation

    @property
    def weighted_score(self) -> float:
        return self.raw_score * self.weight

    def to_dict(self) -> dict:
        return {
            'chemical_id': self.chemical_id,
            'chemical_name': self.chemical_name,
            'source': self.source,
            'score': round(self.raw_score * 100, 1),
            'weighted': round(self.weighted_score * 100, 1),
            'detail': self.detail,
        }


class HybridMatcher:
    """
    Hybrid Multi-Signal Chemical Matching Engine.
    Every field independently generates signals, then fusion picks the best.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._loaded = False

        # Caches
        self._cas_map: dict[str, list[dict]] = {}     # stripped_cas → [{id, name, cas}, ...]
        self._name_map: dict[str, dict] = {}           # UPPER(name) → {id, name}
        self._norm_map: dict[str, dict] = {}           # normalized(name) → {id, name}
        self._synonym_map: dict[str, list[dict]] = {}  # UPPER(syn) → [{id, name}, ...]
        self._formula_map: dict[str, list[dict]] = {}  # normalized(formula) → [{id, name}, ...]
        self._un_map: dict[int, list[dict]] = {}       # un_number → [{id, name}, ...]
        self._fuzzy_names: list[str] = []              # lowercase names for fuzzy
        self._fuzzy_name_to_entry: dict[str, dict] = {}
        self._fuzzy_syns: list[str] = []               # lowercase synonyms for fuzzy
        self._fuzzy_syn_to_entry: dict[str, dict] = {}

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ═══════════════════════════════════════════════════════
    #  Cache loading
    # ═══════════════════════════════════════════════════════

    def _ensure_caches(self):
        if self._loaded:
            return
        conn = self._get_conn()
        c = conn.cursor()

        # ── CAS (store ALL chemicals per CAS) ──
        c.execute("""
            SELECT cc.cas_id, ch.id, ch.name
            FROM chemical_cas cc JOIN chemicals ch ON ch.id = cc.chem_id
        """)
        for row in c.fetchall():
            cas_raw = row['cas_id']
            stripped = re.sub(r'[\s\-]', '', cas_raw)
            self._cas_map.setdefault(stripped, []).append(
                {'id': row['id'], 'name': row['name'], 'cas': cas_raw}
            )

        # ── UN (store ALL chemicals per UN) ──
        c.execute("""
            SELECT cu.unna_id, ch.id, ch.name
            FROM chemical_unna cu JOIN chemicals ch ON ch.id = cu.chem_id
        """)
        for row in c.fetchall():
            self._un_map.setdefault(int(row['unna_id']), []).append(
                {'id': row['id'], 'name': row['name']}
            )

        # ── Names, Synonyms, Formulas ──
        c.execute("SELECT id, name, synonyms, formulas FROM chemicals")
        for row in c.fetchall():
            cid = row['id']
            name = (row['name'] or '').strip()
            entry = {'id': cid, 'name': name}

            # Name caches
            if name:
                name_upper = name.upper()
                self._name_map[name_upper] = entry
                name_norm = _normalize(name)
                if name_norm:
                    self._norm_map[name_norm] = entry
                low = name.lower()
                self._fuzzy_names.append(low)
                self._fuzzy_name_to_entry[low] = entry

            # Synonym caches
            synonyms = row['synonyms'] or ''
            for syn in synonyms.split('|'):
                syn = syn.strip()
                if not syn:
                    continue
                syn_upper = syn.upper()
                self._synonym_map.setdefault(syn_upper, []).append(entry)
                syn_norm = _normalize(syn)
                if syn_norm and syn_norm not in self._norm_map:
                    self._norm_map[syn_norm] = entry
                syn_low = syn.lower()
                if syn_low not in self._fuzzy_syn_to_entry:
                    self._fuzzy_syns.append(syn_low)
                    self._fuzzy_syn_to_entry[syn_low] = entry

            # Formula caches
            formulas = row['formulas'] or ''
            for f in formulas.split('|'):
                f = f.strip()
                if f:
                    fnorm = _normalize_formula(f)
                    self._formula_map.setdefault(fnorm, []).append(entry)

        conn.close()
        self._loaded = True
        logger.info(
            f"HybridMatcher caches: {len(self._cas_map)} CAS, "
            f"{len(self._name_map)} names, {len(self._norm_map)} normalized, "
            f"{len(self._synonym_map)} synonyms, {len(self._formula_map)} formulas, "
            f"{len(self._un_map)} UN, {len(self._fuzzy_syns)} fuzzy-syns"
        )

    # ═══════════════════════════════════════════════════════
    #  Main entry point
    # ═══════════════════════════════════════════════════════

    def match(self, cleaned: dict) -> dict:
        """
        Hybrid multi-signal match for a cleaned inventory row.

        Returns:
            {
                'chemical_id': int or None,
                'chemical_name': str or None,
                'match_method': str,
                'confidence': float (0.0–1.0),
                'match_status': 'MATCHED' | 'REVIEW_REQUIRED' | 'UNIDENTIFIED',
                'suggestions': list[dict],
                'signals': list[dict],        # all signals for diagnostics
                'conflicts': list[str],       # conflict warnings
                'field_swaps': list[str],     # detected field swaps
            }
        """
        self._ensure_caches()

        signals: list[Signal] = []
        conflicts: list[str] = []
        field_swaps: list[str] = []

        # ── Extract raw inputs ──
        cas_raw = (cleaned.get('cas') or '').strip()
        cas_valid = cleaned.get('cas_valid', False)
        cas_scanned = (cleaned.get('cas_scanned') or '').strip()
        name = (cleaned.get('name') or '').strip()
        formula = (cleaned.get('formula') or '').strip()
        un_number = cleaned.get('un_number')

        # ═══════════════════════════════════════════════════
        #  PHASE 1: Field-swap detection
        #  Check if fields contain data meant for other fields
        # ═══════════════════════════════════════════════════

        # Check if name column contains a CAS number
        cas_in_name = None
        if name:
            cas_matches = CAS_REGEX.findall(name)
            for candidate in cas_matches:
                if _validate_cas_checksum(candidate):
                    cas_in_name = candidate
                    field_swaps.append(f"CAS '{candidate}' found in name column")
                    break

        # Check if CAS column contains a chemical name (non-numeric, no dashes pattern)
        name_in_cas = None
        if cas_raw and not CAS_REGEX.match(cas_raw):
            # CAS column has something that doesn't look like a CAS
            if len(cas_raw) > 3 and not cas_raw.replace('-', '').replace(' ', '').isdigit():
                name_in_cas = cas_raw
                field_swaps.append(f"Name '{cas_raw}' found in CAS column")

        # ═══════════════════════════════════════════════════
        #  PHASE 2: Generate signals from each field
        # ═══════════════════════════════════════════════════

        # ── 2a: CAS signals ──
        if cas_raw and cas_valid:
            sigs = self._signals_from_cas(cas_raw, 'cas_exact')
            signals.extend(sigs)

        if cas_scanned and cas_scanned != cas_raw:
            sigs = self._signals_from_cas(cas_scanned, 'cas_scanned')
            signals.extend(sigs)

        if cas_in_name:
            sigs = self._signals_from_cas(cas_in_name, 'cas_from_name')
            signals.extend(sigs)

        # ── 2b: Name signals (from name column) ──
        name_candidates = []
        if name:
            name_candidates.append(name)
        if name_in_cas:
            name_candidates.append(name_in_cas)

        for n in name_candidates:
            signals.extend(self._signals_from_name(n))

        # ── 2c: Formula signals ──
        if formula:
            signals.extend(self._signals_from_formula(formula))

        # ── 2d: UN number signals ──
        if un_number:
            signals.extend(self._signals_from_un(un_number))

        # ═══════════════════════════════════════════════════
        #  PHASE 3: Weighted fusion
        # ═══════════════════════════════════════════════════

        if not signals:
            return self._build_result(None, None, 'unmatched', 0.0, 'UNIDENTIFIED',
                                      [], signals, conflicts, field_swaps)

        # Aggregate: for each candidate, keep only the BEST signal per source type
        # This prevents multiple fuzzy hits from inflating or diluting scores
        # candidate_best_per_type[cid][source_type] = best Signal
        candidate_best_per_type: dict[int, dict[str, Signal]] = defaultdict(dict)
        candidate_names: dict[int, str] = {}

        for sig in signals:
            cid = sig.chemical_id
            stype = sig.source  # full source like 'cas_exact', 'name_fuzzy'
            candidate_names[cid] = sig.chemical_name
            existing = candidate_best_per_type[cid].get(stype)
            if not existing or sig.weighted_score > existing.weighted_score:
                candidate_best_per_type[cid][stype] = sig

        # Calculate score per candidate: sum of best signal per source type
        candidate_scores: dict[int, float] = {}
        candidate_methods: dict[int, str] = {}
        for cid, type_map in candidate_best_per_type.items():
            total = 0.0
            best_sig = None
            for sig in type_map.values():
                total += sig.weighted_score
                if best_sig is None or sig.weighted_score > best_sig.weighted_score:
                    best_sig = sig
            candidate_scores[cid] = total
            candidate_methods[cid] = best_sig.source if best_sig else 'unmatched'

        # Theoretical max: based on INPUT fields that ACTUALLY PRODUCED useful signals.
        # If a field was provided but produced no strong signal (e.g. Persian name
        # against English DB), it should NOT penalize the denominator.
        # A "useful" signal = weighted_score >= 0.50
        categories_with_useful_signals = set()
        for sig in signals:
            if sig.weighted_score >= 0.50:
                categories_with_useful_signals.add(sig.source.split('_')[0])

        input_category_weights = {}
        if (cas_raw or cas_scanned or cas_in_name) and 'cas' in categories_with_useful_signals:
            input_category_weights['cas'] = SIGNAL_WEIGHTS['cas_exact']
        if (name or name_in_cas) and ('name' in categories_with_useful_signals or 'synonym' in categories_with_useful_signals):
            input_category_weights['name'] = SIGNAL_WEIGHTS['name_exact']
        if formula and 'formula' in categories_with_useful_signals:
            input_category_weights['formula'] = SIGNAL_WEIGHTS['formula_exact']
        if un_number and 'un' in categories_with_useful_signals:
            input_category_weights['un'] = SIGNAL_WEIGHTS['un_exact']

        # Fallback: if no category had useful signals, use all provided fields
        if not input_category_weights:
            if cas_raw or cas_scanned or cas_in_name:
                input_category_weights['cas'] = SIGNAL_WEIGHTS['cas_exact']
            if name or name_in_cas:
                input_category_weights['name'] = SIGNAL_WEIGHTS['name_exact']
            if formula:
                input_category_weights['formula'] = SIGNAL_WEIGHTS['formula_exact']
            if un_number:
                input_category_weights['un'] = SIGNAL_WEIGHTS['un_exact']

        theoretical_max = sum(input_category_weights.values())
        theoretical_max = max(theoretical_max, 0.01)

        # Sort candidates
        ranked = sorted(candidate_scores.items(), key=lambda x: x[1], reverse=True)
        best_id, best_score = ranked[0]
        best_name = candidate_names[best_id]
        best_method = candidate_methods[best_id]

        # Calibrate confidence against theoretical max
        confidence = min(best_score / theoretical_max, 1.0)

        # Boost: if multiple independent field categories agree on best candidate
        # Also detect "same-family" agreement: different DB IDs but same base chemical
        # e.g. "HYDROGEN PEROXIDE, STABILIZED" vs "HYDROGEN PEROXIDE, AQUEOUS SOLUTION"
        agreeing_categories = set()
        best_base = best_name.split(',')[0].strip().upper() if best_name else ''

        for sig in signals:
            if sig.weighted_score < 0.40:
                continue
            cat = sig.source.split('_')[0]
            if sig.chemical_id == best_id:
                agreeing_categories.add(cat)
            elif best_base and len(best_base) >= 5:
                # Same-family: different ID but name starts with same base
                sig_base = sig.chemical_name.split(',')[0].strip().upper() if sig.chemical_name else ''
                if sig_base == best_base:
                    agreeing_categories.add(cat)

        if len(agreeing_categories) >= 2:
            # Strong bonus for multi-field agreement (CAS+UN, CAS+formula, etc.)
            bonus = 0.12 * (len(agreeing_categories) - 1)
            confidence = min(confidence + bonus, 1.0)

        # ═══════════════════════════════════════════════
        #  PHASE 4: Conflict detection
        #  Only consider STRONG signals (weighted ≥ 50%) to avoid noise
        # ═══════════════════════════════════════════════

        CONFLICT_THRESHOLD = 0.50  # Only strong signals trigger conflicts

        # Cross-field conflict: CAS says X, name/synonym says Y (strong signals only)
        strong_cas_cids = set()
        strong_name_cids = set()
        for sig in signals:
            if sig.weighted_score < CONFLICT_THRESHOLD:
                continue
            if sig.source.startswith('cas'):
                strong_cas_cids.add(sig.chemical_id)
            elif sig.source.startswith('name_exact') or sig.source.startswith('name_synonym'):
                strong_name_cids.add(sig.chemical_id)

        if strong_cas_cids and strong_name_cids and not strong_cas_cids.intersection(strong_name_cids):
            cas_names_str = [candidate_names.get(c, '?') for c in strong_cas_cids]
            name_names_str = [candidate_names.get(c, '?') for c in strong_name_cids]
            conflicts.append(
                f"CAS points to [{', '.join(cas_names_str)}] "
                f"but name matches [{', '.join(name_names_str)}]"
            )
            # Penalize but keep at REVIEW level
            confidence = min(confidence, 0.80)

        # Formula vs name conflict (strong signals only)
        strong_formula_cids = set()
        for sig in signals:
            if sig.weighted_score >= CONFLICT_THRESHOLD and sig.source.startswith('formula'):
                strong_formula_cids.add(sig.chemical_id)
        if strong_formula_cids and strong_name_cids and not strong_formula_cids.intersection(strong_name_cids):
            conflicts.append(
                f"Formula and name point to different chemicals"
            )

        # If real conflicts exist, cap at REVIEW_REQUIRED
        if conflicts:
            confidence = min(confidence, 0.84)

        # ═══════════════════════════════════════════════
        #  PHASE 5: Build suggestions
        # ═══════════════════════════════════════════════

        suggestions = []
        seen_sug = set()
        for cid, score in ranked[:5]:
            if cid not in seen_sug:
                seen_sug.add(cid)
                cal_score = min(score / theoretical_max * 100, 100)
                suggestions.append({
                    'chemical_id': cid,
                    'chemical_name': candidate_names[cid],
                    'score': round(cal_score, 1),
                })

        # Also add fuzzy suggestions if we don't have enough
        if len(suggestions) < 3 and name:
            extra = self._fuzzy_suggestions(name, exclude_ids=seen_sug)
            suggestions.extend(extra)
            suggestions = suggestions[:5]

        # ═══════════════════════════════════════════════
        #  PHASE 6: Determine status
        # ═══════════════════════════════════════════════

        if confidence >= THRESHOLD_MATCHED:
            status = 'MATCHED'
        elif confidence >= THRESHOLD_REVIEW:
            status = 'REVIEW_REQUIRED'
        else:
            status = 'UNIDENTIFIED'

        return self._build_result(
            best_id, best_name, best_method, confidence, status,
            suggestions, signals, conflicts, field_swaps
        )

    # ═══════════════════════════════════════════════════════
    #  Signal generators (per field)
    # ═══════════════════════════════════════════════════════

    def _signals_from_cas(self, cas: str, source: str) -> list[Signal]:
        """Generate signals from a CAS number. Prefer base chemicals over mixtures."""
        stripped = re.sub(r'[\s\-]', '', cas)
        hits = self._cas_map.get(stripped, [])
        if not hits:
            return []
        # Sort: prefer shorter names (base chemicals) over long mixture names
        sorted_hits = sorted(hits, key=lambda h: len(h['name']))
        sigs = []
        w = SIGNAL_WEIGHTS.get(source, 0.5)
        for i, hit in enumerate(sorted_hits[:3]):
            # First (shortest name = base chemical) gets full score,
            # subsequent entries get diminishing scores
            raw = 1.0 if i == 0 else 0.6
            sigs.append(Signal(
                chemical_id=hit['id'],
                chemical_name=hit['name'],
                source=source,
                raw_score=raw,
                weight=w,
                detail=f"CAS {cas} → {hit['name']}"
            ))
        return sigs

    def _signals_from_name(self, name: str) -> list[Signal]:
        """
        Generate ALL signals from a name string:
        exact name, exact synonym, normalized, fuzzy name, fuzzy synonym.
        """
        sigs: list[Signal] = []
        name_upper = name.upper().strip()
        name_norm = _normalize(name)
        name_lower = name.lower().strip()

        # Exact name
        hit = self._name_map.get(name_upper)
        if hit:
            sigs.append(Signal(
                hit['id'], hit['name'], 'name_exact', 1.0,
                SIGNAL_WEIGHTS['name_exact'],
                f"Exact name match: '{name}'"
            ))

        # Exact synonym
        syn_hits = self._synonym_map.get(name_upper, [])
        for sh in syn_hits:
            sigs.append(Signal(
                sh['id'], sh['name'], 'name_synonym', 1.0,
                SIGNAL_WEIGHTS['name_synonym'],
                f"Exact synonym match: '{name}' → {sh['name']}"
            ))

        # Normalized match
        if name_norm:
            hit = self._norm_map.get(name_norm)
            if hit and not any(s.chemical_id == hit['id'] and s.source in ('name_exact', 'name_synonym') for s in sigs):
                sigs.append(Signal(
                    hit['id'], hit['name'], 'name_normalized', 1.0,
                    SIGNAL_WEIGHTS['name_normalized'],
                    f"Normalized match: '{name}' → {hit['name']}"
                ))

        # Fuzzy name match (top 3)
        already_found = {s.chemical_id for s in sigs}
        if self._fuzzy_names:
            results = rfprocess.extract(name_lower, self._fuzzy_names, scorer=fuzz.WRatio, limit=5)
            for match_low, score, _idx in results:
                entry = self._fuzzy_name_to_entry.get(match_low)
                if entry and entry['id'] not in already_found and score >= 70:
                    sigs.append(Signal(
                        entry['id'], entry['name'], 'name_fuzzy',
                        score / 100.0,
                        SIGNAL_WEIGHTS['name_fuzzy'],
                        f"Fuzzy name: '{name}' ≈ '{entry['name']}' ({score:.0f}%)"
                    ))
                    already_found.add(entry['id'])

        # Fuzzy synonym match (top 3)
        if self._fuzzy_syns:
            results = rfprocess.extract(name_lower, self._fuzzy_syns, scorer=fuzz.WRatio, limit=5)
            for match_low, score, _idx in results:
                entry = self._fuzzy_syn_to_entry.get(match_low)
                if entry and entry['id'] not in already_found and score >= 70:
                    sigs.append(Signal(
                        entry['id'], entry['name'], 'synonym_fuzzy',
                        score / 100.0,
                        SIGNAL_WEIGHTS['synonym_fuzzy'],
                        f"Fuzzy synonym: '{name}' ≈ '{match_low}' → {entry['name']} ({score:.0f}%)"
                    ))
                    already_found.add(entry['id'])

        return sigs

    def _signals_from_formula(self, formula: str) -> list[Signal]:
        """Generate signals from a chemical formula."""
        sigs: list[Signal] = []
        fnorm = _normalize_formula(formula)
        hits = self._formula_map.get(fnorm, [])
        for hit in hits[:3]:
            sigs.append(Signal(
                hit['id'], hit['name'], 'formula_exact', 1.0,
                SIGNAL_WEIGHTS['formula_exact'],
                f"Formula match: '{formula}' → {hit['name']}"
            ))
        return sigs

    def _signals_from_un(self, un_number) -> list[Signal]:
        """Generate signals from a UN number. Prefer base chemicals over mixtures."""
        try:
            un_int = int(un_number)
        except (ValueError, TypeError):
            return []
        hits = self._un_map.get(un_int, [])
        if not hits:
            return []
        sorted_hits = sorted(hits, key=lambda h: len(h['name']))
        sigs = []
        w = SIGNAL_WEIGHTS['un_exact']
        for i, hit in enumerate(sorted_hits[:3]):
            raw = 1.0 if i == 0 else 0.6
            sigs.append(Signal(
                hit['id'], hit['name'], 'un_exact', raw, w,
                f"UN {un_int} → {hit['name']}"
            ))
        return sigs

    # ═══════════════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════════════

    def _fuzzy_suggestions(self, name: str, exclude_ids: set, limit: int = 3) -> list[dict]:
        """Get additional fuzzy suggestions for the UI."""
        name_lower = name.lower()
        suggestions = []
        if self._fuzzy_names:
            results = rfprocess.extract(name_lower, self._fuzzy_names, scorer=fuzz.WRatio, limit=limit + len(exclude_ids))
            for match_low, score, _idx in results:
                entry = self._fuzzy_name_to_entry.get(match_low)
                if entry and entry['id'] not in exclude_ids:
                    suggestions.append({
                        'chemical_id': entry['id'],
                        'chemical_name': entry['name'],
                        'score': round(score, 1),
                    })
                    exclude_ids.add(entry['id'])
                    if len(suggestions) >= limit:
                        break
        return suggestions

    @staticmethod
    def _build_result(chemical_id, chemical_name, method, confidence, status,
                      suggestions, signals, conflicts, field_swaps) -> dict:
        return {
            'chemical_id': chemical_id,
            'chemical_name': chemical_name,
            'match_method': method,
            'confidence': round(confidence, 4),
            'match_status': status,
            'suggestions': suggestions,
            'signals': [s.to_dict() for s in signals],
            'conflicts': conflicts,
            'field_swaps': field_swaps,
        }


# ═══════════════════════════════════════════════════════
#  Backward-compatible alias
# ═══════════════════════════════════════════════════════
ChemicalMatcher = HybridMatcher
