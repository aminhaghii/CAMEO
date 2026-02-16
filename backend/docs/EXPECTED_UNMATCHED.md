# Expected Unmatched Categories

This document lists chemical names/categories that are **expected** to return `UNIDENTIFIED` status during ETL import. These are not bugs or coverage gaps - they represent measurement categories, generic codes, or non-chemical entries.

---

## 1. Particulate Matter (PM) Metrics

**Category**: Air quality measurement metrics, not individual chemicals

| Input Name | Status | Reason |
|---|---|---|
| PM (condensable) | UNIDENTIFIED ✓ | Air quality metric |
| PM10 | UNIDENTIFIED ✓ | Air quality metric |
| PM10 (filterable) | UNIDENTIFIED ✓ | Air quality metric |
| PM2.5 | UNIDENTIFIED ✓ | Air quality metric |
| PM2.5 (filterable) | UNIDENTIFIED ✓ | Air quality metric |

**Explanation**: These are EPA air quality measurement categories for particulate matter of different sizes. They are not individual chemical substances and should not be matched to the CAMEO Chemicals database.

**User Action**: These can be safely ignored or filtered out during import review.

---

## 2. Generic Group Codes (4-digit)

**Category**: Chemical group identifiers, not CAS numbers

| Input Code | Status | Reason |
|---|---|---|
| 1080 | UNIDENTIFIED ✓ | Generic group code (rejected by CAS validation) |
| 1115 | UNIDENTIFIED ✓ | Generic group code (rejected by CAS validation) |
| 1150 | UNIDENTIFIED ✓ | Generic group code (rejected by CAS validation) |
| 9901 | UNIDENTIFIED ✓ | Generic group code (rejected by CAS validation) |

**Explanation**: 4-digit codes are used in some inventory systems to represent chemical groups or categories, but they are not valid CAS Registry Numbers. The ETL system correctly rejects these during CAS validation (see `backend/etl/clean.py:validate_cas()`).

**User Action**: If these represent actual chemicals, the user should provide proper CAS numbers or chemical names.

---

## 3. Chemical Families/Groups

**Category**: Broad chemical families without specific CAS numbers

| Input Name | Status | Reason |
|---|---|---|
| Nitrogen oxides (NOx) | REVIEW/UNIDENTIFIED | Generic family term |
| Chromium compounds | REVIEW/UNIDENTIFIED | Generic family term |
| Diesel exhaust | UNIDENTIFIED ✓ | Complex mixture, not single chemical |

**Explanation**: These represent families or mixtures of chemicals rather than individual substances. The CAMEO database contains specific chemicals (e.g., "NITROGEN DIOXIDE", "CHROMIUM TRIOXIDE") but not generic family terms.

**User Action**: 
- For NOx: Specify which oxide (NO, NO2, N2O, etc.)
- For Chromium compounds: Specify oxidation state and compound (Chromium III chloride, Chromium VI oxide, etc.)
- For Diesel exhaust: This is a complex mixture and cannot be matched to a single chemical

---

## 4. Database Coverage Gaps (Valid CAS, Not in CAMEO)

**Category**: Valid chemicals with proper CAS numbers, but not in CAMEO Chemicals database

| CAS Number | Chemical Name | Status | Reason |
|---|---|---|---|
| 590-19-2 | 1,2-Butadiene | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 598-25-4 | 1,1-Dimethylallene | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 591-95-7 | 1,2-Pentadiene | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 1574-41-0 | cis-1,3-Pentadiene | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 2004-70-8 | trans-1,3-Pentadiene | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 198-55-0 | Perylene | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 13827-32-2 | Sulfur monoxide | REVIEW/UNIDENTIFIED | Not in CAMEO DB |
| 16065-83-1 | Chromium III compounds | REVIEW/UNIDENTIFIED | Not in CAMEO DB |

**Explanation**: These are valid chemicals with correct CAS numbers (verified by checksum), but they are not present in the CAMEO Chemicals database. This is a **true coverage gap**, not a bug in the ETL system.

**Database Verification** (2026-02-16):
```sql
-- Verified: None of these CAS numbers exist in chemical_cas table
SELECT COUNT(*) FROM chemical_cas WHERE cas_id IN (
    '590-19-2', '598-25-4', '591-95-7', '1574-41-0',
    '2004-70-8', '198-55-0', '13827-32-2', '16065-83-1'
);
-- Result: 0
```

**User Action**: 
- These chemicals cannot be automatically matched
- User must manually add them to inventory OR
- Contact CAMEO database maintainers to request addition

---

## Expected Match Statistics

For a typical refinery/chemical plant inventory of ~188 chemicals:

```
✓ Matched:       171 (91%)   - Successfully matched to CAMEO DB
⚠ Review:        1-2 (<1%)    - Ambiguous matches requiring user confirmation
✗ Unidentified:  16 (8.5%)    - Expected breakdown:
                               • 8 missing from CAMEO DB (coverage gaps)
                               • 5 PM metrics (not chemicals)
                               • 3 generic codes (invalid CAS)

Coverage Rate:   171/172 valid chemicals = 99.4% ✓
```

**Note**: "Valid chemicals" excludes PM metrics and generic codes, which are not actual chemical substances.

---

## System Behavior

### What is Working Correctly ✓

1. **PM Metrics** → `UNIDENTIFIED` (correct - they're not chemicals)
2. **4-digit codes** → Rejected by CAS validation (correct - they're not CAS numbers)
3. **Missing CAS** → `REVIEW` or `UNIDENTIFIED` (correct - not in database)

### What Would Be a Bug ✗

1. Valid CAS in database → `UNIDENTIFIED` (this would be a lookup failure)
2. Exact name match → `REVIEW` (this would be a matching logic error)
3. PM metrics → `MATCHED` to random chemical (this would be a false positive)

---

## Maintenance

**Last Updated**: 2026-02-16  
**Database Version**: CAMEO Chemicals (5097 chemicals)  
**Verified By**: ETL Phase 1.5 testing

**Update Frequency**: Review this document when:
- CAMEO database is updated with new chemicals
- New categories of expected unmatched entries are discovered
- User reports unexpected UNIDENTIFIED results
