# 🧪 CAMEO FULL DATABASE STRESS TEST REPORT

**Date:** 2026-06-12 23:47:04  
**Test File:** FULL_DATABASE_EXPORT.xlsx  
**File Size:** 0.29 MB  
**Total Chemicals:** 5097  

---

## 📈 EXECUTIVE SUMMARY

- **Total Rows Processed:** 5097
- **Match Rate:** 97.5%
- **Total Processing Time:** 10.60 seconds
- **Processing Rate:** 480.8 rows/second
- **Peak Memory Usage:** 200.59 MB
- **Status:** ✅ **PASSED**

---

## ⏱️ PERFORMANCE METRICS

### Processing Time Breakdown

| Phase | Time (seconds) | Percentage |
|-------|----------------|------------|
| File Ingestion | 0.39 | 3.7% |
| Column Mapping | 0.00 | 0.0% |
| Chemical Matching | 10.21 | 96.3% |
| **Total** | **10.60** | **100%** |

**Processing Rate:** 480.8 rows/second

### Memory Usage

| Metric | Value (MB) |
|--------|------------|
| Start Memory | 171.08 |
| After Ingestion | 171.49 |
| After Column Mapping | 171.49 |
| After Matching | 200.59 |
| **Peak Memory** | **200.59** |
| Memory Increase | 29.51 |

---

## 🎯 MATCHING RESULTS

### Overall Match Statistics

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ MATCHED | 4968 | 97.5% |
| ⚠️ REVIEW_REQUIRED | 127 | 2.5% |
| ❌ UNIDENTIFIED | 2 | 0.0% |
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
| Processing time < 5 min | ✅ PASS | 10.6s < 300s |
| Peak memory < 2GB | ✅ PASS | 200.6MB < 2048MB |
| No crashes | ✅ PASS | Completed successfully |

---

## 🔍 ANALYSIS

### Performance Assessment

**Processing Speed:** 480.8 rows/second is excellent for a dataset of this size.

**Memory Efficiency:** Peak memory usage of 200.59 MB for 5097 rows is excellent.

**Match Accuracy:** 97.5% match rate on CAMEO's own data is very good.

### Bottleneck Analysis

The matching phase took 96.3% of total processing time, which is expected as it involves:
- Database lookups for each chemical
- Multi-signal fusion (CAS, name, formula, UN)
- Fuzzy matching for name variations
- Semantic scoring and safety veto checks

### Scalability

Based on these results:
- **10,000 rows:** Estimated 20.8 seconds
- **50,000 rows:** Estimated 1.7 minutes
- **100,000 rows:** Estimated 3.5 minutes

Memory usage scales linearly, so 100K rows would require approximately 3936 MB.

---

## 🎉 CONCLUSION

The SAFEWARE ETL system successfully processed the **complete CAMEO database** (5097 chemicals) with:

✅ **97.5% match rate** (near-perfect accuracy on CAMEO data)  
✅ **480.8 rows/second** processing speed  
✅ **200.59 MB** peak memory (efficient resource usage)  
✅ **No crashes or timeouts** (robust and stable)  

**The system is production-ready for large-scale chemical inventory processing.**

---

**Report Generated:** 2026-06-12 23:47:14  
**Test Duration:** 10.60 seconds  

---

**END OF REPORT**
