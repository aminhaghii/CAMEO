"""Reproduce the exact error from the search endpoint."""
import sqlite3
import re
import traceback

conn = sqlite3.connect('data/chemicals.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

query = "Acetone"
q_upper = query.upper()
like_term = f'%{query}%'
un_query = re.sub(r'^UN\s*', '', query, flags=re.IGNORECASE)
un_like = f'%{un_query}%'

sql = """
    SELECT DISTINCT
        c.id, c.name, c.synonyms, c.formulas,
        c.nfpa_health, c.nfpa_flam, c.nfpa_react, c.nfpa_special
    FROM chemicals c
    LEFT JOIN chemical_cas cc ON c.id = cc.chem_id
    LEFT JOIN chemical_unna cu ON c.id = cu.chem_id
    WHERE c.name LIKE ?
       OR c.synonyms LIKE ?
       OR c.formulas LIKE ?
       OR cc.cas_id LIKE ?
       OR CAST(cu.unna_id AS TEXT) LIKE ?
    LIMIT 200
"""

try:
    cursor.execute(sql, (like_term, like_term, like_term, like_term, un_like))
    rows = [dict(r) for r in cursor.fetchall()]
    print(f"Got {len(rows)} rows")
    
    # Check types of first row
    if rows:
        r = rows[0]
        for k, v in r.items():
            print(f"  {k}: type={type(v).__name__}, value={repr(v)[:60]}")
    
    # Now simulate the scoring
    chem_ids = list({row['id'] for row in rows})
    cas_map = {}
    un_map = {}
    if chem_ids:
        ph = ','.join('?' * len(chem_ids))
        cursor.execute(f"SELECT chem_id, cas_id FROM chemical_cas WHERE chem_id IN ({ph}) ORDER BY sort", chem_ids)
        for r in cursor.fetchall():
            cas_map.setdefault(r['chem_id'], []).append(str(r['cas_id']))
        cursor.execute(f"SELECT chem_id, unna_id FROM chemical_unna WHERE chem_id IN ({ph}) ORDER BY sort", chem_ids)
        for r in cursor.fetchall():
            un_map.setdefault(r['chem_id'], []).append(str(r['unna_id']))
    
    conn.close()
    
    un_query_upper = un_query.upper()
    
    for row in rows:
        cid = row['id']
        name = row['name'] or ''
        synonyms_raw = row['synonyms'] or ''
        formula = row['formulas'] or ''
        cas_list = cas_map.get(cid, [])
        un_list = un_map.get(cid, [])
        name_upper = name.upper()
        
        # Test each comparison
        try:
            _ = name_upper == q_upper
            _ = name_upper.startswith(q_upper)
            _ = any(q_upper in cas.upper() for cas in cas_list)
            _ = formula and q_upper in formula.upper()
            _ = any(un_query_upper in un for un in un_list)
            _ = q_upper in name_upper
            _ = q_upper in synonyms_raw.upper()
        except Exception as e:
            print(f"\nERROR on cid={cid}, name={name}")
            print(f"  name type: {type(row['name'])}")
            print(f"  synonyms type: {type(row['synonyms'])}")
            print(f"  formulas type: {type(row['formulas'])}")
            traceback.print_exc()
            break
    else:
        print("\nAll rows processed without error!")
        
except Exception as e:
    print(f"SQL Error: {e}")
    traceback.print_exc()
