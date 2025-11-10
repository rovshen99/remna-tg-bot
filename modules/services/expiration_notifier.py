import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone, time as dtime
from typing import Dict, List, Optional, Tuple

from telegram.ext import Application, ContextTypes

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from modules.api.users import UserAPI
from modules.utils import admin_store
from modules.utils.formatters import escape_markdown
from modules.config import (
    EXPIRATION_NOTIFICATION_ENABLED,
    EXPIRATION_NOTIFICATION_DAYS,
    EXPIRATION_NOTIFICATION_HOUR,
    EXPIRATION_NOTIFICATION_MINUTE,
    EXPIRATION_NOTIFICATION_TZ,
    SUPER_ADMIN_USER_IDS,
)

logger = logging.getLogger(__name__)

try:
    NOTIFICATION_TZ = ZoneInfo(EXPIRATION_NOTIFICATION_TZ)
except ZoneInfoNotFoundError:
    logger.warning(
        "Unknown timezone '%s' for expiration notifications. Falling back to UTC.",
        EXPIRATION_NOTIFICATION_TZ,
    )
    NOTIFICATION_TZ = timezone.utc

SUPER_ADMINS = set(int(admin_id) for admin_id in SUPER_ADMIN_USER_IDS)

ExpiringUser = Tuple[Dict, datetime]


def schedule_expiration_notifications(application: Application) -> None:
    """Register the daily expiration notification job."""
    if not EXPIRATION_NOTIFICATION_ENABLED:
        logger.info("Expiration notifications are disabled via configuration.")
        return

    job_time = dtime(
        hour=EXPIRATION_NOTIFICATION_HOUR,
        minute=EXPIRATION_NOTIFICATION_MINUTE,
        tzinfo=NOTIFICATION_TZ,
    )
    application.job_queue.run_daily(
        notify_expiring_subscriptions,
        time=job_time,
        name="expiration_notifications",
    )
    logger.info(
        "Scheduled expiration notifications at %02d:%02d (%s).",
        EXPIRATION_NOTIFICATION_HOUR,
        EXPIRATION_NOTIFICATION_MINUTE,
        NOTIFICATION_TZ,
    )


async def notify_expiring_subscriptions(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job callback: notify admins about soon-to-expire subscriptions."""
    try:
        expiring_users = await _collect_expiring_users(EXPIRATION_NOTIFICATION_DAYS)
    except Exception as exc:
        logger.error("Failed to collect expiring users: %s", exc, exc_info=True)
        return

    if not expiring_users:
        logger.info("No expiring users found for notification window.")
        return

    admin_records = {
        admin["user_id"]: admin
        for admin in admin_store.list_admins()
        if admin.get("is_active", 1)
    }
    grouped_users = _group_by_owner(expiring_users)

    await _notify_admins(context, grouped_users, admin_records)
    await _notify_superadmins(context, grouped_users, admin_records)


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    grouped_users: Dict[Optional[int], List[ExpiringUser]],
    admin_records: Dict[int, Dict],
) -> None:
    for admin_id, items in grouped_users.items():
        if admin_id is None or admin_id in SUPER_ADMINS:
            continue
        if admin_id not in admin_records:
            continue
        text = _build_admin_message(items)
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logger.debug("Sent expiration reminder to admin %s (%d users).", admin_id, len(items))
        except Exception as exc:
            logger.error("Failed to send expiration reminder to admin %s: %s", admin_id, exc)


async def _notify_superadmins(
    context: ContextTypes.DEFAULT_TYPE,
    grouped_users: Dict[Optional[int], List[ExpiringUser]],
    admin_records: Dict[int, Dict],
) -> None:
    if not SUPER_ADMINS:
        return

    message = _build_superadmin_message(grouped_users, admin_records)
    if not message:
        return

    for chat_id in SUPER_ADMINS:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            logger.debug("Sent expiration summary to superadmin %s.", chat_id)
        except Exception as exc:
            logger.error("Failed to send expiration summary to superadmin %s: %s", chat_id, exc)


async def _collect_expiring_users(days: int) -> List[ExpiringUser]:
    response = await UserAPI.get_all_users()
    users: List[Dict] = []
    if isinstance(response, dict):
        users = response.get("users") or []
    elif isinstance(response, list):
        users = response

    if not users:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    expiring: List[ExpiringUser] = []

    for user in users:
        expire_at = _parse_iso_datetime(user.get("expireAt"))
        if not expire_at:
            continue
        if now <= expire_at <= cutoff:
            expiring.append((user, expire_at))

    expiring.sort(key=lambda item: item[1])
    return expiring


def _group_by_owner(expiring_users: List[ExpiringUser]) -> Dict[Optional[int], List[ExpiringUser]]:
    grouped: Dict[Optional[int], List[ExpiringUser]] = defaultdict(list)
    for user, expire_at in expiring_users:
        tag = str(user.get("tag") or "").strip()
        owner_id: Optional[int]
        if tag.isdigit():
            owner_id = int(tag)
        else:
            owner_id = None
        grouped[owner_id].append((user, expire_at))
    return grouped


def _build_admin_message(items: List[ExpiringUser]) -> str:
    header = (
        "⚠️ *Напоминание о подписках*\n"
        f"У {len(items)} ваших пользователей подписка истекает в ближайшие "
        f"{EXPIRATION_NOTIFICATION_DAYS} дн.\n\n"
    )
    lines = [_format_user_line(user, expire_at) for user, expire_at in items]
    return header + "\n".join(lines)


def _build_superadmin_message(
    grouped_users: Dict[Optional[int], List[ExpiringUser]],
    admin_records: Dict[int, Dict],
) -> Optional[str]:
    total = sum(len(items) for items in grouped_users.values())
    if total == 0:
        return None

    header = (
        "👑 *Сводка истекающих подписок*\n"
        f"Всего пользователей в окне {EXPIRATION_NOTIFICATION_DAYS} дн.: {total}\n\n"
    )
    sections = []
    for owner_id in sorted(grouped_users.keys(), key=lambda x: (x is None, x)):
        items = grouped_users[owner_id]
        title = _owner_title(owner_id, admin_records)
        section_lines = [f"*{escape_markdown(title)}* — {len(items)} пользователей"]
        section_lines.extend(_format_user_line(user, expire_at) for user, expire_at in items)
        sections.append("\n".join(section_lines))

    return header + "\n\n".join(sections)


def _owner_title(owner_id: Optional[int], admin_records: Dict[int, Dict]) -> str:
    if owner_id is None:
        return "Без владельца"
    admin = admin_records.get(owner_id)
    if admin and admin.get("display_name"):
        return f"{admin['display_name']} (ID {owner_id})"
    return f"ID {owner_id}"


def _format_user_line(user: Dict, expire_at: datetime) -> str:
    username = user.get("username") or user.get("email") or user.get("uuid") or "Без имени"
    expire_str = expire_at.astimezone(NOTIFICATION_TZ).strftime("%d.%m.%Y %H:%M")
    return f"• `{escape_markdown(username)}` — до {expire_str}"


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        logger.debug("Invalid ISO date encountered: %s", value)
        return None
