"""
BRD from History Lambda Function
Generates BRD directly from conversation history using Bedrock
"""

import json
import logging
import os
import uuid
from typing import List, Dict

import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'Test-DGwqpP7Rvj')
AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'analyst-session')
S3_BUCKET = os.getenv('S3_BUCKET_NAME', 'test-development-bucket-siriusai')
TEMPLATE_S3_KEY = 'templates/Deluxe_BRD_Template_v2+2.docx'
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0')
MAX_TOKENS = 8192
TEMPERATURE = 0.0

# Lazy loading
_bedrock_runtime = None
_agentcore_memory_client = None
_s3_client = None

# BRD Generation from Chat History Prompt
# This prompt instructs Bedrock to generate BRD from conversation history
BRD_FROM_CHAT_PROMPT = """
════════════════════════════════════════════════════════════════
SPECIAL INSTRUCTIONS FOR BRD GENERATION FROM ANALYST CHAT HISTORY
════════════════════════════════════════════════════════════════

CONTEXT:
You are generating a Business Requirements Document (BRD) from a requirements discovery conversation.
The conversation below is NOT a formal meeting transcript - it's a natural dialogue between a Business Analyst and a User.

CRITICAL: You MUST follow the TEMPLATE structure provided below exactly.
All 16 sections from the template MUST be present in the final BRD.

════════════════════════════════════════════════════════════════
LABELING REQUIREMENTS (MANDATORY):
════════════════════════════════════════════════════════════════

You MUST clearly label ALL content in the BRD as one of:

[USER-PROVIDED]
- Information explicitly stated by the USER in the conversation
- Example: "[USER-PROVIDED] System must support 10,000 concurrent users"

[AI ASSUMPTION]
- Information you are inferring based on professional judgment
- Example: "[AI ASSUMPTION] 24/7 availability required (standard for customer-facing chatbots)"

[PARTIALLY SPECIFIED - USER + AI]
- User provided partial information, you are expanding it
- Example: "[PARTIALLY SPECIFIED - USER + AI] User mentioned 'mobile app'; assuming iOS and Android support"

════════════════════════════════════════════════════════════════
CONFIDENCE LEVELS FOR AI ASSUMPTIONS:
════════════════════════════════════════════════════════════════

- HIGH CONFIDENCE: Strongly implied by user
  Example: "[AI ASSUMPTION - HIGH CONFIDENCE] 24/7 availability (user said 'always available')"

- MEDIUM CONFIDENCE: Reasonable inference
  Example: "[AI ASSUMPTION - MEDIUM CONFIDENCE] Mobile-first design (user discussed mobile app)"

- LOW CONFIDENCE: Standard industry practice
  Example: "[AI ASSUMPTION - LOW CONFIDENCE] GDPR compliance (standard for customer data)"

- TO BE CONFIRMED: Needs validation
  Example: "[AI ASSUMPTION - TO BE CONFIRMED] Integration with Salesforce CRM"

════════════════════════════════════════════════════════════════
CREATING COMPLETE BRD SKELETON:
════════════════════════════════════════════════════════════════

RULE: NEVER leave any section empty. Every section MUST have content.

For each section:
1. If user discussed it → Use [USER-PROVIDED]
2. If user partially discussed it → Use [PARTIALLY SPECIFIED - USER + AI]
3. If user didn't discuss it → Use [AI ASSUMPTION] with professional defaults

NEVER write: "Not discussed", "Information not available", "TBD"
ALWAYS provide content with appropriate labels.

════════════════════════════════════════════════════════════════
INSTRUCTIONS:
════════════════════════════════════════════════════════════════

1. READ the entire conversation below
2. IDENTIFY what the USER explicitly said (mark as [USER-PROVIDED])
3. IDENTIFY what the USER partially mentioned (mark as [PARTIALLY SPECIFIED])
4. FILL ALL GAPS with professional assumptions (mark as [AI ASSUMPTION])
5. FOLLOW the template structure exactly (all 16 sections)
6. USE bullet points, tables, and clear formatting
7. BE SPECIFIC and actionable in every section

════════════════════════════════════════════════════════════════
TEMPLATE STRUCTURE TO FOLLOW:
════════════════════════════════════════════════════════════════

{template}

════════════════════════════════════════════════════════════════
CONVERSATION HISTORY:
════════════════════════════════════════════════════════════════

{conversation}

════════════════════════════════════════════════════════════════
NOW GENERATE THE COMPLETE BRD:
════════════════════════════════════════════════════════════════

Return only the completed BRD as plain text following the template structure exactly.
"""


def _get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client('bedrock-runtime', region_name=AWS_REGION)
    return _bedrock_runtime


def _get_agentcore_memory_client():
    global _agentcore_memory_client
    if _agentcore_memory_client is None:
        _agentcore_memory_client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)
    return _agentcore_memory_client


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3', region_name=AWS_REGION)
    return _s3_client


