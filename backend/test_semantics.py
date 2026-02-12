"""
Test script for the Semantic Token Classifier & Safety Veto Engine.
Traces the required validation cases through the new logic.
"""
import sys
sys.path.insert(0, '.')

from etl.semantics import (
    classify_token, classify_name, semantic_score,
    TokenRole, extract_base_tokens, extract_salt_tokens,
    has_safety_context, has_hazard_tokens, is_pharma_name,
    is_likely_product_code, is_plausible_cas,
)

def test_token_classification():
    """Test individual token classification."""
    print("=" * 60)
    print("TEST 1: Token Classification")
    print("=" * 60)

    cases = [
        # (token, expected_role)
        ("Gluconate", TokenRole.BASE),
        ("Atorvastatin", TokenRole.BASE),
        ("Benzene", TokenRole.BASE),
        ("Sodium", TokenRole.SALT),
        ("Calcium", TokenRole.SALT),
        ("HCl", TokenRole.SALT),
        ("Sulfate", TokenRole.SALT),
        ("Powder", TokenRole.FORM),
        ("Pellet", TokenRole.FORM),
        ("Liquid", TokenRole.FORM),
        ("Capsule", TokenRole.FORM),
        ("USP", TokenRole.GRADE),
        ("BP", TokenRole.GRADE),
        ("Grade", TokenRole.GRADE),
        ("39%", TokenRole.CONC),
        ("50mg/ml", TokenRole.CONC),
        ("96%", TokenRole.CONC),
        ("Flavor", TokenRole.SAFETY),
        ("Wax", TokenRole.SAFETY),
        ("Edible", TokenRole.GRADE),  # edible is in GRADE
        ("Phosphorus", TokenRole.HAZARD),
        ("Cyanide", TokenRole.SALT),  # cyanide is a salt anion
        ("Arsenic", TokenRole.HAZARD),
        ("E330", TokenRole.BASE),  # E-numbers are identity
    ]

    passed = 0
    for token, expected in cases:
        result = classify_token(token)
        ok = "PASS" if result == expected else "FAIL"
        if ok == "PASS":
            passed += 1
        else:
            print(f"  {ok}: classify_token('{token}') = {result.value}, expected {expected.value}")
    print(f"  {passed}/{len(cases)} passed")
    print()


def test_name_classification():
    """Test full name classification."""
    print("=" * 60)
    print("TEST 2: Name Classification")
    print("=" * 60)

    names = [
        "White Wax",
        "Zinc Gluconate",
        "Arachis Oil",
        "Strawberry Flavour",
        "Atorvastatin Calcium",
        "Venlafaxine HCl pellet 39%",
        "Vitamin E powder 50%",
        "Gelatin Capsule",
        "Glyphosate 48% SL",
        "Ammonium Nitrate",
        "Crude Oil",
        "Citric Acid E330",
    ]

    for name in names:
        tokens = classify_name(name)
        bases = extract_base_tokens(tokens)
        salts = extract_salt_tokens(tokens)
        safety = has_safety_context(tokens)
        print(f"  '{name}':")
        for t in tokens:
            print(f"    {t.text:20s} → {t.role.value}")
        print(f"    BASE: {bases}, SALT: {salts}, SAFETY: {safety}")
        print()


