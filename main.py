import os
import logging
import sys
from dotenv import load_dotenv

# Setup logging first, before any other imports
def setup_logging():
    """Setup logging configuration from environment variables"""
    # Load environment variables first
    load_dotenv()
    
    # Get log level from environment variable
    log_level = os.getenv("LOG_LEVEL", "ERROR").upper()
    
    # Map string log levels to logging constants
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    
    # Set the log level, default to ERROR if invalid level provided
    level = log_levels.get(log_level, logging.ERROR)
    
    # Configure basic logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=level,
        force=True,  # Override any existing logging configuration
        handlers=[
            logging.StreamHandler()  # Ensure logs go to stdout
        ]
    )
    
    # Also configure a custom handler to ensure logs go to stdout
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(sys.stdout)
    
    # Force flush to ensure logs are written
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Test logging configuration
    test_logger = logging.getLogger('test')
    test_logger.info("Logging system initialized successfully")
    test_logger.warning("Warning logging enabled")
    test_logger.error("Error logging enabled")
    
    # Force flush again
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Configure telegram library logging
    # For production (ERROR), disable telegram debug logs
    # For development (DEBUG/INFO), allow telegram logs
    if level <= logging.INFO:
        logging.getLogger('telegram').setLevel(logging.INFO)
        logging.getLogger('telegram.ext').setLevel(logging.INFO)
    else:
        logging.getLogger('telegram').setLevel(logging.ERROR)
        logging.getLogger('telegram.ext').setLevel(logging.ERROR)
    
    return level

# Setup logging immediately
current_log_level = setup_logging()
logger = logging.getLogger(__name__)

# Test logging configuration
logger.info("Logging system initialized")
logger.debug("Debug logging enabled")
logger.warning("Warning logging enabled")
logger.error("Error logging enabled")

# Force flush to ensure logs are written
sys.stdout.flush()
sys.stderr.flush()

from telegram import BotCommand
from telegram.ext import Application, MessageHandler, CallbackQueryHandler, filters, JobQueue

# Import modules
from modules.handlers.core.conversation import create_conversation_handler
from modules import localization  # noqa: F401 - ensure localization patches are loaded
from modules.utils import admin_store
from modules.services.expiration_notifier import schedule_expiration_notifications


async def _set_bot_commands(application: Application):
    commands = [
        BotCommand("start", "Открыть главное меню"),
    ]
    await application.bot.set_my_commands(commands)


def main():
    # Load environment variables
    load_dotenv()
    
    logger.info("Starting RemnaWave Telegram Bot...")
    
    # Check if required environment variables are set
    api_token = os.getenv("REMNAWAVE_API_TOKEN")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    superadmin_user_ids = [int(id) for id in os.getenv("SUPER_ADMIN_USER_IDS", os.getenv("SUPERADMIN_USER_IDS", "")).split(",") if id]

    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'unknown')}")
    logger.info(f"Log level: {os.getenv('LOG_LEVEL', 'ERROR')}")
    logger.info(f"Super admin user IDs: {superadmin_user_ids}")
    
    # Force flush to ensure logs are written
    sys.stdout.flush()
    sys.stderr.flush()

    if not api_token:
        logger.error("Configure REMNAWAVE_API_TOKEN to allow the bot to access the panel API")
        return

    logger.info("Using API token authentication for Remnawave API")

    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is not set")
        return

    if not superadmin_user_ids:
        logger.error("SUPER_ADMIN_USER_IDS environment variable is not set. Configure at least one superadmin.")
        return
    
    # Ensure admin database initialized
    admin_store.init_db()
    logger.info("Loaded %d admins from database", len(admin_store.list_admins()))
    # Create the Application
    logger.info("Creating Telegram Application...")
    job_queue = JobQueue()
    application = (
        Application.builder()
        .token(bot_token)
        .post_init(_set_bot_commands)
        .job_queue(job_queue)
        .build()
    )
    logger.info("Telegram Application created successfully")
    
    # Cache cleanup will be handled automatically by the cache TTL mechanism
    logger.info("Cache system initialized")
    
    # Create and add conversation handler
    logger.info("Creating conversation handler...")
    conv_handler = create_conversation_handler()
    application.add_handler(conv_handler, group=0)
    logger.info("Conversation handler added successfully")
    
    # Schedule daily expiration notifications
    schedule_expiration_notifications(application)
    
    import signal
    import time

    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        _shutdown = True

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Run polling with retry logic
    max_retries = 10
    retry_count = 0

    while retry_count < max_retries:
        if _shutdown:
            logger.info("Shutdown requested, exiting.")
            break
        try:
            logger.info(f"Starting bot polling (attempt {retry_count + 1}/{max_retries})")
            application.run_polling(
                poll_interval=0.5,
                timeout=30,
                bootstrap_retries=5,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30,
                drop_pending_updates=True
            )
            # run_polling() exits cleanly on SIGTERM — no retry needed
            break
        except (KeyboardInterrupt, SystemExit):
            logger.info("Received shutdown signal, stopping.")
            break
        except RuntimeError as e:
            if "event loop is closed" in str(e).lower():
                logger.info("Event loop closed during shutdown, stopping.")
                break
            retry_count += 1
            logger.error(f"RuntimeError during polling (attempt {retry_count}/{max_retries}): {e}")
        except Exception as e:
            retry_count += 1
            logger.error(f"Error during polling (attempt {retry_count}/{max_retries}): {e}")
            logger.error(f"Exception type: {type(e).__name__}")

            if retry_count >= max_retries:
                logger.error(f"Max retries reached. Bot failed to start after {max_retries} attempts.")
                raise

            wait_time = min(30 * retry_count, 300)
            logger.info(f"Waiting {wait_time} seconds before retry...")
            try:
                time.sleep(wait_time)
            except (KeyboardInterrupt, SystemExit):
                logger.info("Interrupted during wait, stopping.")
                break

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        pass  # Graceful shutdown
    except Exception as e:
        logger.error(f"Critical error in main: {e}", exc_info=True)
