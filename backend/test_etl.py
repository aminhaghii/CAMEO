"""End-to-end test: ETL v3 Hybrid Multi-Signal Matcher with adversarial + real Excel files."""
import sys
import os
import io
import requests
import time
import json

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = "http://127.0.0.1:5000"

# All test files
TEST_FILES = [
    ("test_data/adversarial_test.csv", "adversarial_test.csv", "text/csv"),
    ("uploads/1.xlsx", "1.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("uploads/2.xlsx", "2.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("uploads/3.xlsx", "3.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("uploads/4.xlsx", "4.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
]

print("=" * 70)
print("  ETL v3 - Hybrid Multi-Signal Matching Engine - Full Test Suite")
print("=" * 70)


def safe(s):
    """Make string safe for console output."""
    if not s:
        return ''
    return str(s)


def run_test(filepath, filename, mimetype):
    """Upload a file, poll for completion, and print results."""
    print(f"\n{'='*70}")
    print(f"  FILE: {filename}")
    print(f"{'='*70}")

    if not os.path.exists(filepath):
        print(f"  SKIP: File not found: {filepath}")
        return None

    # Upload
    with open(filepath, "rb") as f:
        r = requests.post(f"{BASE}/api/inventory/upload",
                          files={"file": (filename, f, mimetype)})
    data = r.json()
    if "error" in data:
        print(f"  ERROR: {data['error']}")
        return None

    batch_id = data["batch_id"]
    print(f"  Batch: {batch_id[:8]}...")

    # Poll
    for i in range(30):
        time.sleep(1)
        r = requests.get(f"{BASE}/api/inventory/status/{batch_id}")
        status = r.json()
        s = status["status"]
        processed = status.get("processed", 0)
        total = status.get("total_rows", 0)
        if s == "completed":
            print(f"  Completed in {i+1}s ({processed}/{total} rows)")
            break
        elif s == "error":
            print(f"  ERROR: {status.get('error_msg')}")
            return None
    else:
        print("  TIMEOUT")
        return None

    # Results
    summary = status.get("summary", {})
    total = summary.get('total_rows', 0)
    matched = summary.get('matched', 0)
    review = summary.get('review_required', 0)
    unid = summary.get('unidentified', 0)
    rate = summary.get('match_rate', 0)
    avg_q = summary.get('avg_quality_score', 0)
    avg_c = summary.get('avg_confidence', 0)

    print(f"\n  MATCHED: {matched}/{total}  |  REVIEW: {review}  |  UNIDENTIFIED: {unid}")
    print(f"  Match rate: {rate:.1%}  |  Avg quality: {avg_q}  |  Avg confidence: {avg_c:.2f}")
    print(f"  Methods: {json.dumps(summary.get('method_breakdown', {}))}")

    # Needs review
    needs = summary.get("needs_review", [])
    if needs:
        print(f"\n  --- Needs Review ({len(needs)} rows) ---")
        for item in needs:
            name = safe(item.get('input_name', '?'))
            cas = safe(item.get('input_cas', ''))
            st = item['match_status']
            meth = item['match_method']
            conf = item.get('confidence', 0)
            qs = item['quality_score']
            print(f"  Row {item['row_index']:2d}: [{st:17s}] conf={conf:.2f} q={qs:3d}  name={name[:40]}")

            # Conflicts
            for cf in item.get('conflicts', []):
                print(f"         [!] CONFLICT: {safe(cf)[:80]}")

            # Field swaps
            for fs in item.get('field_swaps', []):
                print(f"         [~] SWAP: {safe(fs)[:80]}")

            # Signals summary
            sigs = item.get('signals', [])
            if sigs:
                top3 = sorted(sigs, key=lambda x: x.get('weighted', 0), reverse=True)[:3]
                for sig in top3:
                    print(f"         [{sig['source']:18s}] w={sig['weighted']:5.1f}%  {safe(sig['detail'])[:55]}")

            # Suggestions
            sugs = item.get('suggestions', [])
            if sugs:
                sug_str = ', '.join([f"{safe(s['chemical_name'])}({s['score']:.0f}%)" for s in sugs[:3]])
                print(f"         Suggest: {sug_str}")

    # Top issues
    top_issues = summary.get("top_issues", [])
    if top_issues:
        print(f"\n  --- Top Issues ---")
        for issue, count in top_issues[:5]:
            print(f"  [{count}x] {safe(issue)[:80]}")

    return summary


# Run all tests
all_results = {}
for filepath, filename, mimetype in TEST_FILES:
    result = run_test(filepath, filename, mimetype)
    if result:
        all_results[filename] = result

# Final summary
print(f"\n{'='*70}")
print("  FINAL SUMMARY")
print(f"{'='*70}")
total_rows = 0
total_matched = 0
total_review = 0
total_unid = 0
for fname, s in all_results.items():
    t = s.get('total_rows', 0)
    m = s.get('matched', 0)
    r = s.get('review_required', 0)
    u = s.get('unidentified', 0)
    total_rows += t
    total_matched += m
    total_review += r
    total_unid += u
    rate = m / t * 100 if t else 0
    print(f"  {fname:30s}  {m:2d}/{t:2d} matched ({rate:5.1f}%)  review={r}  unid={u}")

overall_rate = total_matched / total_rows * 100 if total_rows else 0
print(f"\n  OVERALL: {total_matched}/{total_rows} matched ({overall_rate:.1f}%)")
print(f"  Review: {total_review}  |  Unidentified: {total_unid}")
print(f"{'='*70}")
