"""Lightweight Appwrite helper for the Flet app.

This module initializes an Appwrite Databases client from environment
variables and provides helpers to list/create message documents.

Environment variables used:
- APPWRITE_ENDPOINT (e.g. https://YOUR_APPWRITE_HOST/v1)
- APPWRITE_PROJECT  (your Appwrite project ID)
- APPWRITE_API_KEY  (API key with permissions for the DB)
- APPWRITE_DATABASE_ID (optional; used by helpers if provided)
- APPWRITE_COLLECTION_ID (optional; used by helpers if provided)

Security note: Do not commit secrets to source control. Prefer setting
these environment variables in your local environment or CI secrets.
"""
import os
from typing import Optional, List, Dict

try:
    from appwrite.client import Client
    from appwrite.services.databases import Databases
except Exception:
    Client = None
    Databases = None


def _init_db() -> Optional[Databases]:
    """Initialize and return an Appwrite Databases service or None if not configured."""
    endpoint = os.getenv("APPWRITE_ENDPOINT")
    project = os.getenv("APPWRITE_PROJECT")
    api_key = os.getenv("APPWRITE_API_KEY")

    if not Client or not Databases:
        # appwrite SDK not installed
        return None

    if not endpoint or not project or not api_key:
        return None

    client = Client()
    client.set_endpoint(endpoint).set_project(project).set_key(api_key)
    return Databases(client)


# Single shared DB client (or None)
_db = _init_db()


def is_configured() -> bool:
    return _db is not None


def get_messages(database_id: Optional[str] = None,
                 collection_id: Optional[str] = None,
                 limit: int = 50) -> List[Dict]:
    """Return up to `limit` message documents from Appwrite.

    Returns a list of dicts normalized to contain: id, sender, text, createdAt, pinned.
    Raises RuntimeError when not configured.
    """
    if _db is None:
        raise RuntimeError("Appwrite client not configured. Set APPWRITE_ENDPOINT, APPWRITE_PROJECT and APPWRITE_API_KEY environment variables and install the 'appwrite' package.")

    database_id = database_id or os.getenv("APPWRITE_DATABASE_ID")
    collection_id = collection_id or os.getenv("APPWRITE_COLLECTION_ID")
    if not database_id or not collection_id:
        raise ValueError("database_id and collection_id are required (either pass them or set APPWRITE_DATABASE_ID and APPWRITE_COLLECTION_ID)")

    resp = _db.list_documents(database_id=database_id, collection_id=collection_id, limit=limit)
    # Appwrite response contains 'documents'
    docs = resp.get('documents') if isinstance(resp, dict) else getattr(resp, 'documents', None)
    if docs is None:
        # Best effort: return empty list
        return []

    messages = []
    for d in docs:
        messages.append({
            'id': d.get('$id') or d.get('id'),
            'sender': d.get('sender') or d.get('from') or d.get('username') or 'Unknown',
            'text': d.get('text') or d.get('message') or '',
            'timestamp': d.get('createdAt') or d.get('time') or '',
            'pinned': d.get('pinned', False),
        })

    return messages


def create_message(payload: Dict, database_id: Optional[str] = None, collection_id: Optional[str] = None) -> Dict:
    """Create a message document in Appwrite and return the created document.

    Expects payload to be a dict with at least 'text' and optional 'sender' keys.
    """
    if _db is None:
        raise RuntimeError("Appwrite client not configured")

    database_id = database_id or os.getenv("APPWRITE_DATABASE_ID")
    collection_id = collection_id or os.getenv("APPWRITE_COLLECTION_ID")
    if not database_id or not collection_id:
        raise ValueError("database_id and collection_id are required (either pass them or set APPWRITE_DATABASE_ID and APPWRITE_COLLECTION_ID)")

    # Use random id generation by Appwrite by passing None as document_id
    res = _db.create_document(database_id=database_id, collection_id=collection_id, document_id='unique()', data=payload)
    return res
