# main.py
import flet as ft
try:
    import emoji as emoji_lib  # emoji library for emoji picker
except ImportError:
    emoji_lib = None
try:
    from PIL import Image
except ImportError:
    Image = None

# Backwards-compat aliases: some older code uses ft.colors / ft.icons or
# libraries that expect these attributes. Ensure both exist and point to
# the canonical names available in this Flet installation.
try:
    ft.colors = ft.Colors
except Exception:
    pass
try:
    ft.icons = ft.Icons
except Exception:
    pass
import requests
import json
import os
import secrets
import time
import threading
import asyncio
from typing import Optional
import datetime
import traceback
import re
import smtplib
from io import BytesIO
import mimetypes
import base64
from bg_stub import create_background_control, create_background_stack, apply_pattern_to_control
# --- Appwrite integration (inlined) ---------------------------------
try:
    from appwrite.client import Client as _AppwriteClient
    from appwrite.services.databases import Databases as _AppwriteDatabases
    from appwrite.services.storage import Storage as _AppwriteStorage
except Exception:
    _AppwriteClient = None
    _AppwriteDatabases = None
    _AppwriteStorage = None

# Hardcoded Appwrite defaults (falls back to env vars if present).
# The endpoint and API key were provided by the user and are embedded
# here per their request. For better security, prefer setting these as
# environment variables instead of hardcoding.
APPWRITE_ENDPOINT_DEFAULT = "https://nyc.cloud.appwrite.io/v1"
APPWRITE_API_KEY_DEFAULT = "standard_3fdc9fb7f17d2b427c90e8c6c2e5ee34fa375e26a49fdb1be6c6b663318c4e3a14f748c6e16c510ce975d70314454137c4ebece802417e9c82d5a6eb8a94983b489cccd8bc57339f87779ae4b9649eda7c92c353b58716fa05f570370324ba61aa08f3dd43e78cb4618edb3d285c8adbdd9287d140bcc928e8879860b2922f24"
APPWRITE_PROJECT_DEFAULT = "69022dc400325c342455"
APPWRITE_DATABASE_ID_DEFAULT = "clc_chat_db"
APPWRITE_COLLECTION_ID_DEFAULT = "messages"
APPWRITE_STORAGE_BUCKET_DEFAULT = "691ac029001de1d4b4bd"  # default storage bucket id provided by user

# Small emoji list for the picker
EMOJI_LIST = [
    "😀", "😂", "😊", "😍", "👍", "🔥", "🎉", "❤️", "😮", "🙌", "🙏", "🤝", "😅", "😉", "😎"
]

class _InlineAppwriteClient:
    def __init__(self):
        self._db = None
        self._init_db()

    def _init_db(self):
        endpoint = os.getenv("APPWRITE_ENDPOINT") or APPWRITE_ENDPOINT_DEFAULT
        project = os.getenv("APPWRITE_PROJECT") or APPWRITE_PROJECT_DEFAULT
        api_key = os.getenv("APPWRITE_API_KEY") or APPWRITE_API_KEY_DEFAULT
        if not _AppwriteClient or not _AppwriteDatabases:
            self._db = None
            return
        if not endpoint or not project or not api_key:
            self._db = None
            return
        client = _AppwriteClient()
        client.set_endpoint(endpoint).set_project(project).set_key(api_key)
        try:
            self._db = _AppwriteDatabases(client)
            # storage client if available
            try:
                if _AppwriteStorage:
                    self._storage = _AppwriteStorage(client)
                else:
                    self._storage = None
            except Exception:
                self._storage = None
        except Exception:
            self._db = None

    def upload_file(self, local_path: str, bucket_id: Optional[str] = None):
        """Upload a local file to Appwrite Storage and return file metadata or None."""
        # Prefer REST multipart upload to avoid SDK file-object compatibility issues.
        bucket = bucket_id or os.getenv('APPWRITE_STORAGE_BUCKET') or APPWRITE_STORAGE_BUCKET_DEFAULT
        if not bucket:
            raise ValueError('Storage bucket id not provided')

        endpoint = os.getenv('APPWRITE_ENDPOINT') or APPWRITE_ENDPOINT_DEFAULT
        api_key = os.getenv('APPWRITE_API_KEY') or APPWRITE_API_KEY_DEFAULT
        project = os.getenv('APPWRITE_PROJECT') or APPWRITE_PROJECT_DEFAULT

        upload_url = f"{endpoint.rstrip('/')}/storage/buckets/{bucket}/files"
        headers = {
            'X-Appwrite-Project': project,
            'X-Appwrite-Key': api_key,
        }

        try:
            with open(local_path, 'rb') as fh:
                files = {'file': (os.path.basename(local_path), fh)}
                # Appwrite requires a fileId form field (use 'unique()' to let Appwrite generate one)
                data = {'fileId': 'unique()'}
                resp = requests.post(upload_url, headers=headers, files=files, data=data, timeout=60)

            try:
                resp.raise_for_status()
            except Exception:
                # Return None on failure but log the body for debugging
                try:
                    print('Appwrite upload failed:', resp.status_code, resp.text)
                except Exception:
                    pass
                return None

            try:
                return resp.json()
            except Exception:
                return None

        except Exception as e:
            # Last-resort: try SDK method if available
            try:
                if hasattr(self, '_storage') and self._storage is not None:
                    with open(local_path, 'rb') as f:
                        return self._storage.create_file(bucket_id=bucket, file_id='unique()', file=f)
            except Exception:
                pass
            print('Upload file error:', e)
            return None

    def is_configured(self) -> bool:
        return self._db is not None

    def get_messages(self, database_id: Optional[str] = None,
                     collection_id: Optional[str] = None,
                     limit: int = 50):
        if self._db is None:
            raise RuntimeError("Appwrite client not configured")
        database_id = database_id or os.getenv("APPWRITE_DATABASE_ID") or APPWRITE_DATABASE_ID_DEFAULT
        collection_id = collection_id or os.getenv("APPWRITE_COLLECTION_ID") or APPWRITE_COLLECTION_ID_DEFAULT
        if not database_id or not collection_id:
            raise ValueError("database_id and collection_id required")
        try:
            # Try with limit parameter first (newer SDK)
            resp = self._db.list_documents(database_id=database_id, collection_id=collection_id, limit=limit)
        except TypeError:
            # Fallback if limit not supported
            resp = self._db.list_documents(database_id=database_id, collection_id=collection_id)
        
        docs = resp.get('documents') if isinstance(resp, dict) else getattr(resp, 'documents', None)
        if docs is None:
            return []
        messages = []
        for d in docs:
            messages.append({
                'id': d.get('$id') or d.get('id'),
                'sender': d.get('sender') or d.get('sender_name') or d.get('from') or d.get('username') or 'Unknown',
                'text': d.get('text') or d.get('message') or d.get('content') or '',
                'timestamp': d.get('createdAt') or d.get('time') or '',
                'pinned': d.get('pinned', False),
            })
        return messages

    def create_message(self, payload: dict, database_id: Optional[str] = None, collection_id: Optional[str] = None):
        if self._db is None:
            raise RuntimeError("Appwrite client not configured")
        database_id = database_id or os.getenv("APPWRITE_DATABASE_ID") or APPWRITE_DATABASE_ID_DEFAULT
        collection_id = collection_id or os.getenv("APPWRITE_COLLECTION_ID") or APPWRITE_COLLECTION_ID_DEFAULT
        if not database_id or not collection_id:
            raise ValueError("database_id and collection_id required")
        return self._db.create_document(database_id=database_id, collection_id=collection_id, document_id='unique()', data=payload)

    def update_document(self, document_id: str, data: dict, database_id: Optional[str] = None, collection_id: Optional[str] = None):
        """Update a document in Appwrite (databases.update_document wrapper).
        Returns the updated document or raises on failure.
        """
        if self._db is None:
            raise RuntimeError("Appwrite client not configured")
        database_id = database_id or os.getenv("APPWRITE_DATABASE_ID") or APPWRITE_DATABASE_ID_DEFAULT
        collection_id = collection_id or os.getenv("APPWRITE_COLLECTION_ID") or APPWRITE_COLLECTION_ID_DEFAULT
        if not database_id or not collection_id:
            raise ValueError("database_id and collection_id required")
        try:
            # Newer SDK uses update_document(database_id, collection_id, document_id, data)
            return self._db.update_document(database_id=database_id, collection_id=collection_id, document_id=document_id, data=data)
        except TypeError:
            # Fallback if signature differs
            return self._db.update_document(database_id=database_id, collection_id=collection_id, document_id=document_id, data=data)

# single instance used by the rest of the file
appwrite_client = _InlineAppwriteClient()
# --------------------------------------------------------------------
BREVO_API_KEY = "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"
# ---------- CONFIG ----------
BASE_PATH = r"C:\Users\victor\Desktop\learn flet"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SENDER = "michael.mutemi16@gmail.com"
EMAIL_PASSWORD = "pcbylmtgeaetcoto"

BACKGROUND_PATHS = [
    fr"{BASE_PATH}\background.png",
    fr"{BASE_PATH}\background2.png",
    fr"{BASE_PATH}\background3.png",
    fr"{BASE_PATH}\background4.png",
    fr"{BASE_PATH}\background5.png",
]
LOGO_PATH = fr"{BASE_PATH}\logo.svg"

FIREBASE_DB_URL: Optional[str] = None
SERVICE_ACCOUNT_PATH = None
# Try to auto-detect a service account JSON in the project folder
for _f in os.listdir(BASE_PATH):
    if _f.lower().endswith('.json') and ("firebase" in _f.lower() or "adminsdk" in _f.lower() or "service" in _f.lower() or "clc" in _f.lower()):
        SERVICE_ACCOUNT_PATH = os.path.join(BASE_PATH, _f)
        break

# Firebase admin availability flag
#FIREBASE_ADMIN_AVAILABLE = False


# Vibrant color palette
PALETTE = {
    "primary": "#6366F1",      # Vibrant indigo
    "secondary": "#10B981",    # Emerald green
    "accent": "#F59E0B",       # Amber
    "danger": "#EF4444",       # Red
    "background": "#0F172A",   # Dark blue-gray
    "surface": "#1E293B",      # Lighter blue-gray
    "on_surface": "#F1F5F9",   # Light gray text
    "success": "#22C55E",      # Green
    "warning": "#F97316"       # Orange (warning)
}

# Sample lists
KENYAN_TERTIARY = [
    "University of Nairobi",
    "Kenyatta University", 
    "Moi University",
    "Jomo Kenyatta University of Agriculture and Technology",
    "Egerton University",
    "Maseno University",
    "Technical University of Kenya",
    "Technical University of Mombasa",
    "Dedan Kimathi University of Technology",
    "Meru University of Science and Technology",
    "Chuka University",
    "Karatina University",
    "Kirinyaga University",
    "Laikipia University",
    "Maasai Mara University",
    "Machakos University",
    "Murang'a University of Technology",
    "Multimedia University of Kenya",
    "Kisii University",
    "Pwani University",
    "Rongo University",
    "South Eastern Kenya University",
    "University of Eldoret",
    "Jaramogi Oginga Odinga University of Science and Technology",
    "Co-operative University of Kenya",
    "Garissa University",
    "Taita Taveta University",
    "Kibabii University",
    "University of Kabianga",
    "Strathmore University",
    "United States International University Africa",
    "Catholic University of Eastern Africa",
    "Daystar University",
    "Africa Nazarene University",
    "Scott Christian University",
    "Kenya Methodist University",
    "St. Paul's University",
    "Mount Kenya University",
    "Kabarak University",
    "KCA University",
    "Pan Africa Christian University",
    "Adventist University of Africa",
    "Great Lakes University of Kisumu",
    "Presbyterian University of East Africa",
    "Uzima University",
    "Tangaza University College",
    "Riara University",
    "Zetech University",
    "Kenya Technical Trainers College",
    "Kenya Institute of Development Studies",
    "Kenya School of Government",
    "Kenya Medical Training College",
    "Kenya Polytechnic University College",
    "The Kenya Polytechnic",
    "Nairobi Technical Training Institute",
    "Mombasa Technical Training Institute",
    "Kisumu National Polytechnic",
    "Eldoret National Polytechnic",
    "Meru National Polytechnic",
    "Nyeri National Polytechnic",
    "Thika Technical Training Institute",
    "Kitale National Polytechnic",
    "Kenyatta University Teachers College",
    "Nairobi Teachers College",
    "Mombasa Teachers College",
    "Kisumu Teachers College",
    "Eldoret Teachers College",
    "Machakos Teachers College",
    "Aga Khan University",
    "Kenya Medical Training College (Various Campuses)",
    "International Medical and Technological University",
    "Kenya Institute of Management",
    "Kenya Institute of Mass Communication",
    "Kenya School of Law",
    "Kenya Institute of Special Education",
    "Kenya Institute of Administration",
    "St. Paul's United Theological College",
    "Scott Theological College",
    "Nairobi Evangelical Graduate School of Theology",
    "Bukura Agricultural College",
    "Siriba Agricultural College",
    "Embu Agricultural College"
]

KENYAN_COUNTIES = [
    "Mombasa", 
    "Kwale",
    "Kilifi",
    "Tana River",
    "Lamu",
    "Taita-Taveta",
    "Garissa",
    "Wajir",
    "Mandera",
    "Marsabit",
    "Isiolo",
    "Meru",
    "Tharaka-Nithi",
    "Embu",
    "Kitui",
    "Machakos",
    "Makueni",
    "Nyandarua",
    "Nyeri",
    "Kirinyaga",
    "Murang'a",
    "Kiambu",
    "Turkana",
    "West Pokot",
    "Samburu",
    "Trans Nzoia",
    "Uasin Gishu",
    "Elgeyo-Marakwet",
    "Nandi",
    "Baringo",
    "Laikipia",
    "Nakuru",
    "Narok",
    "Kajiado",
    "Kericho",
    "Bomet",
    "Kakamega",
    "Vihiga",
    "Bungoma",
    "Busia",
    "Siaya",
    "Kisumu",
    "Homa Bay",
    "Migori",
    "Kisii",
    "Nyamira",
    "Nairobi"
]

# CLC Kenya regional structure for categorizing users and recipients
CLC_REGIONS = {
    "Nairobi": {
        "counties": ["Nairobi"],
        "institutions": [
            "University of Nairobi",
            "Kenyatta University",
            "Strathmore University",
            "Catholic University of Eastern Africa",
            "Daystar University",
            "Africa Nazarene University",
            "Technical University of Kenya",
            "Kenya Medical Training College (Nairobi)",
            "Nairobi Technical Training Institute",
            "Kenya Institute of Mass Communication",
            "Kenya School of Government",
            "Jomo Kenyatta University of Agriculture and Technology (Nairobi CBD)",
            "Inoorero University",
            "Tangaza University College",
            "Regis University",
            "Uzima University",
            "Marist International University College",
            "Hezekiah University"
        ]
    },
    "Central": {
        "counties": ["Kiambu", "Murang'a", "Nyandarua", "Nyeri", "Kirinyaga"],
        "institutions": [
            "Jomo Kenyatta University of Agriculture and Technology",
            "Karatina University",
            "Dedan Kimathi University of Technology",
            "Murang'a University of Technology",
            "Kenyatta University (Ruiru Campus)",
            "Mount Kenya University (Main Campus)",
            "Presbyterian University of East Africa",
            "Africa International University",
            "Thika Technical Training Institute",
            "Nyeri Technical Training Institute",
            "Kenya Medical Training College (Nyeri)",
            "Kenya Institute of Highways and Building Technology",
            "Kagumo Teachers College",
            "Kenya Science Teachers College",
            "Kiambu Institute of Science and Technology"
        ]
    },
    "Rift Valley": {
        "counties": ["Nakuru", "Uasin Gishu", "Baringo", "Bomet", "Elgeyo-Marakwet", "Kajiado", "Kericho", "Laikipia", "Nandi", "Narok", "Samburu", "Trans Nzoia", "Turkana", "West Pokot"],
        "institutions": [
            "Moi University",
            "Egerton University",
            "University of Eldoret",
            "Laikipia University",
            "Maasai Mara University",
            "Kabarak University",
            "Kenya Methodist University",
            "Rift Valley Technical Training Institute",
            "Kenya Medical Training College (Nakuru)",
            "Kenya Institute of Management (Nakuru)",
            "Molo Technical Training Institute",
            "Eldoret Technical Training Institute",
            "Kitale Technical Training Institute",
            "Kapsabet Technical Training Institute",
            "Narok Teachers College",
            "Baringo Technical College",
            "Kericho Teachers College"
        ]
    },
    "Western": {
        "counties": ["Kakamega", "Bungoma", "Busia", "Vihiga"],
        "institutions": [
            "Masinde Muliro University of Science and Technology",
            "Kibabii University",
            "University of Eastern Africa, Baraton",
            "Alupe University College",
            "Friends College Kaimosi",
            "Kenya Medical Training College (Kakamega)",
            "Sangalo Institute of Science and Technology",
            "Bungoma Technical Training Institute",
            "Busia Technical Training Institute",
            "Vihiga Technical and Vocational College"
        ]
    },
    "Nyanza": {
        "counties": ["Kisumu", "Kisii", "Homa Bay", "Migori", "Nyamira", "Siaya"],
        "institutions": [
            "Kisii University",
            "Jaramogi Oginga Odinga University of Science and Technology",
            "Rongo University",
            "Tom Mboya University College",
            "Great Lakes University of Kisumu",
            "Kenya Medical Training College (Kisumu)",
            "Kisumu National Polytechnic",
            "Kisii National Polytechnic",
            "Siaya Institute of Technology",
            "Nyamira Technical Training Institute",
            "Homa Bay Technical Training Institute",
            "Migori Teachers College"
        ]
    },
    "Coast": {
        "counties": ["Mombasa", "Kilifi", "Kwale", "Lamu", "Taita Taveta", "Tana River"],
        "institutions": [
            "Technical University of Mombasa",
            "Pwani University",
            "University of Nairobi (Mombasa Campus)",
            "Kenya Methodist University (Mombasa Campus)",
            "Mount Kenya University (Mombasa Campus)",
            "Bandari College",
            "Kenya Medical Training College (Mombasa)",
            "Mombasa Technical Training Institute",
            "Shanzu Teachers College",
            "Taita Taveta University",
            "Coast Institute of Technology"
        ]
    },
    "Eastern": {
        "counties": ["Machakos", "Makueni", "Kitui", "Embu", "Meru", "Tharaka-Nithi"],
        "institutions": [
            "Kenyatta University (Machakos Campus)",
            "Machakos University",
            "South Eastern Kenya University",
            "University of Embu",
            "Chuka University",
            "Meru University of Science and Technology",
            "Kenya Medical Training College (Machakos)",
            "Machakos Technical Training Institute",
            "Embu Technical Training Institute",
            "Meru Technical Training Institute",
            "Kitui Technical Training Institute"
        ]
    },
    "North Eastern": {
        "counties": ["Garissa", "Mandera", "Wajir"],
        "institutions": [
            "Garissa University",
            "Kenya Medical Training College (Garissa)",
            "Garissa Technical Training Institute",
            "Wajir Technical Training Institute",
            "Mandera Technical Training Institute"
        ]
    }
}

# Helper function to get user's region based on their institution or county
def get_user_region(user_data: dict) -> str:
    """Determine region from user's institution or county. Returns region name or 'Unknown'."""
    institution = user_data.get("extra", "")
    county = user_data.get("county", "")
    
    for region, data in CLC_REGIONS.items():
        if institution in data.get("institutions", []):
            return region
        if county in data.get("counties", []):
            return region
    return "Unknown"

# Helper function to get all users in a specific region
def get_users_in_region(region: str, all_users: dict) -> dict:
    """Filter users by region. Returns dict of users in that region."""
    if region not in CLC_REGIONS:
        return {}
    
    region_data = CLC_REGIONS[region]
    filtered = {}
    for uid, user in all_users.items():
        user_region = get_user_region(user)
        if user_region == region:
            filtered[uid] = user
    return filtered

# ---------------- Firebase helper ----------------
def _init_firebase_admin_if_possible():
    """Attempt to initialize firebase-admin using the detected service account.
    Returns True if initialization succeeded.
    """
    global FIREBASE_ADMIN_AVAILABLE, FIREBASE_DB_URL
    if not SERVICE_ACCOUNT_PATH or not os.path.exists(SERVICE_ACCOUNT_PATH):
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials, db
        # Read project_id from service account to derive DB URL if needed
        with open(SERVICE_ACCOUNT_PATH, 'r', encoding='utf-8') as f:
            info = json.load(f)
        proj = info.get('project_id')
        # Try a few common Realtime Database hostnames. Newer projects use -default-rtdb format.
        candidates = []
        if proj:
            candidates = [
                f"https://{proj}-default-rtdb.firebaseio.com",
                f"https://{proj}.firebaseio.com",
                f"https://{proj}.firebasedatabase.app",
            ]

        # Prefer an explicitly configured FIREBASE_DB_URL, otherwise pick the
        # first candidate that doesn't return 404 (or accept 200/401/403).
        db_url = FIREBASE_DB_URL
        if not db_url and candidates:
            for c in candidates:
                try:
                    test_url = c.rstrip('/') + '/.json'
                    resp = requests.get(test_url, timeout=4)
                    # Accept anything but 404 Not Found as an indication the
                    # database endpoint exists (401/403 means protected but exists).
                    if resp.status_code != 404:
                        db_url = c
                        break
                except Exception:
                    continue
        # Fallback to the first candidate if none responded
        if not db_url and candidates:
            db_url = candidates[0]
        cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
        if db_url:
            firebase_admin.initialize_app(cred, { 'databaseURL': db_url })
            FIREBASE_DB_URL = db_url
        else:
            firebase_admin.initialize_app(cred)
        FIREBASE_ADMIN_AVAILABLE = True
        return True
    except Exception:
        FIREBASE_ADMIN_AVAILABLE = False
        return False


def fetch_all_users():
    """Fetch all users from Firebase. Uses firebase-admin if available, otherwise falls back to REST.
    Returns a dict of users keyed by uid.
    """
    if FIREBASE_ADMIN_AVAILABLE:
        from firebase_admin import db
        ref = db.reference('/users')
        data = ref.get()
        return data or {}
    if not FIREBASE_DB_URL:
        raise RuntimeError("FIREBASE_DB_URL not configured.")
    url = FIREBASE_DB_URL.rstrip('/') + '/users.json'
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json() or {}


