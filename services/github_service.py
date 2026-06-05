"""
GitHub Service for pushing .feature files to repositories.
Uses the GitHub REST API (via PyGithub or direct requests).
"""

import requests
import base64
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GitHubService:
    """Service for interacting with GitHub API to push feature files"""

    def __init__(self, token: str):
        """
        Initialize GitHub service with a Personal Access Token.

        Args:
            token: GitHub PAT with repo scope
        """
        self.token = token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def test_connection(self) -> Dict:
        """Verify the token is valid and return authenticated user info"""
        resp = requests.get(
            f"{self.base_url}/user", headers=self.headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return {"login": data["login"], "name": data.get("name")}

    def _parse_repo(self, repo_url: str) -> tuple:
        """Extract owner/repo from a GitHub URL or owner/repo string"""
        # Handle full URLs
        match = re.match(
            r"(?:https?://github\.com/)?([^/]+)/([^/.\s]+?)(?:\.git)?/?$",
            repo_url.strip(),
        )
        if not match:
            raise ValueError(
                f"Invalid GitHub repository: {repo_url}. "
                "Expected format: https://github.com/owner/repo or owner/repo"
            )
        return match.group(1), match.group(2)

    def _get_repo_info(self, owner: str, repo: str) -> Optional[Dict]:
        """Return the repo info dict, or None if the repo doesn't exist."""
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}",
            headers=self.headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        return None

    def _bootstrap_empty_repo(self, owner: str, repo: str, default_branch: str) -> str:
        """
        Create the FIRST commit in an empty repo by PUTting a README on the
        repo's default branch. GitHub creates the initial commit + branch
        automatically when you PUT a file to an empty repo (only if the
        target branch equals the repo's configured default branch).

        Returns the SHA of the resulting first commit.
        """
        readme_path = "README.md"
        readme_body = (
            f"# {repo}\n\n"
            "Initial commit created automatically by the SDLC test-generation "
            "service so that auto-generated feature files have a base branch "
            "to commit to.\n"
        )
        resp = requests.put(
            f"{self.base_url}/repos/{owner}/{repo}/contents/{readme_path}",
            headers=self.headers,
            json={
                "message": "chore: initial commit (auto)",
                "content": base64.b64encode(readme_body.encode("utf-8")).decode("ascii"),
                "branch": default_branch,
            },
            timeout=15,
        )
        resp.raise_for_status()
        first_sha = resp.json()["commit"]["sha"]
        logger.info(
            f"Bootstrapped empty repo {owner}/{repo}: initial commit "
            f"{first_sha[:8]} on '{default_branch}'."
        )
        return first_sha

    def _get_or_create_branch(self, owner: str, repo: str, branch: str, base_branch: str = "main") -> Optional[str]:
        """
        Ensure target branch exists. Returns the latest commit SHA on the
        branch, or None if the repo was empty and the target branch IS the
        repo's default branch (in that case the caller's next PUT will
        create the initial commit automatically).

        Handles three cases:
          1. Target branch exists                            → return its SHA
          2. Target missing, some base branch exists         → create target from base
          3. Repo is completely empty (no branches at all)   → auto-bootstrap
             a. If target == default_branch: return None (PUT will init the repo)
             b. If target != default_branch: PUT a README on default branch to
                create the initial commit, then create target from there
        """
        # 1. Try target branch
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}/branches/{branch}",
            headers=self.headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["commit"]["sha"]

        # 2. Target missing — try every plausible base
        for candidate in (base_branch, "main", "master"):
            base_resp = requests.get(
                f"{self.base_url}/repos/{owner}/{repo}/branches/{candidate}",
                headers=self.headers,
                timeout=10,
            )
            if base_resp.status_code == 200:
                base_sha = base_resp.json()["commit"]["sha"]
                create_resp = requests.post(
                    f"{self.base_url}/repos/{owner}/{repo}/git/refs",
                    headers=self.headers,
                    json={"ref": f"refs/heads/{branch}", "sha": base_sha},
                    timeout=10,
                )
                create_resp.raise_for_status()
                logger.info(
                    f"Created branch '{branch}' from {candidate} @ {base_sha[:8]}"
                )
                return base_sha

        # 3. No branches exist at all. Repo is empty — bootstrap it.
        info = self._get_repo_info(owner, repo)
        if info is None:
            # Repo doesn't exist or token has no access. Surface a clearer error.
            raise RuntimeError(
                f"Repository {owner}/{repo} not found or inaccessible. "
                "Check the URL and that the PAT has 'repo' scope."
            )

        default_branch = info.get("default_branch") or "main"

        if branch == default_branch:
            # Target IS the default branch. The caller's PUT will create the
            # initial commit on it. Nothing to do here.
            logger.info(
                f"Repo {owner}/{repo} is empty. Target branch '{branch}' is the "
                f"default — caller's PUT will create the initial commit."
            )
            return None

        # Target is something other than the default (e.g. test/auto-generated).
        # We need a commit on the default branch to branch from. Create one.
        logger.info(
            f"Repo {owner}/{repo} is empty. Bootstrapping default branch "
            f"'{default_branch}', then creating target branch '{branch}'."
        )
        base_sha = self._bootstrap_empty_repo(owner, repo, default_branch)
        create_resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/git/refs",
            headers=self.headers,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            timeout=10,
        )
        create_resp.raise_for_status()
        logger.info(
            f"Created target branch '{branch}' from '{default_branch}' @ {base_sha[:8]}"
        )
        return base_sha

    def _create_or_update_file(
        self, owner: str, repo: str, path: str, content: str, branch: str, message: str
    ) -> Dict:
        """Create or update a single file via the Contents API"""
        url = f"{self.base_url}/repos/{owner}/{repo}/contents/{path}"

        # Check if file already exists (to get its SHA for update)
        existing_sha = None
        resp = requests.get(
            url, headers=self.headers, params={"ref": branch}, timeout=10
        )
        if resp.status_code == 200:
            existing_sha = resp.json().get("sha")

        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        resp = requests.put(url, headers=self.headers, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def push_feature_files(
        self,
        repo_url: str,
        feature_files: List[Dict],
        branch: str = "test/auto-generated",
        base_path: str = "Include/features",
        create_pr: bool = True,
    ) -> Dict:
        """
        Push multiple .feature files to a GitHub repository.

        Args:
            repo_url: GitHub repo URL or owner/repo string
            feature_files: List of dicts with 'filename' and 'content' keys
            branch: Target branch name
            base_path: Directory path in repo for feature files.
                       Defaults to `Include/features` so files land in a
                       Katalon-recognised location — Katalon Studio's BDD
                       Cucumber plugin and the Katalon AI Assistant's
                       "Select files to attach" dialog both look there.
                       Override only if pushing to a non-Katalon project.
            create_pr: Whether to create a PR after pushing

        Returns:
            Dict with branch, files pushed, and optional PR URL
        """
        owner, repo = self._parse_repo(repo_url)
        logger.info(f"Pushing {len(feature_files)} feature files to {owner}/{repo} on branch '{branch}'")

        # Ensure the branch exists
        self._get_or_create_branch(owner, repo, branch)

        pushed_files = []
        for ff in feature_files:
            filename = ff["filename"]
            if not filename.endswith(".feature"):
                filename += ".feature"
            file_path = f"{base_path}/{filename}"

            self._create_or_update_file(
                owner=owner,
                repo=repo,
                path=file_path,
                content=ff["content"],
                branch=branch,
                message=f"test: add {filename} (auto-generated Gherkin)",
            )
            pushed_files.append(file_path)
            logger.info(f"  Pushed: {file_path}")

        result = {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "files": pushed_files,
            "repo_url": f"https://github.com/{owner}/{repo}",
            "branch_url": f"https://github.com/{owner}/{repo}/tree/{branch}",
        }

        # Create a Pull Request if requested
        if create_pr:
            try:
                pr = self._create_pull_request(
                    owner=owner,
                    repo=repo,
                    head=branch,
                    title=f"test: auto-generated Gherkin feature files",
                    body=self._build_pr_body(pushed_files),
                )
                result["pr_url"] = pr["html_url"]
                result["pr_number"] = pr["number"]
                logger.info(f"  Created PR #{pr['number']}: {pr['html_url']}")
            except Exception as e:
                # PR creation is best-effort (may already exist)
                logger.warning(f"Could not create PR: {e}")
                result["pr_url"] = None

        return result

    def _create_pull_request(
        self, owner: str, repo: str, head: str, title: str, body: str, base: str = "main"
    ) -> Dict:
        """Create a pull request"""
        # Check if a PR already exists for this branch
        resp = requests.get(
            f"{self.base_url}/repos/{owner}/{repo}/pulls",
            headers=self.headers,
            params={"head": f"{owner}:{head}", "state": "open"},
            timeout=10,
        )
        if resp.status_code == 200 and resp.json():
            # Return existing PR
            return resp.json()[0]

        resp = requests.post(
            f"{self.base_url}/repos/{owner}/{repo}/pulls",
            headers=self.headers,
            json={"title": title, "body": body, "head": head, "base": base},
            timeout=15,
        )
        if resp.status_code == 422:
            # If base branch is 'main' but repo uses 'master', retry
            resp = requests.post(
                f"{self.base_url}/repos/{owner}/{repo}/pulls",
                headers=self.headers,
                json={"title": title, "body": body, "head": head, "base": "master"},
                timeout=15,
            )
        resp.raise_for_status()
        return resp.json()

    def _build_pr_body(self, files: List[str]) -> str:
        file_list = "\n".join(f"- `{f}`" for f in files)
        return (
            "## Auto-Generated Gherkin Test Cases\n\n"
            "These `.feature` files were auto-generated from BRD test scenarios "
            "via the QA Testing Pipeline.\n\n"
            f"### Files\n{file_list}\n\n"
            "### Next Steps\n"
            "1. Review the generated Gherkin scenarios\n"
            "2. Import into Katalon Studio (File → Import → BDD Feature Files)\n"
            "3. Katalon will auto-generate Groovy step definition stubs\n"
            "4. Implement the step definitions and run\n"
        )
