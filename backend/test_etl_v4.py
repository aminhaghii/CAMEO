"""Quick integration test for ETL v4 upgrade."""
import sys
sys.path.insert(0, '.')

import pandas as pd
from etl.schema import map_columns
from etl.clean import convert_persian_digits, validate_row
from etl.ingest import smart_ingest, _detect_header_row, _score_header_candidate

print("=" * 60)
print("ETL v4 Integration Tests")
print("=" * 60)

# ── Test 1: Persian numeral conversion ──
assert convert_persian_digits('۱۲۳۴۵') == '12345'
assert convert_persian_digits('٠١٢٣٤') == '01234'
assert convert_persian_digits('CAS: ۶۷-۶۴-۱') == 'CAS: 67-64-1'
print("[PASS] Persian numeral conversion")

# ── Test 2: Column mapping with English headers ──
df = pd.DataFrame({
    'Chemical Name': ['Acetone', 'Sulfuric Acid', 'Ethanol'],
    'CAS No.': ['67-64-1', '7664-93-9', '64-17-5'],
    'Qty': ['100', '50', '200'],
    'Unit': ['kg', 'L', 'kg'],
    'Supplier': ['Merck', 'Merck', 'Sigma'],
    'Batch': ['LOT123', 'LOT456', 'LOT789'],
    'Price ($)': ['150', '200', '100'],
})
result = map_columns(df)
cm = result['column_mapping']
print("\nColumn mapping results:")
for col, info in cm.items():
    print(f"  {col:20s} -> {info['semantic_type']:15s} ({info['confidence']}%, {info['method']})")

assert cm['Chemical Name']['semantic_type'] == 'name', f"Expected 'name', got {cm['Chemical Name']['semantic_type']}"
assert cm['CAS No.']['semantic_type'] == 'cas', f"Expected 'cas', got {cm['CAS No.']['semantic_type']}"
print("[PASS] Column mapping (English)")

# ── Test 3: Column mapping with Persian headers ──
df_fa = pd.DataFrame({
    'نام ماده': ['استون', 'اسید سولفوریک'],
    'شماره کس': ['67-64-1', '7664-93-9'],
    'مقدار': ['100', '50'],
    'واحد': ['کیلو', 'لیتر'],
    'تامین کننده': ['مرک', 'سیگما'],
})
result_fa = map_columns(df_fa)
cm_fa = result_fa['column_mapping']
print("\nPersian column mapping:")
for col, info in cm_fa.items():
    print(f"  {col:20s} -> {info['semantic_type']:15s} ({info['confidence']}%, {info['method']})")
print("[PASS] Column mapping (Persian)")

# ── Test 4: Validate row with all new fields ──
row = {
    'name': 'Acetone',
    'cas': '67-64-1',
    'quantity': '100',
    'unit': 'kg',
    'location': 'Warehouse A',
    'supplier': 'Sigma-Aldrich Co.',
    'batch_number': 'Lot# 12345',
    'purity': '99.5%',
    'price': '$150.00',
    'date': '1402/10/25',
    'product_code': 'SKU-001',
}
result = validate_row(row)
c = result['cleaned']
assert c['name'] == 'Acetone'
assert c['cas_valid'] == True
assert c['supplier'] == 'Sigma-Aldrich Co', f"Got: '{c['supplier']}'"
assert c['batch_number'] == '12345'
assert c['purity'] == 99.5
assert c['price'] == 150.0
assert c['price_currency'] == 'USD'
assert c['date_type'] == 'jalali'
assert c['product_code'] == 'SKU-001'
assert result['quality_score'] >= 80
print(f"\n[PASS] Validate row (quality_score={result['quality_score']}, issues={result['issues']})")

# ── Test 5: Definitive rules (CAS detection from content) ──
df_cas = pd.DataFrame({
    'Col A': ['Acetone', 'Ethanol'],
    'Col B': ['67-64-1', '64-17-5'],
    'Col C': ['100', '200'],
})
result_cas = map_columns(df_cas)
cm_cas = result_cas['column_mapping']
print("\nDefinitive rule detection:")
for col, info in cm_cas.items():
    print(f"  {col:10s} -> {info['semantic_type']:15s} ({info['confidence']}%, {info['method']})")
assert cm_cas['Col B']['semantic_type'] == 'cas'
print("[PASS] CAS definitive rule detection")

# ── Test 6: Header detection ──
df_header = pd.DataFrame([
    ['Company XYZ', 'Inventory Report', '', '', ''],
    ['Date: 2024-01-15', '', '', '', ''],
    ['Name', 'CAS', 'Quantity', 'Unit', 'Location'],
    ['Acetone', '67-64-1', '100', 'kg', 'WH-A'],
    ['Ethanol', '64-17-5', '200', 'L', 'WH-B'],
])
header_idx, conf, warnings = _detect_header_row(df_header)
print(f"\nHeader detection: row={header_idx}, confidence={conf}%, warnings={warnings}")
assert header_idx == 2, f"Expected header at row 2, got {header_idx}"
print("[PASS] Header detection")

print("\n" + "=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
