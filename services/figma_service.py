"""
Figma Service — wraps the Figma REST API.
Auth: X-Figma-Token header (Personal Access Token).
"""

import requests
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class FigmaService:
    BASE_URL = "https://api.figma.com/v1"

    def __init__(self, pat: str, team_id: str):
        self.headers = {"X-Figma-Token": pat}
        self.team_id = team_id

    def test_connection(self) -> tuple[bool, Optional[str]]:
        """Validate PAT via GET /me. Returns (ok, error_message)."""
        try:
            resp = requests.get(f"{self.BASE_URL}/me", headers=self.headers, timeout=10)
            if resp.ok and not resp.json().get("err"):
                return True, None
            return False, "Invalid PAT — check your token and try again"
        except requests.exceptions.Timeout:
            return False, "Request timed out — check your network"
        except Exception as e:
            return False, str(e)

    def get_team_projects(self) -> list:
        """GET /teams/{team_id}/projects → [{id, name}]"""
        resp = requests.get(
            f"{self.BASE_URL}/teams/{self.team_id}/projects",
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("projects", [])

    def get_project_files(self, project_id: str) -> list:
        """GET /projects/{project_id}/files → [{key, name, thumbnail_url, last_modified}]"""
        resp = requests.get(
            f"{self.BASE_URL}/projects/{project_id}/files",
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("files", [])
