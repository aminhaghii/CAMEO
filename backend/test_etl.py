"""End-to-end test: upload inventory_sample.csv via API and poll for results."""
import requests
import time
import json

BASE = "http://127.0.0.1:5000"

# Step 1: Upload
print("=== Uploading inventory_sample.csv ===")
with open("test_data/inventory_sample.csv", "rb") as f:
    r = requests.post(f"{BASE}/api/inventory/upload", files={"file": ("inventory_sample.csv", f, "text/csv")})

data = r.json()
print(f"  Status: {r.status_code}")
print(f"  Response: {data}")

if "error" in data:
    print(f"  ERROR: {data['error']}")
    exit(1)

batch_id = data["batch_id"]
print(f"  Batch ID: {batch_id}")

# Step 2: Poll until done
print("\n=== Polling status ===")
for i in range(30):
    time.sleep(1)
    r = requests.get(f"{BASE}/api/inventory/status/{batch_id}")
    status = r.json()
    s = status["status"]
    processed = status.get("processed", 0)
    total = status.get("total_rows", 0)
    print(f"  [{i+1}s] status={s}, progress={processed}/{total}")

    if s == "completed":
        break
    elif s == "error":
        print(f"  ERROR: {status.get('error_msg')}")
        exit(1)

# Step 3: Show results
print("\n=== Results ===")
summary = status.get("summary", {})
print(f"  Total rows:    {summary.get('total_rows', '?')}")
print(f"  Matched:       {summary.get('matched', '?')}")
print(f"  Unmatched:     {summary.get('unmatched', '?')}")
print(f"  Ambiguous:     {summary.get('ambiguous', '?')}")
print(f"  Match rate:    {summary.get('match_rate', '?')}")
print(f"  Avg quality:   {summary.get('avg_quality_score', '?')}")
print(f"  Avg confidence:{summary.get('avg_confidence', '?')}")
print(f"  Methods:       {summary.get('method_breakdown', {})}")

print("\n=== Needs Review ===")
for item in summary.get("needs_review", []):
    print(f"  Row {item['row_index']}: name={item.get('name','?')}, cas={item.get('cas','')}, "
          f"status={item['match_status']}, method={item['match_method']}, "
          f"score={item['quality_score']}, issues={item.get('issues', [])}")

print("\n=== Top Issues ===")
for issue, count in summary.get("top_issues", []):
    print(f"  [{count}x] {issue}")
