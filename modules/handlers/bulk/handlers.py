from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging

from modules.config import (
    MAIN_MENU,
    BULK_MENU,
    BULK_ACTION,
    BULK_CONFIRM,
    EXPIRATION_NOTIFICATION_ENABLED,
    EXPIRATION_NOTIFICATION_DAYS,
)
from modules.api.bulk import BulkAPI
from modules.api.users import UserAPI
from modules.utils.selection_helpers import SelectionHelper
from modules.handlers.core.start import show_main_menu
from modules.utils.auth import check_superadmin
from modules.services.expiration_notifier import (
    build_superadmin_notification,
)

logger = logging.getLogger(__name__)


@check_superadmin
async def show_bulk_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bulk operations menu"""
    keyboard = [
        [
            InlineKeyboardButton(
                "🔄 Сбросить трафик всем",
                callback_data="bulk_reset_all_traffic",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Удалить неактивных",
                callback_data="bulk_delete_inactive",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Удалить истекших",
                callback_data="bulk_delete_expired",
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Массовое обновление",
                callback_data="bulk_update_all",
            )
        ],
        *(
            [
                [
                    InlineKeyboardButton(
                        "🔔 Напомнить об истечениях",
                        callback_data="bulk_notify_expiring",
                    )
                ]
            ]
            if EXPIRATION_NOTIFICATION_ENABLED
            else []
        ),
        [
            InlineKeyboardButton(
                "🔙 Назад в главное меню",
                callback_data="back_to_main",
            )
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = "🔄 *Массовые операции*\n\n"
    message += "⚠️ Внимание! Эти операции затрагивают множество пользователей одновременно.\n\n"
    message += "Выберите действие:"

    await update.callback_query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return BULK_MENU


@check_superadmin
async def handle_bulk_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bulk operations menu selection"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "bulk_reset_all_traffic":
        # Confirm reset all traffic
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Да, сбросить всем",
                    callback_data="confirm_reset_all_traffic",
                ),
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="back_to_bulk",
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚠️ Вы уверены, что хотите сбросить трафик ВСЕМ пользователям?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return BULK_CONFIRM

    elif data == "bulk_delete_inactive":
        # Confirm delete inactive
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Да, удалить неактивных",
                    callback_data="confirm_delete_inactive",
                ),
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="back_to_bulk",
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚠️ Вы уверены, что хотите удалить ВСЕХ неактивных пользователей?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return BULK_CONFIRM

    elif data == "bulk_delete_expired":
        # Confirm delete expired
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Да, удалить истекших",
                    callback_data="confirm_delete_expired",
                ),
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="back_to_bulk",
                ),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⚠️ Вы уверены, что хотите удалить ВСЕХ пользователей с истекшим сроком?",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return BULK_CONFIRM

    elif data == "bulk_update_all":
        # TODO: Implement bulk update all
        await query.edit_message_text(
            "🚧 Функция массового обновления находится в разработке.",
            parse_mode="Markdown"
        )
        return BULK_MENU

    elif data == "bulk_notify_expiring":
        await query.edit_message_text(
            "⏳ Собираю пользователей с истекающей подпиской...",
            parse_mode="Markdown",
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 Назад",
                    callback_data="back_to_bulk",
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = await build_superadmin_notification()

        if not message_text:
            message = (
                f"ℹ️ В ближайшие {EXPIRATION_NOTIFICATION_DAYS} дн. нет пользователей "
                "с истекающей подпиской."
            )
        else:
            message = message_text

        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return BULK_MENU

    elif data == "back_to_bulk":
        await show_bulk_menu(update, context)
        return BULK_MENU

    elif data == "back_to_main":
        await show_main_menu(update, context)
        return MAIN_MENU

    return BULK_MENU


@check_superadmin
async def handle_bulk_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bulk operation confirmation"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "confirm_reset_all_traffic":
        # Reset all users traffic
        result = await BulkAPI.bulk_reset_all_users_traffic()
        
        if result:
            message = "✅ Трафик успешно сброшен у всех пользователей."
        else:
            message = "❌ Ошибка при сбросе трафика."
        
        # Add back button
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 Назад",
                    callback_data="back_to_bulk",
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return BULK_MENU

    elif data == "confirm_delete_inactive":
        # Delete all inactive users
        result = await BulkAPI.bulk_delete_users_by_status("DISABLED")
        
        if result:
            message = f"✅ Успешно удалено {result.get('deletedCount', 0)} неактивных пользователей."
        else:
            message = "❌ Ошибка при удалении пользователей."
        
        # Add back button
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔙 Назад",
                    callback_data="back_to_bulk",
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return BULK_MENU

    elif data == "confirm_delete_expired":
        # Delete all expired users
        result = await BulkAPI.bulk_delete_users_by_status("EXPIRED")
        
        if result:
            message = f"✅ Успешно удалено {result.get('deletedCount', 0)} пользователей с истекшим сроком."
        else:
            message = "❌ Ошибка при удалении пользователей."
        
        # Add back button
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_bulk")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return BULK_MENU

    elif data == "back_to_bulk":
        await show_bulk_menu(update, context)
        return BULK_MENU

    return BULK_MENU
