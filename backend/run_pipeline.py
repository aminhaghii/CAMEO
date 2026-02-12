"""Delete user.db, upload Book1.xlsx, wait for completion, show results."""
import os, time, requests, json

# Reset via API or just upload fresh (pipeline handles new batch)
print("Uploading fresh batch...")

# Upload
with open('Book1.xlsx', 'rb') as f:
    r = requests.post('http://127.0.0.1:5000/api/inventory/upload', files={'file': f})
batch_id = r.json()['batch_id']
print(f"Uploaded: batch_id={batch_id}")

# Wait for completion
for i in range(120):
    time.sleep(1)
    r = requests.get(f'http://127.0.0.1:5000/api/inventory/status/{batch_id}')
    j = r.json()
    if j['status'] == 'completed':
        s = j.get('summary', {})
        print(f"Completed: {j['processed']}/{j['total_rows']}")
        print(f"Matched: {s.get('matched')} | Review: {s.get('review_required')} | Unidentified: {s.get('unidentified')}")
        break
    if i % 10 == 0:
        print(f"  Processing... {j.get('processed', 0)}/{j.get('total_rows', '?')}")
else:
    print("Timeout!")
