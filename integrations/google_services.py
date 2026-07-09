"""
IMQ2 Google Services
Shared OAuth client factory for all Google APIs.
Uses the same credentials/token as Gmail — the token is extended with
additional scopes when setup_gmail_oauth.py is re-run after adding them
to the OAuth consent screen.

Supported services:
  gmail     — Gmail API v1
  youtube   — YouTube Data API v3
  drive     — Google Drive API v3
  sheets    — Google Sheets API v4
  docs      — Google Docs API v1
  calendar  — Google Calendar API v3
"""

import logging
from pathlib import Path

log = logging.getLogger(__name__)

CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials" / "gmail_credentials.json"
TOKEN_FILE       = Path(__file__).parent.parent / "credentials" / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/calendar",
]

SERVICE_CONFIGS = {
    "gmail":    ("gmail",    "v1"),
    "youtube":  ("youtube",  "v3"),
    "drive":    ("drive",    "v3"),
    "sheets":   ("sheets",   "v4"),
    "docs":     ("docs",     "v1"),
    "calendar": ("calendar", "v3"),
}


def get_google_service(service_name: str):
    """
    Return an authenticated Google API service object.
    Loads token from disk and auto-refreshes if expired.
    Raises ValueError for unknown service names.
    Raises FileNotFoundError if credentials.json is missing.
    Raises RuntimeError if the required scope wasn't granted.
    """
    if service_name not in SERVICE_CONFIGS:
        raise ValueError(f"Unknown service '{service_name}'. Valid: {list(SERVICE_CONFIGS)}")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            log.info("Google token refreshed.")
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {CREDENTIALS_FILE}."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            log.info("Google OAuth flow completed.")
        TOKEN_FILE.write_text(creds.to_json())

    api_name, version = SERVICE_CONFIGS[service_name]
    return build(api_name, version, credentials=creds)
