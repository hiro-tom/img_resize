import base64
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet

DB_PATH = Path(__file__).parent / "data" / "app.db"
_SECRET = os.getenv("SESSION_SECRET", "change-me")


def _get_fernet() -> Fernet:
    # Derive a 32-byte key from SESSION_SECRET
    import hashlib

    digest = hashlib.sha256(_SECRET.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                detail TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL,
                password TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_settings(data: Dict[str, Any]) -> None:
    # Encrypt password if present; retain previous password when empty
    current = load_settings() or {}
    pwd = data.get("password")
    if (pwd is None or pwd == "") and current.get("password"):
        data["password"] = current["password"]
    elif pwd:
        f = _get_fernet()
        token = f.encrypt(pwd.encode())
        data["password"] = token.decode()

    serialized = json.dumps(data)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (id, data) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data",
            (serialized,),
        )
        conn.commit()


def load_settings() -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
        if not row:
            return None
        data = json.loads(row[0])
        # Decrypt password if present
        pwd = data.get("password")
        if isinstance(pwd, str) and pwd:
            try:
                f = _get_fernet()
                dec = f.decrypt(pwd.encode()).decode()
                data["password"] = dec
            except Exception:
                # If decryption fails, treat as unset
                data["password"] = None
        return data


def insert_log(level: str, message: str, detail: Optional[str] = None) -> None:
    with get_connection() as conn:
        # Use JST (UTC+9) for Japan time
        conn.execute(
            "INSERT INTO logs (created_at, level, message, detail) VALUES (datetime('now', '+9 hours'), ?, ?, ?)",
            (level, message, detail),
        )
        conn.commit()


def list_logs(limit: int = 200):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, level, message, detail FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_logs() -> None:
    """Delete all log entries from the database."""
    with get_connection() as conn:
        conn.execute("DELETE FROM logs")
        conn.commit()

def load_user() -> Optional[Dict[str, str]]:
    """Load login user credentials (username and password)."""
    with get_connection() as conn:
        row = conn.execute("SELECT username, password FROM users WHERE id = 1").fetchone()
        if not row:
            return None
        
        username = row[0]
        encrypted_password = row[1]
        
        # Decrypt password
        try:
            f = _get_fernet()
            decrypted_password = f.decrypt(encrypted_password.encode()).decode()
            return {"username": username, "password": decrypted_password}
        except Exception:
            # If decryption fails, return None
            return None


def save_user(username: str, password: str) -> None:
    """Save login user credentials (username and password)."""
    with get_connection() as conn:
        # Encrypt password before storing
        f = _get_fernet()
        encrypted_password = f.encrypt(password.encode()).decode()
        
        conn.execute(
            "INSERT INTO users (id, username, password) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET username = excluded.username, password = excluded.password",
            (username, encrypted_password),
        )
        conn.commit()