def post_user(user_data: dict):
    """Create a new user in Firebase. Returns the created record info (REST returns {'name': id})."""
    print(f"post_user: FIREBASE_ADMIN_AVAILABLE={globals().get('FIREBASE_ADMIN_AVAILABLE')}, FIREBASE_DB_URL={globals().get('FIREBASE_DB_URL')}")
    if FIREBASE_ADMIN_AVAILABLE:
        try:
            from firebase_admin import db
            ref = db.reference('/users')
            new_ref = ref.push(user_data)
            print(f"post_user: created user via admin with key={new_ref.key}")
            return {'name': new_ref.key}
        except Exception as ex:
            print(f"post_user: admin push failed: {ex}")
            # Fall through to REST fallback
    # fallback to REST
    if not FIREBASE_DB_URL:
        raise RuntimeError("FIREBASE_DB_URL not configured.")
    url = FIREBASE_DB_URL.rstrip('/') + '/users.json'
    try:
        resp = requests.post(url, json=user_data, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as ex:
        print(f"post_user: REST post to {url} failed: {ex}")
        raise


def update_user_password(user_id: str, new_password: str):
    """Update a user's password by uid. Uses admin SDK when present, otherwise REST PATCH."""
    if FIREBASE_ADMIN_AVAILABLE:
        from firebase_admin import db
        ref = db.reference(f'/users/{user_id}')
        ref.update({'password': new_password})
        return True
    if not FIREBASE_DB_URL:
        raise RuntimeError("FIREBASE_DB_URL not configured.")
    update_url = f"{FIREBASE_DB_URL.rstrip('/')}/users/{user_id}/password.json"
    update_resp = requests.patch(update_url, json=new_password, timeout=10)
    update_resp.raise_for_status()
    return True


def save_to_firebase(user_data: dict):
    """Compatibility wrapper for creating users."""
    # Before creating the user, check if there are any existing users.
    # If there are none, mark this user as the first admin.
    try:
        users = fetch_all_users()
        # fetch_all_users returns a dict keyed by uid; treat empty as no users
        if not users:
            try:
                user_data = dict(user_data) if user_data is not None else {}
                user_data.setdefault('is_admin', True)
                print("save_to_firebase: no existing users found — marking first user as admin")
            except Exception:
                pass
    except Exception as ex:
        # If we can't fetch users (e.g., no DB), proceed without setting admin
        print(f"save_to_firebase: could not check existing users: {ex}")

    return post_user(user_data)

# ============ Registration Screen Class ============
class Registration:
    """Encapsulates registration screen logic and components."""
    
    def __init__(self, page: ft.Page, app: "CLCKenyaApp" = None, on_registration_complete=None):
        self.page = page
        self.app = app
        self.palette = PALETTE
        self.firebase_url = FIREBASE_DB_URL
        self.on_registration_complete = on_registration_complete
        
        # Initialize UI components
        self.name_field = None
        self.email_field = None
        self.phone_field = None
        self.date_row = None
        self.role_dropdown = None
        self.password_field = None
        self.register_btn = None
        self.dynamic_field_holder = None
        self.form_container = None
        self.form_overlay = None
        self.form_column = None
        # Simple on-screen debug area (keeps a few recent lines)
        self._debug_lines = []
        self.debug_text = ft.Text(
            "",
            size=12,
            text_align=ft.TextAlign.LEFT,
            color=ft.Colors.WHITE,
        )
    
    def _get_page_width(self, default=800):
        """Safely get page width, handling various Flet version attributes."""
        try:
            return self.page.window_width or self.page.width or default
        except AttributeError:
            return getattr(self.page, 'width', None) or default
    
    def append_debug(self, message: str):
        """Append a debug line to the on-screen debug area and print to console."""
        try:
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            line = f"[{ts}] {message}"
            print(line)
            self._debug_lines.append(line)
            # keep last 6 lines
            self._debug_lines = self._debug_lines[-6:]
            self.debug_text.value = "\n".join(self._debug_lines)
            try:
                self.page.update()
            except Exception:
                pass
        except Exception:
            pass
    
    def create_date_picker_field(self):
        """Create date picker dropdowns."""
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        
        current_year = datetime.datetime.now().year
        years = list(range(current_year - 70, current_year - 15))
        
        month_dd = ft.Dropdown(
            label="Month",
            expand=True,
            options=[ft.dropdown.Option(month) for month in months],
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            text_size=12,
        )
        
        day_dd = ft.Dropdown(
            label="Day",
            expand=True,
            options=[ft.dropdown.Option(f"{i:02d}") for i in range(1, 32)],
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            text_size=12,
        )
        
        year_dd = ft.Dropdown(
            label="Year",
            expand=True,
            options=[ft.dropdown.Option(str(year)) for year in years],
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            text_size=12,
        )
        
        return month_dd, day_dd, year_dd
    
    def create_text_field(self, label, hint_text, **kwargs):
        """Create a styled text field."""
        return ft.TextField(
            label=label,
            hint_text=hint_text,
            expand=True,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            label_style=ft.TextStyle(color=self.palette["on_surface"]),
            hint_style=ft.TextStyle(color=ft.Colors.WHITE54),
            text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
            cursor_color=self.palette["accent"],
            **kwargs
        )
    
    def validate_name(self, e):
        """Validate name field (min 3 characters)."""
        value = self.name_field.value.strip()
        if len(value) < 3 and len(value) > 0:
            self.name_field.border_color = self.palette["danger"]
            self.name_field.error_text = "Name must be at least 3 characters"
        elif len(value) > 100:
            self.name_field.border_color = self.palette["danger"]
            self.name_field.error_text = "Name must not exceed 100 characters"
        else:
            self.name_field.border_color = self.palette["primary"]
            self.name_field.error_text = ""
        self.page.update()

    # ---------- OTP / Email helpers ----------
    @staticmethod
    def generate_otp(digits: int = 6) -> str:
        """Generate a cryptographically secure numeric OTP of given digits."""
        range_max = 10 ** digits
        code = str(secrets.randbelow(range_max)).zfill(digits)
        return code

    @staticmethod
    def send_otp_email(to_email: str, otp_code: str) -> bool:
        """Send OTP email. Try Brevo API first; if it fails, fall back to Gmail SMTP using
        the SMTP credentials configured at the top of this file.

        Returns True on success, False otherwise.
        """
        # Attempt to get Brevo API key (env -> brevo_key.txt -> hard-coded)
        api_key = os.environ.get("BREVO_API_KEY")
        if not api_key:
            try:
                key_file = os.path.join(BASE_PATH, "brevo_key.txt")
                if os.path.exists(key_file):
                    with open(key_file, "r", encoding="utf-8") as kf:
                        candidate = kf.read().strip()
                        if candidate:
                            api_key = candidate
            except Exception:
                api_key = None

        if not api_key:
            api_key = globals().get("BREVO_API_KEY")

        # Prepare message content
        subject = "Your CLC KENYA verification code"
        html_body = f"<html><body><p>Your verification code is: <strong>{otp_code}</strong></p><p>This code will expire in 10 minutes.</p></body></html>"
        text_body = f"Your verification code is: {otp_code}. It will expire in 10 minutes."

        # Try Brevo API first when key is available
        if api_key:
            try:
                url = "https://api.brevo.com/v3/smtp/email"
                headers = {
                    "accept": "application/json",
                    "content-type": "application/json",
                    "api-key": api_key,
                }

                payload = {
                    "sender": {"name": "CLC KENYA", "email": "no-reply@clckenya.org"},
                    "to": [{"email": to_email}],
                    "subject": subject,
                    "htmlContent": html_body,
                    "textContent": text_body,
                }

                resp = requests.post(url, headers=headers, json=payload, timeout=10)
                if resp.status_code in (200, 201, 202):
                    print("send_otp_email: sent via Brevo")
                    return True
                else:
                    print(f"Brevo send failed ({resp.status_code}): {resp.text}")
            except Exception as ex:
                print(f"Error sending OTP via Brevo: {ex}")

        # If we reach here, Brevo either had no key or failed -> attempt SMTP fallback
        try:
            # Ensure SMTP credentials exist
            if not (globals().get("SMTP_SERVER") and globals().get("SMTP_PORT") and globals().get("EMAIL_SENDER") and globals().get("EMAIL_PASSWORD")):
                print("SMTP credentials not configured - cannot send via SMTP fallback.")
                return False

            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = EMAIL_SENDER
            msg["To"] = to_email
            msg.set_content(text_body)
            msg.add_alternative(html_body, subtype="html")

            # Connect and send via Gmail SMTP
            smtp_host = SMTP_SERVER
            smtp_port = SMTP_PORT
            print(f"send_otp_email: attempting SMTP fallback via {smtp_host}:{smtp_port} for {to_email}")
            with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(EMAIL_SENDER, EMAIL_PASSWORD)
                smtp.send_message(msg)

            print("send_otp_email: sent via SMTP fallback (Gmail)")
            return True
        except Exception as ex2:
            print(f"SMTP fallback failed: {ex2}")
            return False
    
    def validate_email(self, e):
        """Validate email field."""
        import re
        value = self.email_field.value.strip()
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        
        if not value:
            self.email_field.border_color = self.palette["primary"]
            self.email_field.error_text = ""
        elif not re.match(email_pattern, value):
            self.email_field.border_color = self.palette["danger"]
            self.email_field.error_text = "Please enter a valid email address"
        else:
            self.email_field.border_color = self.palette["success"]
            self.email_field.error_text = "✓ Valid email"
        self.page.update()
    
    def validate_phone(self, e):
        """Validate phone number (basic format check)."""
        import re
        value = self.phone_field.value.strip()
        # Allow formats: +254XXXXXXXXX, 07XXXXXXXXX, 01XXXXXXXXX
        phone_pattern = r'^(\+254|0)[17]\d{8}$'
        
        if not value:
            self.phone_field.border_color = self.palette["primary"]
            self.phone_field.error_text = ""
        elif not re.match(phone_pattern, value):
            self.phone_field.border_color = self.palette["danger"]
            self.phone_field.error_text = "Use format: +254XXXXXXXXX or 07XXXXXXXXX"
        else:
            self.phone_field.border_color = self.palette["success"]
            self.phone_field.error_text = "✓ Valid phone"
        self.page.update()
    
    def validate_password(self, e):
        """Validate password strength."""
        value = self.password_field.value
        errors = []
        
        if len(value) < 8:
            errors.append("At least 8 characters")
        if not any(c.isupper() for c in value):
            errors.append("One uppercase letter")
        if not any(c.islower() for c in value):
            errors.append("One lowercase letter")
        if not any(c.isdigit() for c in value):
            errors.append("One number")
        if not any(c in "!@#$%^&*" for c in value):
            errors.append("One special character (!@#$%^&*)")
        
        if not value:
            self.password_field.border_color = self.palette["primary"]
            self.password_field.error_text = ""
        elif errors:
            self.password_field.border_color = self.palette["danger"]
            self.password_field.error_text = f"Need: {', '.join(errors)}"
        else:
            self.password_field.border_color = self.palette["success"]
            self.password_field.error_text = "✓ Strong password"
        self.page.update()

    
    def build(self):
        """Build and return the registration screen."""
        # Title with gradient
        title = ft.ShaderMask(
            content=ft.Text(
                "CLC KENYA",
                size=38,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.WHITE,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[self.palette["primary"], self.palette["secondary"]],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )

        # Logo container with plain SVG image (no pulsing animation), larger size
        logo_container = ft.Container(
            width=220,
            height=160,
            bgcolor=ft.Colors.with_opacity(0.1, self.palette["primary"]),
            border_radius=20,
            alignment=ft.alignment.center,
            content=ft.Image(
                src=LOGO_PATH,
                width=160,
                height=140,
                fit=ft.ImageFit.CONTAIN,
            ),
            shadow=ft.BoxShadow(
                blur_radius=15,
                color=ft.Colors.with_opacity(0.3, self.palette["primary"]),
            ),
        )

        # Description
        desc = ft.Text(
            "In all things to love and to serve",
            size=18,
            color=self.palette["accent"],
            weight=ft.FontWeight.W_500,
            text_align=ft.TextAlign.CENTER,
        )

        # Form fields
        self.name_field = self.create_text_field("Full name", "e.g. Jane Doe", autofocus=True)
        self.name_field.on_change = self.validate_name
        
        self.email_field = self.create_text_field("Email address", "you@example.com")
        self.email_field.on_change = self.validate_email
        
        self.phone_field = self.create_text_field("Phone number", "+2547XXXXXXXX", keyboard_type=ft.KeyboardType.PHONE)
        self.phone_field.on_change = self.validate_phone

        # Date picker
        month_dd, day_dd, year_dd = self.create_date_picker_field()
        self.date_row = ft.Row(
            controls=[month_dd, day_dd, year_dd],
            alignment=ft.MainAxisAlignment.SPACE_EVENLY,
            spacing=2,
            wrap=False,
        )

        # Role dropdown
        self.role_dropdown = ft.Dropdown(
            label="Select role",
            expand=True,
            options=[
                ft.dropdown.Option("Student"),
                ft.dropdown.Option("Non-student")
            ],
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            label_style=ft.TextStyle(color=self.palette["on_surface"]),
            text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
        )

        # Dynamic field holder
        self.dynamic_field_holder = ft.Container(content=ft.Text(""), padding=0)

        # Password field
        self.password_field = self.create_text_field(
            "Password",
            "Choose a strong password",
            password=True,
            can_reveal_password=True
        )
        self.password_field.on_change = self.validate_password

        # Register button
        self.register_btn = ft.ElevatedButton(
            "Create Account",
            width=220,
            height=50,
            bgcolor=self.palette["secondary"],
            color=ft.Colors.WHITE,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation=8,
                shadow_color=ft.Colors.with_opacity(0.3, self.palette["secondary"]),
            ),
            icon=ft.Icons.PERSON_ADD,
            icon_color=ft.Colors.WHITE,
        )

        # Login link
        login_link = ft.TextButton(
            "Already have an account? Login here",
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["primary"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )

        # Form column
        self.form_column = ft.Column(
            controls=[
                title,
                ft.Container(height=10),
                logo_container,
                ft.Container(height=15),
                desc,
                ft.Container(height=25),
                ft.Container(content=self.name_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=self.email_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=self.phone_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Text("Date of Birth", color=self.palette["on_surface"], size=14, weight=ft.FontWeight.W_500),
                ft.Container(content=self.date_row, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=self.role_dropdown, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=self.dynamic_field_holder, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=self.password_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=25),
                ft.Container(
                    content=self.register_btn,
                    alignment=ft.alignment.center
                ),
                ft.Container(height=15),
                ft.Container(
                    content=login_link,
                    alignment=ft.alignment.center
                )
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.ADAPTIVE,
        )

        # Form container
        self.form_container = ft.Container(
            content=self.form_column,
            padding=30,
            bgcolor=ft.Colors.with_opacity(0.85, self.palette["surface"]),
            border_radius=24,
            shadow=ft.BoxShadow(
                blur_radius=50,
                color=ft.Colors.with_opacity(0.3, self.palette["background"]),
            ),
            margin=ft.margin.symmetric(horizontal=20),
        )

        # Form overlay
        self.form_overlay = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.25, self.palette["secondary"]),
            border_radius=24,
        )

        # Form stack
        form_stack = ft.Stack(
            expand=False,
            controls=[
                self.form_overlay,
                self.form_container,
            ],
        )

        # List view
        list_view = ft.ListView(
            controls=[form_stack],
            padding=20,
            spacing=10,
            auto_scroll=False,
            expand=False,
        )

        

        # Main content wrapped with background placeholder (background created by stub)
        inner_content = ft.Container(
            expand=False,
            content=ft.Column(
                controls=[
                    ft.Container(
                        expand=False,
                        bgcolor=ft.Colors.with_opacity(0.35, self.palette["background"]),
                        content=ft.Container(expand=False),
                    ),
                    ft.Container(
                        expand=False,
                        alignment=ft.alignment.center,
                        content=list_view,
                    ),
                ]
            )
        )

        main_content = create_background_stack(inner_content, page=self.page, padding=12)

        # Layout updater
        def _update_layout(e=None):
            try:
                win_w = self._get_page_width(800)
                if win_w < 600:
                    target_w = max(300, int(win_w * 0.9))
                    field_padding = 15
                else:
                    target_w = max(400, int(win_w * 0.6))
                    field_padding = 30

                self.form_container.width = target_w
                self.form_overlay.width = target_w + 20
                self.form_overlay.padding = 10

                # On small screens make input fields occupy 95% of the form width
                input_w = int(target_w * 0.95) if win_w < 600 else None
                if input_w:
                    for fld in (self.name_field, self.email_field, self.phone_field, self.password_field):
                        try:
                            fld.width = input_w
                        except Exception:
                            pass
                    # If dynamic field exists and is a control, try to set its width too
                    try:
                        dyn = self.dynamic_field_holder.content
                        if hasattr(dyn, 'width'):
                            dyn.width = input_w
                    except Exception:
                        pass

                self.page.update()
            except Exception:
                pass

        self.page.on_resize = _update_layout
        _update_layout()

        # (logo animation removed - logo is now a static larger image)

        # Register button click handler
        def on_register(e):
            """Handle registration form submission."""
            # Collect date of birth from dropdowns
            try:
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                month_dd = self.date_row.controls[0]
                day_dd = self.date_row.controls[1]
                year_dd = self.date_row.controls[2]
                month_number = months.index(month_dd.value) + 1
                dob_value = f"{year_dd.value}-{month_number:02d}-{day_dd.value}"
            except Exception:
                dob_value = None

            # Get dynamic field value
            dynamic_ctrl = self.dynamic_field_holder.content
            dynamic_value = dynamic_ctrl.value if hasattr(dynamic_ctrl, 'value') else None

            user = {
                "name": self.name_field.value,
                "email": self.email_field.value,
                "phone": self.phone_field.value,
                "dob": dob_value,
                "role": self.role_dropdown.value,
                "extra": dynamic_value,
                "password": self.password_field.value,
                "timestamp": datetime.datetime.now().isoformat()
            }

            if not all([user["name"], user["email"], user["password"]]):
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("Please fill all required fields", color=ft.Colors.WHITE),
                    bgcolor=self.palette["danger"]
                )
                self.page.snack_bar.open = True
                self.page.update()
                return

            # Generate and send OTP
            otp = Registration.generate_otp(6)
            try:
                self.append_debug(f"Generated OTP: {otp}")
            except Exception:
                pass
            sent = Registration.send_otp_email(user["email"], otp)
            if sent:
                try:
                    self.append_debug(f"OTP sent to {user['email']}")
                except Exception:
                    pass
            else:
                try:
                    self.append_debug(f"Failed to send OTP to {user['email']} - using local fallback")
                except Exception:
                    pass
                try:
                    self.page.snack_bar = ft.SnackBar(
                        content=ft.Text("Warning: OTP delivery failed; using local fallback.", color=ft.Colors.WHITE),
                        bgcolor=self.palette["accent"]
                    )
                    self.page.snack_bar.open = True
                    self.page.update()
                except Exception:
                    pass
            
            # Store OTP and pending user
            OTPScreen.store_otp(user["email"], otp, user)
            try:
                self.append_debug("OTP stored and pending_user saved")
            except Exception:
                pass
            
            # Callback: runs when OTP is verified
            def on_verified_callback(verified_user):
                try:
                    self.append_debug("on_verified_callback: invoked")
                except Exception:
                    pass
                if self.firebase_url:
                    try:
                        try:
                            self.append_debug("Saving verified user to Firebase...")
                        except Exception:
                            pass
                        try:
                            print(f"on_verified_callback: FIREBASE_ADMIN_AVAILABLE={globals().get('FIREBASE_ADMIN_AVAILABLE')}, FIREBASE_DB_URL={globals().get('FIREBASE_DB_URL')}")
                        except Exception:
                            pass
                        res = save_to_firebase(verified_user)
                        self.page.snack_bar = ft.SnackBar(
                            content=ft.Text("🎉 Registration successful! Redirecting to login...", color=ft.Colors.WHITE),
                            bgcolor=self.palette["success"]
                        )
                        self.page.snack_bar.open = True
                        self.page.update()
                        try:
                            self.append_debug(f"Saved to Firebase: {res}")
                        except Exception:
                            pass
                        import threading
                        threading.Timer(2.0, lambda: self.on_registration_complete() if self.on_registration_complete else None).start()
                    except Exception as ex:
                        try:
                            self.append_debug(f"Registration save failed: {ex}")
                        except Exception:
                            pass
                        self.page.snack_bar = ft.SnackBar(
                            content=ft.Text(f"Registration failed: {str(ex)}", color=ft.Colors.WHITE),
                            bgcolor=self.palette["danger"]
                        )
                        self.page.snack_bar.open = True
                else:
                    try:
                        self.append_debug("Firebase not configured - registration prepared locally")
                    except Exception:
                        pass
                    self.page.snack_bar = ft.SnackBar(
                        content=ft.Text("✅ Registration data prepared (Firebase not configured). Redirecting...", color=ft.Colors.WHITE),
                        bgcolor=self.palette["success"]
                    )
                    self.page.snack_bar.open = True
                    self.page.update()
                    import threading
                    threading.Timer(2.0, lambda: self.on_registration_complete() if self.on_registration_complete else None).start()
            
            # Callback: runs when user goes back
            def on_back_callback():
                self.page.clean()
                registration = Registration(self.page, app=self.app)
                try:
                    if self.app:
                        self.page.add(self.app._wrap_with_global_background(registration.build()))
                    else:
                        self.page.add(registration.build())
                except Exception:
                    self.page.add(registration.build())
            
            # Show OTP screen
            try:
                self.append_debug("Showing OTP screen to user")
            except Exception:
                pass
            self.page.clean()
            otp_screen = OTPScreen(self.page, user["email"], user, on_verified_callback, on_back_callback, app=self.app)
            try:
                if self.app:
                    self.page.add(self.app._wrap_with_global_background(otp_screen.build()))
                else:
                    self.page.add(otp_screen.build())
            except Exception:
                self.page.add(otp_screen.build())

        # Dynamic field logic
        def set_dynamic_field(e):
            role_value = self.role_dropdown.value
            if role_value == "Student":
                opts = [ft.dropdown.Option(s) for s in KENYAN_TERTIARY]
                new_ctrl = ft.Dropdown(
                    label="Select Institution",
                    expand=True,
                    options=opts,
                    border_color=self.palette["primary"],
                    focused_border_color=self.palette["accent"],
                    label_style=ft.TextStyle(color=self.palette["on_surface"]),
                    text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
                )
            else:
                opts = [ft.dropdown.Option(c) for c in KENYAN_COUNTIES]
                new_ctrl = ft.Dropdown(
                    label="Current county of residence", 
                    expand=True,
                    options=opts,
                    border_color=self.palette["primary"],
                    focused_border_color=self.palette["accent"],
                    label_style=ft.TextStyle(color=self.palette["on_surface"]),
                    text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
                )

            self.dynamic_field_holder.content = new_ctrl
            self.page.update()
            try:
                months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                month_number = months.index(month_dd.value) + 1
                dob_value = f"{year_dd.value}-{month_number:02d}-{day_dd.value}"
            except Exception:
                # If parsing fails, set dob to None
                dob_value = None

            dynamic_ctrl = self.dynamic_field_holder.content
            dynamic_value = dynamic_ctrl.value if hasattr(dynamic_ctrl, 'value') else None

            user = {
                "name": self.name_field.value,
                "email": self.email_field.value,
                "phone": self.phone_field.value,
                "dob": dob_value,
                "role": self.role_dropdown.value,
                "extra": dynamic_value,
                "password": self.password_field.value,
                "timestamp": datetime.datetime.now().isoformat()
            }

            if not all([user["name"], user["email"], user["password"]]):
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text("Please fill all required fields", color=ft.Colors.WHITE),
                    bgcolor=self.palette["danger"]
                )
                self.page.snack_bar.open = True
                self.page.update()
                return

            # Generate and send OTP
            # Generate and send OTP
            otp = Registration.generate_otp(6)
            try:
                self.append_debug(f"Generated OTP: {otp}")
            except Exception:
                pass
            sent = Registration.send_otp_email(user["email"], otp)
            if sent:
                try:
                    self.append_debug(f"OTP sent to {user['email']}")
                except Exception:
                    pass
            else:
                # Log failure but continue with a local/dev fallback so verification
                # can proceed during development/testing when external email
                # services are unavailable.
                try:
                    self.append_debug(f"Failed to send OTP to {user['email']} - using local fallback")
                except Exception:
                    pass
                # Show a non-blocking warning to the user but continue flow
                try:
                    self.page.snack_bar = ft.SnackBar(
                        content=ft.Text("Warning: OTP delivery failed; using local fallback.", color=ft.Colors.WHITE),
                        bgcolor=self.palette["accent"]
                    )
                    self.page.snack_bar.open = True
                    self.page.update()
                except Exception:
                    pass
            
            # Store OTP and pending user
            OTPScreen.store_otp(user["email"], otp, user)
            try:
                self.append_debug("OTP stored and pending_user saved")
            except Exception:
                pass
            
            # Callback: runs when OTP is verified
            def on_verified_callback(verified_user):
                try:
                    self.append_debug("on_verified_callback: invoked")
                except Exception:
                    pass
                # Save to Firebase
                if self.firebase_url:
                    try:
                        try:
                            self.append_debug("Saving verified user to Firebase...")
                        except Exception:
                            pass
                        # Debug: print admin availability and DB URL before saving
                        try:
                            print(f"on_verified_callback: FIREBASE_ADMIN_AVAILABLE={globals().get('FIREBASE_ADMIN_AVAILABLE')}, FIREBASE_DB_URL={globals().get('FIREBASE_DB_URL')}")
                        except Exception:
                            pass
                        res = save_to_firebase(verified_user)
                        self.page.snack_bar = ft.SnackBar(
                            content=ft.Text("🎉 Registration successful! Redirecting to login...", color=ft.Colors.WHITE),
                            bgcolor=self.palette["success"]
                        )
                        self.page.snack_bar.open = True
                        self.page.update()
                        try:
                            self.append_debug(f"Saved to Firebase: {res}")
                        except Exception:
                            pass
                        # Call completion callback and navigate to login
                        import threading
                        threading.Timer(2.0, lambda: self.on_registration_complete() if self.on_registration_complete else None).start()
                    except Exception as ex:
                        try:
                            self.append_debug(f"Registration save failed: {ex}")
                        except Exception:
                            pass
                        self.page.snack_bar = ft.SnackBar(
                            content=ft.Text(f"Registration failed: {str(ex)}", color=ft.Colors.WHITE),
                            bgcolor=self.palette["danger"]
                        )
                        self.page.snack_bar.open = True
                else:
                    try:
                        self.append_debug("Firebase not configured - registration prepared locally")
                    except Exception:
                        pass
                    self.page.snack_bar = ft.SnackBar(
                        content=ft.Text("✅ Registration data prepared (Firebase not configured). Redirecting...", color=ft.Colors.WHITE),
                        bgcolor=self.palette["success"]
                    )
                    self.page.snack_bar.open = True
                    self.page.update()
                    # Call completion callback after delay
                    import threading
                    threading.Timer(2.0, lambda: self.on_registration_complete() if self.on_registration_complete else None).start()
            
            # Callback: runs when user goes back
            def on_back_callback():
                # Return to registration form
                self.page.clean()
                registration = Registration(self.page, app=self.app)
                try:
                    if self.app:
                        self.page.add(self.app._wrap_with_global_background(registration.build()))
                    else:
                        self.page.add(registration.build())
                except Exception:
                    self.page.add(registration.build())
            
            # Show OTP screen
            try:
                self.append_debug("Showing OTP screen to user")
            except Exception:
                pass
            self.page.clean()
            otp_screen = OTPScreen(self.page, user["email"], user, on_verified_callback, on_back_callback, app=self.app)
            try:
                if self.app:
                    self.page.add(self.app._wrap_with_global_background(otp_screen.build()))
                else:
                    self.page.add(otp_screen.build())
            except Exception:
                self.page.add(otp_screen.build())
        # Login link callback - navigate back to login screen
        def on_login_link_click(e):
            try:
                self.page.clean()
                if self.app:
                    self.page.add(self.app._wrap_with_global_background(self.app.login_screen.build()))
                else:
                    self.page.add(self.app.login_screen.build())
            except Exception as ex:
                print(f"on_login_link_click error: {ex}")

        # wire the button after defining the handler
        self.register_btn.on_click = on_register
        self.password_field.on_submit = on_register
        self.role_dropdown.on_change = set_dynamic_field
        login_link.on_click = on_login_link_click

        return main_content

# ========== End Registration Class ==========

