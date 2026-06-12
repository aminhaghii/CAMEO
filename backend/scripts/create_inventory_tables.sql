-- Phase 2 inventory persistence tables
-- User-managed inventory rows finalized after interactive review
CREATE TABLE IF NOT EXISTS user_inventories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    chemical_id INTEGER NOT NULL,
    quantity TEXT,
    unit TEXT,
    storage_location TEXT,
    notes TEXT,
    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_inventories_batch ON user_inventories(batch_id);
CREATE INDEX IF NOT EXISTS idx_user_inventories_chemical ON user_inventories(chemical_id);

-- Stores analysis output payloads for each batch
CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    analysis_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    total_chemicals INTEGER,
    dangerous_pairs INTEGER,
    storage_warnings INTEGER,
    risk_matrix_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_results_batch ON analysis_results(batch_id);

-- Warehouses
CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Warehouse Sections
CREATE TABLE IF NOT EXISTS warehouse_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    position_index INTEGER NOT NULL,
    color TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Chemical Placements in Sections
CREATE TABLE IF NOT EXISTS chemical_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES warehouse_sections(id) ON DELETE CASCADE,
    chemical_id INTEGER NOT NULL,
    chemical_name TEXT NOT NULL,
    cas_number TEXT,
    quantity_kg REAL,
    reactive_groups TEXT, -- JSON array of group IDs (e.g. [24, 28])
    status TEXT DEFAULT 'placed', -- 'placed' or 'ghost' (for suggestions)
    placed_by TEXT, -- 'human' or 'auto_arrange'
    placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Placement Compatibility Violations
CREATE TABLE IF NOT EXISTS placement_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES warehouse_sections(id) ON DELETE CASCADE,
    chemical_a_id INTEGER NOT NULL,
    chemical_b_id INTEGER NOT NULL,
    violation_type TEXT NOT NULL, -- 'INCOMPATIBLE', 'CAUTION', 'NO_DATA'
    severity TEXT, -- 'high', 'medium', 'low'
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

