from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler, filters,
    ConversationHandler
)
from telegram import Update
from telegram.ext import ContextTypes
import logging
import warnings

# Подавляем предупреждение PTBUserWarning о per_message=False с CallbackQueryHandler
warnings.filterwarnings("ignore", message=".*?per_message=False.*?CallbackQueryHandler", category=UserWarning)

from modules.config import (
    MAIN_MENU, USER_MENU, NODE_MENU, STATS_MENU, HOST_MENU, INBOUND_MENU, BULK_MENU,
    SELECTING_USER, WAITING_FOR_INPUT, CONFIRM_ACTION,
    EDIT_USER, EDIT_FIELD, EDIT_VALUE,
    CREATE_USER, CREATE_USER_FIELD, BULK_CONFIRM, 
    EDIT_NODE, EDIT_NODE_FIELD, EDIT_HOST, EDIT_HOST_FIELD, NODE_PORT,
    CREATE_NODE, NODE_NAME, NODE_ADDRESS, SELECT_INBOUNDS, CREATE_HOST, HOST_PROFILE, HOST_INBOUND, HOST_PARAMS,
    ADMIN_MENU_STATE, ADMIN_WAITING_INPUT
)
from modules.utils.auth import check_authorization

from modules.handlers.core.start import start, show_main_menu
from modules.handlers.core.menu import handle_menu_selection
from modules.handlers.users import (
    handle_users_menu, handle_user_selection, handle_user_action,
    handle_action_confirmation, handle_text_input,
    handle_edit_field_selection, handle_edit_field_value,
    handle_create_user_input, handle_cancel_user_creation
)
from modules.handlers.nodes import (
    handle_nodes_menu, handle_node_edit_menu, handle_node_field_input, handle_cancel_node_edit,
    handle_node_creation, show_node_certificate
)
from modules.handlers.stats import handle_stats_menu
from modules.handlers.hosts import (
    handle_hosts_menu, handle_host_edit_menu, handle_host_field_input, handle_cancel_host_edit,
    handle_host_creation_text
)
from modules.handlers.inbounds import handle_inbounds_menu
from modules.handlers.bulk import handle_bulk_menu, handle_bulk_confirm
from modules.handlers.admins import show_admins_menu, handle_admins_menu, handle_admin_input
from modules.handlers.core.language import LANGUAGE_MENU_CALLBACK, LANGUAGE_SELECT_PREFIX

logger = logging.getLogger(__name__)

async def unauthorized_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unauthorized access attempts"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    
    # Проверяем авторизацию
    if not check_authorization(update.effective_user):
        logger.warning(f"Unauthorized access attempt from user {user_id} (@{username})")
        
        if update.message:
            await update.message.reply_text("⛔ Вы не авторизованы для использования этого бота.")
        elif update.callback_query:
            await update.callback_query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        
        return ConversationHandler.END
    
    # Если пользователь авторизован, но попал в fallback, перенаправляем на главное меню
    return await start(update, context)


