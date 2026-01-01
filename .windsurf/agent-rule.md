🧠 WINDSURF AGENT MEMORY PROTOCOL v3.0
برای پروژه: CAMEO Chemicals - Flask Backend
text
═══════════════════════════════════════════════════════════════════════
   ██████╗ █████╗ ███╗   ███╗███████╗ ██████╗     ███╗   ███╗███████╗███╗   ███╗
  ██╔════╝██╔══██╗████╗ ████║██╔════╝██╔═══██╗    ████╗ ████║██╔════╝████╗ ████║
  ██║     ███████║██╔████╔██║█████╗  ██║   ██║    ██╔████╔██║█████╗  ██╔████╔██║
  ██║     ██╔══██║██║╚██╔╝██║██╔══╝  ██║   ██║    ██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║
  ╚██████╗██║  ██║██║ ╚═╝ ██║███████╗╚██████╔╝    ██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║
   ╚═════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝ ╚═════╝     ╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝
                        AGENT MEMORY PROTOCOL v3.0
═══════════════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────────────┐
│  ⚠️  CRITICAL RULE: NO STRUCTURAL CHANGE WITHOUT MEMORY CONSULTATION  │
│     هیچ تغییر معماری بدون مشاوره با حافظه Agent - بدون استثنا        │
└──────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════
🔴 PHASE 1: PRE-FLIGHT CHECK (قبل از هر ویرایش)
═══════════════════════════════════════════════════════════════════════

### 1.1 📖 MANDATORY MEMORY LOAD

BEFORE touching ANY code, you MUST:

