"""Minimal client for the Metabase API, shared by the build and verify scripts.

Targets the local Metabase by default. Set the METABASE_URL environment
variable to target another one, for example the hosted demo.
"""

import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

import requests

METABASE_URL = os.environ.get("METABASE_URL", "http://localhost:3000").rstrip("/")
DATA_DIR = Path(__file__).resolve().parent.parent / ".metabase"
# One admin account, and one credentials file, per Metabase.
_HOST = urlparse(METABASE_URL).hostname
CREDENTIALS_FILE = DATA_DIR / (
    "admin-credentials.json" if _HOST == "localhost" else f"admin-credentials-{_HOST}.json"
)
ADMIN_EMAIL = "admin@toulouse-data.local"
# Name of the BigQuery connection inside Metabase.
DATABASE_NAME = "Toulouse Data (BigQuery)"


class Metabase:
    def __init__(self) -> None:
        self.http = requests.Session()
        self.url = METABASE_URL

    def call(self, method: str, path: str, **kwargs):
        response = self.http.request(method, f"{METABASE_URL}/api/{path}", timeout=180, **kwargs)
        if not response.ok:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response.json() if response.content else None

    def get(self, path: str, **params):
        return self.call("GET", path, params=params)

    def post(self, path: str, body: dict | None = None):
        # After Metabase is killed without a clean shutdown, its H2 database
        # can hand out ids that are already taken. Each attempt moves the id
        # sequence forward, so retrying gets past the used ids.
        for _ in range(50):
            try:
                return self.call("POST", path, json=body or {})
            except RuntimeError as error:
                if "PRIMARY KEY" not in str(error):
                    raise
        raise RuntimeError(f"POST {path}: still hitting used ids after 50 attempts")

    def put(self, path: str, body: dict):
        return self.call("PUT", path, json=body)

    def login(self) -> "Metabase":
        """Log in as the admin, creating the account on first use."""
        if CREDENTIALS_FILE.exists():
            credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        else:
            credentials = self._create_admin()
        session = self.post(
            "session",
            {"username": credentials["email"], "password": credentials["password"]},
        )
        self.http.headers["X-Metabase-Session"] = session["id"]
        return self

    def _create_admin(self) -> dict:
        properties = self.get("session/properties")
        if properties.get("has-user-setup"):
            raise RuntimeError(
                f"Metabase already has an admin but {CREDENTIALS_FILE} is missing."
            )
        credentials = {"email": ADMIN_EMAIL, "password": secrets.token_urlsafe(18)}
        self.post(
            "setup",
            {
                "token": properties["setup-token"],
                "user": {
                    "first_name": "Local",
                    "last_name": "Admin",
                    "email": credentials["email"],
                    "password": credentials["password"],
                    "site_name": "Toulouse Data",
                },
                "prefs": {"site_name": "Toulouse Data", "allow_tracking": False},
            },
        )
        DATA_DIR.mkdir(exist_ok=True)
        CREDENTIALS_FILE.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
        print(f"Created the admin account; credentials are in {CREDENTIALS_FILE}")
        return credentials
