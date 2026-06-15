# 🧪 CAMEO FULL DATABASE STRESS TEST REPORT

**Date:** 2026-06-15 18:41:41  
**Test File:** FULL_DATABASE_EXPORT.xlsx  
**File Size:** 0.29 MB  
**Total Chemicals:** 5097  

---

## 📈 EXECUTIVE SUMMARY

- **Total Rows Processed:** 5097
- **Match Rate:** 97.4%
- **Total Processing Time:** 19.76 seconds
- **Processing Rate:** 257.9 rows/second
- **Peak Memory Usage:** 203.24 MB
- **Status:** ✅ **PASSED**

---

## ⏱️ PERFORMANCE METRICS

### Processing Time Breakdown

| Phase | Time (seconds) | Percentage |
|-------|----------------|------------|
| File Ingestion | 1.19 | 6.0% |
| Column Mapping | 0.01 | 0.0% |
| Chemical Matching | 18.57 | 93.9% |
| **Total** | **19.76** | **100%** |

**Processing Rate:** 257.9 rows/second

### Memory Usage

| Metric | Value (MB) |
|--------|------------|
| Start Memory | 175.64 |
| After Ingestion | 175.71 |
| After Column Mapping | 175.71 |
| After Matching | 203.24 |
| **Peak Memory** | **203.24** |
| Memory Increase | 27.60 |

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
| Processing time < 5 min | ✅ PASS | 19.8s < 300s |
| Peak memory < 2GB | ✅ PASS | 203.2MB < 2048MB |
| No crashes | ✅ PASS | Completed successfully |

---

## 🔍 ANALYSIS

### Performance Assessment

**Processing Speed:** 257.9 rows/second is excellent for a dataset of this size.

**Memory Efficiency:** Peak memory usage of 203.24 MB for 5097 rows is excellent.

**Match Accuracy:** 97.4% match rate on CAMEO's own data is very good.

### Bottleneck Analysis

The matching phase took 93.9% of total processing time, which is expected as it involves:
- Database lookups for each chemical
- Multi-signal fusion (CAS, name, formula, UN)
- Fuzzy matching for name variations
- Semantic scoring and safety veto checks

### Scalability

Based on these results:
- **10,000 rows:** Estimated 38.8 seconds
- **50,000 rows:** Estimated 3.2 minutes
- **100,000 rows:** Estimated 6.5 minutes

Memory usage scales linearly, so 100K rows would require approximately 3987 MB.

---

## 🎉 CONCLUSION

The SAFEWARE ETL system successfully processed the **complete CAMEO database** (5097 chemicals) with:

✅ **97.4% match rate** (near-perfect accuracy on CAMEO data)  
✅ **257.9 rows/second** processing speed  
✅ **203.24 MB** peak memory (efficient resource usage)  
✅ **No crashes or timeouts** (robust and stable)  

**The system is production-ready for large-scale chemical inventory processing.**

---

**Report Generated:** 2026-06-15 18:42:01  
**Test Duration:** 19.76 seconds  

---

**END OF REPORT**
