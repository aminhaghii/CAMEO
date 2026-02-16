"""
test_phase1.py — Unit tests for Phase 1 Foundation Fixes.

Tests:
1. Smart header detection (ingest.py)
2. Concentration normalization (clean.py)
3. CAS-first cascade logic (match_cascade.py)
4. Bug fixes: Excel date corruption, column mapping, header order, 4-digit CAS
"""

import pytest
import pandas as pd
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from etl.ingest import _detect_header_row, _flatten_structure, _fix_excel_date_corruption, _map_columns
from etl.clean import normalize_concentration, validate_cas
from etl.match_cascade import CascadeMatcher, MatchFlag


class TestHeaderDetection:
    """Test smart header detection in ingest.py."""
    
    def test_header_row_0(self):
        """Header at row 0 (default)."""
        df = pd.DataFrame([
            ['Chemical Name', 'CAS', 'Quantity'],
            ['Sulfuric Acid', '7664-93-9', '100L']
        ])
        header_idx, confidence, warnings = _detect_header_row(df)
        assert header_idx == 0
        assert confidence >= 70
    
    def test_header_row_5(self):
        """Header at row 5 (with junk above)."""
        df = pd.DataFrame([
            ['', '', ''],
            ['Logo', '', ''],
            ['Report Title', '', ''],
            ['', '', ''],
            ['Material', 'CAS Number', 'Amount'],  # Row 4
            ['Acid', '7664-93-9', '10']
        ])
        header_idx, confidence, warnings = _detect_header_row(df)
        assert header_idx == 4
        assert confidence >= 70
    
    def test_header_with_keywords(self):
        """Header detection using keyword matching."""
        df = pd.DataFrame([
            ['Product', 'Chemical', 'UN Number', 'Storage'],
            ['Item 1', 'Ethanol', '1170', 'Zone A']
        ])
        header_idx, confidence, warnings = _detect_header_row(df)
        assert header_idx == 0
        assert confidence >= 85  # High confidence due to multiple keywords


class TestConcentration:
    """Test concentration normalization in clean.py."""
    
    def test_extract_percentage(self):
        """Extract percentage suffix."""
        name, conc = normalize_concentration("Hydrogen Peroxide 30%")
        assert name == "Hydrogen Peroxide"
        assert conc == "30%"
    
    def test_extract_percentage_with_space(self):
        """Handle space before %."""
        name, conc = normalize_concentration("H2O2 50 %")
        assert name == "H2O2"
        assert conc == "50%"
    
    def test_extract_decimal_percentage(self):
        """Handle decimal percentages."""
        name, conc = normalize_concentration("Sulfuric Acid 98.5%")
        assert name == "Sulfuric Acid"
        assert conc == "98.5%"
    
    def test_no_concentration(self):
        """No concentration suffix."""
        name, conc = normalize_concentration("Sulfuric Acid")
        assert name == "Sulfuric Acid"
        assert conc is None
    
    def test_percent_word(self):
        """Handle 'percent' word."""
        name, conc = normalize_concentration("Ethanol 95 percent")
        assert name == "Ethanol"
        assert conc == "95%"


