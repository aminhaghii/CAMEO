"""
test_edge_cases.py — Test Task 1 (Header Guard) and Task 2 (Last-Ditch Recovery)

Validates the two surgical edge case fixes:
1. Repeated headers in data are removed
2. UNIDENTIFIED rows are recovered via deep scan and cross-column lookup
"""

import pytest
import pandas as pd
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from etl.header_guard import remove_repeated_headers
from etl.last_ditch_recovery import attempt_last_ditch_recovery

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'chemicals.db')


class TestHeaderGuard:
    """Test Task 1: Header Guard removes repeated headers."""
    
    def test_repeated_header_in_middle(self):
        """Test: Repeated header row in middle of data is removed."""
        df = pd.DataFrame({
            'Chemical Name': ['ACETONE', 'Chemical Name', 'METHANOL'],
            'CAS Number': ['67-64-1', 'CAS Number', '67-56-1'],
            'Quantity': ['100 L', 'Quantity', '50 L']
        })
        
        result = remove_repeated_headers(df, 'test_batch')
        
        # Should remove row 1 (repeated header)
        assert len(result) == 2, f"Expected 2 rows after header removal, got {len(result)}"
        assert result.iloc[0]['Chemical Name'] == 'ACETONE'
        assert result.iloc[1]['Chemical Name'] == 'METHANOL'
    
    def test_repeated_header_at_end(self):
        """Test: Repeated header row at end of data is removed."""
        df = pd.DataFrame({
            'name': ['ACETONE', 'METHANOL', 'name'],
            'cas': ['67-64-1', '67-56-1', 'cas'],
            'quantity': ['100 L', '50 L', 'quantity']
        })
        
        result = remove_repeated_headers(df, 'test_batch')
        
        # Should remove row 2 (repeated header)
        assert len(result) == 2
        assert result.iloc[0]['name'] == 'ACETONE'
        assert result.iloc[1]['name'] == 'METHANOL'
    
    def test_no_repeated_headers(self):
        """Test: Normal data without repeated headers is unchanged."""
        df = pd.DataFrame({
            'Chemical Name': ['ACETONE', 'METHANOL', 'ETHANOL'],
            'CAS Number': ['67-64-1', '67-56-1', '64-17-5'],
            'Quantity': ['100 L', '50 L', '75 L']
        })
        
        result = remove_repeated_headers(df, 'test_batch')
        
        # Should keep all rows
        assert len(result) == 3
    
    def test_partial_header_match(self):
        """Test: Row with partial header match (≥50% threshold) is removed."""
        df = pd.DataFrame({
            'Chemical Name': ['ACETONE', 'Chemical Name', 'METHANOL'],
            'CAS Number': ['67-64-1', 'cas', '67-56-1'],  # 'cas' matches 'CAS Number'
            'Quantity': ['100 L', 'qty', '50 L']  # 'qty' doesn't match 'Quantity'
        })
        
        result = remove_repeated_headers(df, 'test_batch')
        
        # Should remove row 1 (2/3 = 66% match)
        assert len(result) == 2


class TestLastDitchRecovery:
    """Test Task 2: Last-Ditch Recovery for UNIDENTIFIED rows."""
    
    def test_cas_in_wrong_column(self):
        """Test: CAS number found in unexpected column (e.g., Name column)."""
        row_dict = {
            'Chemical Name': '67-64-1',  # CAS in name column
            'CAS Number': '',
            'Quantity': '100 L'
        }
        cleaned = {
            'name': '67-64-1',
            'cas': '',
            'quantity': '100 L'
        }
        
        result = attempt_last_ditch_recovery(row_dict, cleaned, DB_PATH, 'test_batch', 1)
        
        assert result is not None, "Expected recovery to succeed"
        assert result['match_status'] == 'REVIEW_REQUIRED'
        assert result['chemical_name'] == 'ACETONE'
        assert 'CAS found in unexpected column' in str(result.get('field_swaps', []))
    
    def test_chemical_name_in_cas_column(self):
        """Test: Chemical name found in CAS column (cross-column lookup)."""
        row_dict = {
            'Chemical Name': '',
            'CAS Number': 'ACETONE',  # Name in CAS column
            'Quantity': '100 L'
        }
        cleaned = {
            'name': '',
            'cas': 'ACETONE',
            'quantity': '100 L'
        }
        
        result = attempt_last_ditch_recovery(row_dict, cleaned, DB_PATH, 'test_batch', 1)
        
        assert result is not None, "Expected recovery to succeed"
        assert result['match_status'] == 'REVIEW_REQUIRED'
        assert result['chemical_name'] == 'ACETONE'
        assert 'Chemical name found in CAS column' in str(result.get('field_swaps', []))
    
    def test_cas_in_random_column(self):
        """Test: CAS number found in random column (e.g., Notes)."""
        row_dict = {
            'Chemical Name': 'Unknown',
            'CAS Number': '',
            'Notes': 'Contains 67-64-1 chemical'  # CAS in notes
        }
        cleaned = {
            'name': 'Unknown',
            'cas': '',
            'notes': 'Contains 67-64-1 chemical'
        }
        
        result = attempt_last_ditch_recovery(row_dict, cleaned, DB_PATH, 'test_batch', 1)
        
        assert result is not None, "Expected recovery to succeed"
        assert result['match_status'] == 'REVIEW_REQUIRED'
        assert result['chemical_name'] == 'ACETONE'
        assert 'deep row scan' in result.get('recovery_note', '').lower()
    
    def test_no_recovery_possible(self):
        """Test: No recovery possible when no valid identifiers found."""
        row_dict = {
            'Chemical Name': 'Unknown Chemical XYZ',
            'CAS Number': 'invalid',
            'Quantity': '100 L'
        }
        cleaned = {
            'name': 'Unknown Chemical XYZ',
            'cas': 'invalid',
            'quantity': '100 L'
        }
        
        result = attempt_last_ditch_recovery(row_dict, cleaned, DB_PATH, 'test_batch', 1)
        
        assert result is None, "Expected recovery to fail (no valid identifiers)"
    
    def test_cas_with_name_in_name_column(self):
        """Test: Name column contains both name and CAS (e.g., 'ACETONE (67-64-1)')."""
        row_dict = {
            'Chemical Name': 'ACETONE (67-64-1)',
            'CAS Number': '',
            'Quantity': '100 L'
        }
        cleaned = {
            'name': 'ACETONE (67-64-1)',
            'cas': '',
            'quantity': '100 L'
        }
        
        result = attempt_last_ditch_recovery(row_dict, cleaned, DB_PATH, 'test_batch', 1)
        
        assert result is not None, "Expected recovery to succeed"
        assert result['match_status'] == 'REVIEW_REQUIRED'
        assert result['chemical_name'] == 'ACETONE'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
