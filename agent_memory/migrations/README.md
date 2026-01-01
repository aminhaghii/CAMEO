# Database Migration Scripts

This directory contains SQL migration scripts for database schema changes in the CAMEO project.

## Naming Convention
Migration scripts should be named using the format:
```
YYYY-MM-DD_descriptive_name.sql
```

Example: `2026-01-15_add_favorites_priority.sql`

## Migration Script Structure

Each migration script should follow this template:

```sql
-- agent_memory/migrations/YYYY-MM-DD_description.sql
-- Description: Brief description of what this migration does
-- Affected: database_name:table_name
-- Author: Agent/Developer name
-- Safety Level: LOW/MEDIUM/HIGH/CRITICAL

BEGIN TRANSACTION;

-- Add your migration SQL here
-- Example: ALTER TABLE favorites ADD COLUMN priority INTEGER DEFAULT 0;

-- Create indexes if needed
-- Example: CREATE INDEX IF NOT EXISTS idx_favorites_priority ON favorites(priority DESC);

-- Data migration if needed
-- Example: UPDATE favorites SET priority = 0 WHERE priority IS NULL;

COMMIT;

-- Rollback procedure (commented):
-- BEGIN TRANSACTION;
-- ALTER TABLE favorites DROP COLUMN priority;
-- DROP INDEX IF EXISTS idx_favorites_priority;
-- COMMIT;
```

## Safety Levels
- **LOW**: Cosmetic changes, adding optional columns
- **MEDIUM**: Adding required columns with defaults, new indexes
- **HIGH**: Structural changes, table modifications
- **CRITICAL**: Safety-critical schema changes, reactivity rules

## Testing Procedure
1. **Backup**: Always backup affected database before running migration
2. **Test Copy**: Run migration on a copy of production data first
3. **Validation**: Verify data integrity after migration
4. **Rollback Test**: Test the rollback procedure
5. **Documentation**: Update `agent_memory/database_schema.json`

## Required Updates After Migration
1. Update `agent_memory/database_schema.json` with new schema
2. Update `agent_memory/history/YYYY-MM-DD.md` with migration details
3. Test affected API endpoints
4. Update TypeScript interfaces if needed (frontend)
5. Update any hardcoded SQL queries in code

## Example Migrations

### Adding a Column
```sql
-- 2026-01-15_add_favorites_priority.sql
BEGIN TRANSACTION;
ALTER TABLE favorites ADD COLUMN priority INTEGER DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_favorites_priority ON favorites(priority DESC);
COMMIT;
```

### Creating a New Table
```sql
-- 2026-01-20_create_user_preferences.sql
BEGIN TRANSACTION;
CREATE TABLE user_preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    preference_value TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, preference_key)
);
CREATE INDEX idx_user_prefs_user ON user_preferences(user_id);
COMMIT;
```

## Dangerous Operations
⚠️ **NEVER** perform these operations without explicit user approval:
- DROP TABLE
- DELETE FROM (bulk deletes)
- ALTER TABLE DROP COLUMN
- Changes to safety-critical tables (reactivity_rules, chemicals)

## Execution
Migrations should be executed manually using:
```bash
# For user.db
sqlite3 backend/data/user.db < agent_memory/migrations/YYYY-MM-DD_script.sql

# For chemicals.db (READ-ONLY - use extreme caution)
sqlite3 backend/data/chemicals.db < agent_memory/migrations/YYYY-MM-DD_script.sql
```
