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
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=10)
            
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
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=15)
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
    
    def get_project_issues(self, project_key: str, max_results: int = 100) -> List[Dict]:
        """
        Fetch issues from a specific Jira project
        
        Args:
            project_key: The Jira project key (e.g., 'PROJ')
            max_results: Maximum number of results to return
            
        Returns:
            List of issues with all fields needed by the frontend
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
        ]
        
        jql = f"project = {project_key} ORDER BY created DESC"
        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ",".join(fields)
        }
        
        # Use the new /search/jql endpoint (Atlassian migrated from /search)
        try:
            url = f"{self.base_url}/rest/api/3/search/jql"
            logger.info(f"Fetching Jira issues from: {url} with JQL: {jql}")
            
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=15)
            
            # Handle specific error codes
            if response.status_code == 400:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('errorMessages', ['Invalid JQL query'])[0]
                    logger.error(f"Jira 400 error: {error_msg}")
                    raise Exception(f"Invalid request: {error_msg}")
                except:
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
            logger.info(f"Successfully fetched {len(issues)} issues from project {project_key}")
            return issues
            
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



