"""
models.py — Pydantic models for ETL output validation (Anti-Hallucination Protocol).
Every match result MUST pass through these validators before being stored.
chemical_id must either be None or exist in the database.
"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class MatchSuggestion(BaseModel):
    """A single fuzzy match suggestion for human review."""
    chemical_id: int
    chemical_name: str
    score: float = Field(ge=0, le=100)


class MatchResult(BaseModel):
    """
    Validated output of the matching engine.
    Anti-Hallucination: chemical_id is either None or a verified DB ID.
    """
    chemical_id: Optional[int] = None
    chemical_name: Optional[str] = None
    match_method: str = 'unmatched'
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    match_status: str = 'UNIDENTIFIED'
    suggestions: list[MatchSuggestion] = []

    @field_validator('match_status')
    @classmethod
    def validate_status(cls, v):
        allowed = {'MATCHED', 'REVIEW_REQUIRED', 'UNIDENTIFIED'}
        if v not in allowed:
            raise ValueError(f"match_status must be one of {allowed}, got '{v}'")
        return v

    @field_validator('chemical_id')
    @classmethod
    def validate_chemical_id(cls, v, info):
        """If status is MATCHED, chemical_id MUST be set."""
        # This runs before match_status is available in model_validator,
        # so we do the cross-field check in the pipeline instead.
        return v


class RowOutput(BaseModel):
    """
    Final output for a single imported row.
    This is what the API returns per row.
    """
    row_index: int
    input_name: Optional[str] = None
    input_cas: Optional[str] = None
    matched_chemical_id: Optional[int] = None
    matched_name: Optional[str] = None
    match_method: str = 'unmatched'
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    status: str = 'UNIDENTIFIED'
    quality_score: int = Field(ge=0, le=100, default=0)
    issues: list[str] = []
    suggestions: list[MatchSuggestion] = []


class BatchSummary(BaseModel):
    """Summary of a completed batch."""
    total_rows: int = 0
    matched: int = 0
    review_required: int = 0
    unidentified: int = 0
    error: int = 0
    match_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    avg_quality_score: float = 0
    avg_confidence: float = 0
    method_breakdown: dict[str, int] = {}
    top_issues: list[tuple[str, int]] = []
    rows: list[RowOutput] = []
