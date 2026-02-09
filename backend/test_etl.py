"""End-to-end test: upload inventory_sample.csv via API and poll for results (ETL v2)."""
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
print("\n=== Results (ETL v2) ===")
summary = status.get("summary", {})
print(f"  Total rows:      {summary.get('total_rows', '?')}")
print(f"  MATCHED:         {summary.get('matched', '?')}")
print(f"  REVIEW_REQUIRED: {summary.get('review_required', '?')}")
print(f"  UNIDENTIFIED:    {summary.get('unidentified', '?')}")
print(f"  Match rate:      {summary.get('match_rate', '?')}")
print(f"  Avg quality:     {summary.get('avg_quality_score', '?')}")
print(f"  Avg confidence:  {summary.get('avg_confidence', '?')}")
print(f"  Methods:         {summary.get('method_breakdown', {})}")

print("\n=== Needs Review ===")
for item in summary.get("needs_review", []):
    sugs = item.get('suggestions', [])
    sug_str = ', '.join([f"{s['chemical_name']}({s['score']}%)" for s in sugs]) if sugs else 'none'
    print(f"  Row {item['row_index']}: input={item.get('input_name','?')}, "
          f"status={item['match_status']}, method={item['match_method']}, "
          f"conf={item.get('confidence',0)}, score={item['quality_score']}")
    print(f"    Suggestions: {sug_str}")
    if item.get('issues'):
        print(f"    Issues: {item['issues']}")

print("\n=== Top Issues ===")
for issue, count in summary.get("top_issues", []):
    print(f"  [{count}x] {issue}")

# Step 4: Test search endpoint
print("\n=== Testing search_chemicals endpoint ===")
r = requests.get(f"{BASE}/api/inventory/search_chemicals?q=sulfuric")
data = r.json()
print(f"  Search 'sulfuric': {len(data.get('results',[]))} results")
for res in data.get('results', [])[:3]:
    print(f"    id={res['chemical_id']}, name={res['chemical_name']}")
