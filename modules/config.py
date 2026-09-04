import os
from typing import List, Optional, Tuple

from dotenv import load_dotenv
import logging
import json

# Load environment variables
load_dotenv()

# Set up logging for config
logger = logging.getLogger(__name__)

def _parse_cookie_header(value: str) -> dict:
    """Parse a raw Cookie header string into a mapping."""
    result = {}
    for part in value.split(";"):
        name, _, raw_value = part.strip().partition("=")
        if name and raw_value:
            result[name] = raw_value
    return result

def _load_api_cookies(raw_value: str) -> dict:
    """Load cookie configuration supplied via environment variables."""
    if not raw_value:
        return {}
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        logger.debug("Cookies env value is not JSON, falling back to header format.")
        return _parse_cookie_header(raw_value)
    else:
        if isinstance(parsed, dict):
            return {str(name): str(value) for name, value in parsed.items() if name and value is not None}
        if isinstance(parsed, list):
            cookies = {}
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                value = item.get("value")
                if name and value is not None:
                    cookies[str(name)] = str(value)
            if cookies:
                return cookies
        logger.error("Unsupported cookie configuration. Provide JSON object or cookie header string.")
        return {}

_raw_cookies = os.getenv("REMNAWAVE_COOKIES") or os.getenv("COOKIES", "")
API_COOKIES = _load_api_cookies(_raw_cookies)

if _raw_cookies and not API_COOKIES:
    logger.warning("Cookie configuration is set but no valid cookies were parsed.")

# API Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://remnawave:3000/api")
API_TOKEN = os.getenv("REMNAWAVE_API_TOKEN")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MAIN_MENU_TITLE = os.getenv("MAIN_MENU_TITLE", "Remnawave Admin")
INBOUNDS_MENU_ENABLED = os.getenv("INBOUNDS_MENU_ENABLED", "false").lower() == "true"
EXPORT_EXCEL_ENABLED = os.getenv("EXPORT_EXCEL_ENABLED", "false").lower() == "true"

super_admin_ids_str = os.getenv("SUPER_ADMIN_USER_IDS") or os.getenv("SUPERADMIN_USER_IDS", "")
logger.info(f"Raw SUPER_ADMIN_USER_IDS from env: '{super_admin_ids_str}'")

SUPER_ADMIN_USER_IDS = []
if super_admin_ids_str:
    try:
        SUPER_ADMIN_USER_IDS = [int(id.strip()) for id in super_admin_ids_str.split(",") if id.strip()]
        logger.info(f"Parsed SUPER_ADMIN_USER_IDS: {SUPER_ADMIN_USER_IDS}")
    except ValueError as e:
        logger.error(f"Error parsing SUPER_ADMIN_USER_IDS: {e}")
        SUPER_ADMIN_USER_IDS = []
else:
    logger.warning("SUPER_ADMIN_USER_IDS is empty or not set!")

operator_ids_str = os.getenv("OPERATOR_USER_IDS", "")
logger.info(f"Raw OPERATOR_USER_IDS from env: '{operator_ids_str}'")

OPERATOR_USER_IDS = []
if operator_ids_str:
    try:
        OPERATOR_USER_IDS = [int(id.strip()) for id in operator_ids_str.split(",") if id.strip()]
        logger.info(f"Parsed OPERATOR_USER_IDS: {OPERATOR_USER_IDS}")
    except ValueError as e:
        logger.error(f"Error parsing OPERATOR_USER_IDS: {e}")
        OPERATOR_USER_IDS = []
else:
    logger.info("OPERATOR_USER_IDS is empty or not set")

ADMIN_DB_PATH = os.getenv("ADMIN_DB_PATH", os.path.join("data", "admins.db"))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

_admin_notifications_chat_id_raw = os.getenv("ADMIN_NOTIFICATIONS_CHAT_ID")
ADMIN_NOTIFICATIONS_CHAT_ID: Optional[int] = None
if _admin_notifications_chat_id_raw:
    try:
        ADMIN_NOTIFICATIONS_CHAT_ID = int(_admin_notifications_chat_id_raw.strip())
        logger.info("Admin notifications will be sent to chat ID %s", ADMIN_NOTIFICATIONS_CHAT_ID)
    except ValueError:
        logger.error(
            "Invalid ADMIN_NOTIFICATIONS_CHAT_ID '%s'. It must be an integer chat ID.",
            _admin_notifications_chat_id_raw,
        )


def _resolve_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value if os.path.isabs(value) else os.path.join(ROOT_DIR, value)


GOOGLE_SERVICE_ACCOUNT_FILE = _resolve_path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"))
GOOGLE_OAUTH_TOKEN_FILE = _resolve_path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE"))
GOOGLE_OAUTH_CLIENT_SECRET_FILE = _resolve_path(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE"))

GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID")
SUBSCRIPTION_DRIVE_LINK = os.getenv("SUBSCRIPTION_DRIVE_LINK", "false").lower() == "true"
# Which subscription link(s) to show in the user detail view:
# "crypto" (default) = Happ crypto link only, "regular" = subscriptionUrl only, "both" = show both
SUBSCRIPTION_LINK_MODE = os.getenv("SUBSCRIPTION_LINK_MODE", "crypto").strip().lower()
if SUBSCRIPTION_LINK_MODE not in ("crypto", "regular", "both"):
    SUBSCRIPTION_LINK_MODE = "crypto"
SUBSCRIPTION_SCRIPT_URL = os.getenv(
    "SUBSCRIPTION_SCRIPT_URL",
    "https://script.google.com/macros/s/AKfycbwIMaOuOjlvfqBN7tLevued0dqMZXrSkGIXBry9YSHxFJkH2Ewbx2Rl7ACEV5SiF9iy/exec?id={shortUuid}",
)

active_squads_env = os.getenv("ACTIVE_INTERNAL_SQUADS", "")
ACTIVE_INTERNAL_SQUADS = [s.strip() for s in active_squads_env.split(",") if s.strip()]


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time_pair(value: str, default: Tuple[int, int] = (9, 0)) -> Tuple[int, int]:
    if not value:
        return default
    parts = value.split(":")
    if len(parts) != 2:
        logger.warning("Invalid time format '%s', expected HH:MM. Using default %s:%s", value, *default)
        return default
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1])))
        return hour, minute
    except ValueError:
        logger.warning("Invalid numeric time '%s'. Using default %s:%s", value, *default)
        return default