class TestBugFixes:
    """Test critical bug fixes."""
    
    def test_excel_date_corruption_fix(self):
        """Test that Excel date corruption is fixed."""
        # Create a DataFrame with datetime column (simulating Excel date corruption)
        df = pd.DataFrame({
            'CAS': ['2001-02-03 00:00:00'],  # This would be a datetime column
            'Name': ['Test Chemical'],
        })
        df['CAS'] = pd.to_datetime(df['CAS'])  # Convert to datetime
        
        # Apply fix
        df_fixed = _fix_excel_date_corruption(df)
        
        # Check that datetime was converted back to string
        assert df_fixed['CAS'].dtype == 'object'  # Should be string, not datetime
        assert df_fixed['CAS'].iloc[0] == '2001-02-3'  # Converted to CAS-like format
    
    def test_column_mapping(self):
        """Test column name mapping."""
        df = pd.DataFrame({
            'Chemical Name': ['Sulfuric Acid'],
            'CAS Number': ['7664-93-9'],
            'Quantity': ['100L'],
        })
        
        mapped = _map_columns(df)
        
        assert 'name' in mapped
        assert 'cas' in mapped
        assert 'quantity' in mapped
        assert mapped['name'] == 'Chemical Name'
        assert mapped['cas'] == 'CAS Number'
    
    def test_four_digit_cas_rejected(self):
        """Test that 4-digit group codes are rejected."""
        # Test 4-digit group codes are rejected
        is_valid, result = validate_cas('1080')
        assert not is_valid
        assert '4-digit code' in result
        
        is_valid, result = validate_cas('1115')
        assert not is_valid
        
        is_valid, result = validate_cas('9901')
        assert not is_valid
        
        # Test that valid CAS still works
        is_valid, result = validate_cas('7664-93-9')
        assert is_valid


class TestCascade:
    """Test CAS-first cascade matching."""
    
    @pytest.fixture
    def matcher(self):
        """Create matcher instance."""
        db_path = os.path.join(
            os.path.dirname(__file__), 
            '..', 'data', 'chemicals.db'
        )
        return CascadeMatcher(db_path)
    
    def test_cas_instant_confirm(self, matcher):
        """CAS match should return CONFIRMED immediately."""
        result = matcher.match({
            'name': 'Wrong Name',
            'cas': '7664-93-9',  # Sulfuric Acid
            'cas_valid': True,
        })
        assert result.status == 'CONFIRMED'
        assert result.confidence == 1.0
        assert result.method == 'cas_exact'
    
    def test_cas_with_name_mismatch_flag(self, matcher):
        """CAS match with very different name should add flag."""
        result = matcher.match({
            'name': 'Completely Wrong Chemical',
            'cas': '7664-93-9',
            'cas_valid': True,
        })
        assert result.status == 'CONFIRMED'
        assert MatchFlag.NAME_MISMATCH in result.flags
    
    def test_un_exact_single(self, matcher):
        """UN number with single match."""
        result = matcher.match({
            'name': 'Test',
            'un_number': 1789,  # Hydrochloric Acid
        })
        # This should either CONFIRM or REVIEW depending on DB
        assert result.status in ['CONFIRMED', 'REVIEW']
        assert result.confidence >= 0.70
    
    def test_name_fuzzy_high(self, matcher):
        """High fuzzy match should go to REVIEW."""
        result = matcher.match({
            'name': 'Sulphuric Acid',  # British spelling
        })
        # Should match SULFURIC ACID with high fuzzy score
        assert result.status in ['CONFIRMED', 'REVIEW']
        assert result.confidence >= 0.60
    
    def test_no_match(self, matcher):
        """No matching signals."""
        result = matcher.match({
            'name': 'XYZ123NonexistentChemical',
        })
        assert result.status == 'UNIDENTIFIED'
        assert result.confidence == 0.0


class TestIntegration:
    """Integration tests combining multiple components."""
    
    def test_concentration_then_match(self):
        """Test that concentration normalization improves matching."""
        from etl.match_cascade import CascadeMatcher
        
        db_path = os.path.join(
            os.path.dirname(__file__), 
            '..', 'data', 'chemicals.db'
        )
        matcher = CascadeMatcher(db_path)
        
        # Without normalization: "Hydrogen Peroxide 30%" might not match well
        # With normalization: "Hydrogen Peroxide" should match better
        name_with_conc = "Hydrogen Peroxide 30%"
        name_clean, conc = normalize_concentration(name_with_conc)
        
        assert name_clean == "Hydrogen Peroxide"
        assert conc == "30%"
        
        # Now match the cleaned name
        result = matcher.match({'name': name_clean})
        # Should get better match than with concentration suffix
        assert result.confidence > 0.5


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
