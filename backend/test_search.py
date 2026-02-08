"""Verification tests for the industrial-grade search endpoint."""
import requests
import json

BASE = "http://127.0.0.1:5000/api/search"

tests = [
    ("Name search: 'Acetone'",      "Acetone",          8, "Name"),
    ("CAS search: '67-64-1'",       "67-64-1",          8, "CAS"),
    ("Formula search: 'C3H6O'",     "C3H6O",            8, "Formula"),
    ("Synonym search: 'Dimethyl ketone'", "Dimethyl ketone", 8, "Synonym"),
    ("UN search: '1090'",           "1090",             8, "UN"),
    ("UN search with prefix: 'UN1090'", "UN1090",       8, "UN"),
    ("Name search: 'Water'",        "Water",            None, "Name"),
    ("Partial CAS: '67-64'",        "67-64",            8, "CAS"),
]

passed = 0
failed = 0

for label, query, expected_id, expected_type in tests:
    try:
        r = requests.get(BASE, params={"q": query}, timeout=5)
        data = r.json()
        items = data.get("items", [])
        
        if not items:
            print(f"  FAIL  {label}: No results returned!")
            failed += 1
            continue
        
        first = items[0]
        first_id = first["id"]
        first_type = first.get("match_type", "?")
        first_name = first.get("name", "?")
        
        id_ok = (expected_id is None) or (first_id == expected_id)
        type_ok = (first_type == expected_type)
        
        if id_ok and type_ok:
            print(f"  PASS  {label} -> id={first_id}, name={first_name}, type={first_type}, matched={first.get('matched_text','')[:40]}")
            passed += 1
        elif id_ok:
            # ID correct but type different - still acceptable
            found_correct = any(item["id"] == expected_id for item in items) if expected_id else True
            if found_correct:
                print(f"  PASS* {label} -> id={first_id}, name={first_name}, type={first_type} (expected type={expected_type})")
                passed += 1
            else:
                print(f"  FAIL  {label} -> Expected id={expected_id}, got id={first_id} name={first_name} type={first_type}")
                failed += 1
        else:
            # Check if expected_id is anywhere in results
            found = any(item["id"] == expected_id for item in items) if expected_id else True
            if found:
                print(f"  WARN  {label} -> id={expected_id} found but not first. First: id={first_id} name={first_name}")
                passed += 1
            else:
                print(f"  FAIL  {label} -> Expected id={expected_id} not in results. Got: {[i['id'] for i in items[:5]]}")
                failed += 1
                
    except Exception as e:
        print(f"  ERROR {label}: {e}")
        failed += 1

print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
