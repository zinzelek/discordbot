import sqlite3
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional

logger = logging.getLogger("InstaLingBot.Database")

DB_FILE = os.path.join(os.path.dirname(__file__), "accounts.db")
BACKUP_JSON_FILE = os.path.join(os.path.dirname(__file__), "accounts_backup.json")

def _save_local_backup():
    """Zapisuje lokalną kopię zapasową kont w formacie JSON"""
    try:
        accs = get_all_accounts()
        with open(BACKUP_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(accs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"Nie udało się zapisać lokalnego backupu JSON: {e}")

def _restore_from_local_backup_if_empty():
    """Przywraca konta z lokalnego pliku JSON jeśli baza SQLite jest pusta"""
    if not os.path.exists(BACKUP_JSON_FILE):
        return
    try:
        accs = get_all_accounts()
        if not accs:
            with open(BACKUP_JSON_FILE, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
            if backup_data and isinstance(backup_data, list):
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                for item in backup_data:
                    c.execute("""
                        INSERT OR IGNORE INTO accounts (discord_user_id, login, password, language, auto_daily, last_run, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        item.get("discord_user_id"),
                        item.get("login"),
                        item.get("password"),
                        item.get("language", "auto"),
                        item.get("auto_daily", 1),
                        item.get("last_run"),
                        item.get("expires_at")
                    ))
                conn.commit()
                conn.close()
                logger.info(f"💾 Przywrócono {len(backup_data)} kont z lokalnego backupu JSON!")
    except Exception as e:
        logger.error(f"Błąd przywracania z lokalnego backupu JSON: {e}")

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
            auto_daily INTEGER DEFAULT 1,
            last_run TEXT,
            expires_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(discord_user_id, login)
        )
    """)
    # Migracja dla istniejących baz: dodanie kolumny expires_at jeśli jej nie ma
    try:
        c.execute("ALTER TABLE accounts ADD COLUMN expires_at TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()
    _restore_from_local_backup_if_empty()

def is_account_active(acc: Dict) -> bool:
    """Sprawdza czy konto nie wygasło czasowo"""
    expires_at = acc.get("expires_at")
    if not expires_at or str(expires_at).lower() in ("none", "null", ""):
        return True  # Na zawsze
    try:
        exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        return datetime.now() <= exp_dt
    except Exception:
        return True

def add_or_update_account(
    discord_user_id: int,
    login: str,
    password: str,
    language: str = "auto",
    auto_daily: int = 1,
    expires_at: Optional[str] = None
):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO accounts (discord_user_id, login, password, language, auto_daily, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(discord_user_id, login) DO UPDATE SET
            password = excluded.password,
            language = excluded.language,
            auto_daily = excluded.auto_daily,
            expires_at = excluded.expires_at
    """, (discord_user_id, login.strip(), password.strip(), language.lower().strip(), auto_daily, expires_at))
    conn.commit()
    conn.close()
    _save_local_backup()

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
    """Pobiera tylko aktywne (niewygasłe) konta z włączonym harmonogramem"""
    all_acc = get_all_accounts()
    return [a for a in all_acc if is_account_active(a)]

def delete_account(discord_user_id: int, login: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM accounts WHERE discord_user_id = ? AND login = ?", (discord_user_id, login.strip()))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    if changed:
        _save_local_backup()
    return changed

def set_auto_daily(discord_user_id: int, login: str, enabled: bool) -> bool:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    val = 1 if enabled else 0
    c.execute("UPDATE accounts SET auto_daily = ? WHERE discord_user_id = ? AND login = ?", (val, discord_user_id, login.strip()))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    if changed:
        _save_local_backup()
    return changed

def update_last_run(discord_user_id: int, login: str, timestamp_str: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE accounts SET last_run = ? WHERE discord_user_id = ? AND login = ?", (timestamp_str, discord_user_id, login.strip()))
    conn.commit()
    conn.close()
    _save_local_backup()

async def sync_with_firebase(firebase_client) -> dict:
    """
    Synchronizuje konta między lokalną bazą SQLite a trwałą chmurą Firebase.
    - Jeśli pliki zostały zmienione/zresetowane, przywraca wszystkie konta z Firebase.
    - Jeśli lokalnie są konta, których nie ma w chmurze, wysyła je do Firebase.
    - Aktualizuje lokalny plik backupu accounts_backup.json.
    """
    stats = {"restored": 0, "uploaded": 0, "total": 0}
    try:
        cloud_accounts = await firebase_client.get_all_accounts_cloud()
        local_accounts = get_all_accounts()

        local_map = {(int(a["discord_user_id"]), a["login"].strip().lower()): a for a in local_accounts}

        # 1. Przywróć konta z Firebase do SQLite
        if cloud_accounts and isinstance(cloud_accounts, dict):
            for acc_key, acc_data in cloud_accounts.items():
                if not isinstance(acc_data, dict):
                    continue
                user_id = acc_data.get("discord_user_id")
                login = acc_data.get("login")
                password = acc_data.get("password")
                if not user_id or not login or not password:
                    continue

                user_id = int(user_id)
                norm_key = (user_id, login.strip().lower())
                if norm_key not in local_map:
                    # Nowe konto z chmury -> przywracamy do SQLite
                    add_or_update_account(
                        discord_user_id=user_id,
                        login=login,
                        password=password,
                        language=acc_data.get("language", "auto"),
                        auto_daily=int(acc_data.get("auto_daily", 1)),
                        expires_at=acc_data.get("expires_at")
                    )
                    if acc_data.get("last_run"):
                        update_last_run(user_id, login, acc_data.get("last_run"))
                    stats["restored"] += 1
                else:
                    # Jeśli w chmurze jest inna data wygaśnięcia lub hasło, zaktualizuj lokalnie
                    loc = local_map[norm_key]
                    if (acc_data.get("password") != loc.get("password") or 
                        acc_data.get("expires_at") != loc.get("expires_at") or 
                        acc_data.get("language") != loc.get("language")):
                        add_or_update_account(
                            discord_user_id=user_id,
                            login=login,
                            password=password,
                            language=acc_data.get("language", "auto"),
                            auto_daily=int(acc_data.get("auto_daily", 1)),
                            expires_at=acc_data.get("expires_at")
                        )

        # 2. Wyślij konta z SQLite do Firebase, jeśli jakiegoś brakuje w chmurze
        cloud_keys = set()
        if cloud_accounts and isinstance(cloud_accounts, dict):
            for acc_data in cloud_accounts.values():
                if isinstance(acc_data, dict) and acc_data.get("discord_user_id") and acc_data.get("login"):
                    cloud_keys.add((int(acc_data["discord_user_id"]), str(acc_data["login"]).strip().lower()))

        for local_acc in get_all_accounts():
            norm_key = (int(local_acc["discord_user_id"]), local_acc["login"].strip().lower())
            if norm_key not in cloud_keys:
                ok = await firebase_client.save_account_cloud(local_acc)
                if ok:
                    stats["uploaded"] += 1

        all_now = get_all_accounts()
        stats["total"] = len(all_now)
        _save_local_backup()

        logger.info(
            f"☁️ [ACCOUNT-SYNC] Zsynchronizowano konta z chmurą Firebase! "
            f"Przywrócono: {stats['restored']}, Wysłano do chmury: {stats['uploaded']}, Łącznie aktywnych kont: {stats['total']}."
        )
    except Exception as e:
        logger.error(f"Błąd podczas synchronizacji kont z Firebase: {e}")

    return stats

# Inicjalizacja przy imporcie
init_db()
