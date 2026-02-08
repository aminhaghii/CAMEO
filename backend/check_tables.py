import sqlite3

conn = sqlite3.connect('data/chemicals.db')
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()

print("All tables in chemicals.db:")
for table in tables:
    print(f"  - {table[0]}")

# Check for UN-related tables
print("\nUN-related tables:")
for table in tables:
    if 'un' in table[0].lower():
        print(f"  - {table[0]}")
        # Show first few rows
        cursor.execute(f"SELECT * FROM {table[0]} LIMIT 3")
        print(f"    Sample data: {cursor.fetchall()}")

conn.close()