def get_conversation_history(session_id: str, max_messages: int = 99) -> List[Dict]:
    """Get conversation history from AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    try:
        logger.info(f"Fetching conversation history for session: {session_id}")
        response = client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=min(max_messages, 99)
        )
        
        messages = []
        for event in response.get("events", []):
            for payload_item in event.get("payload", []):
                conv_data = payload_item.get("conversational")
                if not conv_data:
                    continue
                
                text_content = conv_data.get("content", {}).get("text")
                if not text_content:
                    continue
                
                role = conv_data.get("role", "assistant").lower()
                messages.append({
                    "role": role,
                    "content": text_content
                })
        
        logger.info(f"Retrieved {len(messages)} messages from history")
        return messages
        
    except Exception as e:
        logger.error(f"Error retrieving history: {e}", exc_info=True)
        return []


def format_conversation(messages: List[Dict]) -> str:
    """Format conversation history as readable text"""
    lines = []
    
    for msg in messages:
        role = "USER" if msg.get("role") == "user" else "ANALYST"
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    
    return "\n\n".join(lines)


def fetch_template_from_s3() -> str:
    """Fetch BRD template from S3 and extract text"""
    s3_client = _get_s3_client()
    
    try:
        logger.info(f"Fetching template from s3://{S3_BUCKET}/{TEMPLATE_S3_KEY}")
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=TEMPLATE_S3_KEY)
        template_bytes = response['Body'].read()
        
        # Extract text from DOCX
        template_text = extract_text_from_docx(template_bytes)
        logger.info(f"Template extracted: {len(template_text)} characters")
        return template_text
        
    except Exception as e:
        logger.error(f"Error fetching template: {e}", exc_info=True)
        raise


def extract_text_from_docx(docx_bytes: bytes) -> str:
    """Extract text from DOCX file"""
    import zipfile
    import xml.etree.ElementTree as ET
    import io
    
    try:
        zip_file = zipfile.ZipFile(io.BytesIO(docx_bytes))
        document_xml = zip_file.read('word/document.xml')
        root = ET.fromstring(document_xml)
        
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        
        paragraphs = []
        for para in root.findall('.//w:p', ns):
            texts = []
            for text_elem in para.findall('.//w:t', ns):
                if text_elem.text:
                    texts.append(text_elem.text)
            if texts:
                paragraphs.append(''.join(texts))
        
        return '\n'.join(paragraphs)
        
    except Exception as e:
        logger.error(f"Failed to extract text from DOCX: {e}", exc_info=True)
        raise


def generate_brd_with_bedrock(template: str, conversation: str) -> str:
    """Generate BRD using Bedrock AI"""
    bedrock = _get_bedrock_runtime()
    
    # Build the full prompt
    prompt = BRD_FROM_CHAT_PROMPT.format(
        template=template,
        conversation=conversation
    )
    
    logger.info(f"Calling Bedrock with prompt length: {len(prompt)} characters")
    logger.info(f"Model: {BEDROCK_MODEL_ID}, Max tokens: {MAX_TOKENS}")
    
    try:
        response = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}]
                }
            ],
            inferenceConfig={
                "maxTokens": MAX_TOKENS,
                "temperature": TEMPERATURE
            }
        )
        
        # Extract generated BRD text
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        
        brd_text = "".join(
            block.get("text", "")
            for block in content_blocks
            if "text" in block
        )
        
        logger.info(f"Generated BRD: {len(brd_text)} characters")
        return brd_text
        
    except Exception as e:
        logger.error(f"Error calling Bedrock: {e}", exc_info=True)
        raise


def convert_brd_to_json(brd_text: str) -> Dict:
    """
    Convert plain-text BRD into structured JSON format for editing.
    
    Parses sections, paragraphs, bullet points, and tables.
    """
    import re
    
    try:
        sections = []
        lines = brd_text.split('\n')
        current_section = None
        current_content = []
        
        for line in lines:
            line = line.strip()
            if not line:
                if current_section and current_content:
                    # Add accumulated content as paragraph
                    current_section['content'].append({
                        "type": "paragraph",
                        "text": '\n'.join(current_content).strip()
                    })
                    current_content = []
                continue
            
            # Look for section headers (1-16 only)
            section_match = re.match(r'^(?:SECTION\s+)?(\d+)\.?\s*(.+)$', line, re.IGNORECASE)
            if section_match:
                section_num = int(section_match.group(1))
                
                # Only treat as section if it's 1-16
                if section_num > 16:
                    if current_section:
                        current_content.append(line)
                    continue
                
                # Save previous section
                if current_section:
                    if current_content:
                        current_section['content'].append({
                            "type": "paragraph",
                            "text": '\n'.join(current_content).strip()
                        })
                    sections.append(current_section)
                
                # Start new section
                title = section_match.group(2).strip()
                current_section = {
                    "section_number": section_num,
                    "title": title,
                    "content": []
                }
                current_content = []
                
            elif line.startswith('##') and len(line) > 3:
                # Alternative section header format
                if current_section:
                    if current_content:
                        current_section['content'].append({
                            "type": "paragraph",
                            "text": '\n'.join(current_content).strip()
                        })
                    sections.append(current_section)
                
                title = line.replace('##', '').strip()
                current_section = {
                    "title": title,
                    "content": []
                }
                current_content = []
                
            elif current_section:
                # Check for bullet points
                if line.startswith('- ') or line.startswith('• ') or line.startswith('* '):
                    bullet_text = re.sub(r'^[-•*]\s+', '', line)
                    if current_section['content'] and current_section['content'][-1].get('type') == 'bullet':
                        current_section['content'][-1]['items'].append(bullet_text)
                    else:
                        # Start new bullet list
                        if current_content:
                            current_section['content'].append({
                                "type": "paragraph",
                                "text": '\n'.join(current_content).strip()
                            })
                            current_content = []
                        current_section['content'].append({
                            "type": "bullet",
                            "items": [bullet_text]
                        })
                    continue
                
                # Check for tables
                if '|' in line:
                    cells = [cell.strip() for cell in line.split('|') if cell.strip()]
                    if cells and len(cells) > 1:
                        if current_section['content'] and current_section['content'][-1].get('type') == 'table':
                            current_section['content'][-1]['rows'].append(cells)
                        else:
                            if current_content:
                                current_section['content'].append({
                                    "type": "paragraph",
                                    "text": '\n'.join(current_content).strip()
                                })
                                current_content = []
                            current_section['content'].append({
                                "type": "table",
                                "rows": [cells]
                            })
                        continue
                
                # Regular content line
                current_content.append(line)
        
        # Don't forget the last section
        if current_section:
            if current_content:
                current_section['content'].append({
                    "type": "paragraph",
                    "text": '\n'.join(current_content).strip()
                })
            sections.append(current_section)
        
        logger.info(f"Converted BRD to JSON: {len(sections)} sections")
        return {"sections": sections}
        
    except Exception as e:
        logger.error(f"Error converting BRD to JSON: {e}", exc_info=True)
        return {"sections": [], "error": str(e)}


def save_brd_to_s3(brd_text: str, brd_id: str) -> Dict[str, str]:
    """Save generated BRD to S3 in both text and JSON formats"""
    s3_client = _get_s3_client()
    
    try:
        # Save as text file
        txt_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=txt_key,
            Body=brd_text.encode('utf-8'),
            ContentType='text/plain'
        )
        txt_location = f"s3://{S3_BUCKET}/{txt_key}"
        logger.info(f"Saved BRD text to {txt_location}")
        
        # Convert to JSON structure
        brd_json = convert_brd_to_json(brd_text)
        
        # Save as JSON file
        json_key = f"brds/{brd_id}/BRD_{brd_id}.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=json_key,
            Body=json.dumps(brd_json, indent=2).encode('utf-8'),
            ContentType='application/json'
        )
        json_location = f"s3://{S3_BUCKET}/{json_key}"
        logger.info(f"Saved BRD JSON to {json_location}")
        
        return {
            "txt": txt_location,
            "json": json_location
        }
        
    except Exception as e:
        logger.error(f"Error saving BRD to S3: {e}", exc_info=True)
        raise


def lambda_handler(event, context):
    """
    Lambda handler for BRD generation from conversation history.
    
    Expected event:
    {
        "session_id": "analyst-session-xxx",
        "brd_id": "optional-brd-id"
    }
    """
    logger.info("=== BRD from History Lambda Started ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Extract inputs
        session_id = event.get('session_id')
        brd_id = event.get('brd_id')
        
        if not session_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required field: session_id'
                })
            }
        
        # Generate BRD ID if not provided
        if not brd_id:
            brd_id = str(uuid.uuid4())
            logger.info(f"Generated new BRD ID: {brd_id}")
        else:
            logger.info(f"Using provided BRD ID: {brd_id}")
        
        # Step 1: Fetch conversation history from AgentCore Memory
        logger.info("Step 1: Fetching conversation history...")
        messages = get_conversation_history(session_id)
        
        if not messages:
            logger.warning("No conversation history found")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'No conversation history found for this session',
                    'message': 'Please have a conversation with the analyst first'
                })
            }
        
        # Step 2: Format conversation
        logger.info("Step 2: Formatting conversation...")
        conversation_text = format_conversation(messages)
        
        # Step 3: Fetch template from S3
        logger.info("Step 3: Fetching template from S3...")
        template_text = fetch_template_from_s3()
        
        # Step 4: Generate BRD using Bedrock
        logger.info("Step 4: Generating BRD with Bedrock...")
        brd_text = generate_brd_with_bedrock(template_text, conversation_text)
        
        # Step 5: Save BRD to S3
        logger.info("Step 5: Saving BRD to S3...")
        s3_locations = save_brd_to_s3(brd_text, brd_id)
        
        logger.info(f"BRD generation completed successfully. BRD ID: {brd_id}")
        
        # Return success
        return {
            'statusCode': 200,
            'body': json.dumps({
                'brd_id': brd_id,
                'message': 'BRD generated successfully from conversation history',
                'status': 'success',
                's3_location_txt': s3_locations['txt'],
                's3_location_json': s3_locations['json']
            })
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Error generating BRD from history'
            })
        }
