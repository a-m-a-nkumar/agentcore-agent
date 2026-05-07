import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

BITBUCKET_API = "https://api.bitbucket.org/2.0"


class BitbucketService:
    """Service for interacting with Bitbucket Cloud API using Atlassian credentials."""

    def __init__(self, email: str, api_token: str):
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json"}

    def test_connection(self) -> Tuple[bool, Optional[str]]:
        """Verify credentials by fetching the authenticated user profile."""
        try:
            resp = requests.get(f"{BITBUCKET_API}/user", auth=self.auth, timeout=15)
            if resp.status_code == 200:
                return True, None
            if resp.status_code == 401:
                return False, "Invalid credentials. The Atlassian API token does not have Bitbucket access."
            return False, f"Connection failed ({resp.status_code}): {resp.text[:200]}"
        except requests.exceptions.ConnectionError:
            return False, "Could not reach api.bitbucket.org. Check your network connection."
        except Exception as e:
            return False, str(e)

    def get_user(self) -> Dict:
        """Return the authenticated Bitbucket user profile."""
        resp = requests.get(f"{BITBUCKET_API}/user", auth=self.auth, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_workspaces(self) -> List[Dict]:
        """List all workspaces the user is a member of."""
        workspaces = []
        url = f"{BITBUCKET_API}/workspaces"
        while url:
            resp = requests.get(url, auth=self.auth, params={"pagelen": 100}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for ws in data.get("values", []):
                workspaces.append({
                    "slug": ws.get("slug"),
                    "name": ws.get("name"),
                    "uuid": ws.get("uuid"),
                })
            url = data.get("next")
        return workspaces

    def get_repositories(self, workspace: str) -> List[Dict]:
        """List all repositories in a workspace."""
        repos = []
        url = f"{BITBUCKET_API}/repositories/{workspace}"
        while url:
            resp = requests.get(url, auth=self.auth, params={"pagelen": 100}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for r in data.get("values", []):
                repos.append({
                    "slug": r.get("slug"),
                    "name": r.get("name"),
                    "full_name": r.get("full_name"),
                    "is_private": r.get("is_private"),
                    "scm": r.get("scm"),
                })
            url = data.get("next")
        return repos

    def get_branches(self, workspace: str, repo_slug: str) -> List[str]:
        """List branch names for a repository."""
        branches = []
        url = f"{BITBUCKET_API}/repositories/{workspace}/{repo_slug}/refs/branches"
        while url:
            resp = requests.get(url, auth=self.auth, params={"pagelen": 100}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for b in data.get("values", []):
                branches.append(b.get("name"))
            url = data.get("next")
        return branches

    def _list_directory(self, workspace: str, repo_slug: str, path: str, ref: str) -> List[Dict]:
        """Recursively list all files under a path."""
        url = f"{BITBUCKET_API}/repositories/{workspace}/{repo_slug}/src/{ref}/{path}"
        resp = requests.get(url, auth=self.auth, params={"pagelen": 100}, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        entries = []
        for item in data.get("values", []):
            item_type = item.get("type")
            item_path = item.get("path", "")
            if item_type == "commit_file":
                entries.append({"type": "file", "path": item_path, "size": item.get("size", 0)})
            elif item_type == "commit_directory":
                entries.extend(self._list_directory(workspace, repo_slug, item_path, ref))
        return entries

    def list_files(self, workspace: str, repo_slug: str, ref: str = "main", path: str = "") -> List[Dict]:
        """
        Return a flat list of all files in the repo at the given ref.
        Each entry: {"type": "file", "path": "...", "size": N}
        """
        return self._list_directory(workspace, repo_slug, path, ref)

    def get_file_content(self, workspace: str, repo_slug: str, path: str, ref: str = "main") -> str:
        """Fetch raw content of a single file."""
        url = f"{BITBUCKET_API}/repositories/{workspace}/{repo_slug}/src/{ref}/{path}"
        resp = requests.get(url, auth=self.auth, timeout=30)
        resp.raise_for_status()
        return resp.text

    def get_files_bulk(
        self,
        workspace: str,
        repo_slug: str,
        ref: str = "main",
        path: str = "",
        extensions: Optional[List[str]] = None,
        max_files: int = 100,
    ) -> Dict[str, str]:
        """
        Fetch multiple files from the repo and return {path: content}.
        Optionally filter by file extensions (e.g. [".tf", ".tfvars"]).
        """
        all_files = self.list_files(workspace, repo_slug, ref, path)

        if extensions:
            all_files = [f for f in all_files if any(f["path"].endswith(ext) for ext in extensions)]

        all_files = all_files[:max_files]

        result: Dict[str, str] = {}
        for entry in all_files:
            try:
                result[entry["path"]] = self.get_file_content(workspace, repo_slug, entry["path"], ref)
            except Exception as e:
                logger.warning(f"Could not fetch {entry['path']}: {e}")
        return result
