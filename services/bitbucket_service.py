import re
import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

BITBUCKET_API = "https://api.bitbucket.org/2.0"
BITBUCKET_WEB = "https://bitbucket.org"


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

    @staticmethod
    def parse_repo_url(repo_url: str) -> Tuple[str, str]:
        """Extract workspace and repo_slug from a Bitbucket URL or workspace/slug string."""
        match = re.match(
            r"(?:https?://bitbucket\.org/)?([^/\s]+)/([^/.\s]+?)(?:\.git)?/?$",
            repo_url.strip(),
        )
        if not match:
            raise ValueError(
                f"Invalid Bitbucket repository: {repo_url}. "
                "Expected format: https://bitbucket.org/workspace/repo or workspace/repo"
            )
        return match.group(1), match.group(2)

    def _get_default_branch(self, workspace: str, repo_slug: str) -> str:
        """Return the default branch name for the repository."""
        resp = requests.get(
            f"{BITBUCKET_API}/repositories/{workspace}/{repo_slug}",
            auth=self.auth,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("mainbranch", {}).get("name", "main")

    def push_feature_files(
        self,
        workspace: str,
        repo_slug: str,
        feature_files: List[Dict],
        branch: str = "test/auto-generated",
        base_path: str = "Include/features",
        create_pr: bool = True,
        commit_message: str = None,
    ) -> Dict:
        """
        Commit feature files to a Bitbucket repository using the src API.
        Creates the branch if it doesn't exist, then optionally opens a PR.
        """
        if not feature_files:
            raise ValueError("No feature files provided")

        message = commit_message or f"feat(tests): add {len(feature_files)} auto-generated .feature file(s)"

        # Build multipart form-data. The Bitbucket src API accepts:
        #   branch=<name>, message=<text>, <file-path>=<content>, ...
        files_form = []
        committed_paths = []
        for ff in feature_files:
            full_path = f"{base_path}/{ff['filename']}" if base_path else ff["filename"]
            files_form.append((full_path, (None, ff["content"], "text/plain")))
            committed_paths.append(full_path)

        data = {"branch": branch, "message": message}

        url = f"{BITBUCKET_API}/repositories/{workspace}/{repo_slug}/src"
        resp = requests.post(url, auth=self.auth, data=data, files=files_form, timeout=60)

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Bitbucket src API error {resp.status_code}: {resp.text[:300]}"
            )

        branch_url = f"{BITBUCKET_WEB}/{workspace}/{repo_slug}/src/{branch}/"
        result: Dict = {
            "branch": branch,
            "branch_url": branch_url,
            "files": committed_paths,
            "pr_url": None,
            "pr_number": None,
        }

        if create_pr:
            try:
                default_branch = self._get_default_branch(workspace, repo_slug)
                if branch != default_branch:
                    pr_resp = requests.post(
                        f"{BITBUCKET_API}/repositories/{workspace}/{repo_slug}/pullrequests",
                        auth=self.auth,
                        json={
                            "title": f"feat(tests): auto-generated .feature files",
                            "description": (
                                f"Automatically generated {len(feature_files)} Gherkin .feature "
                                f"file(s) under `{base_path}/`.\n\nGenerated by Velox SDLC."
                            ),
                            "source": {"branch": {"name": branch}},
                            "destination": {"branch": {"name": default_branch}},
                            "close_source_branch": False,
                        },
                        headers={"Content-Type": "application/json"},
                        timeout=30,
                    )
                    if pr_resp.status_code in (200, 201):
                        pr_data = pr_resp.json()
                        result["pr_number"] = pr_data.get("id")
                        result["pr_url"] = pr_data.get("links", {}).get("html", {}).get("href")
            except Exception as e:
                logger.warning(f"PR creation failed (files still committed): {e}")

        return result
