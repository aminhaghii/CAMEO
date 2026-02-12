"""Quick test: verify Book1.xlsx ingestion after sheet selection fix."""
import sys
sys.path.insert(0, '.')
from etl.ingest import read_file

df = read_file(r'Book1.xlsx')
meta = df.attrs.get('ingestion_metadata', {})

print(f"Status: {meta.get('status', '?')}")
print(f"Shape: {df.shape}")
print(f"Sheet: {meta.get('metadata', {}).get('sheet_name', '?')}")
print(f"Header row: {meta.get('metadata', {}).get('header_row_index', '?')}")
print(f"Confidence: {meta.get('confidence', {})}")
print(f"Warnings: {meta.get('warnings', [])}")
print(f"Columns: {list(df.columns)}")
if len(df) > 0:
    print(f"\nFirst 3 rows:")
    print(df.head(3).to_string())
else:
    print("\nDATAFRAME IS EMPTY!")
