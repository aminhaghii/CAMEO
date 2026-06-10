"""
═══════════════════════════════════════════════════════════════════════════════
CAMEO CHEMICALS: REACTIVITY ENGINE
Safety-Critical Module - Based on CAMEO_MASTER_LOGIC_SPEC_V5

⚠️ WARNING: This module directly affects human safety.
Any deviation from the specified logic may result in dangerous incidents.
═══════════════════════════════════════════════════════════════════════════════
"""

import json
import logging
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import sqlite3

from .constants import (
    Compatibility, COMPATIBILITY_MAP, WATER_GROUP_ID, DB_COMPATIBILITY_MAP
)

logger = logging.getLogger(__name__)


@dataclass
class PairResult:
    """Result of analyzing a pair of chemicals"""
    chem_a_id: int
    chem_b_id: int
    chem_a_name: str
    chem_b_name: str
    compatibility: Compatibility
    hazards: List[str] = field(default_factory=list)
    gas_products: List[str] = field(default_factory=list)
    interaction_details: List[Dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class MatrixResult:
    """Complete matrix analysis result"""
    timestamp: str
    chemical_count: int
    overall_compatibility: Compatibility
    matrix: List[List[Optional[PairResult]]]
    chemicals: List[Dict]
    critical_pairs: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    audit_id: Optional[int] = None


class ReactivityEngine:
    """
    ═══════════════════════════════════════════════════════════════
    Chemical Reactivity Prediction Engine
    Based on CAMEO Chemicals (NOAA) methodology
    ═══════════════════════════════════════════════════════════════
    
    ⚠️ Safety Warning:
    This tool is for prediction only, not a guarantee.
    Always consult SDS and expert opinion.
    """
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._rule_cache: Dict[Tuple[int, int], Dict] = {}
        self._group_cache: Dict[int, List[int]] = {}
        logger.info(f"ReactivityEngine initialized with database: {db_path}")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Create database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _normalize_pair(self, g1: int, g2: int) -> Tuple[int, int]:
        """
        Normalize group IDs for unique lookup
        Always returns (smaller, larger)
        """
        return (min(g1, g2), max(g1, g2))
    
    def _get_chemical_groups(self, chemical_id: int) -> List[int]:
        """
        Get all reactive groups for a chemical
        Uses cache for optimization
        """
        if chemical_id in self._group_cache:
            return self._group_cache[chemical_id]
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Use existing mm_chemical_react table
        cursor.execute(
            "SELECT react_id FROM mm_chemical_react WHERE chem_id = ?",
            (chemical_id,)
        )
        
        groups = [row['react_id'] for row in cursor.fetchall()]
        conn.close()
        
        if not groups:
            logger.warning(f"⚠️ Chemical ID {chemical_id} has no reactive groups assigned!")
        
        self._group_cache[chemical_id] = groups
        return groups
    
    def _get_rule(self, group1_id: int, group2_id: int) -> Dict:
        """
        ═══════════════════════════════════════════════════════════
        🔴 SAFETY-CRITICAL FUNCTION
        ═══════════════════════════════════════════════════════════
        
        Get compatibility rule for a pair of groups
        
        ⚠️ CRITICAL NOTE:
        If no rule exists, return NO_DATA, NOT COMPATIBLE!
        This is FAIL-SAFE behavior.
        """
        normalized = self._normalize_pair(group1_id, group2_id)
        
        # Check cache
        if normalized in self._rule_cache:
            return self._rule_cache[normalized]
        
        # Same group = compatible
        if group1_id == group2_id:
            result = {
                'compatibility': Compatibility.COMPATIBLE,
                'hazards': [],
                'gas_products': [],
                'notes': 'Same reactive group'
            }
            self._rule_cache[normalized] = result
            return result
        
        # Query existing reactivity table
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Try both orderings since DB may not enforce order
        cursor.execute(
            """
            SELECT pair_compatibility, gas_products, hazards_documentation
            FROM reactivity
            WHERE (react1 = ? AND react2 = ?) OR (react1 = ? AND react2 = ?)
            """,
            (normalized[0], normalized[1], normalized[1], normalized[0])
        )
        
        row = cursor.fetchone()
        conn.close()
        
        if row is None:
            # ════════════════════════════════════════════════════════
            # 🔴 FAIL-SAFE: No data = Unknown, NOT Compatible!
            # ════════════════════════════════════════════════════════
            logger.warning(
                f"⚠️ No reactivity rule for groups {normalized}. "
                f"Treating as NO_DATA (Caution level) for safety."
            )
            result = {
                'compatibility': Compatibility.NO_DATA,
                'hazards': [],
                'gas_products': [],
                'notes': 'No reactivity data in database - exercise caution'
            }
        else:
            # Map DB compatibility value to our enum
            db_compat = row['pair_compatibility'] or 'Compatible'
            compat = DB_COMPATIBILITY_MAP.get(db_compat, Compatibility.NO_DATA)
            
            # Parse gas products
            gas_products = []
            if row['gas_products']:
                gas_products = [g.strip() for g in row['gas_products'].split('|') if g.strip()]
            
            # Parse hazards from documentation
            hazards = []
            hazards_doc = row['hazards_documentation'] or ''
            if hazards_doc:
                # Extract hazard types from documentation
                hazard_keywords = {
                    'fire': 'FIRE',
                    'explosion': 'EXPLOSION',
                    'heat': 'HEAT',
                    'toxic': 'TOXIC_GAS',
                    'flammable': 'FLAMMABLE_GAS',
                    'corrosive': 'CORROSIVE_GAS',
                    'violent': 'VIOLENT_REACTION',
                    'ignit': 'SPONTANEOUS_IGNITION',
                    'polymer': 'POLYMERIZATION'
                }
                doc_lower = hazards_doc.lower()
                for keyword, hazard in hazard_keywords.items():
                    if keyword in doc_lower:
                        hazards.append(hazard)
            
            # Infer hazards from gas products
            toxic_gases = ['HCN', 'H2S', 'CO', 'Cl2', 'NH3', 'NOx', 'SO2', 'HCl', 'HF']
            flammable_gases = ['H2', 'CH4', 'C2H2', 'C2H4']
            
            for gas in gas_products:
                if any(tg in gas for tg in toxic_gases):
                    if 'TOXIC_GAS' not in hazards:
                        hazards.append('TOXIC_GAS')
                if any(fg in gas for fg in flammable_gases):
                    if 'FLAMMABLE_GAS' not in hazards:
                        hazards.append('FLAMMABLE_GAS')
            
            # If incompatible but no specific hazards, add HEAT as minimum
            if compat == Compatibility.INCOMPATIBLE and not hazards:
                hazards.append('HEAT')
                hazards.append('VIOLENT_REACTION')
            
            result = {
                'compatibility': compat,
                'hazards': hazards,
                'gas_products': gas_products,
                'notes': hazards_doc
            }
        
        self._rule_cache[normalized] = result
        return result
    
    def _get_special_hazards(self, chemical_id: int) -> List[Dict]:
        """Get special hazards for a chemical (Self-Hazards)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Check special_hazards field in chemicals table
        cursor.execute(
            "SELECT special_hazards FROM chemicals WHERE id = ?",
            (chemical_id,)
        )
        
        row = cursor.fetchone()
        conn.close()
        
        hazards = []
        if row and row['special_hazards']:
            special = row['special_hazards']
            # Parse special hazards text
            hazard_types = {
                'peroxide': 'PEROXIDE_FORMER',
                'pyrophoric': 'PYROPHORIC',
                'water reactive': 'WATER_REACTIVE',
                'air reactive': 'AIR_REACTIVE',
                'explosive': 'EXPLOSIVE',
                'polymeriz': 'POLYMERIZABLE'
            }
            special_lower = special.lower()
            for keyword, hazard_type in hazard_types.items():
                if keyword in special_lower:
                    hazards.append({
                        'type': hazard_type,
                        'severity': 'high',
                        'description': special[:200],
                        'storage': None
                    })
        
        return hazards
    
    def _analyze_pair(
        self,
        chem_a_id: int,
        chem_b_id: int,
        chem_a_name: str,
        chem_b_name: str,
        groups_a: List[int],
        groups_b: List[int]
    ) -> PairResult:
        """
        ═══════════════════════════════════════════════════════════
        Analyze compatibility between two chemicals
        
        This function implements CARTESIAN PRODUCT logic:
        All combinations of groups from A with groups from B are checked.
        ═══════════════════════════════════════════════════════════
        """
        result = PairResult(
            chem_a_id=chem_a_id,
            chem_b_id=chem_b_id,
            chem_a_name=chem_a_name,
            chem_b_name=chem_b_name,
            compatibility=Compatibility.COMPATIBLE
        )
        
        max_priority = 1  # Start with Compatible (priority 1)
        all_hazards: Set[str] = set()
        all_gases: Set[str] = set()
        
        # Edge Case: Chemical without groups
        if not groups_a or not groups_b:
            result.compatibility = Compatibility.NO_DATA
            result.notes.append("One or both chemicals have no reactive group data")
            logger.warning(f"Missing group data for chemicals {chem_a_id} or {chem_b_id}")
            return result
        
        # ════════════════════════════════════════════════════════════
        # 🔄 CARTESIAN PRODUCT LOOP - Core CAMEO Logic
        # ════════════════════════════════════════════════════════════
        
        for g_a in groups_a:
            for g_b in groups_b:
                rule = self._get_rule(g_a, g_b)
                
                rule_priority = COMPATIBILITY_MAP[rule['compatibility']].priority
                
                # Track worst case
                if rule_priority > max_priority:
                    max_priority = rule_priority
                
                # Collect all hazards
                all_hazards.update(rule['hazards'])
                all_gases.update(rule['gas_products'])
                
                # Record details for non-compatible interactions
                if rule['compatibility'] != Compatibility.COMPATIBLE:
                    result.interaction_details.append({
                        'group_a_id': g_a,
                        'group_b_id': g_b,
                        'compatibility': rule['compatibility'].value,
                        'hazards': rule['hazards'],
                        'gases': rule['gas_products']
                    })
        
        # ════════════════════════════════════════════════════════════
        # Determine final result (worst case)
        # ════════════════════════════════════════════════════════════
        
        for compat, info in COMPATIBILITY_MAP.items():
            if info.priority == max_priority:
                result.compatibility = compat
                break
        
        result.hazards = list(all_hazards)
        result.gas_products = list(all_gases)
        
        # Log incompatible pairs
        if result.compatibility == Compatibility.INCOMPATIBLE:
            logger.warning(
                f"⛔️ INCOMPATIBLE: {chem_a_name} + {chem_b_name} "
                f"| Hazards: {result.hazards} | Gases: {result.gas_products}"
            )
        
        return result
    
    def _save_audit_log(
        self,
        chemical_ids: List[int],
        result: 'MatrixResult',
        user_id: Optional[int] = None
    ) -> Optional[int]:
        """Save audit log for safety tracking"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # Create audit_log table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    action_type TEXT NOT NULL,
                    user_id INTEGER,
                    chemical_ids_json TEXT,
                    result_summary TEXT,
                    hazards_found_json TEXT,
                    ip_address TEXT,
                    user_agent TEXT
                )
            """)
            
            cursor.execute(
                """
                INSERT INTO audit_log 
                (action_type, user_id, chemical_ids_json, result_summary, hazards_found_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    'analyze',
                    user_id,
                    json.dumps(chemical_ids),
                    result.overall_compatibility.value,
                    json.dumps(list(set(h for pair in result.matrix for cell in pair if cell for h in cell.hazards)))
                )
            )
            
            conn.commit()
            audit_id = cursor.lastrowid
            conn.close()
            
            logger.info(f"Audit log saved with ID: {audit_id}")
            return audit_id
        except Exception as e:
            logger.error(f"Failed to save audit log: {e}")
            return None
    
    def analyze(
        self,
        chemical_ids: List[int],
        include_water_check: bool = True,
        user_id: Optional[int] = None,
        save_audit: bool = True
    ) -> MatrixResult:
        """
        ═══════════════════════════════════════════════════════════════
        🔬 Main Analysis Function - PUBLIC API
        ═══════════════════════════════════════════════════════════════
        
        Analyze compatibility of N chemicals and produce N×N matrix
        
        Args:
            chemical_ids: List of chemical IDs to analyze
            include_water_check: Check water reactivity
            user_id: User ID for audit logging
        
        Returns:
            MatrixResult: Complete result including matrix and summary
        
        Raises:
            ValueError: If fewer than 2 chemicals provided
        """
        n = len(chemical_ids)
        
        if n < 2:
            raise ValueError("At least 2 chemicals required for analysis")
        
        if n > 20:
            logger.warning(f"⚠️ Large analysis: {n} chemicals = {n*n} matrix cells")
        
        logger.info(f"Starting analysis for {n} chemicals: {chemical_ids}")
        
        # Initialize result
        result = MatrixResult(
            timestamp=datetime.utcnow().isoformat() + 'Z',
            chemical_count=n,
            overall_compatibility=Compatibility.COMPATIBLE,
            matrix=[[None] * n for _ in range(n)],
            chemicals=[]
        )
        
        # Get chemical info and groups
        conn = self._get_connection()
        cursor = conn.cursor()
        
        chem_groups: Dict[int, List[int]] = {}
        chem_names: Dict[int, str] = {}
        
        for chem_id in chemical_ids:
            # Get chemical info
            cursor.execute(
                "SELECT id, name, synonyms, formulas FROM chemicals WHERE id = ?",
                (chem_id,)
            )
            row = cursor.fetchone()
            
            if row:
                result.chemicals.append({
                    'id': row['id'],
                    'name': row['name'],
                    'synonyms': row['synonyms'],
                    'formula': row['formulas']
                })
                chem_names[chem_id] = row['name']
            else:
                logger.error(f"Chemical ID {chem_id} not found in database!")
                result.warnings.append(f"Chemical ID {chem_id} not found")
                chem_names[chem_id] = f"Unknown ({chem_id})"
            
            # Get groups
            chem_groups[chem_id] = self._get_chemical_groups(chem_id)
        
        conn.close()
        
        # ════════════════════════════════════════════════════════════
        # 🔄 Main Matrix Building Loop
        # ════════════════════════════════════════════════════════════
        
        overall_max_priority = 1
        
        for i in range(n):
            for j in range(n):
                chem_a_id = chemical_ids[i]
                chem_b_id = chemical_ids[j]
                
                if i == j:
                    # Diagonal: Self-Interaction
                    special = self._get_special_hazards(chem_a_id)
                    
                    if special:
                        self_result = PairResult(
                            chem_a_id=chem_a_id,
                            chem_b_id=chem_a_id,
                            chem_a_name=chem_names[chem_a_id],
                            chem_b_name=chem_names[chem_a_id],
                            compatibility=Compatibility.CAUTION,
                            hazards=[h['type'] for h in special],
                            notes=[h['description'] for h in special if h.get('description')]
                        )
                        result.warnings.append(
                            f"⚠️ {chem_names[chem_a_id]} has special hazards: {', '.join(self_result.hazards)}"
                        )
                    else:
                        self_result = PairResult(
                            chem_a_id=chem_a_id,
                            chem_b_id=chem_a_id,
                            chem_a_name=chem_names[chem_a_id],
                            chem_b_name=chem_names[chem_a_id],
                            compatibility=Compatibility.COMPATIBLE
                        )
                    
                    result.matrix[i][j] = self_result
                
                elif i < j:
                    # Upper triangle: Analyze pair
                    pair_result = self._analyze_pair(
                        chem_a_id, chem_b_id,
                        chem_names[chem_a_id], chem_names[chem_b_id],
                        chem_groups[chem_a_id], chem_groups[chem_b_id]
                    )
                    
                    # Store in matrix (both [i][j] and [j][i] - symmetry)
                    result.matrix[i][j] = pair_result
                    result.matrix[j][i] = pair_result
                    
                    # Update overall worst case
                    priority = COMPATIBILITY_MAP[pair_result.compatibility].priority
                    if priority > overall_max_priority:
                        overall_max_priority = priority
                    
                    # Record critical pairs
                    if pair_result.compatibility == Compatibility.INCOMPATIBLE:
                        result.critical_pairs.append({
                            'chemicals': [chem_names[chem_a_id], chem_names[chem_b_id]],
                            'hazards': pair_result.hazards,
                            'gases': pair_result.gas_products,
                            'details': pair_result.interaction_details
                        })
        
        # ════════════════════════════════════════════════════════════
        # Water reactivity check (optional)
        # ════════════════════════════════════════════════════════════
        
        if include_water_check:
            for chem_id in chemical_ids:
                groups = chem_groups.get(chem_id, [])
                for g in groups:
                    water_rule = self._get_rule(g, WATER_GROUP_ID)
                    if water_rule['compatibility'] in (Compatibility.INCOMPATIBLE, Compatibility.CAUTION):
                        result.warnings.append(
                            f"💧 {chem_names[chem_id]} is water-reactive - "
                            f"store in dry conditions"
                        )
                        break
        
        # Determine overall compatibility
        for compat, info in COMPATIBILITY_MAP.items():
            if info.priority == overall_max_priority:
                result.overall_compatibility = compat
                break
        
        # Save audit log
        if save_audit:
            result.audit_id = self._save_audit_log(chemical_ids, result, user_id)
        
        logger.info(
            f"Analysis complete: Overall={result.overall_compatibility.value}, "
            f"Critical pairs={len(result.critical_pairs)}, Warnings={len(result.warnings)}"
        )
        
        return result
    
    def get_compatibility_info(self, compatibility: Compatibility):
        """Get display info for a compatibility level"""
        return COMPATIBILITY_MAP[compatibility]
    
    def clear_cache(self):
        """Clear cache (after database updates)"""
        self._rule_cache.clear()
        self._group_cache.clear()
        logger.info("Cache cleared")
    
    def get_statistics(self) -> Dict:
        """Get database statistics"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        cursor.execute("SELECT COUNT(*) FROM chemicals")
        stats['total_chemicals'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM reacts")
        stats['total_groups'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM reactivity")
        stats['total_rules'] = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM reactivity WHERE pair_compatibility = 'Incompatible'")
        stats['incompatible_rules'] = cursor.fetchone()[0]
        
        conn.close()
        return stats
