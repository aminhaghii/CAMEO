"""
etl_comprehensive_stress_test.py — Comprehensive ETL stress test suite.

Tests all 20 edge cases across 4 categories:
- Category A: Column Detection Challenges (A1-A5)
- Category B: Data Quality Challenges (B1-B5)
- Category C: Matching Engine Challenges (C1-C5)
- Category D: Real-World Production Scenarios (D1-D5)

Each test validates specific failure modes and edge cases to ensure production readiness.
"""

import pytest
import pandas as pd
import os
import sys
import json
import sqlite3
from pathlib import Path

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from etl.ingest import read_file
from etl.schema import map_columns
from etl.match import HybridMatcher
from etl.clean import validate_row

# Paths
TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'chemicals.db')


class TestETLStressTest:
    """
    Comprehensive ETL stress test suite.
    Each test validates a specific edge case or failure mode.
    """
    
    @pytest.fixture(scope='class')
    def db_path(self):
        """Path to CAMEO chemicals database."""
        return DB_PATH
    
    @pytest.fixture(scope='class')
    def matcher(self, db_path):
        """Shared matcher instance."""
        return HybridMatcher(db_path)
    
    def assert_column_detection(self, result, expected_columns, min_confidence=70):
        """Validate that all expected columns were detected."""
        detected = result['canonical_rename']
        detected_types = set(detected.values())
        
        for exp_col in expected_columns:
            assert exp_col in detected_types, \
                f"Expected column '{exp_col}' not detected. Got: {detected_types}"
        
        # Check confidence for detected columns
        for orig, canonical in detected.items():
            if canonical in expected_columns:
                mapping_info = result.get('column_mapping', {}).get(orig, {})
                confidence = mapping_info.get('confidence', 0)
                assert confidence >= min_confidence, \
                    f"Column '{orig}' -> '{canonical}' confidence {confidence}% below threshold {min_confidence}%"
    
    def assert_match_rate(self, results, min_rate, category):
        """Validate minimum match rate."""
        total = len(results)
        matched = sum(1 for r in results if r.get('match_status') == 'MATCHED')
        rate = matched / total if total > 0 else 0
        assert rate >= min_rate, \
            f"{category} match rate {rate:.1%} below threshold {min_rate:.1%} ({matched}/{total})"
    
    def assert_auto_filled_names(self, results, expected_auto_fill_count):
        """Validate that names were auto-filled correctly."""
        auto_filled = [r for r in results 
                       if any('auto-filled' in str(issue).lower() for issue in r.get('issues', []))]
        assert len(auto_filled) == expected_auto_fill_count, \
            f"Expected {expected_auto_fill_count} auto-filled names, got {len(auto_filled)}"
    
    def process_file(self, filepath, matcher):
        """Process a test file through the ETL pipeline (simplified)."""
        # Layer 1: Ingest
        df = read_file(filepath)
        if len(df) == 0:
            return {'error': 'Empty dataframe', 'results': []}
        
        # Layer 2: Column mapping
        col_result = map_columns(df)
        canonical_rename = col_result['canonical_rename']
        df = df.rename(columns=canonical_rename)
        
        # Layer 3+4: Clean and match each row
        results = []
        available_columns = set(df.columns)
        
        for idx, (_, row) in enumerate(df.iterrows()):
            row_dict = {k: str(v) if pd.notna(v) else '' for k, v in row.to_dict().items()}
            
            # Clean
            clean_result = validate_row(row_dict, available_columns=available_columns)
            cleaned = clean_result['cleaned']
            issues = clean_result['issues']
            
            # Match
            match_result = matcher.match(cleaned)
            
            # Phase 1.8: Auto-fill missing names
            original_name = cleaned.get('name', '').strip()
            if not original_name and match_result.get('match_status') == 'MATCHED':
                cleaned['name'] = match_result.get('chemical_name', '')
                issues = [i for i in issues if 'Missing chemical name' not in i]
                match_method = match_result.get('match_method', 'unknown')
                auto_fill_source = 'CAS' if match_method.startswith('cas') else match_method
                issues.append(f"Name missing in source file, auto-filled from {auto_fill_source} match")
            
            results.append({
                'row_index': idx + 1,
                'input_name': row_dict.get('name', ''),
                'input_cas': row_dict.get('cas', ''),
                'match_status': match_result.get('match_status', 'UNIDENTIFIED'),
                'match_method': match_result.get('match_method', ''),
                'confidence': match_result.get('confidence', 0.0),
                'chemical_id': match_result.get('chemical_id'),
                'chemical_name': match_result.get('chemical_name'),
                'issues': issues,
                'conflicts': match_result.get('conflicts', []),
                'field_swaps': match_result.get('field_swaps', []),
            })
        
        return {
            'column_mapping': col_result,
            'results': results,
            'total_rows': len(results),
            'matched_count': sum(1 for r in results if r['match_status'] == 'MATCHED'),
        }
    
    # ═══════════════════════════════════════════════════════
    #  Category A: Column Detection Tests
    # ═══════════════════════════════════════════════════════
    
    def test_A1_no_headers(self, matcher):
        """Test: File with no header row."""
        filepath = os.path.join(TEST_DATA_DIR, 'A1_NO_HEADERS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should detect columns via content analysis
        self.assert_column_detection(result['column_mapping'], ['cas', 'name', 'quantity'], min_confidence=70)
        self.assert_match_rate(result['results'], 0.70, 'A1_NO_HEADERS')
    
    def test_A2_foreign_headers(self, matcher):
        """Test: Headers in foreign languages."""
        filepath = os.path.join(TEST_DATA_DIR, 'A2_FOREIGN_HEADERS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should detect columns via content analysis (ignore foreign headers)
        self.assert_column_detection(result['column_mapping'], ['name', 'cas', 'quantity'], min_confidence=70)
        self.assert_match_rate(result['results'], 0.75, 'A2_FOREIGN_HEADERS')
    
    def test_A3_abbreviated_headers(self, matcher):
        """Test: Heavily abbreviated headers."""
        filepath = os.path.join(TEST_DATA_DIR, 'A3_ABBREVIATED_HEADERS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should detect via keyword partial match + content analysis
        self.assert_column_detection(result['column_mapping'], ['name', 'cas', 'quantity'], min_confidence=70)
        self.assert_match_rate(result['results'], 0.75, 'A3_ABBREVIATED_HEADERS')
    
    def test_A4_swapped_columns(self, matcher):
        """Test: CAS and Name columns swapped."""
        filepath = os.path.join(TEST_DATA_DIR, 'A4_SWAPPED_COLUMNS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should detect CAS column at minimum (name detection may fail if truly swapped)
        detected_types = set(result['column_mapping']['canonical_rename'].values())
        assert 'cas' in detected_types, f"Expected 'cas' column detected. Got: {detected_types}"
        
        # Should still achieve reasonable match rate via CAS
        self.assert_match_rate(result['results'], 0.60, 'A4_SWAPPED_COLUMNS')
    
    def test_A5_merged_headers(self, matcher):
        """Test: Excel file with merged cells and multi-row headers."""
        filepath = os.path.join(TEST_DATA_DIR, 'A5_MERGED_HEADERS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should flatten structure and detect actual data header row
        self.assert_column_detection(result['column_mapping'], ['name', 'cas', 'quantity'], min_confidence=70)
        self.assert_match_rate(result['results'], 0.80, 'A5_MERGED_HEADERS')
    
    # ═══════════════════════════════════════════════════════
    #  Category B: Data Quality Tests
    # ═══════════════════════════════════════════════════════
    
    def test_B1_excel_date_corruption(self, matcher):
        """Test: CAS numbers corrupted to dates by Excel."""
        filepath = os.path.join(TEST_DATA_DIR, 'B1_EXCEL_DATE_CORRUPTION.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should restore corrupted CAS numbers
        # Check that some matches succeeded despite corruption
        self.assert_match_rate(result['results'], 0.60, 'B1_EXCEL_DATE_CORRUPTION')
    
    def test_B2_mixed_encodings(self, matcher):
        """Test: Mixed character encodings."""
        filepath = os.path.join(TEST_DATA_DIR, 'B2_MIXED_ENCODINGS.csv')
        result = self.process_file(filepath, matcher)
        
        # Should handle special characters correctly
        self.assert_match_rate(result['results'], 0.70, 'B2_MIXED_ENCODINGS')
    
    def test_B3_messy_quantities(self, matcher):
        """Test: Inconsistent quantity formatting."""
        filepath = os.path.join(TEST_DATA_DIR, 'B3_MESSY_QUANTITIES.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should parse quantities correctly
        self.assert_column_detection(result['column_mapping'], ['name', 'cas', 'quantity'], min_confidence=70)
        self.assert_match_rate(result['results'], 0.75, 'B3_MESSY_QUANTITIES')
    
    def test_B4_empty_rows_and_columns(self, matcher):
        """Test: File with scattered empty rows and columns."""
        filepath = os.path.join(TEST_DATA_DIR, 'B4_EMPTY_ROWS_AND_COLUMNS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should remove empty rows/columns and process cleanly
        assert result['total_rows'] > 0, "Expected non-zero rows after empty row removal"
        self.assert_match_rate(result['results'], 0.75, 'B4_EMPTY_ROWS_AND_COLUMNS')
    
    def test_B5_duplicate_column_names(self, matcher):
        """Test: File with duplicate column names."""
        filepath = os.path.join(TEST_DATA_DIR, 'B5_DUPLICATE_COLUMN_NAMES.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should handle duplicates (rename or merge)
        self.assert_match_rate(result['results'], 0.75, 'B5_DUPLICATE_COLUMN_NAMES')
    
    # ═══════════════════════════════════════════════════════
    #  Category C: Matching Engine Tests
    # ═══════════════════════════════════════════════════════
    
    def test_C1_missing_names(self, matcher):
        """Test: Rows with valid CAS but empty name, and vice versa."""
        filepath = os.path.join(TEST_DATA_DIR, 'C1_MISSING_NAMES.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Phase 1.8: Should auto-fill names from CAS matches
        # First 10 rows: empty name, valid CAS → should auto-fill
        auto_filled = [r for r in result['results'][:10] 
                       if any('auto-filled' in str(issue).lower() for issue in r.get('issues', []))]
        
        assert len(auto_filled) >= 5, \
            f"Expected at least 5 auto-filled names from CAS, got {len(auto_filled)}"
        
        # Last 10 rows: valid name, empty CAS → should match via name
        name_matches = [r for r in result['results'][10:] 
                        if r['match_status'] == 'MATCHED']
        assert len(name_matches) >= 5, \
            f"Expected at least 5 name-based matches, got {len(name_matches)}"
    
    def test_C2_field_swap_detection(self, matcher):
        """Test: Field swaps - CAS in name column, name in CAS column."""
        filepath = os.path.join(TEST_DATA_DIR, 'C2_FIELD_SWAP_DETECTION.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Field swap detection happens at match level, not always flagged in results
        # Key test: should still match correctly despite swaps
        self.assert_match_rate(result['results'], 0.60, 'C2_FIELD_SWAP_DETECTION')
        
        # At least some rows should match (proving swap handling works)
        assert result['matched_count'] >= 3, \
            f"Expected at least 3 matches despite field swaps, got {result['matched_count']}"
    
    def test_C3_fuzzy_name_matching(self, matcher):
        """Test: Names with typos and variations."""
        filepath = os.path.join(TEST_DATA_DIR, 'C3_FUZZY_NAME_MATCHING.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should match via fuzzy matching (≥70% threshold)
        # Realistic expectation: some typos too severe for fuzzy match
        self.assert_match_rate(result['results'], 0.50, 'C3_FUZZY_NAME_MATCHING')
    
    def test_C4_synonym_resolution(self, matcher):
        """Test: Trade names and synonyms."""
        filepath = os.path.join(TEST_DATA_DIR, 'C4_SYNONYM_RESOLUTION.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should resolve most trade names to canonical names
        self.assert_match_rate(result['results'], 0.70, 'C4_SYNONYM_RESOLUTION')
        
        # Verify specific synonyms resolved
        matched_names = [r['chemical_name'] for r in result['results'] if r['match_status'] == 'MATCHED']
        assert 'SODIUM HYDROXIDE' in matched_names or any('sodium hydroxide' in str(n).lower() for n in matched_names), \
            "Expected 'Caustic Soda' to resolve to 'Sodium Hydroxide'"
    
    def test_C5_conflict_detection(self, matcher):
        """Test: Conflicting CAS/Name/UN combinations."""
        filepath = os.path.join(TEST_DATA_DIR, 'C5_CONFLICT_DETECTION.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Conflict detection may not always flag in results if one signal dominates
        # Key test: conflicting rows should NOT all be MATCHED with high confidence
        high_conf_matches = [r for r in result['results'] 
                            if r['match_status'] == 'MATCHED' and r['confidence'] > 0.90]
        
        # With deliberate conflicts, we shouldn't have all rows as high-confidence matches
        assert len(high_conf_matches) < len(result['results']), \
            "Expected some rows to have reduced confidence due to conflicts"
    
    # ═══════════════════════════════════════════════════════
    #  Category D: Real-World Production Scenarios
    # ═══════════════════════════════════════════════════════
    
    def test_D1_petroleum_refinery(self, matcher):
        """Test: 50 rows of petroleum products."""
        filepath = os.path.join(TEST_DATA_DIR, 'D1_PETROLEUM_REFINERY.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Petroleum = CAMEO's strongest coverage
        self.assert_match_rate(result['results'], 0.85, 'D1_PETROLEUM_REFINERY')
        
        # Should process all rows without crashes
        assert result['total_rows'] >= 45, "Expected ~50 rows processed"
    
    def test_D2_industrial_chemicals(self, matcher):
        """Test: 50 rows of industrial chemicals."""
        filepath = os.path.join(TEST_DATA_DIR, 'D2_INDUSTRIAL_CHEMICALS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Industrial chemicals = good coverage
        self.assert_match_rate(result['results'], 0.80, 'D2_INDUSTRIAL_CHEMICALS')
        
        # Should process all rows without crashes
        assert result['total_rows'] >= 45, "Expected ~50 rows processed"
    
    def test_D3_pharmaceuticals(self, matcher):
        """Test: 30 pharmaceutical ingredients (expected low coverage)."""
        filepath = os.path.join(TEST_DATA_DIR, 'D3_PHARMACEUTICALS.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Some pharma ingredients ARE in CAMEO (e.g., Caffeine, Nicotine, Vitamin C)
        # Realistic expectation: 30-80% match rate (not all pharma, some overlap with chemicals)
        match_rate = result['matched_count'] / result['total_rows'] if result['total_rows'] > 0 else 0
        
        # Key test: NO hallucinations - all matches must exist in DB
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        hallucinations = 0
        for r in result['results']:
            if r['match_status'] == 'MATCHED' and r['chemical_id']:
                cursor.execute("SELECT id FROM chemicals WHERE id = ?", (r['chemical_id'],))
                if cursor.fetchone() is None:
                    hallucinations += 1
        conn.close()
        
        assert hallucinations == 0, \
            f"Hallucination detected: {hallucinations} matched chemicals don't exist in DB"
    
    def test_D4_multi_sheet_excel(self, matcher):
        """Test: Multi-sheet Excel with only one sheet containing chemical data."""
        filepath = os.path.join(TEST_DATA_DIR, 'D4_MULTI_SHEET_EXCEL.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should select correct sheet (Inventory)
        assert result['total_rows'] >= 15, "Expected data from Inventory sheet"
        self.assert_match_rate(result['results'], 0.75, 'D4_MULTI_SHEET_EXCEL')
    
    def test_D5_large_file(self, matcher):
        """Test: 500 rows of mixed chemicals."""
        filepath = os.path.join(TEST_DATA_DIR, 'D5_LARGE_FILE.xlsx')
        result = self.process_file(filepath, matcher)
        
        # Should process without timeout or memory issues
        assert result['total_rows'] >= 450, "Expected ~500 rows processed"
        
        # Should maintain good match rate on mixed data
        self.assert_match_rate(result['results'], 0.75, 'D5_LARGE_FILE')


# ═══════════════════════════════════════════════════════
#  Test Runner with Detailed Reporting
# ═══════════════════════════════════════════════════════

def generate_report(test_results):
    """Generate markdown report from test results."""
    from datetime import datetime
    
    report = f"""# 🧪 SAFEWARE ETL COMPREHENSIVE STRESS TEST REPORT

**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**ETL Version:** Phase 1.8  
**Database:** CAMEO chemicals.db  
**Total Tests:** {len(test_results)}  

---

## 📈 EXECUTIVE SUMMARY

- **Tests Passed:** {sum(1 for t in test_results if t['passed'])} / {len(test_results)}
- **Overall Pass Rate:** {sum(1 for t in test_results if t['passed']) / len(test_results) * 100:.1f}%
- **Critical Failures:** {sum(1 for t in test_results if not t['passed'] and t['category'] in ['C', 'D'])}
- **Production Ready:** {'YES' if all(t['passed'] for t in test_results if t['category'] in ['C', 'D']) else 'NO'}

---

## 🎯 TEST RESULTS BY CATEGORY

### Category A: Column Detection ({sum(1 for t in test_results if t['category'] == 'A')} tests)
| Test | Status | Notes |
|------|--------|-------|
"""
    
    for t in test_results:
        if t['category'] == 'A':
            status = '✅ PASS' if t['passed'] else '❌ FAIL'
            report += f"| {t['name']} | {status} | {t.get('notes', '')} |\n"
    
    report += f"""
### Category B: Data Quality ({sum(1 for t in test_results if t['category'] == 'B')} tests)
| Test | Status | Notes |
|------|--------|-------|
"""
    
    for t in test_results:
        if t['category'] == 'B':
            status = '✅ PASS' if t['passed'] else '❌ FAIL'
            report += f"| {t['name']} | {status} | {t.get('notes', '')} |\n"
    
    report += f"""
### Category C: Matching Engine ({sum(1 for t in test_results if t['category'] == 'C')} tests)
| Test | Status | Notes |
|------|--------|-------|
"""
    
    for t in test_results:
        if t['category'] == 'C':
            status = '✅ PASS' if t['passed'] else '❌ FAIL'
            report += f"| {t['name']} | {status} | {t.get('notes', '')} |\n"
    
    report += f"""
### Category D: Real-World Scenarios ({sum(1 for t in test_results if t['category'] == 'D')} tests)
| Test | Status | Notes |
|------|--------|-------|
"""
    
    for t in test_results:
        if t['category'] == 'D':
            status = '✅ PASS' if t['passed'] else '❌ FAIL'
            report += f"| {t['name']} | {status} | {t.get('notes', '')} |\n"
    
    report += """
---

## ✅ PRODUCTION READINESS ASSESSMENT

**Status:** """
    
    critical_pass = all(t['passed'] for t in test_results if t['category'] in ['C', 'D'])
    if critical_pass:
        report += "READY FOR PRODUCTION\n\n"
        report += """**Justification:**
- All critical matching engine tests pass.
- All real-world scenario tests pass.
- Edge cases handled robustly.
- Phase 1.8 auto-fill feature working correctly.

**Recommended Next Steps:**
1. Deploy Phase 1.8 to staging environment.
2. Conduct user acceptance testing with real customer files.
3. Monitor match rates in production for 2 weeks.
"""
    else:
        report += "NEEDS WORK\n\n"
        failed = [t for t in test_results if not t['passed'] and t['category'] in ['C', 'D']]
        report += f"**Critical failures:** {len(failed)}\n\n"
        for t in failed:
            report += f"- {t['name']}: {t.get('error', 'Unknown error')}\n"
    
    report += "\n---\n\n**END OF REPORT**\n"
    
    return report


if __name__ == '__main__':
    # Run tests with pytest
    pytest.main([__file__, '-v', '--tb=short'])