1. Read Core Memory Files:
   ```bash
   agent_memory/
   ├── README.md                      # Project context & philosophy
   ├── project_structure.json         # File tree + module registry
   ├── api_registry.json              # Flask endpoint catalog
   ├── database_schema.json           # SQLite schema versioning
   ├── dependency_graph.json          # Cross-layer dependencies
   └── technology_stack.json          # Tech versions & configs
Integrity Validation:

✅ All files exist and readable?

✅ JSON files parseable? (Try json.loads())

✅ Timestamps < 7 days old?

❌ ANY issue → HALT + notify user immediately

Historical Context:

bash
agent_memory/history/
├── YYYY-MM-DD.md                 # Daily change logs
└── archive/YYYY-MM/              # Monthly archives
Load last 3 days of history

Check for TODOs, warnings, incomplete tasks

Identify related changes

1.2 🎯 CHANGE CLASSIFICATION
Classify the requested change as STRUCTURAL or COSMETIC:

┌─────────────────────────────────────────────────────────────────┐
│ 🚨 STRUCTURAL CHANGES (Memory Protocol MANDATORY) │
├─────────────────────────────────────────────────────────────────┤
│ ✅ Flask Backend (backend/) │
│ • New/modified routes in app.py │
│ • Changes to request/response formats │
│ • New/modified logic modules (reactivity_engine.py, etc) │
│ • Template changes (templates/mixer.html) │
│ │
│ ✅ Database Layer (backend/data/) │
│ • Schema modifications (ALTER TABLE, CREATE INDEX) │
│ • New tables or columns │
│ • Query optimization affecting response structure │
│ │
│ ✅ React Frontend (src/) │
│ • Component hierarchy changes │
│ • New service modules │
│ • State management structure │
│ • API client modifications │
│ │
│ ✅ Cross-Layer Changes │
│ • Adding/removing dependencies (requirements.txt, package.json)│
│ • Build config (vite.config.ts) │
│ • Environment variables │
│ • CORS policies │
│ │
│ ✅ Critical Business Logic │
│ • Reactivity rules engine changes │
│ • Compatibility calculations │
│ • Safety threshold adjustments │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ ⚪️ COSMETIC CHANGES (Memory Update Optional) │
├─────────────────────────────────────────────────────────────────┤
│ • CSS/Tailwind styling │
│ • UI text/labels (non-structural) │
│ • Code comments/docstrings │
│ • Logging statements │
│ • Variable renaming (internal scope) │
│ • Formatting (black, prettier) │
└─────────────────────────────────────────────────────────────────┘

Decision Tree:

Amin Dg, [1/1/2026 6:31 PM]
text
Is it STRUCTURAL?
├─ YES → Continue to Phase 2
├─ MAYBE → Treat as STRUCTURAL (safer)
└─ NO → Quick edit + optional note
═══════════════════════════════════════════════════════════════════════
🟡 PHASE 2: IMPACT ANALYSIS (تحلیل تأثیرات)
═══════════════════════════════════════════════════════════════════════

2.1 🔗 DEPENDENCY MAPPING
Load agent_memory/dependency_graph.json and check:

json
{
  "backend/app.py": {
    "provides": [
      "POST /api/analyze",
      "GET /api/search",
      "GET /api/chemical/<id>"
    ],
    "depends_on": [
      "backend/logic/reactivity_engine.py",
      "backend/data/chemicals.db"
    ],
    "consumed_by": [
      "src/services/ChemicalService.ts",
      "src/services/ReactivityService.ts"
    ]
  },
  "backend/logic/reactivity_engine.py": {
    "provides": ["ReactivityEngine.analyze()"],
    "depends_on": [
      "backend/logic/constants.py",
      "backend/data/chemicals.db"
    ],
    "consumed_by": ["backend/app.py"]
  }
}
Questions to Answer:

Which modules import this file?

Which API endpoints use this logic?

Which frontend components call this API?

Does this affect database queries?

2.2 ⚠️ RISK ASSESSMENT
Assign risk level based on impact scope:

🔴 HIGH RISK (Requires User Approval)
Database schema changes (chemicals.db, user.db)

Breaking changes to API contracts (/api/analyze, /api/search)

ReactivityEngine core logic modifications

Dependency version upgrades (Flask, React, SQLite driver)

CORS policy changes

Action: Show user a detailed impact report before proceeding.

🟠 MEDIUM RISK (Log Warning)
Adding new API endpoints

Changing response format (backward compatible)

Refactoring services with multiple consumers

Adding optional parameters

Action: Proceed with caution, detailed logging required.

🟢 LOW RISK (Standard Memory Update)
Internal function refactoring

Adding utility functions

Creating new isolated components

Performance optimizations (no API changes)

Action: Standard workflow.

2.3 🧪 TEST COVERAGE CHECK
Before modifying critical paths, check:

bash
tests/
├── backend/
│   ├── test_reactivity_engine.py    # Does this exist?
│   └── test_api.py                  # Does it test the endpoint?
└── frontend/
    └── ...
IF test coverage missing for HIGH/MEDIUM risk change:

⚠️ Flag as "UNTESTED CHANGE"

Suggest test creation

Log in memory as technical debt

═══════════════════════════════════════════════════════════════════════
🟢 PHASE 3: EXECUTE CHANGE (اجرای تغییر)
═══════════════════════════════════════════════════════════════════════

3.1 Transaction-Like Editing
python
# Conceptual workflow (not literal code)
try:
    validate_memory_state()
    assess_risk_level()
    
    if risk == HIGH:
        await get_user_approval()
    
    create_snapshot()  # For rollback
    apply_changes()
    verify_syntax()    # Python: compile, TS: tsc check
    
    update_memory()
    commit()
except Exception as e:
    rollback_to_snapshot()
    log_failure()
    notify_user(e)
3.2 Atomic Changes
For multi-file changes, ensure:

All related files updated together

No partial states committed

Cross-layer consistency maintained

═══════════════════════════════════════════════════════════════════════
🔵 PHASE 4: MEMORY UPDATE (بروزرسانی حافظه)
═══════════════════════════════════════════════════════════════════════

4.1 📝 HISTORY LOG (MANDATORY)
ALWAYS append to agent_memory/history/YYYY-MM-DD.md:

text
## 🕐 [17:30] Change #5: Added Chemical Filtering to Search API

### 📋 Summary
Extended /api/search endpoint to support filtering by reactive group

### 🎯 Intent/Reason
User requested ability to filter search results by chemical reactivity groups
to improve search precision for safety officers

### 📁 Files Modified
- backend/app.py (lines 45-67)
  - Added reactive_group_id query parameter
  - Modified SQL query to JOIN with chemical_reactive_groups table
  
- agent_memory/api_registry.json
  - Updated /api/search parameter documentation

Amin Dg, [1/1/2026 6:31 PM]
### 🔗 Dependencies Affected
- Backend: backend/logic/reactivity_engine.py (indirect - uses same tables)
- Frontend: src/services/ChemicalService.ts (needs update for new param)
- Database: chemicals.db (no schema change, new query pattern)

### ⚙️ Technical Details
```python
# New query parameter
@app.route('/api/search', methods=['GET'])
def search():
    query = request.args.get('q', '')
    reactive_group_id = request.args.get('reactive_group_id', type=int)
    
    if reactive_group_id:
        sql = """
            SELECT DISTINCT c.id, c.name, c.synonyms 
            FROM chemicals c
            JOIN chemical_reactive_groups crg ON c.id = crg.chemical_id
            WHERE (c.name LIKE ? OR c.synonyms LIKE ?)
            AND crg.reactive_group_id = ?
        """
