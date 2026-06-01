import time
import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Module-level cache of the FULL Jira project list, keyed by user email so
# different users never see each other's projects. Mirrors the equivalent
# cache in confluence_service. 5-min TTL: new Jira projects appear within
# that window but every modal open after the first hits memory, not Jira.
_PROJECT_LIST_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}
_PROJECT_LIST_CACHE_TTL_SECS = 300  # 5 minutes


class JiraService:
    """Service for interacting with Jira Cloud API"""

    def __init__(self, domain: str, email: str, api_token: str):
        """
        Initialize Jira service

        Args:
            domain: Atlassian domain (e.g., 'mycompany.atlassian.net')
            email: User's email address
            api_token: Atlassian API token
        """
        self.base_url = f"https://{domain}"
        self.email = email                          # used as cache key for the project list
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        # requests.Session pools TCP + TLS so subsequent paginated calls reuse
        # warm connections instead of paying a fresh handshake each time.
        # Same change we made on Confluence — and the right baseline for any
        # service that does multi-page enumeration against Atlassian.
        self._session = requests.Session()
    
    def test_connection(self) -> tuple[bool, Optional[str]]:
        """
        Test if credentials are valid by fetching current user info
        
        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        try:
            url = f"{self.base_url}/rest/api/3/myself"
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=30)
            
            if response.status_code == 200:
                return (True, None)
            elif response.status_code == 401:
                return (False, "Invalid email or API token. Please check your credentials.")
            elif response.status_code == 404:
                return (False, f"Invalid domain '{self.base_url}'. Please check your Atlassian domain.")
            else:
                return (False, f"Connection failed with status {response.status_code}: {response.text}")
                
        except requests.exceptions.Timeout:
            logger.error("Jira connection test timed out")
            return (False, "Connection timed out. Please check your network connection.")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Jira connection error: {e}")
            return (False, f"Could not connect to {self.base_url}. Please verify the domain is correct.")
        except Exception as e:
            logger.error(f"Jira connection test failed: {e}")
            return (False, f"Connection test failed: {str(e)}")
    
    def _fetch_all_projects(self) -> List[Dict]:
        """One full paginated enumeration of /rest/api/3/project/search.

        Used to (re)populate the module-level cache. Sequential pagination
        (no parallelism): Jira's /project/search endpoint is fast enough
        and adding ThreadPoolExecutor here is unnecessary risk — Atlassian's
        WAF was happy to serve linear page-by-page reads on this endpoint
        in our testing. Session reuse keeps TLS warm across the pages.
        """
        url = f"{self.base_url}/rest/api/3/project/search"
        all_projects: List[Dict] = []
        start_at = 0
        page_size = 50  # Jira /project/search hard cap
        while True:
            response = self._session.get(
                url,
                headers=self.headers,
                auth=self.auth,
                params={"startAt": start_at, "maxResults": page_size},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            values = payload.get("values", [])
            all_projects.extend(values)
            if payload.get("isLast", True) or len(values) < page_size:
                break
            start_at += page_size

        return [
            {
                "key": project["key"],
                "name": project["name"],
                "id": project["id"],
                "type": project.get("projectTypeKey", "software"),
            }
            for project in all_projects
        ]

    def get_projects(self) -> List[Dict]:
        """
        Return all accessible Jira projects for the linked account.

        Backed by `_PROJECT_LIST_CACHE` keyed by user email with a 5-min TTL.
        First call within the window fills the cache (one full paginated
        enumeration of /rest/api/3/project/search); every subsequent call
        — including each time the user re-opens the create-project modal —
        returns the cached list instantly without touching Jira.

        Why a cache: the frontend Jira picker loads the full project list
        on every modal open and then does cmdk-side filtering. Without a
        cache that's a fresh ~2s Jira round-trip every time the user clicks
        "+ New Project", on top of the same call that already fires for
        Confluence spaces. With it, the second-and-onwards modal open is
        effectively free.
        """
        now = time.time()
        cached = _PROJECT_LIST_CACHE.get(self.email)
        if cached and (now - cached[0]) < _PROJECT_LIST_CACHE_TTL_SECS:
            return cached[1]

        try:
            logger.info(
                f"[Jira] cache miss/expired for {self.email}; fetching full project list…"
            )
            projects = self._fetch_all_projects()
            _PROJECT_LIST_CACHE[self.email] = (now, projects)
            logger.info(
                f"[Jira] cached {len(projects)} projects for {self.email} "
                f"(TTL {_PROJECT_LIST_CACHE_TTL_SECS}s)"
            )
            return projects
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Jira projects: {e}")
            raise Exception(f"Failed to fetch Jira projects: {str(e)}")
    
    def get_project_issues(self, project_key: str) -> List[Dict]:
        """
        Fetch ALL issues from a specific Jira project using cursor pagination.
        Results are ordered by updated date descending (most recently active first).

        Atlassian replaced /rest/api/3/search with /rest/api/3/search/jql in 2024.
        The new endpoint uses cursor-based pagination via `nextPageToken` and
        signals completion with `isLast: true`. It does NOT return a `total`
        field by default, so the old startAt/total loop terminated after a
        single page and silently capped results at ~100 issues — that's why
        the Status / Type dropdowns in the Jira module were only showing the
        statuses/types that happened to fall in the first 100 results.

        Args:
            project_key: The Jira project key (e.g., 'PROJ')

        Returns:
            List of all issues with all fields needed by the frontend
        """
        fields = [
            "summary",
            "description",
            "status",
            "assignee",
            "reporter",
            "priority",
            "issuetype",
            "created",
            "updated",
            "labels",
            "customfield_10016",  # Story points
            "customfield_10014",  # Epic Link — classic / company-managed projects
                                   # use this to point stories at their epic;
                                   # newer team-managed projects unify under
                                   # `parent` but customfield_10014 is still
                                   # populated on the legacy ones. Fetching
                                   # both ensures the frontend can resolve
                                   # the hierarchy on either project style.
            "parent",
        ]

        jql = f"project = {project_key} ORDER BY updated DESC"
        url = f"{self.base_url}/rest/api/3/search/jql"
        page_size = 100
        next_page_token: Optional[str] = None
        all_issues: List[Dict] = []
        batch_num = 0

        logger.info(f"[Jira] Fetching ALL issues from {url} with JQL: {jql}")

        try:
            while True:
                batch_num += 1
                params: Dict[str, str] = {
                    "jql": jql,
                    "maxResults": str(page_size),
                    "fields": ",".join(fields),
                }
                if next_page_token:
                    params["nextPageToken"] = next_page_token

                response = self._session.get(
                    url,
                    headers=self.headers,
                    auth=self.auth,
                    params=params,
                    timeout=30,
                )

                if response.status_code == 400:
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('errorMessages', ['Invalid JQL query'])[0]
                        logger.error(f"Jira 400 error: {error_msg}")
                        raise Exception(f"Invalid request: {error_msg}")
                    except Exception as parse_err:
                        if "Invalid request" in str(parse_err):
                            raise
                        raise Exception(f"Invalid request for project '{project_key}'")

                elif response.status_code == 404:
                    logger.error(f"Project {project_key} not found (404)")
                    raise Exception(f"Project '{project_key}' not found. Please verify the project key is correct.")

                elif response.status_code == 410:
                    logger.error(f"Project {project_key} returned 410")
                    raise Exception(f"Project '{project_key}' may be archived, deleted, or inaccessible. Please verify in Jira.")

                response.raise_for_status()
                result = response.json()
                issues = result.get('issues', []) or []
                all_issues.extend(issues)

                is_last = result.get('isLast')
                next_page_token = result.get('nextPageToken')

                logger.info(
                    f"[Jira] project={project_key} batch {batch_num}: {len(issues)} issues "
                    f"(running total={len(all_issues)}, isLast={is_last}, "
                    f"hasNextToken={bool(next_page_token)})"
                )

                # New cursor API: stop when isLast=true, no nextPageToken, or
                # the batch came back empty. We rely on the token rather than
                # `total` (which the new endpoint may omit).
                if is_last is True or not next_page_token or len(issues) == 0:
                    break

                # Defensive cap to avoid runaway loops on a misbehaving server
                # (would mean Atlassian kept handing back tokens but no issues).
                if batch_num >= 500:
                    logger.warning(
                        f"[Jira] project={project_key} reached batch cap (500). "
                        f"Stopping pagination at {len(all_issues)} issues."
                    )
                    break

            logger.info(
                f"[Jira] DONE — fetched all {len(all_issues)} issues from "
                f"project {project_key} across {batch_num} batches"
            )
            return all_issues

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error fetching Jira issues: {e}")
            try:
                logger.error(f"Response status: {response.status_code}, body: {response.text[:500]}")
            except Exception:
                pass
            raise Exception(f"Failed to fetch Jira issues: {str(e)}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Jira issues: {e}")
            raise Exception(f"Failed to fetch Jira issues: {str(e)}")
    
    def get_project_issue_types(self, project_key: str) -> List[Dict]:
        """
        Fetch available issue types for a specific project
        
        Args:
            project_key: The Jira project key (e.g., 'PROJ')
            
        Returns:
            List of issue types with id, name, and other metadata
        """
        try:
            # First get the project details to get the project ID
            url = f"{self.base_url}/rest/api/3/project/{project_key}"
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=30)
            response.raise_for_status()
            
            project_data = response.json()
            project_id = project_data.get('id')
            
            # Now get issue types for this project
            url = f"{self.base_url}/rest/api/3/issuetype/project"
            params = {"projectId": project_id}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
            response.raise_for_status()
            
            issue_types = response.json()
            logger.info(f"Found {len(issue_types)} issue types for project {project_key}")
            
            return issue_types
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching issue types for project {project_key}: {e}")
            raise Exception(f"Failed to fetch issue types: {str(e)}")
    
    def get_issue_type_id(self, project_key: str, issue_type_name: str) -> Optional[str]:
        """
        Get the issue type ID for a specific issue type name
        
        Args:
            project_key: The Jira project key
            issue_type_name: Name of the issue type (e.g., 'Epic', 'Story', 'Task')
            
        Returns:
            Issue type ID or None if not found
        """
        try:
            issue_types = self.get_project_issue_types(project_key)
            
            for issue_type in issue_types:
                if issue_type.get('name', '').lower() == issue_type_name.lower():
                    return issue_type.get('id')
            
            logger.warning(f"Issue type '{issue_type_name}' not found in project {project_key}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting issue type ID: {e}")
            return None
    
    def create_issue(self, issue_data: Dict) -> Dict:
        """
        Create a new issue in Jira
        
        Args:
            issue_data: Issue data including fields like summary, description, issuetype, etc.
            
        Returns:
            Created issue data with key and id
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue"
            
            logger.info(f"Creating Jira issue: {issue_data.get('fields', {}).get('summary', 'Unknown')}")
            
            response = requests.post(
                url,
                json=issue_data,
                headers=self.headers,
                auth=self.auth,
                timeout=30
            )
            
            if response.status_code == 400:
                error_data = response.json()
                error_messages = error_data.get('errors', {})
                logger.error(f"Jira validation error: {error_messages}")
                raise Exception(f"Invalid issue data: {error_messages}")
            
            response.raise_for_status()
            
            result = response.json()
            logger.info(f"Created Jira issue: {result.get('key')}")
            
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Jira issue: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to create Jira issue: {str(e)}")

    def get_boards(self, project_key: str) -> List[Dict]:
        """
        Fetch Jira boards associated with a project using the Agile REST API.

        Args:
            project_key: The Jira project key (e.g., 'PROJ')

        Returns:
            List of boards with id, name, and type
        """
        try:
            url = f"{self.base_url}/rest/agile/1.0/board"
            params = {"projectKeyOrId": project_key}
            response = requests.get(
                url,
                headers=self.headers,
                auth=self.auth,
                params=params,
                timeout=30
            )
            response.raise_for_status()

            data = response.json()
            boards = data.get("values", [])
            logger.info(f"Found {len(boards)} boards for project {project_key}")

            return [
                {
                    "id": board["id"],
                    "name": board["name"],
                    "type": board.get("type", "unknown"),
                }
                for board in boards
            ]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching boards for project {project_key}: {e}")
            raise Exception(f"Failed to fetch Jira boards: {str(e)}")






