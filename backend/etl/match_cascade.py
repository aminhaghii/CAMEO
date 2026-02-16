"""
match_cascade.py — CAS-First Deterministic Cascade Matching (Phase 1).

Replaces complex weighted fusion with simple priority-based cascade.
Exit on FIRST high-confidence match.
"""

import sqlite3
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List
from rapidfuzz import fuzz


class MatchFlag(Enum):
    """Warning flags for matches (not blockers)."""
    NAME_MISMATCH = "CAS/UN matched but name differs significantly"
    AMBIGUOUS_UN = "UN number matches multiple chemicals"
    LOW_CONFIDENCE = "Match confidence below 75%"


@dataclass
class MatchResult:
    """Result from cascade matching."""
    status: str  # 'CONFIRMED' | 'REVIEW' | 'UNIDENTIFIED'
    match: Optional[dict]
    confidence: float
    method: str
    flags: List[MatchFlag] = field(default_factory=list)
    reason: Optional[str] = None


class CascadeMatcher:
    """
    CAS-First Deterministic Cascade Matcher.
    
    Priority (exit on first match):
    1. CAS exact → CONFIRMED (1.0)
    2. UN exact (single) → CONFIRMED (0.98)
    3. Formula + Name fuzzy >85% → CONFIRMED (0.90)
    4. Synonym exact → CONFIRMED (0.95)
    5. Name fuzzy 70-90% → REVIEW (0.70-0.75)
    6. Semantic match >85% → REVIEW (0.70)
    7. None → UNIDENTIFIED
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = None
    
    def _get_conn(self) -> sqlite3.Connection:
        if not self._conn:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def match(self, cleaned: dict) -> MatchResult:
        """
        Run cascade matching on cleaned row.
        
        Args:
            cleaned: dict with keys: name, cas, cas_valid, formula, un_number
        
        Returns:
            MatchResult with status, match, confidence, method, flags
        """
        conn = self._get_conn()
        
        cas = cleaned.get('cas', '').strip()
        cas_valid = cleaned.get('cas_valid', False)
        name = cleaned.get('name', '').strip()
        formula = cleaned.get('formula', '').strip()
        un_number = cleaned.get('un_number')
        
        flags = []
        
        # ═══════════════════════════════════════════════════
        # PRIORITY 1: CAS EXACT MATCH
        # ═══════════════════════════════════════════════════
        if cas and cas_valid:
            # Query chemical_cas table for exact CAS match
            cursor = conn.execute("""
                SELECT c.id, c.name, cc.cas_id
                FROM chemicals c
                JOIN chemical_cas cc ON c.id = cc.chem_id
                WHERE cc.cas_id = ?
                LIMIT 1
            """, (cas,))
            row = cursor.fetchone()
            
            if row:
                match = dict(row)
                # Check name consistency
                if name and fuzz.ratio(name.lower(), match['name'].lower()) < 60:
                    flags.append(MatchFlag.NAME_MISMATCH)
                
                return MatchResult(
                    status='CONFIRMED',
                    match=match,
                    confidence=1.0,
                    method='cas_exact',
                    flags=flags,
                    reason=f"CAS exact match: {cas}"
                )
        
        # ═══════════════════════════════════════════════════
        # PRIORITY 2: UN EXACT MATCH
        # ═══════════════════════════════════════════════════
        if un_number:
            # Query chemical_unna table
            cursor = conn.execute("""
                SELECT c.id, c.name, cu.unna_id
                FROM chemicals c
                JOIN chemical_unna cu ON c.id = cu.chem_id
                WHERE cu.unna_id = ?
            """, (un_number,))
            matches = cursor.fetchall()
            
            if len(matches) == 1:
                match = dict(matches[0])
                return MatchResult(
                    status='CONFIRMED',
                    match=match,
                    confidence=0.98,
                    method='un_exact',
                    flags=flags,
                    reason=f"UN exact match: {un_number}"
                )
            elif len(matches) > 1:
                flags.append(MatchFlag.AMBIGUOUS_UN)
                # Disambiguate with name
                if name:
                    best = max(matches, key=lambda m: fuzz.ratio(name.lower(), m['name'].lower()))
                    name_score = fuzz.ratio(name.lower(), best['name'].lower())
                    if name_score > 80:
                        return MatchResult(
                            status='CONFIRMED',
                            match=dict(best),
                            confidence=0.95,
                            method='un_exact_disambiguated',
                            flags=flags,
                            reason=f"UN {un_number} disambiguated by name"
                        )
                    else:
                        # Multiple UN matches, can't disambiguate
                        return MatchResult(
                            status='REVIEW',
                            match=[dict(m) for m in matches],
                            confidence=0.70,
                            method='un_ambiguous',
                            flags=flags,
                            reason=f"UN {un_number} matches {len(matches)} chemicals"
                        )
        
        # ═══════════════════════════════════════════════════
        # PRIORITY 3: FORMULA + NAME FUZZY
        # ═══════════════════════════════════════════════════
        if formula and name:
            # Normalize formula
            formula_norm = formula.replace(' ', '').lower()
            cursor = conn.execute("""
                SELECT id, name, formulas
                FROM chemicals
                WHERE LOWER(REPLACE(formulas, ' ', '')) LIKE ?
            """, (f'%{formula_norm}%',))
            formula_matches = cursor.fetchall()
            
            for match in formula_matches:
                name_score = fuzz.ratio(name.lower(), match['name'].lower())
                if name_score > 85:
                    return MatchResult(
                        status='CONFIRMED',
                        match=dict(match),
                        confidence=0.90,
                        method='formula_name_match',
                        flags=flags,
                        reason=f"Formula + name fuzzy {name_score}%"
                    )
        
        # ═══════════════════════════════════════════════════
        # PRIORITY 4: SYNONYM EXACT MATCH
        # ═══════════════════════════════════════════════════
        if name:
            cursor = conn.execute("""
                SELECT id, name, synonyms
                FROM chemicals
                WHERE LOWER(synonyms) LIKE ?
                LIMIT 1
            """, (f'%{name.lower()}%',))
            row = cursor.fetchone()
            
            if row:
                # Verify it's an exact synonym match (not just substring)
                synonyms = row['synonyms'].lower().split('|')
                if name.lower() in synonyms:
                    return MatchResult(
                        status='CONFIRMED',
                        match=dict(row),
                        confidence=0.95,
                        method='synonym_exact',
                        flags=flags,
                        reason=f"Synonym exact match"
                    )
        
        # ═══════════════════════════════════════════════════
        # PRIORITY 5-6: FUZZY NAME MATCHING
        # ═══════════════════════════════════════════════════
        if name:
            # Get all chemical names for fuzzy matching
            cursor = conn.execute("SELECT id, name FROM chemicals")
            all_chemicals = cursor.fetchall()
            
            best_match = None
            best_score = 0
            
            for chem in all_chemicals:
                score = fuzz.WRatio(name.lower(), chem['name'].lower())
                if score > best_score:
                    best_score = score
                    best_match = chem
            
            if best_score >= 90:
                return MatchResult(
                    status='REVIEW',
                    match=dict(best_match),
                    confidence=0.75,
                    method='name_fuzzy_high',
                    flags=flags,
                    reason=f"Name fuzzy {best_score}%"
                )
            elif best_score >= 70:
                flags.append(MatchFlag.LOW_CONFIDENCE)
                return MatchResult(
                    status='REVIEW',
                    match=dict(best_match),
                    confidence=0.60,
                    method='name_fuzzy_medium',
                    flags=flags,
                    reason=f"Name fuzzy {best_score}%"
                )
        
        # ═══════════════════════════════════════════════════
        # PRIORITY 7: NO MATCH
        # ═══════════════════════════════════════════════════
        return MatchResult(
            status='UNIDENTIFIED',
            match=None,
            confidence=0.0,
            method='no_match',
            flags=flags,
            reason="No matching signals found"
        )
    
    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
