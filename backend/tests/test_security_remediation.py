import os
import sys
import pytest
from unittest.mock import patch
import secrets

# Insert backend directory to python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app import app

def test_csrf_analyze_endpoint():
    """Verify POST request to /api/analyze without a valid CSRF token yields a 403 error."""
    mock_user = {
        'id': 1, 'email': 'user@test.com', 'full_name': 'Test User',
        'role': 'company_admin', 'status': 'ACTIVE', 'company_id': 1,
        'company_name': 'Test Company', 'tenant_db_path': 'test.db'
    }
    
    with patch('app.validate_session', return_value=mock_user):
        with app.test_client() as client:
            client.set_cookie('session_id', 'mock_session_id')
            client.set_cookie('csrf_token', 'valid_token')
            
            # Disable TESTING mode to enforce CSRF
            app.config['TESTING'] = False
            try:
                # Missing CSRF token
                res = client.post('/api/analyze', json={'chemical_ids': [1, 2]})
                assert res.status_code == 403
                
                # Incorrect CSRF token
                res_wrong = client.post('/api/analyze', json={'chemical_ids': [1, 2]}, headers={'X-CSRF-Token': 'wrong_token'})
                assert res_wrong.status_code == 403
                
                # Correct CSRF token
                res_ok = client.post('/api/analyze', json={'chemical_ids': [1, 2]}, headers={'X-CSRF-Token': 'valid_token'})
                assert res_ok.status_code != 403
            finally:
                app.config['TESTING'] = True

def test_csrf_inventory_add_endpoint():
    """Verify POST request to /api/inventory/add without a valid CSRF token yields a 403 error."""
    mock_user = {
        'id': 1, 'email': 'user@test.com', 'full_name': 'Test User',
        'role': 'company_admin', 'status': 'ACTIVE', 'company_id': 1,
        'company_name': 'Test Company', 'tenant_db_path': 'test.db'
    }
    
    with patch('app.validate_session', return_value=mock_user):
        with app.test_client() as client:
            client.set_cookie('session_id', 'mock_session_id')
            client.set_cookie('csrf_token', 'valid_token')
            
            app.config['TESTING'] = False
            try:
                res = client.post('/api/inventory/add', json={'name': 'Water'})
                assert res.status_code == 403
            finally:
                app.config['TESTING'] = True

def test_secret_key_persistence(tmp_path):
    """Verify Flask application preserves secret key across instantiations using .flask_secret_key."""
    DATA_DIR = str(tmp_path / "data")
    os.makedirs(DATA_DIR, exist_ok=True)
    secret_path = os.path.join(DATA_DIR, '.flask_secret_key')
    
    # Replicate the app.py logic
    def load_secret_key(data_dir, secret_file_path):
        key = None
        if os.environ.get('FLASK_SECRET_KEY'):
            key = os.environ['FLASK_SECRET_KEY']
        else:
            try:
                if os.path.exists(secret_file_path):
                    with open(secret_file_path, 'r') as f:
                        key = f.read().strip()
                else:
                    _generated = secrets.token_hex(32)
                    with open(secret_file_path, 'w') as f:
                        f.write(_generated)
                    key = _generated
            except Exception as e:
                key = secrets.token_hex(32)
        return key

    # 1. First run: should create the file
    if 'FLASK_SECRET_KEY' in os.environ:
        del os.environ['FLASK_SECRET_KEY']
        
    first_key = load_secret_key(DATA_DIR, secret_path)
    assert os.path.exists(secret_path)
    
    with open(secret_path, 'r') as f:
        file_key = f.read().strip()
    assert first_key == file_key
    
    # 2. Second run: should read the SAME key from file
    second_key = load_secret_key(DATA_DIR, secret_path)
    assert second_key == first_key
    
    # 3. Third run with mocked permission error
    with patch('builtins.open', side_effect=PermissionError("Mocked Permission Error")):
        third_key = load_secret_key(DATA_DIR, secret_path)
        assert third_key != first_key
        assert len(third_key) == 64
