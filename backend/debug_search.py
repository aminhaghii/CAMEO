"""Debug the search SQL to find why queries fail."""
import sqlite3

conn = sqlite3.connect('data/chemicals.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Test 1: Direct name search
print("=== Direct name search for 'Acetone' ===")
cursor.execute("SELECT id, name FROM chemicals WHERE name LIKE '%Acetone%' LIMIT 5")
for r in cursor.fetchall():
    print(f"  id={r['id']}, name={r['name']}")

# Test 2: Check if name is case-sensitive
print("\n=== Case check: UPPER name ===")
cursor.execute("SELECT id, name FROM chemicals WHERE UPPER(name) LIKE '%ACETONE%' LIMIT 5")
for r in cursor.fetchall():
    print(f"  id={r['id']}, name={r['name']}")

# Test 3: The actual LEFT JOIN query
print("\n=== LEFT JOIN query for 'Acetone' ===")
like_term = '%Acetone%'
sql = """
    SELECT DISTINCT c.id, c.name
    FROM chemicals c
    LEFT JOIN chemical_cas cc ON c.id = cc.chem_id
    LEFT JOIN chemical_unna cu ON c.id = cu.chem_id
    WHERE c.name LIKE ?
       OR c.synonyms LIKE ?
       OR c.formulas LIKE ?
       OR cc.cas_id LIKE ?
       OR CAST(cu.unna_id AS TEXT) LIKE ?
    LIMIT 10
"""
cursor.execute(sql, (like_term, like_term, like_term, like_term, like_term))
rows = cursor.fetchall()
print(f"  Found {len(rows)} results")
for r in rows:
    print(f"  id={r['id']}, name={r['name']}")

# Test 4: CAS search
print("\n=== LEFT JOIN query for '67-64-1' ===")
like_term = '%67-64-1%'
cursor.execute(sql, (like_term, like_term, like_term, like_term, like_term))
rows = cursor.fetchall()
print(f"  Found {len(rows)} results")
for r in rows:
    print(f"  id={r['id']}, name={r['name']}")

# Test 5: UN search
print("\n=== LEFT JOIN query for '1090' ===")
like_term = '%1090%'
cursor.execute(sql, (like_term, like_term, like_term, like_term, like_term))
rows = cursor.fetchall()
print(f"  Found {len(rows)} results")
for r in rows:
    print(f"  id={r['id']}, name={r['name']}")

# Test 6: Synonym search
print("\n=== LEFT JOIN query for 'Dimethyl ketone' ===")
like_term = '%Dimethyl ketone%'
cursor.execute(sql, (like_term, like_term, like_term, like_term, like_term))
rows = cursor.fetchall()
print(f"  Found {len(rows)} results")
for r in rows:
    print(f"  id={r['id']}, name={r['name']}")

# Test 7: Water
print("\n=== LEFT JOIN query for 'Water' ===")
like_term = '%Water%'
cursor.execute(sql, (like_term, like_term, like_term, like_term, like_term))
rows = cursor.fetchall()
print(f"  Found {len(rows)} results")
for r in rows:
    print(f"  id={r['id']}, name={r['name']}")

# Test 8: Check synonyms format for Acetone
print("\n=== Synonyms for Acetone (id=8) ===")
cursor.execute("SELECT synonyms FROM chemicals WHERE id = 8")
r = cursor.fetchone()
print(f"  synonyms: {r['synonyms']}")

conn.close()
