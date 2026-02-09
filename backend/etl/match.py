"""
match.py — Advanced Chemical Matching Engine (ETL v2).

Waterfall Resolution Logic:
  Step 1: CAS Registry Number (Gold Standard) — exact + stripped comparison
  Step 2: Exact Name Match (case-insensitive) against name + synonyms
  Step 3: Normalized String Match (remove all non-alphanumeric)
  Step 4: Fuzzy Match (rapidfuzz Token Set Ratio with strict thresholds)

Anti-Hallucination: NEVER creates new chemicals. Output is always
a chemical_id that EXISTS in chemicals.db, or None.

Statuses:
  MATCHED          — confidence > 95% (auto-accept)
  REVIEW_REQUIRED  — confidence 80-95% (human review)
  UNIDENTIFIED     — confidence < 80% (red flag)
"""

import re
import logging
import sqlite3
from typing import Optional

from rapidfuzz import fuzz, process as rfprocess

logger = logging.getLogger(__name__)


def _normalize_for_comparison(s: str) -> str:
    """Remove all non-alphanumeric characters and lowercase for loose comparison."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


class ChemicalMatcher:
    """
    Matches inventory rows against chemicals.db using a prioritized waterfall.
    Caches DB data in memory for performance.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Caches (loaded lazily)
        self._cas_map: Optional[dict[str, dict]] = None       # stripped_cas → {id, name, cas_formatted}
        self._name_map: Optional[dict[str, dict]] = None       # UPPER(name) → {id, name}
        self._norm_map: Optional[dict[str, dict]] = None       # normalized(name) → {id, name}
        self._synonym_map: Optional[dict[str, dict]] = None    # UPPER(synonym_token) → {id, name}
        self._fuzzy_choices: Optional[list[tuple]] = None      # [(name, id), ...] for rapidfuzz

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ═══════════════════════════════════════════════════════
    #  Cache loading
    # ═══════════════════════════════════════════════════════

    def _ensure_caches(self):
        """Load all caches from DB once."""
        if self._cas_map is not None:
            return
        conn = self._get_conn()
        cursor = conn.cursor()

        # ── CAS cache ──
        self._cas_map = {}
        cursor.execute("""
            SELECT cc.cas_id, c.id, c.name
            FROM chemical_cas cc
            JOIN chemicals c ON c.id = cc.chem_id
        """)
        for row in cursor.fetchall():
            cas_raw = row['cas_id']
            stripped = re.sub(r'[\s\-]', '', cas_raw)
            self._cas_map[stripped] = {
                'id': row['id'], 'name': row['name'], 'cas': cas_raw
            }

        # ── Name + Synonym + Normalized caches ──
        self._name_map = {}
        self._norm_map = {}
        self._synonym_map = {}
        fuzzy_list = []

        cursor.execute("SELECT id, name, synonyms FROM chemicals")
        for row in cursor.fetchall():
            cid = row['id']
            name = row['name'] or ''
            name_upper = name.upper().strip()
            name_norm = _normalize_for_comparison(name)

            entry = {'id': cid, 'name': name}

            if name_upper:
                self._name_map[name_upper] = entry
            if name_norm:
                self._norm_map[name_norm] = entry

            fuzzy_list.append((name, cid))

            # Parse synonyms (pipe-delimited)
            synonyms = row['synonyms'] or ''
            for syn in synonyms.split('|'):
                syn = syn.strip()
                if syn:
                    syn_upper = syn.upper()
                    syn_norm = _normalize_for_comparison(syn)
                    if syn_upper not in self._synonym_map:
                        self._synonym_map[syn_upper] = entry
                    if syn_norm and syn_norm not in self._norm_map:
                        self._norm_map[syn_norm] = entry

        self._fuzzy_choices = fuzzy_list
        conn.close()
        logger.info(
            f"Caches loaded: {len(self._cas_map)} CAS, "
            f"{len(self._name_map)} names, "
            f"{len(self._norm_map)} normalized, "
            f"{len(self._synonym_map)} synonyms"
        )

    # ═══════════════════════════════════════════════════════
    #  Main match method
    # ═══════════════════════════════════════════════════════

    def match(self, cleaned: dict) -> dict:
        """
        Waterfall resolution for a cleaned inventory row.

        Returns:
            {
                'chemical_id': int or None,       # MUST exist in DB or be None
                'chemical_name': str or None,      # Official name from DB
                'match_method': str,               # exact_cas | exact_name | exact_synonym | normalized | fuzzy_XX | unmatched
                'confidence': float,               # 0.0 - 1.0
                'match_status': str,               # MATCHED | REVIEW_REQUIRED | UNIDENTIFIED
                'suggestions': list[dict],         # Top 3 fuzzy suggestions for review
            }
        """
        self._ensure_caches()

        result = {
            'chemical_id': None,
            'chemical_name': None,
            'match_method': 'unmatched',
            'confidence': 0.0,
            'match_status': 'UNIDENTIFIED',
            'suggestions': [],
        }

        # ── Step 1: CAS Registry Number (Gold Standard) ──
        cas = cleaned.get('cas')
        if cas and cleaned.get('cas_valid'):
            hit = self._match_by_cas(cas)
            if hit:
                result.update(hit)
                result['match_method'] = 'exact_cas'
                result['confidence'] = 1.0
                result['match_status'] = 'MATCHED'
                return result

        # Also try cas_scanned (found via regex in other columns)
        cas_scanned = cleaned.get('cas_scanned')
        if cas_scanned and cas_scanned != cas:
            hit = self._match_by_cas(cas_scanned)
            if hit:
                result.update(hit)
                result['match_method'] = 'exact_cas'
                result['confidence'] = 1.0
                result['match_status'] = 'MATCHED'
                return result

        name = (cleaned.get('name') or '').strip()
        if not name:
            return result

        # ── Step 2: Exact Name Match (case-insensitive) ──
        hit = self._match_exact_name(name)
        if hit:
            result.update(hit)
            result['match_method'] = 'exact_name'
            result['confidence'] = 1.0
            result['match_status'] = 'MATCHED'
            return result

        # ── Step 2b: Exact Synonym Match ──
        hit = self._match_exact_synonym(name)
        if hit:
            result.update(hit)
            result['match_method'] = 'exact_synonym'
            result['confidence'] = 0.98
            result['match_status'] = 'MATCHED'
            return result

        # ── Step 3: Normalized String Match ──
        hit = self._match_normalized(name)
        if hit:
            result.update(hit)
            result['match_method'] = 'normalized'
            result['confidence'] = 0.96
            result['match_status'] = 'MATCHED'
            return result

        # ── Step 4: Fuzzy Match (rapidfuzz Token Set Ratio) ──
        fuzzy_result = self._match_fuzzy(name)
        if fuzzy_result:
            result.update(fuzzy_result)
            return result

        return result

    # ═══════════════════════════════════════════════════════
    #  Waterfall step implementations
    # ═══════════════════════════════════════════════════════

    def _match_by_cas(self, cas: str) -> Optional[dict]:
        """Match by CAS — strip dashes/spaces for comparison."""
        stripped = re.sub(r'[\s\-]', '', cas)
        hit = self._cas_map.get(stripped)
        if hit:
            return {'chemical_id': hit['id'], 'chemical_name': hit['name']}
        return None

    def _match_exact_name(self, name: str) -> Optional[dict]:
        """Case-insensitive exact name match."""
        hit = self._name_map.get(name.upper().strip())
        if hit:
            return {'chemical_id': hit['id'], 'chemical_name': hit['name']}
        return None

    def _match_exact_synonym(self, name: str) -> Optional[dict]:
        """Case-insensitive exact synonym token match."""
        hit = self._synonym_map.get(name.upper().strip())
        if hit:
            return {'chemical_id': hit['id'], 'chemical_name': hit['name']}
        return None

    def _match_normalized(self, name: str) -> Optional[dict]:
        """
        Normalized string match — remove all non-alphanumeric.
        Example: "Ethyl-Alcohol" → "ethylalcohol"
        """
        norm = _normalize_for_comparison(name)
        if not norm:
            return None
        hit = self._norm_map.get(norm)
        if hit:
            return {'chemical_id': hit['id'], 'chemical_name': hit['name']}
        return None

    def _match_fuzzy(self, name: str) -> Optional[dict]:
        """
        Fuzzy matching using rapidfuzz WRatio (case-insensitive).
        WRatio combines multiple strategies and works well for both
        single-token and multi-token chemical names.

        Thresholds:
          > 95%  → MATCHED (auto-accept)
          80-95% → REVIEW_REQUIRED (yellow, "Did you mean?")
          < 80%  → UNIDENTIFIED (red)
        Always returns top 3 suggestions for UI.
        """
        if not self._fuzzy_choices:
            return None

        # Build lowercase choices for case-insensitive matching
        # Map: lowercase_name → (original_name, id)
        lower_choices = []
        lower_to_orig = {}
        for orig_name, cid in self._fuzzy_choices:
            low = orig_name.lower()
            lower_choices.append(low)
            lower_to_orig[low] = (orig_name, cid)

        query_lower = name.lower()

        results = rfprocess.extract(
            query_lower,
            lower_choices,
            scorer=fuzz.WRatio,
            limit=5,
        )

        if not results:
            return None

        # Build suggestions list (top 3) with original names
        suggestions = []
        for match_lower, score, _idx in results[:3]:
            orig_name, cid = lower_to_orig[match_lower]
            suggestions.append({
                'chemical_id': cid,
                'chemical_name': orig_name,
                'score': round(score, 1),
            })

        best_lower, best_score, _ = results[0]
        best_orig, best_id = lower_to_orig[best_lower]
        confidence = round(best_score / 100.0, 3)
        method = f'fuzzy_{int(best_score)}'

        if best_score > 95:
            return {
                'chemical_id': best_id,
                'chemical_name': best_orig,
                'match_method': method,
                'confidence': confidence,
                'match_status': 'MATCHED',
                'suggestions': suggestions,
            }
        elif best_score >= 80:
            return {
                'chemical_id': best_id,
                'chemical_name': best_orig,
                'match_method': method,
                'confidence': confidence,
                'match_status': 'REVIEW_REQUIRED',
                'suggestions': suggestions,
            }
        else:
            # Below threshold — UNIDENTIFIED, but still provide suggestions
            return {
                'chemical_id': None,
                'chemical_name': None,
                'match_method': 'unmatched',
                'confidence': confidence,
                'match_status': 'UNIDENTIFIED',
                'suggestions': suggestions,
            }
