"""Quick diagnostic of pipeline results."""
import sqlite3, json

conn = sqlite3.connect('data/user.db')
c = conn.cursor()

# Get latest batch
c.execute('SELECT id FROM inventory_batches ORDER BY created_at DESC LIMIT 1')
batch_id = c.fetchone()[0]

# Status counts
c.execute('SELECT match_status, COUNT(*) FROM inventory_staging WHERE batch_id=? GROUP BY match_status', (batch_id,))
print("=== Status Counts ===")
for row in c.fetchall():
    print(f"  {row[0]}: {row[1]}")

# Sample UNIDENTIFIED rows
print("\n=== Sample UNIDENTIFIED (first 10) ===")
c.execute('''SELECT cleaned_data, match_status, confidence, issues, conflicts_json
             FROM inventory_staging WHERE batch_id=? AND match_status='UNIDENTIFIED'
             LIMIT 10''', (batch_id,))
for row in c.fetchall():
    cleaned = json.loads(row[0])
    name = cleaned.get('name', '?')
    conf = row[2]
    issues = json.loads(row[3]) if row[3] else []
    conflicts = json.loads(row[4]) if row[4] else []
    sem_issues = [i for i in issues if 'SEMANTIC' in i or 'VETO' in i or 'SAFETY' in i]
    print(f"  '{name[:45]}' conf={conf:.3f} sem={sem_issues[:1]}")

# Sample MATCHED rows
print("\n=== Sample MATCHED (first 5) ===")
c.execute('''SELECT cleaned_data, confidence, match_method
             FROM inventory_staging WHERE batch_id=? AND match_status='MATCHED'
             LIMIT 5''', (batch_id,))
for row in c.fetchall():
    cleaned = json.loads(row[0])
    name = cleaned.get('name', '?')
    print(f"  '{name[:45]}' conf={row[1]:.3f} method={row[2]}")

# Check safety vetoes
print("\n=== Safety Vetoes ===")
c.execute('''SELECT issues FROM inventory_staging WHERE batch_id=? AND issues LIKE '%SAFETY_VETO%' ''', (batch_id,))
vetoes = c.fetchall()
print(f"  Total safety vetoes: {len(vetoes)}")
for v in vetoes[:5]:
    issues = json.loads(v[0])
    veto = [i for i in issues if 'SAFETY_VETO' in i]
    print(f"  {veto[0][:100]}")

# Check semantic mismatches
print("\n=== Semantic Mismatches ===")
c.execute('''SELECT issues FROM inventory_staging WHERE batch_id=? AND issues LIKE '%SEMANTIC_MISMATCH%' ''', (batch_id,))
mismatches = c.fetchall()
print(f"  Total semantic mismatches: {len(mismatches)}")

conn.close()