async def handle_callback_reentry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow stale inline buttons to re-enter the conversation after bot restart."""
    query = update.callback_query
    if not query:
        return ConversationHandler.END

    if not check_authorization(update.effective_user):
        await query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        return ConversationHandler.END

    data = query.data or ""

    main_menu_callbacks = {
        "users",
        "menu_users",
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
        "inbounds",
        "menu_inbounds",
        "create_user",
        "menu_create_user",
        "notify_expiring_self",
        "export_subscriptions",
        "confirm_export_subscriptions",
        "cancel_export_subscriptions",
        "back_to_main",
        LANGUAGE_MENU_CALLBACK,
    }
    user_menu_callbacks = {
        "list_users",
        "list_expired_users",
        "search_user",
        "export_superadmin_excel",
        "export_dealers_excel",
        "back_to_users",
    }
    user_selection_callbacks = {
        "back",
        "page_info",
        "prev_page",
        "next_page",
        "back_to_list",
    }
    user_action_prefixes = (
        "user_action_",
        "edit_",
        "disable_",
        "enable_",
        "reset_",
        "revoke_",
        "delete_",
        "hwid_",
        "stats_",
        "confirm_del_hwid_",
    )
    user_selection_prefixes = (
        "select_user_",
        "users_page_",
        "view_",
        "add_hwid_",
        "show_qr_",
        "refresh_qr_",
        "show_sub_",
        "view_hwid_",
        "delete_hwid_",
        "confirm_delete_hwid_",
        "cancel_delete_hwid_",
        "back_to_hwid_",
        "show_traffic_history_",
        "show_online_stats_",
        "show_links_",
        "show_user_",
    )
    node_callbacks = {
        "list_nodes",
        "add_node",
        "get_panel_certificate",
        "restart_all_nodes",
        "confirm_restart_all",
        "nodes_usage",
        "back_to_nodes",
    }
    node_prefixes = (
        "view_node_",
        "select_node_",
        "page_nodes_",
        "enable_node_",
        "disable_node_",
        "restart_node_",
        "node_stats_",
        "edit_node_",
    )
    stats_callbacks = {
        "system_stats",
        "bandwidth_stats",
        "nodes_stats",
        "back_to_stats",
    }
    host_callbacks = {
        "list_hosts",
        "create_host",
        "back_to_hosts",
    }
    host_prefixes = (
        "view_host_",
        "enable_host_",
        "disable_host_",
        "edit_host_",
        "delete_host_",
        "confirm_delete_host_",
    )
    inbound_callbacks = {
        "list_inbounds",
        "list_full_inbounds",
        "list_inbounds_stats",
        "filter_inbounds",
        "refresh_inbounds",
        "debug_users",
        "back_to_inbounds",
    }
    inbound_prefixes = (
        "view_inbound_",
        "select_inbound_",
        "select_full_inbound_",
        "inbound_action_",
        "page_inbounds_",
        "page_full_inbounds_",
    )
    bulk_callbacks = {
        "bulk_reset_all_traffic",
        "bulk_delete_inactive",
        "bulk_delete_expired",
        "bulk_update_all",
        "bulk_notify_expiring",
        "back_to_bulk",
    }
    bulk_confirm_callbacks = {
        "confirm_reset_all_traffic",
        "confirm_delete_inactive",
        "confirm_delete_expired",
    }

    if data.startswith("expire_extend_") or data.startswith(LANGUAGE_SELECT_PREFIX) or data in main_menu_callbacks:
        return await handle_menu_selection(update, context)

    if data in user_menu_callbacks:
        return await handle_users_menu(update, context)

    if data.startswith(user_action_prefixes):
        return await handle_user_action(update, context)

    if data in user_selection_callbacks or data.startswith(user_selection_prefixes):
        return await handle_user_selection(update, context)

    if data in node_callbacks or data.startswith(node_prefixes):
        return await handle_nodes_menu(update, context)

    if data in stats_callbacks:
        return await handle_stats_menu(update, context)

    if data in host_callbacks or data.startswith(host_prefixes):
        return await handle_hosts_menu(update, context)

    if data in inbound_callbacks or data.startswith(inbound_prefixes):
        return await handle_inbounds_menu(update, context)

    if data in bulk_callbacks:
        return await handle_bulk_menu(update, context)

    if data in bulk_confirm_callbacks:
        return await handle_bulk_confirm(update, context)

    if data.startswith("admin_"):
        return await handle_admins_menu(update, context)

    logger.info("Unhandled stale callback after restart: %s", data)
    return await show_main_menu(update, context)

def create_conversation_handler():
    """Create the main conversation handler"""
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(handle_callback_reentry),
        ],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(handle_menu_selection, pattern="^expire_extend_"),
                CallbackQueryHandler(handle_menu_selection),
                CallbackQueryHandler(handle_cancel_user_creation, pattern="^cancel_create$")
            ],
            USER_MENU: [
                CallbackQueryHandler(handle_users_menu)
            ],
            NODE_MENU: [
                CallbackQueryHandler(show_node_certificate, pattern="^show_certificate_"),
                CallbackQueryHandler(show_node_certificate, pattern="^get_panel_certificate$"),
                CallbackQueryHandler(handle_nodes_menu)
            ],
            STATS_MENU: [
                CallbackQueryHandler(handle_stats_menu)
            ],
            HOST_MENU: [
                CallbackQueryHandler(handle_hosts_menu)
            ],
            HOST_PROFILE: [
                CallbackQueryHandler(handle_hosts_menu)
            ],
            HOST_INBOUND: [
                CallbackQueryHandler(handle_hosts_menu)
            ],
            HOST_PARAMS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_host_creation_text),
                CallbackQueryHandler(handle_hosts_menu)
            ],
            ADMIN_MENU_STATE: [
                CallbackQueryHandler(handle_admins_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input),
            ],
            ADMIN_WAITING_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input),
                CallbackQueryHandler(handle_admins_menu),
            ],
            INBOUND_MENU: [
                CallbackQueryHandler(handle_inbounds_menu)
            ],
            BULK_MENU: [
                CallbackQueryHandler(handle_bulk_menu)
            ],
            SELECTING_USER: [
                # Handle both new and legacy user action patterns
                CallbackQueryHandler(handle_user_action, pattern="^user_action_"),
                CallbackQueryHandler(handle_user_action, pattern="^(edit_|disable_|enable_|reset_|revoke_|delete_|hwid_|stats_|confirm_del_hwid_)"),
                CallbackQueryHandler(handle_user_selection)
            ],
            WAITING_FOR_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input),
                CallbackQueryHandler(handle_users_menu)
            ],
            CONFIRM_ACTION: [
                CallbackQueryHandler(handle_action_confirmation)
            ],
            EDIT_USER: [
                CallbackQueryHandler(handle_edit_field_selection)
            ],
            EDIT_FIELD: [
                CallbackQueryHandler(handle_edit_field_selection)
            ],
            EDIT_VALUE: [
                CallbackQueryHandler(handle_edit_field_value),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_field_value)
            ],
            CREATE_USER: [
                CallbackQueryHandler(handle_cancel_user_creation, pattern="^cancel_create$"),
                CallbackQueryHandler(handle_create_user_input)
            ],
            CREATE_USER_FIELD: [
                CallbackQueryHandler(handle_cancel_user_creation, pattern="^cancel_create$"),
                CallbackQueryHandler(handle_create_user_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_create_user_input)
            ],
            BULK_CONFIRM: [
                CallbackQueryHandler(handle_bulk_confirm)
            ],
            EDIT_NODE: [
                CallbackQueryHandler(handle_node_edit_menu),
                CallbackQueryHandler(handle_cancel_node_edit, pattern="^cancel_edit_node_")
            ],
            EDIT_NODE_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_node_field_input),
                CallbackQueryHandler(handle_cancel_node_edit, pattern="^cancel_edit_node_")
            ],
            EDIT_HOST: [
                CallbackQueryHandler(handle_host_edit_menu),
                CallbackQueryHandler(handle_cancel_host_edit, pattern="^ceh_")
            ],
            EDIT_HOST_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_host_field_input),
                CallbackQueryHandler(handle_cancel_host_edit, pattern="^ceh_")
            ],
            CREATE_NODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_node_creation),
                CallbackQueryHandler(handle_node_creation, pattern="^cancel_create_node$"),
            ],
            NODE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_node_creation),
                CallbackQueryHandler(handle_node_creation, pattern="^cancel_create_node$"),
            ],
            NODE_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_node_creation),
                CallbackQueryHandler(handle_node_creation, pattern="^cancel_create_node$"),
            ],
            NODE_PORT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_node_creation),
                CallbackQueryHandler(handle_node_creation, pattern="^(cancel_create_node|use_port_3000)$"),
            ],
            SELECT_INBOUNDS: [
                CallbackQueryHandler(show_node_certificate, pattern="^show_certificate_"),
                CallbackQueryHandler(show_node_certificate, pattern="^get_panel_certificate$"),
                CallbackQueryHandler(handle_node_creation, pattern="^(select_inbound_|remove_inbound_|finish_node_creation|cancel_create_node)"),
            ],
        },
        fallbacks=[
            CommandHandler("start", unauthorized_handler),
            MessageHandler(filters.TEXT, unauthorized_handler),
            CallbackQueryHandler(unauthorized_handler)
        ],
        name="remnawave_admin_conversation",
        persistent=False,
        per_chat=True,
        per_user=True,
        per_message=False
    )