# ============ OTP Verification Screen Class ============
class OTPScreen:
    """OTP verification screen with auto-navigation between boxes."""
    
    # Class-level store for OTP data (email -> {otp, timestamp, pending_user})
    _otp_store = {}
    
    def __init__(self, page: ft.Page, email: str, pending_user: dict, on_verified, on_back=None, app: "CLCKenyaApp" = None):
        self.page = page
        self.email = email
        self.pending_user = pending_user
        self.on_verified = on_verified
        self.on_back = on_back
        self.app = app
        self.palette = PALETTE
        
        # OTP storage and state
        self.otp_boxes = []
        self.entered_otp = ["", "", "", "", "", ""]
        self.correct_otp = None  # Will be set from OTPScreen._otp_store
        self.max_attempts = 3
        self.attempts = 0
        
        # UI components
        self.title = None
        self.email_display = None
        self.otp_container = None
        self.verify_btn = None
        self.resend_btn = None
        self.timer_text = None
        self.error_text = None
        self.countdown = 120  # 2 minutes
    
    def _get_page_width(self, default=800):
        """Safely get page width, handling various Flet version attributes."""
        try:
            return self.page.window_width or self.page.width or default
        except AttributeError:
            return getattr(self.page, 'width', None) or default
    
    @classmethod
    def store_otp(cls, email: str, otp_code: str, pending_user: dict = None):
        """Store OTP with timestamp and pending user data."""
        cls._otp_store[email] = {
            "otp": otp_code,
            "timestamp": time.time(),
            "pending_user": pending_user or {}
        }
    
    @classmethod
    def get_otp_data(cls, email: str):
        """Retrieve OTP data if it exists and hasn't expired."""
        data = cls._otp_store.get(email)
        if not data:
            return None
        
        elapsed = time.time() - data["timestamp"]
        if elapsed > 600:  # 10 minute expiry
            del cls._otp_store[email]
            return None
        
        return data
    
    def _mask_email(self, email: str) -> str:
        """Mask email for security (e.g., test@example.com -> te**@ex******.com)"""
        if "@" not in email:
            return email
            
        local_part, domain = email.split("@", 1)
        domain_parts = domain.split(".", 1)
        
        # Mask local part (keep first 2 chars)
        if len(local_part) > 2:
            masked_local = local_part[:2] + "*" * (len(local_part) - 2)
        else:
            masked_local = local_part[0] + "*" if len(local_part) > 1 else local_part
        
        # Mask domain (keep first 2 chars of main domain)
        if len(domain_parts) > 1:
            main_domain = domain_parts[0]
            if len(main_domain) > 2:
                masked_domain = main_domain[:2] + "*" * (len(main_domain) - 2) + "." + domain_parts[1]
            else:
                masked_domain = main_domain[0] + "*." + domain_parts[1] if len(main_domain) > 1 else main_domain + "." + domain_parts[1]
        else:
            masked_domain = domain
            
        return f"{masked_local}@{masked_domain}"
    
    def _create_otp_box(self, index: int) -> ft.TextField:
        """Create a single OTP input box."""
        return ft.TextField(
            width=50,
            height=60,
            text_align=ft.TextAlign.CENTER,
            text_size=24,
            max_length=1,
            content_padding=0,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            text_style=ft.TextStyle(
                color=self.palette["on_surface"],
                weight=ft.FontWeight.W_600
            ),
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=lambda e, idx=index: self._on_otp_change(e, idx),
            on_focus=lambda e, idx=index: self._on_otp_focus(e, idx),
        )
    
    def _on_otp_change(self, e, index: int):
        """Handle OTP box value change with auto-navigation."""
        value = e.control.value.strip()
        
        if value and value.isdigit():
            self.entered_otp[index] = value
            
            # Auto-focus next box if available
            if index < len(self.otp_boxes) - 1:
                try:
                    self.otp_boxes[index + 1].focus()
                except Exception:
                    pass
            else:
                # Last box filled, update page
                try:
                    self.page.update()
                except Exception:
                    pass
                
        elif not value:
            # Backspace pressed on empty field, go to previous
            self.entered_otp[index] = ""
            if index > 0:
                try:
                    self.otp_boxes[index - 1].focus()
                except Exception:
                    pass
        
        self._update_verify_button()
    
    def _on_otp_focus(self, e, index: int):
        """Handle OTP box focus.
        Note: Flet's TextField doesn't have select_all(); just pass for now.
        """
        pass
    
    def _update_verify_button(self):
        """Update verify button state based on OTP completion."""
        is_complete = all(self.entered_otp) and len("".join(self.entered_otp)) == 6
        
        if is_complete:
            self.verify_btn.disabled = False
            self.verify_btn.bgcolor = self.palette["secondary"]
        else:
            self.verify_btn.disabled = True
            self.verify_btn.bgcolor = ft.Colors.GREY
            
        self.page.update()
    
    def _verify_otp(self, e):
        """Verify the entered OTP against stored OTP data."""
        entered_code = "".join(self.entered_otp)
        
        if not entered_code or len(entered_code) != 6:
            self._show_error("Please enter a complete 6-digit code")
            return
        
        # Get stored OTP data
        otp_data = OTPScreen.get_otp_data(self.email)
        
        if not otp_data:
            self._show_error("OTP expired or not found. Request a new one.")
            self.verify_btn.disabled = True
            self.resend_btn.disabled = False
            return
        
        self.attempts += 1
        
        if entered_code == otp_data["otp"]:
            self._show_success("✓ Verification successful!")
            # Prevent duplicate verification: consume the stored OTP and disable the verify button
            try:
                if self.email in OTPScreen._otp_store:
                    del OTPScreen._otp_store[self.email]
            except Exception:
                pass

            try:
                self.verify_btn.disabled = True
                self.verify_btn.bgcolor = ft.Colors.GREY
            except Exception:
                pass

            # Call the verification callback after a short delay
            import threading
            threading.Timer(1.0, lambda: self.on_verified(otp_data.get("pending_user", {}))).start()
        else:
            remaining_attempts = self.max_attempts - self.attempts
            if remaining_attempts > 0:
                self._show_error(f"Invalid code. {remaining_attempts} attempts remaining")
                self._clear_otp_boxes()
            else:
                self._show_error("Too many failed attempts. Please request a new code.")
                self.verify_btn.disabled = True
                self.resend_btn.disabled = False
    
    def _clear_otp_boxes(self):
        """Clear all OTP boxes and reset focus."""
        for i, box in enumerate(self.otp_boxes):
            box.value = ""
            self.entered_otp[i] = ""
        
        if self.otp_boxes:
            self.otp_boxes[0].focus()
        self._update_verify_button()
    
    def _resend_otp(self, e):
        """Resend OTP code."""
        # Generate new OTP
        otp = Registration.generate_otp(6)
        
        # Store it
        OTPScreen.store_otp(self.email, otp, self.pending_user)
        
        # Send email
        sent = Registration.send_otp_email(self.email, otp)
        
        if sent:
            self.attempts = 0
            self.countdown = 120
            self._clear_otp_boxes()
            self.resend_btn.disabled = True
            self.verify_btn.disabled = True
            self.timer_text.value = "02:00"
            self.error_text.value = "✓ New code sent to your email"
            self.error_text.color = self.palette["success"]
            
            # Start countdown again
            self._start_countdown()
            self.page.update()
        else:
            self._show_error("Failed to send OTP. Check your connection.")
    
    def _start_countdown(self):
        """Start the countdown timer for OTP resend."""
        def update_timer():
            while self.countdown > 0:
                time.sleep(1)
                self.countdown -= 1
                mins = self.countdown // 60
                secs = self.countdown % 60
                if hasattr(self, 'timer_text') and self.timer_text:
                    self.timer_text.value = f"{mins:02d}:{secs:02d}"
                    self.page.update()
            
            if hasattr(self, 'resend_btn') and self.resend_btn:
                self.resend_btn.disabled = False
                self.timer_text.value = "Code expired"
                self.page.update()
        
        import threading
        timer_thread = threading.Thread(target=update_timer, daemon=True)
        timer_thread.start()
    
    def _show_error(self, message: str):
        """Show error message."""
        self.error_text.value = message
        self.error_text.color = self.palette["danger"]
        self.page.update()
    
    def _show_success(self, message: str):
        """Show success message."""
        self.error_text.value = message
        self.error_text.color = self.palette["success"]
        self.page.update()
    
    def _handle_keyboard(self, e: ft.KeyboardEvent):
        """Handle keyboard events for OTP navigation."""
        if e.key == "Enter" and not self.verify_btn.disabled:
            self._verify_otp(None)
        elif e.key == "Backspace":
            # Find the first empty box from the right and focus the previous one
            for i in range(len(self.otp_boxes) - 1, -1, -1):
                if not self.otp_boxes[i].value and i > 0:
                    self.otp_boxes[i - 1].focus()
                    break
    
    def build(self):
        """Build and return the OTP verification screen."""
        # Create OTP boxes
        self.otp_boxes = [self._create_otp_box(i) for i in range(6)]
        
        # Title
        self.title = ft.ShaderMask(
            content=ft.Text(
                "Verify Your Email",
                size=32,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.WHITE,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[self.palette["primary"], self.palette["secondary"]],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )
        
        # Email display (masked)
        masked_email = self._mask_email(self.email)
        self.email_display = ft.Text(
            f"Enter the 6-digit code sent to:\n{masked_email}",
            size=16,
            color=self.palette["on_surface"],
            text_align=ft.TextAlign.CENTER,
            weight=ft.FontWeight.W_500,
        )
        
        # OTP input container
        self.otp_container = ft.Row(
            controls=self.otp_boxes,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )
        
        # Verify button
        self.verify_btn = ft.ElevatedButton(
            "Verify",
            width=200,
            height=50,
            disabled=True,
            bgcolor=ft.Colors.GREY,
            color=ft.Colors.WHITE,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation=8,
            ),
            icon=ft.Icons.VERIFIED,
            icon_color=ft.Colors.WHITE,
            on_click=self._verify_otp,
        )
        
        # Resend button and timer
        self.resend_btn = ft.TextButton(
            "Resend Code",
            disabled=True,
            on_click=self._resend_otp,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["primary"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )
        
        self.timer_text = ft.Text(
            "02:00",
            size=14,
            color=self.palette["accent"],
            weight=ft.FontWeight.W_600,
        )
        
        # Error/success message
        self.error_text = ft.Text(
            "",
            size=14,
            text_align=ft.TextAlign.CENTER,
            color=self.palette["on_surface"],
        )
        
        # Back button
        back_btn = ft.TextButton(
            "← Back",
            on_click=lambda e: self.on_back() if callable(self.on_back) else None,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["primary"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )
        
        # Create form column
        form_column = ft.Column(
            controls=[
                self.title,
                ft.Container(height=20),
                self.email_display,
                ft.Container(height=30),
                self.otp_container,
                ft.Container(height=20),
                ft.Container(
                    content=self.verify_btn,
                    alignment=ft.alignment.center
                ),
                ft.Container(height=15),
                ft.Row(
                    controls=[
                        self.resend_btn,
                        self.timer_text,
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=20,
                ),
                ft.Container(height=10),
                ft.Container(
                    content=self.error_text,
                    alignment=ft.alignment.center,
                    width=300,
                ),
                ft.Container(height=20),
                ft.Container(
                    content=back_btn,
                    alignment=ft.alignment.center
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.ADAPTIVE,
        )
        
        # Form container
        form_container = ft.Container(
            content=form_column,
            padding=40,
            bgcolor=ft.Colors.with_opacity(0.85, self.palette["surface"]),
            border_radius=24,
            shadow=ft.BoxShadow(
                blur_radius=50,
                color=ft.Colors.with_opacity(0.3, self.palette["background"]),
            ),
            margin=ft.margin.symmetric(horizontal=20),
        )
        
        # Form overlay with green accent
        form_overlay = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.15, self.palette["secondary"]),
            border_radius=24,
        )
        
        # Form stack
        form_stack = ft.Stack(
            expand=False,
            controls=[
                form_overlay,
                form_container,
            ],
        )
        
        # List view for scrolling
        list_view = ft.ListView(
            controls=[form_stack],
            padding=20,
            spacing=10,
            auto_scroll=False,
            expand=False,
        )
        

        # Main content wrapped with background placeholder (background created by stub)
        inner_content = ft.Container(
            expand=False,
            content=ft.Column(
                controls=[
                    ft.Container(
                        expand=False,
                        bgcolor=ft.Colors.with_opacity(0.35, self.palette["background"]),
                        content=ft.Container(expand=False),
                    ),
                    ft.Container(
                        expand=False,
                        alignment=ft.alignment.center,
                        content=list_view,
                    ),
                ]
            )
        )

        main_content = create_background_stack(inner_content, page=self.page, padding=12)
        
        # Layout updater for responsiveness
        def _update_layout(e=None):
            try:
                win_w = self._get_page_width(800)
                if win_w < 600:
                    target_w = max(300, int(win_w * 0.9))
                else:
                    target_w = max(400, int(win_w * 0.5))
                
                form_container.width = target_w
                form_overlay.width = target_w + 20
                form_overlay.padding = 10

                # Inputs should take 95% of the form width on small screens
                try:
                    input_w = int(target_w * 0.95) if win_w < 600 else None
                    if input_w:
                        try:
                            self.email_field.width = input_w
                        except Exception:
                            pass
                        try:
                            self.password_field.width = input_w
                        except Exception:
                            pass
                except Exception:
                    pass
                
                # Adjust OTP box sizes for smaller screens
                box_size = 40 if win_w < 400 else 50
                for box in self.otp_boxes:
                    box.width = box_size
                    box.height = box_size + 10
                
                self.page.update()
            except Exception:
                pass
        
        self.page.on_resize = _update_layout
        _update_layout()
        
        # Set up keyboard event handler
        self.page.on_keyboard_event = self._handle_keyboard
        
        # Start countdown timer
        self._start_countdown()
        
        # Focus first OTP box shortly after the UI is added to the page.
        # Use a short delayed call and guard exceptions because the control
        # may not yet be attached to the page when build() returns.
        if self.otp_boxes:
            import threading

            def _delayed_focus():
                try:
                    self.otp_boxes[0].focus()
                except Exception:
                    # If focusing fails (control not yet added), attempt a safe update
                    try:
                        self.page.update()
                    except Exception:
                        pass

            threading.Timer(0.25, _delayed_focus).start()
        
        return main_content
    
    def set_correct_otp(self, otp: str):
        """Set the correct OTP that should be verified against."""
        self.correct_otp = otp

# ========== End OTPScreen Class ==========
# Example usage in your main app:
def show_otp_screen(page: ft.Page, email: str, correct_otp: str):
    """Show OTP verification screen."""
    
    def on_verified():
        # This is called when OTP is successfully verified
        page.snack_bar = ft.SnackBar(
            content=ft.Text("Email verified successfully!", color=ft.Colors.WHITE),
            bgcolor=PALETTE["success"]
        )
        page.snack_bar.open = True
        page.update()
        # Navigate to next screen or perform action
    
    def on_back():
        # Go back to previous screen
        page.clean()
        # Show registration screen again or previous screen
        registration_app = Registration(page)
        page.add(registration_app.build())
    
    # Create and show OTP screen
    otp_screen = OTPScreen(page, email, None, on_verified, on_back)
    otp_screen.set_correct_otp(correct_otp)
    
    page.clean()
    page.add(otp_screen.build())

# In your registration class, modify the OTP sending part:
def send_otp_and_prompt(self, email: str, on_verified):
    """Generate OTP and show OTP verification screen."""
    otp = self._generate_otp(6)
    
    # Store for verification
    if not hasattr(self, "_pending_otps"):
        self._pending_otps = {}
    self._pending_otps[email] = (otp, time.time())
    
    # Send email (your existing code)
    sent = self._send_otp_email(email, otp)
    
    if sent:
        # Show OTP screen instead of dialog
        show_otp_screen(self.page, email, otp)
    else:
        self.page.snack_bar = ft.SnackBar(
            content=ft.Text("Could not send OTP. Please try again later."),
            bgcolor=self.palette["danger"]
        )
        self.page.snack_bar.open = True
        self.page.update()
# ============ Login Screen Class ============
class LoginScreen:
    """Login screen with email and password fields."""
    
    def __init__(self, page: ft.Page, on_login_success=None, on_register_click=None, on_forgot_password=None):
        self.page = page
        self.on_login_success = on_login_success
        self.on_register_click = on_register_click
        self.on_forgot_password = on_forgot_password
        self.palette = PALETTE
        
        # Form fields
        self.email_field = None
        self.password_field = None
        self.login_btn = None
        self.error_text = None
        # On-screen debug area for login
        self._debug_lines = []
        self.debug_text = ft.Text(
            "",
            size=12,
            text_align=ft.TextAlign.LEFT,
            color=ft.Colors.WHITE,
        )

    def _get_page_width(self, default=800):
        """Safely get page width, handling various Flet version attributes."""
        try:
            return self.page.window_width or self.page.width or default
        except AttributeError:
            return getattr(self.page, 'width', None) or default

    def append_debug(self, message: str):
        """Append a debug line to the on-screen debug area and print to console."""
        try:
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            line = f"[{ts}] {message}"
            print(line)
            self._debug_lines.append(line)
            self._debug_lines = self._debug_lines[-6:]
            self.debug_text.value = "\n".join(self._debug_lines)
            try:
                self.page.update()
            except Exception:
                pass
        except Exception:
            pass
        
    def create_text_field(self, label, hint_text, **kwargs):
        """Create a styled text field."""
        return ft.TextField(
            label=label,
            hint_text=hint_text,
            expand=True,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            label_style=ft.TextStyle(color=self.palette["on_surface"]),
            hint_style=ft.TextStyle(color=ft.Colors.WHITE54),
            text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
            cursor_color=self.palette["accent"],
            **kwargs
        )
    
    def validate_email(self, e):
        """Validate email field."""
        import re
        value = self.email_field.value.strip()
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        
        if not value:
            self.email_field.border_color = self.palette["primary"]
            self.email_field.error_text = ""
        elif not re.match(email_pattern, value):
            self.email_field.border_color = self.palette["danger"]
            self.email_field.error_text = "Please enter a valid email address"
        else:
            self.email_field.border_color = self.palette["success"]
            self.email_field.error_text = ""
        self.page.update()
    
        def _delayed_call(self, callback, delay):
            """Execute a callback after a delay on the main thread."""
            import time
            time.sleep(delay)
            callback()
    
    def on_login(self, e):
        """Handle login attempt with Firebase authentication."""
        email = self.email_field.value.strip()
        password = self.password_field.value
        
        # Basic validation
        if not email or not password:
            self._show_error("Please fill in all fields")
            return
        
        # Validate email format
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, email):
            self._show_error("Please enter a valid email address")
            return
        
        # Start login process
        try:
            self.append_debug(f"on_login: starting for {email}")
        except Exception:
            pass
        self.login_btn.disabled = True
        self.login_btn.text = "Signing in..."
        self.page.update()
        
        # Authenticate against Firebase
        import threading
        def attempt_login():
            try:
                try:
                    self.append_debug("attempt_login: authenticating against Firebase...")
                except Exception:
                    pass
                user = self._authenticate_user(email, password)

                if user:
                    try:
                        user_email = str(user.get('email', 'unknown'))
                        self.append_debug("Login successful for user: " + user_email)
                        print(f"[DEBUG] on_login: user authenticated: {user_email}")
                    except Exception:
                        pass
                    self._show_success("✓ Login successful!")
                    # Call success callback after short delay to show message
                    print(f"[DEBUG] on_login: scheduling on_login_success callback in 1.0s")
                    try:
                        self.append_debug("Redirecting to dashboard...")
                    except Exception:
                        pass
                    # Execute callback after 1.0 second delay
                    def execute_callback():
                        import traceback
                        print(f"[DEBUG] on_login: callback executing after 1.0s delay")
                        print(f"[DEBUG] on_login: self.on_login_success = {self.on_login_success}")
                        print(f"[DEBUG] on_login: user = {user}")
                        if self.on_login_success:
                            try:
                                print(f"[DEBUG] on_login: calling on_login_success with user data")
                                self.on_login_success(user)
                                print(f"[DEBUG] on_login: on_login_success returned successfully")
                            except Exception as e:
                                print(f"[ERROR] on_login: callback failed: {e}")
                                traceback.print_exc()
                        else:
                            print(f"[ERROR] on_login: on_login_success is None or False")

                    # Use threading.Timer to schedule after 1.0 second (non-daemon so it keeps app alive)
                    timer = threading.Timer(1.0, execute_callback)
                    timer.daemon = False  # Ensure timer keeps the app alive
                    timer.start()
                else:
                    try:
                        self.append_debug("Login failed: invalid credentials")
                    except Exception:
                        pass
                    self._show_error("❌ Invalid email or password")
                    self.login_btn.disabled = False
                    self.login_btn.text = "Sign In"
                    self.page.update()
            except Exception as ex:
                try:
                    self.append_debug(f"Login exception: {ex}")
                except Exception:
                    pass
                self._show_error(f"Login failed: {str(ex)}")
                self.login_btn.disabled = False
                self.login_btn.text = "Sign In"
                self.page.update()
        
        threading.Thread(target=attempt_login, daemon=True).start()
    
    def _authenticate_user(self, email: str, password: str) -> dict:
        """
        Authenticate user against Firebase Realtime Database.
        Retrieves user by email and validates password.
        Returns user dict if valid, None otherwise.
        """
        if not FIREBASE_DB_URL:
            raise RuntimeError("FIREBASE_DB_URL not configured. Cannot authenticate.")
        
        try:
            # Fetch all users from Firebase (admin SDK or REST fallback)
            try:
                self.append_debug("_authenticate_user: fetching users from Firebase...")
            except Exception:
                pass
            users_data = fetch_all_users()

            if not users_data:
                try:
                    self.append_debug("_authenticate_user: no users returned from Firebase")
                except Exception:
                    pass
                return None

            try:
                self.append_debug(f"_authenticate_user: fetched {len(users_data)} users")
            except Exception:
                pass

            # Search for user by email
            for user_id, user_data in users_data.items():
                if isinstance(user_data, dict) and user_data.get("email") == email:
                    try:
                        self.append_debug(f"_authenticate_user: found user id={user_id}")
                    except Exception:
                        pass
                    # Found user - validate password
                    if user_data.get("password") == password:
                        # Password matches - return user data with ID
                        user_data["id"] = user_id
                        try:
                            self.append_debug("_authenticate_user: password match")
                        except Exception:
                            pass
                        return user_data
                    else:
                        try:
                            self.append_debug("_authenticate_user: password mismatch")
                        except Exception:
                            pass
                        # Password doesn't match
                        return None

            # User not found
            try:
                self.append_debug("_authenticate_user: user not found by email")
            except Exception:
                pass
            return None

        except requests.exceptions.RequestException as ex:
            try:
                self.append_debug(f"_authenticate_user: Firebase connection error: {ex}")
            except Exception:
                pass
            raise Exception(f"Firebase connection error: {str(ex)}")
    
    def _show_error(self, message: str):
        """Show error message."""
        self.error_text.value = message
        self.error_text.color = self.palette["danger"]
        self.page.update()
    
    def _show_success(self, message: str):
        """Show success message."""
        self.error_text.value = message
        self.error_text.color = self.palette["success"]
        self.page.update()
    
    def _handle_keyboard(self, e: ft.KeyboardEvent):
        """Handle keyboard events."""
        if e.key == "Enter" and not self.login_btn.disabled:
            self.on_login(None)
    
    def build(self):
        """Build and return the login screen."""
        # Title with gradient
        title = ft.ShaderMask(
            content=ft.Text(
                "CLC KENYA",
                size=38,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.WHITE,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[self.palette["primary"], self.palette["secondary"]],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )

        # Logo container
        logo_container = ft.Container(
            width=140,
            height=100,
            bgcolor=ft.Colors.with_opacity(0.1, self.palette["primary"]),
            border_radius=20,
            alignment=ft.alignment.center,
            content=ft.Image(
                src=LOGO_PATH,
                width=160,
                height=140,
                fit=ft.ImageFit.CONTAIN,
            ),
            shadow=ft.BoxShadow(
                blur_radius=15,
                color=ft.Colors.with_opacity(0.3, self.palette["primary"]),
            ),
            # static logo (no animation)
        )

        # Description
        desc = ft.Text(
            "In all things to love and to serve",
            size=18,
            color=self.palette["accent"],
            weight=ft.FontWeight.W_500,
            text_align=ft.TextAlign.CENTER,
        )

        # Form fields
        self.email_field = self.create_text_field("Email address", "you@example.com")
        self.email_field.on_change = self.validate_email
        
        self.password_field = self.create_text_field(
            "Password",
            "Enter your password",
            password=True,
            can_reveal_password=True
        )

        # Login button
        self.login_btn = ft.ElevatedButton(
            "Sign In",
            width=220,
            height=50,
            bgcolor=self.palette["secondary"],
            color=ft.Colors.WHITE,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation=8,
                shadow_color=ft.Colors.with_opacity(0.3, self.palette["secondary"]),
            ),
            icon=ft.Icons.LOGIN,
            icon_color=ft.Colors.WHITE,
            on_click=self.on_login,
        )

        # Register link
        register_link = ft.TextButton(
            "Don't have an account? Register here",
            on_click=lambda e: self.on_register_click() if callable(self.on_register_click) else None,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["primary"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )

        # Error/success message
        self.error_text = ft.Text(
            "",
            size=14,
            text_align=ft.TextAlign.CENTER,
            color=self.palette["on_surface"],
        )

        # Forgot password link
        forgot_password = ft.TextButton(
            "Forgot your password?",
            on_click=lambda e: self.on_forgot_password() if callable(getattr(self, 'on_forgot_password', None)) else None,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["accent"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )

        # Create form column
        form_column = ft.Column(
            controls=[
                title,
                ft.Container(height=10),
                logo_container,
                ft.Container(height=15),
                desc,
                ft.Container(height=25),
                ft.Container(content=self.email_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=self.password_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=5),
                ft.Container(
                    content=forgot_password,
                    alignment=ft.alignment.center_right,
                    padding=ft.padding.only(right=30),
                ),
                ft.Container(height=20),
                ft.Container(
                    content=self.login_btn,
                    alignment=ft.alignment.center
                ),
                ft.Container(height=10),
                ft.Container(
                    content=self.error_text,
                    alignment=ft.alignment.center,
                    width=300,
                ),
                ft.Container(height=15),
                ft.Container(
                    content=register_link,
                    alignment=ft.alignment.center
                )
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.ADAPTIVE,
        )

        # Form container
        form_container = ft.Container(
            content=form_column,
            padding=40,
            bgcolor=ft.Colors.with_opacity(0.85, self.palette["surface"]),
            border_radius=24,
            shadow=ft.BoxShadow(
                blur_radius=50,
                color=ft.Colors.with_opacity(0.3, self.palette["background"]),
            ),
            margin=ft.margin.symmetric(horizontal=20),
        )

        # Form overlay with green accent
        form_overlay = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.15, self.palette["secondary"]),
            border_radius=24,
        )

        # Form stack
        form_stack = ft.Stack(
            expand=False,
            controls=[
                form_overlay,
                form_container,
            ],
        )

        # List view for scrolling
        list_view = ft.ListView(
            controls=[form_stack],
            padding=20,
            spacing=10,
            auto_scroll=False,
            expand=False,
        )
        

        # Main content wrapped with background placeholder (background created by stub)
        inner_content = ft.Container(
            expand=False,
            content=ft.Column(
                controls=[
                    ft.Container(
                        expand=False,
                        bgcolor=ft.Colors.with_opacity(0.35, self.palette["background"]),
                        content=ft.Container(expand=False),
                    ),
                    ft.Container(
                        expand=False,
                        alignment=ft.alignment.center,
                        content=list_view,
                    ),
                ]
            )
        )

        main_content = create_background_stack(inner_content, page=self.page, padding=12)

        # Layout updater for responsiveness
        def _update_layout(e=None):
            try:
                win_w = self._get_page_width(800)
                if win_w < 600:
                    target_w = max(300, int(win_w * 0.9))
                    field_padding = 15
                else:
                    target_w = max(400, int(win_w * 0.5))
                    field_padding = 30
                
                form_container.width = target_w
                form_overlay.width = target_w + 20
                form_overlay.padding = 10
                self.page.update()
            except Exception:
                pass

        self.page.on_resize = _update_layout
        _update_layout()

        # Set up keyboard event handler
        self.page.on_keyboard_event = self._handle_keyboard

        # (logo animation removed - logo is now static image)

        # Auto-focus email field
        def focus_email_field():
            try:
                self.email_field.focus()
            except Exception:
                pass
        
        threading.Timer(1.0, focus_email_field).start()

        return main_content
# ============ Password Reset Flow Class ============
class PasswordResetFlow:
    """Handles the complete password reset flow."""
    
    def __init__(self, page: ft.Page, on_complete=None, app: "CLCKenyaApp" = None):
        self.page = page
        self.on_complete = on_complete
        self.app = app
        self.palette = PALETTE
        
        # State management
        self.current_screen = None
        self.user_email = None
        self.reset_otp = None
        self.screens = {}
        
    def show_email_input_screen(self):
        """Show the initial email input screen."""
        # Title with gradient
        title = ft.ShaderMask(
            content=ft.Text(
                "Reset Password",
                size=32,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.WHITE,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[self.palette["primary"], self.palette["secondary"]],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )

        # Logo container (static image)
        logo_container = ft.Container(
            width=220,
            height=160,
            bgcolor=ft.Colors.with_opacity(0.1, self.palette["primary"]),
            border_radius=20,
            alignment=ft.alignment.center,
            content=ft.Image(
                src=LOGO_PATH,
                width=160,
                height=140,
                fit=ft.ImageFit.CONTAIN,
            ),
            shadow=ft.BoxShadow(
                blur_radius=15,
                color=ft.Colors.with_opacity(0.3, self.palette["primary"]),
            ),
        )

        # Description
        desc = ft.Text(
            "Enter your email to receive a verification code",
            size=16,
            color=self.palette["on_surface"],
            text_align=ft.TextAlign.CENTER,
            weight=ft.FontWeight.W_500,
        )

        # Email field
        email_field = ft.TextField(
            label="Email address",
            hint_text="you@example.com",
            expand=True,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            label_style=ft.TextStyle(color=self.palette["on_surface"]),
            text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
            keyboard_type=ft.KeyboardType.EMAIL,
        )

        # Send code button
        send_btn = ft.ElevatedButton(
            "Send Verification Code",
            width=220,
            height=50,
            bgcolor=self.palette["secondary"],
            color=ft.Colors.WHITE,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation=8,
            ),
            icon=ft.Icons.SEND,
            icon_color=ft.Colors.WHITE,
        )

        # Back to login link
        back_link = ft.TextButton(
            "← Back to Login",
            on_click=lambda e: self.on_complete() if callable(self.on_complete) else None,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["primary"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )

        # Error message
        error_text = ft.Text(
            "",
            size=14,
            text_align=ft.TextAlign.CENTER,
            color=self.palette["danger"],
        )

        def on_send_code(e):
            email = email_field.value.strip()
            if not email:
                error_text.value = "Please enter your email address"
                error_text.color = self.palette["danger"]
                self.page.update()
                return
            
            # Validate email format
            import re
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, email):
                error_text.value = "Please enter a valid email address"
                error_text.color = self.palette["danger"]
                self.page.update()
                return
            
            # Check if user exists in Firebase
            if not FIREBASE_DB_URL:
                error_text.value = "Firebase not configured. Cannot reset password."
                error_text.color = self.palette["danger"]
                self.page.update()
                return
            
            send_btn.disabled = True
            send_btn.text = "Checking..."
            self.page.update()
            
            import threading
            def verify_user_and_send_otp():
                try:
                    # Fetch users from Firebase (admin SDK or REST fallback)
                    users_data = fetch_all_users()
                    
                    if not users_data:
                        error_text.value = "No users found in the system"
                        error_text.color = self.palette["danger"]
                        send_btn.disabled = False
                        send_btn.text = "Send Verification Code"
                        self.page.update()
                        return
                    
                    # Search for user by email
                    user_found = False
                    for user_id, user_data in users_data.items():
                        if isinstance(user_data, dict) and user_data.get("email") == email:
                            user_found = True
                            break
                    
                    if not user_found:
                        error_text.value = "Email not found in our system"
                        error_text.color = self.palette["danger"]
                        send_btn.disabled = False
                        send_btn.text = "Send Verification Code"
                        self.page.update()
                        return
                    
                    # Generate OTP
                    self.reset_otp = Registration.generate_otp(6)
                    
                    # Send email
                    sent = Registration.send_otp_email(email, self.reset_otp)
                    
                    if sent:
                        # Store email and show success
                        self.user_email = email
                        error_text.value = "✓ Verification code sent to your email"
                        error_text.color = self.palette["success"]
                        send_btn.disabled = False
                        send_btn.text = "Send Verification Code"
                        self.page.update()
                        
                        # Show OTP screen after a short delay
                        import threading
                        threading.Timer(1.5, self.show_otp_verification_screen).start()
                    else:
                        error_text.value = "Failed to send verification code. Please try again."
                        error_text.color = self.palette["danger"]
                        send_btn.disabled = False
                        send_btn.text = "Send Verification Code"
                        self.page.update()
                        
                except requests.exceptions.RequestException as ex:
                    error_text.value = f"Connection error: {str(ex)}"
                    error_text.color = self.palette["danger"]
                    send_btn.disabled = False
                    send_btn.text = "Send Verification Code"
                    self.page.update()
                except Exception as ex:
                    error_text.value = f"Error: {str(ex)}"
                    error_text.color = self.palette["danger"]
                    send_btn.disabled = False
                    send_btn.text = "Send Verification Code"
                    self.page.update()
            
            threading.Thread(target=verify_user_and_send_otp, daemon=True).start()

        send_btn.on_click = on_send_code

        # Create form column
        form_column = ft.Column(
            controls=[
                title,
                ft.Container(height=20),
                logo_container,
                ft.Container(height=15),
                desc,
                ft.Container(height=25),
                ft.Container(content=email_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=20),
                ft.Container(
                    content=send_btn,
                    alignment=ft.alignment.center
                ),
                ft.Container(height=10),
                ft.Container(
                    content=error_text,
                    alignment=ft.alignment.center,
                    width=300,
                ),
                ft.Container(height=20),
                ft.Container(
                    content=back_link,
                    alignment=ft.alignment.center
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.ADAPTIVE,
        )

        self._build_screen(form_column, "Reset Password")
        
    def show_otp_verification_screen(self):
        """Show OTP verification screen."""
        otp_screen = ResetOTPScreen(
            self.page, 
            self.user_email, 
            on_verified=lambda: self.show_new_password_screen(),
            on_back=lambda: self.show_email_input_screen()
        )
        otp_screen.set_correct_otp(self.reset_otp)
        self._build_screen(otp_screen.build(), "Verify OTP")
        
    def show_new_password_screen(self):
        """Show new password input screen."""
        # Title with gradient
        title = ft.ShaderMask(
            content=ft.Text(
                "New Password",
                size=32,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.WHITE,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[self.palette["primary"], self.palette["secondary"]],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )

        # Logo container (static image)
        logo_container = ft.Container(
            width=220,
            height=160,
            bgcolor=ft.Colors.with_opacity(0.1, self.palette["primary"]),
            border_radius=20,
            alignment=ft.alignment.center,
            content=ft.Image(
                src=LOGO_PATH,
                width=160,
                height=140,
                fit=ft.ImageFit.CONTAIN,
            ),
            shadow=ft.BoxShadow(
                blur_radius=15,
                color=ft.Colors.with_opacity(0.3, self.palette["primary"]),
            ),
        )

        # Description
        desc = ft.Text(
            "Create a new strong password for your account",
            size=16,
            color=self.palette["on_surface"],
            text_align=ft.TextAlign.CENTER,
            weight=ft.FontWeight.W_500,
        )

        # Password fields
        new_password_field = ft.TextField(
            label="New Password",
            hint_text="Enter your new password",
            expand=True,
            password=True,
            can_reveal_password=True,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            label_style=ft.TextStyle(color=self.palette["on_surface"]),
            text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
        )

        confirm_password_field = ft.TextField(
            label="Confirm Password",
            hint_text="Re-enter your new password",
            expand=True,
            password=True,
            can_reveal_password=True,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            label_style=ft.TextStyle(color=self.palette["on_surface"]),
            text_style=ft.TextStyle(color=self.palette["on_surface"], size=14),
        )

        # Password strength indicator
        strength_text = ft.Text(
            "",
            size=12,
            text_align=ft.TextAlign.LEFT,
            color=self.palette["on_surface"],
        )

        # Match indicator
        match_text = ft.Text(
            "",
            size=12,
            text_align=ft.TextAlign.LEFT,
            color=self.palette["on_surface"],
        )

        # Reset button
        reset_btn = ft.ElevatedButton(
            "Reset Password",
            width=220,
            height=50,
            disabled=True,
            bgcolor=ft.Colors.GREY,
            color=ft.Colors.WHITE,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=12),
                elevation=8,
            ),
            icon=ft.Icons.CHECK_CIRCLE,
            icon_color=ft.Colors.WHITE,
        )

        # Error message
        error_text = ft.Text(
            "",
            size=14,
            text_align=ft.TextAlign.CENTER,
            color=self.palette["danger"],
        )

        def validate_password_strength(e):
            """Validate password strength."""
            password = new_password_field.value
            errors = []
            
            if len(password) < 8:
                errors.append("At least 8 characters")
            if not any(c.isupper() for c in password):
                errors.append("One uppercase letter")
            if not any(c.islower() for c in password):
                errors.append("One lowercase letter")
            if not any(c.isdigit() for c in password):
                errors.append("One number")
            if not any(c in "!@#$%^&*" for c in password):
                errors.append("One special character (!@#$%^&*)")
            
            if not password:
                strength_text.value = ""
                new_password_field.border_color = self.palette["primary"]
            elif errors:
                strength_text.value = f"Need: {', '.join(errors)}"
                strength_text.color = self.palette["danger"]
                new_password_field.border_color = self.palette["danger"]
            else:
                strength_text.value = "✓ Strong password"
                strength_text.color = self.palette["success"]
                new_password_field.border_color = self.palette["success"]
            
            _validate_passwords_match(None)
            self.page.update()

        def validate_password_match(e):
            """Validate that passwords match."""
            _validate_passwords_match(e)

        def _validate_passwords_match(e):
            """Internal method to validate password match."""
            password = new_password_field.value
            confirm = confirm_password_field.value
            
            if not confirm:
                match_text.value = ""
                confirm_password_field.border_color = self.palette["primary"]
            elif password != confirm:
                match_text.value = "✗ Passwords do not match"
                match_text.color = self.palette["danger"]
                confirm_password_field.border_color = self.palette["danger"]
            else:
                match_text.value = "✓ Passwords match"
                match_text.color = self.palette["success"]
                confirm_password_field.border_color = self.palette["success"]
            
            # Enable reset button only when both conditions are met
            is_strong = not strength_text.value.startswith("Need:") and strength_text.value
            do_match = match_text.value.startswith("✓")
            
            reset_btn.disabled = not (is_strong and do_match)
            if not reset_btn.disabled:
                reset_btn.bgcolor = self.palette["secondary"]
            else:
                reset_btn.bgcolor = ft.Colors.GREY
            
            self.page.update()

        def on_reset_password(e):
            """Handle password reset - update password in Firebase."""
            password = new_password_field.value
            confirm = confirm_password_field.value
            
            if password != confirm:
                error_text.value = "Passwords do not match"
                error_text.color = self.palette["danger"]
                self.page.update()
                return
            
            if not FIREBASE_DB_URL:
                error_text.value = "Firebase not configured"
                error_text.color = self.palette["danger"]
                self.page.update()
                return
            
            reset_btn.disabled = True
            reset_btn.text = "Resetting..."
            self.page.update()
            
            import threading
            def reset_password():
                try:
                    # Fetch all users from Firebase
                    users_data = fetch_all_users()
                    
                    if not users_data:
                        error_text.value = "No users found"
                        error_text.color = self.palette["danger"]
                        reset_btn.disabled = False
                        reset_btn.text = "Reset Password"
                        self.page.update()
                        return
                    
                    # Find user by email and get their ID
                    user_id = None
                    for uid, user_data in users_data.items():
                        if isinstance(user_data, dict) and user_data.get("email") == self.user_email:
                            user_id = uid
                            break
                    
                    if not user_id:
                        error_text.value = "User not found"
                        error_text.color = self.palette["danger"]
                        reset_btn.disabled = False
                        reset_btn.text = "Reset Password"
                        self.page.update()
                        return
                    
                    # Update password in Firebase (admin SDK or REST fallback)
                    update_user_password(user_id, password)
                    
                    # Show success message
                    error_text.value = "✓ Password reset successfully!"
                    error_text.color = self.palette["success"]
                    reset_btn.disabled = False
                    reset_btn.text = "Reset Password"
                    self.page.update()
                    
                    # Redirect to login after delay
                    threading.Timer(2.0, lambda: self.on_complete() if self.on_complete else None).start()
                    
                except requests.exceptions.RequestException as ex:
                    error_text.value = f"Connection error: {str(ex)}"
                    error_text.color = self.palette["danger"]
                    reset_btn.disabled = False
                    reset_btn.text = "Reset Password"
                    self.page.update()
                except Exception as ex:
                    error_text.value = f"Error: {str(ex)}"
                    error_text.color = self.palette["danger"]
                    reset_btn.disabled = False
                    reset_btn.text = "Reset Password"
                    self.page.update()
            
            threading.Thread(target=reset_password, daemon=True).start()

        # Wire up event handlers
        new_password_field.on_change = validate_password_strength
        confirm_password_field.on_change = validate_password_match
        reset_btn.on_click = on_reset_password

        # Create form column
        form_column = ft.Column(
            controls=[
                title,
                ft.Container(height=20),
                logo_container,
                ft.Container(height=15),
                desc,
                ft.Container(height=25),
                ft.Container(content=new_password_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(content=strength_text, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=10),
                ft.Container(content=confirm_password_field, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(content=match_text, padding=ft.padding.symmetric(horizontal=30)),
                ft.Container(height=20),
                ft.Container(
                    content=reset_btn,
                    alignment=ft.alignment.center
                ),
                ft.Container(height=10),
                ft.Container(
                    content=error_text,
                    alignment=ft.alignment.center,
                    width=300,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.ADAPTIVE,
        )

        self._build_screen(form_column, "New Password")
        
    def _build_screen(self, form_column, title):
        """Build a consistent screen layout."""
        # Form container
        form_container = ft.Container(
            content=form_column,
            padding=40,
            bgcolor=ft.Colors.with_opacity(0.85, self.palette["surface"]),
            border_radius=24,
            shadow=ft.BoxShadow(
                blur_radius=50,
                color=ft.Colors.with_opacity(0.3, self.palette["background"]),
            ),
            margin=ft.margin.symmetric(horizontal=20),
        )

        # Form overlay with green accent
        form_overlay = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.15, self.palette["secondary"]),
            border_radius=24,
        )

        # Form stack
        form_stack = ft.Stack(
            expand=False,
            controls=[
                form_overlay,
                form_container,
            ],
        )

        # List view for scrolling
        list_view = ft.ListView(
            controls=[form_stack],
            padding=20,
            spacing=10,
            auto_scroll=False,
            expand=False,
        )
        

        # Main content wrapped with background placeholder (background created by stub)
        inner_content = ft.Container(
            expand=False,
            content=ft.Column(
                controls=[
                    ft.Container(
                        expand=False,
                        bgcolor=ft.Colors.with_opacity(0.35, self.palette["background"]),
                        content=ft.Container(expand=False),
                    ),
                    ft.Container(
                        expand=False,
                        alignment=ft.alignment.center,
                        content=list_view,
                    ),
                ]
            )
        )

        main_content = create_background_stack(inner_content, page=self.page, padding=12)

        # Layout updater for responsiveness
        def _update_layout(e=None):
            try:
                win_w = self._get_page_width(800)
                if win_w < 600:
                    target_w = max(300, int(win_w * 0.9))
                else:
                    target_w = max(400, int(win_w * 0.5))
                
                form_container.width = target_w
                form_overlay.width = target_w + 20
                form_overlay.padding = 10
                self.page.update()
            except Exception:
                pass

        self.page.on_resize = _update_layout
        _update_layout()

        # Update page (wrap with global background if available)
        self.page.clean()
        try:
            if getattr(self, 'app', None):
                self.page.add(self.app._wrap_with_global_background(main_content))
            else:
                self.page.add(main_content)
        except Exception:
            self.page.add(main_content)
        self.page.update()


# ============ Reset OTP Screen Class ============
class ResetOTPScreen:
    """OTP verification screen for password reset."""
    
    def __init__(self, page: ft.Page, email: str, on_verified, on_back=None):
        self.page = page
        self.email = email
        self.on_verified = on_verified
        self.on_back = on_back
        self.palette = PALETTE
        
        # OTP state
        self.otp_boxes = []
        self.entered_otp = ["", "", "", "", "", ""]
        self.correct_otp = None
    
    def _get_page_width(self, default=800):
        """Safely get page width, handling various Flet version attributes."""
        try:
            return self.page.window_width or self.page.width or default
        except AttributeError:
            return getattr(self.page, 'width', None) or default
        
    def _mask_email(self, email: str) -> str:
        """Mask email for security."""
        if "@" not in email:
            return email
            
        local_part, domain = email.split("@", 1)
        domain_parts = domain.split(".", 1)
        
        if len(local_part) > 2:
            masked_local = local_part[:2] + "*" * (len(local_part) - 2)
        else:
            masked_local = local_part[0] + "*" if len(local_part) > 1 else local_part
        
        if len(domain_parts) > 1:
            main_domain = domain_parts[0]
            if len(main_domain) > 2:
                masked_domain = main_domain[:2] + "*" * (len(main_domain) - 2) + "." + domain_parts[1]
            else:
                masked_domain = main_domain[0] + "*." + domain_parts[1] if len(main_domain) > 1 else main_domain + "." + domain_parts[1]
        else:
            masked_domain = domain
            
        return f"{masked_local}@{masked_domain}"
    
    def _create_otp_box(self, index: int) -> ft.TextField:
        """Create a single OTP input box."""
        return ft.TextField(
            width=50,
            height=60,
            text_align=ft.TextAlign.CENTER,
            text_size=24,
            max_length=1,
            content_padding=0,
            border_color=self.palette["primary"],
            focused_border_color=self.palette["accent"],
            text_style=ft.TextStyle(
                color=self.palette["on_surface"],
                weight=ft.FontWeight.W_600
            ),
            keyboard_type=ft.KeyboardType.NUMBER,
            on_change=lambda e, idx=index: self._on_otp_change(e, idx),
        )
    
    def _on_otp_change(self, e, index: int):
        """Handle OTP box value change."""
        value = e.control.value.strip()
        
        if value and value.isdigit():
            self.entered_otp[index] = value
            
            # Auto-focus next box
            if index < len(self.otp_boxes) - 1:
                try:
                    self.otp_boxes[index + 1].focus()
                except Exception:
                    pass
        
        elif not value:
            # Backspace pressed, go to previous
            self.entered_otp[index] = ""
            if index > 0:
                try:
                    self.otp_boxes[index - 1].focus()
                except Exception:
                    pass
        
        # Check if all boxes are filled
        if all(self.entered_otp):
            entered_code = "".join(self.entered_otp)
            # Validate against stored OTP
            otp_data = OTPScreen.get_otp_data(self.email)
            if otp_data and entered_code == otp_data["otp"]:
                self.on_verified()
            else:
                self._show_error("Invalid code. Please try again.")
                self._clear_boxes()
    
    def _clear_boxes(self):
        """Clear all OTP boxes."""
        for i, box in enumerate(self.otp_boxes):
            box.value = ""
            self.entered_otp[i] = ""
        
        if self.otp_boxes:
            self.otp_boxes[0].focus()
        self.page.update()
    
    def _show_error(self, message: str):
        """Show error message."""
        # You can implement error display here
        print(f"OTP Error: {message}")
    
    def build(self):
        """Build and return the OTP screen."""
        self.otp_boxes = [self._create_otp_box(i) for i in range(6)]
        
        # Title
        title = ft.ShaderMask(
            content=ft.Text(
                "Verify OTP",
                size=32,
                weight=ft.FontWeight.BOLD,
                text_align=ft.TextAlign.CENTER,
                color=ft.Colors.WHITE,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.top_left,
                end=ft.alignment.bottom_right,
                colors=[self.palette["primary"], self.palette["secondary"]],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )
        
        # Email display
        masked_email = self._mask_email(self.email)
        email_display = ft.Text(
            f"Enter the code sent to:\n{masked_email}",
            size=16,
            color=self.palette["on_surface"],
            text_align=ft.TextAlign.CENTER,
            weight=ft.FontWeight.W_500,
        )
        
        # OTP container
        otp_container = ft.Row(
            controls=self.otp_boxes,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
        )
        
        # Back button
        back_btn = ft.TextButton(
            "← Back",
            on_click=lambda e: self.on_back() if callable(self.on_back) else None,
            style=ft.ButtonStyle(
                text_style=ft.TextStyle(
                    color=self.palette["primary"],
                    weight=ft.FontWeight.W_500
                )
            ),
        )
        
        # Create form column
        form_column = ft.Column(
            controls=[
                title,
                ft.Container(height=20),
                email_display,
                ft.Container(height=30),
                otp_container,
                ft.Container(height=30),
                ft.Container(
                    content=back_btn,
                    alignment=ft.alignment.center
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        
        return form_column
    
    def set_correct_otp(self, otp: str):
        """Set the correct OTP."""
        self.correct_otp = otp
# ============ Main App Class with All Functionality ============
class CLCKenyaApp:
    """Main application class managing all screens and functionality."""
    
    def __init__(self, page: ft.Page):
        self.page = page
        self.palette = PALETTE
        self.current_user = None
        self.is_admin = False
        
        # Initialize all screen managers
        self.about_screen = AboutScreen(self)
        self.user_chat_screen = UserChatScreen(self)
        self.admin_chat_screen = AdminChatScreen(self)
        self.admin_settings_screen = AdminSettingsScreen(self)
        self.user_settings_screen = UserSettingsScreen(self)
        self.admin_inbox_screen = AdminInboxScreen(self)
        self.user_inbox_screen = UserInboxScreen(self)
        self.login_screen = LoginScreen(page, self.on_login_success, self.show_registration, self.show_password_reset)
        # Pass app and a completion callback so registration can redirect after successful signup
        try:
            self.registration_screen = Registration(page, app=self, on_registration_complete=self.show_login_screen)
        except Exception:
            # Fallback to simple construction if something unexpected occurs
            self.registration_screen = Registration(page)
        self.password_reset_flow = PasswordResetFlow(page, self.show_login_screen, app=self)
        
        # Navigation state
        self.current_screen = None
        self.screen_history = []
        
        # Initialize a single global background image used by all screens
        try:
            self._init_global_background()
        except Exception:
            # If background init fails, continue without breaking the app
            pass
    
    def on_login_success(self, user_data=None):
        """Handle successful login and determine user type."""
        # In real app, you'd get this from your auth system
        self.current_user = user_data or {"email": "user@example.com", "name": "Test User"}
        self.is_admin = user_data.get("is_admin", False) if user_data else False
        
        user_email = self.current_user.get("email", "unknown")
        is_admin_str = "admin" if self.is_admin else "regular"
        print(f"[DEBUG] CLCKenyaApp.on_login_success: user_email={user_email}, is_admin={is_admin_str}")
        
        # Show appropriate main screen based on user type
        if self.is_admin:
            print(f"[DEBUG] CLCKenyaApp.on_login_success: showing admin dashboard")
            self.show_admin_dashboard()
        else:
            print(f"[DEBUG] CLCKenyaApp.on_login_success: showing user dashboard")
            self.show_user_dashboard()
    
    def show_login_screen(self):
        """Show login screen."""
        self.current_screen = "login"
        self.page.clean()
        # Ensure the login screen callback is correctly wired to the app
        try:
            self.login_screen.on_login_success = self.on_login_success
            print(f"[DEBUG] CLCKenyaApp.show_login_screen: wired login_screen.on_login_success = {self.login_screen.on_login_success}")
        except Exception:
            pass
        # Add the login screen wrapped with the global background
        try:
            wrapped = self._wrap_with_global_background(self.login_screen.build())
            self.page.add(wrapped)
        except Exception:
            # Fallback to adding the raw built screen
            self.page.add(self.login_screen.build())
    
    def show_registration(self):
        """Show registration screen."""
        self.current_screen = "registration"
        self.page.clean()
        try:
            wrapped = self._wrap_with_global_background(self.registration_screen.build())
            self.page.add(wrapped)
        except Exception:
            self.page.add(self.registration_screen.build())
    
    def show_password_reset(self):
        """Show password reset flow screen."""
        self.current_screen = "password_reset"
        try:
            self.password_reset_flow.show_email_input_screen()
        except Exception as e:
            print(f"show_password_reset error: {e}")
            traceback.print_exc()
    
    def show_user_dashboard(self):
        """Show user dashboard/main screen."""
        self.current_screen = "user_dashboard"
        print(f"[DEBUG] CLCKenyaApp.show_user_dashboard: building user dashboard")
        self.page.clean()
        print(f"[DEBUG] CLCKenyaApp.show_user_dashboard: page cleaned")
        
        # Create user dashboard with navigation
        dashboard = UserDashboard(self)
        print(f"[DEBUG] CLCKenyaApp.show_user_dashboard: UserDashboard instance created")
        
        built_screen = dashboard.build()
        print(f"[DEBUG] CLCKenyaApp.show_user_dashboard: dashboard.build() returned: {type(built_screen)}")
        
        try:
            wrapped = self._wrap_with_global_background(built_screen)
            self.page.add(wrapped)
        except Exception:
            self.page.add(built_screen)
        print(f"[DEBUG] CLCKenyaApp.show_user_dashboard: built_screen added to page")
    
    def show_admin_dashboard(self):
        """Show admin dashboard/main screen."""
        self.current_screen = "admin_dashboard"
        print(f"[DEBUG] CLCKenyaApp.show_admin_dashboard: building admin dashboard")
        self.page.clean()
        print(f"[DEBUG] CLCKenyaApp.show_admin_dashboard: page cleaned")
        
        # Create admin dashboard with navigation
        dashboard = AdminDashboard(self)
        print(f"[DEBUG] CLCKenyaApp.show_admin_dashboard: AdminDashboard instance created")
        
        built_screen = dashboard.build()
        print(f"[DEBUG] CLCKenyaApp.show_admin_dashboard: dashboard.build() returned: {type(built_screen)}")
        
        try:
            wrapped = self._wrap_with_global_background(built_screen)
            self.page.add(wrapped)
        except Exception:
            self.page.add(built_screen)
        print(f"[DEBUG] CLCKenyaApp.show_admin_dashboard: built_screen added to page")
    
    def logout(self):
        """Logout current user."""
        self.current_user = None
        self.is_admin = False
        self.screen_history.clear()
        self.show_login_screen()

    # --- Global background helpers ---
    def _get_page_width(self, default=800):
        try:
            return self.page.window_width or self.page.width or default
        except AttributeError:
            return getattr(self.page, 'width', None) or default

    def _get_page_height(self, default=800):
        try:
            return self.page.window_height or self.page.height or default
        except AttributeError:
            return getattr(self.page, 'height', None) or default

    def _init_global_background(self):
        """Create a single global background image and wire page resize to update it."""
        try:
            # Create a global background placeholder via the central stub
            self.background_image = create_background_control(self.page, BACKGROUND_PATHS[0] if BACKGROUND_PATHS else None)

            prev_on_resize = getattr(self.page, 'on_resize', None)

            def _on_resize(e=None):
                try:
                    new_w = self._get_page_width()
                    new_h = self._get_page_height()
                    try:
                        self.background_image.width = new_w
                        self.background_image.height = new_h
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    if callable(prev_on_resize):
                        prev_on_resize(e)
                except Exception:
                    pass
                try:
                    self.page.update()
                except Exception:
                    pass

            self.page.on_resize = _on_resize
        except Exception:
            # Preserve app startup even if background can't be initialized
            self.background_image = ft.Container(expand=True)

    def _create_screen_background(self):
        """Create a fresh background container for a screen.
        Each screen gets its own background instance created on-demand.
        """
        try:
            # Delegate creation to the central stub so screens can get a consistent placeholder
            return create_background_control(self.page, BACKGROUND_PATHS[0] if BACKGROUND_PATHS else None)
        except Exception:
            return ft.Container(expand=True)

    def _wrap_with_global_background(self, built_screen):
        """Return the built screen as-is.
        Screens now handle their own background Stack internally.
        """
        try:
            return built_screen
        except Exception:
            return built_screen


# ============ Base Screen Class ============
class BaseScreen:
    """Base class for all screens with common functionality."""
    
    def __init__(self, app: CLCKenyaApp):
        self.app = app
        self.page = app.page
        self.palette = app.palette
    
    def _get_page_width(self):
        """Safely get page width, handling various Flet version attributes."""
        try:
            return self.page.window_width or self.page.width or 400
        except AttributeError:
            return getattr(self.page, 'width', None) or 400
    
    def _get_page_height(self):
        """Safely get page height, handling various Flet version attributes."""
        try:
            return self.page.window_height or self.page.height or 800
        except AttributeError:
            return getattr(self.page, 'height', None) or 800
    
    def build_screen_container(self, content, title=None):
        """Build a consistent screen container with stacked backgrounds and styling.
        Optimized for small phones: persistent backgrounds, larger inputs, tighter spacing.
        """
        # Detect screen size for responsive adjustments
        page_width = self._get_page_width()
        is_small_phone = page_width < 400
        
        # Responsive values
        padding = 10 if is_small_phone else 20
        margin_between = 8 if is_small_phone else 15
        overlay_margin = 5 if is_small_phone else 10
        
        # Header with title and back button
        header = self._build_header(title, is_small_phone) if title else ft.Container(height=0)
        
        # Main content column with responsive spacing
        main_column = ft.Column(
            controls=[header, content] if title else [content],
            expand=True,
            scroll=ft.ScrollMode.ADAPTIVE,
            spacing=margin_between,
        )
        
        # Responsive content container (keeps padding minimal on small screens)
        content_container = ft.Container(
            content=main_column,
            expand=True,
            padding=padding,
        )
        
        # Return content on top of the global app background. The app-level
        # background is created by CLCKenyaApp and will occupy the bottom
        # layer; here we only provide a semi-transparent overlay and the
        # content container so each screen benefits from consistent global
        # imagery without creating its own copy.
        return ft.Stack(
            expand=True,
            controls=[
                # Overlay (darker on small screens for better text contrast)
                ft.Container(
                    expand=True,
                    bgcolor=ft.colors.with_opacity(0.4 if is_small_phone else 0.3, self.palette["background"]),
                ),
                # Content (on top of overlay)
                content_container,
            ],
        )
    
    def _build_header(self, title, is_small_phone=False):
        """Build screen header with title and back button, optimized for small screens."""
        header_padding = 8 if is_small_phone else 10
        header_size = 20 if is_small_phone else 24
        
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.ARROW_BACK,
                        icon_color=self.palette["primary"],
                        icon_size=20,
                        on_click=lambda e: self._go_back(),
                    ),
                    ft.Text(
                        title,
                        size=header_size,
                        weight=ft.FontWeight.BOLD,
                        color=self.palette["on_surface"],
                    ),
                    ft.Container(expand=True),  # Spacer
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=header_padding,
            bgcolor=ft.colors.with_opacity(0.85, self.palette["surface"]),
            border_radius=10,
            margin=ft.margin.only(bottom=8 if is_small_phone else 10),
        )
    
    def get_responsive_input_height(self):
        """Get responsive TextField height based on screen size."""
        page_width = self._get_page_width()
        return 60 if page_width < 400 else 50
    
    def get_responsive_spacing(self):
        """Get responsive spacing values for small screens."""
        page_width = self._get_page_width()
        if page_width < 400:
            return {"padding": 8, "margin": 5, "height": 60}
        else:
            return {"padding": 15, "margin": 10, "height": 50}
    
    def build_stacked_backgrounds(self):
        """Build a column of stacked background images filling screen width."""
        # Create a single background image sized to the current page height so it
        # sits in the same Stack as overlays and forms. Update its dimensions on
        # resize while preserving any existing on_resize handler.
        page_h = self._get_page_height()
        page_w = self._get_page_width()

        if BACKGROUND_PATHS:
            background_image = create_background_control(self.page, BACKGROUND_PATHS[0] if BACKGROUND_PATHS else None)
        else:
            background_image = ft.Container(expand=True)

        # Preserve any previous on_resize handler
        prev_on_resize = getattr(self.page, "on_resize", None)

        def _on_resize(e=None):
            try:
                new_h = self._get_page_height()
                new_w = self._get_page_width()
                # update image dimensions where possible
                try:
                    background_image.height = new_h
                    background_image.width = new_w
                except Exception:
                    pass
            except Exception:
                pass
            # Call previous handler if present
            try:
                if callable(prev_on_resize):
                    prev_on_resize(e)
            except Exception:
                pass
            try:
                self.page.update()
            except Exception:
                pass

        # Attach the resize handler (per-screen override is acceptable)
        self.page.on_resize = _on_resize

        return background_image
    
    def build_main_container(self, initial_screen="chat"):
        """Build a main container with bottom navigation bar and content area.
        
        The container has:
        - A content area at the top that holds the current screen
        - A bottom navigation bar with icons for switching screens
        
        Args:
            initial_screen: the initial screen to show (e.g., 'chat', 'inbox', 'settings')
        
        Returns:
            A Column with content area and bottom nav bar
        """
        print(f"[DEBUG] build_main_container: initializing with initial_screen='{initial_screen}'")
        # Current screen state (use app attribute if available)
        current_screen_state = {"screen": initial_screen}
        
        # Screen instances (lazy-loaded or pre-created)
        screens = {}
        
        # Content area that holds the current screen
        content_area = ft.Container(
            expand=True,
            content=ft.Container(expand=True),  # placeholder
        )
        
        def show_screen(screen_name: str):
            """Switch to a screen and update the content area."""
            try:
                print(f"[DEBUG] show_screen: loading screen '{screen_name}'")
                
                # Create or retrieve the screen instance
                if screen_name not in screens:
                    print(f"[DEBUG] show_screen: screen '{screen_name}' not cached, creating new instance")
                    # Choose admin or user variants for screens that differ
                    is_admin = getattr(self.app, 'is_admin', False)
                    if screen_name == "chat":
                        if is_admin and hasattr(self.app, 'admin_chat_screen'):
                            screens[screen_name] = self.app.admin_chat_screen.build()
                        elif hasattr(self.app, 'user_chat_screen'):
                            screens[screen_name] = self.app.user_chat_screen.build()
                        else:
                            screens[screen_name] = ft.Text("Chat")
                    elif screen_name == "inbox":
                        if is_admin and hasattr(self.app, 'admin_inbox_screen'):
                            screens[screen_name] = self.app.admin_inbox_screen.build()
                        elif hasattr(self.app, 'user_inbox_screen'):
                            screens[screen_name] = self.app.user_inbox_screen.build()
                        else:
                            screens[screen_name] = ft.Text("Inbox")
                    elif screen_name == "settings":
                        if is_admin and hasattr(self.app, 'admin_settings_screen'):
                            screens[screen_name] = self.app.admin_settings_screen.build()
                        elif hasattr(self.app, 'user_settings_screen'):
                            screens[screen_name] = self.app.user_settings_screen.build()
                        else:
                            screens[screen_name] = ft.Text("Settings")
                    elif screen_name == "about":
                        screens[screen_name] = self.app.about_screen.build() if hasattr(self.app, 'about_screen') else ft.Text("About")
                    print(f"[DEBUG] show_screen: screen instance created for '{screen_name}'")
                else:
                    print(f"[DEBUG] show_screen: screen '{screen_name}' already cached, retrieving")
                
                # Debug: scan the built screen for any None entries in controls (Flet cannot build None)
                def _find_none(obj, path="root", seen=None):
                    if seen is None:
                        seen = set()
                    try:
                        oid = id(obj)
                        if oid in seen:
                            return []
                        seen.add(oid)
                    except Exception:
                        pass
                    found = []
                    if obj is None:
                        return [path]
                    # If it's a list/tuple, check items
                    if isinstance(obj, (list, tuple)):
                        for i, it in enumerate(obj):
                            found += _find_none(it, f"{path}[{i}]", seen)
                        return found
                    # If it has 'controls' attribute (Flet Control), scan it
                    try:
                        ctrs = getattr(obj, 'controls', None)
                        if ctrs is not None:
                            found += _find_none(ctrs, path + ".controls", seen)
                    except Exception:
                        pass
                    # If it has 'content', scan
                    try:
                        content = getattr(obj, 'content', None)
                        if content is not None:
                            found += _find_none(content, path + ".content", seen)
                    except Exception:
                        pass
                    return found

                built = screens[screen_name]
                none_paths = _find_none(built)
                if none_paths:
                    print(f"[ERROR] Found None in controls for screen '{screen_name}':")
                    for p in none_paths[:20]:
                        print("  -", p)
                    # still attempt to set content to see full traceback
                # Update the content area
                content_area.content = built
                current_screen_state["screen"] = screen_name
                self.page.update()
                print(f"[DEBUG] show_screen: content_area updated and page refreshed for '{screen_name}'")
            except Exception as ex:
                print(f"[ERROR] Error switching to screen {screen_name}: {ex}")
                traceback.print_exc()
        
        # Bottom navigation bar with icons (About first, no Profile icon)
        def _make_nav_button(name, icon):
            btn = ft.IconButton(
                icon=icon,
                icon_color=self.palette["primary"],
                tooltip=name.capitalize(),
            )

            def _on_click(e, screen=name, button=btn):
                # simple click animation: briefly highlight the button
                try:
                    button.bgcolor = ft.Colors.with_opacity(0.15, self.palette["primary"])
                    self.page.update()
                except Exception:
                    pass
                # switch screen
                show_screen(screen)

            btn.on_click = _on_click
            return btn

        nav_items = [
            ("about", ft.Icons.INFO),
            ("chat", ft.Icons.CHAT),
            ("inbox", ft.Icons.INBOX),
            ("settings", ft.Icons.SETTINGS),
        ]

        nav_buttons = [
            _make_nav_button(name, icon) for name, icon in nav_items
        ]

        bottom_nav = ft.Container(
            content=ft.Row(
                controls=nav_buttons,
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=10,
            bgcolor=ft.Colors.with_opacity(0.9, self.palette["surface"]),
            border_radius=ft.border_radius.only(top_left=15, top_right=15),
            height=60,
        )
        
        # Main layout: content on top, bottom nav at bottom.
        # Keep bottom nav visually fixed by giving the content area a bottom
        # margin equal to the nav height so content won't be covered when
        # scrolling. Using a Column keeps layout simple and compatible with
        # all Flet versions (avoids Positioned which may not exist).
        content_area.margin = ft.margin.only(bottom=60)
        main_layout = ft.Column(
            controls=[
                content_area,
                bottom_nav,
            ],
            expand=True,
            spacing=0,
        )
        
        # Show initial screen
        print(f"[DEBUG] build_main_container: showing initial screen '{initial_screen}'")
        show_screen(initial_screen)
        print(f"[DEBUG] build_main_container: completed, returning main_layout")
        
        return main_layout
    
    def _build_profile_screen(self):
        """Build a simple profile screen showing logged-in user info."""
        user = self.app.current_user or {"email": "user@example.com", "name": "User"}
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "My Profile",
                        size=24,
                        weight=ft.FontWeight.BOLD,
                        color=self.palette["on_surface"],
                    ),
                    ft.Container(height=20),
                    ft.ListTile(
                        title=ft.Text("Name", color=self.palette["on_surface"]),
                        subtitle=ft.Text(user.get("name", "N/A"), color=self.palette["accent"]),
                    ),
                    ft.ListTile(
                        title=ft.Text("Email", color=self.palette["on_surface"]),
                        subtitle=ft.Text(user.get("email", "N/A"), color=self.palette["accent"]),
                    ),
                    ft.Container(height=20),
                    ft.ElevatedButton(
                        "Logout",
                        width=200,
                        bgcolor=self.palette["danger"],
                        color=ft.Colors.WHITE,
                        icon=ft.Icons.LOGOUT,
                        on_click=lambda e: self.app.logout(),
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                scroll=ft.ScrollMode.ADAPTIVE,
            ),
            padding=20,
        )
    
    def _go_back(self):
        """Navigate back to previous screen."""
        if hasattr(self.app, 'screen_history') and self.app.screen_history:
            previous_screen = self.app.screen_history.pop()
            # Implement navigation logic based on your app structure
            pass


# ============ About Screen ============
class AboutScreen(BaseScreen):
    """About CLC Kenya screen with animated sections."""

    def build(self):
        content = ft.Column(
            controls=[
                self._animated(self._build_hero_section(), delay=0),
                self._animated(self._build_info_section(), delay=80),
                self._animated(self._build_contact_section(), delay=160),
                self._animated(self._build_team_section(), delay=240),
            ],
            scroll=ft.ScrollMode.ADAPTIVE,
            spacing=20,
        )

        return self.build_screen_container(content, "About CLC Kenya")

    # ---------------------------------------------------------------------
    # Animation Wrapper
    # ---------------------------------------------------------------------
    def _animated(self, control, delay=0, duration=400):
        """
        Wrap any control with fade + slide animation.
        """
        return ft.AnimatedSwitcher(
            content=control,
            transition=ft.AnimatedSwitcherTransition.FADE,
            duration=duration,
            reverse_duration=duration,
            switch_in_curve=ft.AnimationCurve.EASE_OUT,
            switch_out_curve=ft.AnimationCurve.EASE_IN,
        )

    # ---------------------------------------------------------------------
    # Sections
    # ---------------------------------------------------------------------

    def _build_hero_section(self):
        """Main hero header with gradient title."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.ShaderMask(
                        content=ft.Text(
                            "CLC KENYA",
                            size=40,
                            weight=ft.FontWeight.BOLD,
                            text_align=ft.TextAlign.CENTER,
                            color=ft.colors.WHITE,
                        ),
                        shader=ft.LinearGradient(
                            colors=[self.palette["primary"], self.palette["secondary"]],
                        ),
                        blend_mode=ft.BlendMode.SRC_IN,
                    ),
                    ft.Text(
                        "In all things to love and to serve",
                        size=18,
                        color=self.palette["accent"],
                        text_align=ft.TextAlign.CENTER,
                        weight=ft.FontWeight.W_500,
                    ),
                    ft.Container(height=20),
                    ft.Text(
                        (
                            "Christian Life Community Kenya is a national community of Christians "
                            "who seek to know, love, and serve God through the Spiritual Exercises "
                            "of St. Ignatius of Loyola."
                        ),
                        size=16,
                        color=self.palette["on_surface"],
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=10,
            ),
            padding=30,
            bgcolor=ft.colors.with_opacity(0.8, self.palette["surface"]),
            border_radius=15,
            margin=ft.margin.symmetric(horizontal=10),
            animate=ft.Animation(400, ft.AnimationCurve.EASE_OUT),
        )

    def _build_info_section(self):
        """Key info items."""
        info_items = [
            ("🎯 Mission", "To form Christian communities committed to faith, justice, and solidarity"),
            ("🙏 Spirituality", "Based on the Spiritual Exercises of St. Ignatius"),
            ("🌍 Reach", "Serving communities across all 47 counties in Kenya"),
            ("👥 Community", "Open to all Christians seeking deeper spiritual life"),
        ]

        info_cards = []
        for title, description in info_items:
            info_cards.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
                            ft.Text(description, size=14, color=self.palette["on_surface"]),
                        ],
                        spacing=5,
                    ),
                    padding=20,
                    bgcolor=ft.colors.with_opacity(0.7, self.palette["surface"]),
                    border_radius=10,
                    expand=True,
                    animate=ft.Animation(350, curve=ft.AnimationCurve.EASE_OUT),
                )
            )

        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Who We Are",
                        size=22,
                        weight=ft.FontWeight.BOLD,
                        color=self.palette["on_surface"],
                    ),
                    ft.ResponsiveRow(
                        controls=info_cards,
                        columns=2,
                        spacing=10,
                    ),
                ],
                spacing=15,
            ),
            padding=20,
        )

    def _build_contact_section(self):
        """Contact details."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Contact Information",
                        size=22,
                        weight=ft.FontWeight.BOLD,
                        color=self.palette["on_surface"],
                    ),
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.EMAIL, color=self.palette["primary"]),
                        title=ft.Text("Email", color=self.palette["on_surface"]),
                        subtitle=ft.Text("info@clckenya.org", color=self.palette["accent"]),
                    ),
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.PHONE, color=self.palette["primary"]),
                        title=ft.Text("Phone", color=self.palette["on_surface"]),
                        subtitle=ft.Text("+254 700 000000", color=self.palette["accent"]),
                    ),
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.LOCATION_ON, color=self.palette["primary"]),
                        title=ft.Text("Address", color=self.palette["on_surface"]),
                        subtitle=ft.Text("Nairobi, Kenya", color=self.palette["accent"]),
                    ),
                ],
                spacing=10,
            ),
            padding=20,
            bgcolor=ft.colors.with_opacity(0.7, self.palette["surface"]),
            border_radius=15,
            margin=ft.margin.symmetric(horizontal=10),
            animate=ft.Animation(350, ft.AnimationCurve.EASE_OUT),
        )

    def _build_team_section(self):
        """Leadership section."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Our Leadership",
                        size=22,
                        weight=ft.FontWeight.BOLD,
                        color=self.palette["on_surface"],
                    ),
                    ft.Text(
                        "Dedicated volunteers serving the community",
                        size=14,
                        color=self.palette["on_surface"],
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Container(height=10),
                    ft.Text(
                        (
                            "• National Coordinator\n"
                            "• Regional Coordinators\n"
                            "• Spiritual Directors\n"
                            "• Volunteer Team"
                        ),
                        size=14,
                        color=self.palette["on_surface"],
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=20,
            animate=ft.Animation(400, ft.AnimationCurve.EASE_OUT),
        )


# ============ User Chat Screen ============
class UserChatScreen(BaseScreen):
    """User chat interface for viewing messages from admins/community."""
    
    def __init__(self, app: CLCKenyaApp):
        super().__init__(app)
        self.messages = []
        self.current_chat = None
        self.messages_container_ref = None
        self._poller_thread = None
        
        # Appwrite bucket for media files
        self.bucket_id = os.getenv("APPWRITE_BUCKET_ID") or "chat_media"
        
        # Debug log
        self.debug_log = []

    def _debug_log(self, msg: str):
        """Add a debug message with timestamp and print to console."""
        try:
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            line = f"[UserChat:{ts}] {msg}"
            print(line)
            # Keep last 100 debug lines
            try:
                self.debug_log.append(line)
                self.debug_log = self.debug_log[-100:]
            except Exception:
                pass
        except Exception:
            pass
        
    def build(self, chat_id=None):
        """Build the user chat screen."""
        # If no chat_id provided, default to current user's id
        if chat_id:
            self.current_chat = chat_id
        else:
            current_user = getattr(self.app, 'current_user', {}) or {}
            self.current_chat = current_user.get('id') or 'all'
        
        try:
            self._debug_log(f"build(): resolved current_chat={self.current_chat}")
        except Exception:
            pass
        
        # Load initial messages
        try:
            self._load_chat_messages()
        except Exception:
            pass
        
        # Start polling for new messages
        self._start_polling()
        
        content = ft.Column(
            controls=[
                self._build_chat_header(),
                self._build_messages_list(),
                self._build_read_only_notice(),
            ],
            expand=True,
        )
        
        return self.build_screen_container(content, "Chat")
    
    def _build_chat_header(self):
        """Build chat header with participant info."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.CircleAvatar(
                        content=ft.Text("A", color=ft.Colors.WHITE),
                        bgcolor=self.palette["primary"],
                    ),
                    ft.Column(
                        controls=[
                            ft.Text("CLC Admin", weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                            ft.Text("Online", size=12, color=self.palette["success"]),
                        ],
                        spacing=0,
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.Icons.INFO_OUTLINE,
                        icon_color=self.palette["primary"],
                        tooltip="Chat Info",
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=15,
            bgcolor=ft.Colors.with_opacity(0.8, self.palette["surface"]),
            border_radius=10,
        )
    
    def _build_read_only_notice(self):
        """Build a notice indicating this is a read-only chat."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.VISIBILITY, size=16, color=self.palette["secondary"]),
                    ft.Text(
                        "Read-only mode - Contact admins for assistance",
                        size=12,
                        color=self.palette["secondary"],
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=5,
            ),
            padding=10,
            bgcolor=ft.colors.with_opacity(0.1, self.palette["secondary"]),
            border_radius=5,
            margin=ft.margin.symmetric(horizontal=15, vertical=5),
        )
    
    def _build_messages_list(self):
        """Build the messages list view with media support."""
        # Create a persistent ListView for messages
        lv = ft.ListView(
            controls=[],
            spacing=10,
            padding=10,
            expand=True,
            auto_scroll=True,
        )

        # Populate initial items from self.messages
        try:
            cards = []
            for msg in self.messages:
                bubble = self._build_message_bubble(msg)
                cards.append(bubble)
            
            lv.controls[:] = cards
        except Exception as e:
            self._debug_log(f"Error building initial messages: {e}")

        self.messages_container_ref = lv
        return ft.Container(content=lv, expand=True)

    # ============ MEDIA SUPPORT FROM BUCKETS ============
    
    def _get_file_url(self, file_id: str) -> str:
        """Get public URL for a file in Appwrite storage."""
        try:
            if appwrite_client and appwrite_client.is_configured():
                return appwrite_client.get_file_download_url(
                    bucket_id=self.bucket_id,
                    file_id=file_id
                )
            return None
        except Exception as e:
            self._debug_log(f"Error getting file URL: {e}")
            return None
    
    def _build_media_content(self, message):
        """Responsive media rendering for images, video, audio, and files."""
        attachments = message.get("attachments", [])
        if not attachments:
            return None

        blocks = []

        for a in attachments:
            file_id = a.get("uploaded_id") or a.get("file_id")
            filename = a.get("filename", "file")
            mime = (a.get("type") or "").lower()
            url = self._get_file_url(file_id)

            if not file_id or not url:
                continue

            # 🎨 IMAGE PREVIEW
            if mime.startswith("image/"):
                blocks.append(
                    ft.Container(
                        content=ft.Image(
                            src=url,
                            fit=ft.ImageFit.CONTAIN,
                            border_radius=ft.border_radius.all(12),
                        ),
                        height=180,
                        bgcolor=ft.Colors.BLACK12,
                        border_radius=12,
                        on_click=lambda e, u=url: self._open_media_viewer(u, filename),
                    )
                )

            # 🎥 VIDEO PREVIEW CARD
            elif mime.startswith("video/"):
                blocks.append(
                    ft.Container(
                        content=ft.Stack([
                            ft.Container(
                                content=ft.Icon(
                                    ft.Icons.PLAY_CIRCLE_FILL,
                                    size=48,
                                    color=ft.Colors.WHITE,
                                ),
                                alignment=ft.alignment.center,
                            ),
                            ft.Container(
                                content=ft.Text(
                                    filename,
                                    size=10,
                                    color=ft.Colors.WHITE,
                                    opacity=0.8,
                                ),
                                alignment=ft.alignment.bottom_center,
                                padding=6,
                            ),
                        ]),
                        height=160,
                        bgcolor=ft.Colors.BLACK54,
                        border_radius=12,
                        on_click=lambda e, u=url: self._open_media_viewer(u, filename),
                    )
                )

            # 🎧 AUDIO FILE
            elif mime.startswith("audio/"):
                blocks.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.AUDIOTRACK, color=self.palette["primary"]),
                            ft.Text(filename, weight=ft.FontWeight.BOLD),
                        ]),
                        padding=12,
                        bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]),
                        border_radius=10,
                        on_click=lambda e, u=url: self._download_file(u, filename),
                    )
                )

            # 📎 OTHER FILES
            else:
                blocks.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.ATTACH_FILE, color=self.palette["primary"]),
                            ft.Column([
                                ft.Text(filename, weight=ft.FontWeight.BOLD),
                                ft.Text("Tap to download", size=10),
                            ])
                        ]),
                        padding=12,
                        bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]),
                        border_radius=10,
                        on_click=lambda e, u=url: self._download_file(u, filename),
                    )
                )

        return ft.Column(blocks, spacing=8)
    def _open_media_viewer(self, url: str, filename: str):
        """Open media in a dialog for better viewing."""
        try:
            # For images, show in a dialog
            if url.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                content = ft.Column([
                    ft.Image(
                        src=url,
                        fit=ft.ImageFit.CONTAIN,
                        width=400,
                        height=400
                    ),
                    ft.Text(
                        filename,
                        size=12,
                        color=self.palette["secondary"],
                        text_align=ft.TextAlign.CENTER,
                    )
                ], spacing=10)
            else:
                # For videos and other media, show download option
                content = ft.Column([
                    ft.Icon(ft.Icons.FILE_DOWNLOAD, size=48, color=self.palette["primary"]),
                    ft.Text(f"Download: {filename}", weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER),
                    ft.Text("Click the button below to download this file", size=12, text_align=ft.TextAlign.CENTER),
                    ft.TextButton(
                        "Download File", 
                        icon=ft.Icons.DOWNLOAD,
                        on_click=lambda e: self._download_file(url, filename)
                    )
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=15)
            
            dialog = ft.AlertDialog(
                title=ft.Text("Media Viewer"),
                content=content,
                actions=[
                    ft.TextButton("Close", on_click=lambda e: self._close_dialog())
                ]
            )
            
            self.page.dialog = dialog
            dialog.open = True
            self.page.update()
            
        except Exception as e:
            self._debug_log(f"Error opening media viewer: {e}")
            self._show_snackbar("Could not open media")
    
    def _download_file(self, url: str, filename: str):
        """Trigger file download."""
        try:
            self._debug_log(f"Downloading file: {filename} from {url}")
            self._show_snackbar(f"Downloading {filename}...")
            
            # For web, we can open in new tab
            import webbrowser
            webbrowser.open(url)
            
            # Close the dialog after starting download
            self._close_dialog()
            
        except Exception as e:
            self._debug_log(f"Download error: {e}")
            self._show_snackbar("Download failed")
    
    def _close_dialog(self):
        """Close the current dialog."""
        if self.page and self.page.dialog:
            self.page.dialog.open = False
            self.page.update()

    def _show_snackbar(self, message: str):
        """Show a snackbar message."""
        try:
            if self.page:
                self.page.snack_bar = ft.SnackBar(
                    content=ft.Text(message),
                    bgcolor=self.palette["primary"]
                )
                self.page.snack_bar.open = True
                self.page.update()
        except Exception as e:
            self._debug_log(f"Snackbar error: {e}")

    # ============ ENHANCED MESSAGE DISPLAY ============
    
    def _build_message_bubble(self, message):
        """Beautiful, responsive message bubble with media support."""

        is_user = message.get("is_own", False)

        # Colors
        bg = (
            ft.colors.with_opacity(0.95, self.palette["primary"])
            if is_user
            else ft.colors.with_opacity(0.85, self.palette["surface"])
        )
        fg = (
            ft.Colors.WHITE
            if is_user
            else self.palette["on_surface"]
        )

        # Alignment (right for user, left for admin)
        alignment = (
            ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START
        )

        # Bubble max width for responsiveness
        max_bubble_width = 320

        # Media block
        media_block = self._build_media_content(message)

        # Layout content
        content = []

        # Sender (admin only)
        if not is_user:
            content.append(
                ft.Text(
                    message.get("sender", "Admin"),
                    size=12,
                    color=self.palette["secondary"],
                    weight=ft.FontWeight.BOLD,
                )
            )

        # Message text
        text = message.get("text") or message.get("content") or ""
        if text:
            content.append(
                ft.Text(
                    text,
                    color=fg,
                    size=14,
                    selectable=True,
                    weight=ft.FontWeight.W_400,
                )
            )

        # Caption
        caption = message.get("caption", "")
        if caption:
            content.append(
                ft.Text(
                    caption,
                    color=fg,
                    size=12,
                    italic=True,
                    opacity=0.8,
                )
            )

        # Media (image/video/audio/doc)
        if media_block:
            content.append(media_block)

        # Timestamp
        content.append(
            ft.Text(
                message.get("timestamp", ""),
                size=10,
                color=fg,
                opacity=0.7,
            )
        )

        return ft.Row(
            alignment=alignment,
            controls=[
                ft.Container(
                    content=ft.Column(content, spacing=8),
                    padding=12,
                    width=max_bubble_width,
                    border_radius=ft.border_radius.all(14) if is_user else ft.border_radius.all(14),
                    bgcolor=bg,
                    shadow=ft.BoxShadow(
                        blur_radius=8,
                        spread_radius=1,
                        color=ft.colors.with_opacity(0.25, ft.Colors.BLACK),
                        offset=ft.Offset(2, 2),
                    ),
                    margin=ft.margin.symmetric(vertical=4, horizontal=8),
                    animate=ft.Animation(350, curve=ft.AnimationCurve.EASE_OUT),
                )
            ]
        )

    # ============ MESSAGE LOADING AND POLLING (NO FILTERING) ============
    
    def _start_polling(self):
        """Start polling for new messages."""
        try:
            if not getattr(self, '_poller_thread', None):
                def _poll_loop():
                    try:
                        count = 0
                        while True:
                            try:
                                count += 1
                                # No longer need chat_id for filtering
                                if count % 5 == 0:
                                    self._debug_log(f"Poller iteration={count} - loading ALL messages")
                                else:
                                    print(f"[UserChat:poll] iter={count}")
                                
                                try:
                                    self._load_chat_messages()
                                except Exception as ex:
                                    try:
                                        self._debug_log(f"Poller: _load_chat_messages exception: {ex}")
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            time.sleep(3)
                    except Exception:
                        pass

                t = threading.Thread(target=_poll_loop, daemon=True)
                t.start()
                self._poller_thread = t
        except Exception:
            pass

    def _load_chat_messages(self):
        """Load ALL chat messages without any filtering."""
        try:
            self._debug_log(f"_load_chat_messages: loading ALL messages (no filtering)")
            
            # Initialize change tracker if needed
            if not hasattr(self, '_last_message_ids'):
                self._last_message_ids = set()
            
            # Fetch all messages from Appwrite
            docs = []
            try:
                if appwrite_client and appwrite_client.is_configured():
                    docs = appwrite_client.get_messages(
                        database_id=APPWRITE_DATABASE_ID_DEFAULT,
                        collection_id=APPWRITE_COLLECTION_ID_DEFAULT,
                        limit=500
                    )
                    self._debug_log(f"Fetched {len(docs)} documents from Appwrite")
                else:
                    self._debug_log("Appwrite not configured")
                    docs = []
            except Exception as ex:
                self._debug_log(f"Error fetching from Appwrite: {ex}")
                docs = []

            # Map ALL documents to message dicts (NO FILTERING)
            mapped = []
            current_user_id = str(getattr(self.app, 'current_user', {}).get('id', 'unknown'))
            
            for d in docs or []:
                msg_id = d.get('id') or d.get('$id')
                
                # Parse timestamp
                raw_ts = d.get('timestamp') or d.get('createdAt') or d.get('time')
                ts_numeric = 0
                human = ''
                
                try:
                    if isinstance(raw_ts, (int, float)):
                        ts_numeric = int(raw_ts)
                    elif isinstance(raw_ts, str) and raw_ts.isdigit():
                        ts_numeric = int(raw_ts)
                    else:
                        try:
                            parsed_dt = datetime.datetime.fromisoformat(str(raw_ts))
                            ts_numeric = int(parsed_dt.timestamp())
                        except Exception:
                            ts_numeric = 0
                    
                    # Format human-readable timestamp
                    if ts_numeric > 0:
                        human = datetime.datetime.fromtimestamp(ts_numeric).strftime('%b %d, %I:%M %p')
                    else:
                        human = str(raw_ts)
                except Exception:
                    human = str(raw_ts)

                # Get sender info for is_own calculation
                sender_id = str(d.get('sender_id') or '')
                is_own = (sender_id == current_user_id)
                
                # INCLUDE ALL MESSAGES - NO FILTERING
                mapped.append({
                    'id': msg_id,
                    'sender': d.get('sender_name') or d.get('sender') or d.get('sender_id') or 'Unknown',
                    'sender_id': sender_id,
                    'text': d.get('content') or d.get('text') or d.get('message') or '',
                    'timestamp_raw': ts_numeric,
                    'timestamp': human,
                    'is_own': is_own,
                    'attachments': d.get('attachments', []),
                    'caption': d.get('caption', ''),
                })

            self._debug_log(f"Mapped {len(mapped)} messages (ALL messages, no filtering)")

            # Sort by timestamp (oldest first)
            try:
                mapped.sort(key=lambda x: x.get('timestamp_raw', 0))
                self._debug_log(f"Sorted {len(mapped)} messages by timestamp")
            except Exception as sort_ex:
                self._debug_log(f"Sort error: {sort_ex}")

            # Check if messages changed
            current_ids = set(m.get('id') for m in mapped if m.get('id'))
            has_changes = (current_ids != self._last_message_ids) or (len(mapped) != len(self.messages))
            
            self._debug_log(f"Change detection: current={len(current_ids)} ids, previous={len(self._last_message_ids)} ids, has_changes={has_changes}")
            
            # Update stored messages and message ID tracker
            self.messages = mapped
            self._last_message_ids = current_ids

            # Update UI if there are changes
            if has_changes and self.page and getattr(self, 'messages_container_ref', None):
                self._refresh_messages_display()
            elif not has_changes:
                self._debug_log("No message changes detected; skipping ListView update")
                
        except Exception as e:
            self._debug_log(f'Error in _load_chat_messages: {e}')
            traceback.print_exc()

    def _refresh_messages_display(self):
        """Refresh the messages display with current messages."""
        try:
            lv = self.messages_container_ref
            cards = []
            for msg in self.messages:
                bubble = self._build_message_bubble(msg)
                cards.append(bubble)
            
            # Replace controls in-place
            lv.controls[:] = cards
            self._debug_log(f"ListView updated with {len(cards)} message cards")
            
            # Auto-scroll to the last message
            try:
                if cards:
                    lv.scroll_to(offset=-1, duration=300)
            except Exception:
                pass
            
            try:
                if self.page:
                    self.page.update()
            except Exception:
                pass
                
        except Exception as ex:
            self._debug_log(f"Error updating ListView: {ex}")

    async def _poll_messages(self):
        """Poll messages periodically for realtime updates."""
        while True:
            try:
                # No longer need current_chat check since we're loading all messages
                await asyncio.to_thread(self._load_chat_messages)
            except Exception:
                pass
            await asyncio.sleep(3)


# ============ Admin Chat Screen ============
class AdminChatScreen(BaseScreen):
    """Admin chat interface with pinning, deleting, real-time status updates, and media support."""
    
    def __init__(self, app: CLCKenyaApp):
        super().__init__(app)
        self.active_chats = []
        self.pinned_chats = []
        self.selected_chat = None
        self.selected_recipients = set()
        self.is_broadcast_mode = False
        self.show_pinned_only = False
        
        # File picker and attachment state
        self.file_picker = ft.FilePicker(on_result=self._on_file_picker_result)
        self.attached_file = None
        self.attached_files = []
        self.message_caption = ""
        
        # Real-time status tracking
        self.tracked_messages = set()  # Messages we're tracking for status updates
        self.status_poll_interval = 2  # seconds for status polling
        self._status_poller = None
        self._message_poller = None
        
        # Message input state
        self.message_composer = None
        
        # Debug helper
        self.debug_log = []
        
        # Messages list
        self.messages = []
        self.messages_container_ref = None
        
        # Appwrite bucket ID for media files
        self.bucket_id = os.getenv("APPWRITE_BUCKET_ID") or "chat_media"
        
    def _debug_log(self, msg: str):
        """Add debug message with timestamp."""
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        log_msg = f"[{ts}] {msg}"
        self.debug_log.append(log_msg)
        self.debug_log = self.debug_log[-50:]
        print(log_msg)

    # ============ REAL-TIME STATUS UPDATES ============
    
    def build(self):
        """Build the admin screen and start real-time polling."""
        # Start polling when screen is built
        self._start_polling()
        return self._build_admin_chat_interface()
    
    def _start_polling(self):
        """Start real-time polling for messages and status updates."""
        # Start message polling
        if not self._message_poller:
            self._message_poller = threading.Thread(
                target=self._message_poll_loop, 
                daemon=True
            )
            self._message_poller.start()
        
        # Start status polling (more frequent for ticks)
        if not self._status_poller:
            self._status_poller = threading.Thread(
                target=self._status_poll_loop, 
                daemon=True
            )
            self._status_poller.start()
    
    def _message_poll_loop(self):
        """Poll for new messages every 5 seconds."""
        while True:
            try:
                if hasattr(self, 'page') and self.page:
                    self._load_chat_messages()
            except Exception as e:
                self._debug_log(f"Message poll error: {e}")
            time.sleep(5)
    
    def _status_poll_loop(self):
        """Poll for status updates every 2 seconds (for real-time ticks)."""
        while True:
            try:
                if hasattr(self, 'page') and self.page and self.tracked_messages:
                    self._update_message_statuses()
            except Exception as e:
                self._debug_log(f"Status poll error: {e}")
            time.sleep(self.status_poll_interval)
    
    def _update_message_statuses(self):
        """Update status of tracked messages in real-time."""
        try:
            if not self.tracked_messages or not appwrite_client:
                return
                
            # Fetch current status for tracked messages
            for message_id in list(self.tracked_messages):
                current_status = self._get_message_status(message_id)
                if current_status:
                    self._update_message_status_ui(message_id, current_status)
                    
                    # Stop tracking if message is read
                    if current_status == 'read':
                        self.tracked_messages.discard(message_id)
                        
        except Exception as e:
            self._debug_log(f"Status update error: {e}")
    
    def _get_message_status(self, message_id: str) -> str:
        """Get current status of a message from Appwrite."""
        try:
            if not appwrite_client.is_configured():
                return None
                
            doc = appwrite_client.get_document(
                database_id=APPWRITE_DATABASE_ID_DEFAULT,
                collection_id=APPWRITE_COLLECTION_ID_DEFAULT,
                document_id=message_id
            )
            
            return doc.get('status', 'sent')
        except Exception as e:
            self._debug_log(f"Error getting message status: {e}")
            return None
    
    def _update_message_status_ui(self, message_id: str, new_status: str):
        """Update the status indicator for a specific message in the UI."""
        try:
            if not hasattr(self, 'messages_container_ref'):
                return
                
            # Find the message in our local cache and update status
            for msg in self.messages:
                if msg.get('id') == message_id:
                    old_status = msg.get('status', 'sent')
                    if old_status != new_status:
                        msg['status'] = new_status
                        self._debug_log(f"Status updated: {message_id} {old_status} -> {new_status}")
                        
                        # Refresh the message display
                        self._refresh_message_display()
                    break
                    
        except Exception as e:
            self._debug_log(f"UI status update error: {e}")
    
    def _refresh_message_display(self):
        """Refresh the messages display to show updated status."""
        try:
            if (hasattr(self, 'messages_container_ref') and 
                hasattr(self, 'page') and self.page):
                
                # Rebuild message cards with updated status
                if hasattr(self.messages_container_ref, 'controls'):
                    self.messages_container_ref.controls.clear()
                    
                    for msg in self.messages:
                        card = self._build_message_card(msg)
                        self.messages_container_ref.controls.append(card)
                    
                    # Auto-scroll to latest message
                    try:
                        self.messages_container_ref.scroll_to(offset=-1, duration=300)
                    except Exception:
                        pass
                    
                    # Trigger UI update
                    self.messages_container_ref.update()
                    
        except Exception as e:
            self._debug_log(f"Refresh display error: {e}")

    # ============ MEDIA SUPPORT FROM BUCKETS ============
    
    def _upload_file_to_bucket(self, file_path: str, filename: str) -> dict:
        """Upload file to Appwrite storage bucket and return file metadata."""
        try:
            if not appwrite_client or not appwrite_client.is_configured():
                self._debug_log("Appwrite not configured for file upload")
                return None
                
            self._debug_log(f"Uploading file to bucket: {filename}")
            
            # Upload file to Appwrite storage
            file_info = appwrite_client.upload_file(
                bucket_id=self.bucket_id,
                file_path=file_path,
                file_name=filename
            )
            
            if file_info:
                self._debug_log(f"File uploaded successfully: {file_info.get('$id')}")
                return {
                    'file_id': file_info.get('$id'),
                    'filename': filename,
                    'bucket_id': self.bucket_id,
                    'url': f"/storage/buckets/{self.bucket_id}/files/{file_info.get('$id')}/view"
                }
            else:
                self._debug_log("File upload failed")
                return None
                
        except Exception as e:
            self._debug_log(f"File upload error: {e}")
            return None
    
    def _get_file_url(self, file_id: str) -> str:
        """Get public URL for a file in Appwrite storage."""
        try:
            if appwrite_client and appwrite_client.is_configured():
                return appwrite_client.get_file_download_url(
                    bucket_id=self.bucket_id,
                    file_id=file_id
                )
            return None
        except Exception as e:
            self._debug_log(f"Error getting file URL: {e}")
            return None
    
    def _build_media_content(self, message: dict):
        """Build media content for messages with attachments."""
        attachments = message.get('attachments', [])
        if not attachments:
            return None
            
        media_controls = []
        
        for attachment in attachments:
            file_id = attachment.get('uploaded_id') or attachment.get('file_id')
            filename = attachment.get('filename', 'file')
            file_type = attachment.get('type', '').lower()
            
            if file_id:
                file_url = self._get_file_url(file_id)
                if file_url:
                    if file_type.startswith('image/'):
                        # Image attachment
                        media_controls.append(
                            ft.Container(
                                content=ft.Image(
                                    src=file_url,
                                    width=200,
                                    height=150,
                                    fit=ft.ImageFit.COVER,
                                    border_radius=10,
                                ),
                                margin=ft.margin.only(bottom=5),
                                on_click=lambda e, url=file_url: self._open_media_viewer(url, filename)
                            )
                        )
                    elif file_type.startswith('video/'):
                        # Video attachment - show thumbnail with play button
                        media_controls.append(
                            ft.Container(
                                content=ft.Column([
                                    ft.Icon(ft.Icons.PLAY_CIRCLE_FILLED, size=40, color=ft.Colors.WHITE),
                                    ft.Text("Play Video", size=12, color=ft.Colors.WHITE)
                                ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                width=200,
                                height=150,
                                bgcolor=ft.Colors.BLACK54,
                                border_radius=10,
                                alignment=ft.alignment.center,
                                on_click=lambda e, url=file_url: self._open_media_viewer(url, filename)
                            )
                        )
                    elif file_type.startswith('audio/'):
                        # Audio attachment
                        media_controls.append(
                            ft.Container(
                                content=ft.Row([
                                    ft.Icon(ft.Icons.AUDIO_FILE, size=24),
                                    ft.Column([
                                        ft.Text(filename, size=12, weight=ft.FontWeight.BOLD),
                                        ft.Text("Audio File", size=10, color=self.palette['secondary'])
                                    ], spacing=2)
                                ]),
                                padding=10,
                                bgcolor=ft.colors.with_opacity(0.1, self.palette['primary']),
                                border_radius=10,
                                on_click=lambda e, url=file_url: self._download_file(url, filename)
                            )
                        )
                    else:
                        # Generic file attachment
                        media_controls.append(
                            ft.Container(
                                content=ft.Row([
                                    ft.Icon(ft.Icons.INSERT_DRIVE_FILE, size=24),
                                    ft.Column([
                                        ft.Text(filename, size=12, weight=ft.FontWeight.BOLD),
                                        ft.Text("Document", size=10, color=self.palette['secondary'])
                                    ], spacing=2)
                                ]),
                                padding=10,
                                bgcolor=ft.colors.with_opacity(0.1, self.palette['primary']),
                                border_radius=10,
                                on_click=lambda e, url=file_url: self._download_file(url, filename)
                            )
                        )
                else:
                    # Fallback for files without URL
                    media_controls.append(
                        ft.Container(
                            content=ft.Row([
                                ft.Icon(ft.Icons.INSERT_DRIVE_FILE, size=24),
                                ft.Text(filename, size=12)
                            ]),
                            padding=10,
                            bgcolor=ft.colors.with_opacity(0.1, self.palette['secondary']),
                            border_radius=10,
                        )
                    )
        
        return ft.Column(controls=media_controls, spacing=5) if media_controls else None
    
    def _open_media_viewer(self, url: str, filename: str):
        """Open media in a dialog for better viewing."""
        try:
            # For images, show in a dialog
            if url.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
                content = ft.Image(
                    src=url,
                    fit=ft.ImageFit.CONTAIN,
                    width=400,
                    height=400
                )
            else:
                # For videos and other media, show download option
                content = ft.Column([
                    ft.Text(f"Media: {filename}", weight=ft.FontWeight.BOLD),
                    ft.TextButton(
                        "Download File", 
                        on_click=lambda e: self._download_file(url, filename)
                    )
                ])
            
            dialog = ft.AlertDialog(
                title=ft.Text(filename),
                content=content,
                actions=[
                    ft.TextButton("Close", on_click=lambda e: self._close_dialog())
                ]
            )
            
            self.page.dialog = dialog
            dialog.open = True
            self.page.update()
            
        except Exception as e:
            self._debug_log(f"Error opening media viewer: {e}")
            self._show_snackbar("Could not open media")
    
    def _download_file(self, url: str, filename: str):
        """Trigger file download."""
        try:
            # In a real app, this would use platform-specific download mechanisms
            self._debug_log(f"Downloading file: {filename} from {url}")
            self._show_snackbar(f"Downloading {filename}...")
            
            # For web, we can open in new tab
            import webbrowser
            webbrowser.open(url)
            
        except Exception as e:
            self._debug_log(f"Download error: {e}")
            self._show_snackbar("Download failed")
    
    def _close_dialog(self):
        """Close the current dialog."""
        if self.page and self.page.dialog:
            self.page.dialog.open = False
            self.page.update()

    # ============ ENHANCED MESSAGE CARD WITH STATUS TICKS ============
    
    def _build_message_card(self, message):
        """Build message card with real-time status ticks and media support."""
        is_own_message = message.get('is_own', False)
        
        # Build status indicator only for admin messages
        status_indicator = self._build_status_indicator(message.get('status', 'sent')) if is_own_message else ft.Container()
        
        # Build media content if message has attachments
        media_content = self._build_media_content(message)
        
        # Message bubble styling
        bubble_color = self.palette["primary"] if is_own_message else self.palette["surface"]
        text_color = ft.Colors.WHITE if is_own_message else self.palette["on_surface"]
        
        # Build message content
        message_content = []
        
        # Add sender name for others' messages
        if not is_own_message:
            message_content.append(
                ft.Text(
                    message.get('sender', 'Unknown'),
                    size=12,
                    weight=ft.FontWeight.BOLD,
                    color=self.palette['secondary']
                )
            )
        
        # Add text content if present
        text_content = message.get('text', '') or message.get('content', '')
        if text_content:
            message_content.append(
                ft.Text(
                    text_content,
                    color=text_color,
                    selectable=True
                )
            )
        
        # Add media content if present
        if media_content:
            message_content.append(media_content)
        
        # Add caption if present
        caption = message.get('caption', '')
        if caption:
            message_content.append(
                ft.Text(
                    caption,
                    size=12,
                    color=text_color,
                    italic=True,
                    opacity=0.8
                )
            )
        
        # Add timestamp and status
        message_content.append(
            ft.Row([
                ft.Text(
                    message.get('timestamp', ''),
                    size=10,
                    color=text_color,
                    opacity=0.7
                ),
                status_indicator,
            ], spacing=5, alignment=ft.MainAxisAlignment.END)
        )
        
        # Pin indicator
        pin_indicator = ft.Icon(
            ft.Icons.PUSH_PIN,
            size=12,
            color=self.palette["warning"],
        ) if message.get("pinned") else ft.Container()
        
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row([
                                    pin_indicator,
                                    ft.Container(expand=True),  # Spacer
                                ]),
                                ft.Column(message_content, spacing=5),
                            ],
                            spacing=2,
                        ),
                        padding=15,
                        bgcolor=bubble_color,
                        border_radius=15,
                        margin=ft.margin.symmetric(horizontal=10),
                    ),
                ],
                alignment=ft.MainAxisAlignment.END if is_own_message else ft.MainAxisAlignment.START,
            ),
            padding=5,
        )
    
    def _build_status_indicator(self, status: str):
        """Build status indicator with appropriate ticks and colors."""
        if status == 'read':
            return ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DONE_ALL, size=14, color=ft.Colors.BLUE),
                    ft.Icon(ft.Icons.DONE_ALL, size=14, color=ft.Colors.BLUE),
                ],
                spacing=1,
            )
        elif status == 'delivered':
            return ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DONE_ALL, size=14, color=ft.Colors.GREY_600),
                    ft.Icon(ft.Icons.DONE_ALL, size=14, color=ft.Colors.GREY_600),
                ],
                spacing=1,
            )
        else:  # sent
            return ft.Icon(ft.Icons.DONE, size=14, color=ft.Colors.GREY_400)

    # ============ ENHANCED MESSAGE SENDING WITH MEDIA UPLOAD ============
    
    def _send_message(self, text: str = None, recipients: list = None, attachments: list = None, caption: str = ""):
        """Enhanced send message with media upload and status tracking."""
        try:
            # Use parameters from UI if not provided
            if text is None and hasattr(self, 'message_input'):
                text = (self.message_input.value or '').strip()
            
            if recipients is None:
                if self.is_broadcast_mode:
                    recipients = ["all"]
                elif self.selected_recipients:
                    recipients = list(self.selected_recipients)
                elif self.selected_chat:
                    recipients = [self.selected_chat["id"]]
                else:
                    recipients = ["all"]
            
            if attachments is None:
                attachments = self.attached_files

            self._debug_log(f"_send_message() called: text_len={len(text or '')}, recipients={len(recipients)}, attachments={len(attachments)}")
            
            # Validate inputs
            if not text and not attachments:
                self._show_snackbar("Cannot send empty message")
                return False
            
            # Only admins can send messages
            if not getattr(self.app, 'is_admin', False):
                self._show_snackbar("Only admins can send messages")
                return False

            # Prepare message payload
            current_user = self.app.current_user or {}
            sender_id = current_user.get("id", "unknown")
            sender_name = current_user.get("name", "Unknown Admin")
            
            # Upload attachments to Appwrite storage
            attachment_data = []
            if attachments:
                for att in attachments:
                    # Upload file to Appwrite bucket
                    file_meta = self._upload_file_to_bucket(att["path"], att["filename"])
                    if file_meta:
                        attachment_data.append({
                            "filename": att.get("filename", "file"),
                            "size": att.get("size", 0),
                            "type": att.get("type", "application/octet-stream"),
                            "uploaded_id": file_meta['file_id'],
                            "bucket_id": file_meta['bucket_id'],
                            "url": file_meta['url']
                        })
                        self._debug_log(f"Uploaded attachment: {att['filename']}")
                    else:
                        self._debug_log(f"Failed to upload attachment: {att['filename']}")
            
            # Prepare recipient groups
            tgt_list = recipients or ["all"]
            if isinstance(tgt_list, list):
                target_groups_str = ",".join(map(str, tgt_list)) if tgt_list else "all"
            else:
                target_groups_str = str(tgt_list)
            
            # Create message document
            message_doc = {
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content": text or "",
                "caption": caption or self.message_caption or "",
                "attachments": attachment_data,
                "target_groups": target_groups_str,
                "timestamp": int(time.time()),
                "pinned": False,
                "important": False,
                "status": "sent",  # Initial status
            }
            
            # Send to Appwrite
            created_id = None
            if appwrite_client and appwrite_client.is_configured():
                try:
                    result = appwrite_client.create_message(
                        message_doc,
                        database_id=APPWRITE_DATABASE_ID_DEFAULT,
                        collection_id=APPWRITE_COLLECTION_ID_DEFAULT
                    )
                    
                    # Extract created message ID
                    if isinstance(result, dict):
                        created_id = result.get('$id') or result.get('id')
                    else:
                        created_id = getattr(result, '$id', None) or getattr(result, 'id', None)
                    
                    self._debug_log(f"Message sent to Appwrite: {created_id}")
                    
                    # Track this message for status updates
                    if created_id:
                        self.tracked_messages.add(created_id)
                        self._debug_log(f"Tracking message for status updates: {created_id}")
                    
                except Exception as e:
                    self._debug_log(f"Appwrite send failed: {e}")
                    self._show_snackbar(f'Failed to send: {e}')
                    return False
            else:
                self._debug_log("Appwrite not configured")
                self._show_snackbar("Backend not configured")
                return False

            # Add to local messages for immediate UI feedback
            new_msg = {
                'id': created_id or f'local-{int(time.time())}',
                'sender': sender_name,
                'text': text or "",
                'caption': caption or self.message_caption or "",
                'timestamp': datetime.datetime.now().strftime('%I:%M %p'),
                'attachments': attachment_data,
                'pinned': False,
                'is_own': True,
                'status': 'sent',
            }
            
            self.messages.append(new_msg)
            self._debug_log(f"Added message to UI: {new_msg['id']}")

            # Update UI
            self._refresh_message_display()
            
            # Clear input and attachments
            if hasattr(self, 'message_input'):
                self.message_input.value = ''
            self.attached_files.clear()
            self.message_caption = ""
            self._update_attachment_display()
            
            self._show_snackbar('Message sent ✓')
            return True
            
        except Exception as e:
            self._debug_log(f"_send_message() error: {e}")
            traceback.print_exc()
            self._show_snackbar(f'Error: {e}')
            return False

    # ============ ENHANCED FILE HANDLING ============
    
    def _on_file_picker_result(self, e: ft.FilePickerResultEvent):
        """Enhanced file picker with Appwrite upload preparation."""
        try:
            if e.files:
                for f in e.files:
                    self._debug_log(f"File selected: {f.name} ({f.path})")
                    
                    # Create thumbnail for images
                    thumbnail_path = None
                    if Image and f.name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                        try:
                            img = Image.open(f.path)
                            img.thumbnail((100, 100), Image.Resampling.LANCZOS)
                            thumb_path = f.path.replace(".png", "_thumb.png").replace(".jpg", "_thumb.jpg")
                            img.save(thumb_path)
                            thumbnail_path = thumb_path
                            self._debug_log(f"Created thumbnail: {thumbnail_path}")
                        except Exception as te:
                            self._debug_log(f"Thumbnail creation failed: {te}")
                    
                    # Store file info with upload preparation
                    file_info = {
                        "path": f.path,
                        "filename": f.name,
                        "size": os.path.getsize(f.path) if os.path.exists(f.path) else 0,
                        "type": mimetypes.guess_type(f.path)[0] or "application/octet-stream",
                        "thumbnail": thumbnail_path,
                        "uploaded_id": None,  # Will be set after upload
                        "bucket_id": self.bucket_id,
                    }
                    
                    self.attached_files.append(file_info)
                    self._debug_log(f"Added file to attachments: {file_info}")
                    
                    # Update UI to show attachment thumbnails
                    self._update_attachment_display()
                    
        except Exception as e:
            self._debug_log(f"_on_file_picker_result() error: {e}")

    # ============ PRESERVE ALL EXISTING METHODS ============
    # All existing methods below remain exactly as they were...
    
    async def initialize(self):
        """Initialize the chat screen."""
        await self._load_all_chats()
        self._debug_log("AdminChatScreen.initialize(): completed")
    
    def _fetch_messages_for_recipient(self, recipient_id: str = None):
        """Fetch all messages for this user from backend with persistent timestamps and sorting by date."""
        try:
            self._debug_log(f"_fetch_messages_for_recipient({recipient_id})")
            
            if not appwrite_client or not appwrite_client.is_configured():
                self._debug_log("Appwrite not configured, returning empty")
                return []
            
            messages = appwrite_client.get_messages(
                database_id=APPWRITE_DATABASE_ID_DEFAULT,
                collection_id=APPWRITE_COLLECTION_ID_DEFAULT,
                limit=200
            )
            
            self._debug_log(f"Fetched {len(messages)} total messages from Appwrite")
            
            # Filter for this recipient and normalize timestamps
            filtered = []
            current_user = self.app.current_user or {}
            user_id = current_user.get("id", "unknown")
            
            for msg in messages:
                raw_tgs = msg.get("target_groups", [])
                if isinstance(raw_tgs, list):
                    tgs_list = raw_tgs
                elif isinstance(raw_tgs, str):
                    tgs_list = [t.strip() for t in raw_tgs.split(',') if t.strip()]
                else:
                    tgs_list = []

                if 'all' in tgs_list or user_id in tgs_list:
                    raw_ts = msg.get('timestamp') or msg.get('createdAt') or msg.get('time')
                    ts_numeric = 0
                    try:
                        if isinstance(raw_ts, (int, float)):
                            ts_numeric = int(raw_ts)
                        elif isinstance(raw_ts, str) and raw_ts.isdigit():
                            ts_numeric = int(raw_ts)
                        else:
                            try:
                                parsed_dt = datetime.datetime.fromisoformat(str(raw_ts))
                                ts_numeric = int(parsed_dt.timestamp())
                            except Exception:
                                ts_numeric = int(time.time())
                    except Exception:
                        ts_numeric = int(time.time())
                    
                    msg['timestamp_raw'] = ts_numeric
                    filtered.append(msg)
            
            try:
                filtered.sort(key=lambda x: x.get('timestamp_raw', 0))
                self._debug_log(f"Filtered to {len(filtered)} messages and sorted by date")
            except Exception as sort_ex:
                self._debug_log(f"Sort error in _fetch_messages_for_recipient: {sort_ex}")
            
            return filtered
            
        except Exception as e:
            self._debug_log(f"_fetch_messages_for_recipient() error: {e}")
            return []
class AdminChatScreen(BaseScreen):
    """Admin chat interface with pinning and deleting features."""
    
    def __init__(self, app: CLCKenyaApp):
        super().__init__(app)
        self.active_chats = []
        self.pinned_chats = []
        self.selected_chat = None
        self.selected_recipients = set()
        self.is_broadcast_mode = False
        self.show_pinned_only = False
        # File picker and attachment state
        self.file_picker = ft.FilePicker(on_result=self._on_file_picker_result)
        self.attached_file = None  # {'path':..., 'name':..., 'uploaded': {...} }
        # Polling task
        self._poller_task = None
        # Message input state
        self.message_composer = None
        self.attached_files = []  # List of attached files with metadata
        self.message_caption = ""
        
        # Debug helper
        self.debug_log = []
        
        # Messages list (will be populated on first build)
        self.messages = []
        # Reference to the messages area container for dynamic updates
        self.messages_container_ref = None
        
    def _debug_log(self, msg: str):
        """Add debug message with timestamp."""
        ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        log_msg = f"[{ts}] {msg}"
        self.debug_log.append(log_msg)
        # Keep last 50 logs
        self.debug_log = self.debug_log[-50:]
        print(log_msg)
    
    async def initialize(self):
        """Initialize the chat screen."""
        await self._load_all_chats()
        self._debug_log("AdminChatScreen.initialize(): completed")
    
    def _send_message(self, text: str, recipients: list = None, attachments: list = None, caption: str = ""):
        """Send message via Appwrite backend with full metadata."""
        try:
            self._debug_log(f"_send_message() called: text_len={len(text)}, recipients={len(recipients or [])}, attachments={len(attachments or [])}")
            
            # Validate inputs
            if not text and not attachments:
                self._debug_log("ERROR: Message empty and no attachments")
                return False
            
            # Prepare message payload
            current_user = self.app.current_user or {}
            sender_id = current_user.get("id", "unknown")
            sender_name = current_user.get("name", "Unknown Admin")
            
            # Prepare attachment info
            attachment_data = []
            if attachments:
                for att in attachments:
                    attachment_data.append({
                        "filename": att.get("filename", "file"),
                        "size": att.get("size", 0),
                        "type": att.get("type", "application/octet-stream"),
                        "uploaded_id": att.get("uploaded_id", None),
                    })
                self._debug_log(f"Prepared {len(attachment_data)} attachments for upload")
            
            # Prepare recipient groups (store as comma-separated string for Appwrite schema)
            tgt_list = recipients or ["all"]
            if isinstance(tgt_list, list):
                target_groups_str = ",".join(map(str, tgt_list)) if tgt_list else "all"
            else:
                target_groups_str = str(tgt_list)
            self._debug_log(f"Target groups: {target_groups_str}")
            
            # Create message document
            message_doc = {
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content": text,
                "caption": caption,
                "attachments": attachment_data,
                "target_groups": target_groups_str,
                "timestamp": int(time.time()),
                "pinned": False,
                "important": False,
                "read": False,
            }
            
            # Send to Appwrite
            if appwrite_client and appwrite_client.is_configured():
                try:
                    result = appwrite_client.create_message(
                        message_doc,
                        database_id=APPWRITE_DATABASE_ID_DEFAULT,
                        collection_id=APPWRITE_COLLECTION_ID_DEFAULT
                    )
                    self._debug_log(f"Message sent to Appwrite: {result}")
                    return True
                except Exception as e:
                    self._debug_log(f"Appwrite send failed: {e}")
                    return False
            else:
                self._debug_log("Appwrite not configured - storing locally only")
                return False
                
        except Exception as e:
            self._debug_log(f"_send_message() error: {e}")
            traceback.print_exc()
            return False
    
    def _fetch_messages_for_recipient(self, recipient_id: str = None):
        """Fetch all messages for this user from backend with persistent timestamps and sorting by date."""
        try:
            self._debug_log(f"_fetch_messages_for_recipient({recipient_id})")
            
            if not appwrite_client or not appwrite_client.is_configured():
                self._debug_log("Appwrite not configured, returning empty")
                return []
            
            messages = appwrite_client.get_messages(
                database_id=APPWRITE_DATABASE_ID_DEFAULT,
                collection_id=APPWRITE_COLLECTION_ID_DEFAULT,
                limit=200
            )
            
            self._debug_log(f"Fetched {len(messages)} total messages from Appwrite")
            
            # Filter for this recipient and normalize timestamps
            filtered = []
            current_user = self.app.current_user or {}
            user_id = current_user.get("id", "unknown")
            
            for msg in messages:
                raw_tgs = msg.get("target_groups", [])
                # Normalize to list for checking (backend may store string or list)
                if isinstance(raw_tgs, list):
                    tgs_list = raw_tgs
                elif isinstance(raw_tgs, str):
                    # comma-separated string
                    tgs_list = [t.strip() for t in raw_tgs.split(',') if t.strip()]
                else:
                    tgs_list = []

                # Include if 'all' in tgs_list or user_id in tgs_list
                if 'all' in tgs_list or user_id in tgs_list:
                    # Ensure persistent timestamp for sorting
                    raw_ts = msg.get('timestamp') or msg.get('createdAt') or msg.get('time')
                    ts_numeric = 0
                    try:
                        if isinstance(raw_ts, (int, float)):
                            ts_numeric = int(raw_ts)
                        elif isinstance(raw_ts, str) and raw_ts.isdigit():
                            ts_numeric = int(raw_ts)
                        else:
                            try:
                                parsed_dt = datetime.datetime.fromisoformat(str(raw_ts))
                                ts_numeric = int(parsed_dt.timestamp())
                            except Exception:
                                ts_numeric = int(time.time())  # default to now
                    except Exception:
                        ts_numeric = int(time.time())
                    
                    # Ensure message has numeric timestamp for sorting
                    msg['timestamp_raw'] = ts_numeric
                    filtered.append(msg)
            
            # Sort by timestamp (oldest first for chronological order)
            try:
                filtered.sort(key=lambda x: x.get('timestamp_raw', 0))
                self._debug_log(f"Filtered to {len(filtered)} messages and sorted by date")
            except Exception as sort_ex:
                self._debug_log(f"Sort error in _fetch_messages_for_recipient: {sort_ex}")
            
            return filtered
            
        except Exception as e:
            self._debug_log(f"_fetch_messages_for_recipient() error: {e}")
            return []
    
    def _open_emoji_picker(self, e=None):
        """Open a simple emoji picker dialog and insert the chosen emoji into the composer."""
        try:
            emojis = [
                "😀", "😃", "😄", "😁", "😆", "😅", "😂", "🙂", "😉",
                "😊", "😍", "😘", "😎", "😔", "😢", "😭", "😡", "🤔",
            ]

            def _pick(ev, em):
                try:
                    if not hasattr(self, 'message_input') or self.message_input is None:
                        # Ensure a message_input exists
                        self.message_input = ft.TextField(hint_text="Type a message...", expand=True)
                    cur = self.message_input.value or ""
                    self.message_input.value = cur + em
                except Exception:
                    pass
                # close dialog
                try:
                    if getattr(self.page, 'dialog', None):
                        self.page.dialog.open = False
                        self.page.dialog = None
                except Exception:
                    pass
                try:
                    self.page.update()
                except Exception:
                    pass

            buttons = [ft.TextButton(text=em, on_click=lambda ev, em=em: _pick(ev, em)) for em in emojis]

            dialog = ft.AlertDialog(
                title=ft.Text("Emoji picker"),
                content=ft.Column(controls=[ft.Row(controls=buttons[i:i+9], spacing=6) for i in range(0, len(buttons), 9)], spacing=6),
                actions=[ft.TextButton("Close", on_click=lambda ev: (setattr(self.page, 'dialog', None), self.page.update()))],
            )

            self.page.dialog = dialog
            dialog.open = True
            self.page.update()
        except Exception:
            try:
                traceback.print_exc()
            except Exception:
                pass

    def _open_attachment_sheet(self):
        """Show an attachment options overlay panel appended to `page.overlay`.
        Using an overlay container is the most reliable way to ensure visibility
        across Flet versions and embed modes.
        """
        try:
            self._debug_log("Opening attachment overlay panel")

            def pick_any(e=None):
                self._debug_log("Attachment: Document selected")
                self._close_attachment_overlay()
                self.file_picker.pick_files(allow_multiple=True)

            def pick_images(e=None):
                self._debug_log("Attachment: Gallery/Camera selected")
                self._close_attachment_overlay()
                self.file_picker.pick_files(allow_multiple=True)

            def pick_audio(e=None):
                self._debug_log("Attachment: Audio selected")
                self._close_attachment_overlay()
                self.file_picker.pick_files(allow_multiple=True)

            def not_supported(e=None, name="Feature"):
                self._close_attachment_overlay()
                self._show_snackbar(f"{name} not supported in this client")

            # Build overlay content
            btns = [
                ft.IconButton(icon=ft.Icons.INSERT_DRIVE_FILE, icon_size=28, icon_color=self.palette['primary'], on_click=pick_any, tooltip='Document'),
                ft.IconButton(icon=ft.Icons.CAMERA_ALT, icon_size=28, icon_color=self.palette['primary'], on_click=pick_images, tooltip='Camera'),
                ft.IconButton(icon=ft.Icons.PHOTO, icon_size=28, icon_color=self.palette['primary'], on_click=pick_images, tooltip='Gallery'),
                ft.IconButton(icon=ft.Icons.HEADSET, icon_size=28, icon_color=self.palette['primary'], on_click=pick_audio, tooltip='Audio'),
                ft.IconButton(icon=ft.Icons.PLACE, icon_size=28, icon_color=self.palette['primary'], on_click=lambda e: not_supported(e, 'Location'), tooltip='Location'),
                ft.IconButton(icon=ft.Icons.PERSON, icon_size=28, icon_color=self.palette['primary'], on_click=lambda e: not_supported(e, 'Contact'), tooltip='Contact'),
            ]

            panel = ft.Container(
                content=ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row(controls=btns[:3], alignment=ft.MainAxisAlignment.SPACE_AROUND),
                                ft.Row(controls=btns[3:], alignment=ft.MainAxisAlignment.SPACE_AROUND),
                                ft.Row(controls=[ft.TextButton("Cancel", on_click=lambda e: self._close_attachment_overlay())], alignment=ft.MainAxisAlignment.END),
                            ],
                            spacing=12,
                        ),
                        padding=12,
                    ),
                ),
                width=self.page.window_width if getattr(self.page, 'window_width', None) else 600,
                alignment=ft.alignment.bottom_center,
                padding=ft.padding.symmetric(vertical=8, horizontal=12),
            )

            # ensure overlay exists
            try:
                if getattr(self.page, 'overlay', None) is None:
                    self.page.overlay = []
            except Exception:
                pass

            # remove previous overlay if present
            try:
                if getattr(self, '_attachment_overlay', None) and self._attachment_overlay in self.page.overlay:
                    self.page.overlay.remove(self._attachment_overlay)
            except Exception:
                pass

            # append and store reference
            try:
                self.page.overlay.append(panel)
                self._attachment_overlay = panel
                self.page.update()
                self._debug_log("Attachment overlay panel appended to page.overlay")
            except Exception as e:
                self._debug_log(f"Failed to append attachment overlay: {e}")

        except Exception as e:
            self._debug_log(f"_open_attachment_sheet() error: {e}")

    def _close_bottom_sheet(self):
        """Close the attachment dialog if open."""
        try:
            if getattr(self.page, 'dialog', None):
                self.page.dialog.open = False
                self.page.update()
        except Exception:
            pass

    def _close_attachment_overlay(self):
        """Remove the attachment overlay panel from `page.overlay` if present."""
        try:
            if getattr(self, '_attachment_overlay', None) and getattr(self.page, 'overlay', None):
                try:
                    if self._attachment_overlay in self.page.overlay:
                        self.page.overlay.remove(self._attachment_overlay)
                except Exception:
                    pass
                self._attachment_overlay = None
                try:
                    self.page.update()
                except Exception:
                    pass
                self._debug_log("Attachment overlay closed")
        except Exception as e:
            self._debug_log(f"_close_attachment_overlay() error: {e}")

    def _attach_file(self, e=None):
        """Open the attachment sheet when attach button is clicked."""
        try:
            self._open_attachment_sheet()
        except Exception as ex:
            self._debug_log(f"_attach_file() error: {ex}")

    def _build_attachment_preview(self):
        """Build the attachment preview area showing selected files."""
        # Return a Container with attachment row; the row is updated dynamically
        if not self.attached_files:
            return ft.Container(height=0, visible=False)
        
        try:
            thumb_controls = []
            for idx, att in enumerate(self.attached_files):
                remove_btn = ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_size=14,
                    on_click=lambda e, i=idx: self._remove_attachment(i)
                )

                if att.get("thumbnail") and os.path.exists(att.get("thumbnail")):
                    left = ft.Image(src=att["thumbnail"], width=56, height=56, fit=ft.ImageFit.COVER)
                elif att.get("type", "").startswith('image') and os.path.exists(att.get('path')):
                    left = ft.Image(src=att["path"], width=56, height=56, fit=ft.ImageFit.COVER)
                else:
                    left = ft.Icon(ft.Icons.ATTACH_FILE, size=36)

                fname = att.get('filename') or 'file'
                fsize = att.get('size', 0)
                human_size = f"{round(fsize/1024,1)} KB" if fsize else ''

                mid_col = ft.Column(
                    controls=[
                        ft.Text(fname, size=12, weight=ft.FontWeight.BOLD, color=self.palette['on_surface']),
                        ft.Text(human_size, size=11, color=self.palette['secondary']),
                    ],
                    spacing=2
                )

                item = ft.Container(
                    content=ft.Row(controls=[left, ft.Container(width=8), mid_col, ft.Row(controls=[remove_btn])], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=8,
                    margin=ft.margin.only(right=8),
                    bgcolor=ft.colors.with_opacity(0.06, self.palette['surface']),
                    border_radius=10,
                    height=72,
                )
                thumb_controls.append(item)

            attachment_row = ft.Row(
                controls=thumb_controls,
                scroll=ft.ScrollMode.AUTO,
                height=90,
                visible=True,
            )
            self.attachment_row_ref = attachment_row
            return ft.Container(content=attachment_row, height=90, visible=True)
        except Exception as e:
            self._debug_log(f"_build_attachment_preview() error: {e}")
            return ft.Container(height=0, visible=False)
    
    def _close_emoji_dialog(self, dlg):
        """Close emoji picker dialog."""
        dlg.open = False
        self.page.update()
    
    def _on_file_picker_result(self, e: ft.FilePickerResultEvent):
        """Handle file selection from file picker."""
        try:
            if e.files:
                for f in e.files:
                    self._debug_log(f"File selected: {f.name} ({f.path})")
                    
                    # Try to create thumbnail for images
                    thumbnail_path = None
                    if Image and f.name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                        try:
                            img = Image.open(f.path)
                            img.thumbnail((100, 100), Image.Resampling.LANCZOS)
                            # Save thumbnail to temp location
                            thumb_path = f.path.replace(".png", "_thumb.png").replace(".jpg", "_thumb.jpg")
                            img.save(thumb_path)
                            thumbnail_path = thumb_path
                            self._debug_log(f"Created thumbnail: {thumbnail_path}")
                        except Exception as te:
                            self._debug_log(f"Thumbnail creation failed: {te}")
                    
                    # Store file info
                    file_info = {
                        "path": f.path,
                        "filename": f.name,
                        "size": os.path.getsize(f.path) if os.path.exists(f.path) else 0,
                        "type": mimetypes.guess_type(f.path)[0] or "application/octet-stream",
                        "thumbnail": thumbnail_path,
                        "uploaded_id": None,
                    }
                    
                    self.attached_files.append(file_info)
                    self._debug_log(f"Added file to attachments: {file_info}")
                    
                    # Update UI to show attachment thumbnails in WhatsApp-like style
                    self._update_attachment_display()
                    
        except Exception as e:
            self._debug_log(f"_on_file_picker_result() error: {e}")

    def _update_attachment_display(self):
        """Update the UI to show thumbnails of attached files in a WhatsApp-like style."""
        try:
            # Build thumbnail row
            thumb_controls = []
            for idx, att in enumerate(self.attached_files):
                # Create remove button
                remove_btn = ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_size=14,
                    on_click=lambda e, i=idx: self._remove_attachment(i)
                )

                # Thumbnail or icon
                if att.get("thumbnail") and os.path.exists(att.get("thumbnail")):
                    left = ft.Image(src=att["thumbnail"], width=56, height=56, fit=ft.ImageFit.COVER)
                elif att.get("type", "").startswith('image') and os.path.exists(att.get('path')):
                    left = ft.Image(src=att["path"], width=56, height=56, fit=ft.ImageFit.COVER)
                else:
                    left = ft.Icon(ft.Icons.ATTACH_FILE, size=36)

                # Middle column: filename and size
                fname = att.get('filename') or 'file'
                fsize = att.get('size', 0)
                human_size = f"{round(fsize/1024,1)} KB" if fsize else ''

                mid_col = ft.Column(
                    controls=[
                        ft.Text(fname, size=12, weight=ft.FontWeight.BOLD, color=self.palette['on_surface']),
                        ft.Text(human_size, size=11, color=self.palette['secondary']),
                    ],
                    spacing=2
                )

                item = ft.Container(
                    content=ft.Row(controls=[left, ft.Container(width=8), mid_col, ft.Row(controls=[remove_btn])], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=8,
                    margin=ft.margin.only(right=8),
                    bgcolor=ft.colors.with_opacity(0.06, self.palette['surface']),
                    border_radius=10,
                    height=72,
                )

                thumb_controls.append(item)

            # If we have an attachment row reference, update it
            if getattr(self, 'attachment_row_ref', None):
                ar = self.attachment_row_ref
                ar.controls.clear()
                ar.controls.extend(thumb_controls)
                ar.height = 90 if thumb_controls else 0
                ar.visible = bool(thumb_controls)

            self._debug_log(f"Updated attachment display: {len(thumb_controls)} thumbnails")
            if getattr(self, 'page', None):
                self.page.update()

        except Exception as e:
            self._debug_log(f"_update_attachment_display() error: {e}")
    
    def _remove_attachment(self, index: int):
        """Remove an attachment from the list."""
        try:
            if 0 <= index < len(self.attached_files):
                removed = self.attached_files.pop(index)
                self._debug_log(f"Removed attachment: {removed['filename']}")
                self._update_attachment_display()
                if self.page:
                    self.page.update()
        except Exception as e:
            self._debug_log(f"_remove_attachment() error: {e}")
    
    def build(self):
        """Build the admin chat screen with a clean, responsive layout."""
        # Responsive layout: pinned section (if any), scrollable chat area, input at bottom
        pinned_section = None
        if self.pinned_chats:
            pinned_msgs = [self._build_message_card(msg) for msg in self.pinned_chats]
            pinned_section = ft.Container(
                content=ft.Column(
                    controls=[ft.Text("Pinned Messages", weight=ft.FontWeight.BOLD, size=14, color=self.palette["primary"])] + pinned_msgs,
                    spacing=6,
                ),
                bgcolor=ft.colors.with_opacity(0.08, self.palette["primary"]),
                padding=10,
                border_radius=10,
                margin=ft.margin.only(bottom=8),
            )

        chat_area = self._build_messages_area()
        input_area = self._build_message_input()

        # Compose the main chat screen layout (build controls list explicitly to avoid None)
        controls = []
        if pinned_section:
            controls.append(pinned_section)

        controls.append(ft.Container(
            content=chat_area,
            expand=True,
            bgcolor=self.palette["surface"],
            border_radius=10,
            padding=0,
            width=None,
        ))

        controls.append(ft.Container(
            content=input_area,
            expand=False,
            bgcolor=ft.colors.with_opacity(0.08, self.palette["primary"]),
            border_radius=10,
            padding=0,
            width=None,
        ))

        main_column = ft.Column(
            controls=controls,
            expand=True,
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # Ensure file picker is available on the page overlay
        try:
            if self.file_picker not in self.page.overlay:
                self.page.overlay.append(self.file_picker)
        except Exception:
            pass

        # Ensure keyboard events go to the chat handler while on this screen
        try:
            self.page.on_keyboard_event = self._chat_keyboard_event
        except Exception:
            pass

        # Start polling for messages if not running
        if not self._poller_task:
            try:
                self._poller_task = asyncio.create_task(self._poll_messages())
            except Exception:
                pass

        # Return the clean, modern chat screen container
        return self.build_screen_container(main_column, "Admin Chat")
    
    def _build_chats_list(self):
        """Build the list of active chats with filtering options."""
        # Filter chats based on current view
        display_chats = self.pinned_chats if self.show_pinned_only else self.active_chats
        
        chat_tiles = []
        for chat in display_chats:
            chat_tiles.append(self._build_chat_tile(chat))
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    # Header with filter options
                    ft.Row([
                        ft.Text("Active Chats", weight=ft.FontWeight.BOLD, size=16, 
                               color=self.palette["on_surface"], expand=True),
                        ft.PopupMenuButton(
                            items=[
                                ft.PopupMenuItem(
                                    text="All Chats",
                                    on_click=lambda _: self._toggle_pinned_view(False)
                                ),
                                ft.PopupMenuItem(
                                    text="Pinned Only",
                                    on_click=lambda _: self._toggle_pinned_view(True)
                                ),
                            ]
                        )
                    ]),
                    
                    # Quick actions row
                    ft.Row([
                        ft.IconButton(
                            icon=ft.Icons.PUSH_PIN,
                            tooltip="Show pinned chats",
                            on_click=lambda _: self._toggle_pinned_view(not self.show_pinned_only),
                            icon_color=self.palette["primary"] if self.show_pinned_only else None,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.GROUP,
                            tooltip="Select multiple recipients",
                            on_click=self._enable_multi_select,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            tooltip="Delete selected chats",
                            on_click=self._delete_selected_chats,
                        ),
                    ]),
                    
                    # Chats list
                    ft.ListView(
                        controls=chat_tiles,
                        expand=True,
                    ),
                ],
                spacing=10,
            ),
            width=300,
            bgcolor=ft.colors.with_opacity(0.8, self.palette["surface"]),
            padding=15,
            border_radius=10,
        )
    
    def _build_chat_tile(self, chat):
        """Build individual chat list tile with pin and delete options."""
        # Pin indicator
        pin_indicator = ft.Icon(
            ft.Icons.PUSH_PIN,
            color=self.palette["warning"],
            size=16,
        ) if chat.get("pinned") else ft.Container(width=16, height=16)
        
        # Selection indicator
        is_selected = chat["id"] in self.selected_recipients
        selection_indicator = ft.Icon(
            ft.Icons.CHECK_CIRCLE,
            color=self.palette["primary"],
            size=16,
        ) if is_selected else ft.Icon(
            ft.Icons.RADIO_BUTTON_UNCHECKED,
            color=self.palette["secondary"],
            size=16,
        )
        
        # Online status indicator
        status_indicator = ft.Container(
            width=8,
            height=8,
            border_radius=4,
            bgcolor=self.palette["success"] if chat.get("is_online") else self.palette["secondary"],
        )
        
        # Unread badge
        unread_badge = ft.Container(
            content=ft.Text(str(chat["unread"]), color=ft.colors.WHITE, size=12),
            bgcolor=self.palette["danger"],
            border_radius=10,
            padding=5,
        ) if chat["unread"] > 0 else None
        
        # Time and indicators column
        trailing_controls = [
            ft.Column([
                ft.Text(chat["time"], size=10, color=self.palette["on_surface"]),
                ft.Row([pin_indicator, selection_indicator], spacing=2),
            ], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END)
        ]
        
        # Action buttons (visible on hover or long-press)
        action_buttons = ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.PUSH_PIN,
                    icon_size=16,
                    icon_color=self.palette["warning"] if chat.get("pinned") else self.palette["secondary"],
                    tooltip="Pin chat" if not chat.get("pinned") else "Unpin chat",
                    on_click=lambda e, c=chat: self._toggle_pin_chat(c),
                    data=chat,
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=16,
                    icon_color=self.palette["danger"],
                    tooltip="Delete chat",
                    on_click=lambda e, c=chat: self._show_delete_confirmation(c),
                    data=chat,
                ),
            ],
            spacing=0,
        )
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    # Main chat tile
                    ft.ListTile(
                        leading=ft.Stack(
                            controls=[
                                ft.CircleAvatar(
                                    content=ft.Text(chat["user"][0], color=ft.colors.WHITE),
                                    bgcolor=self.palette["primary"],
                                ),
                                ft.Container(
                                    content=status_indicator,
                                    width=12,
                                    height=12,
                                    right=0,
                                    bottom=0,
                                    alignment=ft.alignment.bottom_right,
                                )
                            ]
                        ),
                        title=ft.Text(chat["user"], color=self.palette["on_surface"]),
                        subtitle=ft.Text(
                            chat["last_message"][:50] + "..." if len(chat["last_message"]) > 50 else chat["last_message"],
                            size=12,
                            color=self.palette["on_surface"]
                        ),
                        trailing=ft.Column(
                            controls=trailing_controls,
                            spacing=2,
                            horizontal_alignment=ft.CrossAxisAlignment.END,
                        ),
                        on_click=lambda e, c=chat: self._select_chat(c),
                        on_long_press=lambda e, c=chat: self._show_chat_actions(c),
                    ),
                    # Action buttons (hidden by default, shown on long-press)
                    ft.Container(
                        content=action_buttons,
                        padding=ft.padding.only(left=60, right=10, bottom=5),
                        visible=False,
                    )
                ]
            ),
            bgcolor=ft.colors.with_opacity(
                0.3, self.palette["primary"]
            ) if self.selected_chat and self.selected_chat["id"] == chat["id"] else None,
            border_radius=5,
            padding=2,
            on_hover=self._on_chat_tile_hover,
        )
    
    def _build_chat_area(self):
        """Build the main chat area."""
        # Content area (either placeholder or selected chat interface)
        if not self.selected_chat and not self.selected_recipients:
            content_area = ft.Container(
                content=ft.Column(
                    controls=[
                        ft.Icon(ft.Icons.CHAT, size=50, color=self.palette["primary"]),
                        ft.Text("Select a chat to start conversation", color=self.palette["on_surface"]),
                        ft.Text("or long-press chats to select multiple recipients", 
                               size=12, color=self.palette["secondary"]),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                expand=True,
            )
        else:
            content_area = self._build_selected_chat_interface()

        # Always show the message input at the bottom so users see the composer
        input_bar = self._build_message_input()

        return ft.Container(
            content=ft.Column(
                controls=[
                    content_area,
                    ft.Container(height=1, bgcolor=ft.colors.TRANSPARENT),
                    input_bar,
                ],
                expand=True,
            ),
            expand=True,
        )
    
    def _build_selected_chat_interface(self):
        """Build the interface for the selected chat(s)."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    self._build_chat_header(),
                    self._build_messages_area(),
                    self._build_recipient_indicator(),
                    self._build_message_input(),
                ],
                expand=True,
            ),
            expand=True,
            padding=15,
        )
    
    def _build_chat_header(self):
        """Build chat header with pin and delete actions."""
        if self.selected_chat:
            # Single chat selected
            user = self.selected_chat["user"]
            is_online = self.selected_chat.get("is_online", False)
            is_pinned = self.selected_chat.get("pinned", False)
            
            return ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.CircleAvatar(
                                    content=ft.Text(user[0], color=ft.colors.WHITE),
                                    bgcolor=self.palette["primary"],
                                ),
                                ft.Column(
                                    controls=[
                                        # Build row controls explicitly to avoid inserting None
                                        ft.Row(
                                            controls=(lambda: (lambda _controls: (_controls.append(ft.Icon(
                                                ft.Icons.PUSH_PIN,
                                                size=16,
                                                color=self.palette["warning"],
                                            )) if is_pinned else None) or _controls)([ft.Text(user, weight=ft.FontWeight.BOLD)]))(),
                                            spacing=5
                                        ),
                                        ft.Text(
                                            "Online" if is_online else "Offline",
                                            size=12,
                                            color=self.palette["success"] if is_online else self.palette["secondary"],
                                        ),
                                    ],
                                    spacing=0,
                                ),
                            ]
                        ),
                        ft.Row(
                            controls=[
                                ft.IconButton(
                                    icon=ft.Icons.PUSH_PIN,
                                    icon_color=self.palette["warning"] if is_pinned else None,
                                    tooltip="Pin chat" if not is_pinned else "Unpin chat",
                                    on_click=lambda _: self._toggle_pin_chat(self.selected_chat),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    icon_color=self.palette["danger"],
                                    tooltip="Delete chat",
                                    on_click=lambda _: self._show_delete_confirmation(self.selected_chat),
                                ),
                                ft.IconButton(
                                    icon=ft.Icons.MORE_VERT,
                                    on_click=self._show_chat_options,
                                ),
                            ]
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=10,
                bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]),
                border_radius=10,
            )
        else:
            # Multiple recipients selected
            return ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Icon(ft.Icons.GROUP, color=self.palette["primary"]),
                                ft.Column(
                                    controls=[
                                        ft.Text(f"{len(self.selected_recipients)} recipients selected", 
                                               weight=ft.FontWeight.BOLD),
                                        ft.Text(
                                            "Group message",
                                            size=12,
                                            color=self.palette["secondary"],
                                        ),
                                    ],
                                    spacing=0,
                                ),
                            ]
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CLEAR,
                            on_click=self._clear_selection,
                            tooltip="Clear selection",
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=10,
                bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]),
                border_radius=10,
            )
    
    def _build_recipient_indicator(self):
        """Build the recipient indicator near the send button."""
        if self.is_broadcast_mode:
            recipient_text = "Sending to: All active users"
            icon = ft.Icons.BROADCAST_ON_HOME
        elif self.selected_recipients:
            if len(self.selected_recipients) == 1:
                recipient_text = f"Sending to: {self._get_user_name(list(self.selected_recipients)[0])}"
            else:
                recipient_text = f"Sending to: {len(self.selected_recipients)} recipients"
            icon = ft.Icons.GROUP
        elif self.selected_chat:
            recipient_text = f"Sending to: {self.selected_chat['user']}"
            icon = ft.Icons.PERSON
        else:
            recipient_text = "Select recipients"
            icon = ft.Icons.PERSON_OUTLINE
        
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(icon, size=16, color=self.palette["secondary"]),
                    ft.Text(recipient_text, size=12, color=self.palette["secondary"]),
                ],
                spacing=5,
            ),
            padding=ft.padding.only(left=15, bottom=5),
        )
    
    def _build_messages_area(self):
        """Build the scrollable messages area with message cards, sorted by date with persistent timestamps.

        If Appwrite is configured via environment variables the app will try
        to fetch recent messages from the configured collection. On any
        failure it falls back to an empty list so the UI remains functional.
        """
        # Initialize messages list if not exists
        if not hasattr(self, 'messages') or self.messages is None:
            self.messages = []
        
        # Try to fetch live messages from Appwrite if available
        try:
            if appwrite_client.is_configured():
                db_id = os.getenv("APPWRITE_DATABASE_ID") or APPWRITE_DATABASE_ID_DEFAULT
                col_id = os.getenv("APPWRITE_COLLECTION_ID") or APPWRITE_COLLECTION_ID_DEFAULT
                docs = appwrite_client.get_messages(database_id=db_id, collection_id=col_id, limit=200)
                
                # Only fetch from backend if we haven't added local messages yet
                if not self.messages:
                    self.messages = []
                    # map Appwrite docs to the message shape used by the UI with persistent timestamps
                    current_user = None
                    if hasattr(self, 'app') and getattr(self.app, 'current_user', None):
                        cu = getattr(self.app, 'current_user')
                        current_user = cu.get('id') if isinstance(cu, dict) else None

                    for d in docs:
                        # Parse timestamp with both raw and formatted versions
                        raw_ts = d.get('timestamp') or d.get('createdAt') or d.get('time')
                        ts_numeric = 0
                        try:
                            if isinstance(raw_ts, (int, float)):
                                ts_numeric = int(raw_ts)
                            elif isinstance(raw_ts, str) and raw_ts.isdigit():
                                ts_numeric = int(raw_ts)
                            else:
                                try:
                                    parsed_dt = datetime.datetime.fromisoformat(str(raw_ts))
                                    ts_numeric = int(parsed_dt.timestamp())
                                except Exception:
                                    ts_numeric = 0
                        except Exception:
                            ts_numeric = 0
                        
                        self.messages.append({
                            'id': d.get('id') or d.get('$id'),
                            'sender': d.get('sender_name') or d.get('sender') or 'Unknown',
                            'text': d.get('content') or d.get('text') or d.get('message') or '',
                            'timestamp': d.get('timestamp') or d.get('createdAt') or '',
                            'timestamp_raw': ts_numeric,
                            'pinned': d.get('pinned', False),
                            'is_own': bool(current_user and str(d.get('sender_id') or d.get('sender') or '') == str(current_user)),
                            'status': d.get('status', ''),
                        })
                    
                    # Sort by timestamp (oldest first for chronological order)
                    try:
                        self.messages.sort(key=lambda x: x.get('timestamp_raw', 0))
                        self._debug_log(f"Fetched and sorted {len(self.messages)} messages by date")
                    except Exception as sort_ex:
                        self._debug_log(f"Sort error: {sort_ex}")
            else:
                raise RuntimeError('Appwrite not configured')
        except Exception as e:
            self._debug_log(f"Failed to fetch messages from Appwrite: {e}")
            # Initialize with empty list - don't show mock messages
            if not self.messages:
                self.messages = []

        # Build message cards from current messages list (already sorted)
        message_cards = [self._build_message_card(msg) for msg in self.messages]

        # Create ListView with reference for dynamic updates and auto-scroll
        lv = ft.ListView(
            controls=message_cards,
            auto_scroll=True,
            expand=True,
            spacing=8,
        )
        
        container = ft.Container(
            content=lv,
            expand=True,
            padding=10,
        )
        
        # Store reference for dynamic updates and auto-scroll
        self.messages_container_ref = lv
        self.messages_lv = lv
        return container
    
    def _build_message_card(self, message):
        """Build a modern message card with tap-to-menu functionality."""
        # Pin indicator
        pin_indicator = ft.Container(
            content=ft.Icon(ft.Icons.PUSH_PIN, size=12, color=self.palette["warning"]),
            padding=2,
        ) if message.get("pinned") else ft.Container()
        
        # Message bubble with styling based on sender
        is_own = message.get("is_own", False)
        bubble_color = ft.colors.with_opacity(0.15, self.palette["primary"]) if is_own else ft.colors.with_opacity(0.1, self.palette["secondary"])
        alignment = ft.alignment.center_right if is_own else ft.alignment.center_left
        
        # Helper: format timestamp for display (Today hh:mm AM/PM or Weekday hh:mm)
        def _format_ts(ts_val):
            try:
                if isinstance(ts_val, int) or (isinstance(ts_val, str) and ts_val.isdigit()):
                    t = datetime.datetime.fromtimestamp(int(ts_val))
                    now = datetime.datetime.now()
                    if t.date() == now.date():
                        return f"Today {t.strftime('%-I:%M %p')}" if hasattr(t, 'strftime') else t.strftime('%I:%M %p')
                    else:
                        return f"{t.strftime('%A')} {t.strftime('%-I:%M %p')}"
                # If it's already a human string, just return it
                return str(ts_val or '')
            except Exception:
                try:
                    return str(ts_val or '')
                except Exception:
                    return ''

        # Use timestamp_raw for formatting (numeric timestamp), fallback to timestamp string
        display_ts = _format_ts(message.get('timestamp_raw') or message.get('timestamp'))

        # Status ticks: one grey (sent), two grey (delivered), two blue (read)
        status = (message.get('status') or message.get('delivery_status') or '').lower()
        grey_col = '#9CA3AF'
        read_col = self.palette.get('primary', '#6366F1')
        status_icon = None
        try:
            if status in ('read', 'seen'):
                status_icon = ft.Icon(ft.Icons.DONE_ALL, size=14, color=read_col)
            elif status in ('delivered', 'deliv'):
                status_icon = ft.Icon(ft.Icons.DONE_ALL, size=14, color=grey_col)
            else:
                # default to single-tick sent
                status_icon = ft.Icon(ft.Icons.DONE, size=14, color=grey_col)
        except Exception:
            status_icon = ft.Icon(ft.Icons.DONE, size=14, color=grey_col)

        # Build message bubble with timestamp + status aligned to the end
        message_bubble = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text(message.get('sender') or '', size=12, weight=ft.FontWeight.BOLD, color=self.palette['secondary']),
                            pin_indicator,
                        ],
                        spacing=5,
                    ),
                    ft.Text(
                        message.get('text') or '',
                        size=14,
                        color=self.palette['on_surface'],
                        selectable=True,
                    ),
                    ft.Row(
                        controls=[
                            ft.Text(display_ts, size=10, color=self.palette['secondary']),
                            status_icon,
                        ],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=6,
                    ),
                ],
                spacing=6,
            ),
            bgcolor=bubble_color,
            padding=12,
            border_radius=12,
            width=None,
        )
        
        # Clickable container shows a simple dialog with actions
        def show_message_menu(e):
            """Show dialog for message actions (pin/delete)."""
            def pin_message(ev):
                self._show_snackbar(f"Message {'pinned' if not message.get('pinned') else 'unpinned'}")
                message["pinned"] = not message.get("pinned")
                # close dialog
                if hasattr(self.page, 'dialog') and self.page.dialog:
                    self.page.dialog.open = False
                self.page.update()

            def delete_message(ev):
                self._show_snackbar(f"Message deleted")
                if message in self.messages:
                    self.messages.remove(message)
                # close dialog
                if hasattr(self.page, 'dialog') and self.page.dialog:
                    self.page.dialog.open = False
                self.page.update()

            def cancel(ev):
                if hasattr(self.page, 'dialog') and self.page.dialog:
                    self.page.dialog.open = False
                self.page.update()

            dialog = ft.AlertDialog(
                title=ft.Text("Message actions"),
                content=ft.Column([
                    ft.Text(message.get("text", "")),
                ], tight=True),
                actions=[
                    ft.TextButton("Pin message", on_click=pin_message),
                    ft.TextButton("Delete", on_click=delete_message),
                    ft.TextButton("Cancel", on_click=cancel),
                ],
            )
            self.page.dialog = dialog
            dialog.open = True
            self.page.update()
        
        return ft.Container(
            content=message_bubble,
            alignment=alignment,
            padding=ft.padding.symmetric(horizontal=10, vertical=5),
            on_click=show_message_menu,
        )
    
    def _build_message_input(self):
        """Build the message input area with enhanced send button."""
        self.message_input = ft.TextField(
            hint_text="Type a message...",
            expand=True,
            multiline=True,
            max_lines=3,
            on_submit=lambda e: self._send_message(),
        )
        # Only allow sending when user is admin
        is_admin = getattr(self.app, 'is_admin', False)

        emoji_button = ft.TextButton(text="😀", on_click=lambda e: self._open_emoji_picker(), tooltip="Emoji")

        send_button = ft.IconButton(
            icon=ft.Icons.SEND,
            on_click=lambda e: self._send_message(),
            on_long_press=self._show_recipient_selection,
            tooltip="Tap to send, long-press to select recipients",
            disabled=not is_admin,
        )

        if not is_admin:
            # Show a subtle hint for non-admins
            send_button.tooltip = "Admins only"

        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.IconButton(
                                icon=ft.Icons.ATTACH_FILE,
                                on_click=self._attach_file,
                                tooltip="Attach file",
                                disabled=not is_admin,
                            ),
                            emoji_button,
                            self.message_input,
                            ft.Container(
                                content=send_button,
                                margin=ft.margin.only(right=5),
                            ),
                        ]
                    ),
                    # Attachment preview area
                    self._build_attachment_preview(),
                ],
                spacing=6,
            ),
            bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]),
            border_radius=20,
            padding=10,
        )

    # Pinning functionality
    async def _toggle_pin_chat(self, chat):
        """Toggle pin status of a chat."""
        try:
            chat_id = chat["id"]
            new_pinned_status = not chat.get("pinned", False)
            
            # Update local state
            chat["pinned"] = new_pinned_status
            
            # Update in Appwrite
            if hasattr(self, 'database'):
                await self.database.update_document(
                    self.chats_collection,
                    chat_id,
                    data={"pinned": new_pinned_status}
                )
            
            # Refresh chat lists
            await self._refresh_chat_lists()
            
            # Show confirmation
            action = "pinned" if new_pinned_status else "unpinned"
            self._show_snackbar(f"Chat {action} successfully")
            
        except Exception as error:
            print(f"Error toggling pin: {error}")
            self._show_snackbar("Error updating pin status")
    
    async def _refresh_chat_lists(self):
        """Refresh pinned and active chat lists."""
        self.pinned_chats = [chat for chat in self.active_chats if chat.get("pinned")]
        
        # Keep active chats sorted (pinned first, then by time)
        self.active_chats.sort(key=lambda x: (not x.get("pinned", False), x.get("last_message_at", "")), reverse=True)
        
        if self.page:
            self.page.update()
    
    def _toggle_pinned_view(self, show_pinned):
        """Toggle between showing all chats and pinned only."""
        self.show_pinned_only = show_pinned
        self.page.update()
    
    # Delete functionality
    def _show_delete_confirmation(self, chat=None):
        """Show confirmation dialog for deleting chat(s)."""
        if chat:
            # Single chat deletion
            title = "Delete Chat"
            content = f"Are you sure you want to delete the chat with {chat['user']}? This action cannot be undone."
            chats_to_delete = [chat]
        elif self.selected_recipients:
            # Multiple chat deletion
            title = "Delete Multiple Chats"
            content = f"Are you sure you want to delete {len(self.selected_recipients)} selected chats? This action cannot be undone."
            chats_to_delete = [chat for chat in self.active_chats if chat["id"] in self.selected_recipients]
        else:
            self._show_snackbar("Please select chats to delete")
            return
        
        def confirm_delete(e):
            asyncio.create_task(self._delete_chats(chats_to_delete))
            dialog.open = False
            self.page.update()
        
        def cancel_delete(e):
            dialog.open = False
            self.page.update()
        
        dialog = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Text(content),
            actions=[
                ft.TextButton("Cancel", on_click=cancel_delete),
                ft.TextButton("Delete", on_click=confirm_delete, style=ft.ButtonStyle(color=self.palette["danger"])),
            ],
        )
        
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
    
    async def _delete_chats(self, chats_to_delete):
        """Delete the specified chats."""
        try:
            for chat in chats_to_delete:
                chat_id = chat["id"]
                
                # Soft delete in Appwrite (set deleted flag)
                if hasattr(self, 'database'):
                    await self.database.update_document(
                        self.chats_collection,
                        chat_id,
                        data={"deleted": True, "deleted_at": datetime.utcnow().isoformat()}
                    )
                
                # Remove from local state
                self.active_chats = [c for c in self.active_chats if c["id"] != chat_id]
                self.pinned_chats = [c for c in self.pinned_chats if c["id"] != chat_id]
                
                # Remove from selections
                if chat_id in self.selected_recipients:
                    self.selected_recipients.remove(chat_id)
                
                # Clear selected chat if it's being deleted
                if self.selected_chat and self.selected_chat["id"] == chat_id:
                    self.selected_chat = None
            
            await self._refresh_chat_lists()
            self._show_snackbar(f"{len(chats_to_delete)} chat(s) deleted successfully")
            
        except Exception as error:
            print(f"Error deleting chats: {error}")
            self._show_snackbar("Error deleting chats")
    
    def _delete_selected_chats(self, e=None):
        """Delete all selected chats."""
        if not self.selected_recipients:
            self._show_snackbar("Please select chats to delete (long-press to select)")
            return
        self._show_delete_confirmation()
    
    # Enhanced UI interactions
    def _show_chat_actions(self, chat):
        """Show action buttons for a chat on long-press."""
        # Find the chat tile and show its action buttons
        for control in self._find_chat_tile_controls(chat["id"]):
            if hasattr(control, 'content') and len(control.content.controls) > 1:
                action_buttons = control.content.controls[1]
                action_buttons.visible = True
        self.page.update()
    
    def _on_chat_tile_hover(self, e):
        """Show/hide action buttons on hover."""
        if e.data == "true":  # Mouse enter
            # Find and show action buttons for this tile
            for control in self._find_chat_tile_controls(e.control.data["id"] if hasattr(e.control, 'data') else None):
                if hasattr(control, 'content') and len(control.content.controls) > 1:
                    action_buttons = control.content.controls[1]
                    action_buttons.visible = True
        else:  # Mouse leave
            # Hide all action buttons
            for control in self._find_all_chat_tiles():
                if hasattr(control, 'content') and len(control.content.controls) > 1:
                    action_buttons = control.content.controls[1]
                    action_buttons.visible = False
        self.page.update()
    
    def _find_chat_tile_controls(self, chat_id):
        """Find chat tile controls by chat ID."""
        # Implementation depends on your specific UI structure
        # This is a simplified version
        tiles = []
        for chat in self.active_chats + self.pinned_chats:
            if chat["id"] == chat_id:
                # You'd need to return the actual control references
                pass
        return tiles
    
    def _find_all_chat_tiles(self):
        """Find all chat tile controls."""
        # Implementation depends on your specific UI structure
        return []
    
    # Existing methods (from previous implementation)
    def _select_chat(self, chat):
        """Select a single chat for conversation."""
        self.selected_chat = chat
        self.selected_recipients = set()
        self.is_broadcast_mode = False
        
        # Hide any visible action buttons
        for control in self._find_all_chat_tiles():
            if hasattr(control, 'content') and len(control.content.controls) > 1:
                action_buttons = control.content.controls[1]
                action_buttons.visible = False
        
        if hasattr(self, '_load_chat_messages'):
            asyncio.create_task(self._load_chat_messages(chat['id']))
        
        self.page.update()
    
    def _toggle_recipient_selection(self, chat):
        """Toggle recipient selection for multi-send."""
        chat_id = chat["id"]
        
        if chat_id in self.selected_recipients:
            self.selected_recipients.remove(chat_id)
        else:
            self.selected_recipients.add(chat_id)
        
        if self.selected_recipients:
            self.selected_chat = None
            self.is_broadcast_mode = False
        
        self.page.update()
    
    async def _load_all_chats(self):
        """Load all chats including pinned status."""
        try:
            # Sample implementation - replace with actual Appwrite calls
            sample_chats = [
                {"id": 1, "user": "John Doe", "last_message": "Hello, I have a question...", 
                 "unread": 2, "time": "10:15 AM", "is_online": True, "pinned": True},
                {"id": 2, "user": "Jane Smith", "last_message": "About the retreat...", 
                 "unread": 0, "time": "09:30 AM", "is_online": False, "pinned": False},
                {"id": 3, "user": "Mike Johnson", "last_message": "Thank you for your help!", 
                 "unread": 0, "time": "Yesterday", "is_online": True, "pinned": True},
                {"id": 4, "user": "Sarah Wilson", "last_message": "Can you help me with...", 
                 "unread": 1, "time": "11:45 AM", "is_online": True, "pinned": False},
            ]
            
            self.active_chats = sample_chats
            await self._refresh_chat_lists()
                
        except Exception as e:
            print(f"Error loading chats: {e}")
    
    def _show_snackbar(self, message):
        """Show a snackbar message."""
        snackbar = ft.SnackBar(content=ft.Text(message))
        self.page.snack_bar = snackbar
        snackbar.open = True
        self.page.update()
    
    # Other existing methods...
    def _enable_multi_select(self, e=None):
        """Enable multi-select mode."""
        self.selected_chat = None
        self.is_broadcast_mode = False
        self.page.update()
    
    def _show_recipient_selection(self, e):
        """Show recipient selection dialog."""
        # Build a dialog allowing selection of multiple recipients
        checkboxes = []

        def make_checkbox(chat):
            cid = str(chat['id'])
            cb = ft.Checkbox(label=chat.get('user', cid), value=(cid in self.selected_recipients))
            def on_toggle(e, cid=cid):
                if e.control.value:
                    self.selected_recipients.add(cid)
                else:
                    self.selected_recipients.discard(cid)
                self.page.update()
            cb.on_change = on_toggle
            return cb

        for chat in self.active_chats:
            checkboxes.append(make_checkbox(chat))

        broadcast_cb = ft.Checkbox(label='Broadcast to all', value=self.is_broadcast_mode)
        def on_broadcast_change(e):
            self.is_broadcast_mode = e.control.value
            if self.is_broadcast_mode:
                self.selected_recipients.clear()
            self.page.update()
        broadcast_cb.on_change = on_broadcast_change

        def confirm(e):
            dialog.open = False
            self.page.update()

        def cancel(e):
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text('Select Recipients'),
            content=ft.Column(controls=[broadcast_cb] + checkboxes, spacing=6),
            actions=[ft.TextButton('Cancel', on_click=cancel), ft.TextButton('OK', on_click=confirm)],
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()
    
    def _send_message(self):
        """Send message to selected recipients - reads from UI input."""
        try:
            # Only admins can send messages
            if not getattr(self.app, 'is_admin', False):
                self._show_snackbar("Only admins can send messages")
                return

            # Read text from input
            text = ''
            if getattr(self, 'message_input', None):
                text = (self.message_input.value or '').strip()

            if not text and not self.attached_files:
                self._show_snackbar("Cannot send empty message")
                return

            self._debug_log(f"_send_message() called: text_len={len(text)}, attachments={len(self.attached_files)}")

            # Build payload to match Appwrite collection schema
            current_user = getattr(self.app, 'current_user', {}) or {}
            sender_id = current_user.get('id') or current_user.get('email') or 'admin'
            sender_name = current_user.get('name') or current_user.get('email') or 'Admin'
            
            # Determine recipients (store as comma-separated string)
            if self.is_broadcast_mode:
                target_groups = 'all'
            elif self.selected_recipients:
                # join selected recipient ids into a string
                target_groups = ",".join(map(str, self.selected_recipients))
            else:
                target_groups = 'all'
            
            ts_int = int(time.time())
            human_ts = datetime.datetime.fromtimestamp(ts_int).strftime('%I:%M %p')

            # Prepare attachments info (for reference, not stored in message)
            attachment_data = []
            for att in self.attached_files:
                attachment_data.append({
                    "filename": att.get("filename", "file"),
                    "size": att.get("size", 0),
                    "type": att.get("type", "application/octet-stream"),
                })
            
            if attachment_data:
                self._debug_log(f"Prepared {len(attachment_data)} attachments")

            # Determine media type based on attachments
            media_type = 'none'
            if attachment_data:
                media_type = attachment_data[0].get('type', 'file').split('/')[0]  # 'image', 'video', 'file', etc.
                if media_type not in ['image', 'video', 'audio', 'file']:
                    media_type = 'file'
            
            # Build payload with only schema-compatible fields
            payload = {
                'sender_name': sender_name,
                'sender_id': sender_id,
                'content': text,
                'timestamp': ts_int,
                'message_type': 'text',
                'media_type': media_type,
                # include target_groups as string
                'target_groups': target_groups,
                'status': 'sent',
            }

            # Send to Appwrite
            created_id = None
            if appwrite_client and appwrite_client.is_configured():
                try:
                    # Debug: log exact payload sent to Appwrite
                    try:
                        self._debug_log(f"Sending payload to Appwrite: {json.dumps(payload, default=str)}")
                    except Exception:
                        self._debug_log(f"Sending payload to Appwrite (repr): {repr(payload)}")

                    res = appwrite_client.create_message(
                        payload=payload,
                        database_id=APPWRITE_DATABASE_ID_DEFAULT,
                        collection_id=APPWRITE_COLLECTION_ID_DEFAULT
                    )
                    
                    # Extract id from response
                    if isinstance(res, dict):
                        created_id = res.get('$id') or res.get('id')
                    else:
                        created_id = getattr(res, '$id', None) or getattr(res, 'id', None)
                    
                    self._debug_log(f"Message sent to Appwrite: {created_id}")
                    
                except Exception as e:
                    self._debug_log(f"Appwrite send failed: {e}")
                    self._show_snackbar(f'Failed to send: {e}')
                    return
            else:
                self._debug_log("Appwrite not configured")
                self._show_snackbar("Backend not configured")
                return

            # Add to local messages for immediate UI feedback
            new_msg = {
                'id': created_id or f'local-{ts_int}',
                'sender': sender_name,
                'text': text,
                'timestamp': human_ts,
                'pinned': False,
                'is_own': True,
            }
            
            if not hasattr(self, 'messages'):
                self.messages = []
            self.messages.append(new_msg)
            self._debug_log(f"Added message to UI: {new_msg['id']}")

            # If we have a reference to the messages ListView, append the new card
            try:
                if getattr(self, 'messages_container_ref', None) and getattr(self.messages_container_ref, 'content', None):
                    lv = self.messages_container_ref.content
                    # Append a single message card control so we don't rebuild the whole list
                    try:
                        lv.controls.append(self._build_message_card(new_msg))
                        # If ListView has an update method, call it; otherwise update page
                        try:
                            lv.update()
                        except Exception:
                            pass
                    except Exception as e:
                        self._debug_log(f"Failed to append message card to ListView: {e}")
            except Exception:
                pass

            # Clear input
            if getattr(self, 'message_input', None):
                self.message_input.value = ''
            
            # Clear attachments
            self.attached_files.clear()
            self.attached_file = None
            
            self._show_snackbar('Message sent ✓')
            
            # Rebuild the messages area to display new message
            try:
                # Find the ListView in the chat area and update it
                if hasattr(self, 'page') and self.page:
                    self.page.update()
                    self._debug_log("Page updated - messages should display")
            except Exception as e:
                self._debug_log(f"Error updating page: {e}")
                
        except Exception as e:
            self._debug_log(f"_send_message() error: {e}")
            traceback.print_exc()
            self._show_snackbar(f'Error: {e}')
            return
    
    def _clear_selection(self, e=None):
        """Clear current selection."""
        self.selected_recipients.clear()
        self.selected_chat = None
        self.is_broadcast_mode = False
        self.page.update()