def test_semantic_scoring():
    """Test semantic scoring with the required validation cases."""
    print("=" * 60)
    print("TEST 3: Semantic Scoring (Validation Cases)")
    print("=" * 60)

    cases = [
        # (input, candidate, should_veto, expected_behavior)
        ("White Wax", "PHOSPHORUS, WHITE", True,
         "SAFETY(Wax) vs HAZARD(Phosphorus) → VETO"),
        ("White Wax", "PARAFFIN WAX", False,
         "SAFETY(Wax) matches SAFETY(Wax) → OK"),
        ("Gelatin Capsule", "CHLOROPICRIN", True,
         "SAFETY(Gelatin,Capsule) vs HAZARD(Chloropicrin) → VETO"),
        ("Strawberry Flavour", "CALCIUM ARSENATE", True,
         "SAFETY(Flavour) vs HAZARD(Arsenate) → VETO"),
        ("Arachis Oil", "AMMONIUM NITRATE-FUEL OIL MIXTURE", True,
         "Edible oil context veto: arachis oil vs fuel oil mixture"),
        ("Zinc Gluconate", "ZINC CHLORIDE", False,
         "No veto, but low BASE overlap (Gluconate vs Chloride)"),
        ("Zinc Gluconate", "ZINC GLUCONATE", False,
         "Perfect match: BASE + SALT overlap"),
        ("Atorvastatin Calcium", "CALCIUM CYANAMIDE", False,
         "BASE mismatch: Atorvastatin not in candidate"),
        ("Rabeprazole Sodium", "SODIUM AZIDE", True,
         "Pharma name + HAZARD(Azide) → VETO"),
        ("Venlafaxine HCl pellet 39%", "CALCIUM HYPOCHLORITE (>39%)", True,
         "Pharma name + HAZARD(Hypochlorite) → VETO"),
        ("Vitamin E powder 50%", "PERCHLORIC ACID (>50%)", True,
         "SAFETY(Vitamin) vs HAZARD(Perchloric) → VETO"),
        ("Glyphosate 48% SL", "GLYPHOSATE", False,
         "BASE match: Glyphosate"),
        ("Crude Oil", "CRUDE OIL", False,
         "Exact BASE match"),
        ("Citric Acid E330", "CITRIC ACID", False,
         "BASE match: Citric + Acid"),
    ]

    passed = 0
    for input_name, candidate, expect_veto, description in cases:
        result = semantic_score(input_name, candidate)
        veto_ok = result['vetoed'] == expect_veto
        status = "PASS" if veto_ok else "FAIL"
        if veto_ok:
            passed += 1
        print(f"  {status}: '{input_name}' vs '{candidate}'")
        print(f"    Score={result['score']:.2f}, Vetoed={result['vetoed']}, "
              f"Base={result['base_overlap']}, Salt={result['salt_overlap']}")
        if result['veto_reason']:
            print(f"    Veto: {result['veto_reason']}")
        if not veto_ok:
            print(f"    EXPECTED veto={expect_veto}, GOT veto={result['vetoed']}")
        print(f"    [{description}]")
        print()

    print(f"  {passed}/{len(cases)} passed")
    print()


def test_cas_validation():
    """Test strict CAS validation."""
    print("=" * 60)
    print("TEST 4: Strict CAS Validation")
    print("=" * 60)

    cases = [
        # (input, is_product_code, is_plausible)
        ("1112420015", True, None),   # Product code
        ("1112110005", True, None),   # Product code
        ("1111120003", True, None),   # Product code
        ("7664939", False, None),     # Real CAS digits (H2SO4)
        ("67-64-1", None, True),      # Real CAS (Acetone)
        ("50-00-0", None, True),      # Real CAS (Formaldehyde)
        ("1234567-89-0", None, False), # Too long, likely fake
    ]

    passed = 0
    for raw, expect_prod, expect_plaus in cases:
        if expect_prod is not None:
            result = is_likely_product_code(raw)
            ok = result == expect_prod
            status = "PASS" if ok else "FAIL"
            if ok: passed += 1
            print(f"  {status}: is_likely_product_code('{raw}') = {result} (expected {expect_prod})")
        if expect_plaus is not None:
            result = is_plausible_cas(raw)
            ok = result == expect_plaus
            status = "PASS" if ok else "FAIL"
            if ok: passed += 1
            print(f"  {status}: is_plausible_cas('{raw}') = {result} (expected {expect_plaus})")

    print(f"\n  Total checks passed: {passed}")
    print()


def test_pharma_detection():
    """Test pharma drug name detection."""
    print("=" * 60)
    print("TEST 5: Pharma Name Detection")
    print("=" * 60)

    cases = [
        ("Atorvastatin", True),
        ("Rabeprazole", True),
        ("Venlafaxine", True),  # ends in -faxine (modern drug stem)
        ("Omeprazole", True),
        ("Amoxicillin", True),
        ("Sodium Chloride", False),
        ("Benzene", False),
    ]

    passed = 0
    for name, expected in cases:
        result = is_pharma_name(name)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        if ok: passed += 1
        print(f"  {status}: is_pharma_name('{name}') = {result} (expected {expected})")

    print(f"\n  {passed}/{len(cases)} passed")
    print()


if __name__ == '__main__':
    test_token_classification()
    test_name_classification()
    test_semantic_scoring()
    test_cas_validation()
    test_pharma_detection()
    print("=" * 60)
    print("ALL TESTS COMPLETE")
    print("=" * 60)
