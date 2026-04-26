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
            is_active INTEGER NOT NULL DEFAULT 1,
            device_limit_presets TEXT
        )
        """
    )
    # Ensure legacy DBs have is_active column
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(admins)").fetchall()}
    if "is_active" not in columns:
        conn.execute("ALTER TABLE admins ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    if "device_limit_presets" not in columns:
        conn.execute("ALTER TABLE admins ADD COLUMN device_limit_presets TEXT")
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
                    is_active,
                    device_limit_presets
                FROM admins
                ORDER BY is_active DESC, display_name COLLATE NOCASE
                """
            ).fetchall()
            admins = [dict(row) for row in rows]
            for admin in admins:
                admin["device_limit_presets_list"] = _parse_device_limit_presets(admin.get("device_limit_presets"))
            return admins


def _parse_device_limit_presets(raw_value: Optional[str]) -> Optional[List[int]]:
    value = (raw_value or "").strip()
    if not value:
        return None

    result: List[int] = []
    seen = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            preset = int(token)
        except ValueError:
            continue
        if preset < 0 or preset in seen:
            continue
        seen.add(preset)
        result.append(preset)

    return result or None


def _serialize_device_limit_presets(presets: List[int]) -> str:
    seen = set()
    normalized: List[int] = []
    for preset in presets:
        if preset < 0 or preset in seen:
            continue
        seen.add(preset)
        normalized.append(preset)
    if not normalized:
        raise ValueError("Device presets must contain at least one non-negative integer")
    return ",".join(str(value) for value in normalized)


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


def get_admin(user_id: int) -> Optional[Dict]:
    user_id = int(user_id)
    with _lock:
        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    user_id,
                    COALESCE(display_name, '') AS display_name,
                    is_active,
                    device_limit_presets
                FROM admins
                WHERE user_id=?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return None
            admin = dict(row)
            admin["device_limit_presets_list"] = _parse_device_limit_presets(admin.get("device_limit_presets"))
            return admin


def get_admin_device_limit_presets(user_id: int) -> Optional[List[int]]:
    admin = get_admin(user_id)
    if not admin:
        return None
    return admin.get("device_limit_presets_list")


def set_admin_device_limit_presets(user_id: int, presets: List[int]) -> bool:
    user_id = int(user_id)
    serialized = _serialize_device_limit_presets(presets)
    with _lock:
        with _get_connection() as conn:
            cur = conn.execute(
                "UPDATE admins SET device_limit_presets=? WHERE user_id=?",
                (serialized, user_id),
            )
            conn.commit()
            updated = cur.rowcount > 0
            if updated:
                logger.info("Updated device presets for admin %s: %s", user_id, serialized)
            return updated


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