# ============ Admin Settings Screen ============
class AdminSettingsScreen(BaseScreen):
    """Admin settings and configuration screen."""
    
    def build(self):
        """Build the admin settings screen."""
        content = ft.Column(
            controls=[
                self._build_section_header("System Settings"),
                self._build_system_settings(),
                self._build_section_header("User Management"),
                self._build_user_management(),
                self._build_section_header("Community Settings"),
                self._build_community_settings(),
            ],
            scroll=ft.ScrollMode.ADAPTIVE,
            spacing=20,
        )
        
        return self.build_screen_container(content, "Admin Settings")
    
    def _build_section_header(self, title):
        """Build section header."""
        return ft.Container(
            content=ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
            padding=ft.padding.only(left=10),
        )
    
    def _build_system_settings(self):
        """Build system settings section."""
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.ListTile(
                            title=ft.Text("Email Notifications", color=self.palette["on_surface"]),
                            subtitle=ft.Text("Receive email alerts for new messages", color=self.palette["on_surface"]),
                            trailing=ft.Switch(value=True, active_color=self.palette["secondary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Maintenance Mode", color=self.palette["on_surface"]),
                            subtitle=ft.Text("Put the system under maintenance", color=self.palette["on_surface"]),
                            trailing=ft.Switch(value=False, active_color=self.palette["secondary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Clear Cache", color=self.palette["on_surface"]),
                            trailing=ft.ElevatedButton(
                                "Clear",
                                bgcolor=self.palette["accent"],
                                color=ft.colors.WHITE,
                            ),
                        ),
                    ],
                    spacing=0,
                ),
                padding=15,
            ),
        )
    
    def _build_user_management(self):
        """Build user management section with list of all users and admin toggle buttons."""
        try:
            # Fetch all users from Firebase
            users = fetch_all_users() or {}
            
            if not users:
                return ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("No users found", color=self.palette["on_surface"]),
                            ],
                            spacing=12,
                        ),
                        padding=15,
                    ),
                )
            
            # Build user rows
            user_rows = []
            user_rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text("Email", weight=ft.FontWeight.BOLD, size=12, color=self.palette["on_surface"], expand=2),
                            ft.Text("Name", weight=ft.FontWeight.BOLD, size=12, color=self.palette["on_surface"], expand=2),
                            ft.Text("Status", weight=ft.FontWeight.BOLD, size=12, color=self.palette["on_surface"], expand=1),
                            ft.Text("Action", weight=ft.FontWeight.BOLD, size=12, color=self.palette["on_surface"], expand=1),
                        ],
                        spacing=8,
                    ),
                    padding=ft.padding.symmetric(vertical=8, horizontal=10),
                    bgcolor=self.palette.get("surface_dim", self.palette.get("surface")),
                )
            )
            
            # Add rows for each user
            for user_id, user_data in users.items():
                if not isinstance(user_data, dict):
                    continue
                
                email = user_data.get("email", "N/A")
                name = user_data.get("name", "Unknown")
                is_admin = user_data.get("is_admin", False)
                
                status_text = "Admin" if is_admin else "User"
                status_color = self.palette["primary"] if is_admin else self.palette["secondary"]
                
                # Create toggle button
                def make_toggle_handler(uid, user_email, current_admin):
                    def on_toggle(e):
                        self._toggle_user_admin_status(uid, user_email, current_admin)
                    return on_toggle
                
                btn_text = "Demote" if is_admin else "Make Admin"
                btn_color = ft.colors.ORANGE if is_admin else ft.colors.GREEN
                
                user_row = ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text(email, size=11, color=self.palette["on_surface"], expand=2, no_wrap=True),
                            ft.Text(name, size=11, color=self.palette["on_surface"], expand=2, no_wrap=True),
                            ft.Text(status_text, size=11, color=status_color, weight=ft.FontWeight.BOLD, expand=1),
                            ft.ElevatedButton(
                                btn_text,
                                icon=ft.Icons.ADMIN_PANEL_SETTINGS if is_admin else ft.Icons.PERSON,
                                icon_color=ft.colors.WHITE,
                                bgcolor=btn_color,
                                color=ft.colors.WHITE,
                                on_click=make_toggle_handler(user_id, email, is_admin),
                                width=90,
                                height=32,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(vertical=8, horizontal=10),
                    border=ft.border.only(bottom=ft.border.BorderSide(1, self.palette.get("outline", "#E0E0E0"))),
                )
                user_rows.append(user_row)
            
            # Create scrollable ListView
            users_list = ft.ListView(
                controls=user_rows,
                expand=True,
                spacing=0,
            )
            
            return ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text("User Management", weight=ft.FontWeight.BOLD, size=14, color=self.palette["primary"]),
                            ft.Container(
                                content=users_list,
                                height=400,
                                border=ft.border.all(1, self.palette.get("outline", "#E0E0E0")),
                            ),
                        ],
                        spacing=12,
                    ),
                    padding=15,
                ),
            )
        
        except Exception as e:
            print(f"_build_user_management() error: {e}")
            traceback.print_exc()
            return ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text("Error loading users", color=ft.colors.RED),
                            ft.Text(str(e), size=10, color=self.palette["on_surface"]),
                        ],
                        spacing=8,
                    ),
                    padding=15,
                ),
            )
    
    def _toggle_user_admin_status(self, user_id: str, user_email: str, current_is_admin: bool):
        """Toggle a user's admin status (promote/demote)."""
        try:
            new_admin_status = not current_is_admin
            
            # Update Firebase
            if FIREBASE_ADMIN_AVAILABLE:
                try:
                    from firebase_admin import db
                    ref = db.reference(f'/users/{user_id}')
                    ref.update({'is_admin': new_admin_status})
                    print(f"Updated user {user_email} (admin={new_admin_status}) via Firebase Admin")
                except Exception as e:
                    print(f"Firebase Admin update failed: {e}")
                    # Fall through to REST
                    raise
            else:
                # Use REST API
                if not FIREBASE_DB_URL:
                    raise RuntimeError("FIREBASE_DB_URL not configured")
                
                update_url = f"{FIREBASE_DB_URL.rstrip('/')}/users/{user_id}/is_admin.json"
                resp = requests.patch(update_url, json=new_admin_status, timeout=10)
                resp.raise_for_status()
                print(f"Updated user {user_email} (admin={new_admin_status}) via REST")
            
            # Show success snackbar and refresh the screen
            status_str = "Admin" if new_admin_status else "User"
            self._show_snackbar(f"User {user_email} is now a {status_str}")
            
            # Rebuild the screen to show updated status
            self.page.clean()
            self.page.add(self.build())
            
        except Exception as e:
            print(f"_toggle_user_admin_status() error: {e}")
            traceback.print_exc()
            self._show_snackbar(f"Error updating user: {str(e)}", color=ft.colors.RED)
    
    def _show_snackbar(self, msg: str, color=None):
        """Show a snackbar message."""
        try:
            snack = ft.SnackBar(
                ft.Text(msg, color=ft.colors.WHITE),
                bgcolor=color or self.palette.get("secondary", ft.colors.BLUE),
            )
            self.page.snack_bar = snack
            snack.open = True
            self.page.update()
        except Exception as e:
            print(f"_show_snackbar error: {e}")
    
    def _build_community_settings(self):
        """Build community settings section."""
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.TextField(
                            label="Community Name",
                            value="CLC Kenya",
                            border_color=self.palette["primary"],
                        ),
                        ft.TextField(
                            label="Welcome Message",
                            multiline=True,
                            border_color=self.palette["primary"],
                        ),
                        ft.ElevatedButton(
                            "Save Changes",
                            bgcolor=self.palette["secondary"],
                            color=ft.Colors.WHITE,
                            icon=ft.Icons.SAVE,
                        ),
                    ],
                    spacing=15,
                ),
                padding=15,
            ),
        )


