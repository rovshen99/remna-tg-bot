import logging
from collections import defaultdict
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from modules.config import (
    ADMIN_MENU_STATE,
    ADMIN_WAITING_INPUT,
    SUPER_ADMIN_USER_IDS,
    MAIN_MENU,
    GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID,
    GOOGLE_OAUTH_TOKEN_FILE,
    GOOGLE_SERVICE_ACCOUNT_FILE,
)
from modules.api.users import UserAPI
from modules.handlers.core.start import show_main_menu
from modules.utils import admin_store
from modules.utils.auth import check_superadmin
from modules.utils.google_drive import store_subscription_links

logger = logging.getLogger(__name__)


def _format_admin_label(admin: dict) -> str:
    name = admin.get("display_name") or "Без имени"
    status = "🟢" if admin.get("is_active") else "🔴"
    return f"{status} {name} ({admin['user_id']})"


def _parse_owner_id(tag_value):
    """Convert numeric tag to owner id or return None."""
    tag = str(tag_value or "").strip()
    return int(tag) if tag.isdigit() else None


def _format_status_counts(counts: dict, title: str) -> str:
    status_order = [
        ("ACTIVE", "✅"),
        ("LIMITED", "⚠️"),
        ("EXPIRED", "⏰"),
        ("DISABLED", "❌"),
    ]
    parts = []
    for status, emoji in status_order:
        count = int(counts.get(status, 0))
        if count > 0:
            parts.append(f"{emoji} {count}")

    if not parts:
        return f"{title}: ▫️0"
    return f"{title}: " + " / ".join(parts)


def _format_category(counts: dict, icon: str, title: str) -> str:
    """Human-friendly line for a category (users/devices) with statuses."""
    return f"  {icon} {_format_status_counts(counts, title)}"


async def _collect_dealer_usage_stats():
    """Collect per-dealer user status and HWID device counts grouped by user status."""
    dealer_user_stats: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    dealer_device_stats: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    user_owner_status: dict[str, tuple[int, str]] = {}

    try:
        users_response = await UserAPI.get_all_users()
        users = []
        if isinstance(users_response, dict):
            if "users" in users_response:
                users = users_response["users"]
            elif "response" in users_response and isinstance(users_response["response"], dict):
                users = users_response["response"].get("users") or []
        elif isinstance(users_response, list):
            users = users_response

        for user in users:
            owner_id = _parse_owner_id(user.get("tag"))
            if owner_id is None:
                continue
            status = str(user.get("status") or "UNKNOWN").upper()
            dealer_user_stats[owner_id][status] += 1
            user_uuid = user.get("uuid")
            if user_uuid:
                user_owner_status[user_uuid] = (owner_id, status)
    except Exception as exc:
        logger.error("Failed to collect dealer user stats: %s", exc)

    try:
        devices = await UserAPI.get_all_hwid_devices()
        for device in devices or []:
            user_uuid = device.get("userUuid") or device.get("user_uuid")
            if not user_uuid:
                continue
            owner_info = user_owner_status.get(user_uuid)
            if not owner_info:
                continue
            owner_id, status = owner_info
            dealer_device_stats[owner_id][status] += 1
    except Exception as exc:
        logger.error("Failed to collect dealer device stats: %s", exc)

    return dealer_user_stats, dealer_device_stats


def _build_admins_keyboard() -> InlineKeyboardMarkup:
    admins = admin_store.list_admins()
    keyboard = [[InlineKeyboardButton("➕ Добавить диллера", callback_data="admin_add")]]

    if admins:
        for admin in admins:
            label = _format_admin_label(admin)
            name_or_id = admin.get('display_name') or str(admin['user_id'])
            toggle_text = "🚫 Выкл" if admin.get("is_active") else "✅ Вкл"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{toggle_text} {name_or_id}",
                        callback_data=f"admin_toggle_{admin['user_id']}",
                    ),
                    InlineKeyboardButton(
                        f"🗑️ {name_or_id}",
                        callback_data=f"admin_remove_{admin['user_id']}",
                    ),
                ]
            )
    else:
        keyboard.append([InlineKeyboardButton("ℹ️ Пока нет диллеров", callback_data="admin_noop")])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)


