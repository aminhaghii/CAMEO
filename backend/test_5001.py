import requests

BASE = "http://127.0.0.1:5001/api/search"

tests = [
    ("Name: Acetone",        "Acetone",          8),
    ("CAS: 67-64-1",         "67-64-1",          8),
    ("Formula: C3H6O",       "C3H6O",            8),
    ("Synonym: Dimethyl ketone", "Dimethyl ketone", 8),
    ("UN: 1090",             "1090",             8),
    ("UN prefix: UN1090",    "UN1090",           8),
    ("Name: Water",          "Water",            None),
    ("Partial CAS: 67-64",   "67-64",            8),
]

for label, query, expected_id in tests:
    try:
        r = requests.get(BASE, params={"q": query}, timeout=5)
        d = r.json()
        items = d.get("items", [])
        err = d.get("error", "")
        if err:
            print(f"  FAIL  {label}: error={err}")
            continue
        if not items:
            print(f"  FAIL  {label}: 0 results")
            continue
        first = items[0]
        found = expected_id is None or any(i["id"] == expected_id for i in items)
        top_match = first["id"] == expected_id if expected_id else True
        status = "PASS" if top_match else ("WARN" if found else "FAIL")
        print(f"  {status}  {label} -> #{first['id']} {first['name']} [{first.get('match_type','')}] ({len(items)} results)")
    except Exception as e:
        print(f"  ERROR {label}: {e}")
