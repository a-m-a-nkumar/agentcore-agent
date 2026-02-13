from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import logging
import boto3
import json
import os
import re
from html import unescape
from botocore.config import Config

from auth import verify_azure_token
from db_helper import (
    get_user_atlassian_credentials,
    create_or_update_user,
    get_project
)
from services.confluence_service import ConfluenceService
from services.jira_service import JiraService

router = APIRouter(prefix="/api/jira", tags=["jira"])
logger = logging.getLogger(__name__)

# Bedrock configuration
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
# Timeout for invoke_model (seconds). Large BRDs can take 2–5+ minutes; default 60 often too low.
BEDROCK_READ_TIMEOUT = int(os.getenv("BEDROCK_READ_TIMEOUT", "300"))


# ============================================
# AUTHENTICATION DEPENDENCY
# ============================================

async def get_current_user(token_data: dict = Depends(verify_azure_token)):
    """Get current user from Azure AD token"""
    user_id = token_data.get("oid") or token_data.get("sub")
    email = token_data.get("preferred_username") or token_data.get("email") or token_data.get("upn")
    name = token_data.get("name")
    
    if not user_id or not email:
        raise HTTPException(status_code=401, detail="Invalid token: missing user information")
    
    try:
        user = create_or_update_user(user_id, email, name)
        return user
    except Exception as e:
        logger.error(f"Error creating/updating user: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate user")


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class GenerateJiraItemsRequest(BaseModel):
    confluence_page_id: str = Field(..., description="Confluence page ID")
    project_id: str = Field(..., description="Project ID")


class UserStory(BaseModel):
    story_id: str
    title: str
    description: str
    acceptance_criteria: List[str]
    story_points: int
    priority: str
    mapped_to_requirement: str
    selected: bool = False


class Epic(BaseModel):
    epic_id: str
    title: str
    description: str
    mapped_to_brd_section: str
    user_stories: List[UserStory]


class GenerateJiraItemsResponse(BaseModel):
    epics: List[Epic]
    total_epics: int
    total_stories: int


class CreateJiraItemsRequest(BaseModel):
    project_id: str
    jira_project_key: str
    epics: List[Dict]  # Contains epic_id, create_epic, and selected user_stories


# ============================================
# HELPER FUNCTIONS
# ============================================

def strip_html_tags(html_content: str) -> str:
    """Remove HTML tags and extract plain text from Confluence content"""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', html_content)
    # Unescape HTML entities
    text = unescape(text)
    # Clean up whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _get_bedrock_client():
    """Get Bedrock runtime client with extended read timeout for large BRD generation."""
    config = Config(
        read_timeout=BEDROCK_READ_TIMEOUT,
        connect_timeout=30,
        retries={"max_attempts": 2, "mode": "standard"},
    )
    return boto3.client("bedrock-runtime", region_name=AWS_REGION, config=config)


def convert_to_adf(text: str) -> Dict:
    """
    Convert plain text to Atlassian Document Format (ADF)
    
    Jira Cloud API v3 requires descriptions in ADF format, not plain text.
    
    Args:
        text: Plain text description
        
    Returns:
        ADF document structure
    """
    if not text:
        return {
            "type": "doc",
            "version": 1,
            "content": []
        }
    
    # Split text into paragraphs
    paragraphs = text.split('\n\n')
    
    content = []
    for para in paragraphs:
        if para.strip():
            # Handle bullet points
            if para.strip().startswith('- ') or para.strip().startswith('* '):
                # Create bullet list
                list_items = []
                for line in para.split('\n'):
                    line = line.strip()
                    if line.startswith('- ') or line.startswith('* '):
                        list_items.append({
                            "type": "listItem",
                            "content": [{
                                "type": "paragraph",
                                "content": [{
                                    "type": "text",
                                    "text": line[2:].strip()
                                }]
                            }]
                        })
                
                if list_items:
                    content.append({
                        "type": "bulletList",
                        "content": list_items
                    })
            else:
                # Regular paragraph
                content.append({
                    "type": "paragraph",
                    "content": [{
                        "type": "text",
                        "text": para.strip()
                    }]
                })
    
    return {
        "type": "doc",
        "version": 1,
        "content": content
    }


