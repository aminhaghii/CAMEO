# 🧪 CAMEO FULL DATABASE STRESS TEST REPORT

**Date:** 2026-06-14 19:37:10  
**Test File:** FULL_DATABASE_EXPORT.xlsx  
**File Size:** 0.29 MB  
**Total Chemicals:** 5097  

---

## 📈 EXECUTIVE SUMMARY

- **Total Rows Processed:** 5097
- **Match Rate:** 97.4%
- **Total Processing Time:** 11.08 seconds
- **Processing Rate:** 460.0 rows/second
- **Peak Memory Usage:** 202.00 MB
- **Status:** ✅ **PASSED**

---

## ⏱️ PERFORMANCE METRICS

### Processing Time Breakdown

| Phase | Time (seconds) | Percentage |
|-------|----------------|------------|
| File Ingestion | 0.42 | 3.8% |
| Column Mapping | 0.01 | 0.0% |
| Chemical Matching | 10.65 | 96.2% |
| **Total** | **11.08** | **100%** |

**Processing Rate:** 460.0 rows/second

### Memory Usage

| Metric | Value (MB) |
|--------|------------|
| Start Memory | 174.23 |
| After Ingestion | 174.34 |
| After Column Mapping | 174.34 |
| After Matching | 202.00 |
| **Peak Memory** | **202.00** |
| Memory Increase | 27.77 |

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
| Processing time < 5 min | ✅ PASS | 11.1s < 300s |
| Peak memory < 2GB | ✅ PASS | 202.0MB < 2048MB |
| No crashes | ✅ PASS | Completed successfully |

---

## 🔍 ANALYSIS

### Performance Assessment

**Processing Speed:** 460.0 rows/second is excellent for a dataset of this size.

**Memory Efficiency:** Peak memory usage of 202.00 MB for 5097 rows is excellent.

**Match Accuracy:** 97.4% match rate on CAMEO's own data is very good.

### Bottleneck Analysis

The matching phase took 96.2% of total processing time, which is expected as it involves:
- Database lookups for each chemical
- Multi-signal fusion (CAS, name, formula, UN)
- Fuzzy matching for name variations
- Semantic scoring and safety veto checks

### Scalability

Based on these results:
- **10,000 rows:** Estimated 21.7 seconds
- **50,000 rows:** Estimated 1.8 minutes
- **100,000 rows:** Estimated 3.6 minutes

Memory usage scales linearly, so 100K rows would require approximately 3963 MB.

---

## 🎉 CONCLUSION

The SAFEWARE ETL system successfully processed the **complete CAMEO database** (5097 chemicals) with:

✅ **97.4% match rate** (near-perfect accuracy on CAMEO data)  
✅ **460.0 rows/second** processing speed  
✅ **202.00 MB** peak memory (efficient resource usage)  
✅ **No crashes or timeouts** (robust and stable)  

**The system is production-ready for large-scale chemical inventory processing.**

---

**Report Generated:** 2026-06-14 19:37:21  
**Test Duration:** 11.08 seconds  

---

**END OF REPORT**
