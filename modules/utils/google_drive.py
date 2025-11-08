import asyncio
import io
import logging
from typing import Optional, List

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from modules.config import (
    GOOGLE_SERVICE_ACCOUNT_FILE,
    GOOGLE_OAUTH_TOKEN_FILE,
    GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID,
)

logger = logging.getLogger(__name__)
_SCOPES = ["https://www.googleapis.com/auth/drive"]
_service = None


def _get_service():
    global _service
    if _service is not None:
        return _service
    creds = None
    if GOOGLE_SERVICE_ACCOUNT_FILE:
        try:
            creds = service_account.Credentials.from_service_account_file(
                GOOGLE_SERVICE_ACCOUNT_FILE,
                scopes=_SCOPES,
            )
        except Exception as exc:
            logger.error("Failed to init service account creds: %s", exc)
    elif GOOGLE_OAUTH_TOKEN_FILE:
        try:
            creds = OAuthCredentials.from_authorized_user_file(
                GOOGLE_OAUTH_TOKEN_FILE,
                scopes=_SCOPES,
            )
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(GOOGLE_OAUTH_TOKEN_FILE, "w") as token_file:
                    token_file.write(creds.to_json())
        except Exception as exc:
            logger.error("Failed to load OAuth credentials: %s", exc)

    if not creds:
        logger.warning("Google Drive credentials are not configured")
        return None

    _service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service


def _find_file(service, name: str):
    query_parts = [f"name='{name}'", "trashed=false"]
    if GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID:
        query_parts.append(f"'{GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID}' in parents")
    query = " and ".join(query_parts)
    result = service.files().list(q=query, fields="files(id)").execute()
    files = result.get("files", [])
    return files[0] if files else None


def _write_user_file_sync(filename: str, content: str) -> None:
    service = _get_service()
    if not service:
        return
    try:
        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
        existing = _find_file(service, filename)
        if existing:
            service.files().update(fileId=existing["id"], media_body=media).execute()
        else:
            metadata = {"name": filename}
            if GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID:
                metadata["parents"] = [GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID]
            service.files().create(body=metadata, media_body=media).execute()
        logger.info("Stored subscription links in file %s", filename)
    except Exception as exc:
        logger.error("Failed to store subscription file %s: %s", filename, exc)


async def store_subscription_links(username: Optional[str], short_uuid: Optional[str], links: List[str]) -> None:
    if (
            not GOOGLE_DRIVE_SUBSCRIPTIONS_FOLDER_ID
            or not (GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_OAUTH_TOKEN_FILE)
    ):
        logger.debug("Google Drive folder not configured; skipping write")
        return

    filename = f"{username or 'user'}-{short_uuid or ''}".strip("-")
    if not filename:
        filename = "subscription"
    filename += ".txt"
    content = "\n".join(links)

    await asyncio.to_thread(_write_user_file_sync, filename, content)
