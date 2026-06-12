"""Centralized database connection factory with safety pragmas."""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

def get_safe_connection(db_path: str, readonly: bool = False) -> sqlite3.Connection:
    """
    Create a SQLite connection with all safety pragmas enabled.
    
    MUST be used for every DB access in the application.
    """
    if not db_path:
        raise ValueError("db_path must be a valid path")
    
    # Ensure it's an absolute path
    abs_db_path = os.path.abspath(db_path)
    
    try:
        if readonly:
            # Open SQLite in read-only mode using a URI
            db_uri = f"file:{abs_db_path}?mode=ro"
            conn = sqlite3.connect(db_uri, timeout=10.0, uri=True)
        else:
            conn = sqlite3.connect(abs_db_path, timeout=10.0)
            
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        # Set WAL mode only if we are in write mode (WAL requires write permission to create journal files)
        if not readonly:
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
            except sqlite3.OperationalError as e:
                # Fallback if WAL cannot be set (e.g. read-only filesystem or mounted drive permissions)
                logger.warning(f"Could not enable WAL mode: {e}. Falling back to default journal mode.")
        else:
            conn.execute("PRAGMA synchronous = OFF;")
            
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database at {abs_db_path}: {e}")
        raise
