# 🧪 CAMEO FULL DATABASE STRESS TEST REPORT

**Date:** 2026-06-14 20:27:48  
**Test File:** FULL_DATABASE_EXPORT.xlsx  
**File Size:** 0.29 MB  
**Total Chemicals:** 5097  

---

## 📈 EXECUTIVE SUMMARY

- **Total Rows Processed:** 5097
- **Match Rate:** 97.4%
- **Total Processing Time:** 13.41 seconds
- **Processing Rate:** 380.0 rows/second
- **Peak Memory Usage:** 200.48 MB
- **Status:** ✅ **PASSED**

---

## ⏱️ PERFORMANCE METRICS

### Processing Time Breakdown

| Phase | Time (seconds) | Percentage |
|-------|----------------|------------|
| File Ingestion | 0.62 | 4.6% |
| Column Mapping | 0.01 | 0.0% |
| Chemical Matching | 12.79 | 95.3% |
| **Total** | **13.41** | **100%** |

**Processing Rate:** 380.0 rows/second

### Memory Usage

| Metric | Value (MB) |
|--------|------------|
| Start Memory | 172.96 |
| After Ingestion | 173.08 |
| After Column Mapping | 173.08 |
| After Matching | 200.48 |
| **Peak Memory** | **200.48** |
| Memory Increase | 27.53 |

---

## 🎯 MATCHING RESULTS

### Overall Match Statistics

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ MATCHED | 4965 | 97.4% |
| ⚠️ REVIEW_REQUIRED | 129 | 2.5% |
| ❌ UNIDENTIFIED | 3 | 0.1% |
| **Total** | **5097** | **100%** |

### Confidence Statistics (MATCHED only)

| Metric | Value |
|--------|-------|
| Average Confidence | 0.9995 |
| Minimum Confidence | 0.8081 |
| Maximum Confidence | 1.0000 |

---

## ✅ VALIDATION RESULTS

| Check | Status | Details |
|-------|--------|---------|
| All rows processed | ✅ PASS | 5097 rows processed |
| Match rate ≥ 95% | ✅ PASS | 97.4% match rate |
| Processing time < 5 min | ✅ PASS | 13.4s < 300s |
| Peak memory < 2GB | ✅ PASS | 200.5MB < 2048MB |
| No crashes | ✅ PASS | Completed successfully |

---

## 🔍 ANALYSIS

### Performance Assessment

**Processing Speed:** 380.0 rows/second is excellent for a dataset of this size.

**Memory Efficiency:** Peak memory usage of 200.48 MB for 5097 rows is excellent.

**Match Accuracy:** 97.4% match rate on CAMEO's own data is very good.

### Bottleneck Analysis

The matching phase took 95.3% of total processing time, which is expected as it involves:
- Database lookups for each chemical
- Multi-signal fusion (CAS, name, formula, UN)
- Fuzzy matching for name variations
- Semantic scoring and safety veto checks

### Scalability

Based on these results:
- **10,000 rows:** Estimated 26.3 seconds
- **50,000 rows:** Estimated 2.2 minutes
- **100,000 rows:** Estimated 4.4 minutes

Memory usage scales linearly, so 100K rows would require approximately 3933 MB.

---

## 🎉 CONCLUSION

The SAFEWARE ETL system successfully processed the **complete CAMEO database** (5097 chemicals) with:

✅ **97.4% match rate** (near-perfect accuracy on CAMEO data)  
✅ **380.0 rows/second** processing speed  
✅ **200.48 MB** peak memory (efficient resource usage)  
✅ **No crashes or timeouts** (robust and stable)  

**The system is production-ready for large-scale chemical inventory processing.**

---

**Report Generated:** 2026-06-14 20:28:01  
**Test Duration:** 13.41 seconds  

---

**END OF REPORT**
