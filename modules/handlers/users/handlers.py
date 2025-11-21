from datetime import datetime, timedelta
import logging
import random
import string
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
import re
import asyncio
import qrcode
import httpx

from modules.config import (
    MAIN_MENU,
    USER_MENU,
    SELECTING_USER,
    WAITING_FOR_INPUT,
    CONFIRM_ACTION,
    EDIT_USER,
    EDIT_FIELD,
    EDIT_VALUE,
    CREATE_USER,
    CREATE_USER_FIELD,
    USER_FIELDS,
    ACTIVE_INTERNAL_SQUADS,
    CREATE_USER_EXCLUDED_FIELDS_SET,
    SUBSCRIPTION_DRIVE_LINK,
    SUBSCRIPTION_SCRIPT_URL,
)

# Константы для callback_data
class CallbackData:
    # Основные действия
    LIST_USERS = "list_users"
    LIST_EXPIRED_USERS = "list_expired_users"
    SEARCH_USER = "search_user"
    CREATE_USER = "create_user"
    BACK_TO_MAIN = "back_to_main"
    BACK_TO_USERS = "back_to_users"
    BACK_TO_LIST = "back_to_list"
    
    # Действия с пользователями
    VIEW_USER = "view_"
    EDIT_USER = "edit_"
    DISABLE_USER = "disable_"
    ENABLE_USER = "enable_"
    RESET_TRAFFIC = "reset_"
    REVOKE_SUBSCRIPTION = "revoke_"
    DELETE_USER = "delete_"
    USER_STATS = "stats_"
    USER_HWID = "hwid_"
    
    # Подтверждения
    CONFIRM_ACTION = "confirm_action"
    FINAL_DELETE_USER = "final_delete_user"
    
    # Создание пользователей
    TEMPLATE = "template_"
    CREATE_MANUAL = "create_manual"
    CANCEL_CREATE = "cancel_create"
    USE_TEMPLATE = "use_template_"
    CUSTOMIZE_TEMPLATE = "customize_template_"
    FINISH_TEMPLATE_USER = "finish_template_user"
    ADD_OPTIONAL_FIELDS = "add_optional_fields"
    USE_TEMPLATE_VALUE = "use_template_value_"
    SKIP_FIELD = "skip_field"
    
    # Поля создания
    CREATE_FIELD = "create_field_"
    CREATE_DATE = "create_date_"
    CREATE_TRAFFIC = "create_traffic_"
    CREATE_DESC = "create_desc_"
    CREATE_DEVICE = "create_device_"
    
    # Редактирование полей
    EDIT_FIELD = "edit_field_"
    
    # HWID устройства
    ADD_HWID = "add_hwid_"
    DEL_HWID = "del_hwid_"
    CONFIRM_DEL_HWID = "confirm_del_hwid_"
    
    # Пагинация
    PREV_PAGE = "prev_page"
    NEXT_PAGE = "next_page"
    PAGE_INFO = "page_info"
    USERS_PAGE = "users_page_"
    
    # SelectionHelper
    SELECT_USER = "select_user_"
    USER_ACTION = "user_action_"
    BACK = "back"

# Константы для сообщений
class Messages:
    # Ошибки авторизации
    NOT_AUTHORIZED = "⛔ Вы не авторизованы для использования этого бота."
    
    # Общие ошибки
    USER_NOT_FOUND = "❌ Пользователь не найден или ошибка при получении данных."
    ERROR_LOADING = "❌ Ошибка при загрузке данных."
    INVALID_INPUT = "❌ Неверный формат ввода."
    OPERATION_FAILED = "❌ Не удалось выполнить операцию."
    
    # Успешные операции
    USER_CREATED = "✅ Пользователь успешно создан!"
    USER_UPDATED = "✅ Пользователь успешно обновлен!"
    USER_DELETED = "✅ Пользователь успешно удален!"
    FIELD_UPDATED = "✅ Поле успешно обновлено."
    
    # Предупреждения
    CONFIRM_DELETE = "⚠️ Вы уверены, что хотите удалить пользователя?"
    CONFIRM_DISABLE = "⚠️ Вы уверены, что хотите отключить пользователя?"
    CONFIRM_ENABLE = "⚠️ Вы уверены, что хотите включить пользователя?"
    CONFIRM_RESET = "⚠️ Вы уверены, что хотите сбросить трафик пользователя?"
    CONFIRM_REVOKE = "⚠️ Вы уверены, что хотите отозвать подписку пользователя?"
from modules.api.users import UserAPI
from modules.utils.formatters import (
    format_bytes,
    format_user_details,
    format_user_details_safe,
    escape_markdown,
    safe_edit_message,
    resolve_description_link,
    parse_description_links,
)
from modules.utils.selection_helpers import SelectionHelper
from modules.utils.google_drive import store_subscription_links, delete_drive_file
from modules.utils.auth import (
    check_admin,
    check_authorization,
    get_user_role,
    is_admin_user,
    is_super_admin_user,
    INSUFFICIENT_PERMISSIONS_MESSAGE
)
from modules.handlers.core.start import show_main_menu

logger = logging.getLogger(__name__)

TEMPLATES_ENABLED = False

GB = 1024 * 1024 * 1024
DEFAULT_NON_SUPERADMIN_LIMIT_GB = 200


def _filter_create_fields(fields: List[str], ensure_username: bool = True) -> List[str]:
    """Filter out excluded fields while keeping username available."""
    filtered: List[str] = []
    for field in fields:
        if field != "username" and field in CREATE_USER_EXCLUDED_FIELDS_SET:
            continue
        filtered.append(field)
    if ensure_username and "username" not in filtered and "username" in USER_FIELDS:
        filtered.insert(0, "username")
    return filtered


def _default_create_field_order() -> List[str]:
    """Return a fresh list of fields respecting exclusion rules."""
    return _filter_create_fields(list(USER_FIELDS.keys()))


def _get_create_user_fields(context: ContextTypes.DEFAULT_TYPE) -> List[str]:
    fields = context.user_data.get("create_user_fields")
    if not fields:
        fields = _default_create_field_order()
        context.user_data["create_user_fields"] = fields
    return fields


def _get_current_field_index(context: ContextTypes.DEFAULT_TYPE) -> int:
    index = context.user_data.get("current_field_index")
    if index is None or index < 0:
        index = 0
        context.user_data["current_field_index"] = index
    return index


def _advance_field_index(context: ContextTypes.DEFAULT_TYPE, step: int = 1) -> int:
    index = max(0, _get_current_field_index(context) + step)
    context.user_data["current_field_index"] = index
    return index


