"""
match.py — Chemical matching engine.
Priority cascade: Exact CAS > Exact UN > Formula > Exact Name > Fuzzy Name.
"""

import difflib
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


class ChemicalMatcher:
    """
    Matches inventory rows against chemicals.db using a priority cascade.
    Caches name list for fuzzy matching performance.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._name_cache: Optional[dict[str, int]] = None  # UPPER(name) → id

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_name_cache(self):
        """Load all chemical names into memory for fuzzy matching."""
        if self._name_cache is not None:
            return
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM chemicals")
        self._name_cache = {}
        for row in cursor.fetchall():
            self._name_cache[row['name'].upper()] = row['id']
        conn.close()
        logger.info(f"Name cache loaded: {len(self._name_cache)} chemicals")

    def match(self, cleaned: dict) -> dict:
        """
        Try to match a cleaned inventory row to a chemical.

        Args:
            cleaned: dict with keys: name, cas, cas_valid, un_number, formula

        Returns:
            {
                'chemical_id': int or None,
                'chemical_name': str or None,
                'match_method': str,        # exact_cas | exact_un | formula | exact_name | fuzzy_name | synonym | unmatched
                'confidence': float,        # 0.0 - 1.0
                'match_status': str,        # matched | ambiguous | unmatched
            }
        """
        result = {
            'chemical_id': None,
            'chemical_name': None,
            'match_method': 'unmatched',
            'confidence': 0.0,
            'match_status': 'unmatched',
        }

        # ── 1. Exact CAS match ──
        cas = cleaned.get('cas')
        if cas and cleaned.get('cas_valid'):
            hit = self._match_by_cas(cas)
            if hit:
                result.update(hit)
                result['match_method'] = 'exact_cas'
                result['confidence'] = 1.0
                result['match_status'] = 'matched'
                return result

        # ── 2. Exact UN number match ──
        un = cleaned.get('un_number')
        if un:
            hit = self._match_by_un(un)
            if hit:
                result.update(hit)
                result['match_method'] = 'exact_un'
                result['confidence'] = 0.95
                result['match_status'] = 'matched'
                return result

        # ── 3. Formula match ──
        formula = cleaned.get('formula')
        if formula:
            hit = self._match_by_formula(formula)
            if hit:
                result.update(hit)
                result['match_method'] = 'formula'
                result['confidence'] = 0.85
                result['match_status'] = 'matched'
                return result

        # ── 4. Exact name match ──
        name = cleaned.get('name', '').strip()
        if name:
            hit = self._match_by_exact_name(name)
            if hit:
                result.update(hit)
                result['match_method'] = 'exact_name'
                result['confidence'] = 0.9
                result['match_status'] = 'matched'
                return result

            # ── 4b. Synonym match ──
            hit = self._match_by_synonym(name)
            if hit:
                result.update(hit)
                result['match_method'] = 'synonym'
                result['confidence'] = 0.85
                result['match_status'] = 'matched'
                return result

            # ── 5. Fuzzy name match ──
            hit = self._match_by_fuzzy_name(name)
            if hit:
                result.update(hit)
                return result

        return result

    # ── Private matching methods ──

    def _match_by_cas(self, cas: str) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name FROM chemicals c
            JOIN chemical_cas cc ON c.id = cc.chem_id
            WHERE cc.cas_id = ?
            LIMIT 1
        """, (cas,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'chemical_id': row['id'], 'chemical_name': row['name']}
        return None

    def _match_by_un(self, un_number: int) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name FROM chemicals c
            JOIN chemical_unna cu ON c.id = cu.chem_id
            WHERE cu.unna_id = ?
            LIMIT 1
        """, (un_number,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'chemical_id': row['id'], 'chemical_name': row['name']}
        return None

    def _match_by_formula(self, formula: str) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name FROM chemicals
            WHERE UPPER(formulas) = UPPER(?)
            LIMIT 1
        """, (formula,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'chemical_id': row['id'], 'chemical_name': row['name']}
        return None

    def _match_by_exact_name(self, name: str) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name FROM chemicals
            WHERE UPPER(name) = UPPER(?)
            LIMIT 1
        """, (name,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'chemical_id': row['id'], 'chemical_name': row['name']}
        return None

    def _match_by_synonym(self, name: str) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        # synonyms are pipe-delimited, search with LIKE
        cursor.execute("""
            SELECT id, name FROM chemicals
            WHERE UPPER(synonyms) LIKE ?
            LIMIT 1
        """, (f'%{name.upper()}%',))
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return None

        # Verify it's an actual synonym token, not a substring
        name_upper = name.upper()
        for row in rows:
            conn2 = self._get_conn()
            c2 = conn2.cursor()
            c2.execute("SELECT synonyms FROM chemicals WHERE id = ?", (row['id'],))
            syn_row = c2.fetchone()
            conn2.close()
            if syn_row and syn_row['synonyms']:
                tokens = [s.strip().upper() for s in syn_row['synonyms'].split('|')]
                if name_upper in tokens:
                    return {'chemical_id': row['id'], 'chemical_name': row['name']}
        return None

    def _match_by_fuzzy_name(self, name: str) -> Optional[dict]:
        self._load_name_cache()
        name_upper = name.upper()

        matches = difflib.get_close_matches(
            name_upper,
            self._name_cache.keys(),
            n=3,
            cutoff=0.7
        )

        if not matches:
            return None

        best = matches[0]
        ratio = difflib.SequenceMatcher(None, name_upper, best).ratio()

        if ratio >= 0.9:
            status = 'matched'
        elif ratio >= 0.7:
            status = 'ambiguous'
        else:
            return None

        return {
            'chemical_id': self._name_cache[best],
            'chemical_name': best,
            'match_method': 'fuzzy_name',
            'confidence': round(ratio, 3),
            'match_status': status,
        }
