import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

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
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
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
    
    def get_projects(self) -> List[Dict]:
        """
        Fetch all accessible Jira projects
        
        Returns:
            List of projects with key, name, id, and type
        """
        try:
            url = f"{self.base_url}/rest/api/3/project"
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=30)
            response.raise_for_status()

            projects = response.json()
            
            # Return simplified project list
            return [
                {
                    "key": project["key"],
                    "name": project["name"],
                    "id": project["id"],
                    "type": project.get("projectTypeKey", "software")
                }
                for project in projects
            ]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Jira projects: {e}")
            raise Exception(f"Failed to fetch Jira projects: {str(e)}")
    
    def get_project_issues(self, project_key: str) -> List[Dict]:
        """
        Fetch ALL issues from a specific Jira project using pagination.
        Results are ordered by created date descending (newest first).

        Args:
            project_key: The Jira project key (e.g., 'PROJ')

        Returns:
            List of all issues with all fields needed by the frontend
        """
        # Include all fields that the frontend expects
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
            "customfield_10016",  # Story points (common field ID)
            "parent",  # Parent epic for stories
        ]

        jql = f"project = {project_key} ORDER BY created DESC"
        url = f"{self.base_url}/rest/api/3/search/jql"
        page_size = 100  # Max allowed per request by Jira API
        start_at = 0
        all_issues = []

        logger.info(f"Fetching all Jira issues from: {url} with JQL: {jql}")

        try:
            while True:
                params = {
                    "jql": jql,
                    "maxResults": page_size,
                    "startAt": start_at,
                    "fields": ",".join(fields)
                }

                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)

                # Handle specific error codes
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
                    try:
                        error_body = response.text
                        logger.error(f"Response body: {error_body}")
                    except:
                        pass
                    raise Exception(f"Project '{project_key}' may be archived, deleted, or inaccessible. Please verify in Jira.")

                response.raise_for_status()

                result = response.json()
                issues = result.get('issues', [])
                total = result.get('total', 0)
                all_issues.extend(issues)

                logger.info(f"Fetched {len(all_issues)}/{total} issues from project {project_key}")

                # Check if we've fetched all issues
                if len(all_issues) >= total or len(issues) == 0:
                    break

                start_at += page_size

            logger.info(f"Successfully fetched all {len(all_issues)} issues from project {project_key}")
            return all_issues

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error fetching Jira issues: {e}")
            try:
                logger.error(f"Response status: {response.status_code}, body: {response.text[:500]}")
            except:
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






