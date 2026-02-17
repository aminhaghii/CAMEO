"""
last_ditch_recovery.py — Task 2: Last-Ditch Recovery (Post-processing Fallback)

Attempts to recover UNIDENTIFIED rows via deep row scanning and cross-column lookup.
This is a surgical helper function injected after the main matching engine fails.
"""

import re
import logging
import sqlite3
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Regex patterns for CAS and UN numbers
CAS_REGEX = re.compile(r'\b(\d{2,7}-\d{2}-\d)\b')
UN_REGEX = re.compile(r'\b(UN\s*)?(\d{4})\b', re.IGNORECASE)


def attempt_last_ditch_recovery(
    row_dict: Dict[str, Any],
    cleaned: Dict[str, Any],
    chemicals_db_path: str,
    batch_id: str = '',
    row_index: int = 0
) -> Optional[Dict[str, Any]]:
    """
    Last-ditch recovery for UNIDENTIFIED rows.
    
    Strategy A (Regex Scan):
    - Scan ALL cell values in the row (ignoring column mapping)
    - Look for CAS patterns (e.g., 67-64-1) or UN patterns (e.g., UN1230, 1230)
    - If found, attempt direct DB lookup
    
    Strategy B (Cross-Column Lookup):
    - If 'CAS' column contains text (not a valid CAS), try treating it as a chemical name
    - If 'Name' column contains a CAS-like pattern, try treating it as CAS
    
    Args:
        row_dict: Raw row data (all columns)
        cleaned: Cleaned row data from validate_row
        chemicals_db_path: Path to CAMEO database
        batch_id: Batch ID for logging
        row_index: Row index for logging
        
    Returns:
        Match result dict if recovered, None if recovery failed
        
    Safety:
    - Wrapped in try/except to prevent pipeline crashes
    - All matches marked as REVIEW_REQUIRED (never MATCHED)
    - Adds warning: "Recovered via deep row scan"
    """
    
    try:
        conn = sqlite3.connect(chemicals_db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # ═══════════════════════════════════════════════════════
        #  Strategy A: Regex Scan Across All Cells
        # ═══════════════════════════════════════════════════════
        
        # Scan all cell values for CAS patterns
        for col_name, cell_value in row_dict.items():
            if not cell_value or str(cell_value).strip() in ('', 'nan', 'None'):
                continue
            
            cell_str = str(cell_value).strip()
            
            # Look for CAS pattern
            cas_match = CAS_REGEX.search(cell_str)
            if cas_match:
                cas_candidate = cas_match.group(1)
                
                # Try to find in database
                cursor.execute("""
                    SELECT c.id, c.name
                    FROM chemicals c
                    JOIN chemical_cas cc ON c.id = cc.chem_id
                    WHERE cc.cas_id = ? OR cc.cas_nodash = ?
                    LIMIT 1
                """, (cas_candidate, cas_candidate.replace('-', '')))
                
                result = cursor.fetchone()
                if result:
                    logger.info(
                        f"[Batch {batch_id[:8]}] Row {row_index}: Last-ditch recovery via CAS scan "
                        f"found '{cas_candidate}' in column '{col_name}' → {result['name']}"
                    )
                    conn.close()
                    return {
                        'chemical_id': result['id'],
                        'chemical_name': result['name'],
                        'match_method': 'last_ditch_cas_scan',
                        'confidence': 0.75,  # Lower confidence
                        'match_status': 'REVIEW_REQUIRED',
                        'suggestions': [],
                        'signals': [],
                        'conflicts': [],
                        'field_swaps': [f"CAS found in unexpected column: {col_name}"],
                        'recovery_note': f"Recovered via deep row scan: CAS '{cas_candidate}' found in column '{col_name}'"
                    }
            
            # Look for UN pattern
            un_match = UN_REGEX.search(cell_str)
            if un_match:
                un_candidate = un_match.group(2)  # Extract just the digits
                
                # Try to find in database (note: chemical_un table may not exist)
                try:
                    cursor.execute("""
                        SELECT c.id, c.name
                        FROM chemicals c
                        JOIN chemical_un cu ON c.id = cu.chem_id
                        WHERE cu.un_code = ?
                        LIMIT 1
                    """, (int(un_candidate),))
                    
                    result = cursor.fetchone()
                    if result:
                        logger.info(
                            f"[Batch {batch_id[:8]}] Row {row_index}: Last-ditch recovery via UN scan "
                            f"found 'UN{un_candidate}' in column '{col_name}' → {result['name']}"
                        )
                        conn.close()
                        return {
                            'chemical_id': result['id'],
                            'chemical_name': result['name'],
                            'match_method': 'last_ditch_un_scan',
                            'confidence': 0.75,
                            'match_status': 'REVIEW_REQUIRED',
                            'suggestions': [],
                            'signals': [],
                            'conflicts': [],
                            'field_swaps': [f"UN number found in unexpected column: {col_name}"],
                            'recovery_note': f"Recovered via deep row scan: UN{un_candidate} found in column '{col_name}'"
                        }
                except sqlite3.OperationalError:
                    # chemical_un table doesn't exist, skip UN lookup
                    pass
        
        # ═══════════════════════════════════════════════════════
        #  Strategy B: Cross-Column Lookup
        # ═══════════════════════════════════════════════════════
        
        # B1: If CAS column contains text (not a valid CAS), try as chemical name
        cas_value = cleaned.get('cas', '').strip()
        if cas_value and not CAS_REGEX.match(cas_value):
            # CAS column has text, might be a chemical name
            cursor.execute("""
                SELECT id, name
                FROM chemicals
                WHERE UPPER(name) = UPPER(?)
                LIMIT 1
            """, (cas_value,))
            
            result = cursor.fetchone()
            if result:
                logger.info(
                    f"[Batch {batch_id[:8]}] Row {row_index}: Last-ditch recovery via cross-column "
                    f"lookup: CAS column contains chemical name '{cas_value}' → {result['name']}"
                )
                conn.close()
                return {
                    'chemical_id': result['id'],
                    'chemical_name': result['name'],
                    'match_method': 'last_ditch_cross_column',
                    'confidence': 0.70,
                    'match_status': 'REVIEW_REQUIRED',
                    'suggestions': [],
                    'signals': [],
                    'conflicts': [],
                    'field_swaps': ['Chemical name found in CAS column'],
                    'recovery_note': f"Recovered via cross-column lookup: Name '{cas_value}' found in CAS column"
                }
        
        # B2: If Name column contains CAS pattern, try as CAS
        name_value = cleaned.get('name', '').strip()
        if name_value:
            cas_in_name = CAS_REGEX.search(name_value)
            if cas_in_name:
                cas_candidate = cas_in_name.group(1)
                
                cursor.execute("""
                    SELECT c.id, c.name
                    FROM chemicals c
                    JOIN chemical_cas cc ON c.id = cc.chem_id
                    WHERE cc.cas_id = ? OR cc.cas_nodash = ?
                    LIMIT 1
                """, (cas_candidate, cas_candidate.replace('-', '')))
                
                result = cursor.fetchone()
                if result:
                    logger.info(
                        f"[Batch {batch_id[:8]}] Row {row_index}: Last-ditch recovery via cross-column "
                        f"lookup: CAS '{cas_candidate}' found in Name column → {result['name']}"
                    )
                    conn.close()
                    return {
                        'chemical_id': result['id'],
                        'chemical_name': result['name'],
                        'match_method': 'last_ditch_cross_column',
                        'confidence': 0.70,
                        'match_status': 'REVIEW_REQUIRED',
                        'suggestions': [],
                        'signals': [],
                        'conflicts': [],
                        'field_swaps': ['CAS number found in Name column'],
                        'recovery_note': f"Recovered via cross-column lookup: CAS '{cas_candidate}' found in Name column"
                    }
        
        # No recovery possible
        conn.close()
        return None
    
    except Exception as e:
        # Safety: Never crash the pipeline
        logger.warning(
            f"[Batch {batch_id[:8]}] Row {row_index}: Last-ditch recovery failed (non-critical): {e}"
        )
        return None
