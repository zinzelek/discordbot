import sqlite3
import os
from typing import List, Dict, Optional

DB_FILE = os.path.join(os.path.dirname(__file__), "accounts.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_user_id INTEGER NOT NULL,
            login TEXT NOT NULL,
            password TEXT NOT NULL,
            language TEXT DEFAULT 'auto',
            auto_daily INTEGER DEFAULT 0,
            last_run TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(discord_user_id, login)
        )
    """)
    conn.commit()
    conn.close()

def add_or_update_account(discord_user_id: int, login: str, password: str, language: str = "auto", auto_daily: int = 0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO accounts (discord_user_id, login, password, language, auto_daily)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(discord_user_id, login) DO UPDATE SET
            password = excluded.password,
            language = excluded.language,
            auto_daily = excluded.auto_daily
    """, (discord_user_id, login.strip(), password.strip(), language.lower().strip(), auto_daily))
    conn.commit()
    conn.close()

def get_user_accounts(discord_user_id: int) -> List[Dict]:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE discord_user_id = ?", (discord_user_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_account_by_login(discord_user_id: int, login: str) -> Optional[Dict]:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts WHERE discord_user_id = ? AND login = ?", (discord_user_id, login.strip()))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_accounts() -> List[Dict]:
    """Pobiera wszystkie zarejestrowane konta w bazie bota"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM accounts")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

def get_all_auto_accounts() -> List[Dict]:
    return get_all_accounts()

def delete_account(discord_user_id: int, login: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE discord_user_id = ? AND login = ?", (discord_user_id, login.strip()))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def set_auto_daily(discord_user_id: int, login: str, enabled: bool) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    val = 1 if enabled else 0
    c.execute("UPDATE accounts SET auto_daily = ? WHERE discord_user_id = ? AND login = ?", (val, discord_user_id, login.strip()))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def update_last_run(discord_user_id: int, login: str, timestamp_str: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE accounts SET last_run = ? WHERE discord_user_id = ? AND login = ?", (timestamp_str, discord_user_id, login.strip()))
    conn.commit()
    conn.close()

# Inicjalizacja przy imporcie
init_db()
