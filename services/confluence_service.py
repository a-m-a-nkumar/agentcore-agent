import requests
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Optional
import logging
import html

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
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
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
            all_spaces = []
            start = 0
            limit = 100

            # Paginate through all results
            while True:
                params = {"limit": limit, "start": start}
                response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=15)
                response.raise_for_status()

                data = response.json()
                batch = data.get("results", [])
                all_spaces.extend(batch)

                # Stop if we've received fewer results than the limit (last page)
                if len(batch) < limit:
                    break
                start += limit

            # Filter out personal spaces (keys starting with ~) — keep all real team spaces
            return [
                {
                    "key": space["key"],
                    "name": space["name"],
                    "id": space["id"],
                    "type": space.get("type", "global")
                }
                for space in all_spaces
                if not space["key"].startswith("~")
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

    def get_content_pages(self, space_key: str, limit: int = 100) -> List[Dict]:
        """
        Fetch ALL pages from a space using Content API with pagination.
        GET /rest/api/content?spaceKey=X&type=page&start=N&limit=N

        Confluence Cloud may cap per-request limit at 25, so we paginate
        until all pages are retrieved.
        """
        try:
            url = f"{self.base_url}/rest/api/content"
            all_pages = []
            start = 0
            page_size = min(limit, 100)  # per-request batch size

            while True:
                params = {
                    "spaceKey": space_key,
                    "type": "page",
                    "limit": page_size,
                    "start": start,
                }
                response = requests.get(
                    url, headers=self.headers, auth=self.auth, params=params, timeout=15
                )
                response.raise_for_status()
                data = response.json()
                batch = data.get("results", [])
                all_pages.extend(batch)

                # Stop if we got fewer than requested (last page) or hit caller limit
                if len(batch) < page_size or len(all_pages) >= limit:
                    break
                start += len(batch)

            logger.info(f"Fetched {len(all_pages)} pages from Confluence space '{space_key}'")
            return all_pages[:limit]
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence content pages: {e}")
            raise Exception(f"Failed to fetch Confluence pages: {str(e)}")

    def get_content_page_by_id(self, page_id: str, expand: str = "body.storage,version,ancestors") -> Dict:
        """
        Get a single page by ID with optional expand (same shape as Confluence REST API).
        """
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            params = {"expand": expand}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence page {page_id}: {e}")
            raise Exception(f"Failed to fetch Confluence page: {str(e)}")

    def convert_brd_to_confluence_storage(self, brd_data: Dict) -> str:
        """
        Convert BRD JSON structure to Confluence storage format (HTML-like)
        
        Args:
            brd_data: BRD data with sections structure
            
        Returns:
            Confluence storage format HTML string
        """
        html_parts = []
        
        sections = brd_data.get("sections", [])
        
        for section in sections:
            title = html.escape(section.get("title", ""))
            # Add section title as h2
            html_parts.append(f"<h2>{title}</h2>")
            
            # Process content blocks
            for block in section.get("content", []):
                block_type = block.get("type")
                
                if block_type == "paragraph":
                    text = html.escape(block.get("text", ""))
                    # Replace newlines with <br/> for proper formatting
                    text = text.replace("\n", "<br/>")
                    html_parts.append(f"<p>{text}</p>")
                
                elif block_type == "bullet":
                    items = block.get("items", [])
                    if items:
                        html_parts.append("<ul>")
                        for item in items:
                            escaped_item = html.escape(str(item))
                            html_parts.append(f"<li>{escaped_item}</li>")
                        html_parts.append("</ul>")
                
                elif block_type == "table":
                    rows = block.get("rows", [])
                    if rows:
                        html_parts.append("<table><tbody>")
                        for row_idx, row in enumerate(rows):
                            html_parts.append("<tr>")
                            # First row is header
                            tag = "th" if row_idx == 0 else "td"
                            for cell in row:
                                escaped_cell = html.escape(str(cell))
                                html_parts.append(f"<{tag}>{escaped_cell}</{tag}>")
                            html_parts.append("</tr>")
                        html_parts.append("</tbody></table>")
        
        return "".join(html_parts)
    
    def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: Optional[str] = None
    ) -> Dict:
        """
        Create a new Confluence page
        
        Args:
            space_key: Confluence space key
            title: Page title
            content: Page content in Confluence storage format (HTML)
            parent_id: Optional parent page ID
            
        Returns:
            Created page data with id, title, and web URL
        """
        try:
            url = f"{self.base_url}/rest/api/content"
            
            payload = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {
                    "storage": {
                        "value": content,
                        "representation": "storage"
                    }
                }
            }
            
            # Add parent if specified
            if parent_id:
                payload["ancestors"] = [{"id": parent_id}]
            
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                auth=self.auth,
                timeout=30
            )
            response.raise_for_status()
            
            page_data = response.json()
            
            # Extract useful information
            result = {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "type": page_data.get("type"),
                "status": page_data.get("status"),
                "web_url": f"{self.base_url}{page_data.get('_links', {}).get('webui', '')}"
            }
            
            logger.info(f"Created Confluence page: {result['title']} (ID: {result['id']})")
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Confluence page: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to create Confluence page: {str(e)}")
    
    def find_page_by_title(self, space_key: str, title: str) -> Optional[Dict]:
        """Find a page in a space by exact title. Returns None if not found."""
        try:
            url = f"{self.base_url}/rest/api/content"
            params = {"spaceKey": space_key, "title": title, "type": "page", "expand": "version"}
            response = requests.get(url, headers=self.headers, auth=self.auth, params=params, timeout=15)
            response.raise_for_status()
            results = response.json().get("results", [])
            return results[0] if results else None
        except requests.exceptions.RequestException as e:
            logger.error(f"Error searching Confluence page by title: {e}")
            return None

    def update_page(self, page_id: str, title: str, content: str, current_version: int) -> Dict:
        """Update an existing Confluence page — saves as a new version."""
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            payload = {
                "type": "page",
                "title": title,
                "version": {"number": current_version + 1},
                "body": {
                    "storage": {
                        "value": content,
                        "representation": "storage"
                    }
                }
            }
            response = requests.put(url, json=payload, headers=self.headers, auth=self.auth, timeout=30)
            response.raise_for_status()
            page_data = response.json()
            return {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "web_url": f"{self.base_url}{page_data.get('_links', {}).get('webui', '')}"
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Error updating Confluence page: {e}")
            raise Exception(f"Failed to update Confluence page: {str(e)}")

    def get_page_content(self, page_id: str) -> Dict:
        """
        Get full content of a Confluence page by ID
        
        Args:
            page_id: Confluence page ID
            
        Returns:
            Page data with title, content, and metadata
        """
        try:
            url = f"{self.base_url}/rest/api/content/{page_id}"
            params = {
                "expand": "body.storage,version"
            }
            
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                auth=self.auth,
                timeout=30
            )
            response.raise_for_status()
            
            page_data = response.json()
            
            # Extract useful information
            result = {
                "id": page_data.get("id"),
                "title": page_data.get("title"),
                "type": page_data.get("type"),
                "content": page_data.get("body", {}).get("storage", {}).get("value", ""),
                "version": page_data.get("version", {}).get("number", 1)
            }
            
            logger.info(f"Fetched Confluence page: {result['title']} (ID: {result['id']})")
            return result
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Confluence page content: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to fetch Confluence page: {str(e)}")
