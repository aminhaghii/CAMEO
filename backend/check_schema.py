import sqlite3

conn = sqlite3.connect('data/chemicals.db')
cursor = conn.cursor()

# Check chemical_cas schema
print("=== chemical_cas ===")
cursor.execute("PRAGMA table_info(chemical_cas)")
for col in cursor.fetchall():
    print(f"  {col}")
cursor.execute("SELECT * FROM chemical_cas LIMIT 3")
print("  Sample:", cursor.fetchall())

# Check chemical_unna schema
print("\n=== chemical_unna ===")
cursor.execute("PRAGMA table_info(chemical_unna)")
for col in cursor.fetchall():
    print(f"  {col}")
cursor.execute("SELECT * FROM chemical_unna LIMIT 3")
print("  Sample:", cursor.fetchall())

# Check chemicals table for synonyms column type
print("\n=== chemicals (relevant cols) ===")
cursor.execute("PRAGMA table_info(chemicals)")
for col in cursor.fetchall():
    if col[1] in ('id', 'name', 'synonyms', 'formulas', 'nfpa_health', 'nfpa_flam', 'nfpa_react', 'nfpa_special'):
        print(f"  {col}")

# Test: find Acetone
print("\n=== Acetone test ===")
cursor.execute("SELECT id, name, formulas, synonyms, nfpa_health, nfpa_flam, nfpa_react FROM chemicals WHERE name LIKE '%Acetone%' LIMIT 3")
for r in cursor.fetchall():
    print(f"  id={r[0]}, name={r[1]}, formula={r[2]}, synonyms={r[3][:80] if r[3] else None}, nfpa={r[4]},{r[5]},{r[6]}")

# Test: CAS for Acetone
print("\n=== CAS for Acetone (id=8) ===")
cursor.execute("SELECT * FROM chemical_cas WHERE chem_id = 8")
print("  ", cursor.fetchall())

# Test: UN for Acetone
print("\n=== UN for Acetone (id=8) ===")
cursor.execute("SELECT * FROM chemical_unna WHERE chem_id = 8")
print("  ", cursor.fetchall())

# Check if unna_id is TEXT or INTEGER
print("\n=== unna_id type test ===")
cursor.execute("SELECT typeof(unna_id) FROM chemical_unna LIMIT 1")
print("  type:", cursor.fetchone())
cursor.execute("SELECT unna_id FROM chemical_unna LIMIT 5")
for r in cursor.fetchall():
    print(f"  unna_id={r[0]} (type={type(r[0]).__name__})")

conn.close()