# ============ User Settings Screen ============
class UserSettingsScreen(BaseScreen):
    """User settings and profile management screen."""
    
    def build(self):
        """Build the user settings screen."""
        content = ft.Column(
            controls=[
                self._build_profile_section(),
                self._build_preferences_section(),
                self._build_privacy_section(),
                self._build_actions_section(),
            ],
            scroll=ft.ScrollMode.ADAPTIVE,
            spacing=20,
        )
        
        return self.build_screen_container(content, "Settings")
    
    def _build_profile_section(self):
        """Build profile information section."""
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.ListTile(
                            title=ft.Text("Profile Information", weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
                        ),
                        ft.Container(
                            content=ft.Row(
                                controls=[
                                    ft.CircleAvatar(
                                        content=ft.Text("U", color=ft.colors.WHITE),
                                        bgcolor=self.palette["primary"],
                                        radius=30,
                                    ),
                                    ft.Column(
                                        controls=[
                                            ft.Text("User Name", weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                                            ft.Text("user@example.com", color=self.palette["on_surface"]),
                                        ],
                                        spacing=2,
                                    ),
                                    ft.Container(expand=True),
                                    ft.TextButton("Edit", style=ft.ButtonStyle(color=ft.colors.BLUE)),
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            padding=15,
                        ),
                    ],
                    spacing=0,
                ),
                padding=10,
            ),
        )
    
    def _build_preferences_section(self):
        """Build user preferences section."""
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.ListTile(
                            title=ft.Text("Preferences", weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Email Notifications", color=self.palette["on_surface"]),
                            subtitle=ft.Text("Receive email updates", color=self.palette["on_surface"]),
                            trailing=ft.Switch(value=True, active_color=self.palette["secondary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Push Notifications", color=self.palette["on_surface"]),
                            subtitle=ft.Text("Receive push notifications", color=self.palette["on_surface"]),
                            trailing=ft.Switch(value=True, active_color=self.palette["secondary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Language", color=self.palette["on_surface"]),
                            subtitle=ft.Text("English", color=self.palette["on_surface"]),
                            trailing=ft.Dropdown(
                                width=120,
                                options=[ft.dropdown.Option("English"), ft.dropdown.Option("Kiswahili")],
                                value="English",
                                border_color=self.palette["primary"],
                            ),
                        ),
                    ],
                    spacing=0,
                ),
                padding=10,
            ),
        )
    
    def _build_privacy_section(self):
        """Build privacy settings section."""
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.ListTile(
                            title=ft.Text("Privacy & Security", weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Change Password", color=self.palette["on_surface"]),
                                trailing=ft.IconButton(
                                icon=ft.Icons.ARROW_FORWARD_IOS,
                                icon_color=self.palette["primary"],
                            ),
                        ),
                        ft.ListTile(
                            title=ft.Text("Two-Factor Authentication", color=self.palette["on_surface"]),
                            subtitle=ft.Text("Add extra security to your account", color=self.palette["on_surface"]),
                            trailing=ft.Switch(value=False, active_color=self.palette["secondary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Privacy Policy", color=self.palette["on_surface"]),
                            trailing=ft.IconButton(
                                icon=ft.icons.ARROW_FORWARD_IOS,
                                icon_color=self.palette["primary"],
                            ),
                        ),
                    ],
                    spacing=0,
                ),
                padding=10,
            ),
        )
    
    def _build_actions_section(self):
        """Build actions section (logout, delete account)."""
        return ft.Card(
            content=ft.Container(
                content=ft.Column(
                    controls=[
                        ft.ListTile(
                            title=ft.Text("Actions", weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
                        ),
                        ft.ListTile(
                            title=ft.Text("Logout", color=self.palette["danger"]),
                            leading=ft.Icon(ft.Icons.LOGOUT, color=self.palette["danger"]),
                            on_click=lambda e: self.app.logout(),
                        ),
                        ft.ListTile(
                            title=ft.Text("Delete Account", color=self.palette["danger"]),
                            leading=ft.Icon(ft.Icons.DELETE, color=self.palette["danger"]),
                        ),
                    ],
                    spacing=0,
                ),
                padding=10,
            ),
        )


# ============ Admin Inbox Screen ============
class AdminInboxScreen(BaseScreen):
    """Admin inbox for managing messages and notifications."""
    
    def build(self):
        """Build the admin inbox screen."""
        content = ft.Column(
            controls=[
                self._build_inbox_header(),
                self._build_message_filters(),
                self._build_messages_list(),
            ],
            expand=True,
        )
        
        return self.build_screen_container(content, "Admin Inbox")
    
    def _build_inbox_header(self):
        """Build inbox header with stats."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text("Total Messages", size=14, color=self.palette["on_surface"]),
                            ft.Text("156", size=24, weight=ft.FontWeight.BOLD, color=self.palette["primary"]),
                        ],
                    ),
                    ft.VerticalDivider(),
                    ft.Column(
                        controls=[
                            ft.Text("Unread", size=14, color=self.palette["on_surface"]),
                            ft.Text("12", size=24, weight=ft.FontWeight.BOLD, color=self.palette["accent"]),
                        ],
                    ),
                    ft.VerticalDivider(),
                    ft.Column(
                        controls=[
                            ft.Text("This Week", size=14, color=self.palette["on_surface"]),
                            ft.Text("23", size=24, weight=ft.FontWeight.BOLD, color=self.palette["secondary"]),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_EVENLY,
            ),
            padding=20,
            bgcolor=ft.colors.with_opacity(0.8, self.palette["surface"]),
            border_radius=10,
        )
    
    def _build_message_filters(self):
        """Build message filter controls."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    # Simple filter buttons (FilterChip may be unavailable in some Flet versions)
                    ft.Container(
                        content=ft.Row(
                            controls=[
                                ft.TextButton("All", style=ft.ButtonStyle(color=self.palette["on_surface"])),
                                ft.TextButton("Unread", style=ft.ButtonStyle(color=self.palette["on_surface"])),
                                ft.TextButton("Important", style=ft.ButtonStyle(color=self.palette["on_surface"])),
                            ],
                            spacing=6,
                        ),
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.icons.REFRESH,
                        icon_color=self.palette["primary"],
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=15,
        )
    
    def _build_messages_list(self):
        """Build the messages list."""
        # Sample messages
        sample_messages = [
            {"id": 1, "sender": "John Doe", "subject": "Question about retreat", "preview": "Hello, I would like to know more about...", "time": "10:15 AM", "unread": True, "important": True},
            {"id": 2, "sender": "Jane Smith", "subject": "Registration confirmation", "preview": "Thank you for confirming my registration...", "time": "09:30 AM", "unread": True, "important": False},
            {"id": 3, "sender": "Community Group", "subject": "Weekly meeting update", "preview": "Here's the update from our last meeting...", "time": "Yesterday", "unread": False, "important": True},
        ]
        
        message_tiles = []
        for msg in sample_messages:
            message_tiles.append(self._build_message_tile(msg))
        
        return ft.Container(
            content=ft.ListView(
                controls=message_tiles,
                expand=True,
                spacing=5,
            ),
            expand=True,
        )
    
    def _build_message_tile(self, message):
        """Build individual message tile."""
        return ft.Container(
            content=ft.ListTile(
                leading=ft.CircleAvatar(
                    content=ft.Text(message["sender"][0], color=ft.Colors.WHITE),
                    bgcolor=self.palette["primary"],
                ),
                # Build title controls explicitly to avoid None in controls
                title=ft.Row(
                    controls=(lambda: (lambda _c: (_c.append(ft.Icon(ft.Icons.STAR, color=self.palette["accent"], size=16)) if message["important"] else None) or _c)([ft.Text(message["sender"], weight=ft.FontWeight.BOLD if message["unread"] else ft.FontWeight.NORMAL, color=self.palette["on_surface"])]) )(),
                ),
                subtitle=ft.Column(
                    controls=[
                        ft.Text(message["subject"], color=self.palette["on_surface"]),
                        ft.Text(message["preview"], size=12, color=self.palette["on_surface"]),
                    ],
                    spacing=2,
                ),
                # Build trailing controls without None
                trailing=ft.Column(
                    controls=(lambda: (lambda _c: (_c.append(ft.Container(width=10, height=10, bgcolor=self.palette["secondary"], border_radius=5)) if message["unread"] else None) or _c)([ft.Text(message["time"], size=12, color=self.palette["on_surface"])]) )(),
                    spacing=5,
                    horizontal_alignment=ft.CrossAxisAlignment.END,
                ),
                on_click=lambda e, m=message: self._open_message(m),
            ),
            bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]) if message["unread"] else None,
            border_radius=5,
            padding=2,
        )
    
    def _open_message(self, message):
        """Open a message for reading."""
        # Implement message opening logic
        pass


