"""Regression tests for the July 2026 safety-critical reactivity engine audit fixes.

Covers:
- P0-1: same-group pairs must honour real DB self-reactivity rules instead of
  a hardcoded COMPATIBLE shortcut.
- P1-1: the CAUTION/NO_DATA priority-2 tie in aggregation must resolve to
  NO_DATA (not always CAUTION) when an unknown rule contributed.
- P1-2: WATER_REACTIVE/AIR_REACTIVE self-hazard detection must match the
  hyphenated form used throughout chemicals.db ("water-reactive"), not just
  the spaced form ("water reactive").
"""

import os
import sqlite3

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'chemicals.db')


def _engine():
    return ReactivityEngine(DB_PATH)


def _self_pair_groups():
    """(incompatible_group, caution_group, no_rule_group) verified against chemicals.db."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT react1 FROM reactivity WHERE react1 = react2 AND pair_compatibility = 'Incompatible'")
    incompatible_group = cursor.fetchone()[0]
    cursor.execute("SELECT react1 FROM reactivity WHERE react1 = react2 AND pair_compatibility = 'Caution' LIMIT 1")
    caution_group = cursor.fetchone()[0]
    cursor.execute("SELECT id FROM reacts")
    all_groups = {row[0] for row in cursor.fetchall()}
    cursor.execute("SELECT react1 FROM reactivity WHERE react1 = react2")
    self_ruled = {row[0] for row in cursor.fetchall()}
    no_rule_group = next(iter(all_groups - self_ruled))
    conn.close()
    return incompatible_group, caution_group, no_rule_group


def test_self_pair_honours_incompatible_db_rule():
    """P0-1: a same-group pair with an Incompatible self-rule must NOT be
    silently reported as COMPATIBLE by a hardcoded shortcut."""
    incompatible_group, _, _ = _self_pair_groups()
    engine = _engine()
    rule = engine._get_rule(incompatible_group, incompatible_group)
    assert rule['compatibility'] == Compatibility.INCOMPATIBLE


def test_self_pair_honours_caution_db_rule():
    """P0-1: a same-group pair with a Caution self-rule must not be upgraded
    to COMPATIBLE."""
    _, caution_group, _ = _self_pair_groups()
    engine = _engine()
    rule = engine._get_rule(caution_group, caution_group)
    assert rule['compatibility'] == Compatibility.CAUTION


def test_self_pair_defaults_compatible_when_no_db_rule_exists():
    """P0-1: same-group pairs with NO explicit self-rule in the DB still
    default to COMPATIBLE (a group is compatible with itself unless the data
    says otherwise) — the fix must not regress this common case."""
    _, _, no_rule_group = _self_pair_groups()
    engine = _engine()
    rule = engine._get_rule(no_rule_group, no_rule_group)
    assert rule['compatibility'] == Compatibility.COMPATIBLE


def test_water_reactive_hyphenated_hazard_detected():
    """P1-2: chemicals.db stores 'water-reactive' (hyphen, 487 chemicals);
    the keyword scan must match it, not just the spaced form."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM chemicals WHERE lower(special_hazards) LIKE '%water-reactive%' LIMIT 1")
    chem_id = cursor.fetchone()[0]
    conn.close()

    engine = _engine()
    hazards = engine._get_special_hazards(chem_id)
    hazard_types = [h['type'] for h in hazards]
    assert 'WATER_REACTIVE' in hazard_types


def test_air_reactive_hyphenated_hazard_detected():
    """P1-2: chemicals.db stores 'air-reactive' (hyphen, 161 chemicals);
    same hyphenation bug as water-reactive."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM chemicals WHERE lower(special_hazards) LIKE '%air-reactive%' LIMIT 1")
    chem_id = cursor.fetchone()[0]
    conn.close()

    engine = _engine()
    hazards = engine._get_special_hazards(chem_id)
    hazard_types = [h['type'] for h in hazards]
    assert 'AIR_REACTIVE' in hazard_types


def test_resolve_compatibility_tie_break_no_data():
    """P1-1: at priority 2 (CAUTION/NO_DATA tie), an unknown-rule contribution
    must resolve to NO_DATA, not be silently downgraded to CAUTION."""
    engine = _engine()
    assert engine._resolve_compatibility(2, saw_no_data=True) == Compatibility.NO_DATA


def test_resolve_compatibility_tie_break_caution():
    """P1-1: at priority 2 with no unknown-rule contribution, the result is
    a genuine CAUTION (e.g. a known Caution-level DB rule)."""
    engine = _engine()
    assert engine._resolve_compatibility(2, saw_no_data=False) == Compatibility.CAUTION


def test_resolve_compatibility_incompatible_priority():
    engine = _engine()
    assert engine._resolve_compatibility(3, saw_no_data=True) == Compatibility.INCOMPATIBLE


def test_resolve_compatibility_compatible_priority():
    engine = _engine()
    assert engine._resolve_compatibility(1, saw_no_data=False) == Compatibility.COMPATIBLE


def test_pair_with_only_missing_rule_reports_no_data_not_caution():
    """P1-1 integration: two chemicals whose only shared-relevant groups have
    NO reactivity rule between them (a genuine unknown) must be reported as
    NO_DATA end-to-end through _analyze_pair, so that downstream consumers
    like auto-arrange (which isolates NO_DATA but allows CAUTION) treat them
    correctly as an unknown pair."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM reacts")
    all_groups = sorted(row[0] for row in cursor.fetchall())
    cursor.execute("SELECT react1, react2 FROM reactivity")
    ruled_pairs = {tuple(sorted((r[0], r[1]))) for r in cursor.fetchall()}
    conn.close()

    # Find two distinct groups with no rule row between them at all.
    unruled_pair = None
    for i, g1 in enumerate(all_groups):
        for g2 in all_groups[i + 1:]:
            if (g1, g2) not in ruled_pairs:
                unruled_pair = (g1, g2)
                break
        if unruled_pair:
            break
    assert unruled_pair is not None, "expected at least one unruled group pair in chemicals.db"

    engine = _engine()
    result = engine._analyze_pair(
        chem_a_id=1, chem_b_id=2,
        chem_a_name='Test A', chem_b_name='Test B',
        groups_a=[unruled_pair[0]], groups_b=[unruled_pair[1]],
    )
    assert result.compatibility == Compatibility.NO_DATA
