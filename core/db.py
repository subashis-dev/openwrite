import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "openwrite.db")


def get_db_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db_connection()
    cur = conn.cursor()

    # Transcripts Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            raw_text TEXT NOT NULL,
            processed_text TEXT NOT NULL,
            mode TEXT NOT NULL,
            app_name TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0,
            word_count INTEGER DEFAULT 0
        )
    """)

    # Full-Text Search (FTS5) for instant search across voice notes
    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
            processed_text,
            raw_text,
            mode,
            app_name,
            content='transcripts',
            content_rowid='id'
        )
    """)

    # FTS Triggers to keep FTS index synced
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
            INSERT INTO transcripts_fts(rowid, processed_text, raw_text, mode, app_name)
            VALUES (new.id, new.processed_text, new.raw_text, new.mode, new.app_name);
        END;
    """)

    # Settings Key-Value Store
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # Custom Jargon & Personal Vocabulary Table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vocabulary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT UNIQUE NOT NULL,
            category TEXT DEFAULT 'general',
            created_at REAL NOT NULL
        )
    """)

    # Default Settings
    default_settings = {
        "shortcut_mode": "hybrid",  # push_to_talk, toggle, hybrid
        "hotkey": "<alt>+<space>",
        "active_mode": "enhancement",  # dictation, enhancement, email, code, rewrite, assistant
        "model_name": "tiny.en",
        "device": "cpu",
        "compute_type": "int8",
        "auto_paste": "true",
        "app_aware": "true",
        "vad_sensitivity": "0.5",
        "ui_port": "6992",
    }

    for k, v in default_settings.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Seed initial vocabulary
    seed_vocab = [
        "OpenWrite", "Whisper", "PyTorch", "FastAPI", "React", "TypeScript",
        "SQLite", "CUDA", "iGPU", "VAD", "MedVision", "GitHub"
    ]
    now = time.time()
    for word in seed_vocab:
        cur.execute("INSERT OR IGNORE INTO vocabulary (term, category, created_at) VALUES (?, 'technical', ?)", (word, now))

    conn.commit()
    conn.close()


def insert_transcript(raw_text: str, processed_text: str, mode: str, app_name: str = "", duration_ms: int = 0) -> int:
    conn = get_db_connection()
    cur = conn.cursor()
    words = len(processed_text.split())
    now = time.time()
    cur.execute("""
        INSERT INTO transcripts (created_at, raw_text, processed_text, mode, app_name, duration_ms, word_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (now, raw_text, processed_text, mode, app_name, duration_ms, words))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def search_transcripts(query: str = "", mode: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()

    if query.strip():
        sql = """
            SELECT t.* FROM transcripts t
            JOIN transcripts_fts f ON t.id = f.rowid
            WHERE transcripts_fts MATCH ?
        """
        params = [f'"{query}"*']
        if mode:
            sql += " AND t.mode = ?"
            params.append(mode)
        sql += " ORDER BY t.created_at DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)
    else:
        sql = "SELECT * FROM transcripts"
        params = []
        if mode:
            sql += " WHERE mode = ?"
            params.append(mode)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur.execute(sql, params)

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_settings() -> Dict[str, str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    settings = {r["key"]: r["value"] for r in cur.fetchall()}
    conn.close()
    return settings


def update_setting(key: str, value: str) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def get_vocabulary() -> List[str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT term FROM vocabulary ORDER BY term ASC")
    terms = [r["term"] for r in cur.fetchall()]
    conn.close()
    return terms


def add_vocabulary(term: str, category: str = "custom") -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO vocabulary (term, category, created_at) VALUES (?, ?, ?)", (term.strip(), category, time.time()))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    conn.close()
    return success


if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
    print("Settings:", get_settings())
    print("Vocabulary count:", len(get_vocabulary()))
