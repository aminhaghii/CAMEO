"""
report.py — Generate quality summary JSON from processed staging rows.
"""

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


def generate_summary(db_path: str, batch_id: str) -> dict:
    """
    Read all staging rows for a batch and produce a quality report.

    Returns:
        {
            'total_rows': int,
            'matched': int,
            'unmatched': int,
            'ambiguous': int,
            'error': int,
            'match_rate': float,          # 0.0 - 1.0
            'avg_quality_score': float,
            'avg_confidence': float,
            'method_breakdown': { 'exact_cas': N, 'fuzzy_name': N, ... },
            'top_issues': [ ('Invalid CAS: ...', count), ... ],
            'needs_review': [ { row summary }, ... ],
        }
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT match_status, match_method, quality_score, confidence,
               raw_data, cleaned_data, issues, chemical_id, row_index
        FROM inventory_staging
        WHERE batch_id = ?
        ORDER BY row_index
    """, (batch_id,))
    rows = cursor.fetchall()
    conn.close()

    total = len(rows)
    if total == 0:
        return {'total_rows': 0, 'matched': 0, 'unmatched': 0,
                'ambiguous': 0, 'error': 0, 'match_rate': 0.0,
                'avg_quality_score': 0, 'avg_confidence': 0,
                'method_breakdown': {}, 'top_issues': [], 'needs_review': []}

    matched = 0
    unmatched = 0
    ambiguous = 0
    error_count = 0
    total_score = 0
    total_confidence = 0
    method_counts = {}
    issue_counts = {}
    needs_review = []

    for row in rows:
        status = row['match_status']
        method = row['match_method'] or 'unmatched'
        score = row['quality_score'] or 0
        conf = row['confidence'] or 0

        if status == 'matched':
            matched += 1
        elif status == 'ambiguous':
            ambiguous += 1
        elif status == 'error':
            error_count += 1
        else:
            unmatched += 1

        total_score += score
        total_confidence += conf
        method_counts[method] = method_counts.get(method, 0) + 1

        # Count issues
        issues_json = row['issues']
        if issues_json:
            try:
                issue_list = json.loads(issues_json)
                for issue in issue_list:
                    issue_counts[issue] = issue_counts.get(issue, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass

        # Collect rows that need review
        if status in ('unmatched', 'ambiguous', 'error') or score < 60:
            raw = {}
            cleaned = {}
            try:
                raw = json.loads(row['raw_data']) if row['raw_data'] else {}
                cleaned = json.loads(row['cleaned_data']) if row['cleaned_data'] else {}
            except (json.JSONDecodeError, TypeError):
                pass

            needs_review.append({
                'row_index': row['row_index'],
                'name': raw.get('name', cleaned.get('name', '?')),
                'cas': raw.get('cas', ''),
                'match_status': status,
                'match_method': method,
                'confidence': conf,
                'quality_score': score,
                'chemical_id': row['chemical_id'],
                'issues': json.loads(issues_json) if issues_json else [],
            })

    # Top issues sorted by frequency
    top_issues = sorted(issue_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        'total_rows': total,
        'matched': matched,
        'unmatched': unmatched,
        'ambiguous': ambiguous,
        'error': error_count,
        'match_rate': round(matched / total, 3) if total else 0,
        'avg_quality_score': round(total_score / total, 1) if total else 0,
        'avg_confidence': round(total_confidence / total, 3) if total else 0,
        'method_breakdown': method_counts,
        'top_issues': top_issues,
        'needs_review': needs_review,
    }