def generate_epics_and_stories_with_bedrock(confluence_content: str, page_title: str) -> Dict:
    """
    Use Bedrock to generate Epics and User Stories from Confluence content
    
    Args:
        confluence_content: HTML content from Confluence page
        page_title: Title of the Confluence page
        
    Returns:
        Dictionary with epics and user stories
    """
    # Strip HTML and get plain text
    plain_text = strip_html_tags(confluence_content)
    
    # Prepare enhanced prompt for Claude with explicit FR extraction
    prompt = f"""You are a senior Jira expert and agile coach analyzing a Business Requirements Document (BRD) from Confluence.

Page Title: {page_title}

BRD Content:
{plain_text}

CRITICAL MISSION:
You MUST create User Stories for EVERY SINGLE functional requirement in this BRD. Missing even one requirement is unacceptable.

STEP 1: EXTRACT ALL FUNCTIONAL REQUIREMENTS (MANDATORY)
First, create a complete list of ALL functional requirements in the BRD:
- Look for sections titled "Functional Requirements", "Features", "Requirements", etc.
- Find ALL numbered requirements (FR-001, FR-01, FR-1, etc.)
- Include requirements from user stories section if they describe functionality
- List EVERY requirement you find - do not skip any
- If you find 13 requirements, you MUST create stories for all 13
- If you find 23 requirements, you MUST create stories for all 23

STEP 2: VERIFY COMPLETENESS
Before proceeding, count how many functional requirements you found.
This number determines the MINIMUM number of User Stories you must create.
Example: If you found 13 FRs, you need AT LEAST 13 User Stories (ideally 15-20).

STEP 3: GROUP INTO LOGICAL EPICS
Group related functional requirements into Epics:
- User Management & Authentication (registration, login, KYC, verification)
- Payment & Transactions (send, receive, payment methods, wallet)
- Data & Reporting (history, analytics, dashboard)
- Notifications & Alerts (push notifications, email, SMS)
- Integrations (CRM, payment gateways, third-party services)
- Security & Compliance (encryption, KYC, regulations)
- APIs & Backend (REST APIs, webhooks, data sync)
- Mobile/Web Interface (UI components, navigation)
- Support & Help (error handling, customer support, documentation)

STEP 4: CREATE USER STORIES FOR EACH REQUIREMENT
For EVERY functional requirement you identified in Step 1:
- Create 1-2 User Stories
- Format: "As a [specific role], I want [specific goal], so that [clear benefit]"
- Include detailed implementation description
- Add 3-5 SPECIFIC, TESTABLE acceptance criteria
- Assign realistic story points (1-21)
- Set priority based on BRD (High/Medium/Low)
- EXPLICITLY map to the requirement ID (e.g., "FR-001: User registration")

STEP 5: FINAL VERIFICATION
Before outputting JSON, verify:
✓ Every FR from Step 1 has at least one User Story
✓ Total User Stories >= Total Functional Requirements
✓ Each story has 3-5 acceptance criteria
✓ All stories are mapped to specific requirements
✓ No requirement was skipped or forgotten

OUTPUT FORMAT (JSON ONLY - NO EXPLANATIONS):
{{
  "epics": [
    {{
      "epic_id": "temp_epic_1",
      "title": "Descriptive Epic Title",
      "description": "Comprehensive epic description covering the feature area",
      "mapped_to_brd_section": "Comma-separated requirement IDs (e.g., 'FR-001, FR-002, FR-003')",
      "user_stories": [
        {{
          "story_id": "temp_story_1",
          "title": "As a [specific role], I want [specific goal], so that [clear benefit]",
          "description": "Detailed description of implementation requirements",
          "acceptance_criteria": [
            "Specific, measurable criterion 1",
            "Specific, measurable criterion 2",
            "Specific, measurable criterion 3",
            "Specific, measurable criterion 4"
          ],
          "story_points": 5,
          "priority": "High",
          "mapped_to_requirement": "Exact requirement ID and title (e.g., 'FR-001: User registration with email and password')"
        }}
      ]
    }}
  ]
}}

MANDATORY RULES:
1. Create stories for EVERY functional requirement - no exceptions
2. If BRD has N functional requirements, generate AT LEAST N user stories
3. Each story MUST map to a specific requirement ID
4. Do NOT skip requirements because they seem small or obvious
5. Do NOT combine multiple requirements into one story unless explicitly related
6. Output ONLY the JSON object - no explanations, no summaries, no extra text

START YOUR ANALYSIS NOW - BE THOROUGH AND COMPLETE:"""

    try:
        bedrock_client = _get_bedrock_client()
        
        # Prepare request for Claude with increased token limit
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 75000,  # Increased from 8000 to handle comprehensive BRDs
            "temperature": 0.3,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        logger.info(f"Calling Bedrock to generate comprehensive Epics and User Stories...")
        logger.info(f"BRD content length: {len(plain_text)} characters")
        
        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(request_body)
        )
        
        response_body = json.loads(response['body'].read())
        generated_text = response_body['content'][0]['text']
        
        logger.info(f"Bedrock response received, length: {len(generated_text)} characters")
        
        # Parse JSON response
        # Remove markdown code blocks if present
        generated_text = re.sub(r'```json\s*', '', generated_text)
        generated_text = re.sub(r'```\s*$', '', generated_text)
        generated_text = generated_text.strip()
        
        # Extract JSON object - find the first { and matching }
        # This handles cases where AI adds explanatory text before or after the JSON
        try:
            # Find the start of JSON
            json_start = generated_text.find('{')
            if json_start == -1:
                raise ValueError("No JSON object found in response")
            
            # Find the matching closing brace
            brace_count = 0
            json_end = -1
            for i in range(json_start, len(generated_text)):
                if generated_text[i] == '{':
                    brace_count += 1
                elif generated_text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break
            
            if json_end == -1:
                raise ValueError("No matching closing brace found")
            
            # Extract only the JSON part
            json_text = generated_text[json_start:json_end]
            
            logger.info(f"Extracted JSON length: {len(json_text)} characters")
            
            result = json.loads(json_text)
            
        except ValueError as e:
            logger.error(f"JSON extraction error: {e}")
            logger.error(f"Response text (first 2000 chars): {generated_text[:2000]}")
            raise Exception(f"Failed to extract JSON from AI response: {str(e)}")
        
        # Validate structure
        if "epics" not in result:
            raise ValueError("Invalid response: missing 'epics' field")
        
        # Log statistics
        total_stories = sum(len(epic.get('user_stories', [])) for epic in result['epics'])
        logger.info(f"Successfully generated {len(result['epics'])} epics with {total_stories} total user stories")
        
        # Log each epic summary
        for epic in result['epics']:
            logger.info(f"  Epic: {epic.get('title')} - {len(epic.get('user_stories', []))} stories")
        
        return result
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Bedrock response as JSON: {e}")
        logger.error(f"Response text (first 2000 chars): {generated_text[:2000]}")
        raise Exception(f"Failed to parse AI response: {str(e)}")
    except Exception as e:
        logger.error(f"Error calling Bedrock: {e}", exc_info=True)
        raise Exception(f"Failed to generate Jira items: {str(e)}")


