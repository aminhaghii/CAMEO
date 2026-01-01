"""
NOAA CAMEO Water Compatibility Verification Test
Based on official CAMEO Chemicals matrix provided by user.

Tests that our ReactivityEngine produces the same results as NOAA CAMEO.
"""

import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility

DB_PATH = os.environ.get(
    "CHEMICALS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "resources", "chemicals.db"),
)

# Test data from NOAA CAMEO Matrix
# Format: (chemical_name, chemical_id, expected_with_water)
# Expected values: 'C' = Compatible (Green), 'I-C' = Caution (Yellow), 'I' = Incompatible (Red)
TEST_CASES = [
    ("ACETONE", 8, "C"),           # Should be Green
    ("SULFURIC ACID", 5193, "I-C"), # Should be Yellow (Caution)
    ("ACETAL", 2, "C"),            # Should be Green
    ("GASOLINE", 11498, "C"),      # Should be Green
    ("ALLETHRIN", 47, "I-C"),      # Should be Yellow (has group 13 which is Caution)
    ("FILM", 17480, "C"),          # Should be Green
    ("MAGNESITE", 25039, "I-C"),   # Should be Yellow
    ("MIREX", 8861, "I-C"),        # Should be Yellow
    ("FAMPHUR", 16211, "I-C"),     # Should be Yellow (has groups 13,60 which are Caution)
    ("SILANE", 4434, "I"),         # Should be Red
    ("SODIUM PERCHLORATE", 1514, "I"), # Should be Red
    ("AMMONIUM NITRATE", 98, "I-C"),   # Should be Yellow (has group 38 which is Caution)
]

WATER_ID = 30024

# Map Compatibility enum to string codes
COMPAT_TO_CODE = {
    Compatibility.COMPATIBLE: "C",
    Compatibility.INCOMPATIBLE: "I",
    Compatibility.CAUTION: "I-C",
    Compatibility.NO_DATA: "N",
}


def run_tests():
    print(f"[INFO] Using database: {DB_PATH}")
    print(f"[INFO] Water ID: {WATER_ID}")
    print("=" * 70)
    
    engine = ReactivityEngine(DB_PATH)
    
    passed = 0
    failed = 0
    results = []
    
    for name, chem_id, expected in TEST_CASES:
        # Analyze the pair
        result = engine.analyze([chem_id, WATER_ID])
        
        # Get the compatibility from matrix[0][1] or matrix[1][0]
        if result.matrix and len(result.matrix) > 1:
            pair_result = result.matrix[0][1] if result.matrix[0][1] else result.matrix[1][0]
            if pair_result:
                actual_compat = pair_result.compatibility
                actual_code = COMPAT_TO_CODE.get(actual_compat, "?")
            else:
                actual_code = "?"
        else:
            actual_code = "?"
        
        # Compare
        status = "PASS" if actual_code == expected else "FAIL"
        symbol = "✅" if status == "PASS" else "❌"
        
        if status == "PASS":
            passed += 1
        else:
            failed += 1
        
        results.append({
            "name": name,
            "expected": expected,
            "actual": actual_code,
            "status": status,
        })
        
        print(f"{symbol} {name:25} | Expected: {expected:4} | Actual: {actual_code:4} | {status}")
    
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(TEST_CASES)} tests")
    
    # Critical safety checks
    print("\n" + "=" * 70)
    print("🔴 CRITICAL SAFETY CHECKS:")
    print("=" * 70)
    
    # Sulfuric Acid must NOT be green
    sulfuric_result = next((r for r in results if r["name"] == "SULFURIC ACID"), None)
    if sulfuric_result and sulfuric_result["actual"] == "C":
        print("❌ CRITICAL FAILURE: Sulfuric Acid + Water is GREEN! This is DANGEROUS!")
        return False
    else:
        print("✅ Sulfuric Acid + Water is NOT green (safe)")
    
    # Silane must be red
    silane_result = next((r for r in results if r["name"] == "SILANE"), None)
    if silane_result and silane_result["actual"] != "I":
        print(f"⚠️ WARNING: Silane + Water is {silane_result['actual']}, expected Red (I)")
    else:
        print("✅ Silane + Water is Red (correct)")
    
    # Acetone should be green
    acetone_result = next((r for r in results if r["name"] == "ACETONE"), None)
    if acetone_result and acetone_result["actual"] == "C":
        print("✅ Acetone + Water is Green (correct)")
    else:
        print(f"⚠️ WARNING: Acetone + Water is {acetone_result['actual'] if acetone_result else '?'}, expected Green (C)")
    
    # Gasoline should be green
    gasoline_result = next((r for r in results if r["name"] == "GASOLINE"), None)
    if gasoline_result and gasoline_result["actual"] == "C":
        print("✅ Gasoline + Water is Green (correct)")
    else:
        print(f"⚠️ WARNING: Gasoline + Water is {gasoline_result['actual'] if gasoline_result else '?'}, expected Green (C)")
    
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
