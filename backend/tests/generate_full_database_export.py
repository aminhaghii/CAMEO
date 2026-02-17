"""
generate_full_database_export.py — Generate complete CAMEO database export for stress testing.

Exports all 5097+ chemicals from CAMEO database with:
- Chemical name
- CAS number(s)
- UN number(s) if available
- Quantity (random realistic values)
- Location (random warehouse locations)
- Supplier (random suppliers)
- Date received (random dates)
"""

import sqlite3
import pandas as pd
import os
import random
from datetime import datetime, timedelta

# Paths
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'chemicals.db')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), 'data', 'FULL_DATABASE_EXPORT.xlsx')


def generate_full_database_export():
    """Generate Excel file with all chemicals from CAMEO database."""
    print("=" * 70)
    print("CAMEO FULL DATABASE EXPORT GENERATOR")
    print("=" * 70)
    print()
    
    # Connect to database
    print(f"Connecting to database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get total chemical count
    cursor.execute("SELECT COUNT(*) as count FROM chemicals")
    total = cursor.fetchone()['count']
    print(f"Total chemicals in database: {total}")
    print()
    
    # Query all chemicals with CAS and UN numbers
    print("Querying all chemicals with CAS and UN numbers...")
    query = """
        SELECT 
            c.id,
            c.name,
            GROUP_CONCAT(DISTINCT cc.cas_id) as cas_numbers,
            c.formulas
        FROM chemicals c
        LEFT JOIN chemical_cas cc ON c.id = cc.chem_id
        GROUP BY c.id, c.name
        ORDER BY c.name
    """
    
    cursor.execute(query)
    chemicals = cursor.fetchall()
    print(f"Retrieved {len(chemicals)} chemicals")
    print()
    
    # Generate Excel data
    print("Generating Excel data with random inventory details...")
    data = []
    
    # Random data generators
    units = ['L', 'kg', 'gal', 'drums', 'ml', 'g', 'lb', 'oz']
    warehouses = ['A', 'B', 'C', 'D', 'E', 'F']
    suppliers = [
        'Chemical Supply Co.', 'Industrial Chemicals Inc.', 'Global Chem Ltd.',
        'Petrochemical Distributors', 'Lab Supplies USA', 'ChemSource International',
        'Reagent Depot', 'Specialty Chemicals Corp.', 'Bulk Chemical Warehouse',
        'Scientific Supply House'
    ]
    
    for i, chem in enumerate(chemicals, 1):
        # Progress indicator
        if i % 500 == 0:
            print(f"  Processed {i}/{len(chemicals)} chemicals...")
        
        # Get first CAS number if available
        cas = ''
        if chem['cas_numbers']:
            cas_list = chem['cas_numbers'].split(',')
            cas = cas_list[0] if cas_list else ''
        
        # Generate random inventory data
        quantity = random.randint(1, 1000)
        unit = random.choice(units)
        location = f"{random.choice(warehouses)}-{random.randint(1, 100)}"
        supplier = random.choice(suppliers)
        days_ago = random.randint(1, 730)  # 0-2 years ago
        date_received = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
        batch = f"BATCH-{random.randint(10000, 99999)}"
        
        data.append({
            'Chemical Name': chem['name'],
            'CAS Number': cas,
            'Quantity': quantity,
            'Unit': unit,
            'Location': location,
            'Supplier': supplier,
            'Batch Number': batch,
            'Date Received': date_received,
        })
    
    print(f"  Processed {len(chemicals)}/{len(chemicals)} chemicals... Done!")
    print()
    
    # Create DataFrame
    df = pd.DataFrame(data)
    
    # Save to Excel
    print(f"Saving to Excel: {OUTPUT_FILE}")
    df.to_excel(OUTPUT_FILE, index=False, engine='openpyxl')
    
    # Get file size
    file_size = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)  # MB
    
    print()
    print("=" * 70)
    print("✓ FULL DATABASE EXPORT COMPLETE")
    print("=" * 70)
    print(f"File: {OUTPUT_FILE}")
    print(f"Total rows: {len(df)}")
    print(f"Total columns: {len(df.columns)}")
    print(f"File size: {file_size:.2f} MB")
    print()
    print("Columns:")
    for col in df.columns:
        print(f"  - {col}")
    print()
    print("Sample data (first 5 rows):")
    print(df.head().to_string())
    print()
    print("=" * 70)
    
    conn.close()


if __name__ == '__main__':
    generate_full_database_export()
