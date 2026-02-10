import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class ConfluenceService:
    """Service for interacting with Confluence Cloud API"""
    
    def __init__(self, domain: str, email: str, api_token: str):
        """
        Initialize Confluence service
        
        Args:
            domain: Atlassian domain (e.g., 'mycompany.atlassian.net')
            email: User's email address
            api_token: Atlassian API token
        """
        self.base_url = f"https://{domain}/wiki"
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json"}
    
    def test_connection(self) -> bool:
        """Test if credentials are valid by fetching current user info"""
        try:
            url = f"{self.base_url}/rest/api/user/current"
            response = requests.get(url, headers=self.headers, auth=self.auth, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Confluence connection test failed: {e}")
            return False
    
    def get_spaces(self) -> List[Dict]:
        """
        Fetch all accessible Confluence spaces
        
        Returns:
            List of spaces with key, name, id, and type
        """
        try:
            url = f"{self.base_url}/rest/api/space"
            params = {"limit": 100}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            spaces = data.get("results", [])
            
            # Return simplified space list
            return [
                {
                    "key": space["key"],
                    "name": space["name"],
                    "id": space["id"],
                    "type": space.get("type", "global")
                }
                for space in spaces
            ]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence spaces: {e}")
            raise Exception(f"Failed to fetch Confluence spaces: {str(e)}")
    
    def get_space_pages(self, space_key: str, limit: int = 100) -> List[Dict]:
        """
        Fetch pages from a specific Confluence space
        
        Args:
            space_key: The Confluence space key
            limit: Maximum number of pages to return
            
        Returns:
            List of pages with id, title, and content
        """
        try:
            url = f"{self.base_url}/rest/api/space/{space_key}/content/page"
            params = {"limit": limit}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            return data.get("results", [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence pages: {e}")
            raise Exception(f"Failed to fetch Confluence pages: {str(e)}")
