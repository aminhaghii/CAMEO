"""
Fix Water Group Assignment

Problem: Water (ID 30024) is incorrectly assigned to Group 104 (Strong Oxidizing Agent)
         in addition to Group 100 (Water and Aqueous Solutions).
         
         Water is NOT a strong oxidizing agent. This incorrect assignment causes
         all Water compatibility checks to fail because there are no rules for
         Group 104 vs other groups, triggering the fail-safe "Caution" response.

Solution: Remove Water from Group 104, keeping only Group 100.

Safety: This change is safe because:
1. Water is the ONLY chemical in Group 104
2. Water is chemically NOT a strong oxidizing agent
3. Group 100 correctly represents Water
"""

import os
import sqlite3

DB_PATH = os.environ.get(
    "CHEMICALS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "resources", "chemicals.db"),
)

WATER_ID = 30024
WATER_GROUP = 100  # Correct group
WRONG_GROUP = 104  # Incorrect group (Strong Oxidizing Agent)


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def show_current_state(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT react_id FROM mm_chemical_react WHERE chem_id = ? ORDER BY react_id",
        (WATER_ID,),
    )
    groups = [r["react_id"] for r in cur.fetchall()]
    print(f"[BEFORE] Water (ID {WATER_ID}) groups: {groups}")
    return groups


def fix_water_groups(conn):
    cur = conn.cursor()
    
    # Remove Water from Group 104
    cur.execute(
        "DELETE FROM mm_chemical_react WHERE chem_id = ? AND react_id = ?",
        (WATER_ID, WRONG_GROUP),
    )
    
    deleted = cur.rowcount
    print(f"[FIX] Removed Water from Group {WRONG_GROUP}: {deleted} row(s) deleted")
    
    conn.commit()
    return deleted


def verify(conn):
    cur = conn.cursor()
    cur.execute(
        "SELECT react_id FROM mm_chemical_react WHERE chem_id = ? ORDER BY react_id",
        (WATER_ID,),
    )
    groups = [r["react_id"] for r in cur.fetchall()]
    print(f"[AFTER] Water (ID {WATER_ID}) groups: {groups}")
    
    # Verify Water is ONLY in Group 100
    if groups == [WATER_GROUP]:
        print("✅ SUCCESS: Water is now correctly assigned to only Group 100")
        return True
    else:
        print(f"⚠️ WARNING: Water groups unexpected: {groups}")
        return False


def main():
    print(f"[INFO] Database: {DB_PATH}")
    print("=" * 60)
    
    conn = connect()
    
    # Show current state
    before = show_current_state(conn)
    
    if WRONG_GROUP not in before:
        print(f"[SKIP] Water is not in Group {WRONG_GROUP}. No fix needed.")
        conn.close()
        return
    
    # Apply fix
    fix_water_groups(conn)
    
    # Verify
    success = verify(conn)
    
    conn.close()
    
    print("=" * 60)
    if success:
        print("🎉 Water group assignment fixed successfully!")
    else:
        print("⚠️ Fix may not have completed correctly. Please verify manually.")


if __name__ == "__main__":
    main()
