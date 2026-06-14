"""
Test Auto-Arrange Algorithm: Before vs After Fix
"""

import os
import sys
import sqlite3
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from logic.reactivity_engine import ReactivityEngine
from logic.constants import Compatibility

DB_PATH = os.path.join(os.path.dirname(__file__), 'backend', 'data', 'chemicals.db')

os.environ['PYTHONIOENCODING'] = 'utf-8'


def get_test_chemicals(n=20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM chemicals ORDER BY RANDOM() LIMIT ?", (n,))
    chems = [dict(r) for r in cur.fetchall()]
    for c in chems:
        cur.execute("SELECT react_id FROM mm_chemical_react WHERE chem_id = ?", (c['id'],))
        c['reactive_groups'] = [r[0] for r in cur.fetchall()]
    conn.close()
    return chems


def build_sections(count):
    return [{'id': i + 1, 'name': f'Section {i + 1}'} for i in range(count)]


def build_placements(chemicals):
    return [{
        'placement_id': i + 1,
        'chemical_id': c['id'],
        'chemical_name': c['name'],
        'reactive_groups': c.get('reactive_groups', []),
    } for i, c in enumerate(chemicals)]


def build_conflict_graph(placements, engine, blocking_set):
    adjacency = {p['placement_id']: set() for p in placements}
    for i, pa in enumerate(placements):
        for pb in placements[i + 1:]:
            res = engine._analyze_pair(
                pa['chemical_id'], pb['chemical_id'],
                pa['chemical_name'], pb['chemical_name'],
                pa.get('reactive_groups', []), pb.get('reactive_groups', []),
            )
            if res.compatibility in blocking_set:
                adjacency[pa['placement_id']].add(pb['placement_id'])
                adjacency[pb['placement_id']].add(pa['placement_id'])
    return adjacency


def greedy_place(sections, placements, adjacency):
    occupants = {s['id']: [] for s in sections}
    mapping = {}
    unplaced = []
    ordered = sorted(placements, key=lambda p: -len(adjacency[p['placement_id']]))
    for pl in ordered:
        pid = pl['placement_id']
        target = None
        for s in sections:
            sid = s['id']
            if all(o['placement_id'] not in adjacency[pid] for o in occupants[sid]):
                target = sid
                break
        mapping[pid] = target
        if target is None:
            unplaced.append(pl)
        else:
            occupants[target].append(pl)
    return mapping, occupants, unplaced


def count_edges(adj):
    return sum(len(v) for v in adj.values()) // 2


def find_minimum(chemicals, engine, blocking_set, max_s=15):
    placements = build_placements(chemicals)
    adj = build_conflict_graph(placements, engine, blocking_set)
    for n in range(1, max_s + 1):
        secs = build_sections(n)
        _, _, unplaced = greedy_place(secs, placements, adj)
        if len(unplaced) == 0:
            return n, count_edges(adj)
    return max_s, count_edges(adj)


def main():
    print("=" * 70)
    print("  AUTO-ARRANGE ALGORITHM: OLD vs NEW")
    print("=" * 70)

    engine = ReactivityEngine(DB_PATH)
    BLOCK_OLD = {Compatibility.INCOMPATIBLE, Compatibility.CAUTION, Compatibility.NO_DATA}
    BLOCK_NEW = {Compatibility.INCOMPATIBLE}

    for test_size in [15, 20, 25, 30]:
        chemicals = get_test_chemicals(test_size)
        if len(chemicals) < test_size:
            chemicals = get_test_chemicals(len(chemicals))
            test_size = len(chemicals)

        placements = build_placements(chemicals)

        adj_old = build_conflict_graph(placements, engine, BLOCK_OLD)
        adj_new = build_conflict_graph(placements, engine, BLOCK_NEW)

        edges_old = count_edges(adj_old)
        edges_new = count_edges(adj_new)

        min_old, _ = find_minimum(chemicals, engine, BLOCK_OLD)
        min_new, _ = find_minimum(chemicals, engine, BLOCK_NEW)

        # Count compat breakdown
        compat_counts = {}
        for pa in placements:
            for pb in placements[pa['placement_id']:]:
                res = engine._analyze_pair(
                    pa['chemical_id'], pb['chemical_id'],
                    pa['chemical_name'], pb['chemical_name'],
                    pa.get('reactive_groups', []), pb.get('reactive_groups', []),
                )
                c = str(res.compatibility.value)
                compat_counts[c] = compat_counts.get(c, 0) + 1

        print(f"\n{'='*60}")
        print(f"  TEST: {test_size} chemicals")
        print(f"{'='*60}")
        print(f"  Compatibility breakdown:")
        for k, v in sorted(compat_counts.items()):
            print(f"    {k:<20} {v:>5} pairs")
        print(f"")
        print(f"  Conflict edges (graph density):")
        print(f"    OLD (INCOMP+CAUT+NODATA): {edges_old}")
        print(f"    NEW (INCOMP only):        {edges_new}")
        print(f"    Reduction:                {edges_old - edges_new} ({(edges_old - edges_new) * 100 // max(edges_old, 1)}%)")
        print(f"")
        print(f"  Minimum sections needed (greedy):")
        print(f"    OLD: {min_old} sections")
        print(f"    NEW: {min_new} sections")
        print(f"    Saved: {min_old - min_new} sections")
        print(f"")

        # Run actual placement
        secs_old = build_sections(min_old)
        secs_new = build_sections(min_new)
        t0 = time.time()
        map_old, occ_old, unp_old = greedy_place(secs_old, placements, adj_old)
        t1 = time.time()
        map_new, occ_new, unp_new = greedy_place(secs_new, placements, adj_new)
        t2 = time.time()

        print(f"  Placement results:")
        print(f"    OLD: {len(placements) - len(unp_old)}/{len(placements)} placed, {len(unp_old)} unplaced, {t1-t0:.3f}s")
        print(f"    NEW: {len(placements) - len(unp_new)}/{len(placements)} placed, {len(unp_new)} unplaced, {t2-t1:.3f}s")
        if unp_old:
            print(f"    OLD unplaced: {[u['chemical_name'][:30] for u in unp_old[:5]]}")
        if unp_new:
            print(f"    NEW unplaced: {[u['chemical_name'][:30] for u in unp_new[:5]]}")

        # Section distribution
        print(f"\n  Section distribution (NEW):")
        for sid, occ in sorted(occ_new.items()):
            names = [o['chemical_name'][:25] for o in occ]
            print(f"    Section {sid}: {len(occ)} chemicals - {', '.join(names[:5])}{'...' if len(names) > 5 else ''}")

    print(f"\n{'='*70}")
    print("  CONCLUSION:")
    print("  OLD: CAUTION+NO_DATA block -> dense graph -> many sections")
    print("  NEW: Only INCOMPATIBLE block -> sparse graph -> fewer sections")
    print("  SAFETY: _validate_layout_update still blocks CAUTION unless admin")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
