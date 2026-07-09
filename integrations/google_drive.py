"""
IMQ2 Google Drive Integration
Upload files, create folders, list contents, and get shareable links.
"""

import logging
import mimetypes
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def upload_file(local_path: str, filename: Optional[str] = None,
                folder_id: Optional[str] = None,
                share: bool = True) -> dict:
    """
    Upload a local file to Drive. Returns dict with id, name, url.
    If share=True, makes the file viewable by anyone with the link.
    """
    from googleapiclient.http import MediaFileUpload
    from integrations.google_services import get_google_service
    drive = get_google_service("drive")

    path     = Path(local_path)
    name     = filename or path.name
    mimetype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    metadata = {"name": name}
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(path), mimetype=mimetype, resumable=True)
    file  = drive.files().create(
        body=metadata, media_body=media, fields="id,name,webViewLink"
    ).execute()

    file_id = file["id"]

    if share:
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

    url = file.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
    log.info(f"Uploaded '{name}' to Drive ({file_id})")
    return {"id": file_id, "name": name, "url": url}


def create_folder(name: str, parent_id: Optional[str] = None) -> dict:
    """Create a folder in Drive. Returns dict with id and name."""
    from integrations.google_services import get_google_service
    drive = get_google_service("drive")

    metadata = {
        "name":     name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = drive.files().create(body=metadata, fields="id,name").execute()
    log.info(f"Created Drive folder '{name}' ({folder['id']})")
    return {"id": folder["id"], "name": name}


def list_files(folder_id: Optional[str] = None,
               max_results: int = 20) -> list[dict]:
    """List files in Drive root or a specific folder."""
    from integrations.google_services import get_google_service
    drive = get_google_service("drive")

    q = f"'{folder_id}' in parents" if folder_id else "'root' in parents"
    q += " and trashed = false"

    resp = drive.files().list(
        q=q, pageSize=max_results,
        fields="files(id,name,mimeType,webViewLink,modifiedTime)"
    ).execute()

    files = resp.get("files", [])
    log.info(f"Listed {len(files)} files from Drive")
    return files


def get_or_create_folder(name: str, parent_id: Optional[str] = None) -> str:
    """Return folder ID, creating it if it doesn't exist."""
    from integrations.google_services import get_google_service
    drive = get_google_service("drive")

    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"

    resp = drive.files().list(q=q, fields="files(id)").execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    return create_folder(name, parent_id)["id"]


def upload_text(content: str, filename: str,
                folder_id: Optional[str] = None,
                share: bool = True) -> dict:
    """Upload a string as a text file to Drive."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix=Path(filename).suffix,
                                     delete=False, encoding='utf-8') as f:
        f.write(content)
        tmp_path = f.name
    try:
        return upload_file(tmp_path, filename=filename,
                           folder_id=folder_id, share=share)
    finally:
        os.unlink(tmp_path)
