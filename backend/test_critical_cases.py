"""Verify the critical failure cases from the mission are now fixed."""
import sqlite3, json

conn = sqlite3.connect('data/user.db')
c = conn.cursor()
batch_id = c.execute('SELECT id FROM inventory_batches ORDER BY created_at DESC LIMIT 1').fetchone()[0]

# Get all rows
c.execute('''SELECT cleaned_data, match_status, confidence, chemical_id,
             conflicts_json, match_method, suggestions, signals_json
             FROM inventory_staging WHERE batch_id=?''', (batch_id,))
rows = c.fetchall()

def get_matched_name(row):
    """Extract matched chemical name from suggestions or signals."""
    chem_id = row[3]
    if not chem_id:
        return 'None'
    sug = json.loads(row[6]) if row[6] else []
    for s in sug:
        if s.get('chemical_id') == chem_id:
            return s.get('chemical_name', '?')
    sig = json.loads(row[7]) if row[7] else []
    for s in sig:
        if s.get('chemical_id') == chem_id:
            return s.get('chemical_name', '?')
    return f'ID:{chem_id}'

# Check function uses matched_name string
def make_check(bad_keyword):
    def check(matched_name):
        return bad_keyword.upper() not in matched_name.upper()
    return check

critical_checks = {
    'White Wax': ('Must NOT match PHOSPHORUS', make_check('PHOSPHORUS')),
    'Gelatin Capsule': ('Must NOT match CHLOROPICRIN', make_check('CHLOROPICRIN')),
    'Strawberry Flavour': ('Must NOT match ARSENATE', make_check('ARSENATE')),
    'Arachis Oil': ('Must NOT match AMMONIUM NITRATE-FUEL', make_check('FUEL')),
    'Tutti Frutti flavor': ('Must NOT match ARSENATE/THIOUREA', 
                            lambda n: 'ARSENATE' not in n.upper() and 'THIOUREA' not in n.upper()),
    'Zinc Gluconate': ('Must NOT match ZINC CHLORIDE', make_check('ZINC CHLORIDE')),
    'Atorvastatin Calcium': ('Must NOT match CALCIUM CYANAMIDE', make_check('CYANAMIDE')),
    'Rabeprazole Sodium': ('Must NOT match SODIUM AZIDE', make_check('AZIDE')),
    'Hydroxychloroquine Sulfate': ('Must NOT match DIMETHYL SULFATE', make_check('DIMETHYL SULFATE')),
    'Venlafaxine HCl pellet': ('Must NOT match CALCIUM HYPOCHLORITE', make_check('HYPOCHLORITE')),
    'Vitamin E powder': ('Must NOT match PERCHLORIC ACID', make_check('PERCHLORIC')),
    'Simethicone 30': ('Must NOT match SODIUM SULFIDE', make_check('SODIUM SULFIDE')),
}

print("=" * 70)
print("CRITICAL SAFETY CASE VERIFICATION")
print("=" * 70)

passed = 0
total = 0
for row in rows:
    cleaned = json.loads(row[0])
    name = cleaned.get('name', '')

    for check_name, (desc, check_fn) in critical_checks.items():
        if check_name.lower() in name.lower():
            total += 1
            matched_name = get_matched_name(row)
            ok = check_fn(matched_name)
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            conf = row[2]
            conflicts = json.loads(row[4]) if row[4] else []
            veto = [c for c in conflicts if 'VETO' in c or 'MISMATCH' in c]
            print(f"  {status}: '{name[:45]}' -> '{matched_name[:45]}'")
            print(f"    Status={row[1]}, Conf={conf:.3f}, Method={row[5]}")
            if veto:
                print(f"    Veto: {veto[0][:90]}")
            print(f"    Check: {desc}")
            print()

print(f"\n{'=' * 70}")
print(f"RESULT: {passed}/{total} critical safety checks PASSED")
print(f"{'=' * 70}")

conn.close()
