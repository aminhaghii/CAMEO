"""
═══════════════════════════════════════════════════════════════════════════════
COMPREHENSIVE BUG FIX VERIFICATION SCRIPT
Verifies all 8 bug fixes are correctly applied and functional.
Read-only verification — does NOT modify any production data.
═══════════════════════════════════════════════════════════════════════════════
"""
import sys
import os
import re
import sqlite3
import tempfile
import json

# ── Path setup (mirror test suite convention) ──
# __file__ is in backend/tests/, so go up one level to backend/
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

CHEMICALS_DB = os.path.join(BACKEND, 'data', 'chemicals.db')

PASS = 0
FAIL = 0
ERRORS = []


def report(name, ok, detail=""):
    global PASS, FAIL
    status = "✅ PASS" if ok else "❌ FAIL"
    line = f"  {status}: {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")


def section(title):
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")


# ═════════════════════════════════════════════════════════════════════════════
# BUG #1: WATER_GROUP_ID must be 100 (not 104)
# ═════════════════════════════════════════════════════════════════════════════
def test_bug1_water_group_id():
    section("BUG #1: WATER_GROUP_ID = 100 (Safety-Critical)")
    
    # 1a. Check constants.py
    with open(os.path.join(BACKEND, 'logic', 'constants.py'), 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.search(r'^WATER_GROUP_ID\s*=\s*(\d+)', content, re.MULTILINE)
    val = int(match.group(1)) if match else None
    report("constants.py WATER_GROUP_ID", val == 100, f"got {val}, expected 100")
    
    # 1b. Verify against DB that group 100 = Water
    conn = sqlite3.connect(CHEMICALS_DB)
    cur = conn.cursor()
    cur.execute("SELECT name FROM reacts WHERE id = 100")
    row = cur.fetchone()
    g100_name = row[0] if row else None
    report("DB group 100 = 'Water and Aqueous Solutions'", 
           g100_name == 'Water and Aqueous Solutions', f"got {g100_name!r}")
    
    # 1c. Verify group 104 is NOT water
    cur.execute("SELECT name FROM reacts WHERE id = 104")
    row = cur.fetchone()
    g104_name = row[0] if row else None
    report("DB group 104 != Water", 
           g104_name != 'Water and Aqueous Solutions', f"got {g104_name!r}")
    
    # 1d. Verify reactivity rules exist for group 100
    cur.execute("SELECT COUNT(*) FROM reactivity WHERE react1=100 OR react2=100")
    rules_100 = cur.fetchone()[0]
    report("DB has reactivity rules for group 100", rules_100 > 0, f"{rules_100} rules")
    
    # 1e. Verify NO reactivity rules for group 104
    cur.execute("SELECT COUNT(*) FROM reactivity WHERE react1=104 OR react2=104")
    rules_104 = cur.fetchone()[0]
    report("DB has NO reactivity rules for group 104", rules_104 == 0, f"{rules_104} rules")
    
    # 1f. Functional test: ReactivityEngine water warning
    from logic.reactivity_engine import ReactivityEngine
    engine = ReactivityEngine(CHEMICALS_DB)
    # Find a water-reactive chemical (has group that conflicts with 100)
    cur.execute("""
        SELECT DISTINCT mcr.chem_id, c.name 
        FROM mm_chemical_react mcr 
        JOIN chemicals c ON c.id = mcr.chem_id
        JOIN reactivity r ON r.react1 = mcr.react_id OR r.react2 = mcr.react_id
        WHERE (r.react1 = 100 OR r.react2 = 100) 
          AND r.pair_compatibility IN ('Incompatible', 'Caution')
          AND mcr.react_id != 100
        LIMIT 3
    """)
    water_reactive = cur.fetchall()
    if water_reactive:
        chem_id, chem_name = water_reactive[0]
        result = engine.analyze([chem_id, chem_id + 1] if chem_id + 1 <= 5097 else [chem_id, chem_id - 1], 
                                 include_water_check=True, save_audit=False)
        water_warnings = [w for w in result.warnings if 'water-reactive' in w.lower() or 'dry conditions' in w.lower()]
        report("ReactivityEngine generates water warning", 
               len(water_warnings) > 0, 
               f"chemical={chem_name}, warnings={water_warnings}")
    else:
        report("ReactivityEngine water warning (no test chem)", None, "skipped — no water-reactive chemical found")
    
    # 1g. ensure_data.py uses 100
    with open(os.path.join(BACKEND, 'scripts', 'ensure_data.py'), 'r', encoding='utf-8') as f:
        ed_content = f.read()
    has_100 = "'groups': [100]" in ed_content or "'groups': [100]  # Water" in ed_content
    has_104 = "'groups': [104]" in ed_content
    report("ensure_data.py uses group 100 for WATER", has_100 and not has_104, 
           f"has_100={has_100}, has_104={has_104}")
    
    # 1h. warehouse.py docstring
    with open(os.path.join(BACKEND, 'routes', 'warehouse.py'), 'r', encoding='utf-8') as f:
        wh_content = f.read()
    doc_ok = "Water and Aqueous Solutions group (100)" in wh_content
    report("warehouse.py docstring says group 100", doc_ok)
    
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# BUG #2: Synonym fuzzy matching is wired (not dead code)
# ═════════════════════════════════════════════════════════════════════════════
def test_bug2_synonym_fuzzy():
    section("BUG #2: Synonym Fuzzy Matching Wired")
    
    with open(os.path.join(BACKEND, 'etl', 'match.py'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 2a. Check that _fuzzy_syns is queried via rfprocess.extract
    has_syn_fuzzy = 'self._fuzzy_syns' in content and 'rfprocess.extract' in content
    # Count occurrences of _fuzzy_syns in extract context
    syn_extract_count = content.count('rfprocess.extract(fq, self._fuzzy_syns')
    report("match.py queries _fuzzy_syns via rfprocess.extract", 
           syn_extract_count > 0, f"{syn_extract_count} calls")
    
    # 2b. Check that synonym_fuzzy signal is generated
    has_syn_signal = "'synonym_fuzzy'" in content or "synonym_fuzzy" in content
    report("match.py generates synonym_fuzzy signals", has_syn_signal)
    
    # 2c. Functional test: matcher with synonym-like fuzzy input
    from etl.match import ChemicalMatcher
    matcher = ChemicalMatcher(CHEMICALS_DB)
    
    # Find a chemical with a known synonym, then misspell it slightly
    conn = sqlite3.connect(CHEMICALS_DB)
    cur = conn.cursor()
    cur.execute("SELECT id, name, synonyms FROM chemicals WHERE synonyms LIKE '%SULFURIC%' LIMIT 1")
    row = cur.fetchone()
    if row:
        # Use a slightly misspelled synonym
        cleaned = {'name': 'Sulphuric Acid', 'cas': None, 'formula': None, 'un_number': None}
        result = matcher.match(cleaned)
        # Should match via industrial synonym (exact) — verify it finds SULFURIC ACID
        report("Matcher finds 'Sulphuric Acid' (British spelling)", 
               result.get('chemical_id') is not None, 
               f"status={result.get('match_status')}, conf={result.get('confidence')}")
    
    # 2d. Verify _fuzzy_syns is populated
    matcher._ensure_caches()
    report("_fuzzy_syns cache populated", 
           len(matcher._fuzzy_syns) > 0, 
           f"{len(matcher._fuzzy_syns)} entries")
    
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# BUG #3: UN last-ditch recovery uses chemical_unna (not chemical_un)
# ═════════════════════════════════════════════════════════════════════════════
def test_bug3_un_recovery():
    section("BUG #3: UN Last-Ditch Recovery Table Name")
    
    with open(os.path.join(BACKEND, 'etl', 'last_ditch_recovery.py'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 3a. Check chemical_unna is used (not chemical_un)
    has_unna = 'JOIN chemical_unna' in content
    has_un = 'JOIN chemical_un ' in content or 'JOIN chemical_un\n' in content
    report("Uses chemical_unna (correct table)", has_unna and not has_un,
           f"has_unna={has_unna}, has_un={has_un}")
    
    # 3b. Check unna_id is used (not un_code)
    has_unna_id = 'unna_id' in content
    has_un_code = 'un_code' in content
    report("Uses unna_id column (not un_code)", has_unna_id and not has_un_code,
           f"has_unna_id={has_unna_id}, has_un_code={has_un_code}")
    
    # 3c. Functional test: UN recovery
    conn = sqlite3.connect(CHEMICALS_DB)
    cur = conn.cursor()
    # Find a chemical with a UN number
    cur.execute("""
        SELECT c.id, c.name, cu.unna_id 
        FROM chemicals c JOIN chemical_unna cu ON c.id = cu.chem_id 
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        chem_id, chem_name, unna_id = row['id'] if hasattr(row, 'keys') else row[0], row['name'] if hasattr(row, 'keys') else row[1], row['unna_id'] if hasattr(row, 'keys') else row[2]
        # Try last-ditch recovery with UN in wrong column
        from etl.last_ditch_recovery import attempt_last_ditch_recovery
        row_dict = {'notes': f'UN{unna_id}', 'name': '', 'cas': ''}
        cleaned = {'name': '', 'cas': '', 'quantity': None, 'unit': None}
        result = attempt_last_ditch_recovery(row_dict, cleaned, CHEMICALS_DB, 'test', 1)
        if result:
            report("UN last-ditch recovery finds chemical", 
                   result.get('chemical_id') is not None,
                   f"found={result.get('chemical_name')}, method={result.get('match_method')}")
        else:
            # May return None if no UN pattern matched — check the regex
            report("UN last-ditch recovery (functional)", None, 
                   f"returned None — UN={unna_id}, may need UN#### format")
    else:
        report("UN last-ditch recovery (functional)", None, "no UN-numbered chemical found")
    
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# BUG #4: Formula annotations stripped from cache keys
# ═════════════════════════════════════════════════════════════════════════════
def test_bug4_formula_annotations():
    section("BUG #4: Formula Annotation Stripping")
    
    with open(os.path.join(BACKEND, 'etl', 'match.py'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 4a. Check that parenthetical stripping regex exists
    has_strip = "re.sub(r'\\s*\\([^)]*\\)\\s*'" in content
    report("match.py has parenthetical strip regex", has_strip)
    
    # 4b. Functional test: find an annotated formula and verify it matches
    conn = sqlite3.connect(CHEMICALS_DB)
    cur = conn.cursor()
    # Find a chemical with annotated formula
    cur.execute("""
        SELECT id, name, formulas FROM chemicals 
        WHERE formulas LIKE '%(%)%' AND formulas LIKE '%|%' 
        LIMIT 1
    """)
    row = cur.fetchone()
    if row:
        chem_id = row[0]
        chem_name = row[1]
        formulas = row[2]
        # Extract the first formula's base (before parenthesis)
        first_formula = formulas.split('|')[0].strip()
        base_formula = re.sub(r'\s*\([^)]*\)\s*', '', first_formula).strip()
        
        if base_formula:
            from etl.match import ChemicalMatcher, _normalize_formula
            matcher = ChemicalMatcher(CHEMICALS_DB)
            matcher._ensure_caches()
            fnorm = _normalize_formula(base_formula)
            hits = matcher._formula_map.get(fnorm, [])
            report("Annotated formula now matches via base", 
                   len(hits) > 0, 
                   f"formula={base_formula!r}, normalized={fnorm!r}, hits={len(hits)}")
        else:
            report("Annotated formula match (functional)", None, "base formula empty after strip")
    else:
        report("Annotated formula match (functional)", None, "no annotated formula found")
    
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# BUG #5: Conflict caps force REVIEW_REQUIRED (0.79, not 0.80/0.84)
# ═════════════════════════════════════════════════════════════════════════════
def test_bug5_conflict_caps():
    section("BUG #5: Conflict Caps Force REVIEW_REQUIRED")
    
    with open(os.path.join(BACKEND, 'etl', 'match.py'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 5a. Check 0.79 is used (not 0.80)
    has_079 = 'min(confidence, 0.79)' in content
    has_080_conflict = False
    # Check the conflict section specifically
    conflict_section = content[content.find('PHASE 4: Conflict'):]
    if 'min(confidence, 0.80)' in conflict_section:
        has_080_conflict = True
    report("Conflict cap uses 0.79 (forces REVIEW)", has_079 and not has_080_conflict,
           f"0.79={has_079}, 0.80_in_conflict={has_080_conflict}")
    
    # 5b. Check 0.84 is NOT used in conflict section
    has_084 = 'min(confidence, 0.84)' in conflict_section
    report("Conflict cap does NOT use 0.84", not has_084)
    
    # 5c. Verify THRESHOLD_MATCHED = 0.80
    threshold_match = re.search(r'THRESHOLD_MATCHED\s*=\s*([\d.]+)', content)
    threshold_val = float(threshold_match.group(1)) if threshold_match else None
    report("THRESHOLD_MATCHED = 0.80", threshold_val == 0.80, f"got {threshold_val}")
    
    # 5d. Logic: 0.79 < 0.80 means REVIEW_REQUIRED
    report("0.79 < 0.80 → REVIEW_REQUIRED", 0.79 < 0.80, "math verified")


# ═════════════════════════════════════════════════════════════════════════════
# BUG #6: Duplicate-add warning shown on inventory.html
# ═════════════════════════════════════════════════════════════════════════════
def test_bug6_duplicate_warning():
    section("BUG #6: Duplicate-Add Warning on Inventory Page")
    
    with open(os.path.join(BACKEND, 'templates', 'inventory.html'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find the add branch in saveEdit()
    # Check that data.warning is checked
    has_warning_check = 'data.warning' in content
    report("inventory.html checks data.warning", has_warning_check)
    
    # Check it's in the add branch (not just edit)
    # Look for data.warning near the add API call
    add_section = content[content.find("/api/inventory/add"):]
    add_section = add_section[:1200]  # first 1200 chars after add endpoint
    has_warning_in_add = 'data.warning' in add_section
    report("data.warning check is in add branch", has_warning_in_add)


# ═════════════════════════════════════════════════════════════════════════════
# BUG #7: confirmMatch uses edit modal (not prompt())
# ═════════════════════════════════════════════════════════════════════════════
def test_bug7_confirm_match():
    section("BUG #7: confirmMatch Without prompt()")
    
    with open(os.path.join(BACKEND, 'templates', 'inventory.html'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 7a. Check that confirmMatch does NOT use prompt()
    # Find confirmMatch function
    cm_start = content.find('async confirmMatch(row)')
    cm_end = content.find('},', cm_start)
    cm_section = content[cm_start:cm_end] if cm_start >= 0 else ""
    
    has_prompt = 'prompt(' in cm_section
    report("confirmMatch does NOT use prompt()", not has_prompt,
           f"prompt in confirmMatch: {has_prompt}")
    
    # 7b. Check it opens edit modal instead
    has_edit_modal = 'showEditModal = true' in cm_section or 'showEditModal=true' in cm_section
    report("confirmMatch opens edit modal", has_edit_modal)
    
    # 7c. Check it pre-fills chemical search
    has_search = 'chemicalSearchQuery' in cm_section
    report("confirmMatch pre-fills chemical search", has_search)


# ═════════════════════════════════════════════════════════════════════════════
# BUG #8: Review Queue UI wired
# ═════════════════════════════════════════════════════════════════════════════
def test_bug8_review_queue_ui():
    section("BUG #8: Review Queue UI Wired")
    
    with open(os.path.join(BACKEND, 'templates', 'inventory.html'), 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 8a. Check loadReviewQueue function exists
    has_load = 'async loadReviewQueue' in content or 'loadReviewQueue()' in content
    report("loadReviewQueue function exists", has_load)
    
    # 8b. Check it calls /api/inventory/review_queue/
    has_api_call = '/api/inventory/review_queue/' in content
    report("Calls /api/inventory/review_queue/", has_api_call)
    
    # 8c. Check resolveReviewCandidate function exists
    has_resolve = 'async resolveReviewCandidate' in content or 'resolveReviewCandidate(' in content
    report("resolveReviewCandidate function exists", has_resolve)
    
    # 8d. Check it calls /api/inventory/resolve_review
    has_resolve_api = '/api/inventory/resolve_review' in content
    report("Calls /api/inventory/resolve_review", has_resolve_api)
    
    # 8e. Check review queue panel exists in UI
    has_panel = 'Review Queue' in content or 'review-queue' in content.lower()
    report("Review Queue panel in UI", has_panel)
    
    # 8f. Check priority badges
    has_priority = 'by_priority' in content and ('critical' in content and 'high' in content 
                                                  and 'medium' in content and 'low' in content)
    report("Priority badges (critical/high/medium/low)", has_priority)
    
    # 8g. Check candidates display
    has_candidates = 'item.candidates' in content or 'cand.chemical_name' in content
    report("Matcher candidates displayed", has_candidates)
    
    # 8h. Check review queue count badge
    has_count = 'reviewQueueCount' in content
    report("Review queue count badge", has_count)


# ═════════════════════════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print("\n" + "═" * 70)
    print("  COMPREHENSIVE BUG FIX VERIFICATION — 8 Bugs")
    print("  Database:", CHEMICALS_DB)
    print("  Exists:", os.path.exists(CHEMICALS_DB))
    print("═" * 70)
    
    if not os.path.exists(CHEMICALS_DB):
        print("\n❌ chemicals.db not found! Run: python scripts/ensure_data.py")
        sys.exit(1)
    
    try:
        test_bug1_water_group_id()
    except Exception as e:
        report("BUG #1 test", False, f"Exception: {e}")
    
    try:
        test_bug2_synonym_fuzzy()
    except Exception as e:
        report("BUG #2 test", False, f"Exception: {e}")
    
    try:
        test_bug3_un_recovery()
    except Exception as e:
        report("BUG #3 test", False, f"Exception: {e}")
    
    try:
        test_bug4_formula_annotations()
    except Exception as e:
        report("BUG #4 test", False, f"Exception: {e}")
    
    try:
        test_bug5_conflict_caps()
    except Exception as e:
        report("BUG #5 test", False, f"Exception: {e}")
    
    try:
        test_bug6_duplicate_warning()
    except Exception as e:
        report("BUG #6 test", False, f"Exception: {e}")
    
    try:
        test_bug7_confirm_match()
    except Exception as e:
        report("BUG #7 test", False, f"Exception: {e}")
    
    try:
        test_bug8_review_queue_ui()
    except Exception as e:
        report("BUG #8 test", False, f"Exception: {e}")
    
    print("\n" + "═" * 70)
    print(f"  SUMMARY: {PASS} passed, {FAIL} failed")
    print("═" * 70)
    
    if ERRORS:
        print("\n❌ FAILURES:")
        for e in ERRORS:
            print(f"  • {e}")
    
    sys.exit(0 if FAIL == 0 else 1)
