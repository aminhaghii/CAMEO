import os
import sys
import time
import uuid

# Add the backend directory to sys.path so we can import app
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.chdir(os.path.abspath(os.path.dirname(__file__)))

from app import app
from auth.models import get_auth_db_connection

from unittest.mock import patch

def run_tests():
    print("Starting Strict Backend Verification (Phase 2)...")
    passed = 0
    failed = 0
    
    # Store legacy DB mtime before tests
    legacy_db_path = app.config['USER_DB_PATH']
    initial_mtime = os.path.getmtime(legacy_db_path) if os.path.exists(legacy_db_path) else 0
    
    client = app.test_client()
    
    # Generate unique test data
    test_company_name = f"TestCorp_{uuid.uuid4().hex[:8]}"
    company_id = None
    
    print("\n--- TEST 1: Super Admin POST (Create Company) ---")
    with patch('app.validate_session') as mock_validate:
        mock_validate.return_value = {'id': 'superadmin', 'email': 'admin@demo.com', 'role': 'super_admin', 'company_id': 1}
        # also mock request.cookies to pretend we have a session_id
        client.set_cookie('session_id', 'fake-session')
        res = client.post('/api/admin/companies', json={'name': test_company_name, 'max_users': 15})
    if res.status_code == 201:
        data = res.get_json()
        if data.get('success') and data.get('company_id'):
            company_id = data['company_id']
            # Verify DB directly
            conn = get_auth_db_connection(app.config['AUTH_DB_PATH'])
            cur = conn.cursor()
            cur.execute("SELECT name, max_users FROM companies WHERE id=?", (company_id,))
            row = cur.fetchone()
            conn.close()
            if row and row['name'] == test_company_name and row['max_users'] == 15:
                print("PASS: Company created successfully in DB")
                passed += 1
            else:
                print("FAIL: Company not found in DB or data mismatch")
                failed += 1
        else:
            print(f"FAIL: Success flag not set or missing ID. Response: {data}")
            failed += 1
    else:
        print(f"FAIL: Expected 201, got {res.status_code}. Response: {res.data.decode('utf-8')}")
        failed += 1

    print("\n--- TEST 2: Super Admin PUT (Update Company) ---")
    if company_id:
        with patch('app.validate_session') as mock_validate:
            mock_validate.return_value = {'id': 'superadmin', 'email': 'admin@demo.com', 'role': 'super_admin', 'company_id': 1}
            client.set_cookie('session_id', 'fake-session')
            res = client.put(f'/api/admin/companies/{company_id}', json={'max_users': 99, 'license_status': 'suspended'})
            
        if res.status_code == 200:
            # Verify DB directly
            conn = get_auth_db_connection(app.config['AUTH_DB_PATH'])
            cur = conn.cursor()
            cur.execute("SELECT license_status, max_users FROM companies WHERE id=?", (company_id,))
            row = cur.fetchone()
            conn.close()
            if row and row['license_status'] == 'suspended' and row['max_users'] == 99:
                print("PASS: Company updated successfully in DB")
                passed += 1
            else:
                print(f"FAIL: Data mismatch in DB after update. DB: {dict(row)}")
                failed += 1
        else:
            print(f"FAIL: Expected 200, got {res.status_code}. Response: {res.data.decode('utf-8')}")
            failed += 1
    else:
        print("SKIP: Cannot test PUT because POST failed")
        failed += 1
        
    print("\n--- TEST 3: Isolation (No legacy DB leaks) ---")
    final_mtime = os.path.getmtime(legacy_db_path) if os.path.exists(legacy_db_path) else 0
    if final_mtime == initial_mtime:
        print(f"PASS: Legacy DB ({legacy_db_path}) was not modified.")
        passed += 1
    else:
        print(f"FAIL: Legacy DB was modified! (mtime changed from {initial_mtime} to {final_mtime})")
        failed += 1
        
    print("\n--- TEST 4: RBAC (Company Admin Forbidden) ---")
    if company_id:
        with patch('app.validate_session') as mock_validate:
            mock_validate.return_value = {'id': 'companyadmin', 'email': 'ca@demo.com', 'role': 'company_admin', 'company_id': company_id}
            client.set_cookie('session_id', 'fake-session')
            res = client.put(f'/api/admin/companies/{company_id}', json={'max_users': 100})
            
        if res.status_code == 403:
            print(f"PASS: company_admin gets {res.status_code} Forbidden")
            passed += 1
        elif res.status_code == 401 and "redirect" in str(res.data).lower():
            # Sometimes failed RBAC returns redirect (302) or 401
            print(f"PASS: company_admin properly rejected ({res.status_code})")
            passed += 1
        else:
            print(f"FAIL: Expected 403, got {res.status_code}. Response: {res.data.decode('utf-8')}")
            failed += 1
    else:
        print("SKIP: Cannot test RBAC because POST failed")
        failed += 1
        
    print(f"\nRESULTS: {passed} Passed, {failed} Failed")
    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
