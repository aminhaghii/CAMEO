"""
header_guard.py — Task 1: Header Guard (Pre-processing)

Detects and removes repeated header rows that appear in the middle or end of data.
This is a surgical helper function injected into the pipeline after column mapping.
"""

import pandas as pd
import logging
from typing import Set

logger = logging.getLogger(__name__)


def remove_repeated_headers(df: pd.DataFrame, batch_id: str = '') -> pd.DataFrame:
    """
    Remove rows that are actually repeated headers (not data).
    
    Strategy:
    - For each row, check if cell values match column names (case-insensitive)
    - If ≥50% of non-empty cells match their column headers, drop the row
    - This prevents legacy files with repeated headers from corrupting data
    
    Args:
        df: DataFrame after column mapping (canonical column names)
        batch_id: Batch ID for logging
        
    Returns:
        DataFrame with repeated header rows removed
        
    Safety:
    - Wrapped in try/except to prevent pipeline crashes
    - Only drops rows with high confidence (≥50% match threshold)
    - Logs all dropped rows for audit trail
    """
    
    if df.empty:
        return df
    
    try:
        original_count = len(df)
        rows_to_drop = []
        
        for idx, row in df.iterrows():
            # Count how many cells match their column name
            matches = 0
            non_empty_cells = 0
            
            for col_name in df.columns:
                cell_value = str(row[col_name]).strip().lower()
                
                # Skip empty cells
                if not cell_value or cell_value in ('nan', 'none', ''):
                    continue
                
                non_empty_cells += 1
                col_name_lower = str(col_name).strip().lower()
                
                # Check for exact or partial match
                # Examples: "CAS Number" matches "cas number", "cas", "cas_number"
                if (cell_value == col_name_lower or 
                    cell_value in col_name_lower or 
                    col_name_lower in cell_value):
                    matches += 1
            
            # If ≥50% of non-empty cells match column names, it's likely a repeated header
            if non_empty_cells > 0 and matches / non_empty_cells >= 0.5:
                rows_to_drop.append(idx)
                logger.info(
                    f"[Batch {batch_id[:8]}] Header Guard: Dropping row {idx} "
                    f"(repeated header detected: {matches}/{non_empty_cells} cells match column names)"
                )
        
        # Drop identified rows
        if rows_to_drop:
            df = df.drop(rows_to_drop).reset_index(drop=True)
            logger.info(
                f"[Batch {batch_id[:8]}] Header Guard: Removed {len(rows_to_drop)} repeated header rows "
                f"({original_count} → {len(df)} rows)"
            )
        
        return df
    
    except Exception as e:
        # Safety: Never crash the pipeline
        logger.warning(f"[Batch {batch_id[:8]}] Header Guard failed (non-critical): {e}")
        return df  # Return original DataFrame if guard fails
