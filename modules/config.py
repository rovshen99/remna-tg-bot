import os
from typing import Optional

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


def _resolve_path(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value if os.path.isabs(value) else os.path.join(ROOT_DIR, value)


GOOGLE_SERVICE_ACCOUNT_FILE = _resolve_path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE"))
GOOGLE_OAUTH_TOKEN_FILE = _resolve_path(os.getenv("GOOGLE_OAUTH_TOKEN_FILE"))
GOOGLE_OAUTH_CLIENT_SECRET_FILE = _resolve_path(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE"))

GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID = os.getenv("GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID")

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
    'expireAt': 'Дата истечения (YYYY-MM-DD)',
    'description': 'Описание',
    'telegramId': 'Telegram ID',
    'email': 'Email',
    'tag': 'Тег',
    'hwidDeviceLimit': 'Лимит устройств'
}
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
