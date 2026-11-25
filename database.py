import sqlite3

DB_NAME = "kql_assistant.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS investigations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type  TEXT,
            prompt      TEXT,
            response    TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_library (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            query      TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mask_sessions (
            conversation_id TEXT PRIMARY KEY,
            map_json        TEXT NOT NULL,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    migrate_db()


def migrate_db():
    """Add any missing columns/tables to an existing database without data loss."""
    conn = sqlite3.connect(DB_NAME)

    existing_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    # --- query_library (legacy) ---
    if "query_library" not in existing_tables:
        conn.execute("""
            CREATE TABLE query_library (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL,
                query      TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("[DB] Created missing table: query_library")

    # --- conversation_messages ---
    if "conversation_messages" not in existing_tables:
        conn.execute("""
            CREATE TABLE conversation_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("[DB] Created missing table: conversation_messages")

    # --- mask_sessions ---
    if "mask_sessions" not in existing_tables:
        conn.execute("""
            CREATE TABLE mask_sessions (
                conversation_id TEXT PRIMARY KEY,
                map_json        TEXT NOT NULL,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("[DB] Created missing table: mask_sessions")

    # --- investigations: add conversation_id column ---
    inv_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(investigations)"
    ).fetchall()}

    if "conversation_id" not in inv_cols:
        conn.execute("ALTER TABLE investigations ADD COLUMN conversation_id TEXT")
        print("[DB] Added column: investigations.conversation_id")

    if "title" not in inv_cols:
        conn.execute("ALTER TABLE investigations ADD COLUMN title TEXT")
        print("[DB] Added column: investigations.title")

    conn.commit()
    conn.close()