# ============ User Inbox Screen ============
class UserInboxScreen(BaseScreen):
    """User inbox for personal messages and notifications."""
    
    def build(self):
        """Build the user inbox screen."""
        content = ft.Column(
            controls=[
                self._build_inbox_header(),
                self._build_messages_list(),
            ],
            expand=True,
        )
        
        return self.build_screen_container(content, "My Inbox")
    
    def _build_inbox_header(self):
        """Build inbox header."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text("My Messages", size=20, weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                            ft.Text("Community updates and personal messages", size=14, color=self.palette["on_surface"]),
                        ],
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        icon=ft.icons.ADD,
                        icon_color=self.palette["primary"],
                        tooltip="Compose New Message",
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=20,
            bgcolor=ft.colors.with_opacity(0.8, self.palette["surface"]),
            border_radius=10,
        )
    
    def _build_messages_list(self):
        """Build the messages list."""
        # Sample user messages
        sample_messages = [
            {"id": 1, "sender": "CLC Admin", "subject": "Welcome to CLC Kenya!", "preview": "We're excited to have you join our community...", "time": "2 days ago", "read": True},
            {"id": 2, "sender": "Retreat Team", "subject": "Upcoming Retreat Details", "preview": "Here are the details for the spiritual retreat...", "time": "1 day ago", "read": False},
            {"id": 3, "sender": "Prayer Group", "subject": "Weekly Prayer Meeting", "preview": "Join us this Friday for our weekly prayer session...", "time": "3 hours ago", "read": False},
        ]
        
        message_tiles = []
        for msg in sample_messages:
            message_tiles.append(self._build_message_tile(msg))
        
        return ft.Container(
            content=ft.ListView(
                controls=message_tiles,
                expand=True,
                spacing=5,
            ),
            expand=True,
        )
    
    def _build_message_tile(self, message):
        """Build individual message tile."""
        return ft.Container(
            content=ft.ListTile(
                leading=ft.CircleAvatar(
                    content=ft.Text(message["sender"][0], color=ft.colors.WHITE),
                    bgcolor=self.palette["primary"],
                ),
                title=ft.Text(message["sender"], weight=ft.FontWeight.BOLD if not message["read"] else ft.FontWeight.NORMAL, color=self.palette["on_surface"]),
                subtitle=ft.Column(
                    controls=[
                        ft.Text(message["subject"], color=self.palette["on_surface"]),
                        ft.Text(message["preview"], size=12, color=self.palette["on_surface"]),
                    ],
                    spacing=2,
                ),
                # Build trailing controls explicitly to avoid None
                trailing=ft.Column(
                    controls=(lambda: (lambda _c: (_c.append(ft.Container(width=10, height=10, bgcolor=self.palette["secondary"], border_radius=5)) if not message["read"] else None) or _c)([ft.Text(message["time"], size=12, color=self.palette["on_surface"])]) )(),
                    spacing=5,
                    horizontal_alignment=ft.CrossAxisAlignment.END,
                ),
                on_click=lambda e, m=message: self._open_message(m),
            ),
            bgcolor=ft.colors.with_opacity(0.1, self.palette["primary"]) if not message["read"] else None,
            border_radius=5,
            padding=2,
        )
    
    def _open_message(self, message):
        """Open a message for reading."""
        # Implement message opening logic
        pass


# ============ Dashboard Classes ============
class UserDashboard(BaseScreen):
    """User main dashboard with navigation to all user features."""
    
    def build(self):
        """Build the user dashboard with bottom navigation."""
        # Return a main container with bottom nav that switches between screens
        return self.build_main_container(initial_screen="chat")
    
    def _build_welcome_section(self):
        """Build welcome section."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(
                        "Welcome back!",
                        size=28,
                        weight=ft.FontWeight.BOLD,
                        color=self.palette["on_surface"],
                    ),
                    ft.Text(
                        "In all things to love and to serve",
                        size=16,
                        color=self.palette["accent"],
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=30,
            bgcolor=ft.colors.with_opacity(0.8, self.palette["surface"]),
            border_radius=15,
            margin=ft.margin.symmetric(horizontal=10),
        )
    
    def _build_quick_actions(self):
        """Build quick action buttons."""
        actions = [
            {"icon": ft.icons.CHAT, "label": "Chat", "screen": "user_chat"},
            {"icon": ft.icons.INBOX, "label": "Inbox", "screen": "user_inbox"},
            {"icon": ft.icons.INFO, "label": "About", "screen": "about"},
            {"icon": ft.icons.SETTINGS, "label": "Settings", "screen": "user_settings"},
        ]
        
        action_buttons = []
        for action in actions:
            action_buttons.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(action["icon"], size=30, color=self.palette["primary"]),
                            ft.Text(action["label"], size=14, color=self.palette["on_surface"]),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                    ),
                    padding=20,
                    bgcolor=ft.colors.with_opacity(0.7, self.palette["surface"]),
                    border_radius=10,
                    on_click=lambda e, s=action["screen"]: self._navigate_to(s),
                    alignment=ft.alignment.center,
                    expand=True,
                )
            )
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Quick Actions", size=18, weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                    ft.ResponsiveRow(
                        columns=2,
                        controls=action_buttons,
                        spacing=10,
                    ),
                ],
                spacing=15,
            ),
            padding=20,
        )
    
    def _build_recent_activity(self):
        """Build recent activity section."""
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Recent Activity", size=18, weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                    ft.ListView(
                        controls=[
                            ft.ListTile(
                                leading=ft.Icon(ft.icons.CHAT, color=self.palette["primary"]),
                                title=ft.Text("New message from Admin", color=self.palette["on_surface"]),
                                subtitle=ft.Text("2 hours ago", color=self.palette["on_surface"]),
                            ),
                            ft.ListTile(
                                leading=ft.Icon(ft.icons.EVENT, color=self.palette["primary"]),
                                title=ft.Text("Upcoming retreat reminder", color=self.palette["on_surface"]),
                                subtitle=ft.Text("1 day ago", color=self.palette["on_surface"]),
                            ),
                        ],
                        height=150,
                    ),
                ],
                spacing=10,
            ),
            padding=20,
            bgcolor=ft.colors.with_opacity(0.7, self.palette["surface"]),
            border_radius=15,
            margin=ft.margin.symmetric(horizontal=10),
        )
    
    def _navigate_to(self, screen_name):
        """Navigate to different screens."""
        if screen_name == "user_chat":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="chat")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.user_chat_screen.build()))
        elif screen_name == "user_inbox":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="inbox")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.user_inbox_screen.build()))
        elif screen_name == "about":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="about")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.about_screen.build()))
        elif screen_name == "user_settings":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="settings")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.user_settings_screen.build()))