def _extract_drive_file_id(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    links = parse_description_links(description)
    text = links.get("drive") or str(description)
    text = text.strip().strip("`")
    if not text:
        return None
    try:
        parsed = urlparse(text)
        if parsed.query:
            params = parse_qs(parsed.query)
            file_ids = params.get("id")
            if file_ids:
                return file_ids[0]
        drive_match = re.search(r"/d/([a-zA-Z0-9_-]+)", text)
        if drive_match:
            return drive_match.group(1)
    except Exception as exc:
        logger.debug("Failed to parse Drive link '%s': %s", description, exc)
    return None


def _build_qr_code_payload(data: str) -> BytesIO:
    qr = qrcode.QRCode(version=1, box_size=6, border=3)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "user_qrcode.png"
    return buffer


async def _fetch_encrypted_subscription_link(short_uuid: Optional[str]) -> Optional[str]:
    if not short_uuid:
        return None
    macro_url = SUBSCRIPTION_SCRIPT_URL.format(shortUuid=short_uuid, userShortUuid=short_uuid)
    payload = {"url": macro_url}
    headers = {"Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post("https://crypto.happ.su/api.php", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            encrypted_link = data.get("encrypted_link")
            if encrypted_link:
                return str(encrypted_link)
    except Exception as exc:
        logger.error("Failed to fetch encrypted subscription link for %s: %s", short_uuid, exc)
    return None


# Декоратор для проверки авторизации
def require_authorization(func):
    """Декоратор для проверки авторизации пользователя"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not check_authorization(update.effective_user):
            if update.callback_query:
                await update.callback_query.answer(Messages.NOT_AUTHORIZED, show_alert=True)
            else:
                await update.message.reply_text(Messages.NOT_AUTHORIZED)
            return ConversationHandler.END

        user_id = update.effective_user.id
        context.user_data['role'] = get_user_role(user_id)
        context.user_data['is_admin'] = is_admin_user(user_id)
        context.user_data['is_superadmin'] = is_super_admin_user(user_id)

        return await func(update, context, *args, **kwargs)
    return wrapper

# Декоратор для логирования действий пользователей
def log_user_action(action: str):
    """Декоратор для логирования действий пользователей"""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id if update.effective_user else "unknown"
            username = update.effective_user.username if update.effective_user else "unknown"
            
            logger.info(f"User action: {action} by user {username} (ID: {user_id})")
            
            try:
                result = await func(update, context, *args, **kwargs)
                logger.info(f"Action {action} completed successfully for user {username}")
                return result
            except Exception as e:
                logger.error(f"Action {action} failed for user {username}: {str(e)}")
                raise
        return wrapper
    return decorator

# Helpers for role/status checks used across handlers
def _is_superadmin_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    value = context.user_data.get('is_superadmin')
    if value is None and update.effective_user:
        value = is_super_admin_user(update.effective_user.id)
        context.user_data['is_superadmin'] = value
    return bool(value)

def _is_admin_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    value = context.user_data.get('is_admin')
    if value is None and update.effective_user:
        value = is_admin_user(update.effective_user.id)
        context.user_data['is_admin'] = value
    return bool(value)

def _is_user_active(user: Optional[Dict[str, Any]]) -> bool:
    status = str((user or {}).get('status') or '').upper()
    return status == "ACTIVE"

# Обработка ошибок
class ErrorHandler:
    """Класс для обработки ошибок"""
    
    @staticmethod
    async def handle_api_error(update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception, operation: str = "операция") -> bool:
        """Обрабатывает ошибки API"""
        logger.error(f"API error during {operation}: {str(error)}")
        
        error_message = f"❌ Ошибка при выполнении {operation}.\n\n"
        
        if "connection" in str(error).lower():
            error_message += "🔌 Проблема с подключением к серверу. Попробуйте позже."
        elif "timeout" in str(error).lower():
            error_message += "⏰ Превышено время ожидания. Попробуйте позже."
        elif "unauthorized" in str(error).lower() or "forbidden" in str(error).lower():
            error_message += "🔒 Недостаточно прав для выполнения операции."
        elif "not found" in str(error).lower():
            error_message += "🔍 Запрашиваемые данные не найдены."
        else:
            error_message += "⚠️ Внутренняя ошибка сервера. Обратитесь к администратору."
        
        keyboard = KeyboardBuilder.create_back_button()
        
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=error_message,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Error editing message: {edit_error}")
                await update.callback_query.answer("❌ Ошибка при обновлении сообщения")
        else:
            await update.message.reply_text(
                text=error_message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        
        return True
    
    @staticmethod
    async def handle_validation_error(update: Update, context: ContextTypes.DEFAULT_TYPE, error_message: str, back_callback: str = CallbackData.BACK_TO_USERS) -> bool:
        """Обрабатывает ошибки валидации"""
        keyboard = KeyboardBuilder.create_back_button(back_callback)
        
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=f"❌ {error_message}",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Error editing message: {edit_error}")
                await update.callback_query.answer("❌ Ошибка валидации")
        else:
            await update.message.reply_text(
                text=f"❌ {error_message}",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        
        return True
    
    @staticmethod
    async def handle_unexpected_error(update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception, operation: str = "операция") -> bool:
        """Обрабатывает неожиданные ошибки"""
        logger.error(f"Unexpected error during {operation}: {str(error)}", exc_info=True)
        
        error_message = f"❌ Произошла неожиданная ошибка при выполнении {operation}.\n\n"
        error_message += "🛠️ Обратитесь к администратору системы."
        
        keyboard = KeyboardBuilder.create_back_button()
        
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text=error_message,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            except Exception as edit_error:
                logger.error(f"Error editing message: {edit_error}")
                await update.callback_query.answer("❌ Критическая ошибка")
        else:
            await update.message.reply_text(
                text=error_message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        
        return True

# Кэширование данных пользователей
class UserCache:
    """Класс для кэширования данных пользователей"""
    
    def __init__(self, cache_ttl: int = 300):  # 5 минут по умолчанию
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = cache_ttl
    
    def _is_expired(self, timestamp: float) -> bool:
        """Проверяет, истек ли срок кэша"""
        return datetime.now().timestamp() - timestamp > self._cache_ttl
    
    async def get_user(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Получает пользователя из кэша или API"""
        cache_key = f"user_{uuid}"
        
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if not self._is_expired(cached_data['timestamp']):
                logger.debug(f"User {uuid} found in cache")
                return cached_data['data']
            else:
                # Удаляем устаревшие данные
                del self._cache[cache_key]
        
        # Периодически очищаем кэш (каждый 10-й запрос)
        if len(self._cache) % 10 == 0:
            self.cleanup_expired()
        
        # Получаем данные из API
        try:
            user_data = await UserAPI.get_user_by_uuid(uuid)
            if user_data:
                self._cache[cache_key] = {
                    'data': user_data,
                    'timestamp': datetime.now().timestamp()
                }
                logger.debug(f"User {uuid} cached")
            return user_data
        except Exception as e:
            logger.error(f"Error fetching user {uuid}: {e}")
            return None
    
    async def get_all_users(self) -> Optional[list]:
        """Получает всех пользователей из кэша или API"""
        cache_key = "all_users"
        
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if not self._is_expired(cached_data['timestamp']):
                logger.debug("All users found in cache")
                return cached_data['data']
            else:
                del self._cache[cache_key]
        
        # Получаем данные из API
        try:
            response = await UserAPI.get_all_users()
            users = []
            
            if isinstance(response, dict):
                if 'users' in response:
                    users = response['users'] or []
                elif 'response' in response and isinstance(response['response'], dict) and 'users' in response['response']:
                    users = response['response']['users'] or []
            elif isinstance(response, list):
                users = response
            
            if users:
                self._cache[cache_key] = {
                    'data': users,
                    'timestamp': datetime.now().timestamp()
                }
                logger.debug(f"Cached {len(users)} users")
            
            return users
        except Exception as e:
            logger.error(f"Error fetching all users: {e}")
            return None
    
    def invalidate_user(self, uuid: str):
        """Инвалидирует кэш конкретного пользователя"""
        cache_key = f"user_{uuid}"
        if cache_key in self._cache:
            del self._cache[cache_key]
            logger.debug(f"Cache invalidated for user {uuid}")
    
    def invalidate_all_users(self):
        """Инвалидирует кэш всех пользователей"""
        self._cache.clear()
        logger.debug("All users cache invalidated")
    
    def cleanup_expired(self):
        """Очищает устаревшие записи из кэша"""
        current_time = datetime.now().timestamp()
        expired_keys = [
            key for key, data in self._cache.items()
            if current_time - data['timestamp'] > self._cache_ttl
        ]
        
        for key in expired_keys:
            del self._cache[key]
        
        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")

# Глобальный экземпляр кэша
user_cache = UserCache()

# Функция для ручной очистки кэша
def cleanup_cache():
    """Очищает устаревшие записи из кэша"""
    try:
        user_cache.cleanup_expired()
        logger.debug("Cache cleanup completed")
    except Exception as e:
        logger.error(f"Error during cache cleanup: {e}")

# Утилиты для работы с клавиатурами
class KeyboardBuilder:
    """Класс для создания клавиатур"""
    
    @staticmethod
    def create_main_menu(is_admin: bool):
        """Создает главное меню пользователей"""
        rows = [
            [InlineKeyboardButton("📋 Список всех пользователей", callback_data=CallbackData.LIST_USERS)],
            [InlineKeyboardButton("⌛ Просроченные пользователи", callback_data=CallbackData.LIST_EXPIRED_USERS)],
            [InlineKeyboardButton("🔍 Поиск пользователя", callback_data=CallbackData.SEARCH_USER)]
        ]
        if is_admin:
            rows.append([InlineKeyboardButton("➕ Создать пользователя", callback_data=CallbackData.CREATE_USER)])
        rows.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data=CallbackData.BACK_TO_MAIN)])
        return InlineKeyboardMarkup(rows)
    
    @staticmethod
    def create_back_button(callback_data: str = CallbackData.BACK_TO_USERS):
        """Создает кнопку 'Назад'"""
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=callback_data)]])
    
    @staticmethod
    def create_confirmation_buttons(confirm_callback: str, cancel_callback: str, confirm_text: str = "✅ Да", cancel_text: str = "❌ Отмена"):
        """Создает кнопки подтверждения"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(confirm_text, callback_data=confirm_callback)],
            [InlineKeyboardButton(cancel_text, callback_data=cancel_callback)]
        ])
    
    @staticmethod
    def create_user_actions_keyboard(uuid: str, user_status: str = "ACTIVE"):
        """Создает клавиатуру действий с пользователем"""
        keyboard = [
            [InlineKeyboardButton("📝 Редактировать", callback_data=f"{CallbackData.EDIT_USER}{uuid}")],
            [InlineKeyboardButton("🔄 Сбросить трафик", callback_data=f"{CallbackData.RESET_TRAFFIC}{uuid}")],
            [InlineKeyboardButton("📊 Статистика", callback_data=f"{CallbackData.USER_STATS}{uuid}")],
            [InlineKeyboardButton("📱 Устройства HWID", callback_data=f"{CallbackData.USER_HWID}{uuid}")]
        ]
        if user_status == "ACTIVE":
            keyboard.append([InlineKeyboardButton("🔴 Отключить", callback_data=f"{CallbackData.DISABLE_USER}{uuid}")])
        else:
            keyboard.append([InlineKeyboardButton("🟢 Включить", callback_data=f"{CallbackData.ENABLE_USER}{uuid}")])
        keyboard.append([InlineKeyboardButton("🔄 Отозвать подписку", callback_data=f"{CallbackData.REVOKE_SUBSCRIPTION}{uuid}")])
        keyboard.append([InlineKeyboardButton("🗑️ Удалить", callback_data=f"{CallbackData.DELETE_USER}{uuid}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад к списку", callback_data=CallbackData.BACK_TO_LIST)])
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def create_pagination_buttons(current_page: int, total_pages: int, callback_prefix: str = "page"):
        """Создает кнопки пагинации"""
        keyboard = []
        
        if current_page > 0:
            keyboard.append(InlineKeyboardButton("◀️ Назад", callback_data=f"{callback_prefix}_{current_page - 1}"))
        
        if current_page < total_pages - 1:
            keyboard.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"{callback_prefix}_{current_page + 1}"))
        
        return keyboard

# Дополнительные утилиты
class UserUtils:
    """Утилиты для работы с пользователями"""
    
    @staticmethod
    def format_user_status(status: str) -> str:
        """Форматирует статус пользователя"""
        status_map = {
            "ACTIVE": "✅ Активен",
            "INACTIVE": "❌ Неактивен",
            "EXPIRED": "⏰ Истек",
            "SUSPENDED": "🚫 Заблокирован"
        }
        return status_map.get(status, f"❓ {status}")
    
    @staticmethod
    def format_traffic_usage(used: int, limit: int) -> str:
        """Форматирует использование трафика"""
        if limit == 0:
            return f"📊 {format_bytes(used)} / Безлимитный"
        
        percent = (used / limit) * 100
        status_emoji = "🟢" if percent < 50 else "🟡" if percent < 90 else "🔴"
        
        return f"📊 {format_bytes(used)} / {format_bytes(limit)} ({percent:.1f}%) {status_emoji}"
    
    @staticmethod
    def format_expiration_date(expire_at: str) -> str:
        """Форматирует дату истечения"""
        try:
            expire_date = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
            days_left = (expire_date - datetime.now().astimezone()).days
            
            if days_left < 0:
                return f"⏰ Истек {abs(days_left)} дней назад"
            elif days_left == 0:
                return "⏰ Истекает сегодня"
            elif days_left <= 7:
                return f"⚠️ Истекает через {days_left} дней"
            else:
                return f"📅 Истекает через {days_left} дней"
        except Exception:
            return f"📅 {expire_at[:10]}"
    
    @staticmethod
    def get_user_summary(user: Dict[str, Any]) -> str:
        """Создает краткое описание пользователя"""
        lines = [
            f"👤 *{escape_markdown(user.get('username', 'Без имени'))}*",
            f"🆔 `{user.get('uuid', 'N/A')}`",
            f"📊 {UserUtils.format_traffic_usage(user.get('usedTrafficBytes', 0), user.get('trafficLimitBytes', 0))}",
            f"📅 {UserUtils.format_expiration_date(user.get('expireAt', ''))}",
            f"📱 {UserUtils.format_user_status(user.get('status', 'UNKNOWN'))}"
        ]
        
        if user.get('email'):
            lines.append(f"📧 {escape_markdown(user['email'])}")
        
        if user.get('tag'):
            lines.append(f"🏷️ {escape_markdown(user['tag'])}")
        
        return "\n".join(lines)

# Массовые операции
class BulkOperations:
    """Класс для массовых операций с пользователями"""
    
    @staticmethod
    async def bulk_disable_users(uuids: list[str]) -> Dict[str, bool]:
        """Массовое отключение пользователей"""
        results = {}
        
        for uuid in uuids:
            try:
                result = await UserAPI.disable_user(uuid)
                results[uuid] = result
                if result:
                    user_cache.invalidate_user(uuid)
            except Exception as e:
                logger.error(f"Error disabling user {uuid}: {e}")
                results[uuid] = False
        
        return results
    
    @staticmethod
    async def bulk_enable_users(uuids: list[str]) -> Dict[str, bool]:
        """Массовое включение пользователей"""
        results = {}
        
        for uuid in uuids:
            try:
                result = await UserAPI.enable_user(uuid)
                results[uuid] = result
                if result:
                    user_cache.invalidate_user(uuid)
            except Exception as e:
                logger.error(f"Error enabling user {uuid}: {e}")
                results[uuid] = False
        
        return results
    
    @staticmethod
    async def bulk_reset_traffic(uuids: list[str]) -> Dict[str, bool]:
        """Массовый сброс трафика"""
        results = {}
        
        for uuid in uuids:
            try:
                result = await UserAPI.reset_user_traffic(uuid)
                results[uuid] = result
                if result:
                    user_cache.invalidate_user(uuid)
            except Exception as e:
                logger.error(f"Error resetting traffic for user {uuid}: {e}")
                results[uuid] = False
        
        return results
    
    @staticmethod
    def format_bulk_results(results: Dict[str, bool], operation: str) -> str:
        """Форматирует результаты массовых операций"""
        successful = sum(1 for success in results.values() if success)
        total = len(results)
        
        message = f"📊 *Результаты массовой операции: {operation}*\n\n"
        message += f"✅ Успешно: {successful}/{total}\n"
        message += f"❌ Ошибок: {total - successful}/{total}\n\n"
        
        if successful < total:
            failed_uuids = [uuid for uuid, success in results.items() if not success]
            message += f"❌ Неудачные UUID: `{', '.join(failed_uuids[:5])}`"
            if len(failed_uuids) > 5:
                message += f" и еще {len(failed_uuids) - 5}..."
        
        return message

# Валидаторы данных
class DataValidators:
    """Класс для валидации данных"""
    
    @staticmethod
    def validate_username(username: str) -> tuple[bool, str]:
        """Валидация имени пользователя"""
        if not username:
            return False, "Имя пользователя не может быть пустым"
        
        if not re.match(r"^[a-zA-Z0-9_-]{6,34}$", username):
            return False, "Имя пользователя должно содержать только буквы, цифры, подчеркивания и дефисы. Длина от 6 до 34 символов"
        
        return True, ""
    
    @staticmethod
    def validate_email(email: str) -> tuple[bool, str]:
        """Валидация email"""
        if not email:
            return True, ""  # Email не обязателен
        
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            return False, "Неверный формат email"
        
        return True, ""
    
    @staticmethod
    def validate_telegram_id(telegram_id: str) -> tuple[bool, str, int]:
        """Валидация Telegram ID"""
        if not telegram_id:
            return True, "", 0  # Telegram ID не обязателен
        
        try:
            tid = int(telegram_id)
            if tid <= 0:
                return False, "Telegram ID должен быть положительным числом", 0
            return True, "", tid
        except ValueError:
            return False, "Telegram ID должен быть целым числом", 0
    
    @staticmethod
    def validate_date(date_str: str) -> tuple[bool, str, str]:
        """Валидация даты"""
        if not date_str:
            return True, "", ""  # Дата не обязательна
        
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            formatted_date = date_obj.strftime("%Y-%m-%dT00:00:00.000Z")
            return True, "", formatted_date
        except ValueError:
            return False, "Неверный формат даты. Используйте YYYY-MM-DD", ""
    
    @staticmethod
    def validate_traffic_limit(traffic_str: str) -> tuple[bool, str, int]:
        """Валидация лимита трафика"""
        if not traffic_str:
            return True, "", 0  # Лимит не обязателен
        
        try:
            traffic = int(traffic_str)
            if traffic < 0:
                return False, "Лимит трафика не может быть отрицательным", 0
            return True, "", traffic
        except ValueError:
            return False, "Лимит трафика должен быть целым числом", 0
    
    @staticmethod
    def validate_device_limit(device_str: str) -> tuple[bool, str, int]:
        """Валидация лимита устройств"""
        if not device_str:
            return True, "", 0  # Лимит не обязателен
        
        try:
            devices = int(device_str)
            if devices < 0:
                return False, "Лимит устройств не может быть отрицательным", 0
            return True, "", devices
        except ValueError:
            return False, "Лимит устройств должен быть целым числом", 0

@require_authorization
@log_user_action("show_users_menu")
async def show_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show users menu"""
    has_user_access = (
        context.user_data.get('is_admin', False)
        or context.user_data.get('is_superadmin', False)
    )
    reply_markup = KeyboardBuilder.create_main_menu(has_user_access)

    message = (
        "👥 *Управление пользователями*\n\n"
        "🔍 *Поиск:* введите любую часть имени, Telegram ID, UUID, короткого UUID, email, тега или описания.\n\n"
        "Выберите действие:"
    )

    await safe_edit_message(
        update.callback_query,
        message,
        reply_markup,
        "Markdown"
    )

@require_authorization
@log_user_action("handle_users_menu")
async def handle_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle users menu selection"""
    query = update.callback_query
    await query.answer()

    data = query.data

    try:
        logger.debug(f"handle_user_selection received callback data: {data}")
    except Exception:
        pass

    if data == CallbackData.LIST_USERS:
        await list_users(update, context)
        return SELECTING_USER
    
    elif data == CallbackData.LIST_EXPIRED_USERS:
        await list_expired_users(update, context)
        return SELECTING_USER

    elif data == CallbackData.SEARCH_USER:
        back_markup = KeyboardBuilder.create_back_button()
        search_prompt = (
            "🔍 Введите текст для поиска пользователя:\n\n"
            "💡 *Пример:* имя, часть описания, email, тег, UUID или Telegram ID."
        )
        await safe_edit_message(
            query,
            search_prompt,
            back_markup,
            "Markdown"
        )
        context.user_data["search_type"] = "generic"
        return WAITING_FOR_INPUT
        
    elif data in (CallbackData.CREATE_USER, "menu_create_user"):
        await start_create_user(update, context)
        return CREATE_USER_FIELD

    elif data == CallbackData.BACK_TO_USERS:
        await show_users_menu(update, context)
        return USER_MENU

    elif data == CallbackData.BACK_TO_MAIN:
        await show_main_menu(update, context)
        return MAIN_MENU

    return USER_MENU

async def search_users_by_term(term: str):
    """Fetch users and filter by generic term"""
    try:
        users = await user_cache.get_all_users()
        if not users:
            return []
    except Exception as e:
        logger.error(f"Error fetching users for search: {e}")
        return []

    term_lower = term.lower()
    matches = []
    seen = set()

    for user in users:
        if not isinstance(user, dict):
            continue
        user_uuid = str(user.get('uuid') or '')
        if not user_uuid or user_uuid in seen:
            continue

        fields = [
            str(user.get('username') or ''),
            str(user.get('description') or ''),
            str(user.get('email') or ''),
            str(user.get('tag') or ''),
            str(user.get('shortUuid') or ''),
            user_uuid,
            str(user.get('telegramId') or '')
        ]

        if any(term_lower in field.lower() for field in fields if field):
            matches.append(user)
            seen.add(user_uuid)

    matches.sort(key=lambda u: (u.get('username') or '').lower())
    return matches


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all users with improved selection interface"""
    await update.callback_query.edit_message_text("📋 Загрузка списка пользователей...")

    try:
        # Use SelectionHelper for user-friendly interface
        keyboard, users_data = await SelectionHelper.get_users_selection_keyboard(
            callback_prefix="select_user",
            include_back=True,
            max_per_row=1,
            filter_tag_by_telegram_id=str(update.effective_user.id),
            is_superadmin=context.user_data.get('is_superadmin', False)
        )
        
        if not users_data:
            keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                "❌ Пользователи не найдены.",
                reply_markup=reply_markup
            )
            return USER_MENU

        # Store users data for later use
        context.user_data["users_data"] = users_data
        
        message = f"👥 *Список пользователей* ({len(users_data)} шт.)\n\n"
        message += "Выберите пользователя для просмотра подробной информации:"

        await update.callback_query.edit_message_text(
            text=message,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        return SELECTING_USER
        
    except Exception as e:
        logger.error(f"Error in list_users: {e}")
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"❌ Ошибка при загрузке списка пользователей: {str(e)}",
            reply_markup=reply_markup
        )
        return USER_MENU

