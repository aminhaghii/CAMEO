#!/usr/bin/env python3
"""
Check if problem CAS numbers exist in database.
"""
import sqlite3
import sys

def check_cas_in_db():
    conn = sqlite3.connect('backend/data/chemicals.db')
    cursor = conn.cursor()
    
    problem_cas = [
        '590-19-2',    # 1,2-Butadiene
        '598-25-4',    # 1,1-Dimethylallene
        '591-95-7',    # 1,2-Pentadiene
        '1574-41-0',   # cis-1,3-Pentadiene
        '2004-70-8',   # trans-1,3-Pentadiene
        '198-55-0',    # Perylene
        '13827-32-2',  # Sulfur monoxide
        '16065-83-1',  # Chromium III compounds
    ]
    
    print("Checking CAS numbers in database:\n")
    print(f"{'CAS Number':<15} {'Status':<15} {'Chemical Name'}")
    print("-" * 70)
    
    for cas in problem_cas:
        cursor.execute("""
            SELECT ch.name, cc.cas_id 
            FROM chemicals ch
            JOIN chemical_cas cc ON ch.id = cc.chem_id
            WHERE cc.cas_id = ?
        """, (cas,))
        result = cursor.fetchone()
        
        if result:
            print(f"{cas:<15} {'FOUND':<15} {result[0]}")
        else:
            print(f"{cas:<15} {'NOT FOUND':<15} —")
    
    print("\n" + "=" * 70)
    print("\nAll CAS numbers starting with '590':")
    cursor.execute("SELECT DISTINCT cas_id FROM chemical_cas WHERE cas_id LIKE '590%'")
    results = cursor.fetchall()
    if results:
        for row in results:
            print(f"  {row[0]}")
    else:
        print("  (none found)")
    
    print("\nAll CAS numbers starting with '598':")
    cursor.execute("SELECT DISTINCT cas_id FROM chemical_cas WHERE cas_id LIKE '598%'")
    results = cursor.fetchall()
    if results:
        for row in results:
            print(f"  {row[0]}")
    else:
        print("  (none found)")
    
    conn.close()

if __name__ == '__main__':
    check_cas_in_db()
