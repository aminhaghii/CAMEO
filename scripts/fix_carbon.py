import os
import sqlite3

DB_PATH = os.environ.get(
    "CHEMICALS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "resources", "chemicals.db"),
)

CARBON_ID = 10765  # "CARBON, ACTIVATED"
GROUP_NOT_REACTIVE = 98
GROUP_WATER = 100


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_group_assignment(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO mm_chemical_react (chem_id, react_id) VALUES (?, ?)",
        (CARBON_ID, GROUP_NOT_REACTIVE),
    )
    conn.commit()


def ensure_rule(conn, g1: int, g2: int):
    """Force a green rule with clean note, both orderings normalized."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO reactivity
        (react1, react2, gas_products, pair_compatibility, hazards_documentation)
        VALUES (?, ?, '', 'Compatible', 'No reaction expected.')
        """,
        (min(g1, g2), max(g1, g2)),
    )
    conn.commit()


def verify(conn, carbon_groups):
    cur = conn.cursor()
    cur.execute(
        "SELECT react_id FROM mm_chemical_react WHERE chem_id = ? ORDER BY react_id",
        (CARBON_ID,),
    )
    groups = [r["react_id"] for r in cur.fetchall()]

    rules = []
    for g in carbon_groups:
        cur.execute(
            """
            SELECT pair_compatibility, hazards_documentation
            FROM reactivity
            WHERE (react1 = ? AND react2 = ?) OR (react1 = ? AND react2 = ?)
            """,
            (g, GROUP_WATER, GROUP_WATER, g),
        )
        rules.append((g, cur.fetchone()))
    return groups, rules


def main():
    print(f"Using DB: {DB_PATH}")
    conn = connect()
    ensure_group_assignment(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT react_id FROM mm_chemical_react WHERE chem_id = ? ORDER BY react_id",
        (CARBON_ID,),
    )
    carbon_groups = [r["react_id"] for r in cur.fetchall()]

    # Force green rules for every Carbon group vs Water
    for g in carbon_groups:
        ensure_rule(conn, g, GROUP_WATER)

    groups, rules = verify(conn, carbon_groups)
    print(f"Groups for CARBON, ACTIVATED: {groups}")
    for g, rule in rules:
        print(f"Rule {g} + {GROUP_WATER}:", dict(rule) if rule else None)
    conn.close()


if __name__ == "__main__":
    main()
