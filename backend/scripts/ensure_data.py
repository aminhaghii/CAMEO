"""
═══════════════════════════════════════════════════════════════
CAMEO Data Verification & Patch Script
Ensures critical chemicals exist for testing
═══════════════════════════════════════════════════════════════
"""

import sqlite3
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'chemicals.db')

def check_chemical(cursor, name):
    """Check if a chemical exists by name"""
    cursor.execute("SELECT id, name FROM chemicals WHERE UPPER(name) = UPPER(?)", (name,))
    result = cursor.fetchone()
    return result

def check_groups(cursor, chem_id):
    """Check reactive groups for a chemical"""
    cursor.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (chem_id,))
    return [row[0] for row in cursor.fetchall()]

def insert_chemical(cursor, name, synonyms, formulas):
    """Insert a new chemical"""
    cursor.execute(
        """INSERT INTO chemicals 
        (name, synonyms, formulas, chris_codes, dot_labels, incompatible_absorbents) 
        VALUES (?, ?, ?, ?, ?, ?)""",
        (name, synonyms, formulas, '', '', '')
    )
    return cursor.lastrowid

def insert_group_mapping(cursor, chem_id, group_id):
    """Map chemical to reactive group"""
    cursor.execute(
        "INSERT OR IGNORE INTO mm_chemical_react (chem_id, react_id) VALUES (?, ?)",
        (chem_id, group_id)
    )

def main():
    print("═══════════════════════════════════════════════════════════")
    print("CAMEO Data Verification & Patch")
    print("═══════════════════════════════════════════════════════════\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Define critical chemicals
    critical_chemicals = [
        {
            'name': 'WATER',
            'synonyms': 'H2O|DIHYDROGEN OXIDE|AQUA',
            'formulas': 'H2O',
            'groups': [100]  # Water and Aqueous Solutions
        },
        {
            'name': 'SODIUM',
            'synonyms': 'SODIUM METAL|NA',
            'formulas': 'Na',
            'groups': [21]  # Metals, Alkali, Very Active
        },
        {
            'name': 'SULFURIC ACID',
            'synonyms': 'H2SO4|OIL OF VITRIOL',
            'formulas': 'H2SO4',
            'groups': [1, 2]  # Acids, Strong Non-oxidizing & Oxidizing
        },
        {
            'name': 'SODIUM HYDROXIDE',
            'synonyms': 'CAUSTIC SODA|NAOH|LYE',
            'formulas': 'NaOH',
            'groups': [10]  # Bases, Strong
        },
        {
            'name': 'NITROGEN',
            'synonyms': 'N2|NITROGEN GAS',
            'formulas': 'N2',
            'groups': [101]  # Inert/Air group
        },
        {
            'name': 'TEST_CHEMICAL_NO_GROUPS_A',
            'synonyms': 'DUMMY A|TEST A',
            'formulas': 'TestA',
            'groups': []  # NO GROUPS - for Fail-Safe testing
        },
        {
            'name': 'TEST_CHEMICAL_NO_GROUPS_B',
            'synonyms': 'DUMMY B|TEST B',
            'formulas': 'TestB',
            'groups': []  # NO GROUPS - for Fail-Safe testing
        }
    ]
    
    results = []
    
    for chem_spec in critical_chemicals:
        name = chem_spec['name']
        result = check_chemical(cursor, name)
        
        if result:
            chem_id, db_name = result
            groups = check_groups(cursor, chem_id)
            status = f"✓ EXISTS (ID: {chem_id})"
            
            # Check if groups match
            expected_groups = set(chem_spec['groups'])
            actual_groups = set(groups)
            
            if expected_groups != actual_groups:
                # Add missing groups
                missing = expected_groups - actual_groups
                for group_id in missing:
                    insert_group_mapping(cursor, chem_id, group_id)
                    status += f"\n  → Added group {group_id}"
                
                conn.commit()
            
            results.append({
                'name': name,
                'id': chem_id,
                'status': status,
                'groups': groups if groups else actual_groups
            })
        else:
            # Insert chemical
            chem_id = insert_chemical(
                cursor,
                chem_spec['name'],
                chem_spec['synonyms'],
                chem_spec['formulas']
            )
            
            # Add groups
            for group_id in chem_spec['groups']:
                insert_group_mapping(cursor, chem_id, group_id)
            
            conn.commit()
            
            status = f"✓ INSERTED (ID: {chem_id})"
            results.append({
                'name': name,
                'id': chem_id,
                'status': status,
                'groups': chem_spec['groups']
            })
    
    # Print results
    print("\nVerification Results:")
    print("-" * 60)
    for r in results:
        print(f"{r['name']:<40} {r['status']}")
        if r['groups']:
            print(f"  Groups: {r['groups']}")
    
    print("\n" + "═" * 60)
    print(f"Total chemicals verified/patched: {len(results)}")
    print("═" * 60 + "\n")
    
    # Return IDs for use in tests
    chemical_ids = {r['name']: r['id'] for r in results}
    
    conn.close()
    
    return chemical_ids

if __name__ == '__main__':
    ids = main()
    print("\nChemical IDs for testing:")
    for name, chem_id in ids.items():
        print(f"  {name}: {chem_id}")
