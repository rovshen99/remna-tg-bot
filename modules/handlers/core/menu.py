from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
import logging

from modules.config import (
    MAIN_MENU,
    USER_MENU,
    NODE_MENU,
    STATS_MENU,
    HOST_MENU,
    INBOUND_MENU,
    BULK_MENU,
    CREATE_USER,
    CREATE_USER_FIELD,
    SELECTING_USER,
    INBOUNDS_MENU_ENABLED,
    EXPIRATION_NOTIFICATION_ENABLED,
    EXPIRATION_NOTIFICATION_DAYS,
)

logger = logging.getLogger(__name__)
from modules.utils.auth import check_authorization, get_user_role, is_admin_user, is_super_admin_user
from modules.handlers.users import show_users_menu, start_create_user, show_user_details
from modules.handlers.nodes import show_nodes_menu
from modules.handlers.stats import show_stats_menu
from modules.handlers.hosts import show_hosts_menu
from modules.handlers.inbounds import show_inbounds_menu, handle_inbounds_menu
from modules.handlers.bulk import show_bulk_menu
from modules.handlers.core.start import show_main_menu
from modules.handlers.users.handlers import user_cache
from modules.handlers.core.language import (
    LANGUAGE_MENU_CALLBACK,
    LANGUAGE_SELECT_PREFIX,
    handle_language_selection,
    show_language_menu,
)
from modules.services.expiration_notifier import (
    build_admin_notification,
    build_superadmin_notification,
    build_notification_payload,
    extend_user_subscription_and_reset,
)