def _parse_int_list(value: str, default: List[int], var_name: str) -> List[int]:
    """Parse comma-separated integers from env, falling back to default on errors."""
    if not value:
        return default
    result: List[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            number = int(item)
        except ValueError:
            logger.warning("%s contains a non-integer '%s'; skipping", var_name, item)
            continue
        if number < 0:
            logger.warning("%s contains negative value %s; skipping", var_name, number)
            continue
        result.append(number)
    if not result:
        logger.warning("%s provided no valid values; using default %s", var_name, default)
        return default
    return sorted(set(result))


EXPIRATION_NOTIFICATION_ENABLED = os.getenv("EXPIRATION_NOTIFICATION_ENABLED", "true").lower() == "true"
EXPIRATION_NOTIFICATION_DAYS = max(1, _safe_int(os.getenv("EXPIRATION_NOTIFICATION_DAYS", "3"), 3))
_exp_time = os.getenv("EXPIRATION_NOTIFICATION_TIME", "09:00")
(
    EXPIRATION_NOTIFICATION_HOUR,
    EXPIRATION_NOTIFICATION_MINUTE,
) = _parse_time_pair(_exp_time)
EXPIRATION_NOTIFICATION_TZ = os.getenv("EXPIRATION_NOTIFICATION_TZ", "UTC")

# Conversation states
MAIN_MENU, USER_MENU, NODE_MENU, STATS_MENU, HOST_MENU, INBOUND_MENU = range(6)
SELECTING_USER, WAITING_FOR_INPUT, CONFIRM_ACTION = range(6, 9)
EDIT_USER, EDIT_FIELD, EDIT_VALUE = range(9, 12)
CREATE_USER, CREATE_USER_FIELD = range(12, 14)
BULK_MENU, BULK_ACTION, BULK_CONFIRM = range(14, 17)
EDIT_NODE, EDIT_NODE_FIELD = range(17, 19)
EDIT_HOST, EDIT_HOST_FIELD = range(19, 21)
CREATE_NODE, NODE_NAME, NODE_ADDRESS, NODE_PORT, NODE_TLS, SELECT_INBOUNDS = range(21, 27)
CREATE_HOST, HOST_PROFILE, HOST_INBOUND, HOST_PARAMS = range(27, 31)
ADMIN_MENU_STATE, ADMIN_WAITING_INPUT = range(31, 33)

# User creation fields
USER_FIELDS = {
    'username': 'Имя пользователя',
    'trafficLimitBytes': 'Лимит трафика (в гигабайтах; 0 — безлимит)',
    'trafficLimitStrategy': 'Стратегия сброса трафика (NO_RESET, DAY, WEEK, MONTH)',
    'expireAt': 'Дата истечения (YYYY-MM-DD или форматы вроде 30d/2m)',
    'description': 'Описание',
    'telegramId': 'Telegram ID',
    'email': 'Email',
    'tag': 'Тег',
    'hwidDeviceLimit': 'Лимит устройств'
}

CREATE_USER_EXCLUDED_FIELDS = (
    "trafficLimitStrategy",
    "description",
    "telegramId",
    "email",
    "tag",
)
CREATE_USER_EXCLUDED_FIELDS_SET = {field for field in CREATE_USER_EXCLUDED_FIELDS if field in USER_FIELDS}
# Dashboard display settings - что показывать на главном экране
DASHBOARD_SHOW_SYSTEM_STATS = os.getenv("DASHBOARD_SHOW_SYSTEM_STATS", "true").lower() == "true"
DASHBOARD_SHOW_SERVER_INFO = os.getenv("DASHBOARD_SHOW_SERVER_INFO", "true").lower() == "true"
DASHBOARD_SHOW_USERS_COUNT = os.getenv("DASHBOARD_SHOW_USERS_COUNT", "true").lower() == "true"
DASHBOARD_SHOW_NODES_COUNT = os.getenv("DASHBOARD_SHOW_NODES_COUNT", "true").lower() == "true"
DASHBOARD_SHOW_TRAFFIC_STATS = os.getenv("DASHBOARD_SHOW_TRAFFIC_STATS", "true").lower() == "true"
DASHBOARD_SHOW_UPTIME = os.getenv("DASHBOARD_SHOW_UPTIME", "true").lower() == "true"

# Настройки поиска пользователей
ENABLE_PARTIAL_SEARCH = os.getenv("ENABLE_PARTIAL_SEARCH", "true").lower() == "true"
SEARCH_MIN_LENGTH = int(os.getenv("SEARCH_MIN_LENGTH", "2"))

# Предустановленные варианты лимита устройств для HWID
_device_limit_env = os.getenv("HWID_DEVICE_LIMIT_PRESETS", "1")
HWID_DEVICE_LIMIT_PRESETS = _parse_int_list(_device_limit_env, [1], "HWID_DEVICE_LIMIT_PRESETS")
logger.info("Device limit presets: %s", HWID_DEVICE_LIMIT_PRESETS)
