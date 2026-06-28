"""
IMQ2 Google Docs Integration
Create documents, append content, and get shareable links.
Useful for Q2 to write reports, notes, summaries, or any long-form content.
"""

import logging
from typing import Optional

log = logging.getLogger(__name__)


def create_document(title: str, content: str = "",
                    share: bool = True) -> dict:
    """
    Create a new Google Doc with optional initial content.
    Returns dict with id, url, title.
    """
    from integrations.google_services import get_google_service
    docs  = get_google_service("docs")
    drive = get_google_service("drive")

    doc = docs.documents().create(
        body={"title": title}
    ).execute()

    doc_id = doc["documentId"]
    url    = f"https://docs.google.com/document/d/{doc_id}/edit"

    if content:
        _append_text(docs, doc_id, content)

    if share:
        drive.permissions().create(
            fileId=doc_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

    log.info(f"Created doc '{title}' ({doc_id})")
    return {"id": doc_id, "url": url, "title": title}


def append_to_document(doc_id: str, content: str) -> bool:
    """Append text to an existing document."""
    from integrations.google_services import get_google_service
    docs = get_google_service("docs")

    try:
        _append_text(docs, doc_id, content)
        log.info(f"Appended {len(content)} chars to doc {doc_id}")
        return True
    except Exception as e:
        log.error(f"Failed to append to doc {doc_id}: {e}")
        return False


def get_document_text(doc_id: str) -> str:
    """Read the plain text content of a document."""
    from integrations.google_services import get_google_service
    docs = get_google_service("docs")

    doc  = docs.documents().get(documentId=doc_id).execute()
    body = doc.get("body", {}).get("content", [])

    text = []
    for block in body:
        para = block.get("paragraph", {})
        for el in para.get("elements", []):
            t = el.get("textRun", {}).get("content", "")
            if t:
                text.append(t)

    return "".join(text)


def _append_text(docs_service, doc_id: str, text: str):
    """Internal: append plain text to a document via batchUpdate."""
    # Get current end index
    doc       = docs_service.documents().get(documentId=doc_id).execute()
    end_index = doc["body"]["content"][-1]["endIndex"] - 1

    requests = [{
        "insertText": {
            "location": {"index": end_index},
            "text":     text if text.endswith("\n") else text + "\n",
        }
    }]
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()
