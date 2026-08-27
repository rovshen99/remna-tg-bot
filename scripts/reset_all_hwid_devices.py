"""Bulk-reset HWID devices for every user on the panel.

Usage:
    python scripts/reset_all_hwid_devices.py            # dry run — only reports what would happen
    python scripts/reset_all_hwid_devices.py --execute   # actually deletes the devices

Reads API_BASE_URL / REMNAWAVE_API_TOKEN from .env, same as the bot itself.
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

load_dotenv()

from modules.api.users import UserAPI  # noqa: E402

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("reset_all_hwid")


async def run(execute: bool, delay: float) -> None:
    logger.info("Fetching all HWID devices from the panel...")
    devices = await UserAPI.get_all_hwid_devices()

    users_to_reset = {}
    for device in devices:
        user_uuid = device.get("userUuid")
        if not user_uuid:
            continue
        users_to_reset.setdefault(user_uuid, 0)
        users_to_reset[user_uuid] += 1

    if not users_to_reset:
        logger.info("No HWID devices found on the panel. Nothing to do.")
        return

    logger.info(
        "Found %d devices across %d users.",
        len(devices),
        len(users_to_reset),
    )

    if not execute:
        logger.info("Dry run — no changes made. Re-run with --execute to actually reset devices.")
        for user_uuid, count in users_to_reset.items():
            logger.info("  would reset %d device(s) for user %s", count, user_uuid)
        return

    total_users = len(users_to_reset)
    processed = 0
    total_deleted = 0
    failures = []

    for user_uuid, expected_count in users_to_reset.items():
        processed += 1
        summary = await UserAPI.delete_all_user_hwid_devices(user_uuid)
        deleted = summary.get("deleted", 0)
        total_deleted += deleted

        if deleted != expected_count:
            failures.append((user_uuid, expected_count, deleted))
            logger.warning(
                "[%d/%d] user %s: expected %d, deleted %d",
                processed, total_users, user_uuid, expected_count, deleted,
            )
        else:
            logger.info(
                "[%d/%d] user %s: deleted %d device(s)",
                processed, total_users, user_uuid, deleted,
            )

        if delay:
            await asyncio.sleep(delay)

    logger.info("Done. Reset %d device(s) across %d/%d users.", total_deleted, total_users - len(failures), total_users)
    if failures:
        logger.warning("%d user(s) had a mismatch between expected and deleted device count:", len(failures))
        for user_uuid, expected_count, deleted in failures:
            logger.warning("  %s: expected %d, deleted %d", user_uuid, expected_count, deleted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete the devices. Without this flag, the script only reports what it would do.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds to wait between users when --execute is set (default: 0.2).",
    )
    args = parser.parse_args()

    if args.execute:
        confirmation = input(
            "This will remove ALL HWID devices for EVERY user on the panel. "
            "Type 'RESET ALL' to continue: "
        )
        if confirmation != "RESET ALL":
            logger.info("Confirmation not given, aborting.")
            return

    asyncio.run(run(execute=args.execute, delay=args.delay))


if __name__ == "__main__":
    main()