# ============================================
# API ENDPOINTS
# ============================================

@router.post("/generate-from-confluence", response_model=GenerateJiraItemsResponse)
async def generate_jira_items_from_confluence(
    request: GenerateJiraItemsRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Generate Epics and User Stories from a Confluence page using AI
    
    Flow:
    1. Fetch Confluence page content
    2. Send to Bedrock LLM for analysis
    3. Generate structured Epics and User Stories
    4. Return for user review/selection
    """
    logger.info(f"Generating Jira items from Confluence page {request.confluence_page_id}")
    
    # 1. Get user's Atlassian credentials
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked. Please link your account first."
        )
    
    # 2. Get project
    project = get_project(request.project_id)
    
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 3. Fetch Confluence page content
    try:
        confluence_service = ConfluenceService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
        
        page_data = confluence_service.get_page_content(request.confluence_page_id)
        
        logger.info(f"Fetched Confluence page: {page_data['title']}")
        
    except Exception as e:
        logger.error(f"Error fetching Confluence page: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch Confluence page: {str(e)}"
        )
    
    # 4. Generate Epics and User Stories using Bedrock
    try:
        result = generate_epics_and_stories_with_bedrock(
            page_data['content'],
            page_data['title']
        )
        
        # Count totals
        total_epics = len(result['epics'])
        total_stories = sum(len(epic['user_stories']) for epic in result['epics'])
        
        logger.info(f"Generated {total_epics} epics and {total_stories} user stories")
        
        return {
            "epics": result['epics'],
            "total_epics": total_epics,
            "total_stories": total_stories
        }
        
    except Exception as e:
        logger.error(f"Error generating Jira items: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate Jira items: {str(e)}"
        )


@router.post("/create-from-generated")
async def create_jira_items(
    request: CreateJiraItemsRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Create selected Epics and User Stories in Jira
    
    Flow:
    1. Create Epics in Jira
    2. Create selected User Stories
    3. Link Stories to Epics
    """
    logger.info(f"Creating Jira items for project {request.project_id}")
    
    # 1. Get user's Atlassian credentials
    credentials = get_user_atlassian_credentials(current_user['id'])
    
    if not credentials or not credentials.get('atlassian_api_token'):
        raise HTTPException(
            status_code=400,
            detail="Atlassian account not linked."
        )
    
    # 2. Initialize Jira service
    try:
        jira_service = JiraService(
            credentials['atlassian_domain'],
            credentials['atlassian_email'],
            credentials['atlassian_api_token']
        )
    except Exception as e:
        logger.error(f"Error initializing Jira service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to connect to Jira: {str(e)}")
    
    created_epics = []
    created_stories = []
    failed = []
    
    # 3. Get issue type IDs for Epic and Story
    try:
        epic_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Epic")
        story_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Story")
        
        if not epic_type_id:
            # Try alternative names
            epic_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Epic")
            if not epic_type_id:
                raise Exception("Epic issue type not found in project. Please ensure your Jira project supports Epics.")
        
        if not story_type_id:
            # Try alternative names (Task, User Story, etc.)
            story_type_id = jira_service.get_issue_type_id(request.jira_project_key, "Task")
            if not story_type_id:
                story_type_id = jira_service.get_issue_type_id(request.jira_project_key, "User Story")
            if not story_type_id:
                raise Exception("Story/Task issue type not found in project.")
        
        logger.info(f"Using issue type IDs - Epic: {epic_type_id}, Story: {story_type_id}")
        
    except Exception as e:
        logger.error(f"Error fetching issue types: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch issue types: {str(e)}")
    
    # 4. Create Epics and User Stories
    for epic_data in request.epics:
        epic_id = epic_data.get('epic_id')
        
        try:
            # Create Epic if requested
            if epic_data.get('create_epic', True):
                epic_payload = {
                    "fields": {
                        "project": {"key": request.jira_project_key},
                        "summary": epic_data['title'],
                        "description": convert_to_adf(epic_data.get('description', '')),  # Convert to ADF
                        "issuetype": {"id": epic_type_id}  # Use ID instead of name
                    }
                }
                
                # Create epic in Jira
                jira_epic = jira_service.create_issue(epic_payload)
                jira_epic_key = jira_epic.get('key')
                
                created_epics.append({
                    "temp_id": epic_id,
                    "jira_key": jira_epic_key,
                    "title": epic_data['title']
                })
                
                logger.info(f"Created Epic: {jira_epic_key}")
                
                # Create selected User Stories
                for story in epic_data.get('user_stories', []):
                    if story.get('selected', False):
                        try:
                            # Build description with acceptance criteria
                            description_text = story.get('description', '')
                            if story.get('acceptance_criteria'):
                                description_text += "\n\nAcceptance Criteria:\n"
                                for criterion in story['acceptance_criteria']:
                                    description_text += f"- {criterion}\n"
                            
                            story_payload = {
                                "fields": {
                                    "project": {"key": request.jira_project_key},
                                    "summary": story['title'],
                                    "description": convert_to_adf(description_text),  # Convert to ADF
                                    "issuetype": {"id": story_type_id},  # Use ID instead of name
                                    "parent": {"key": jira_epic_key}  # Link to Epic
                                }
                            }
                            
                            # Add priority if available (this is usually a standard field)
                            if 'priority' in story:
                                try:
                                    story_payload['fields']['priority'] = {"name": story['priority']}
                                except:
                                    logger.warning(f"Could not set priority for story {story['story_id']}")
                            
                            # Note: Story points (customfield_10016) is skipped as it may not be configured
                            # Users can manually add story points in Jira after creation
                            
                            jira_story = jira_service.create_issue(story_payload)
                            jira_story_key = jira_story.get('key')
                            
                            created_stories.append({
                                "temp_id": story['story_id'],
                                "jira_key": jira_story_key,
                                "title": story['title'],
                                "epic": jira_epic_key
                            })
                            
                            logger.info(f"Created Story: {jira_story_key} under Epic {jira_epic_key}")
                            
                        except Exception as e:
                            logger.error(f"Failed to create story {story['story_id']}: {e}")
                            failed.append({
                                "item_id": story['story_id'],
                                "type": "story",
                                "error": str(e)
                            })
        
        except Exception as e:
            logger.error(f"Failed to create epic {epic_id}: {e}")
            failed.append({
                "item_id": epic_id,
                "type": "epic",
                "error": str(e)
            })
    
    return {
        "status": "success",
        "created_epics": created_epics,
        "created_stories": created_stories,
        "failed": failed,
        "summary": {
            "total_epics_created": len(created_epics),
            "total_stories_created": len(created_stories),
            "total_failed": len(failed)
        }
    }