@check_superadmin
async def show_admins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admins management menu"""
    admins = admin_store.list_admins()
    dealer_user_stats, dealer_device_stats = await _collect_dealer_usage_stats()
    status_legend = "Легенда статусов: ✅ актив / ⚠️ ограничен / ⏰ истёк / ❌ выключен\n\n"
    message = "👑 *Управление диллерами*\n\n" + status_legend
    if admins:
        message += "Сейчас назначены:\n"
        # сортируем по имени для стабильного порядка
        sorted_admins = sorted(admins, key=lambda a: (a.get("display_name") or "").lower())
        total_user_counts: dict[str, int] = defaultdict(int)
        total_device_counts: dict[str, int] = defaultdict(int)
        for admin in sorted_admins:
            admin_id = int(admin["user_id"])
            user_counts = dealer_user_stats.get(admin_id, {})
            device_counts = dealer_device_stats.get(admin_id, {})
            for k, v in user_counts.items():
                total_user_counts[k] += v
            for k, v in device_counts.items():
                total_device_counts[k] += v

            message += f"• {_format_admin_label(admin)}\n"
            message += _format_category(user_counts, "👥", "Пользователи") + "\n"
            message += _format_category(device_counts, "📱", "Устройства") + "\n"

        if total_user_counts or total_device_counts:
            message += "\nИтого по всем диллерам:\n"
            message += _format_category(total_user_counts, "👥", "Пользователи") + "\n"
            message += _format_category(total_device_counts, "📱", "Устройства") + "\n"
    else:
        message += "Список диллеров пуст.\n"

    message += "\nВыберите действие:"

    reply_markup = _build_admins_keyboard()

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text=message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    context.user_data.pop("awaiting_admin_input", None)
    return ADMIN_MENU_STATE


@check_superadmin
async def handle_admins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin management callbacks"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "admin_add":
        context.user_data["awaiting_admin_input"] = True
        message = (
            "➕ *Добавление диллера*\n\n"
            "Отправьте сообщение в формате:\n"
            "`<Telegram ID> <Имя>`\n\n"
            "Пример: `489917172 Иван`"
        )
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="admin_cancel_add")]])
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=back_markup)
        return ADMIN_WAITING_INPUT

    if data == "admin_export_subs":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Начать экспорт", callback_data="admin_confirm_export")],
            [InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_export")],
        ])
        await query.edit_message_text(
            "Экспорт может занять несколько минут. Продолжить?",
            reply_markup=keyboard,
        )
        return ADMIN_MENU_STATE

    if data.startswith("admin_remove_"):
        user_id = data.split("_")[2]
        context.user_data["remove_admin_id"] = user_id
        keyboard = [
            [
                InlineKeyboardButton("✅ Удалить", callback_data=f"admin_confirm_remove_{user_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="admin_cancel_remove"),
            ]
        ]
        await query.edit_message_text(
            f"⚠️ Удалить диллера {user_id}?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return ADMIN_MENU_STATE

    if data == "admin_cancel_add":
        context.user_data.pop("awaiting_admin_input", None)
        return await show_admins_menu(update, context)

    if data == "admin_cancel_remove":
        context.user_data.pop("remove_admin_id", None)
        return await show_admins_menu(update, context)

    if data == "admin_cancel_export":
        return await show_admins_menu(update, context)

    if data == "admin_confirm_export":
        return await _export_subscriptions(update, context, return_to_admin=True)

    if data.startswith("admin_confirm_remove_"):
        user_id = int(data.split("_")[3])
        removed = admin_store.remove_admin(user_id)
        message = "✅ Диллер удалён." if removed else "❌ Диллер не найден."
        await query.edit_message_text(message)
        return await show_admins_menu(update, context)

    if data == "admin_export_subs":
        return await _export_subscriptions(update, context)

    if data.startswith("admin_toggle_"):
        user_id = int(data.split("_")[2])
        admins = {adm["user_id"]: adm for adm in admin_store.list_admins()}
        admin = admins.get(user_id)
        if not admin:
            await query.edit_message_text("❌ Диллер не найден.")
            return await show_admins_menu(update, context)
        new_state = not bool(admin.get("is_active"))
        admin_store.set_admin_active(user_id, new_state)
        await query.edit_message_text("✅ Состояние обновлено.")
        return await show_admins_menu(update, context)

    if data == "admin_noop":
        return ADMIN_MENU_STATE

    if data == "back_to_main":
        context.user_data.pop("awaiting_admin_input", None)
        await show_main_menu(update, context)
        return MAIN_MENU

    return ADMIN_MENU_STATE


@check_superadmin
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input while adding admin"""
    if not context.user_data.get("awaiting_admin_input"):
        return ADMIN_MENU_STATE

    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await update.message.reply_text("⚠️ Укажите Telegram ID (число) и, при необходимости, имя.")
        return ADMIN_WAITING_INPUT

    user_id = int(parts[0])
    display_name = parts[1].strip() if len(parts) > 1 else ""

    if user_id in SUPER_ADMIN_USER_IDS:
        await update.message.reply_text("⚠️ Этот пользователь уже является суперадмином.")
        return ADMIN_WAITING_INPUT

    if admin_store.add_admin(user_id, display_name):
        await update.message.reply_text("✅ Диллер добавлен.")
        context.user_data.pop("awaiting_admin_input", None)
    else:
        await update.message.reply_text("ℹ️ Такой диллер уже существует.")
        return ADMIN_WAITING_INPUT

    return await show_admins_menu(update, context)

@check_superadmin
async def _export_subscriptions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    return_to_admin: bool = True,
):
    from modules.api.users import UserAPI

    query = update.callback_query
    destination = show_admins_menu if return_to_admin else show_main_menu

    if (
        not GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID
        or not (GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_OAUTH_TOKEN_FILE)
    ):
        await query.edit_message_text("⚠️ Google Drive не настроен.")
        return await destination(update, context)

    await query.edit_message_text("⏳ Экспорт подписок в Google Drive...")

    users_response = await UserAPI.get_all_users()
    users = []
    if isinstance(users_response, dict) and users_response.get("users"):
        users = users_response["users"]
    elif isinstance(users_response, list):
        users = users_response

    subs_list = await UserAPI.get_all_subscriptions_list()
    subs_by_short = {}
    subs_by_username = {}
    for item in subs_list:
        user_info = item.get("user", {})
        short_uuid = user_info.get("shortUuid")
        username = user_info.get("username")
        if short_uuid:
            subs_by_short[short_uuid] = item
        if username:
            subs_by_username[username] = item

    processed_users = 0

    for user in users:
        username = user.get("username", "unknown")
        short_uuid = user.get("shortUuid")
        subscription = subs_by_short.get(short_uuid) if short_uuid else None
        if not subscription:
            subscription = subs_by_username.get(username)
        links = []
        if subscription:
            links = subscription.get("links") or []

        await store_subscription_links(username, short_uuid, links or [])
        processed_users += 1

    if processed_users == 0:
        await query.edit_message_text("ℹ️ Ссылок не найдено.")
        return await destination(update, context)

    await query.edit_message_text(f"✅ Экспорт завершён. Файлов обновлено: {processed_users}")
    return await destination(update, context)
