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
    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chemical_id) REFERENCES chemicals(id)
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
