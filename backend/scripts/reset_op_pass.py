import sqlite3
import sys
sys.path.append(r'c:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend')
from auth.security import hash_password

conn = sqlite3.connect(r'c:\Users\aminh\OneDrive\Desktop\CAMEO\CAMEO\backend\data\global_auth.db')
cur = conn.cursor()
new_hash = hash_password('operator123')
cur.execute("UPDATE users SET password_hash = ? WHERE role = 'operator'", (new_hash,))
conn.commit()
conn.close()
print('Passwords reset to operator123')
