"""Quick ground truth comparison."""
import sqlite3, json
from collections import Counter

gt = json.load(open('ground_truth.json', 'r', encoding='utf-8'))
conn = sqlite3.connect('data/user.db')
c = conn.cursor()
bid = c.execute('SELECT id FROM inventory_batches ORDER BY created_at DESC LIMIT 1').fetchone()[0]
c.execute('SELECT row_index, match_status FROM inventory_staging WHERE batch_id=?', (bid,))
pipeline = {r[0]: r[1] for r in c.fetchall()}
conn.close()

confusion = Counter()
for g in gt:
    actual = pipeline.get(g['row'], 'MISSING')
    actual = actual.replace('MATCHED', 'CONFIRMED').replace('REVIEW_REQUIRED', 'REVIEW')
    confusion[(g['status'], actual)] += 1

total = sum(confusion.values())
correct = sum(v for (e, a), v in confusion.items() if e == a)
print(f'Accuracy: {correct}/{total} ({100*correct/total:.1f}%)')
for (e, a), v in sorted(confusion.items()):
    m = 'OK' if e == a else 'XX'
    print(f'  {m} {e:15s} -> {a:15s}: {v}')
