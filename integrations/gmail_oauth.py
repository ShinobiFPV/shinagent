"""
IMQ2 Gmail OAuth Integration
Handles Gmail send and receive via Google's OAuth 2.0 API.
Replaces the old SMTP/IMAP approach which Google deprecated for personal
accounts in January 2025.

First run: call authenticate() which opens a browser for the one-time
auth flow and saves ~/imq2/credentials/gmail_token.json. All subsequent
calls use the saved token and auto-refresh it silently.
"""

import base64
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger(__name__)

CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials" / "gmail_credentials.json"
TOKEN_FILE       = Path(__file__).parent.parent / "credentials" / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",  # needed to mark messages read
]


def get_service():
    """
    Return an authenticated Gmail API service object.
    Loads token from disk; if missing or expired, runs the browser auth flow.
    """
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
            log.info("Gmail token refreshed.")
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {CREDENTIALS_FILE}. "
                    "Download from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
            log.info("Gmail OAuth flow completed.")

        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
        log.info(f"Gmail token saved to {TOKEN_FILE}")

    return build("gmail", "v1", credentials=creds)


def send_email(to: str, subject: str, body: str,
               from_name: str = "Q2 (IMQ2)",
               attachments: list[str] = None) -> str:
    """
    Send an email from your-agent-email@gmail.com via the Gmail API.
    attachments: optional list of local file paths to attach.
    """
    import mimetypes
    from email.mime.base import MIMEBase
    from email import encoders

    try:
        service = get_service()
        msg = MIMEMultipart()
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Attach files if provided
        for file_path in (attachments or []):
            path = Path(file_path)
            if not path.exists():
                log.warning(f"Attachment not found, skipping: {file_path}")
                continue
            mime_type, _ = mimetypes.guess_type(str(path))
            main_type, sub_type = (mime_type or "application/octet-stream").split("/", 1)
            with open(path, "rb") as f:
                part = MIMEBase(main_type, sub_type)
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)
            log.info(f"Attached: {path.name}")

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        attach_note = f" with {len(attachments)} attachment(s)" if attachments else ""
        log.info(f"Email sent to {to}: {subject}{attach_note}")
        return f"Email sent to {to}{attach_note}."
    except Exception as e:
        log.error(f"send_email error: {e}")
        return f"[send_email] Error: {e}"


def check_inbox(max_messages: int = 5) -> list[dict]:
    """
    Return recent unread messages as a list of dicts with keys:
    id, subject, sender, date, snippet, body_preview.
    """
    try:
        service = get_service()

        results = service.users().messages().list(
            userId="me", q="is:unread", maxResults=max_messages
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return []

        out = []
        for m in messages:
            msg = service.users().messages().get(
                userId="me", id=m["id"], format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("Subject", "(no subject)")
            sender  = headers.get("From", "")
            date    = headers.get("Date", "")
            snippet = msg.get("snippet", "")

            # Extract plain text body
            body = _extract_body(msg["payload"])

            out.append({
                "id":           m["id"],
                "subject":      subject,
                "sender":       sender,
                "date":         date,
                "snippet":      snippet,
                "body_preview": body[:400],
            })

        return out
    except Exception as e:
        log.error(f"check_inbox error: {e}")
        return []


def forward_message(message_id: str, to: str) -> str:
    """Forward a Gmail message by ID to the given address."""
    try:
        service = get_service()
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender  = headers.get("From", "")
        body    = _extract_body(msg["payload"])

        fwd_subject = f"Fwd: {subject}" if not subject.startswith("Fwd:") else subject
        fwd_body    = (
            f"---------- Forwarded message ----------\n"
            f"From: {sender}\nSubject: {subject}\n\n{body}"
        )

        return send_email(to=to, subject=fwd_subject, body=fwd_body)
    except Exception as e:
        log.error(f"forward_message error: {e}")
        return f"[forward_message] Error: {e}"


def _extract_body(payload: dict, max_chars: int = 2000) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data", "")

    if mime == "text/plain" and data:
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")[:max_chars]
        except Exception:
            pass

    for part in payload.get("parts", []):
        result = _extract_body(part, max_chars)
        if result:
            return result

    return ""