async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu selection"""
    # Проверяем авторизацию
    if not check_authorization(update.effective_user):
        await update.callback_query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        return ConversationHandler.END
    
    query = update.callback_query
    await query.answer()

    role = get_user_role(update.effective_user.id)
    is_admin = is_admin_user(update.effective_user.id)
    is_superadmin = is_super_admin_user(update.effective_user.id)
    is_admin_only = is_admin and not is_superadmin
    context.user_data['role'] = role
    context.user_data['is_admin'] = is_admin
    context.user_data['is_superadmin'] = is_superadmin

    data = query.data
    can_manage_users = is_admin or is_superadmin
    superadmin_sections = {
        "nodes",
        "menu_nodes",
        "stats",
        "menu_stats",
        "hosts",
        "menu_hosts",
        "admins",
        "menu_admins",
        "bulk",
        "menu_bulk",
        "export_subscriptions",
        "confirm_export_subscriptions",
        "cancel_export_subscriptions",
    }
    if INBOUNDS_MENU_ENABLED:
        superadmin_sections.update({"inbounds", "menu_inbounds"})
    admin_sections = {"create_user", "menu_create_user", "notify_expiring_self"}

    if data in superadmin_sections and not is_superadmin:
        await query.answer("Этот раздел доступен только суперадминам.", show_alert=True)
        return MAIN_MENU

    if data in admin_sections and not can_manage_users:
        await query.answer("Недостаточно прав для управления пользователями.", show_alert=True)
        return MAIN_MENU

    logger.info(f"=== MENU SELECTION HANDLER ===")
    logger.info(f"Handling menu callback: {data}")
    logger.info(f"Current state: {context.user_data.get('conversation_state', 'unknown')}")
    logger.info(f"==============================")

    if data.startswith("expire_extend_"):
        uuid = data.replace("expire_extend_", "", 1)
        allow_any_owner = bool(is_superadmin)
        success, msg = await extend_user_subscription_and_reset(
            uuid, update.effective_user.id, allow_any_owner=allow_any_owner
        )
        await query.answer(msg, show_alert=not success)
        if success and query.message:
            user_cache.invalidate_user(uuid)
            user_cache.invalidate_all_users()
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=msg,
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        return MAIN_MENU

    if data == "users" or data == "menu_users":
        await show_users_menu(update, context)
        return USER_MENU

    elif data == "nodes" or data == "menu_nodes":
        await show_nodes_menu(update, context)
        return NODE_MENU

    elif data == "stats" or data == "menu_stats":
        await show_stats_menu(update, context)
        return STATS_MENU

    elif data == "hosts" or data == "menu_hosts":
        await show_hosts_menu(update, context)
        return HOST_MENU

    elif data == "admins" or data == "menu_admins":
        from modules.handlers.admins import show_admins_menu
        return await show_admins_menu(update, context)

    elif data == "export_subscriptions":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Обновить", callback_data="confirm_export_subscriptions")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_export_subscriptions")],
        ])
        await query.edit_message_text(
            "Обновление файлов Google Drive может занять несколько минут. Продолжить?",
            reply_markup=keyboard,
        )
        return MAIN_MENU

    elif data == "confirm_export_subscriptions":
        from modules.handlers.admins import _export_subscriptions
        return await _export_subscriptions(update, context, return_to_admin=False)

    elif data == "cancel_export_subscriptions":
        await show_main_menu(update, context)
        return MAIN_MENU

    elif data == "inbounds" or data == "menu_inbounds":
        await show_inbounds_menu(update, context)
        return INBOUND_MENU

    elif data == "notify_expiring_self":
        if not EXPIRATION_NOTIFICATION_ENABLED:
            await query.answer("Уведомления об истечении отключены.", show_alert=True)
            return MAIN_MENU

        text, keyboard = await build_notification_payload(
            update.effective_user.id, is_superadmin=is_superadmin
        )

        if not text:
            text = (
                f"На ближайшие {EXPIRATION_NOTIFICATION_DAYS} дн. "
                f"{'нет пользователей с истекающей подпиской.' if is_superadmin else 'нет ваших пользователей с истекающей подпиской.'}"
            )

        back_row = [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
        if keyboard:
            rows = list(keyboard.inline_keyboard)
            rows.append(back_row)
            keyboard = InlineKeyboardMarkup(rows)
        else:
            keyboard = InlineKeyboardMarkup([back_row])

        # Стараемся заменить исходное сообщение; если не получается, шлём новое
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.debug("Falling back to send_message for expiring notification: %s", exc)
            chat_id = update.effective_chat.id if update.effective_chat else update.effective_user.id
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
                reply_markup=keyboard,
            )
        return MAIN_MENU

    elif data == LANGUAGE_MENU_CALLBACK:
        await show_language_menu(update, context)
        return MAIN_MENU

    # Handle inbound menu callbacks (temporary fix)
    elif data in ["list_inbounds", "list_full_inbounds", "list_inbounds_stats", "filter_inbounds", "refresh_inbounds", "debug_users"]:
        logger.info(f"Redirecting inbound callback to handle_inbounds_menu: {data}")
        return await handle_inbounds_menu(update, context)

    elif data == "bulk" or data == "menu_bulk":
        await show_bulk_menu(update, context)
        return BULK_MENU

    elif data == "create_user" or data == "menu_create_user":
        await start_create_user(update, context)
        return CREATE_USER_FIELD

    elif data == "back_to_main":
        await show_main_menu(update, context)
        return MAIN_MENU

    elif data.startswith("view_"):
        uuid = data.split("_")[1]
        await show_user_details(update, context, uuid)
        return SELECTING_USER

    elif data.startswith(LANGUAGE_SELECT_PREFIX):
        return await handle_language_selection(update, context)

    inbounds_callbacks = {
        "inbounds",
        "menu_inbounds",
        "list_inbounds",
        "list_full_inbounds",
        "list_inbounds_stats",
        "filter_inbounds",
        "refresh_inbounds",
        "debug_users",
    }
    if not INBOUNDS_MENU_ENABLED and data in inbounds_callbacks:
        await query.answer("Раздел Inbounds временно отключен.", show_alert=True)
        return MAIN_MENU

    return MAIN_MENU

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu with authorization check"""
    # Проверяем авторизацию
    if not check_authorization(update.effective_user):
        await update.callback_query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        return ConversationHandler.END
    
    # Показываем главное меню со статистикой
    await show_main_menu(update, context)
    return MAIN_MENU
