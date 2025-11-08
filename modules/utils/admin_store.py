import logging
import os
import sqlite3
import threading
from typing import List, Dict, Optional

from modules.config import ADMIN_DB_PATH

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def _ensure_directory(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(ADMIN_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    # Ensure legacy DBs have is_active column
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(admins)").fetchall()}
    if "is_active" not in columns:
        conn.execute("ALTER TABLE admins ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    conn.commit()


def init_db() -> None:
    """Initialize admins database"""
    _ensure_directory(ADMIN_DB_PATH)
    with _lock:
        with _get_connection() as conn:
            _ensure_schema(conn)
    logger.info("Admin database initialized at %s", ADMIN_DB_PATH)


def list_admins() -> List[Dict]:
    with _lock:
        with _get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    user_id,
                    COALESCE(display_name, '') AS display_name,
                    is_active
                FROM admins
                ORDER BY is_active DESC, display_name COLLATE NOCASE
                """
            ).fetchall()
            return [dict(row) for row in rows]


def is_admin(user_id: int) -> bool:
    with _lock:
        with _get_connection() as conn:
            row = conn.execute("SELECT 1 FROM admins WHERE user_id=? AND is_active=1", (int(user_id),)).fetchone()
            return row is not None


def add_admin(user_id: int, display_name: Optional[str] = None) -> bool:
    user_id = int(user_id)
    name = (display_name or "").strip() or None
    with _lock:
        with _get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO admins (user_id, display_name, is_active) VALUES (?, ?, 1)",
                    (user_id, name),
                )
                conn.commit()
                logger.info("Added admin %s (%s)", user_id, name or "Без имени")
                return True
            except sqlite3.IntegrityError:
                logger.warning("Admin %s already exists", user_id)
                return False


def update_admin_name(user_id: int, display_name: Optional[str]) -> None:
    user_id = int(user_id)
    name = (display_name or "").strip() or None
    with _lock:
        with _get_connection() as conn:
            conn.execute(
                "UPDATE admins SET display_name=? WHERE user_id=?",
                (name, user_id),
            )
            conn.commit()


def set_admin_active(user_id: int, active: bool) -> None:
    user_id = int(user_id)
    with _lock:
        with _get_connection() as conn:
            conn.execute(
                "UPDATE admins SET is_active=? WHERE user_id=?",
                (1 if active else 0, user_id),
            )
            conn.commit()


def remove_admin(user_id: int) -> bool:
    user_id = int(user_id)
    with _lock:
        with _get_connection() as conn:
            cur = conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
            conn.commit()
            deleted = cur.rowcount > 0
            if deleted:
                logger.info("Removed admin %s", user_id)
            return deleted


# Initialize DB on import
init_db()