async def list_expired_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List only expired users"""
    await update.callback_query.edit_message_text("⌛ Загрузка списка просроченных пользователей...")

    try:
        keyboard, users_data = await SelectionHelper.get_users_selection_keyboard(
            callback_prefix="select_user",
            include_back=True,
            max_per_row=1,
            filter_tag_by_telegram_id=str(update.effective_user.id),
            is_superadmin=context.user_data.get('is_superadmin', False),
            status_filter="EXPIRED"
        )

        if not users_data:
            keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.edit_message_text(
                "✅ Просроченных пользователей не найдено.",
                reply_markup=reply_markup
            )
            return USER_MENU

        context.user_data["users_data"] = users_data

        message = f"⌛ *Просроченные пользователи* ({len(users_data)} шт.)\n\n"
        message += "Выберите пользователя для просмотра подробностей:"

        await update.callback_query.edit_message_text(
            text=message,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

        return SELECTING_USER

    except Exception as e:
        logger.error(f"Error in list_expired_users: {e}")
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            f"❌ Ошибка при загрузке просроченных пользователей: {str(e)}",
            reply_markup=reply_markup
        )
        return USER_MENU

    if not users or not users.get("users"):
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            "❌ Пользователи не найдены или ошибка при получении списка.",
            reply_markup=reply_markup
        )
        return USER_MENU

    # Create a paginated list of users
    users_per_page = 5
    context.user_data["users"] = users["users"]
    context.user_data["current_page"] = 0
    context.user_data["users_per_page"] = users_per_page

    await send_users_page(update, context)
    return SELECTING_USER

async def send_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a page of users"""
    users = context.user_data["users"]
    current_page = context.user_data["current_page"]
    users_per_page = context.user_data["users_per_page"]

    start_idx = current_page * users_per_page
    end_idx = min(start_idx + users_per_page, len(users))

    message = f"👥 *Пользователи* (Страница {current_page + 1}/{(len(users) + users_per_page - 1) // users_per_page}):\n\n"

    for i in range(start_idx, end_idx):
        user = users[i]
        status_emoji = "✅" if user["status"] == "ACTIVE" else "❌"
        
        # Format expiration date
        try:
            expire_date = datetime.fromisoformat(user['expireAt'].replace('Z', '+00:00'))
            days_left = (expire_date - datetime.now().astimezone()).days
            expire_status = "🟢" if days_left > 7 else "🟡" if days_left > 0 else "🔴"
            expire_text = f"{user['expireAt'][:10]} ({days_left} дней)"
        except Exception:
            expire_status = "📅"
            expire_text = user['expireAt'][:10]
        
        message += f"{i+1}. {status_emoji} *{escape_markdown(user['username'])}*\n"
        message += f"   🔑 ID: `{user['shortUuid']}`\n"
        message += f"   📈 Трафик: {format_bytes(user['usedTrafficBytes'])}/{format_bytes(user['trafficLimitBytes'])}\n"
        message += f"   {expire_status} Истекает: {expire_text}\n\n"

    # Create navigation buttons
    keyboard = []
    nav_row = []

    if current_page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Назад", callback_data="prev_page"))

    if end_idx < len(users):
        nav_row.append(InlineKeyboardButton("Вперед ▶️", callback_data="next_page"))

    if nav_row:
        keyboard.append(nav_row)

    # Add action buttons for each user
    for i in range(start_idx, end_idx):
        user = users[i]
        user_row = [
            InlineKeyboardButton(f"👤 {user['username']}", callback_data=f"view_{user['uuid']}")
        ]
        keyboard.append(user_row)

    # Add back button
    keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")])

    reply_markup = InlineKeyboardMarkup(keyboard)

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

async def handle_user_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user selection with improved UI"""
    # Проверяем авторизацию
    if not check_authorization(update.effective_user):
        await update.callback_query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        return ConversationHandler.END
    
    query = update.callback_query
    await query.answer()

    data = query.data

    # Handle new SelectionHelper callbacks
    if data.startswith("select_user_"):
        user_uuid = data.split("_", 2)[2]
        await show_user_details(update, context, user_uuid)
        return SELECTING_USER

    # Handle back button from SelectionHelper
    elif data == "back":
        await show_users_menu(update, context)
        return USER_MENU

    # Handle pagination from SelectionHelper
    elif data.startswith("users_page_"):
        page = int(data.split("_")[2])
        try:
            keyboard, users_data = await SelectionHelper.get_users_selection_keyboard(
                callback_prefix="select_user",
                include_back=True,
                max_per_row=1,
                page=page,
                filter_tag_by_telegram_id=str(update.effective_user.id),
                is_superadmin=context.user_data.get('is_superadmin', False)
            )
            
            context.user_data["users_data"] = users_data
            
            message = f"👥 *Список пользователей* ({len(users_data)} шт.) - страница {page + 1}\n\n"
            message += "Выберите пользователя для просмотра подробной информации:"

            await query.edit_message_text(
                text=message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error in pagination: {e}")
            await show_users_menu(update, context)
            return USER_MENU

    elif data == "page_info":
        await query.answer("Это текущая страница. Используйте стрелки, чтобы переключать список.")
        return SELECTING_USER

    # Legacy support for old callback patterns
    elif data == "prev_page":
        context.user_data["current_page"] -= 1
        await send_users_page(update, context)

    elif data == "next_page":
        context.user_data["current_page"] += 1
        await send_users_page(update, context)

    elif data == "back_to_users":
        await show_users_menu(update, context)
        return USER_MENU

    elif data == "back_to_list":
        await list_users(update, context)
        return SELECTING_USER

    elif data.startswith("view_"):
        uuid = data.split("_")[1]
        try:
            logger.debug(f"Opening user details for uuid={uuid}")
        except Exception:
            pass
        await show_user_details(update, context, uuid)
        
    elif data.startswith("add_hwid_"):
        uuid = data.split("_")[2]
        await start_add_hwid(update, context, uuid)
        return WAITING_FOR_INPUT
        
    elif data.startswith("del_hwid_"):
        parts = data.split("_")
        uuid = parts[2]
        hwid = parts[3]
        await delete_hwid_device(update, context, uuid, hwid)

    return SELECTING_USER

async def show_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    """Show user details (safe formatting to avoid Markdown parse issues)"""
    try:
        logger.debug(f"show_user_details called for uuid={uuid}")
    except Exception:
        pass
    user = await user_cache.get_user(uuid)
    context.user_data.pop("search_type", None)
    context.user_data.pop("waiting_for", None)
    if not user:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            "❌ Пользователь не найден или ошибка при получении данных.",
            reply_markup=reply_markup
        )
        return USER_MENU

    # Формируем безопасное сообщение без Markdown
    try:
        message = format_user_details_safe(user)
    except Exception as e:
        logger.error(f"Error formatting user details (safe): {e}")
        message = f"👤 Пользователь: {user.get('username','')}\n🆔 UUID: {user.get('uuid','')}\n📊 Статус: {user.get('status','')}"

    is_superadmin = _is_superadmin_context(update, context)
    is_admin = _is_admin_context(update, context)
    can_manage_user = bool(is_admin or is_superadmin)
    can_delete_user = bool(is_superadmin or (is_admin and not _is_user_active(user)))
    keyboard = SelectionHelper.create_user_info_keyboard(
        uuid,
        action_prefix="user_action",
        is_admin=can_manage_user,
        allow_delete=can_delete_user
    )

    try:
        await update.callback_query.edit_message_text(
            text=message,
            reply_markup=keyboard,
            parse_mode = "Markdown"
        )
    except Exception as e:
        logger.error(f"Error sending user details: {e}")
        try:
            await update.callback_query.edit_message_caption(
                caption=message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e2:
            logger.error(f"Fallback to edit_message_caption failed: {e2}")
            await update.callback_query.answer("❌ Ошибка при отображении данных")

    context.user_data["current_user"] = user
    return SELECTING_USER


async def send_user_qrcode(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid: str):
    """Generate and send QR code built from the user's description link."""
    query = update.callback_query
    user = context.user_data.get("current_user")

    if not user or user.get("uuid") != uuid:
        user = await user_cache.get_user(uuid)

    if not user:
        if query:
            await query.answer("❌ Пользователь не найден.", show_alert=True)
        return SELECTING_USER

    # link = resolve_description_link(user.get("description"))
    # if not link:
    #     if query:
    #         await query.answer("❌ В описании пользователя нет ссылки для QR-кода.", show_alert=True)
    #     return SELECTING_USER

    crypto_link = user.get('happ', {}).get('cryptoLink', '')

    qr_stream = _build_qr_code_payload(crypto_link)
    username = escape_markdown(user.get("username", ""))
    caption_lines = [f"🔳 QR-код для `{username}`", f"`{escape_markdown(crypto_link)}`"]
    caption = "\n".join(caption_lines)

    target_message = query.message if query else update.effective_message
    if target_message:
        await target_message.reply_photo(photo=qr_stream, caption=caption, parse_mode="Markdown")
    elif update.effective_chat:
        await update.effective_chat.send_photo(photo=qr_stream, caption=caption, parse_mode="Markdown")
    else:
        logger.warning("Unable to send QR code photo: no target message or chat")

    return SELECTING_USER