⚠️ Breaking Changes
 No

 Yes

Backward Compatible: Old API calls still work, new param optional

🧪 Testing Status
 Unit tests added (TODO: Create test_search_filtering.py)

 Manual testing completed (tested with Postman)

 Integration tests updated

📊 Performance Impact
Query time: +5ms avg (acceptable)

Database load: No significant change

🔖 Tags
#backend #api #search #filtering #reactive-groups

📌 Related Issues/PRs
Resolves: User request from 2026-01-01 conversation

Related: Future work on advanced search filters

✅ Follow-up Actions
Update frontend ChemicalService.ts to support new param

Add unit tests for filtering logic

Update API documentation in README

text

### 4.2 🗂 UPDATE CORE MEMORY FILES

#### A) agent_memory/project_structure.json

Update when:
- New files/directories created
- Files moved/renamed
- Module responsibilities changed

```json
{
  "last_updated": "2026-01-01T17:30:00+03:30",
  "agent_protocol_version": "3.0",
  "project": {
    "name": "CAMEO Chemicals",
    "type": "Flask Backend + React Frontend",
    "primary_language": ["Python", "TypeScript"]
  },
  "structure": {
    "backend": {
      "app.py": {
        "role": "Flask main application",
        "routes": [
          {"path": "/api/search", "method": "GET", "handler": "search()"},
          {"path": "/api/chemical/<id>", "method": "GET", "handler": "get_chemical()"},
          {"path": "/api/analyze", "method": "POST", "handler": "analyze_chemicals()"},
          {"path": "/api/favorites", "method": "GET", "handler": "get_favorites()"}
        ],
        "dependencies": [
          "logic/reactivity_engine.py",
          "data/chemicals.db",
          "data/user.db"
        ],
        "last_modified": "2026-01-01",
        "change_frequency": "high"
      },
      "logic/reactivity_engine.py": {
        "role": "Core chemical reactivity analysis engine",
        "public_classes": ["ReactivityEngine"],
        "key_methods": [
          "analyze(chemical_ids, include_water_check)",
          "get_statistics()"
        ],
        "safety_critical": true,
        "dependencies": ["logic/constants.py", "sqlite3"],
        "consumers": ["app.py"],
        "last_modified": "2025-12-28"
      },
      "logic/constants.py": {
        "role": "Compatibility enums and mappings",
        "exports": ["Compatibility", "COMPATIBILITY_MAP"],
        "last_modified": "2025-12-20"
      },
      "data/": {
        "chemicals.db": {
          "type": "SQLite database",
          "mode": "READ_ONLY",
          "tables": ["chemicals", "reacts", "chemical_reactive_groups", "reactivity_rules"],
          "access_pattern": "Read-heavy, no writes"
        },
        "user.db": {
          "type": "SQLite database",
          "mode": "READ_WRITE",
          "tables": ["favorites"],
          "access_pattern": "Low-frequency writes, favorites management"
        }
      }
    },
    "src": {
      "services/": {
        "ChemicalService.ts": {
          "role": "Frontend API client for chemical data",
          "api_endpoints_used": ["/api/search", "/api/chemical/<id>"],
          "consumers": ["components/SearchResults.tsx", "components/ChemicalDetail.tsx"]
        },
        "ReactivityService.ts": {
          "role": "Frontend client for reactivity analysis",

Amin Dg, [1/1/2026 6:31 PM]
"api_endpoints_used": ["/api/analyze"],
          "consumers": ["components/Mixer.tsx"]
        }
      }
    }
  }
}
B) agent_memory/api_registry.json
Critical for Frontend-Backend sync:

json
{
  "last_updated": "2026-01-01T17:30:00+03:30",
  "base_url": "http://localhost:5000",
  "endpoints": [
    {
      "id": "search_chemicals",
      "method": "GET",
      "path": "/api/search",
      "description": "Search chemicals by name or synonym",
      "parameters": {
        "q": {
          "type": "string",
          "required": true,
          "description": "Search query"
        },
        "reactive_group_id": {
          "type": "integer",
          "required": false,
          "description": "Filter by reactive group ID",
          "added_in": "2026-01-01"
        }
      },
      "response": {
        "200": {
          "items": "Chemical[]",
          "total": "number"
        },
        "500": {
          "error": "string"
        }
      },
      "implementation": "backend/app.py:search()",
      "frontend_consumers": ["src/services/ChemicalService.ts"],
      "last_modified": "2026-01-01",
      "breaking_change_history": []
    },
    {
      "id": "analyze_chemicals",
      "method": "POST",
      "path": "/api/analyze",
      "description": "⚠️ SAFETY-CRITICAL: Analyze chemical compatibility",
      "safety_critical": true,
      "request_body": {
        "chemical_ids": {
          "type": "number[]",
          "required": true,
          "min_length": 2,
          "max_length": 20
        },
        "options": {
          "type": "object",
          "required": false,
          "properties": {
            "include_water_check": "boolean"
          }
        }
      },
      "response": {
        "200": {
          "success": "boolean",
          "data": {
            "meta": "object",
            "overall": "CompatibilityResult",
            "matrix": "Cell[][]",
            "critical_pairs": "CriticalPair[]",
            "warnings": "string[]"
          }
        },
        "400": {
          "success": false,
          "error": {"code": "string", "message": "string"}
        }
      },
      "implementation": "backend/app.py:analyze_chemicals()",
      "business_logic": "backend/logic/reactivity_engine.py:ReactivityEngine.analyze()",
      "frontend_consumers": ["src/services/ReactivityService.ts"],
      "last_modified": "2025-12-28"
    }
  ]
}
C) agent_memory/database_schema.json
Track schema evolution:

Amin Dg, [1/1/2026 6:31 PM]
json
{
  "last_updated": "2026-01-01T17:30:00+03:30",
  "databases": {
    "chemicals.db": {
      "path": "backend/data/chemicals.db",
      "mode": "READ_ONLY",
      "version": "1.0",
      "tables": {
        "chemicals": {
          "schema_version": "1.0",
          "columns": [
            {"name": "id", "type": "INTEGER", "pk": true},
            {"name": "name", "type": "TEXT", "indexed": true},
            {"name": "synonyms", "type": "TEXT", "indexed": true}
          ],
          "indexes": [
            {"name": "idx_name", "columns": ["name"]},
            {"name": "idx_synonyms", "columns": ["synonyms"]}
          ],
          "row_count_approx": 5000,
          "consumers": [
            "backend/app.py:search()",
            "backend/app.py:get_chemical()"
          ]
        },
        "reacts": {
          "schema_version": "1.0",
          "description": "Reactive groups master table",
          "columns": [
            {"name": "id", "type": "INTEGER", "pk": true},
            {"name": "name", "type": "TEXT"},
            {"name": "description", "type": "TEXT"}
          ],
          "row_count_approx": 68,
          "consumers": ["backend/logic/reactivity_engine.py"]
        },
        "chemical_reactive_groups": {
          "description": "Many-to-many junction table",
          "columns": [
            {"name": "chemical_id", "type": "INTEGER", "fk": "chemicals.id"},
            {"name": "reactive_group_id", "type": "INTEGER", "fk": "reacts.id"}
          ],
          "indexes": [
            {"name": "idx_chem_group", "columns": ["chemical_id", "reactive_group_id"]}
          ]
        },
        "reactivity_rules": {
          "description": "Compatibility rules between reactive groups",
          "columns": [
            {"name": "group1_id", "type": "INTEGER", "fk": "reacts.id"},
            {"name": "group2_id", "type": "INTEGER", "fk": "reacts.id"},
            {"name": "compatibility", "type": "TEXT"},
            {"name": "hazards", "type": "TEXT"},
            {"name": "gas_products", "type": "TEXT"}
          ],
          "consumers": ["backend/logic/reactivity_engine.py:analyze()"]
        }
      }
    },
    "user.db": {
      "path": "backend/data/user.db",
      "mode": "READ_WRITE",
      "version": "1.0",
      "tables": {
        "favorites": {
          "schema_version": "1.0",
          "columns": [
            {"name": "id", "type": "INTEGER", "pk": true, "autoincrement": true},
            {"name": "chemical_id", "type": "INTEGER"},
            {"name": "added_at", "type": "DATETIME", "default": "CURRENT_TIMESTAMP"},
            {"name": "note", "type": "TEXT", "nullable": true}
          ],
          "indexes": [
            {"name": "idx_chem_fav", "columns": ["chemical_id"]}
          ],
          "consumers": [
            "backend/app.py:get_favorites()",
            "backend/app.py:add_favorite()",
            "backend/app.py:remove_favorite()"
          ]
        }
      },
      "migration_history": [
        {
          "version": "1.0",
          "date": "2025-12-15",
          "description": "Initial schema: favorites table",
          "script": "backend/scripts/init_user_db.sql"
        }
      ]
    }
  }
}
D) agent_memory/dependency_graph.json
Bidirectional dependency tracking:

Amin Dg, [1/1/2026 6:31 PM]
json
{
  "last_updated": "2026-01-01T17:30:00+03:30",
  "graph": {
    "backend/app.py": {
      "imports": [
        "flask",
        "flask_cors",
        "sqlite3",
        "backend.logic.reactivity_engine",
        "backend.logic.constants"
      ],
      "file_dependencies": [
        "backend/logic/reactivity_engine.py",
        "backend/logic/constants.py"
      ],
      "data_dependencies": [
        "backend/data/chemicals.db",
        "backend/data/user.db"
      ],
      "api_consumers": [
        "src/services/ChemicalService.ts",
        "src/services/ReactivityService.ts",
        "src/services/FavoritesService.ts"
      ]
    },
    "backend/logic/reactivity_engine.py": {
      "imports": [
        "sqlite3",
        "typing",
        "dataclasses",
        "backend.logic.constants"
      ],
      "file_dependencies": [
        "backend/logic/constants.py"
      ],
      "data_dependencies": [
        "backend/data/chemicals.db"
      ],
      "used_by": [
        "backend/app.py"
      ]
    },
    "src/services/ChemicalService.ts": {
      "api_calls": [
        "GET /api/search",
        "GET /api/chemical/<id>"
      ],
      "used_by_components": [
        "src/components/SearchResults.tsx",
        "src/components/ChemicalDetail.tsx"
      ]
    }
  }
}
E) agent_memory/technology_stack.json
Track tech versions:

json
{
  "last_updated": "2026-01-01T17:30:00+03:30",
  "backend": {
    "runtime": {
      "python": {
        "version": "3.9+",
        "recommended": "3.11"
      }
    },
    "framework": {
      "flask": {
        "version": "^2.3.0",
        "purpose": "REST API server"
      },
      "flask-cors": {
        "version": "^4.0.0",
        "purpose": "CORS handling"
      }
    },
    "database": {
      "sqlite3": {
        "version": "3.x",
        "driver": "built-in",
        "databases": [
          "chemicals.db (read-only)",
          "user.db (read-write)"
        ]
      }
    }
  },
  "frontend": {
    "runtime": {
      "node": ">=18.0.0"
    },
    "framework": {
      "react": "^18.2.0",
      "typescript": "^5.0.0"
    },
    "build_tool": {
      "vite": "^5.0.0"
    },
    "styling": {
      "tailwindcss": "^3.0.0"
    }
  }
}
4.3 ✅ VALIDATION CHECKLIST
After memory update, verify:

bash
✓ All JSON files valid (no syntax errors)
✓ File paths in memory match actual structure
✓ API endpoint count matches implementation
✓ Database table names match schema
✓ Dependency graph is bidirectional
✓ History file has new entry with all required fields
✓ Timestamps updated
✓ Tags added for searchability
═══════════════════════════════════════════════════════════════════════
🚨 PHASE 5: CROSS-LAYER SYNCHRONIZATION (همگام‌سازی لایه‌ها)
═══════════════════════════════════════════════════════════════════════

5.1 🔄 Frontend ↔️ Backend Sync Protocol
CRITICAL: When backend API changes, frontend MUST be updated.

Scenario 1: API Parameter Added/Changed
Backend changed: /api/search now has reactive_group_id param

Required Actions:

Update agent_memory/api_registry.json

Flag affected frontend files:

text
⚠️ SYNC REQUIRED:
- src/services/ChemicalService.ts
- Update searchChemicals() method signature
Create TODO in history:

text
### 📌 Follow-up Actions
- [ ] Update ChemicalService.ts with new param
- [ ] Update SearchResults.tsx to pass filter
- [ ] Add UI control for reactive group filtering
Scenario 2: Response Format Changed
Backend changed: /api/analyze now includes warnings array

Required Actions:

Update TypeScript interfaces:

typescript
// src/types/Reactivity.ts
interface AnalysisResult {
  // ... existing fields
  warnings: string[];  // NEW
}
Update all consumers to handle new field

Test backward compatibility

5.2 🗄 Database ↔️ Code Sync
When adding a new column to favorites table:

Amin Dg, [1/1/2026 6:31 PM]
text
1. Update database_schema.json
2. Write migration script: agent_memory/migrations/YYYY-MM-DD_add_favorites_priority.sql
3. Update backend/app.py queries
4. Update frontend types
5. Test rollback procedure
═══════════════════════════════════════════════════════════════════════
🔴 PHASE 6: ERROR HANDLING & RECOVERY (مدیریت خطا و بازیابی)
═══════════════════════════════════════════════════════════════════════

6.1 ⛔️ CORRUPTION DETECTION
IF memory files become corrupted:

text
🚨 MEMORY CORRUPTION DETECTED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Affected Files:
├── agent_memory/project_structure.json
│   ❌ JSON parse error at line 42, column 15
│   ❌ Expected ',' but found '}'
│
└── agent_memory/api_registry.json
    ✅ OK

Last Known Good State:
├── Timestamp: 2026-01-01 14:00 (3.5 hours ago)
└── Git commit: a3f9d21

Diagnostic Report:
├── Likely cause: Incomplete write during system crash
├── Affected operations: Last 2 changes may be lost
└── Data integrity: Core project files intact

Recommended Actions:
1. 🔄 Restore from git: git restore agent_memory/
2. 📖 Review git log: git log --oneline -5 agent_memory/
3. 🛠 Manual repair: Edit line 42 in project_structure.json
4. ♻️ Rebuild: Run memory regeneration script

User Action Required: Choose recovery method
6.2 🔄 AUTOMATIC RECOVERY PROCEDURE
python
# Conceptual recovery workflow
def recover_memory():
    if git_available():
        restore_from_git()
    elif backup_exists():
        restore_from_backup()
    else:
        initiate_rebuild()
        
def initiate_rebuild():
    """Rebuild memory from project scan"""
    print("🔧 Rebuilding agent memory from source...")
    
    # Scan project structure
    structure = scan_directory_tree('.')
    
    # Parse Flask routes
    api_endpoints = extract_flask_routes('backend/app.py')
    
    # Analyze imports
    dependencies = build_dependency_graph()
    
    # Inspect database
    db_schema = inspect_sqlite_schema('backend/data/chemicals.db')
    
    # Generate fresh JSON files
    write_memory_files(structure, api_endpoints, dependencies, db_schema)
    
    print("✅ Memory rebuilt successfully")
6.3 🔐 BACKUP STRATEGY
Automatic Backups:

Before any HIGH-risk change

Daily snapshots (kept for 7 days)

Monthly archives

bash
agent_memory/backups/
├── 2026-01-01_17-30_pre-search-filter-change/
│   ├── project_structure.json
│   ├── api_registry.json
│   └── dependency_graph.json
└── monthly/
    └── 2025-12/
═══════════════════════════════════════════════════════════════════════
📊 PHASE 7: MAINTENANCE & HEALTH MONITORING (نگهداری و پایش)
═══════════════════════════════════════════════════════════════════════

7.1 🧹 WEEKLY MAINTENANCE
Every 7 days, agent should suggest:

text
🧹 WEEKLY MEMORY MAINTENANCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Actions to perform:
1. ✅ Archive history older than 30 days
   → Move to agent_memory/history/archive/2025-12/
   
2. ✅ Rebuild dependency graph for accuracy
   → Scan actual imports vs recorded dependencies
   
3. ✅ Verify file paths in project_structure.json
   → Remove entries for deleted files
   
4. ✅ Validate API registry against backend/app.py
   → Check for undocumented endpoints
   
5. ✅ Check for orphaned TODOs
   → Review uncompleted follow-up actions

Run maintenance? [Y/n]
7.2 📈 MONTHLY HEALTH REPORT
text
# 🧠 Agent Memory Health Report - January 2026

## 📊 Statistics
- Total changes logged: 147
- Structural changes: 45 (31%)
- Cosmetic changes: 102 (69%)
- Files tracked: 89
- Dependencies mapped: 234
- API endpoints: 10

## 🔥 Hot Spots (Most Modified)
1. backend/app.py - 23 changes
2. backend/logic/reactivity_engine.py - 18 changes
3. src/services/ChemicalService.ts - 15 changes
4. agent_memory/api_registry.json - 12 changes

## ⚠️ Warnings
- ⚠️ dependency_graph.json last rebuilt: 15 days ago (suggest refresh)
- ⚠️ 5 TODOs pending for >7 days
- ✅ All JSON files healthy
- ✅ No orphaned references

Amin Dg, [1/1/2026 6:31 PM]
## 📝 Recommendations
1. Consider refactoring backend/app.py (growing too large)
2. Add integration tests for /api/analyze (safety-critical)
3. Update frontend TypeScript interfaces for new API params

## 🎯 Memory Efficiency
- History size: 2.3 MB (within limits)
- Oldest entry: 2025-12-01
- Archive needed: No

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generated: 2026-02-01 00:00
Next report: 2026-03-01
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
═══════════════════════════════════════════════════════════════════════
🎯 QUICK DECISION FLOWCHART
═══════════════════════════════════════════════════════════════════════

text
User requests a change
         │
         ▼
    Is it STRUCTURAL?
    ┌────────┴────────┐
    │                 │
   YES               NO
    │                 │
    ▼                 ▼
Load Memory      Quick Edit
    │                 │
    ▼                 ▼
Files OK?        Optional:
┌───┴───┐        Log in
│       │        history
YES    NO        │
│       │        ▼
│       ▼       DONE
│   🚨 HALT
│   + Notify
│
▼
Assess Risk Level
┌──────┼──────┐
│      │      │
HIGH  MED   LOW
│      │      │
▼      ▼      ▼
Ask   Warn   OK
User
│      │      │
└──────┴──────┘
        │
        ▼
Execute Change
        │
        ▼
Update Memory
┌──────┼──────┐
│      │      │
JSON  History API
files   log    reg
│      │      │
└──────┴──────┘
        │
        ▼
   Validate
        │
        ▼
     DONE ✅
═══════════════════════════════════════════════════════════════════════
🔧 SPECIAL RULES: CAMEO-SPECIFIC GUIDELINES
═══════════════════════════════════════════════════════════════════════

Rule 1: Safety-Critical Code
Any change to reactivity analysis logic REQUIRES:

✅ User approval

✅ Detailed testing documentation

✅ Backup of previous version

✅ Clear rollback procedure

✅ Validation against known test cases

Files:

backend/logic/reactivity_engine.py

backend/logic/constants.py

backend/data/reactivity_rules (if schema changes)

Rule 2: Database Migrations
Never directly modify production databases:

✅ Write migration script in agent_memory/migrations/

✅ Test on copy first

✅ Document rollback procedure

✅ Update database_schema.json

Rule 3: API Contract Changes
Breaking changes to public APIs require:

✅ Version bump

✅ Deprecation notice (if possible)

✅ Frontend update plan

✅ Update all consumers before deploying

Rule 4: Frontend-Backend Type Sync
When backend response changes:

typescript
// ALWAYS update TypeScript interfaces
// src/types/Chemical.ts
interface Chemical {
  id: number;
  name: string;
  synonyms: string;
  // NEW FIELD - must match backend response
  reactive_groups?: ReactiveGroup[];
}
Rule 5: ETL Pipeline Changes
Changes to data ingestion (backend/scripts/) require:

✅ Update data source documentation

✅ Validate output database integrity

✅ Log in agent_memory/data_sources.json

✅ Test with sample data first

═══════════════════════════════════════════════════════════════════════
📚 APPENDIX: FILE TEMPLATES
═══════════════════════════════════════════════════════════════════════

A) History Entry Template
text
## 🕐 [HH:MM] Change #N: <Brief Title>

### 📋 Summary
<One sentence description>

### 🎯 Intent/Reason
<Why this change?>

### 📁 Files Modified
- path/to/file (lines X-Y)
  - What was changed

### 🔗 Dependencies Affected
- Layer: Module (relationship)

### ⚙️ Technical Details
```code
# Key code snippets
⚠️ Breaking Changes
 Yes / [x] No