class AdminDashboard(BaseScreen):
    """Admin main dashboard with navigation to all admin features."""
    
    def build(self):
        """Build the admin dashboard with bottom navigation."""
        # Return a main container with bottom nav that switches between screens
        return self.build_main_container(initial_screen="inbox")
    
    def _build_admin_header(self):
        """Build admin header section."""
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Text(
                                "Admin Dashboard",
                                size=28,
                                weight=ft.FontWeight.BOLD,
                                color=self.palette["on_surface"],
                            ),
                            ft.Text(
                                "Manage CLC Kenya Community",
                                size=16,
                                color=self.palette["accent"],
                            ),
                        ],
                    ),
                    ft.Container(expand=True),
                    ft.ElevatedButton(
                        "System Health: Good",
                        bgcolor=self.palette["success"],
                        color=ft.colors.WHITE,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=30,
            bgcolor=ft.colors.with_opacity(0.8, self.palette["surface"]),
            border_radius=15,
            margin=ft.margin.symmetric(horizontal=10),
        )
    
    def _build_admin_actions(self):
        """Build admin action buttons."""
        actions = [
            {"icon": ft.icons.CHAT, "label": "Admin Chat", "screen": "admin_chat"},
            {"icon": ft.icons.INBOX, "label": "Admin Inbox", "screen": "admin_inbox"},
            {"icon": ft.icons.PEOPLE, "label": "User Management", "screen": "user_management"},
            {"icon": ft.icons.SETTINGS, "label": "Admin Settings", "screen": "admin_settings"},
            {"icon": ft.icons.ANALYTICS, "label": "Analytics", "screen": "analytics"},
            {"icon": ft.icons.INFO, "label": "About", "screen": "about"},
        ]
        
        action_buttons = []
        for action in actions:
            action_buttons.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Icon(action["icon"], size=30, color=self.palette["primary"]),
                            ft.Text(action["label"], size=14, color=self.palette["on_surface"], text_align=ft.TextAlign.CENTER),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                    ),
                    padding=20,
                    bgcolor=ft.colors.with_opacity(0.7, self.palette["surface"]),
                    border_radius=10,
                    on_click=lambda e, s=action["screen"]: self._navigate_to(s),
                    alignment=ft.alignment.center,
                    expand=True,
                )
            )
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Admin Tools", size=18, weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                    ft.ResponsiveRow(
                        columns=3,
                        controls=action_buttons,
                        spacing=10,
                    ),
                ],
                spacing=15,
            ),
            padding=20,
        )
    
    def _build_system_overview(self):
        """Build system overview section."""
        stats = [
            {"label": "Total Users", "value": "1,245", "icon": ft.icons.PEOPLE, "color": self.palette["primary"]},
            {"label": "Active Chats", "value": "23", "icon": ft.icons.CHAT, "color": self.palette["secondary"]},
            {"label": "Unread Messages", "value": "12", "icon": ft.icons.EMAIL, "color": self.palette["accent"]},
            {"label": "Online Now", "value": "45", "icon": ft.icons.ONLINE_PREDICTION, "color": self.palette["success"]},
        ]
        
        stat_cards = []
        for stat in stats:
            stat_cards.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Icon(stat["icon"], color=stat["color"]),
                                    ft.Container(expand=True),
                                    ft.Text(stat["value"], size=24, weight=ft.FontWeight.BOLD, color=stat["color"]),
                                ],
                            ),
                            ft.Text(stat["label"], size=14, color=self.palette["on_surface"]),
                        ],
                        spacing=10,
                    ),
                    padding=20,
                    bgcolor=ft.colors.with_opacity(0.7, self.palette["surface"]),
                    border_radius=10,
                    expand=True,
                )
            )
        
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("System Overview", size=18, weight=ft.FontWeight.BOLD, color=self.palette["on_surface"]),
                    ft.ResponsiveRow(
                        columns=2,
                        controls=stat_cards,
                        spacing=10,
                    ),
                ],
                spacing=15,
            ),
            padding=20,
        )
    
    def _navigate_to(self, screen_name):
        """Navigate to different admin screens."""
        if screen_name == "admin_chat":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="chat")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.admin_chat_screen.build()))
        elif screen_name == "admin_inbox":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="inbox")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.admin_inbox_screen.build()))
        elif screen_name == "admin_settings":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="settings")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.admin_settings_screen.build()))
        elif screen_name == "about":
            self.page.clean()
            try:
                new_layout = self.build_main_container(initial_screen="about")
                self.page.add(self.app._wrap_with_global_background(new_layout))
            except Exception:
                self.page.add(self.app._wrap_with_global_background(self.app.about_screen.build()))
# ========== Main app ==========
def main(page: ft.Page):
    page.title = "CLC KENYA "
    page.window.full_screen = False
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.spacing = 0
    
    # Enable page scrolling
    page.scroll = ft.ScrollMode.ADAPTIVE
    
    # Set page background
    page.bgcolor = PALETTE["background"]

    # Initialize firebase-admin inside the Flet app process so admin SDK is
    # available to screen handlers. Doing this inside `main` ensures the
    # initialization runs in the same process/runtime as the UI handlers.
    try:
        ok = _init_firebase_admin_if_possible()
        if ok:
            print(f"Firebase admin initialized. FIREBASE_DB_URL={FIREBASE_DB_URL}")
        else:
            print("Firebase admin not initialized (no valid service account detected or init failed).")
    except Exception as ex:
        print(f"Error during firebase-admin init in main(): {ex}")
    
    # Instantiate the main app controller so it manages navigation and dashboards
    app = CLCKenyaApp(page)

    # Show the login screen using the app controller (this wires on_login_success to
    # the app's on_login_success which will redirect to the dashboard on success)
    app.show_login_screen()

if __name__ == "__main__":
    ft.app(target=main)