async def handle_user_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user action with improved SelectionHelper support"""
    # Проверяем авторизацию
    if not check_authorization(update.effective_user):
        await update.callback_query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        return ConversationHandler.END
    
    query = update.callback_query
    await query.answer()

    data = query.data
    has_user_access = (
        context.user_data.get('is_admin', False)
        or context.user_data.get('is_superadmin', False)
    )

    # Handle new SelectionHelper callback patterns
    if data.startswith("user_action_"):
        action_parts = data.split("_")
        if len(action_parts) >= 4:
            action = action_parts[2]
            admin_only_actions = {"edit", "disable", "enable", "reset", "revoke", "delete", "hwid"}
            if not has_user_access and action in admin_only_actions:
                await query.answer(INSUFFICIENT_PERMISSIONS_MESSAGE, show_alert=True)
                return SELECTING_USER

            uuid = "_".join(action_parts[3:])  # Handle UUIDs with underscores
            
            if action == "edit":
                return await start_edit_user(update, context, uuid)
            elif action == "refresh":
                await show_user_details(update, context, uuid)
                return SELECTING_USER
            elif action == "disable":
                context.user_data["action"] = "disable"
                context.user_data["uuid"] = uuid
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Да, отключить", callback_data="confirm_action"),
                        InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"⚠️ Вы уверены, что хотите отключить пользователя?\n\nUUID: `{uuid}`",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return CONFIRM_ACTION
            elif action == "enable":
                context.user_data["action"] = "enable"
                context.user_data["uuid"] = uuid
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Да, включить", callback_data="confirm_action"),
                        InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"⚠️ Вы уверены, что хотите включить пользователя?\n\nUUID: `{uuid}`",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return CONFIRM_ACTION
            elif action == "reset" and len(action_parts) >= 5 and action_parts[3] == "traffic":
                context.user_data["action"] = "reset"
                context.user_data["uuid"] = "_".join(action_parts[4:])
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Да, сбросить", callback_data="confirm_action"),
                        InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"⚠️ Вы уверены, что хотите сбросить трафик пользователя?\n\nUUID: `{uuid}`",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return CONFIRM_ACTION
            elif action == "revoke":
                context.user_data["action"] = "revoke"
                context.user_data["uuid"] = uuid
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Да, отозвать", callback_data="confirm_action"),
                        InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    f"⚠️ Вы уверены, что хотите отозвать подписку пользователя?\n\nUUID: `{uuid}`",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return CONFIRM_ACTION
            elif action == "qrcode":
                return await send_user_qrcode(update, context, uuid)
            elif action == "delete":
                # Confirm user deletion with extra protection
                next_state = await confirm_delete_user(update, context, uuid)
                return next_state if next_state is not None else CONFIRM_ACTION

    admin_only_prefixes = (
        "disable_",
        "enable_",
        "reset_",
        "revoke_",
        "delete_",
        "edit_",
        "add_hwid_",
        "del_hwid_",
        "confirm_del_hwid_",
    )
    if not has_user_access and data.startswith(admin_only_prefixes):
        await query.answer(INSUFFICIENT_PERMISSIONS_MESSAGE, show_alert=True)
        return SELECTING_USER

    # Legacy support for back navigation
    if data == "back_to_list":
        await list_users(update, context)
        return SELECTING_USER

    elif data == "back_to_users":
        await show_users_menu(update, context)
        return USER_MENU

    elif data.startswith("disable_"):
        uuid = data.split("_")[1]
        context.user_data["action"] = "disable"
        context.user_data["uuid"] = uuid
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, отключить", callback_data="confirm_action"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ Вы уверены, что хотите отключить пользователя?\n\nUUID: `{uuid}`",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return CONFIRM_ACTION

    elif data.startswith("enable_"):
        uuid = data.split("_")[1]
        context.user_data["action"] = "enable"
        context.user_data["uuid"] = uuid
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, включить", callback_data="confirm_action"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ Вы уверены, что хотите включить пользователя?\n\nUUID: `{uuid}`",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return CONFIRM_ACTION

    elif data.startswith("reset_"):
        uuid = data.split("_")[1]
        context.user_data["action"] = "reset"
        context.user_data["uuid"] = uuid
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, сбросить", callback_data="confirm_action"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ Вы уверены, что хотите сбросить трафик пользователя?\n\nUUID: `{uuid}`",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return CONFIRM_ACTION

    elif data.startswith("revoke_"):
        uuid = data.split("_")[1]
        context.user_data["action"] = "revoke"
        context.user_data["uuid"] = uuid
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, отозвать", callback_data="confirm_action"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"⚠️ Вы уверены, что хотите отозвать подписку пользователя?\n\nUUID: `{uuid}`",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return CONFIRM_ACTION

    elif data.startswith("edit_"):
        uuid = data.split("_")[1]
        return await start_edit_user(update, context, uuid)
        
    elif data.startswith("hwid_"):
        uuid = data.split("_")[1]
        return await show_user_hwid_devices(update, context, uuid)
        
    elif data.startswith("stats_"):
        uuid = data.split("_")[1]
        return await show_user_stats(update, context, uuid)
        
    elif data.startswith("confirm_del_hwid_"):
        parts = data.split("_")
        uuid = parts[3]
        hwid = parts[4]
        return await confirm_delete_hwid_device(update, context, uuid, hwid)

    return SELECTING_USER

@check_admin
async def handle_action_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle action confirmation"""
    query = update.callback_query
    await query.answer()

    data = query.data

    # Handle final delete confirmation
    if data == "final_delete_user":
        return await execute_user_deletion(update, context)

    if data == "confirm_action":
        action = context.user_data.get("action")
        uuid = context.user_data.get("uuid")
        
        if not action or not uuid:
            await query.edit_message_text("❌ Ошибка: действие или UUID не найдены.")
            return SELECTING_USER
        
        result = None
        action_text = ""
        
        if action == "disable":
            result = await UserAPI.disable_user(uuid)
            action_text = "отключен"
        elif action == "enable":
            result = await UserAPI.enable_user(uuid)
            action_text = "включен"
        elif action == "reset":
            result = await UserAPI.reset_user_traffic(uuid)
            action_text = "сброшен трафик"
        elif action == "revoke":
            result = await UserAPI.revoke_user_subscription(uuid)
            action_text = "отозвана подписка"
        
        if result:
            keyboard = [
                [InlineKeyboardButton("👁️ Просмотр пользователя", callback_data=f"view_{uuid}")],
                [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ Пользователь успешно {action_text}.\n\nUUID: `{uuid}`",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"view_{uuid}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"❌ Не удалось выполнить действие: {action}.\n\nUUID: `{uuid}`",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
    else:
        uuid = context.user_data.get("uuid")
        if uuid:
            await show_user_details(update, context, uuid)
        else:
            await show_users_menu(update, context)
            return USER_MENU

    return SELECTING_USER

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input"""
    # Check if we're waiting for HWID input
    if context.user_data.get("waiting_for") == "hwid":
        return await handle_hwid_input(update, context)
    
    # Check if we're searching for a user
    search_type = context.user_data.get("search_type")

    if not search_type:
        # Check if we're in user creation mode
        if "create_user_fields" in context.user_data and "current_field_index" in context.user_data:
            return await handle_create_user_input(update, context)
    
        # If we're not in any special mode, show an error
        await update.message.reply_text("❌ Ошибка: тип поиска не найден.")
        await show_users_menu(update, context)
        return USER_MENU

    search_value = update.message.text.strip()

    if search_type in ("generic", "username"):
        term = search_value.strip()
        if len(term) < 2:
            back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]])
            await update.message.reply_text(
                "❗ Введите минимум 2 символа для поиска.",
                reply_markup=back_markup
            )
            return WAITING_FOR_INPUT

        matches = await search_users_by_term(term)

        if not matches:
            back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]])
            try:
                await update.message.reply_text(
                    f"❌ Пользователи по запросу `{escape_markdown(term)}` не найдены.",
                    reply_markup=back_markup,
                    parse_mode="Markdown"
                )
            except Exception:
                await update.message.reply_text(
                    f"❌ Пользователи по запросу '{term}' не найдены.",
                    reply_markup=back_markup
                )
            return USER_MENU

        if len(matches) == 1:
            user = matches[0]
            try:
                message = format_user_details_safe(user)

                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Сбросить трафик", callback_data=f"reset_{user['uuid']}"),
                        InlineKeyboardButton("📝 Редактировать", callback_data=f"edit_{user['uuid']}")
                    ]
                ]

                if user.get('status') == 'ACTIVE':
                    keyboard.append([
                        InlineKeyboardButton("🔴 Отключить", callback_data=f"disable_{user['uuid']}"),
                        InlineKeyboardButton("🔄 Отозвать подписку", callback_data=f"revoke_{user['uuid']}")
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton("🟢 Включить", callback_data=f"enable_{user['uuid']}"),
                        InlineKeyboardButton("🔄 Отозвать подписку", callback_data=f"revoke_{user['uuid']}")
                    ])

                keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")])
                reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    await update.message.reply_text(
                        text=message,
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error sending formatted message with Markdown: {e}")
                    await update.message.reply_text(
                        text=message,
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )

                context.user_data["current_user"] = user
                return SELECTING_USER
            except Exception as e:
                logger.error(f"Error formatting user details in search: {e}")
                keyboard = [[InlineKeyboardButton(f"👤 {user.get('username', 'Без имени')}", callback_data=f"view_{user.get('uuid')}")]]
                keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")])
                reply_markup = InlineKeyboardMarkup(keyboard)

                await update.message.reply_text(
                    text=f"Найден пользователь: {user.get('username','Без имени')}",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                context.user_data["current_user"] = user
                return SELECTING_USER

        max_results = 10
        keyboard = []
        message_lines = [
            f"🔍 Найдено {len(matches)} пользователей по запросу `{escape_markdown(term)}`:",
            ""
        ]

        for index, user in enumerate(matches[:max_results], 1):
            username = user.get('username') or 'Без имени'
            status = user.get('status') or 'UNKNOWN'
            message_lines.append(f"{index}. {escape_markdown(username)} — {escape_markdown(str(status))}")
            user_uuid = user.get('uuid')
            if user_uuid:
                keyboard.append([InlineKeyboardButton(f"👤 {username}", callback_data=f"view_{user_uuid}")])

        if len(matches) > max_results:
            message_lines.append("")
            message_lines.append(f"Показаны первые {max_results} результатов. Уточните запрос для более точного поиска.")

        keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_users")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(message_lines)

        try:
            await update.message.reply_text(
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error sending search results with Markdown: {e}")
            plain_text = message_text.replace('`', '')
            await update.message.reply_text(
                text=plain_text,
                reply_markup=reply_markup
            )

        return SELECTING_USER

    else:  # Text input
        field = context.user_data["edit_field"]
        user = context.user_data["edit_user"]
        value = update.message.text.strip()
        
        # Process the value based on the field
        if field == "expireAt":
            try:
                # Validate date format
                date_obj = datetime.strptime(value, "%Y-%m-%d")
                value = date_obj.strftime("%Y-%m-%dT00:00:00.000Z")
            except ValueError:
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "❌ Неверный формат даты. Используйте YYYY-MM-DD.",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return EDIT_USER
        
        elif field == "trafficLimitBytes":
            try:
                value = int(value)
                if value < 0:
                    raise ValueError("Traffic limit cannot be negative")
            except ValueError:
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "❌ Неверный формат числа. Введите целое число >= 0.",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return EDIT_USER
        
        elif field == "telegramId":
            try:
                value = int(value)
            except ValueError:
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "❌ Неверный формат Telegram ID. Введите целое число.",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return EDIT_USER
                
        elif field == "hwidDeviceLimit":
            try:
                value = int(value)
                if value < 0:
                    raise ValueError("Device limit cannot be negative")
                
                # Если устанавливается лимит устройств > 0, добавляем в обновляемые данные trafficLimitStrategy=NO_RESET
                if value > 0:
                    update_data["trafficLimitStrategy"] = "NO_RESET"
                    logger.info(f"Auto-setting trafficLimitStrategy=NO_RESET when setting hwidDeviceLimit to {value} for user {user['uuid']}")
            except ValueError:
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "❌ Неверный формат числа. Введите целое число >= 0.",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return EDIT_USER
        
        # Update the user with the new value
        update_data = {field: value}
        result = await UserAPI.update_user(user["uuid"], update_data)
        
        if result:
            keyboard = [
                [InlineKeyboardButton("👁️ Просмотр пользователя", callback_data=f"view_{user['uuid']}")],
                [InlineKeyboardButton("📝 Продолжить редактирование", callback_data=f"edit_{user['uuid']}")],
                [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Поле {field} успешно обновлено.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"❌ Не удалось обновить поле {field}.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        
        return EDIT_USER

@check_admin
async def start_create_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start creating a new user - first show template selection"""
    # Clear any previous user creation data
    context.user_data.pop("create_user", None)
    context.user_data.pop("create_user_fields", None)
    context.user_data.pop("current_field_index", None)
    context.user_data.pop("search_type", None)  # Clear search type to avoid confusion
    context.user_data.pop("using_template", None)
    context.user_data.pop("selected_template", None)
    
    # Initialize user creation data
    context.user_data["create_user"] = {}
    
    # Show template selection
    if TEMPLATES_ENABLED:
        await show_template_selection(update, context)
        return CREATE_USER_FIELD

    # Templates отключены - сразу запускаем ручное создание
    context.user_data["create_user_fields"] = _default_create_field_order()
    context.user_data["current_field_index"] = 0
    context.user_data["using_template"] = False
    return await ask_for_field(update, context)

async def show_template_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show template selection menu"""
    message = "🎯 *Создание пользователя*\n\n"
    
    if TEMPLATES_ENABLED:
        from modules.utils.presets import get_template_names
        
        message += "Выберите готовый шаблон или создайте пользователя вручную:\n\n"
        message += "📋 *Готовые шаблоны* содержат все необходимые настройки\n"
        message += "⚙️ *Ручное создание* позволяет настроить каждое поле отдельно"
        
        keyboard = []
        templates = get_template_names()

        for i in range(0, len(templates), 2):
            row = []
            for j in range(2):
                if i + j < len(templates):
                    template_name = templates[i + j]
                    row.append(InlineKeyboardButton(
                        template_name, 
                        callback_data=f"template_{template_name}"
                    ))
            keyboard.append(row)
        
        keyboard.extend([
            [InlineKeyboardButton("⚙️ Создать вручную", callback_data="create_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]
        ])
    else:
        message += "Сейчас доступно только ручное создание пользователя.\n"
        keyboard = [
            [InlineKeyboardButton("⚙️ Создать вручную", callback_data="create_manual")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
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

async def handle_template_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, template_name: str):
    """Handle template selection and show confirmation"""
    from modules.utils.presets import get_template_by_name, format_template_info
    
    template = get_template_by_name(template_name)
    if not template:
        await update.callback_query.edit_message_text(
            "❌ Шаблон не найден. Попробуйте еще раз.",
            parse_mode="Markdown"
        )
        return CREATE_USER_FIELD
    
    # Сохраняем выбранный шаблон
    context.user_data["selected_template"] = template_name
    context.user_data["using_template"] = True
    
    # Показываем информацию о шаблоне
    message = "📋 *Предпросмотр шаблона*\n\n"
    message += format_template_info(template_name)
    message += "\n\n💡 *Что дальше?*\n"
    message += "• Вы можете использовать шаблон как есть, только указав имя пользователя\n"
    message += "• Или настроить дополнительные поля (email, Telegram ID, тег и т.д.)"
    
    keyboard = [
        [InlineKeyboardButton("✅ Использовать шаблон", callback_data=f"use_template_{template_name}")],
        [InlineKeyboardButton("⚙️ Настроить дополнительно", callback_data=f"customize_template_{template_name}")],
        [InlineKeyboardButton("🔙 Выбрать другой шаблон", callback_data="back_to_templates")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def start_template_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, template_name: str, customize: bool = False):
    """Start user creation with selected template"""
    from modules.utils.presets import apply_template_to_user_data
    
    # Применяем шаблон
    context.user_data["create_user"] = apply_template_to_user_data({}, template_name)
    context.user_data["using_template"] = True
    context.user_data["template_name"] = template_name
    creator = update.effective_user
    is_super_admin = bool(creator and is_super_admin_user(creator.id))
    if not is_super_admin:
        traffic_limit = context.user_data["create_user"].get("trafficLimitBytes")
        if traffic_limit == 0:
            fallback_limit = DEFAULT_NON_SUPERADMIN_LIMIT_GB * GB
            context.user_data["create_user"]["trafficLimitBytes"] = fallback_limit
            logger.info(
                "Applied template %s with unlimited traffic for non-super admin %s; defaulted to %s GB",
                template_name,
                creator.id if creator else "unknown",
                DEFAULT_NON_SUPERADMIN_LIMIT_GB,
            )
    
    if customize:
        # Полная настройка - проходим все поля
        context.user_data["create_user_fields"] = _default_create_field_order()
        context.user_data["current_field_index"] = 0
    else:
        # Только имя пользователя и опциональные поля
        context.user_data["create_user_fields"] = ["username"]
        context.user_data["current_field_index"] = 0
    
    # Начинаем с первого поля
    await ask_for_field(update, context)
    return CREATE_USER_FIELD

async def ask_for_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for a field value when creating a user"""
    fields = _get_create_user_fields(context)
    index = _get_current_field_index(context)
    creation_data = context.user_data.setdefault("create_user", {})
    creator = update.effective_user
    user_is_super_admin = bool(creator and is_super_admin_user(creator.id))

    if index >= len(fields):
        # All fields collected, create the user
        return await finish_create_user(update, context)

    field = fields[index]
    field_name = USER_FIELDS[field]

    # Автозаполнение поля tag Telegram ID создателя и пропуск ввода
    if field == "tag":
        try:
            creator_id = str(update.effective_user.id)
            context.user_data.setdefault("create_user", {})["tag"] = creator_id
            logger.info(f"Auto-set tag to creator Telegram ID: {creator_id}")
        except Exception as e:
            logger.warning(f"Could not auto-set tag from creator Telegram ID: {e}")
        # Переходим к следующему полю без запроса ввода
        _advance_field_index(context)
        return await ask_for_field(update, context)

    # Проверяем, используется ли шаблон
    using_template = context.user_data.get("using_template", False)
    current_value = creation_data.get(field)
    
    # Если используется шаблон и поле уже заполнено, показываем текущее значение
    template_info = ""
    if using_template and current_value is not None:
        if field == "trafficLimitBytes":
            if current_value == 0 and not user_is_super_admin:
                fallback_limit = DEFAULT_NON_SUPERADMIN_LIMIT_GB * GB
                creation_data[field] = fallback_limit
                current_value = fallback_limit
                logger.info(
                    "Adjusted template traffic limit to %s GB for non-super admin %s",
                    DEFAULT_NON_SUPERADMIN_LIMIT_GB,
                    creator.id if creator else "unknown",
                )
            from modules.utils.formatters import format_bytes
            display_value = "Безлимитный" if current_value == 0 else format_bytes(current_value)
            template_info = f"\n🎯 *Значение из шаблона:* {display_value}"
        elif field == "hwidDeviceLimit":
            if current_value == 0:
                display_value = "Без лимита"
            elif current_value == 1:
                display_value = "1 устройство"
            elif current_value in [2, 3, 4]:
                display_value = f"{current_value} устройства"
            else:
                display_value = f"{current_value} устройств"
            template_info = f"\n🎯 *Значение из шаблона:* {display_value}"
        elif field == "trafficLimitStrategy":
            strategy_map = {
                "NO_RESET": "Без сброса",
                "DAY": "Ежедневно",
                "WEEK": "Еженедельно",
                "MONTH": "Ежемесячно"
            }
            display_value = strategy_map.get(current_value, current_value)
            template_info = f"\n🎯 *Значение из шаблона:* {display_value}"
        else:
            template_info = f"\n🎯 *Значение из шаблона:* {current_value}"

    # Special handling for username when using template
    if field == "username":
        template_name = context.user_data.get("template_name", "")
        message = f"👤 *Введите имя пользователя*\n\n"
        if using_template:
            message += f"Выбранный шаблон: {template_name}\n"
            message += "Введите уникальное имя пользователя (6-34 символа, только буквы, цифры, дефисы и подчеркивания):"
        else:
            message += "Введите имя пользователя:"
        
        # После ввода имени предлагаем дополнительные поля
        if using_template and len(fields) == 1:  # Только username в списке полей
            keyboard = [
                [InlineKeyboardButton("✅ Создать пользователя", callback_data="finish_template_user")],
                [InlineKeyboardButton("⚙️ Добавить дополнительные поля", callback_data="add_optional_fields")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]
            ]
        else:
            keyboard = []
        
        reply_markup = InlineKeyboardMarkup(keyboard)

    # Special handling for expireAt
    elif field == "expireAt":
        message = f"📅 *Выберите дату истечения*{template_info}\n\n"
        message += "Ниже представлены доступные варианты (30 или 60 дней)."
        if user_is_super_admin:
            message += " Доступен пресет «Безлимит (80 лет)», а также можно вручную ввести дату в формате YYYY-MM-DD."
        else:
            message += " Ввод произвольной даты недоступен для вашего уровня доступа."
        
        # Создаем пресеты дат с разными периодами
        today = datetime.now()
        keyboard = [
            [
                InlineKeyboardButton("30 дней", callback_data=f"create_date_{(today + timedelta(days=30)).strftime('%Y-%m-%d')}"),
                InlineKeyboardButton("60 дней", callback_data=f"create_date_{(today + timedelta(days=60)).strftime('%Y-%m-%d')}")
            ]
        ]
        if user_is_super_admin:
            keyboard.append([
                InlineKeyboardButton("Безлимит (80 лет)", callback_data=f"create_date_{(today + timedelta(days=365*80)).strftime('%Y-%m-%d')}")
            ])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
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
        
        return CREATE_USER_FIELD
    
    # Special handling for trafficLimitBytes
    elif field == "trafficLimitBytes":
        message = "📈 *Выберите лимит трафика*\n\nДоступные пресеты: 50 ГБ, 100 ГБ и 200 ГБ."
        if user_is_super_admin:
            message += "\nТакже можно ввести своё значение в байтах."
        else:
            message += "\nНестандартные значения вводить нельзя."
        
        keyboard = [
            [
                InlineKeyboardButton("50 ГБ", callback_data=f"create_traffic_{50 * GB}"),
                InlineKeyboardButton("100 ГБ", callback_data=f"create_traffic_{100 * GB}"),
                InlineKeyboardButton("200 ГБ", callback_data=f"create_traffic_{200 * GB}")
            ]
        ]
        if user_is_super_admin:
            keyboard.append([InlineKeyboardButton("Безлимитный", callback_data="create_traffic_0")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
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
        
        return CREATE_USER_FIELD

    # Special handling for description
    elif field == "description":
        message = f"📝 *Введите описание пользователя*\n\nВыберите один из шаблонов или введите своё описание:"
        
        # Создаём шаблоны для часто используемых описаний
        keyboard = [
            [InlineKeyboardButton("Стандартный пользователь", callback_data="create_desc_Стандартный пользователь")],
            [InlineKeyboardButton("VIP-клиент", callback_data="create_desc_VIP-клиент")],
            [InlineKeyboardButton("Тестовый аккаунт", callback_data="create_desc_Тестовый аккаунт")],
            [InlineKeyboardButton("Корпоративный клиент", callback_data="create_desc_Корпоративный клиент")],
            [InlineKeyboardButton("Демо-аккаунт", callback_data="create_desc_Демо-аккаунт")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
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
        
        return CREATE_USER_FIELD
    
    # Special handling for hwidDeviceLimit
    elif field == "hwidDeviceLimit":
        message = (
            "📱 *Выберите лимит устройств*\n\n"
            "Ниже приведены доступные варианты."
        )
        if user_is_super_admin:
            message += " Вы можете также ввести своё значение (целое число)."
        else:
            message += " Ввод произвольного значения недоступен."
        
        keyboard = [
            [
                InlineKeyboardButton("1 устройство", callback_data="create_device_1"),
                InlineKeyboardButton("2 устройства", callback_data="create_device_2")
            ]
        ]
        if user_is_super_admin:
            keyboard.append([InlineKeyboardButton("Без лимита (0)", callback_data="create_device_0")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
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
        
        return CREATE_USER_FIELD
        
    # Special handling for trafficLimitStrategy
    elif field == "trafficLimitStrategy":
        keyboard = [
            [InlineKeyboardButton("NO_RESET - Без сброса", callback_data="create_field_NO_RESET")],
            [InlineKeyboardButton("DAY - Ежедневно", callback_data="create_field_DAY")],
            [InlineKeyboardButton("WEEK - Еженедельно", callback_data="create_field_WEEK")],
            [InlineKeyboardButton("MONTH - Ежемесячно", callback_data="create_field_MONTH")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = f"🔄 Выберите стратегию сброса трафика:"
        
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
        
        return CREATE_USER_FIELD

    else:
        message = f"Введите {field_name}:{template_info}"

    if not field == 'username':
        keyboard = []

    # Для шаблонов добавляем кнопку "использовать значение из шаблона"
    if using_template and current_value is not None and field not in ["username"]:
        if field == "trafficLimitBytes":
            display_value = "Безлимитный" if current_value == 0 else format_bytes(current_value)
            keyboard.insert(0, [InlineKeyboardButton(f"✅ Оставить: {display_value}", callback_data=f"use_template_value_{field}")])
        elif field == "hwidDeviceLimit":
            if current_value == 0:
                display_value = "Без лимита"
            elif current_value == 1:
                display_value = "1 устройство"
            elif current_value in [2, 3, 4]:
                display_value = f"{current_value} устройства"
            else:
                display_value = f"{current_value} устройств"
            keyboard.insert(0, [InlineKeyboardButton(f"✅ Оставить: {display_value}", callback_data=f"use_template_value_{field}")])
        elif field == "trafficLimitStrategy":
            strategy_map = {
                "NO_RESET": "Без сброса",
                "DAY": "Ежедневно", 
                "WEEK": "Еженедельно",
                "MONTH": "Ежемесячно"
            }
            display_value = strategy_map.get(current_value, current_value)
            keyboard.insert(0, [InlineKeyboardButton(f"✅ Оставить: {display_value}", callback_data=f"use_template_value_{field}")])
        else:
            keyboard.insert(0, [InlineKeyboardButton(f"✅ Оставить: {current_value}", callback_data=f"use_template_value_{field}")])

    # Add cancel button
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")])

    reply_markup = InlineKeyboardMarkup(keyboard)

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

    return CREATE_USER_FIELD

@check_admin
async def handle_create_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user input when creating a user"""
    query = update.callback_query
    context.user_data.setdefault("create_user", {})

    if query:
        await query.answer()
        data = query.data

        if data.startswith("view_"):
            uuid = data.split("_", 1)[1]
            await show_user_details(update, context, uuid)
            return SELECTING_USER

        if data == "skip_field":
            # Skip this field
            _advance_field_index(context)
            await ask_for_field(update, context)
            return CREATE_USER_FIELD
        
        # elif data == "cancel_create":
        #     # Cancel user creation - handled by separate handler
        #     await show_users_menu(update, context)
        #     return USER_MENU
        
        elif data == "back_to_main":
            # Return to main menu
            await show_main_menu(update, context)
            return MAIN_MENU
        
        # Обработка выбора шаблона
        elif data.startswith("template_"):
            template_name = data[9:]  # убираем "template_"
            await handle_template_selection(update, context, template_name)
            return CREATE_USER_FIELD
        
        elif data == "create_manual":
            # Создание вручную - используем весь список полей
            context.user_data["create_user_fields"] = _default_create_field_order()
            context.user_data["current_field_index"] = 0
            context.user_data["using_template"] = False
            await ask_for_field(update, context)
            return CREATE_USER_FIELD
        
        elif data == "back_to_templates":
            await show_template_selection(update, context)
            return CREATE_USER_FIELD
        
        elif data.startswith("use_template_"):
            template_name = data[13:]  # убираем "use_template_"
            await start_template_creation(update, context, template_name, customize=False)
            return CREATE_USER_FIELD
        
        elif data.startswith("customize_template_"):
            template_name = data[19:]  # убираем "customize_template_"
            await start_template_creation(update, context, template_name, customize=True)
            return CREATE_USER_FIELD
        
        elif data == "finish_template_user":
            # Завершаем создание пользователя с шаблоном
            return await finish_create_user(update, context)
        
        elif data == "add_optional_fields":
            # Добавляем дополнительные поля
            optional_fields = _filter_create_fields(
                ["telegramId", "email", "tag", "expireAt"],
                ensure_username=False,
            )
            current_fields = _get_create_user_fields(context)
            # Добавляем поля, которых еще нет
            for field in optional_fields:
                if field not in current_fields:
                    current_fields.append(field)
            context.user_data["create_user_fields"] = current_fields
            _advance_field_index(context)  # переходим к следующему полю
            await ask_for_field(update, context)
            return CREATE_USER_FIELD
        
        elif data.startswith("use_template_value_"):
            # Использовать значение из шаблона для поля
            field_name = data[19:]  # убираем "use_template_value_"
            # Значение уже есть в данных пользователя из шаблона
            _advance_field_index(context)
            await ask_for_field(update, context)
            return CREATE_USER_FIELD
        
        elif data.startswith("create_field_"):
            # Handle selection for fields with predefined values
            value = data[13:]  # Берем всё, что идет после "create_field_", чтобы избежать обрезания значений
            fields = _get_create_user_fields(context)
            index = _get_current_field_index(context)
            field = fields[index]
            
            # Логирование для отладки, чтобы видеть какое значение устанавливается
            logger.info(f"Setting field {field} to value '{value}' from callback data '{data}'")
            
            context.user_data["create_user"][field] = value
            _advance_field_index(context)
            await ask_for_field(update, context)
            return CREATE_USER_FIELD
            
        elif data.startswith("create_date_"):
            # Handle selection for date presets
            date_str = data[12:] # Получаем YYYY-MM-DD из коллбэка
            fields = _get_create_user_fields(context)
            index = _get_current_field_index(context)
            field = fields[index]
            
            if field == "expireAt":
                # Конвертируем дату в нужный формат
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    value = date_obj.strftime("%Y-%m-%dT00:00:00.000Z")
                    context.user_data["create_user"][field] = value
                    
                    # Показываем сообщение о выбранной дате
                    await query.edit_message_text(
                        f"✅ Выбрана дата истечения: {date_str}",
                        parse_mode="Markdown"
                    )
                    
                    # Переходим к следующему полю
                    _advance_field_index(context)
                    await ask_for_field(update, context)
                except ValueError as e:
                    logger.error(f"Error parsing date: {e}")
                    await query.edit_message_text(
                        "❌ Ошибка при обработке даты. Пожалуйста, выберите другую дату или введите вручную.",
                        parse_mode="Markdown"
                    )
            
            return CREATE_USER_FIELD
            
        elif data.startswith("create_traffic_"):
            # Handle selection for traffic limit presets
            try:
                # Отладочный лог для анализа входящих данных
                logger.debug(f"Processing traffic selection with data: '{data}'")
                
                # Получаем значение в байтах из коллбэка
                traffic_bytes_str = data[14:]  # отрезаем префикс 'create_traffic_'
                logger.debug(f"Extracted traffic value string: '{traffic_bytes_str}'")
                
                fields = _get_create_user_fields(context)
                index = _get_current_field_index(context)
                field = fields[index]
                
                if field == "trafficLimitBytes":
                    # Преобразуем строку в число, игнорируя разделители
                    sanitized_value = traffic_bytes_str.strip().replace(' ', '').replace(',', '')
                    sanitized_value = sanitized_value.lstrip('_')
                    sanitized_value = ''.join(ch for ch in sanitized_value if ch.isdigit())
                    value = int(sanitized_value) if sanitized_value else 0
                    user_is_super_admin = bool(
                        update.effective_user and is_super_admin_user(update.effective_user.id)
                    )
                    if value == 0 and not user_is_super_admin:
                        await query.answer("Безлимит доступен только суперадмину.", show_alert=True)
                        await ask_for_field(update, context)
                        return CREATE_USER_FIELD
                    context.user_data["create_user"][field] = value
                    
                    # Форматируем значение в читаемый вид
                    from modules.utils.formatters import format_bytes
                    readable_value = format_bytes(value)
                    
                    # Для безлимитного трафика (0) показываем особое сообщение
                    if value == 0:
                        readable_value = "Безлимитный"
                    
                    # Показываем сообщение о выбранном лимите
                    await query.edit_message_text(
                        f"✅ Выбран лимит трафика: {readable_value}",
                        parse_mode="Markdown"
                    )
                    
                    # Переходим к следующему полю
                    _advance_field_index(context)
                    await ask_for_field(update, context)
            except ValueError as e:
                logger.error(f"Error parsing traffic limit: {e}")
                await query.edit_message_text(
                    "❌ Ошибка при обработке лимита трафика. Пожалуйста, выберите другое значение или введите вручную.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Unexpected error processing traffic limit: {e}", exc_info=True)
                await query.edit_message_text(
                    "❌ Произошла ошибка. Пожалуйста, попробуйте другое значение или обратитесь к администратору.",
                    parse_mode="Markdown"
                )
            
            return CREATE_USER_FIELD
            
        elif data.startswith("create_desc_"):
            try:
                # Отладочный лог для анализа входящих данных
                logger.debug(f"Processing description template with data: '{data}'")
                
                # Получаем текст описания из коллбэка
                description = data[12:]  # отрезаем префикс 'create_desc_'
                logger.debug(f"Extracted description: '{description}'")
                
                fields = _get_create_user_fields(context)
                index = _get_current_field_index(context)
                field = fields[index]
                
                if field == "description":
                    context.user_data["create_user"][field] = description
                    
                    # Показываем сообщение о выбранном шаблоне
                    await query.edit_message_text(
                        f"✅ Выбрано описание: {description}",
                        parse_mode="Markdown"
                    )
                    
                    # Переходим к следующему полю
                    _advance_field_index(context)
                    await ask_for_field(update, context)
            except Exception as e:
                logger.error(f"Unexpected error processing description template: {e}", exc_info=True)
                await query.edit_message_text(
                    "❌ Произошла ошибка при обработке шаблона описания. Пожалуйста, введите описание вручную.",
                    parse_mode="Markdown"
                )
                _advance_field_index(context)
                await ask_for_field(update, context)
            
            return CREATE_USER_FIELD
            
        elif data.startswith("create_device_"):
            # Handle selection for device limit presets
            try:
                # Отладочный лог для анализа входящих данных
                logger.debug(f"Processing device limit selection with data: '{data}'")
                
                # Получаем значение лимита устройств из коллбэка
                device_limit_str = data[14:]  # отрезаем префикс 'create_device_'
                logger.debug(f"Extracted device limit value string: '{device_limit_str}'")
                
                fields = _get_create_user_fields(context)
                index = _get_current_field_index(context)
                field = fields[index]
                
                if field == "hwidDeviceLimit":
                    # Преобразуем строку в число
                    value = int(device_limit_str)
                    context.user_data["create_user"][field] = value
                    
                    # Формируем читаемое представление (с правильным окончанием для числа устройств)
                    if value == 0:
                        readable_value = "Без лимита"
                    elif value == 1:
                        readable_value = "1 устройство"
                    elif value in [2, 3, 4]:
                        readable_value = f"{value} устройства"
                    else:
                        readable_value = f"{value} устройств"
                    
                    # Если установлен лимит устройств > 0, нужно также установить trafficLimitStrategy = NO_RESET
                    if value > 0:
                        # Явно устанавливаем стратегию NO_RESET
                        context.user_data["create_user"]["trafficLimitStrategy"] = "NO_RESET"
                        logger.info(f"Auto-setting trafficLimitStrategy=NO_RESET for user with hwidDeviceLimit={value}")
                    
                    # Показываем сообщение о выбранном лимите
                    await query.edit_message_text(
                        f"✅ Выбран лимит устройств: {readable_value}",
                        parse_mode="Markdown"
                    )
                    
                    # Переходим к следующему полю
                    _advance_field_index(context)
                    await ask_for_field(update, context)
            except ValueError as e:
                logger.error(f"Error parsing device limit: {e}")
                await query.edit_message_text(
                    "❌ Ошибка при обработке лимита устройств. Пожалуйста, выберите другое значение или введите вручную.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Unexpected error processing device limit: {e}", exc_info=True)
                await query.edit_message_text(
                    "❌ Произошла ошибка. Пожалуйста, попробуйте другое значение или обратитесь к администратору.",
                    parse_mode="Markdown"
                )
            
            return CREATE_USER_FIELD

    else:  # Text input
        try:
            fields = _get_create_user_fields(context)
            index = _get_current_field_index(context)
            field = fields[index]
            value = update.message.text.strip()
            
            # Process the value based on the field
            if field == "username":
                # Validate username format
                if not re.match(r"^[a-zA-Z0-9_-]{6,34}$", value):
                    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="cancel_create")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await update.message.reply_text(
                        "❌ Неверный формат имени пользователя. Используйте только буквы, цифры, подчеркивания и дефисы. Длина должна быть от 6 до 34 символов.\n\nВведите имя ещё раз:",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
            
            elif field == "expireAt":
                user_is_super_admin = bool(
                    update.effective_user and is_super_admin_user(update.effective_user.id)
                )
                if not user_is_super_admin:
                    await update.message.reply_text(
                        "❌ Ввод произвольной даты доступен только суперадмину. Используйте кнопки с готовыми вариантами.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
                try:
                    # Validate date format
                    date_obj = datetime.strptime(value, "%Y-%m-%d")
                    value = date_obj.strftime("%Y-%m-%dT00:00:00.000Z")
                except ValueError:
                    await update.message.reply_text(
                        "❌ Неверный формат даты. Используйте YYYY-MM-DD.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
            
            elif field == "trafficLimitBytes":
                user_is_super_admin = bool(
                    update.effective_user and is_super_admin_user(update.effective_user.id)
                )
                try:
                    value = int(value)
                    if value < 0:
                        raise ValueError("Traffic limit cannot be negative")
                    if not user_is_super_admin:
                        await update.message.reply_text(
                            "❌ Нестандартные значения доступны только суперадмину. Выберите лимит из списка кнопок ниже.",
                            parse_mode="Markdown"
                        )
                        await ask_for_field(update, context)
                        return CREATE_USER_FIELD
                except ValueError:
                    await update.message.reply_text(
                        "❌ Неверный формат числа. Введите целое число >= 0.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
            
            elif field == "telegramId":
                try:
                    value = int(value)
                except ValueError:
                    await update.message.reply_text(
                        "❌ Неверный формат Telegram ID. Введите целое число.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
            
            elif field == "tag":
                if value and not re.match(r"^[A-Z0-9_]{1,16}$", value):
                    await update.message.reply_text(
                        "❌ Неверный формат тега. Используйте только ЗАГЛАВНЫЕ буквы, цифры и подчеркивания. Максимальная длина - 16 символов.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
            
            elif field == "email":
                if value and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", value):
                    await update.message.reply_text(
                        "❌ Неверный формат email.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
                    
            elif field == "hwidDeviceLimit":
                user_is_super_admin = bool(
                    update.effective_user and is_super_admin_user(update.effective_user.id)
                )
                if not user_is_super_admin:
                    await update.message.reply_text(
                        "❌ Ввод произвольного лимита устройств доступен только суперадмину. Используйте кнопки ниже.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
                try:
                    value = int(value)
                    if value < 0:
                        raise ValueError("Device limit cannot be negative")
                    
                    # Если установлен лимит устройств > 0, нужно также установить trafficLimitStrategy = NO_RESET
                    if value > 0:
                        # Явно устанавливаем стратегию NO_RESET
                        context.user_data["create_user"]["trafficLimitStrategy"] = "NO_RESET"
                        logger.info(f"Auto-setting trafficLimitStrategy=NO_RESET for user with hwidDeviceLimit={value}")
                except ValueError:
                    await update.message.reply_text(
                        "❌ Неверный формат числа. Введите целое число >= 0.",
                        parse_mode="Markdown"
                    )
                    return CREATE_USER_FIELD
            
            # Store the value and move to the next field
            context.user_data["create_user"][field] = value
            
            # Если устанавливается лимит устройств, проверим и установим правильную стратегию трафика
            if field == "hwidDeviceLimit" and isinstance(value, int) and value > 0:
                context.user_data["create_user"]["trafficLimitStrategy"] = "NO_RESET"
                logger.info(f"Setting trafficLimitStrategy=NO_RESET because hwidDeviceLimit={value}")
                
            _advance_field_index(context)
            
            # Log the current state of the user creation data
            logger.debug(f"Current user creation data: {context.user_data['create_user']}")
            
            # Ask for the next field
            await ask_for_field(update, context)
            return CREATE_USER_FIELD
            
        except Exception as e:
            # Handle any unexpected errors
            logger.error(f"Error in handle_create_user_input: {e}")
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"❌ Произошла ошибка при обработке ввода: {str(e)}",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return USER_MENU

async def finish_create_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finish creating a user"""
    user_data = context.user_data.get("create_user")
    if not isinstance(user_data, dict):
        logger.warning("create_user context was missing or invalid during finish_create_user; reinitializing")
        user_data = {}
        context.user_data["create_user"] = user_data

    drive_link: Optional[str] = None
    encrypted_link: Optional[str] = None

    # Generate random username if not provided (20 characters, alphanumeric)
    if "username" not in user_data or not user_data["username"]:
        characters = string.ascii_letters + string.digits
        random_username = ''.join(random.choice(characters) for _ in range(20))
        user_data["username"] = random_username
        logger.info(f"Generated random username: {random_username}")

    # For regular admins (non super-admins) append their Telegram ID to username
    creator = update.effective_user
    is_super_admin = bool(creator and is_super_admin_user(creator.id))
    if creator and is_admin_user(creator.id):
        telegram_id_suffix = f"-{creator.id}"
        current_username = user_data.get("username", "")
        if current_username and not current_username.endswith(telegram_id_suffix):
            user_data["username"] = f"{current_username}{telegram_id_suffix}"
            logger.info(
                "Adjusted username for admin %s: %s -> %s",
                creator.id, current_username, user_data["username"]
            )

    # Set default values for required fields if not provided
    if "trafficLimitStrategy" not in user_data:
        user_data["trafficLimitStrategy"] = "NO_RESET"
    
    # Set default traffic limit (100 GB in bytes) if not provided
    if "trafficLimitBytes" not in user_data:
        user_data["trafficLimitBytes"] = 100 * 1024 * 1024 * 1024  # 100 GB in bytes
    elif user_data.get("trafficLimitBytes") == 0 and not is_super_admin:
        user_data["trafficLimitBytes"] = DEFAULT_NON_SUPERADMIN_LIMIT_GB * GB
        logger.info(
            "Non-super admin %s attempted to set unlimited traffic; defaulted to %s GB",
            creator.id if creator else "unknown",
            DEFAULT_NON_SUPERADMIN_LIMIT_GB,
        )
    
    # Set default device limit if not provided
    if "hwidDeviceLimit" not in user_data:
        user_data["hwidDeviceLimit"] = 1
    
    # Set default description if not provided
    if "description" not in user_data or not user_data["description"]:
        user_data["description"] = f"Автоматически созданный пользователь {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    
    # Set default reset day if not provided
    if "resetDay" not in user_data:
        user_data["resetDay"] = 1

    if ACTIVE_INTERNAL_SQUADS and "activeInternalSquads" not in user_data:
        user_data["activeInternalSquads"] = ACTIVE_INTERNAL_SQUADS

    # Автозаполнение тега Telegram ID создателя, если не задан вручную
    if not user_data.get("tag"):
        try:
            creator_id = str(update.effective_user.id)
            user_data["tag"] = creator_id
            logger.info(f"Defaulting user tag to creator Telegram ID: {creator_id}")
        except Exception as e:
            logger.warning(f"Could not set default tag from creator Telegram ID: {e}")

    # Если установлен лимит устройств (hwidDeviceLimit), убедимся, что стратегия сброса трафика установлена правильно
    if "hwidDeviceLimit" in user_data and user_data.get("hwidDeviceLimit", 0) > 0:
        # Принудительно устанавливаем NO_RESET для корректной работы с лимитом устройств
        user_data["trafficLimitStrategy"] = "NO_RESET"
        logger.info(f"Setting trafficLimitStrategy=NO_RESET for user with hwidDeviceLimit={user_data['hwidDeviceLimit']}")

    if "expireAt" not in user_data:
        # Default to 30 days from now
        user_data["expireAt"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z")

    # Log data for debugging
    logger.debug(f"Creating user with data: {user_data}")
    logger.info(f"Creating user with trafficLimitStrategy: {user_data.get('trafficLimitStrategy')}")

    # Create the user
    result = await UserAPI.create_user(user_data)

    if result:
        created_uuid = result.get('uuid')
        if not created_uuid and isinstance(result.get('user'), dict):
            created_uuid = result['user'].get('uuid')
        reply_buttons = []
        if created_uuid:
            reply_buttons.append([InlineKeyboardButton("👁️ Просмотр пользователя", callback_data=f"view_{created_uuid}")])
        reply_buttons.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(reply_buttons)
        
        message = f"✅ Пользователь успешно создан!\n\n"
        message += f"👤 Имя: {escape_markdown(result.get('username',''))}\n"
        if created_uuid:
            message += f"🆔 UUID: `{created_uuid}`\n"
        if result.get('shortUuid'):
            message += f"🔑 Короткий UUID: `{result['shortUuid']}`\n"
        link_for_qr: Optional[str] = None
        drive_link: Optional[str] = None
        encrypted_link: Optional[str] = None
        description_payload: Optional[str] = None
        base_description = user_data.get("description")
        # v208 может не возвращать subscriptionUuid — показываем только URL, если есть
        if result.get('subscriptionUrl'):
            subscription_url = result['subscriptionUrl']
            # message += f"\n🔗 URL подписки: `{subscription_url}`\n"

            subscription_entry = None
            short_uuid = result.get('shortUuid')
            if short_uuid:
                subscription_entry = await UserAPI.get_subscription_by_short_uuid(short_uuid)

            if not subscription_entry:
                subscription_entry = await UserAPI.find_subscription(
                    subscription_url=subscription_url,
                    username=result.get('username')
                )

            links = []
            if subscription_entry:
                links = subscription_entry.get('links') or []

            if created_uuid:
                temp_file_id = await store_subscription_links(
                    username=result.get('username'),
                    short_uuid=result.get('shortUuid'),
                    links=links,
                )
                if temp_file_id:
                    drive_link = f"https://drive.google.com/uc?id={temp_file_id}&export=download"

            if short_uuid and created_uuid:
                encrypted_link = await _fetch_encrypted_subscription_link(short_uuid)
                if not encrypted_link:
                    logger.warning("Encrypted link request returned nothing for short UUID %s", short_uuid)

        if created_uuid:
            description_parts: List[str] = []
            if drive_link:
                description_parts.append(f"drive:{drive_link}")
            if encrypted_link:
                description_parts.append(f"secure:{encrypted_link}")
            if base_description and description_parts:
                description_parts.append(f"text:{base_description}")
            if description_parts:
                description_payload = " || ".join(description_parts)
            else:
                description_payload = base_description

            if description_payload:
                try:
                    await UserAPI.update_user(created_uuid, {"description": description_payload})
                except Exception as exc:
                    logger.error("Failed to update user description: %s", exc)

        # preferred_link = resolve_description_link(description_payload) if description_payload else None
        # if not preferred_link:
        #     primary = drive_link if SUBSCRIPTION_DRIVE_LINK else encrypted_link
        #     preferred_link = primary or drive_link or encrypted_link
        # if preferred_link:
        #     label = "📁 Drive" if SUBSCRIPTION_DRIVE_LINK else "🔐 Happ"
        #     message += f"\n📝 {label}:\n`{escape_markdown(preferred_link)}`\n"
        # if base_description:
        #     message += f"✏️ Примечание: {escape_markdown(base_description)}\n"
        #
        # if preferred_link:
        #     link_for_qr = preferred_link
        # elif not link_for_qr:
        #     link_for_qr = drive_link or encrypted_link

        crypto_link = result.get('happ', {}).get('cryptoLink', '')

        for key in ("create_user", "create_user_fields", "current_field_index", "using_template", "search_type", "waiting_for"):
            context.user_data.pop(key, None)

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
        if crypto_link:
            qr_stream = _build_qr_code_payload(crypto_link)
            username_md = escape_markdown(result.get('username', ''))
            caption = f"🔳 QR-код для `{username_md}`\n`{escape_markdown(crypto_link)}`"
            target_message = update.callback_query.message if update.callback_query else update.message
            if target_message:
                await target_message.reply_photo(photo=qr_stream, caption=caption, parse_mode="Markdown")
            elif update.effective_chat:
                await update.effective_chat.send_photo(photo=qr_stream, caption=caption, parse_mode="Markdown")
            else:
                logger.warning("Unable to send QR code photo after user creation")

        if created_uuid:
            try:
                fresh_user = await UserAPI.get_user_by_uuid(created_uuid)
                if fresh_user:
                    user_cache.invalidate_user(created_uuid)
                    user_cache.invalidate_all_users()
                    context.user_data["current_user"] = fresh_user
            except Exception as exc:
                logger.warning("Failed to refresh cache for created user %s: %s", created_uuid, exc)

        return SELECTING_USER
    else:
        keyboard = [
            [InlineKeyboardButton("🔄 Попробовать снова", callback_data="create_user")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        error_message = "❌ Не удалось создать пользователя. "
        
        # Check for specific validation errors
        if "username" not in user_data:
            error_message += "Отсутствует имя пользователя."
        elif "trafficLimitStrategy" not in user_data:
            error_message += "Отсутствует стратегия сброса трафика."
        elif "expireAt" not in user_data:
            error_message += "Отсутствует дата истечения."
        else:
            error_message += "Пожалуйста, проверьте введенные данные."
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text=error_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                text=error_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        
        return MAIN_MENU

async def show_user_hwid_devices(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid: str):
    """Show user HWID devices"""
    devices = await UserAPI.get_user_hwid_devices(uuid)
    user = context.user_data.get("current_user") or await UserAPI.get_user_by_uuid(uuid)
    
    if not devices:
        keyboard = [
            [InlineKeyboardButton("➕ Добавить устройство", callback_data=f"add_hwid_{uuid}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=f"view_{uuid}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"📱 *Устройства HWID пользователя {escape_markdown(user['username'])}*\n\n"
            f"Устройства не найдены. Вы можете добавить новое устройство.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return SELECTING_USER
    
    message = f"📱 *Устройства HWID пользователя {escape_markdown(user['username'])}*\n\n"
    
    for i, device in enumerate(devices):
        message += f"{i+1}. HWID: `{device['hwid']}`\n"
        if device.get("platform"):
            message += f"   📱 Платформа: {escape_markdown(device['platform'])}\n"
        if device.get("osVersion"):
            message += f"   🖥️ Версия ОС: {escape_markdown(device['osVersion'])}\n"
        if device.get("deviceModel"):
            message += f"   📱 Модель: {escape_markdown(device['deviceModel'])}\n"
        if device.get("createdAt"):
            message += f"   🕒 Добавлено: {device['createdAt'][:10]}\n"
        message += "\n"
    
    # Add action buttons
    keyboard = [
        [InlineKeyboardButton("➕ Добавить устройство", callback_data=f"add_hwid_{uuid}")],
        [InlineKeyboardButton("🔙 Назад к пользователю", callback_data=f"view_{uuid}")]
    ]
    
    # Add delete buttons for each device
    for i, device in enumerate(devices):
        keyboard.append([
            InlineKeyboardButton(f"❌ Удалить {i+1}", callback_data=f"del_hwid_{uuid}_{device['hwid']}")
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return SELECTING_USER

async def show_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    """Show user statistics"""
    user = context.user_data.get("current_user") or await UserAPI.get_user_by_uuid(uuid)
    
    # Get usage for last 30 days
    end_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    usage = await UserAPI.get_user_usage_by_range(uuid, start_date, end_date)
    
    if not usage:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f"view_{uuid}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"❌ Статистика не найдена или ошибка при получении данных.\n\nПользователь: {escape_markdown(user['username'])}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return SELECTING_USER
    
    message = f"📊 *Статистика пользователя {escape_markdown(user['username'])}*\n\n"
    
    # Current usage
    message += f"📈 *Текущее использование*:\n"
    message += f"  • Использовано: {format_bytes(user['usedTrafficBytes'])}\n"
    message += f"  • Лимит: {format_bytes(user['trafficLimitBytes'])}\n"
    
    if user['trafficLimitBytes'] > 0:
        percent = (user['usedTrafficBytes'] / user['trafficLimitBytes']) * 100
        message += f"  • Процент: {percent:.2f}%\n"
    
    message += f"  • За все время: {format_bytes(user['lifetimeUsedTrafficBytes'])}\n\n"
    
    # Usage by node
    if usage:
        message += f"📊 *Использование по серверам (за 30 дней)*:\n"
        
        # Group by node
        node_usage = {}
        for entry in usage:
            node_uuid = entry.get("nodeUuid")
            node_name = entry.get("nodeName", "Неизвестный сервер")
            total = entry.get("total", 0)
            
            if node_uuid not in node_usage:
                node_usage[node_uuid] = {
                    "name": node_name,
                    "total": 0
                }
            
            node_usage[node_uuid]["total"] += total
        
        # Sort by usage
        sorted_nodes = sorted(node_usage.values(), key=lambda x: x["total"], reverse=True)
        
        for i, node in enumerate(sorted_nodes):
            message += f"  • {escape_markdown(node['name'])}: {format_bytes(node['total'])}\n"
    
    # Add action buttons
    keyboard = [
        [InlineKeyboardButton("🔙 Назад к пользователю", callback_data=f"view_{uuid}")],
        [InlineKeyboardButton("🔄 Обновить статистику", callback_data=f"stats_{uuid}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return SELECTING_USER

async def start_add_hwid(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid):
    """Start adding a HWID device"""
    user = context.user_data.get("current_user") or await UserAPI.get_user_by_uuid(uuid)
    
    context.user_data["add_hwid_uuid"] = uuid
    
    keyboard = [[InlineKeyboardButton("🔙 Отмена", callback_data=f"hwid_{uuid}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"📱 *Добавление устройства HWID для пользователя {escape_markdown(user['username'])}*\n\n"
        f"Введите HWID устройства:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    context.user_data["waiting_for"] = "hwid"
    return WAITING_FOR_INPUT

async def delete_hwid_device(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid, hwid):
    """Delete a HWID device"""
    user = context.user_data.get("current_user") or await UserAPI.get_user_by_uuid(uuid)
    
    # Confirm deletion
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"confirm_del_hwid_{uuid}_{hwid}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"hwid_{uuid}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        f"⚠️ *Удаление устройства HWID*\n\n"
        f"Вы уверены, что хотите удалить устройство с HWID `{hwid}` "
        f"для пользователя {escape_markdown(user['username'])}?",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return SELECTING_USER

async def confirm_delete_hwid_device(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid, hwid):
    """Confirm and delete a HWID device"""
    result = await UserAPI.delete_user_hwid_device(uuid, hwid)
    
    if result:
        message = f"✅ Устройство с HWID `{hwid}` успешно удалено."
    else:
        message = f"❌ Не удалось удалить устройство с HWID `{hwid}`."
    
    keyboard = [[InlineKeyboardButton("🔙 Назад к устройствам", callback_data=f"hwid_{uuid}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.callback_query.edit_message_text(
        message,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return SELECTING_USER

async def handle_hwid_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle HWID input"""
    uuid = context.user_data.get("add_hwid_uuid")
    if not uuid:
        await update.message.reply_text("❌ Ошибка: UUID пользователя не найден.")
        return SELECTING_USER
    
    hwid = update.message.text.strip()
    
    # Добавляем устройство
    result = await UserAPI.add_user_hwid_device(uuid, hwid)
    
    if result:
        keyboard = [[InlineKeyboardButton("🔙 Назад к устройствам", callback_data=f"hwid_{uuid}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Устройство с HWID `{hwid}` успешно добавлено.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        keyboard = [[InlineKeyboardButton("🔙 Назад к устройствам", callback_data=f"hwid_{uuid}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"❌ Не удалось добавить устройство с HWID `{hwid}`.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    return SELECTING_USER

def register_user_handlers(application):
    """Register user handlers"""
    # This function would register all the user-related handlers
    pass
async def confirm_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid: str):
    """Show button-based confirmation for user deletion."""
    try:
        user = await UserAPI.get_user_by_uuid(uuid)
        if not user:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.edit_message_text(
                "❌ Пользователь не найден.",
                reply_markup=reply_markup
            )
            return USER_MENU

        if not _is_superadmin_context(update, context) and _is_user_active(user):
            warning_text = (
                "❌ Диллеры не могут удалять активных пользователей. "
                "Сначала отключите пользователя или обратитесь к суперадмину."
            )
            if update.callback_query:
                await update.callback_query.answer(warning_text, show_alert=True)
            elif update.effective_chat:
                await update.effective_chat.send_message(warning_text)
            return SELECTING_USER

        context.user_data["delete_user"] = user
        context.user_data["action"] = "delete"
        context.user_data["uuid"] = uuid

        message_lines = [
            "🚨 *ВНИМАНИЕ! УДАЛЕНИЕ ПОЛЬЗОВАТЕЛЯ* 🚨",
            "",
            "⚠️ Вы собираетесь **НАВСЕГДА** удалить пользователя:",
            f"👤 **Имя:** `{escape_markdown(user['username'])}`",
            f"🆔 **UUID:** `{user['uuid']}`",
            f"📊 **Статус:** {user['status']}",
            f"📈 **Использовано трафика:** {format_bytes(user['usedTrafficBytes'])}",
            f"📅 **Дата истечения:** {user.get('expireAt', 'Не указана')[:10]}",
            "",
            "💀 **ЭТО ДЕЙСТВИЕ НЕЛЬЗЯ ОТМЕНИТЬ!**",
            "Будут удалены статистика, устройства HWID, история использования и настройки.",
            "",
            "🛡️ Подтвердите удаление кнопкой ниже:"
        ]

        keyboard = [
            [InlineKeyboardButton("🗑️ Да, удалить навсегда", callback_data="final_delete_user")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"view_{uuid}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await update.callback_query.edit_message_text(
                text="\n".join(message_lines),
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except Exception as send_error:
            logger.error(f"Error sending deletion confirmation message: {send_error}")
            await update.callback_query.edit_message_text(
                text="\n".join(message_lines),
                reply_markup=reply_markup
            )

        return CONFIRM_ACTION

    except Exception as e:
        logger.error(f"Error in confirm_delete_user: {e}")
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.edit_message_text(
            "❌ Ошибка при подготовке удаления пользователя.",
            reply_markup=reply_markup
        )
        return USER_MENU

async def execute_user_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the actual user deletion"""
    try:
        user_to_delete = context.user_data.get("delete_user")
        if not user_to_delete:
            await update.callback_query.edit_message_text("❌ Ошибка: данные пользователя для удаления не найдены.")
            return USER_MENU
        
        uuid = user_to_delete['uuid']
        username = user_to_delete['username']
        drive_file_id = _extract_drive_file_id(user_to_delete.get("description"))

        if not _is_superadmin_context(update, context) and _is_user_active(user_to_delete):
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=f"view_{uuid}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(
                "❌ Активного пользователя может удалить только суперадмин.",
                reply_markup=reply_markup
            )
            context.user_data.pop("delete_user", None)
            context.user_data.pop("action", None)
            context.user_data.pop("uuid", None)
            context.user_data.pop("waiting_for", None)
            return SELECTING_USER
        
        # Show deletion in progress
        await update.callback_query.edit_message_text(
            f"🗑️ Удаление пользователя `{escape_markdown(username)}`...\n\nПожалуйста, подождите...",
            parse_mode="Markdown"
        )
        
        # Perform the deletion
        result = await UserAPI.delete_user(uuid)
        drive_cleanup_note = ""
        if result and drive_file_id:
            deleted = await delete_drive_file(drive_file_id)
            if deleted:
                drive_cleanup_note = "\n📁 Файл подписки на Google Drive удален."
            else:
                logger.warning("Failed to delete Drive file %s for user %s", drive_file_id, uuid)
        
        # Clear stored deletion data
        context.user_data.pop("delete_user", None)
        context.user_data.pop("action", None)
        context.user_data.pop("uuid", None)
        context.user_data.pop("waiting_for", None)
        
        if result:
            keyboard = [
                [InlineKeyboardButton("📋 Список пользователей", callback_data="list_users")],
                [InlineKeyboardButton("🔙 Главное меню", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            success_message = (
                f"✅ **Пользователь успешно удален!**\n\n"
                f"👤 Имя: `{escape_markdown(username)}`\n"
                f"🆔 UUID: `{uuid}`\n\n"
                f"🗑️ Все данные пользователя были удалены навсегда."
            )
            if drive_cleanup_note:
                success_message += drive_cleanup_note
            
            await update.callback_query.edit_message_text(
                success_message,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
            # Log the deletion for audit purposes
            logger.warning(f"User deleted: {username} (UUID: {uuid}) by admin {update.effective_user.id}")
            
        else:
            keyboard = [
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data=f"user_action_delete_{uuid}")],
                [InlineKeyboardButton("🔙 Назад к списку", callback_data="list_users")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.callback_query.edit_message_text(
                f"❌ **Не удалось удалить пользователя!**\n\n"
                f"👤 Имя: `{escape_markdown(username)}`\n"
                f"🆔 UUID: `{uuid}`\n\n"
                f"Возможные причины:\n"
                f"• Пользователь уже удален\n"
                f"• Ошибка соединения с сервером\n"
                f"• Недостаточно прав доступа\n\n"
                f"Попробуйте еще раз или обратитесь к администратору.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        
        return USER_MENU
        
    except Exception as e:
        logger.error(f"Error in execute_user_deletion: {e}")
        
        # Clear stored deletion data
        context.user_data.pop("delete_user", None)
        context.user_data.pop("action", None)
        context.user_data.pop("uuid", None)
        context.user_data.pop("waiting_for", None)
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к списку", callback_data="list_users")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.callback_query.edit_message_text(
            f"❌ **Критическая ошибка при удалении пользователя!**\n\n"
            f"Ошибка: `{str(e)}`\n\n"
            f"Обратитесь к администратору системы.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        return USER_MENU


async def start_edit_user(update: Update, context: ContextTypes.DEFAULT_TYPE, uuid: str):
    """Start editing a user"""
    # Проверяем авторизацию
    if not check_authorization(update.effective_user):
        await update.callback_query.answer("⛔ Вы не авторизованы для использования этого бота.", show_alert=True)
        return ConversationHandler.END
    
    # Получаем данные пользователя
    user = await UserAPI.get_user_by_uuid(uuid)
    if not user:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.edit_message_text(
            "❌ Пользователь не найден или ошибка при получении данных.",
            reply_markup=reply_markup
        )
        return USER_MENU
    
    # Сохраняем данные пользователя для редактирования
    context.user_data["edit_user"] = user
    context.user_data["edit_field"] = None
    
    # Создаем меню выбора поля для редактирования
    keyboard = []
    for field_key, field_name in USER_FIELDS.items():
        if field_key in CREATE_USER_EXCLUDED_FIELDS_SET:
            continue  # эти поля нельзя редактировать из клиента
        if field_key in user:  # Показываем только поля, которые есть у пользователя
            keyboard.append([InlineKeyboardButton(f"📝 {field_name}", callback_data=f"edit_field_{field_key}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад к пользователю", callback_data=f"view_{uuid}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = f"📝 *Редактирование пользователя {escape_markdown(user['username'])}*\n\n"
    message += "Выберите поле для редактирования:"
    if not keyboard:
        message += "\n\n⚠️ Нет доступных полей для редактирования."

    await update.callback_query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    return EDIT_USER

@check_admin
async def handle_edit_field_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit field selection"""
    query = update.callback_query
    await query.answer()
    
    data = query.data

    if data.startswith("edit_field_"):
        field = data[11:]  # убираем "edit_field_"
        user = context.user_data["edit_user"]
        user_is_super_admin = bool(update.effective_user and is_super_admin_user(update.effective_user.id))
        
        if field not in user:
            await query.edit_message_text("❌ Поле не найдено в данных пользователя.")
            return EDIT_USER
        if field in CREATE_USER_EXCLUDED_FIELDS_SET:
            await query.answer("Это поле нельзя редактировать в клиенте.", show_alert=True)
            return EDIT_USER
        
        # Сохраняем выбранное поле
        context.user_data["edit_field"] = field
        field_name = USER_FIELDS.get(field, field)
        
        # Показываем текущее значение и запрашиваем новое
        current_value = user[field]
        if field == "trafficLimitBytes":
            from modules.utils.formatters import format_bytes
            display_value = "Безлимитный" if current_value == 0 else format_bytes(current_value)
        elif field == "expireAt":
            display_value = current_value[:10] if current_value else "Не указана"
        else:
            display_value = str(current_value) if current_value else "Не указано"
        
        message = f"📝 *Редактирование поля: {field_name}*\n\n"
        message += f"Текущее значение: `{display_value}`\n\n"
        message += f"Введите новое значение для поля {field_name}:"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к выбору поля", callback_data=f"edit_{user['uuid']}")],
            [InlineKeyboardButton("❌ Отмена", callback_data=f"view_{user['uuid']}")]
        ]
        # Add preset inline buttons for specific fields
        preset_keyboard = []
        if field == "expireAt":
            message += "\nВы можете ввести дату в формате `YYYY-MM-DD` для установки точной даты,\n"
            message += "или нажать на кнопку, чтобы добавить дни к текущему сроку:\n"
            preset_keyboard.extend([
                [
                    InlineKeyboardButton("➕ 30 дн.", callback_data="edit_expire_plus_30"),
                    InlineKeyboardButton("➕ 60 дн.", callback_data="edit_expire_plus_60"),
                    InlineKeyboardButton("➕ 90 дн.", callback_data="edit_expire_plus_90"),
                ],
                [
                    InlineKeyboardButton("➕ 180 дн.", callback_data="edit_expire_plus_180"),
                    InlineKeyboardButton("➕ 360 дн.", callback_data="edit_expire_plus_360"),
                ],
            ])
        elif field == "trafficLimitBytes":
            message += "\nВведите лимит в ГБ (целое число)."
            if user_is_super_admin:
                message += " `0` — безлимит (доступно только суперадмину)."
            else:
                message += " Безлимит доступен только суперадмину."
            message += "\nИли выберите готовое значение ниже:"
            traffic_row = [
                InlineKeyboardButton("50 ГБ", callback_data="edit_traffic_gb_50"),
                InlineKeyboardButton("100 ГБ", callback_data="edit_traffic_gb_100"),
                InlineKeyboardButton("200 ГБ", callback_data="edit_traffic_gb_200"),
            ]
            preset_keyboard.append(traffic_row)
            if user_is_super_admin:
                preset_keyboard.append([InlineKeyboardButton("0 (безлимит)", callback_data="edit_traffic_gb_0")])
        elif field == "trafficLimitStrategy":
            message += "\nВыберите стратегию сброса: `NO_RESET` (без сброса), `DAY`, `WEEK`, `MONTH`."
            preset_keyboard.extend([
                [
                    InlineKeyboardButton("NO_RESET", callback_data="edit_strategy_NO_RESET"),
                    InlineKeyboardButton("DAY", callback_data="edit_strategy_DAY"),
                ],
                [
                    InlineKeyboardButton("WEEK", callback_data="edit_strategy_WEEK"),
                    InlineKeyboardButton("MONTH", callback_data="edit_strategy_MONTH"),
                ],
            ])
        elif field == "hwidDeviceLimit":
            message += "\nВведите лимит устройств (целое число). `0` — без ограничений.\nИли выберите готовое значение ниже:"
            preset_keyboard.extend([
                [
                    InlineKeyboardButton("0", callback_data="edit_devices_0"),
                    InlineKeyboardButton("1", callback_data="edit_devices_1"),
                    InlineKeyboardButton("2", callback_data="edit_devices_2"),
                ],
                [
                    InlineKeyboardButton("3", callback_data="edit_devices_3"),
                    InlineKeyboardButton("5", callback_data="edit_devices_5"),
                    InlineKeyboardButton("10", callback_data="edit_devices_10"),
                ],
            ])

        if preset_keyboard:
            keyboard = preset_keyboard + keyboard
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            text=message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        
        return EDIT_VALUE

    elif data.startswith("edit_"):
        # Return to the edit menu for this user
        try:
            uuid = data.split("_", 1)[1]
        except Exception:
            return EDIT_USER
        return await start_edit_user(update, context, uuid)

    elif data.startswith("view_"):
        uuid = data.split("_")[1]
        await show_user_details(update, context, uuid)
        return SELECTING_USER
    
    elif data == "back_to_users":
        await show_users_menu(update, context)
        return USER_MENU
    
    return EDIT_USER

@check_admin
async def handle_edit_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit field value input"""
    # Handle navigation callbacks while in EDIT_VALUE state
    if hasattr(update, "callback_query") and update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data
        user = context.user_data.get("edit_user")

        # Preset handlers for inline buttons while editing a value
        if user is not None:
            if data.startswith("edit_expire_plus_"):
                try:
                    days = int(data.split("_")[-1])
                except Exception:
                    return EDIT_VALUE

                # Base on current expireAt if valid, otherwise today
                base_date = None
                try:
                    if user.get("expireAt"):
                        base_date = datetime.fromisoformat(user['expireAt'].replace('Z', '+00:00'))
                except Exception:
                    base_date = None
                if base_date is None:
                    base_date = datetime.now().astimezone()

                new_date = (base_date + timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
                update_data = {"expireAt": new_date}

                result = await UserAPI.update_user(user["uuid"], update_data)
                if result:
                    context.user_data["edit_user"]["expireAt"] = new_date
                    keyboard = [
                        [InlineKeyboardButton("👤 К пользователю", callback_data=f"view_{user['uuid']}")],
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_{user['uuid']}")],
                        [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")],
                    ]
                    await query.edit_message_text(
                        text=f"✅ Дата истечения обновлена: {new_date[:10]}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return EDIT_USER
                else:
                    await query.edit_message_text(
                        text="❌ Не удалось обновить дату истечения.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]])
                    )
                    return EDIT_VALUE

            elif data.startswith("edit_traffic_gb_"):
                try:
                    gb = int(data.split("_")[-1])
                    bytes_value = 0 if gb == 0 else gb * 1024 * 1024 * 1024
                except Exception:
                    return EDIT_VALUE
                user_is_super_admin = bool(update.effective_user and is_super_admin_user(update.effective_user.id))
                if gb == 0 and not user_is_super_admin:
                    await query.answer("Безлимит доступен только суперадмину.", show_alert=True)
                    return EDIT_VALUE
                update_data = {"trafficLimitBytes": bytes_value}
                result = await UserAPI.update_user(user["uuid"], update_data)
                if result:
                    context.user_data["edit_user"]["trafficLimitBytes"] = bytes_value
                    shown = "Безлимитный" if bytes_value == 0 else f"{gb} ГБ"
                    keyboard = [
                        [InlineKeyboardButton("👤 К пользователю", callback_data=f"view_{user['uuid']}")],
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_{user['uuid']}")],
                        [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")],
                    ]
                    await query.edit_message_text(
                        text=f"✅ Лимит трафика обновлён: {shown}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return EDIT_USER
                else:
                    await query.edit_message_text(
                        text="❌ Не удалось обновить лимит трафика.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]])
                    )
                    return EDIT_VALUE

            elif data.startswith("edit_strategy_"):
                strategy = data.split("_", 2)[2]
                if strategy not in ("NO_RESET", "DAY", "WEEK", "MONTH"):
                    return EDIT_VALUE
                update_data = {"trafficLimitStrategy": strategy}
                result = await UserAPI.update_user(user["uuid"], update_data)
                if result:
                    context.user_data["edit_user"]["trafficLimitStrategy"] = strategy
                    keyboard = [
                        [InlineKeyboardButton("👤 К пользователю", callback_data=f"view_{user['uuid']}")],
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_{user['uuid']}")],
                        [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")],
                    ]
                    await query.edit_message_text(
                        text=f"✅ Стратегия сброса трафика обновлена: {strategy}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return EDIT_USER
                else:
                    await query.edit_message_text(
                        text="❌ Не удалось обновить стратегию сброса трафика.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]])
                    )
                    return EDIT_VALUE

            elif data.startswith("edit_devices_"):
                try:
                    devices = int(data.split("_")[-1])
                except Exception:
                    return EDIT_VALUE
                if devices < 0:
                    return EDIT_VALUE
                update_data = {"hwidDeviceLimit": devices}
                if devices > 0:
                    update_data["trafficLimitStrategy"] = "NO_RESET"
                result = await UserAPI.update_user(user["uuid"], update_data)
                if result:
                    context.user_data["edit_user"].update(update_data)
                    shown = "Без ограничений" if devices == 0 else str(devices)
                    keyboard = [
                        [InlineKeyboardButton("👤 К пользователю", callback_data=f"view_{user['uuid']}")],
                        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_{user['uuid']}")],
                        [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")],
                    ]
                    await query.edit_message_text(
                        text=f"✅ Лимит устройств обновлён: {shown}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return EDIT_USER
                else:
                    await query.edit_message_text(
                        text="❌ Не удалось обновить лимит устройств.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]])
                    )
                    return EDIT_VALUE

        if data.startswith("edit_"):
            try:
                uuid = data.split("_", 1)[1]
            except Exception:
                return EDIT_USER
            return await start_edit_user(update, context, uuid)
        elif data.startswith("view_"):
            uuid = data.split("_", 1)[1]
            await show_user_details(update, context, uuid)
            return SELECTING_USER
        elif data == "back_to_users":
            await show_users_menu(update, context)
            return USER_MENU
        return EDIT_VALUE

    field = context.user_data.get("edit_field")
    user = context.user_data.get("edit_user")
    
    if not field or not user:
        await update.message.reply_text("❌ Ошибка: данные для редактирования не найдены.")
        return USER_MENU
    
    value = update.message.text.strip()
    
    # Process the value based on the field
    if field == "expireAt":
        try:
            # Validate date format
            date_obj = datetime.strptime(value, "%Y-%m-%d")
            value = date_obj.strftime("%Y-%m-%dT00:00:00.000Z")
        except ValueError:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "❌ Неверный формат даты. Используйте YYYY-MM-DD.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return EDIT_USER
    
    elif field == "trafficLimitBytes":
        try:
            gb = int(value)
            if gb < 0:
                raise ValueError("Traffic limit cannot be negative")
            user_is_super_admin = bool(update.effective_user and is_super_admin_user(update.effective_user.id))
            if gb == 0 and not user_is_super_admin:
                keyboard = [
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    "❌ Безлимитный лимит доступен только суперадмину. Введите другое значение.",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
                return EDIT_USER
            # Convert GB to bytes (0 stays unlimited)
            value = 0 if gb == 0 else gb * 1024 * 1024 * 1024
        except ValueError:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "❌ Неверный формат. Введите целое число ГБ (0 — безлимит, доступен только суперадмину).",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return EDIT_USER
    
    elif field == "telegramId":
        try:
            value = int(value)
        except ValueError:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "❌ Неверный формат Telegram ID. Введите целое число.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return EDIT_USER
            
    elif field == "hwidDeviceLimit":
        try:
            value = int(value)
            if value < 0:
                raise ValueError("Device limit cannot be negative")
        except ValueError:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "❌ Неверный формат числа. Введите целое число >= 0.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return EDIT_USER
    
    # Update the user with the new value
    update_data = {field: value}
    
    # Если устанавливается лимит устройств > 0, добавляем в обновляемые данные trafficLimitStrategy=NO_RESET
    if field == "hwidDeviceLimit" and value > 0:
        update_data["trafficLimitStrategy"] = "NO_RESET"
        logger.info(f"Auto-setting trafficLimitStrategy=NO_RESET when setting hwidDeviceLimit to {value} for user {user['uuid']}")
    result = await UserAPI.update_user(user["uuid"], update_data)
    
    if result:
        keyboard = [
            [InlineKeyboardButton("👁️ Просмотр пользователя", callback_data=f"view_{user['uuid']}")],
            [InlineKeyboardButton("📝 Продолжить редактирование", callback_data=f"edit_{user['uuid']}")],
            [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Поле {field} успешно обновлено.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    else:
        keyboard = [
            [InlineKeyboardButton("🔙 Назад", callback_data=f"edit_{user['uuid']}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"❌ Не удалось обновить поле {field}.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    return EDIT_USER

@check_admin
async def handle_cancel_user_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel user creation"""
    query = update.callback_query
    await query.answer("Создание пользователя отменено")
    
    # Очищаем контекст создания пользователя
    keys_to_remove = [
        'create_user', 'create_user_fields', 'current_field_index', 
        'using_template', 'template_name', 'selected_template',
        'search_type', 'waiting_for'
    ]
    
    for key in keys_to_remove:
        context.user_data.pop(key, None)
    
    # Возвращаемся в меню пользователей
    await show_users_menu(update, context)
    return USER_MENU