🧪 Testing Status
 Unit tests

 Integration tests

 Manual testing

🔖 Tags
#tag1 #tag2

📌 Related Issues
Resolves: #X

✅ Follow-up Actions
 TODO 1

 TODO 2

text

### B) Migration Script Template

```sql
-- agent_memory/migrations/2026-01-01_add_favorites_priority.sql
-- Description: Add priority field to favorites for custom ordering
-- Affected: user.db:favorites table

BEGIN TRANSACTION;

-- Add new column
ALTER TABLE favorites ADD COLUMN priority INTEGER DEFAULT 0;

-- Create index for sorting
CREATE INDEX IF NOT EXISTS idx_favorites_priority 
ON favorites(priority DESC);

Amin Dg, [1/1/2026 6:31 PM]
-- Migrate existing data (all get priority 0)
-- No action needed, DEFAULT handles it

COMMIT;

-- Rollback procedure:
-- ALTER TABLE favorites DROP COLUMN priority;
-- DROP INDEX idx_favorites_priority;
═══════════════════════════════════════════════════════════════════════
🏁 END OF PROTOCOL v3.0
═══════════════════════════════════════════════════════════════════════

Last Updated: 2026-01-01
Maintained by: Windsurf IDE AI Agent
Project: CAMEO Chemicals - Chemical Safety Analysis Platform
Stack: Flask (Python) + React (TypeScript) + SQLite

═══════════════════════════════════════════════════════════════════════
این پروتکل باید در فایل زیر قرار گیرد:
→ .windsurf/rules.md
→ agent_memory/AGENT_PROTOCOL.md
═══════════════════════════════════════════════════════════════════════

text

***

## 📂 ساختار کامل Memory Directory (پیشنهادی)

```bash
agent_memory/
├── README.md                          # نمای کلی پروژه
├── AGENT_PROTOCOL.md                  # این پروتکل (v3.0)
├── Agent-Rule.md                      # قوانین فعلی شما (deprecated by v3.0)
│
├── project_structure.json             # ساختار فایل‌ها + نقش ماژول‌ها
├── api_registry.json                  # ثبت endpoint های Flask
├── database_schema.json               # Schema + version tracking
├── dependency_graph.json              # وابستگی‌های bidirectional
├── technology_stack.json              # نسخه‌های tech stack
├── data_sources.json                  # منابع داده ETL (اگر دارید)
│
├── history/                           # تاریخچه تغییرات روزانه
│   ├── 2026-01-01.md
│   ├── 2026-01-02.md
│   └── archive/                       # آرشیو ماهانه
│       ├── 2025-12/
│       └── 2025-11/
│
├── migrations/                        # اسکریپت‌های database migration
│   ├── 2026-01-01_add_favorites_priority.sql
│   └── README.md                      # راهنمای اجرای migration
│
├── backups/                           # پشتیبان‌های خودکار قبل از تغییرات HIGH-risk
│   ├── 2026-01-01_17-30_pre-search-change/
│   │   ├── project_structure.json
│   │   └── api_registry.json
│   └── monthly/
│       └── 2025-12/
│
├── snapshots/                         # Snapshot های milestone ها
│   ├── v1.0-release/
│   └── v1.1-beta/
│
└── reports/                           # گزارش‌های ماهانه health check
    ├── 2026-01-health-report.md
    └── 2025-12-health-report.md---
trigger: manual
---