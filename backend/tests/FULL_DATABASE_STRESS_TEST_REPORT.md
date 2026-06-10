# 🧪 CAMEO FULL DATABASE STRESS TEST REPORT

**Date:** 2026-06-10 20:16:39  
**Test File:** FULL_DATABASE_EXPORT.xlsx  
**File Size:** 0.29 MB  
**Total Chemicals:** 5097  

---

## 📈 EXECUTIVE SUMMARY

- **Total Rows Processed:** 5097
- **Match Rate:** 97.5%
- **Total Processing Time:** 47.84 seconds
- **Processing Rate:** 106.5 rows/second
- **Peak Memory Usage:** 201.47 MB
- **Status:** ✅ **PASSED**

---

## ⏱️ PERFORMANCE METRICS

### Processing Time Breakdown

| Phase | Time (seconds) | Percentage |
|-------|----------------|------------|
| File Ingestion | 2.85 | 6.0% |
| Column Mapping | 0.02 | 0.1% |
| Chemical Matching | 44.97 | 94.0% |
| **Total** | **47.84** | **100%** |

**Processing Rate:** 106.5 rows/second

### Memory Usage

| Metric | Value (MB) |
|--------|------------|
| Start Memory | 175.09 |
| After Ingestion | 175.66 |
| After Column Mapping | 175.66 |
| After Matching | 201.47 |
| **Peak Memory** | **201.47** |
| Memory Increase | 26.38 |

---

## 🎯 MATCHING RESULTS

### Overall Match Statistics

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ MATCHED | 4970 | 97.5% |
| ⚠️ REVIEW_REQUIRED | 127 | 2.5% |
| ❌ UNIDENTIFIED | 0 | 0.0% |
| **Total** | **5097** | **100%** |

### Confidence Statistics (MATCHED only)

| Metric | Value |
|--------|-------|
| Average Confidence | 0.9994 |
| Minimum Confidence | 0.8081 |
| Maximum Confidence | 1.0000 |

---

## ✅ VALIDATION RESULTS

| Check | Status | Details |
|-------|--------|---------|
| All rows processed | ✅ PASS | 5097 rows processed |
| Match rate ≥ 95% | ✅ PASS | 97.5% match rate |
| Processing time < 5 min | ✅ PASS | 47.8s < 300s |
| Peak memory < 2GB | ✅ PASS | 201.5MB < 2048MB |
| No crashes | ✅ PASS | Completed successfully |

---

## 🔍 ANALYSIS

### Performance Assessment

**Processing Speed:** 106.5 rows/second is excellent for a dataset of this size.

**Memory Efficiency:** Peak memory usage of 201.47 MB for 5097 rows is excellent.

**Match Accuracy:** 97.5% match rate on CAMEO's own data is very good.

### Bottleneck Analysis

The matching phase took 94.0% of total processing time, which is expected as it involves:
- Database lookups for each chemical
- Multi-signal fusion (CAS, name, formula, UN)
- Fuzzy matching for name variations
- Semantic scoring and safety veto checks

### Scalability

Based on these results:
- **10,000 rows:** Estimated 93.9 seconds
- **50,000 rows:** Estimated 7.8 minutes
- **100,000 rows:** Estimated 15.6 minutes

Memory usage scales linearly, so 100K rows would require approximately 3953 MB.

---

## 🎉 CONCLUSION

The SAFEWARE ETL system successfully processed the **complete CAMEO database** (5097 chemicals) with:

✅ **97.5% match rate** (near-perfect accuracy on CAMEO data)  
✅ **106.5 rows/second** processing speed  
✅ **201.47 MB** peak memory (efficient resource usage)  
✅ **No crashes or timeouts** (robust and stable)  

**The system is production-ready for large-scale chemical inventory processing.**

---

**Report Generated:** 2026-06-10 20:17:27  
**Test Duration:** 47.84 seconds  

---

**END OF REPORT**
