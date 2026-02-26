"""
Lambda function for chat-based BRD editing with AgentCore Memory

Features:
1. Create/manage chat sessions using AgentCore Memory
2. Update BRD sections based on chat messages
3. Maintain conversation history in AgentCore Memory
4. Stream responses from Claude
"""

import json
import os
import re
import logging
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "4000"))
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
AGENTCORE_GATEWAY_ID = os.getenv("AGENTCORE_GATEWAY_ID", "testgatewayfbdd062d-e2eo4q0y09")
AGENTCORE_MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID", "Test-DGwqpP7Rvj")
AGENTCORE_ACTOR_ID = os.getenv("AGENTCORE_ACTOR_ID", "brd-session")

# Bedrock client (cached)
_bedrock_client = None

def _get_bedrock_client():
    """Get or create Bedrock runtime client"""
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
            config=Config(read_timeout=300, connect_timeout=60)
        )
    return _bedrock_client

def _get_s3_client():
    """Get S3 client"""
    return boto3.client("s3", region_name=BEDROCK_REGION)

def _get_agentcore_memory_client():
    """Get AgentCore Memory client"""
    return boto3.client("bedrock-agentcore", region_name=BEDROCK_REGION)

# -------------------------
# AgentCore Memory Operations
# -------------------------

ALLOWED_METADATA_PATTERN = re.compile(r"[A-Za-z0-9\s._:/=+@-]")


def _sanitize_metadata_text(value: str, max_len: int = 250) -> str:
    """Filter characters to match AgentCore metadata regex."""
    if not value:
        return ""
    filtered_chars = [
        ch if ALLOWED_METADATA_PATTERN.match(ch) else " "
        for ch in value[: max_len * 2]  # allow buffer before truncation
    ]
    sanitized = "".join(filtered_chars)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized[:max_len]


def _build_metadata(brd_id: str, template: str, transcript: str) -> Dict[str, Dict[str, str]]:
    """Create metadata map with sanitized values (<=256 chars)."""

    return {
        "brdId": {"stringValue": _sanitize_metadata_text(brd_id, max_len=64)},
        "templateSnippet": {"stringValue": _sanitize_metadata_text(template)},
        "transcriptSnippet": {"stringValue": _sanitize_metadata_text(transcript)},
    }


def _build_conversational_payload(role: str, text: str) -> List[Dict]:
    """Return AgentCore-compliant conversational payload list."""
    # Ensure text is not empty - AgentCore requires min length of 1
    if not text or not text.strip():
        # Use a default message based on role to satisfy min length requirement
        if role == "user":
            normalized = "User message"
        elif role == "assistant":
            normalized = "Assistant response"
        else:
            normalized = "System message"
    else:
        normalized = (text or "").strip()[:9000]
    
    role_map = {
        "user": "USER",
        "assistant": "ASSISTANT",
        "tool": "TOOL",
        "system": "OTHER",
    }
    agentcore_role = role_map.get((role or "").lower(), "OTHER")
    return [{
        "conversational": {
            "role": agentcore_role,
            "content": {
                "text": normalized
            }
        }
    }]


def create_memory_session(brd_id: str, template: str, transcript: str) -> Dict:
    """Create a new AgentCore Memory session for BRD editing"""
    import uuid
    from datetime import datetime

    client = _get_agentcore_memory_client()
    session_id = f"brd-session-{brd_id}"
    metadata = _build_metadata(brd_id, template, transcript)
    system_message = (
        f"Starting BRD editing session for BRD ID: {brd_id}. "
        "Template and transcript snippets are attached in metadata."
    )

    try:
        params = {
            "memoryId": AGENTCORE_MEMORY_ID,
            "actorId": AGENTCORE_ACTOR_ID,
            "sessionId": session_id,
            "eventTimestamp": datetime.utcnow(),
            "payload": _build_conversational_payload("system", system_message),
            "metadata": metadata,
            "clientToken": str(uuid.uuid4()),
        }

        response = client.create_event(**params)
        event_id = response["event"]["eventId"]

        logger.info(f"Created AgentCore Memory session: {session_id}, event: {event_id}")
        return {
            "session_id": session_id,
            "brd_id": brd_id
        }
    except Exception as e:
        logger.error(f"Failed to create memory session: {e}")
        raise RuntimeError(f"Memory session creation failed: {e}")

def get_memory_session(session_id: str) -> Dict:
    """Retrieve AgentCore Memory session"""
    client = _get_agentcore_memory_client()

    try:
        response = client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=1
        )
        return response
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.warning(f"Session not found: {session_id}")
            return None
        raise

def add_message_to_memory(session_id: str, role: str, content: str, actor_id: str = AGENTCORE_ACTOR_ID) -> Dict:
    """Add a message to AgentCore Memory session"""
    import uuid
    from datetime import datetime

    client = _get_agentcore_memory_client()

    try:
        params = {
            "memoryId": AGENTCORE_MEMORY_ID,
            "actorId": actor_id,
            "sessionId": session_id,
            "eventTimestamp": datetime.utcnow(),
            "payload": _build_conversational_payload(role, content),
            "clientToken": str(uuid.uuid4())
        }

        response = client.create_event(**params)
        logger.info(f"Added {role} message to session {session_id}")
        return response
    except Exception as e:
        logger.error(f"Failed to add message to memory: {e}")
        raise

def get_session_history(session_id: str, max_messages: int = 50) -> List[Dict]:
    """Retrieve conversation history from AgentCore Memory"""
    client = _get_agentcore_memory_client()

    try:
        response = client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=max_messages
        )

        # Extract messages from events
        events = response.get("events", [])
        messages = []
        for event in events:
            payload_list = event.get("payload", [])
            for payload_item in payload_list:
                conv_data = payload_item.get("conversational")
                if not conv_data:
                    continue
                text_content = conv_data.get("content", {}).get("text")
                if not text_content:
                    continue
                # Skip the auto-generated system events when presenting history to Claude
                if text_content.startswith("Starting BRD editing session") or text_content == "Session closed by user.":
                    continue
                messages.append({
                    "role": conv_data.get("role", "assistant"),
                    "content": [{"type": "text", "text": text_content}]
                })

        return messages
    except Exception as e:
        logger.error(f"Failed to get session history: {e}")
        return []

def delete_memory_session(session_id: str) -> bool:
    """Delete AgentCore Memory session (mark as ended)"""
    import uuid
    from datetime import datetime

    client = _get_agentcore_memory_client()

    try:
        params = {
            "memoryId": AGENTCORE_MEMORY_ID,
            "actorId": AGENTCORE_ACTOR_ID,
            "sessionId": session_id,
            "eventTimestamp": datetime.utcnow(),
            "payload": _build_conversational_payload("system", "Session closed by user."),
            "clientToken": str(uuid.uuid4())
        }

        client.create_event(**params)
        logger.info(f"Ended session {session_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to end session: {e}")
        return False

# -------------------------
# BRD Storage (S3 + Memory)
# -------------------------

def get_brd_from_s3(brd_id: str) -> Optional[Dict]:
    """Load BRD JSON from S3. If missing, attempt to backfill from text."""
    s3_client = _get_s3_client()
    key = f"brds/{brd_id}/brd_structure.json"

    try:
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        data = json.loads(response["Body"].read().decode("utf-8"))
        logger.info(f"Loaded BRD structure from S3: {key}")
        return data
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            logger.warning(f"BRD structure not found in S3: {key}. Attempting to reconstruct from text.")
            return backfill_brd_structure(brd_id)
        raise

def save_brd_to_s3(brd_id: str, brd_data: Dict) -> str:
    """Save BRD JSON to S3"""
    s3_client = _get_s3_client()
    key = f"brds/{brd_id}/brd_structure.json"

    try:
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=json.dumps(brd_data, indent=2, ensure_ascii=False),
            ContentType="application/json"
        )
        logger.info(f"Saved BRD structure to S3: {key}")
        return key
    except Exception as e:
        logger.error(f"Failed to save BRD to S3: {e}")
        raise


def render_brd_to_text(brd_data: Dict) -> str:
    """Render structured BRD JSON into readable plain text."""
    sections = brd_data.get("sections", [])
    lines: List[str] = []
    lines.append("Business Requirements Document (BRD)")
    lines.append("")

    # Check if first section is document title
    has_doc_title = False
    start_idx = 0
    if sections:
        first_title = sections[0].get("title", "").lower()
        if ("ai-powered" in first_title or "brd" in first_title or 
            (len(first_title) < 30 and not re.match(r'^\d+\.', first_title))):
            has_doc_title = True
            start_idx = 1
            # Add document title without number prefix
            lines.append(sections[0].get("title", ""))
        lines.append("")

    # Render actual sections (skip document title if present)
    section_counter = 1
    for idx in range(start_idx, len(sections)):
        section = sections[idx]
        title = section.get("title", f"Section {section_counter}")
        
        # Remove any existing number prefix from title to avoid double numbering
        title_clean = re.sub(r'^\d+\.\s*', '', title).strip()
        
        # Add section number prefix
        lines.append(f"{section_counter}. {title_clean}")
        lines.append("")
        section_counter += 1

        for block in section.get("content", []):
            block_type = block.get("type")
            if block_type == "paragraph":
                lines.append(block.get("text", "").strip())
                lines.append("")
            elif block_type == "bullet":
                for item in block.get("items", []):
                    lines.append(f"- {item}")
                lines.append("")
            elif block_type == "table":
                rows = block.get("rows", [])
                if rows:
                    header = rows[0]
                    header_line = " | ".join(str(col) for col in header)
                    lines.append(header_line)
                    lines.append("-" * len(header_line))
                    for row in rows[1:]:
                        lines.append(" | ".join(str(col) for col in row))
                    lines.append("")
    rendered = "\n".join(line.rstrip() for line in lines).rstrip() + "\n"
    return rendered


def save_brd_text_to_s3(brd_id: str, brd_data: Dict) -> str:
    """Save rendered BRD text file to S3 for download."""
    s3_client = _get_s3_client()
    key = f"brds/{brd_id}/BRD_{brd_id}.txt"
    body = render_brd_to_text(brd_data)

    try:
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="text/plain"
        )
        logger.info(f"Saved BRD text to S3: {key}")
        return key
    except Exception as e:
        logger.error(f"Failed to save BRD text to S3: {e}")
        raise


def get_brd_text_from_s3(brd_id: str) -> Optional[str]:
    """Load BRD plain text from S3"""
    s3_client = _get_s3_client()
    key = f"brds/{brd_id}/BRD_{brd_id}.txt"

    try:
        response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        text = response["Body"].read().decode("utf-8", errors="replace")
        logger.info(f"Loaded BRD text from S3: {key}")
        return text
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            logger.error(f"BRD text not found in S3: {key}")
            return None
        raise


def create_minimal_structure_from_text(brd_text: str) -> Optional[Dict]:
    """
    Create a minimal BRD structure from text when JSON reconstruction fails.
    This is a fallback that does simple text parsing without AI.
    """
    try:
        sections = []
        lines = brd_text.split('\n')
        current_section = None
        current_content = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Look for section headers (numbered sections like "1. Title" or "## Title")
            section_match = re.match(r'^(\d+)\.\s*(.+)$', line)
            if section_match or (line.startswith('##') and len(line) > 3):
                # Save previous section
                if current_section:
                    if current_content:
                        current_section['content'].append({
                            "type": "paragraph",
                            "text": '\n'.join(current_content).strip()
                        })
                    sections.append(current_section)
                
                # Start new section
                if section_match:
                    title = section_match.group(2).strip()
                else:
                    title = line.replace('##', '').strip()
                
                current_section = {
                    "title": title,
                    "content": []
                }
                current_content = []
            elif current_section:
                # Add to current section content
                current_content.append(line)
        
        # Don't forget the last section
        if current_section:
            if current_content:
                current_section['content'].append({
                    "type": "paragraph",
                    "text": '\n'.join(current_content).strip()
                })
            sections.append(current_section)
        
        if sections:
            return {"sections": sections}
        return None
    except Exception as e:
        logger.error(f"Failed to create minimal structure: {e}")
        return None


def backfill_brd_structure(brd_id: str) -> Optional[Dict]:
    """Generate structured BRD JSON from the plain-text BRD if structure is missing."""
    brd_text = get_brd_text_from_s3(brd_id)
    if not brd_text:
        logger.error("Cannot backfill BRD structure because BRD text is missing.")
        return None

    try:
        structured = convert_brd_text_to_json(brd_text)
        if not structured or "sections" not in structured:
            logger.error("Generated BRD structure is invalid or missing 'sections'.")
            return None

        # Persist for future calls
        save_brd_to_s3(brd_id, structured)
        try:
            save_brd_text_to_s3(brd_id, structured)
        except Exception as text_err:
            logger.warning(f"Failed to refresh BRD text after backfill: {text_err}")
        logger.info("Backfilled BRD structure and saved to S3.")
        return structured
    except Exception as e:
        logger.error(f"Failed to backfill BRD structure: {e}")
        return None


def convert_brd_text_to_json(brd_text: str) -> Dict:
    """Convert plain-text BRD into structured JSON using Bedrock."""
    # Limit prompt size to avoid huge payloads
    max_chars = int(os.getenv("BRD_STRUCTURE_MAX_CHARS", "20000"))
    truncated_text = brd_text[:max_chars]

    prompt = f"""
You are a JSON converter. Convert the following Business Requirements Document (BRD) text into VALID JSON.

CRITICAL REQUIREMENTS:
1. Output ONLY valid JSON - no markdown, no code blocks, no explanations
2. Every opening brace {{ must have a closing brace }}
3. Every opening bracket [ must have a closing bracket ]
4. All strings must be properly quoted with double quotes
5. Use commas correctly - no trailing commas before closing brackets/braces
6. Escape special characters in strings (quotes, newlines, etc.)

Required JSON schema:
{{
  "sections": [
    {{
      "title": "Section Title",
      "content": [
        {{ "type": "paragraph", "text": "text content" }},
        {{ "type": "bullet", "items": ["item1", "item2"] }},
        {{ "type": "table", "rows": [["header1","header2"],["row1col1","row1col2"]] }}
      ]
    }}
  ]
}}

Rules:
- Preserve section order from the BRD
- Break content into paragraphs, bullets, and tables appropriately
- Ensure JSON is valid and can be parsed by json.loads()
- If content is too long, truncate it rather than breaking JSON syntax

BRD Text to convert:
{truncated_text}

Output ONLY the JSON object, nothing else:
""".strip()

    raw_response = invoke_claude_for_chat(prompt, [])

    # Try multiple strategies to extract JSON
    structured = None
    json_str = None
    
    # Strategy 1: Look for JSON wrapped in markdown code blocks
    code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
    if code_block_match:
        json_str = code_block_match.group(1)
        logger.info("Found JSON in markdown code block")
    
    # Strategy 2: Look for JSON object starting with { and ending with }
    if not json_str:
        # Find the first { and try to match balanced braces
        brace_start = raw_response.find('{')
        if brace_start != -1:
            brace_count = 0
            brace_end = brace_start
            for i in range(brace_start, len(raw_response)):
                if raw_response[i] == '{':
                    brace_count += 1
                elif raw_response[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        brace_end = i
                        break
            if brace_count == 0:
                json_str = raw_response[brace_start:brace_end + 1]
                logger.info(f"Extracted JSON using balanced braces (length: {len(json_str)})")
    
    # Strategy 3: Fallback to regex (less reliable but better than nothing)
    if not json_str:
        json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            logger.info("Extracted JSON using regex fallback")
    
    if not json_str:
        logger.error(f"Could not find JSON in Claude response. Response preview: {raw_response[:500]}")
        raise RuntimeError("Claude did not return JSON for BRD structure.")

    # Try to parse JSON with multiple strategies
    try:
        structured = json.loads(json_str)
        logger.info("Successfully parsed JSON on first attempt")
    except json.JSONDecodeError as e:
        logger.warning(f"First JSON parse attempt failed: {e}. Trying decoder...")
        try:
            # Try using JSONDecoder to handle trailing text
            decoder = json.JSONDecoder()
            structured, _ = decoder.raw_decode(json_str)
            logger.info("Successfully parsed JSON using decoder")
        except json.JSONDecodeError as e2:
            logger.warning(f"Decoder also failed: {e2}. Trying to fix common issues...")
            # Try to fix common JSON issues
            try:
                # Fix 1: Remove trailing commas before closing braces/brackets
                fixed_json = re.sub(r',\s*([}\]])', r'\1', json_str)
                
                # Fix 2: Fix unclosed strings (add closing quote if missing)
                # This is tricky, so we'll try a different approach
                
                # Fix 3: Try to find and extract the largest valid JSON object
                # Count braces to find where JSON might be truncated
                open_braces = fixed_json.count('{')
                close_braces = fixed_json.count('}')
                if open_braces > close_braces:
                    # Missing closing braces - try to add them
                    missing = open_braces - close_braces
                    # Find the last opening brace and try to close properly
                    last_open = fixed_json.rfind('{')
                    if last_open != -1:
                        # Try to intelligently close the JSON
                        # This is a heuristic - look for incomplete structures
                        lines = fixed_json.split('\n')
                        # Try to find where the structure might be incomplete
                        # and add proper closing
                        for i in range(len(lines) - 1, -1, -1):
                            line = lines[i].strip()
                            if line and not line.endswith(('}', ']', ',')):
                                # Might be incomplete
                                break
                        # Add missing closing braces
                        fixed_json = fixed_json + '}' * missing
                        # Also check brackets
                        open_brackets = fixed_json.count('[')
                        close_brackets = fixed_json.count(']')
                        if open_brackets > close_brackets:
                            fixed_json = fixed_json + ']' * (open_brackets - close_brackets)
                
                structured = json.loads(fixed_json)
                logger.info("Successfully parsed JSON after fixing common issues")
            except json.JSONDecodeError as e3:
                logger.error(f"All JSON parsing attempts failed. Error: {e3}")
                logger.error(f"JSON string length: {len(json_str)}, preview: {json_str[:500]}")
                logger.error(f"Error location: line {e3.lineno if hasattr(e3, 'lineno') else 'unknown'}, column {e3.colno if hasattr(e3, 'colno') else 'unknown'}")
                
                # Try to extract and repair JSON using a more sophisticated approach
                try:
                    # Strategy: Find the error location and try to fix it
                    error_pos = e3.pos if hasattr(e3, 'pos') else len(json_str) // 2
                    
                    # Try to extract valid JSON up to the error point
                    # Look backwards from error position to find a valid closing point
                    if error_pos < len(json_str):
                        # Try to find the last complete structure before the error
                        # Look for patterns like: }, ] that indicate complete structures
                        search_start = max(0, error_pos - 1000)  # Look back 1000 chars
                        search_end = min(len(json_str), error_pos + 100)
                        
                        # Try to find a safe cut point
                        safe_cut = None
                        for i in range(error_pos, search_start, -1):
                            if json_str[i] in ['}', ']']:
                                # Check if this might be a safe cut point
                                test_json = json_str[:i+1]
                                # Count braces to see if it's balanced
                                if test_json.count('{') == test_json.count('}') and \
                                   test_json.count('[') == test_json.count(']'):
                                    safe_cut = i + 1
                                    break
                        
                        if safe_cut:
                            truncated_json = json_str[:safe_cut]
                            # Try to close it properly
                            open_braces = truncated_json.count('{') - truncated_json.count('}')
                            open_brackets = truncated_json.count('[') - truncated_json.count(']')
                            
                            # Close any open structures
                            if open_brackets > 0:
                                truncated_json += ']' * open_brackets
                            if open_braces > 0:
                                truncated_json += '}' * open_braces
                            
                            try:
                                structured = json.loads(truncated_json)
                                logger.info(f"Successfully parsed truncated JSON (cut at position {safe_cut})")
                            except:
                                # Last resort: try to manually construct minimal valid JSON
                                logger.warning("Attempting to create minimal valid JSON structure")
                                # Extract what we can and create a basic structure
                                raise RuntimeError(f"JSON too malformed to repair. Error at position {error_pos}: {str(e3)}")
                        else:
                            raise RuntimeError(f"Could not find safe cut point in malformed JSON. Error: {e3}")
                    else:
                        raise RuntimeError(f"Invalid error position. Error: {e3}")
                except Exception as e4:
                    logger.error(f"Final JSON parsing attempt failed: {e4}")
                    # Log more details about the JSON for debugging
                    if error_pos < len(json_str):
                        context_start = max(0, error_pos - 200)
                        context_end = min(len(json_str), error_pos + 200)
                        logger.error(f"JSON context around error (chars {context_start}-{context_end}): {json_str[context_start:context_end]}")
                    raise RuntimeError(f"Failed to parse JSON from Claude response after all attempts. Last error: {e4}. Error position: {error_pos if 'error_pos' in locals() else 'unknown'}")

    if not structured or "sections" not in structured or not isinstance(structured["sections"], list):
        logger.error(f"Parsed JSON is invalid or missing 'sections' array. Keys: {list(structured.keys()) if structured else 'None'}")
        raise RuntimeError("Structured BRD JSON missing 'sections' array.")

    logger.info(f"Successfully converted BRD text to JSON with {len(structured.get('sections', []))} sections")
    return structured

# -------------------------
# Claude Invocation
# -------------------------

def _render_history_as_text(conversation_history: List[Dict]) -> str:
    """Convert structured chat history into plain text transcript."""
    if not conversation_history:
        return ""

    role_map = {
        "user": "User",
        "assistant": "Assistant",
        "system": "System",
        "tool": "Tool",
        "OTHER": "System",
        "USER": "User",
        "ASSISTANT": "Assistant",
        "TOOL": "Tool",
    }

    lines: List[str] = []
    for message in conversation_history[-10:]:
        role = role_map.get(message.get("role", "").lower(), "User")
        content_blocks = message.get("content", [])
        text_parts = []
        if isinstance(content_blocks, list):
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
        elif isinstance(content_blocks, dict):
            text_parts.append(content_blocks.get("text", ""))
        elif isinstance(content_blocks, str):
            text_parts.append(content_blocks)

        text = " ".join(part.strip() for part in text_parts if part).strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def invoke_claude_for_chat(prompt: str, conversation_history: List[Dict] = None) -> str:
    """
    Invoke Bedrock model with conversation context.

    Supports Anthropic Claude, Amazon Titan, and Meta Llama chat-style models.
    
    IMPORTANT: For converse() API, we use a single user message with history as text
    to avoid toolUse/toolResult validation errors.
    """
    client = _get_bedrock_client()
    model_id = BEDROCK_MODEL_ID or ""
    model_lower = model_id.lower()

    # Convert history to plain text (safest approach - avoids toolUse/toolResult issues)
    history_text = _render_history_as_text(conversation_history or [])
    
    # Build a single comprehensive prompt with history as text
    if history_text:
        full_prompt = f"{history_text}\n\nUser: {prompt}\nAssistant:"
    else:
        full_prompt = f"User: {prompt}\nAssistant:"

    try:
        if model_lower.startswith("anthropic.") or model_lower.startswith("global.anthropic."):
            # Use converse() API with a SINGLE user message containing the full conversation
            # This avoids toolUse/toolResult validation errors completely
            logger.info("Using converse() API for Anthropic model (single message with text history)")
            response = client.converse(
                modelId=model_id,
                messages=[{
                    "role": "user",
                    "content": [{"text": full_prompt}]
                }],
                inferenceConfig={
                    "maxTokens": BEDROCK_MAX_TOKENS,
                    "temperature": 0
                }
            )
            
            # Extract text from converse() response format
            output = response.get("output", {})
            message = output.get("message", {})
            content_blocks = message.get("content", [])
            
            if content_blocks:
                text = content_blocks[0].get("text", "")
                logger.info(f"Claude response length: {len(text)} characters")
                return text
            raise RuntimeError("Empty response from Claude")

        elif model_lower.startswith("amazon.titan-text"):
            payload = {
                "inputText": full_prompt,
                "textGenerationConfig": {
                    "temperature": 0,
                    "maxTokenCount": BEDROCK_MAX_TOKENS,
                    "topP": 0.9,
                    "stopSequences": ["\nUser:"]
                }
            }
            response = client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(payload)
            )
            response_body = json.loads(response["body"].read())
            results = response_body.get("results", [])
            if results:
                text = results[0].get("outputText", "")
                logger.info(f"Titan response length: {len(text)} characters")
                return text.strip()
            raise RuntimeError("Empty response from Titan model")

        elif "llama" in model_lower:
            payload = {
                "prompt": full_prompt,
                "max_gen_len": BEDROCK_MAX_TOKENS,
                "temperature": 0,
                "top_p": 0.9,
            }
            response = client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(payload)
            )
            response_body = json.loads(response["body"].read())
            text = response_body.get("generation", "")
            if text:
                logger.info(f"Llama response length: {len(text)} characters")
                return text.strip()
            raise RuntimeError("Empty response from Llama model")

        else:
            raise RuntimeError(f"Unsupported Bedrock model for chat: {model_id}")

    except Exception as e:
        logger.error(f"Bedrock invocation failed: {e}")
        raise

# -------------------------
# Chat Commands
# -------------------------

def _find_section_by_title_or_number(brd_data: Dict, section_identifier: str) -> Optional[int]:
    """
    Find section number by title or number.
    
    Args:
        brd_data: BRD data structure
        section_identifier: Section number (as string) or section title
        
    Returns:
        Section number (1-indexed) or None if not found
    """
    sections = brd_data.get("sections", [])
    
    # Try to parse as number first
    try:
        section_num = int(section_identifier)
        if 1 <= section_num <= len(sections):
            return section_num
    except ValueError:
        pass
    
    # Try to find by title (case-insensitive, partial match)
    section_identifier_lower = section_identifier.lower().strip()
    
    # Check if first section is document title (common pattern)
    has_doc_title = False
    if sections:
        first_title = sections[0].get("title", "").lower()
        if ("ai-powered" in first_title or "brd" in first_title or 
            (len(first_title) < 30 and not re.match(r'^\d+\.', first_title))):
            has_doc_title = True
    
    # Skip document title if present (index 0)
    start_idx = 1 if has_doc_title else 0
    
    for idx in range(start_idx, len(sections)):
        section = sections[idx]
        title = section.get("title", "").lower()
        
        # Skip if this is the document title
        if idx == 0 and has_doc_title:
            continue
        
        # CRITICAL: Return value depends on whether document title exists
        # If document title exists: array index 11 = section 11 (return 11)
        # If no document title: array index 10 = section 11 (return 11)
        # Since we're already skipping document title (start_idx = 1), we need to adjust
        if has_doc_title:
            # With document title: array index 11 = section 11
            # So return idx (which is already the array index)
            section_num = idx
        else:
            # Without document title: array index 10 = section 11
            # So return idx + 1
            section_num = idx + 1
        
        # Exact match
        if title == section_identifier_lower:
            logger.info(f"   📋 Exact title match: '{title}' at array index {idx}, returning section number {section_num}")
            return section_num
        # Partial match (e.g., "stakeholders" matches "4. Stakeholders")
        if section_identifier_lower in title or title in section_identifier_lower:
            logger.info(f"   📋 Partial title match: '{title}' at array index {idx}, returning section number {section_num}")
            return section_num
        # Remove section number prefix and match (e.g., "4. Stakeholders" -> "stakeholders")
        title_clean = re.sub(r'^\d+\.\s*', '', title).strip()
        if title_clean == section_identifier_lower or section_identifier_lower in title_clean:
            logger.info(f"   📋 Cleaned title match: '{title_clean}' at array index {idx}, returning section number {section_num}")
            return section_num
        # Also try matching just the key word (e.g., "assumptions" matches "10. Assumptions")
        # Extract key words from both
        title_words = set(re.findall(r'\b\w+\b', title_clean))
        identifier_words = set(re.findall(r'\b\w+\b', section_identifier_lower))
        if identifier_words and identifier_words.issubset(title_words):
            logger.info(f"   📋 Keyword match: '{title_clean}' at array index {idx}, returning section number {section_num}")
            return section_num
        # Reverse: if title word is in identifier (e.g., "assumptions" in "show assumptions")
        if title_words and any(tw in section_identifier_lower for tw in title_words if len(tw) > 3):
            logger.info(f"   📋 Reverse keyword match: '{title_clean}' at array index {idx}, returning section number {section_num}")
            return section_num
    
    return None

def handle_list_sections(brd_data: Dict) -> str:
    """List all BRD sections"""
    sections = brd_data.get("sections", [])

    if not sections:
        return "No sections found in BRD."

    result = "**BRD Sections:**\n\n"
    for i, section in enumerate(sections, 1):
        result += f"{i}. {section.get('title', 'Untitled')}\n"

    return result

def handle_show_section(brd_data: Dict, section_number: int) -> str:
    """Show content of a specific section"""
    sections = brd_data.get("sections", [])

    # Check if first section is document title (common pattern)
    has_doc_title = False
    if sections:
        first_title = sections[0].get("title", "").lower()
        if ("ai-powered" in first_title or "brd" in first_title or 
            (len(first_title) < 30 and not re.match(r'^\d+\.', first_title))):
            has_doc_title = True
    
    # Calculate array index based on whether document title exists
    if has_doc_title:
        # With document title: section 11 = array index 11
        array_index = section_number
        max_section = len(sections) - 1  # Exclude document title from count
    else:
        # Without document title: section 11 = array index 10
        array_index = section_number - 1
        max_section = len(sections)
    
    if section_number < 1 or section_number > max_section:
        return f"Invalid section number. Please choose 1-{max_section}"

    section = sections[array_index]
    title = section.get("title", "Untitled")
    content_blocks = section.get("content", [])

    # CRITICAL: Ensure consistent section numbering in display
    # Remove any existing number prefix from title (e.g., "5. Scope" -> "Scope")
    # Then add the correct section number based on the actual position
    title_clean = re.sub(r'^\d+\.\s*', '', title).strip()
    # Always display with the correct section number: "## 5. Scope"
    # IMPORTANT: Include section number at the start for context tracking
    result = f"## {section_number}. {title_clean}\n\n"

    for block in content_blocks:
        block_type = block.get("type")
        if block_type == "paragraph":
            result += block.get("text", "") + "\n\n"
        elif block_type == "bullet":
            for item in block.get("items", []):
                result += f"- {item}\n"
            result += "\n"
        elif block_type == "table":
            rows = block.get("rows", [])
            for row in rows:
                result += "| " + " | ".join(str(cell) for cell in row) + " |\n"
            result += "\n"

    return result

def handle_update_section(brd_data: Dict, section_number: int, user_instruction: str, conversation_history: List[Dict]) -> Dict:
    """Update a BRD section based on user instruction"""
    logger.info("=" * 80)
    logger.info("🔍 [STEP 5] HANDLE_UPDATE_SECTION CALLED")
    logger.info(f"   section_number: {section_number} (type: {type(section_number)})")
    logger.info(f"   user_instruction: '{user_instruction}'")
    logger.info("=" * 80)
    
    sections = brd_data.get("sections", [])
    logger.info(f"   Total sections in BRD: {len(sections)}")
    
    # Check if first section is document title (common pattern)
    has_doc_title = False
    if sections:
        first_title = sections[0].get("title", "").lower()
        if ("ai-powered" in first_title or "brd" in first_title or 
            (len(first_title) < 30 and not re.match(r'^\d+\.', first_title))):
            has_doc_title = True
            logger.info(f"   📋 Document title detected at index 0: '{sections[0].get('title', '')}'")
    
    # Log ALL section titles with their indices
    logger.info("   📋 ALL SECTIONS IN BRD:")
    for idx, sec in enumerate(sections):
        title = sec.get('title', 'Untitled')
        is_doc_title = (idx == 0 and has_doc_title)
        logger.info(f"      [{idx+1}] (index {idx}){' [DOC TITLE]' if is_doc_title else ''}: '{title}'")
    
    # Adjust section number if document title exists
    # User says "section 11", but if doc title exists, it's at index 11 (not 10)
    if has_doc_title:
        array_index = section_number  # User's section 11 = array index 11
        if array_index < 1 or array_index >= len(sections):
            logger.error(f"   ❌ Invalid section number: {section_number} (must be 1-{len(sections) - 1})")
            return {
                "success": False,
                "message": f"Invalid section number. Please choose 1-{len(sections) - 1}"
            }
    else:
        array_index = section_number - 1
        if section_number < 1 or section_number > len(sections):
            logger.error(f"   ❌ Invalid section number: {section_number} (must be 1-{len(sections)})")
            return {
                "success": False,
                "message": f"Invalid section number. Please choose 1-{len(sections)}"
            }

    logger.info(f"   🔎 Retrieving section at array index {array_index} (user's section number {section_number})")
    section = sections[array_index]
    actual_title = section.get("title", "Untitled")
    logger.info(f"   ✅ Retrieved section title: '{actual_title}'")
    logger.info(f"   📄 Section content preview (first 300 chars): {str(section.get('content', []))[:300]}")
    
    # Log first few content blocks
    content_blocks = section.get("content", [])
    logger.info(f"   📄 Number of content blocks: {len(content_blocks)}")
    for i, block in enumerate(content_blocks[:3]):
        block_type = block.get("type", "unknown")
        if block_type == "bullet":
            items = block.get("items", [])
            logger.info(f"      Block {i+1} (bullet): {items[0][:100] if items else 'empty'}...")
        elif block_type == "paragraph":
            text = block.get("text", "")[:100]
            logger.info(f"      Block {i+1} (paragraph): {text}...")
        else:
            logger.info(f"      Block {i+1} ({block_type}): {str(block)[:100]}...")
    
    logger.info("=" * 80)
    
    # CRITICAL: Verify we have the right section by checking if the title matches what we expect
    # If the user is viewing "Stakeholders" but we got "Background / Context", there's a mismatch
    # This could happen if the BRD structure has sections in a different order or has extra items

    # Build update prompt - intelligently interpret the instruction
    # If the instruction contains markdown or structured content, treat it as replacement content
    # Otherwise, treat it as an instruction to modify
    instruction_lower = user_instruction.lower()
    has_markdown_headers = "##" in user_instruction or "**" in user_instruction
    has_structured_content = "\n\n" in user_instruction or "- " in user_instruction or "|" in user_instruction
    
    if has_markdown_headers or (has_structured_content and len(user_instruction) > 200):
        # User provided full content - interpret and structure it
        prompt = f"""You are a documentation assistant. The user wants to update a BRD section. They have provided new content below.

CURRENT SECTION:
{json.dumps(section, indent=2)}

USER'S NEW CONTENT:
{user_instruction}

Your task:
1. Parse the user's content and understand what they want
2. If they provided a full section with headers (like "## 5. Scope"), extract just the content parts
3. If they provided specific instructions (like "replace the Out of Scope section with..."), follow those instructions
4. Convert the content into the proper JSON structure below
5. Keep the section title the same unless explicitly changed

Respond ONLY with JSON in this exact structure:
{{
    "title": "<section title>",
    "content": [
        {{ "type": "paragraph", "text": "..." }},
        {{ "type": "bullet", "items": ["item1","item2"] }},
        {{ "type": "table", "rows": [["col1","col2"],["v1","v2"]] }}
    ]
}}

Important:
- If the user provided markdown content, convert it to the JSON structure
- If the user said "replace X with Y", replace X with Y in the content
- If the user provided a full section, extract and structure the content appropriately
- Preserve the section title unless the user explicitly wants it changed"""
    else:
        # Simple instruction - modify based on instruction
        # CRITICAL: Explicitly tell Claude which section number this is
        section_title_from_data = section.get("title", "Untitled Section")
        # Remove any section number prefix from title (e.g., "4. Stakeholders" -> "Stakeholders")
        section_title_clean = re.sub(r'^\d+\.\s*', '', section_title_from_data).strip()
        
        # Get a preview of the section content to help Claude understand what section this is
        section_content_preview = ""
        content_blocks = section.get("content", [])
        if content_blocks:
            first_block = content_blocks[0]
            if first_block.get("type") == "paragraph":
                section_content_preview = first_block.get("text", "")[:200]
            elif first_block.get("type") == "table":
                rows = first_block.get("rows", [])
                if rows:
                    section_content_preview = f"Table with headers: {', '.join(str(cell) for cell in rows[0][:3])}"
            elif first_block.get("type") == "bullet":
                items = first_block.get("items", [])
                if items:
                    section_content_preview = f"Bullet list starting with: {items[0][:100]}"
        
        # Get full section content as text for better context
        section_content_text = ""
        for block in content_blocks[:3]:  # First 3 blocks for context
            if block.get("type") == "paragraph":
                section_content_text += block.get("text", "") + "\n"
            elif block.get("type") == "table":
                rows = block.get("rows", [])
                if rows:
                    section_content_text += "Table: " + " | ".join(str(cell) for cell in rows[0][:5]) + "\n"
            elif block.get("type") == "bullet":
                items = block.get("items", [])
                if items:
                    section_content_text += "Bullets: " + ", ".join(items[:3]) + "\n"
        
        logger.info("=" * 80)
        logger.info("🔍 [STEP 6] BUILDING PROMPT FOR CLAUDE")
        logger.info(f"   Section Number: {section_number}")
        logger.info(f"   Section Title (clean): '{section_title_clean}'")
        logger.info(f"   Section Title (from data): '{section_title_from_data}'")
        logger.info(f"   Section Content Preview: {section_content_text[:300]}")
        logger.info(f"   User Instruction: '{user_instruction}'")
        logger.info("=" * 80)
        
        prompt = f"""You are a documentation assistant. You MUST update BRD section #{section_number} based on the user's instruction.

CRITICAL INFORMATION:
- Section Number: {section_number} (this is the EXACT section number in the BRD structure, at array index {section_number - 1})
- Section Title: "{section_title_clean}"
- Section Content Preview: {section_content_text[:300]}

YOU ARE UPDATING SECTION #{section_number} TITLED "{section_title_clean}".
DO NOT update any other section. IGNORE any section numbers mentioned in conversation history.

FULL SECTION #{section_number} DATA:
{json.dumps(section, indent=2)}

USER INSTRUCTION:
{user_instruction}

Your task:
1. Find the content in section #{section_number} (titled "{section_title_clean}")
2. Apply the user's instruction: "{user_instruction}"
3. Return ONLY the updated section #{section_number}

Respond ONLY with JSON in this exact structure:
{{
    "title": "{section_title_clean}",
    "content": [
        {{ "type": "paragraph", "text": "..." }},
        {{ "type": "bullet", "items": ["item1","item2"] }},
        {{ "type": "table", "rows": [["col1","col2"],["v1","v2"]] }}
    ]
}}

CRITICAL REQUIREMENTS:
1. The "title" field MUST be exactly "{section_title_clean}" (no number prefix, no variations)
2. Only modify the CONTENT array - find the text/items/rows that match the user's instruction and update them
3. If user says "change X to Y", search for X in the content of section #{section_number} and replace ALL occurrences with Y
4. Do NOT change the section title
5. Do NOT modify any other section
6. Return the complete section with all content blocks (not just the changed parts)

VERIFICATION CHECKLIST:
- [ ] Title is exactly "{section_title_clean}"
- [ ] I am updating section #{section_number} only
- [ ] I found and updated the content matching "{user_instruction}"
- [ ] I did NOT change any other section"""

    try:
        # CRITICAL: Do NOT send conversation history to Claude when updating sections
        # Conversation history can confuse Claude about which section to update
        # Only send the prompt with the section data
        logger.info("=" * 80)
        logger.info("🔍 [STEP 7] INVOKING CLAUDE")
        logger.info(f"   Section Number: {section_number}")
        logger.info(f"   Section Title (clean): '{section_title_clean}'")
        logger.info(f"   User Instruction: '{user_instruction}'")
        logger.info(f"   Prompt length: {len(prompt)} characters")
        logger.info(f"   Prompt preview (first 800 chars):")
        logger.info(f"   {prompt[:800]}...")
        logger.info(f"   NOT sending conversation history to Claude (to avoid confusion)")
        logger.info("=" * 80)
        
        raw_response = invoke_claude_for_chat(prompt, [])  # Empty history to avoid confusion
        
        logger.info("=" * 80)
        logger.info("🔍 [STEP 8] CLAUDE RESPONSE RECEIVED")
        logger.info(f"   Response length: {len(raw_response)} characters")
        logger.info(f"   Response preview (first 800 chars):")
        logger.info(f"   {raw_response[:800]}...")
        logger.info("=" * 80)

        # Parse JSON from response
        json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if not json_match:
            return {
                "success": False,
                "message": "Failed to parse JSON response from AI"
            }

        json_str = json_match.group(0)
        try:
            updated_section = json.loads(json_str)
        except json.JSONDecodeError:
            try:
                decoder = json.JSONDecoder()
                updated_section, _ = decoder.raw_decode(json_str)
            except Exception:
                return {
                    "success": False,
                    "message": "Failed to parse JSON response from AI"
                }

        # Validate structure
        if "title" not in updated_section or "content" not in updated_section:
            return {
                "success": False,
                "message": "Invalid section structure in AI response"
            }

        logger.info("=" * 80)
        logger.info("🔍 [STEP 9] VALIDATING CLAUDE'S RESPONSE")
        logger.info(f"   Updated section title from Claude: '{updated_section.get('title', 'Untitled')}'")
        
        # CRITICAL: Verify that Claude updated the correct section
        # Remove any section number prefix from the returned title for comparison
        returned_title_clean = re.sub(r'^\d+\.\s*', '', updated_section.get("title", "")).strip()
        expected_title_clean = re.sub(r'^\d+\.\s*', '', section.get("title", "")).strip()
        
        logger.info(f"   Expected section number: {section_number}")
        logger.info(f"   Expected section title (from BRD data): '{section.get('title', 'Untitled')}'")
        logger.info(f"   Expected section title (cleaned): '{expected_title_clean}'")
        logger.info(f"   Returned section title (from Claude): '{updated_section.get('title', 'Untitled')}'")
        logger.info(f"   Returned section title (cleaned): '{returned_title_clean}'")
        
        # Also check if the returned title matches the section number we requested
        returned_title_lower = returned_title_clean.lower()
        expected_title_lower = expected_title_clean.lower()
        
        logger.info(f"   Comparison (lowercase):")
        logger.info(f"      Expected: '{expected_title_lower}'")
        logger.info(f"      Returned: '{returned_title_lower}'")
        
        # Check if titles match (allowing for minor variations)
        titles_match = (returned_title_lower == expected_title_lower or 
                      returned_title_lower in expected_title_lower or 
                      expected_title_lower in returned_title_lower)
        
        logger.info(f"   Titles match: {titles_match}")
        
        if not titles_match:
            logger.error(f"   ❌ TITLE MISMATCH! Expected section #{section_number} with title '{expected_title_clean}', but Claude returned title '{returned_title_clean}'")
            logger.error(f"   ❌ This indicates Claude updated the wrong section!")
            
            # Try to find the correct section by title
            logger.info(f"   🔎 Searching for which section Claude actually updated...")
            for idx, sec in enumerate(sections, start=1):
                sec_title_clean = re.sub(r'^\d+\.\s*', '', sec.get("title", "")).strip().lower()
                if returned_title_lower == sec_title_clean:
                    logger.error(f"   ❌ FOUND IT! Claude updated section #{idx} ('{sec.get('title', 'Untitled')}') instead of section #{section_number} ('{expected_title_clean}')!")
                    logger.error(f"   ❌ Section #{idx} title: '{sec.get('title', 'Untitled')}'")
                    logger.error(f"   ❌ Section #{section_number} title: '{section.get('title', 'Untitled')}'")
                    logger.info("=" * 80)
                    return {
                        "success": False,
                        "message": f"Error: AI updated section #{idx} ('{returned_title_clean}') instead of section #{section_number} ('{expected_title_clean}'). Please try again with explicit section number."
                    }
            
            logger.error(f"   ❌ Could not find which section Claude updated. Title '{returned_title_clean}' doesn't match any section.")
            logger.info("=" * 80)
        else:
            logger.info(f"   ✅ Title match confirmed! Claude updated the correct section.")
            logger.info("=" * 80)
        
        # Ensure the title doesn't have a section number prefix (we'll add it when displaying)
        updated_section["title"] = expected_title_clean  # Use the expected title to ensure consistency
        
        # CRITICAL: Use the correct array_index that was calculated earlier (accounts for document title)
        # array_index was calculated based on has_doc_title:
        # - With doc title: array_index = section_number (section 11 = index 11)
        # - Without doc title: array_index = section_number - 1 (section 11 = index 10)
        logger.info(f"   🔧 Updating section at array index {array_index} (user's section {section_number})")
        sections[array_index] = updated_section
        brd_data["sections"] = sections
        
        # Log verification
        logger.info(f"   ✅ Section at index {array_index} updated. New title: '{sections[array_index].get('title', 'Untitled')}'")

        return {
            "success": True,
            "message": f"✅ Section '{section_number}. {expected_title_clean}' updated successfully",
            "updated_brd": brd_data
        }

    except Exception as e:
        logger.error(f"Section update failed: {e}")
        return {
            "success": False,
            "message": f"Update failed: {str(e)}"
        }

# -------------------------
# Main Lambda Handler
# -------------------------

def lambda_handler(event, context):
    """
    Handle chat-based BRD editing requests

    Supports both:
    1. Direct API calls: {"action": "...", "brd_id": "...", ...}
    2. Agent invocations: {"parameters": {"action": "...", "brd_id": "...", ...}}

    Expected event format:
    {
        "action": "create_session" | "send_message" | "get_history" | "delete_session",
        "brd_id": "uuid",
        "session_id": "session-id" (for existing sessions),
        "message": "user message" (for send_message),
        "template": "template text" (for create_session),
        "transcript": "transcript text" (for create_session)
    }
    """
    try:
        logger.info(f"Chat Lambda invoked with event: {json.dumps(event)[:500]}")

        # Handle agent invocation format
        if "parameters" in event:
            # Agent is calling - extract parameters from nested structure
            event = event["parameters"]
            logger.info("Detected agent invocation format, extracted parameters")
        
        # Also handle context field if present (from agent function schema)
        if "context" in event and isinstance(event.get("context"), str):
            try:
                context_data = json.loads(event["context"])
                # Merge context data into event
                event.update(context_data)
                logger.info("Merged context data into event")
            except json.JSONDecodeError:
                logger.warning("Failed to parse context field as JSON")

        action = event.get("action")

        # CREATE SESSION
        if action == "create_session":
            brd_id = event.get("brd_id")
            template = event.get("template", "")
            transcript = event.get("transcript", "")

            if not brd_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "brd_id is required"})
                }

            # Create AgentCore Memory session
            session_info = create_memory_session(brd_id, template, transcript)

            # Initialize welcome message
            welcome_msg = f"Chat session created for BRD {brd_id}. You can now:\n- Type 'list' to see all sections\n- Type 'show N' to view section N\n- Type 'update N: your instruction' to modify section N"

            add_message_to_memory(session_info["session_id"], "assistant", welcome_msg)

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "session_id": session_info["session_id"],
                    "brd_id": brd_id,
                    "message": welcome_msg
                })
            }

        # SEND MESSAGE
        elif action == "send_message":
            session_id = event.get("session_id")
            user_message = event.get("message", "").strip()
            brd_id = event.get("brd_id")

            if not user_message or not brd_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "message and brd_id are required"})
                }

            # Auto-create session if not provided or doesn't exist
            if not session_id:
                # Generate a session ID based on BRD ID
                session_id = f"brd-session-{brd_id}"
                logger.info(f"No session_id provided, using auto-generated: {session_id}")
            
            # Verify session exists, create if it doesn't
            session = get_memory_session(session_id)
            if not session:
                logger.info(f"Session {session_id} not found, creating new session for BRD {brd_id}")
                try:
                    # Create a minimal session (we don't have template/transcript here, but that's OK)
                    session_info = create_memory_session(brd_id, "", "")
                    session_id = session_info["session_id"]
                    logger.info(f"Created new session: {session_id}")
                except Exception as e:
                    logger.warning(f"Failed to create session, continuing anyway: {e}")
                    # Continue without session - we'll still process the message

            # Strip [BRD_ID: ...] prefix from message if present
            import re
            user_message_clean = re.sub(r'\[BRD_ID:\s*[^\]]+\]\s*', '', user_message, count=1).strip()
            
            # Add user message to memory (use cleaned version for display)
            add_message_to_memory(session_id, "user", user_message_clean)

            # Get conversation history
            history = get_session_history(session_id)

            # Load current BRD data
            brd_data = get_brd_from_s3(brd_id)
            if not brd_data:
                # Try to backfill structure from text
                logger.info(f"BRD structure not found, attempting to reconstruct from text for {brd_id}")
                brd_data = backfill_brd_structure(brd_id)
                if not brd_data:
                    # Check if at least the text file exists
                    brd_text = get_brd_text_from_s3(brd_id)
                    if brd_text:
                        logger.warning(f"BRD text exists but structure reconstruction failed. Attempting simple text-based parsing...")
                        # Last resort: Try to create a minimal structure from text for basic operations
                        try:
                            brd_data = create_minimal_structure_from_text(brd_text)
                            if brd_data:
                                logger.info(f"Created minimal structure from text with {len(brd_data.get('sections', []))} sections")
                                # Save it so we don't have to do this again
                                try:
                                    save_brd_to_s3(brd_id, brd_data)
                                except Exception as save_err:
                                    logger.warning(f"Could not save minimal structure: {save_err}")
                            else:
                                logger.error("Failed to create minimal structure from text")
                                return {
                                    "statusCode": 500,
                                    "body": json.dumps({
                                        "error": "BRD structure reconstruction failed",
                                        "message": "The BRD text file exists but we could not reconstruct the structured format. The BRD may be too complex or malformed. Please try regenerating the BRD."
                                    })
                                }
                        except Exception as parse_err:
                            logger.error(f"Failed to create minimal structure: {parse_err}")
                            return {
                                "statusCode": 500,
                                "body": json.dumps({
                                    "error": "BRD structure reconstruction failed",
                                    "message": f"The BRD text file exists but we could not parse it. Error: {str(parse_err)}"
                                })
                            }
                    else:
                        return {
                            "statusCode": 404,
                            "body": json.dumps({"error": "BRD not found"})
                        }
            
            # Track last shown section AND last updated section from conversation history AND current message for context-based updates
            last_shown_section = None
            last_shown_section_title = None  # CRITICAL: Also track the section title to find the correct section
            last_updated_section = None  # Track which section was actually updated (for "show me updated section")
            
            logger.info("=" * 80)
            logger.info("🔍 [STEP 1] INITIAL STATE - Section Tracking Variables")
            logger.info(f"   last_shown_section: {last_shown_section}")
            logger.info(f"   last_shown_section_title: {last_shown_section_title}")
            logger.info(f"   last_updated_section: {last_updated_section}")
            logger.info(f"   User message (first 200 chars): {user_message_clean[:200]}")
            logger.info("=" * 80)
            
            # FIRST: Check the current message for section context (e.g., "SECTION 4: Stakeholders" at the start)
            # This handles cases where the agent includes the section in the message
            section_in_message = None
            user_actual_request = None
            
            if user_message_clean:
                # Extract the actual user request - look for "USER REQUEST:" marker
                user_request_match = re.search(r'USER REQUEST:\s*(.+?)(?:\r?\n|$)', user_message_clean, re.IGNORECASE | re.DOTALL)
                if user_request_match:
                    user_actual_request = user_request_match.group(1).strip()
                    logger.info(f"Extracted user request from message: {user_actual_request[:100]}")
                else:
                    # If no USER REQUEST marker, try to find the last meaningful line
                    # Look for patterns that indicate the actual request (not section content)
                    lines = user_message_clean.split('\n')
                    for line in reversed(lines):
                        line = line.strip()
                        if line and not line.startswith(('##', '**', '|', '-', '---', 'SECTION', 'IMPORTANT')):
                            # This might be the actual request
                            if any(word in line.lower() for word in ['change', 'update', 'modify', 'edit', 'replace', 'show', 'list']):
                                user_actual_request = line
                                logger.info(f"Extracted user request from last meaningful line: {user_actual_request[:100]}")
                                break
                
                # Look for patterns like "SECTION 4:", "SECTION 4: Stakeholders", "4. Stakeholders", etc.
                section_match = re.search(r'(?:SECTION\s+)?(\d+)[:\.]\s*([^\r\n]+)?', user_message_clean, re.IGNORECASE)
                if section_match:
                    try:
                        section_in_message = int(section_match.group(1))
                        section_title = section_match.group(2).strip() if section_match.group(2) else None
                        logger.info(f"Found section in current message: {section_in_message} ({section_title})")
                        last_shown_section = section_in_message
                        # CRITICAL: Store the section title when found
                        if section_title:
                            last_shown_section_title = section_title
                            logger.info(f"✅ Stored section title from message: '{last_shown_section_title}'")
                        else:
                            # If no title in message, try to find it from BRD data
                            if brd_data and section_in_message <= len(brd_data.get("sections", [])):
                                # Check if first section is document title (common pattern)
                                sections = brd_data.get("sections", [])
                                # If section 1 is likely a document title, adjust index
                                if sections and section_in_message > 0:
                                    # Try to find section by number, accounting for possible document title
                                    section_idx = section_in_message - 1
                                    # Check if first section is document title (has "AI-Powered" or similar, not "Document Overview")
                                    if section_idx == 0 and sections[0].get("title", "").lower() not in ["document overview", "1. document overview"]:
                                        # First section is likely document title, adjust
                                        if "ai-powered" in sections[0].get("title", "").lower() or len(sections[0].get("title", "")) < 30:
                                            # Document title detected, use next section
                                            section_idx = section_in_message  # Don't subtract 1
                                            logger.info(f"⚠️ Document title detected at index 0, adjusting section index from {section_in_message - 1} to {section_idx}")
                                    
                                    if section_idx < len(sections):
                                        found_section = sections[section_idx]
                                        found_title = re.sub(r'^\d+\.\s*', '', found_section.get("title", "")).strip()
                                        last_shown_section_title = found_title
                                        logger.info(f"✅ Stored section title from BRD data: '{last_shown_section_title}'")
                    except (ValueError, AttributeError):
                        pass
                
                # Also check for "## 4. Title" or "## Section 4" patterns
                if not section_in_message:
                    section_match = re.search(r'##\s*(?:Section\s+)?(\d+)', user_message_clean, re.IGNORECASE)
                    if section_match:
                        try:
                            section_in_message = int(section_match.group(1))
                            logger.info(f"Found section in current message (## format): {section_in_message}")
                            last_shown_section = section_in_message
                        except ValueError:
                            pass
                
                # CRITICAL: If no section number found, check if message starts with a section title
                # This handles cases where frontend sends "Stakeholders\n\nStakeholder\tRole..." without section number
                if not section_in_message and brd_data:
                    # Get the first line (or first few words) of the message
                    first_line = user_message_clean.split('\n')[0].strip()
                    # Remove any markdown formatting
                    first_line_clean = re.sub(r'^#+\s*', '', first_line).strip()
                    # Remove any number prefix if present
                    first_line_clean = re.sub(r'^\d+\.\s*', '', first_line_clean).strip()
                    
                    # Try to find section by title (e.g., "Stakeholders", "Scope", "Background / Context")
                    if first_line_clean and len(first_line_clean) > 2:
                        # Check if this looks like a section title (not a command)
                        # IMPORTANT: Also check if the message contains section content (tables, lists, etc.)
                        # This helps identify when user is sending back section content they just viewed
                        has_section_content = any(indicator in user_message_clean for indicator in [
                            '\t', '|', 'Role', 'Responsibility', 'Stakeholder',  # Table indicators
                            'Business Drivers', 'Pain Points',  # Common section content
                            'Functional Requirements', 'Non-Functional Requirements'
                        ])
                        
                        is_command = any(word in first_line_clean.lower() for word in ['change', 'update', 'modify', 'edit', 'replace', 'show', 'list', 'hi', 'hello'])
                        
                        # If message has section content OR doesn't look like a command, try to find section
                        if has_section_content or (not is_command and len(first_line_clean) > 3):
                            section_num_by_title = _find_section_by_title_or_number(brd_data, first_line_clean)
                            if section_num_by_title:
                                logger.info("=" * 80)
                                logger.info("🔍 [STEP 2] SECTION IDENTIFIED FROM CURRENT MESSAGE")
                                logger.info(f"   First line of message: '{first_line_clean}'")
                                logger.info(f"   Section found by title: section #{section_num_by_title}")
                                logger.info(f"   has_section_content: {has_section_content}")
                                logger.info(f"   is_command: {is_command}")
                                
                                section_in_message = section_num_by_title
                                last_shown_section = section_num_by_title
                                last_shown_section_title = first_line_clean  # CRITICAL: Store the title for later use
                                
                                logger.info(f"   ✅ STORED: last_shown_section = {last_shown_section}")
                                logger.info(f"   ✅ STORED: last_shown_section_title = '{last_shown_section_title}'")
                                
                                # Verify the section we found
                                if section_num_by_title <= len(brd_data.get("sections", [])):
                                    found_section = brd_data.get("sections", [])[section_num_by_title - 1]
                                    found_title = found_section.get("title", "Untitled")
                                    logger.info(f"   📋 VERIFICATION: Section #{section_num_by_title} in BRD has title: '{found_title}'")
                                    logger.info(f"   📋 VERIFICATION: Expected title from message: '{first_line_clean}'")
                                    if first_line_clean.lower() in found_title.lower() or found_title.lower() in first_line_clean.lower():
                                        logger.info(f"   ✅ Title match confirmed!")
                                    else:
                                        logger.warning(f"   ⚠️ Title mismatch! This might cause issues later.")
                                logger.info("=" * 80)
                                
                                # If we found a section and the message contains "here", this is likely a context-based update
                                if "here" in user_message_clean.lower() and not any(word in user_message_clean.lower() for word in ['change', 'update', 'modify', 'edit', 'replace']):
                                    # User might be just viewing, not updating yet
                                    pass
                                elif "here" in user_message_clean.lower():
                                    # User wants to update this section - we already have last_shown_section set
                                    logger.info(f"Context-based update detected for section {last_shown_section} (title: '{last_shown_section_title}') based on section content in message")
                
                # If we extracted the user's actual request, use that for parsing instead of the full message
                # CRITICAL: Preserve last_shown_section before replacing user_message_clean
                if user_actual_request:
                    logger.info(f"Using extracted user request for parsing: {user_actual_request}")
                    logger.info(f"Preserving last_shown_section={last_shown_section} before extracting user request")
                    # Store the section we found before replacing the message
                    preserved_section = last_shown_section if last_shown_section else section_in_message
                    # Update user_message_clean to use the actual request
                    original_message = user_message_clean
                    user_message_clean = user_actual_request
                    # Restore the section we found from the original message
                    if preserved_section and not last_shown_section:
                        last_shown_section = preserved_section
                        logger.info(f"Restored last_shown_section={last_shown_section} after extracting user request")
            
            # SECOND: If not found in current message, check conversation history
            # CRITICAL: We want the section the user is CURRENTLY VIEWING, not the last one updated
            # Priority order (MOST RECENT FIRST):
            # 1. Section from current message (already handled above) - DO NOT OVERRIDE THIS
            # 2. Most recent user "show" command (MOST RELIABLE - user explicitly asked to see this section)
            # 3. Most recent section that was SHOWN/DISPLAYED (assistant showing section content)
            # 4. Fallback: Any section reference (but prefer shown over updated)
            # IMPORTANT: Only check history if we didn't find a section in the current message
            if not last_shown_section and not section_in_message and history:
                # PRIORITY 1: Look for user "show" commands FIRST (most reliable indicator)
                # These tell us exactly what section the user is viewing
                for msg in reversed(history):
                    if msg.get("role") != "user":
                        continue
                    
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict):
                                text_parts.append(item.get("text", ""))
                            elif isinstance(item, str):
                                text_parts.append(item)
                        content = " ".join(text_parts)
                    elif isinstance(content, dict):
                        content = content.get("text", "")
                    
                    if not isinstance(content, str):
                        continue
                    
                    # User "show" commands are the most reliable indicator of what section they're viewing
                    show_match = re.search(r'show\s+(?:me\s+)?(?:section\s+)?(\d+)', content.lower())
                    if show_match:
                        try:
                            last_shown_section = int(show_match.group(1))
                            logger.info(f"Found last shown section from user 'show' command (number): {last_shown_section}")
                            break
                        except ValueError:
                            pass
                    
                    # Also check for section title in "show" commands (e.g., "show stakeholders")
                    if not last_shown_section:
                        show_title_match = re.search(r'show\s+(?:me\s+)?(?:section\s+)?([a-zA-Z\s]{3,})', content.lower())
                        if show_title_match:
                            section_title = show_title_match.group(1).strip()
                            # Filter out common question words
                            if section_title.lower() not in ['updated', 'what', 'which', 'where', 'when', 'why', 'how', 'all']:
                                # Try to find section by title
                                section_num = _find_section_by_title_or_number(brd_data, section_title)
                                if section_num:
                                    last_shown_section = section_num
                                    logger.info(f"Found section {section_num} from user 'show' command with title: '{section_title}'")
                                    break
                
                # PRIORITY 2: If no user "show" command found, look for assistant section displays
                # These are assistant messages that display section content, NOT update confirmations
                if not last_shown_section:
                    for msg in reversed(history):
                        if msg.get("role") != "assistant":
                            continue
                        
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = []
                            for item in content:
                                if isinstance(item, dict):
                                    text_parts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    text_parts.append(item)
                            content = " ".join(text_parts)
                        elif isinstance(content, dict):
                            content = content.get("text", "")
                        
                        if not isinstance(content, str):
                            continue
                        
                        # CRITICAL: Skip update confirmation messages - these are NOT the section being viewed
                        if "updated successfully" in content.lower() or "✅" in content or "Section '" in content:
                            logger.debug(f"Skipping update confirmation message: {content[:100]}")
                            continue
                        
                        # Check if this is a section DISPLAY (showing section content to user)
                        # Look for patterns that indicate section content display:
                        # - Section headers like "5. Scope" or "## 5. Scope"
                        # - Section titles followed by content (tables, lists, etc.)
                        # - NOT update confirmations like "✅ Section '4. Stakeholders' updated"
                        
                        # Pattern 1: Section header at start of message (most reliable)
                        section_header_match = re.search(r'^(?:##\s*)?(\d+)\.\s+([A-Z][^\n]*)', content, re.MULTILINE | re.IGNORECASE)
                        if section_header_match:
                            try:
                                section_num = int(section_header_match.group(1))
                                section_title = section_header_match.group(2).strip()
                                # Verify this looks like a section display (has content after title)
                                if len(content) > len(section_title) + 20:  # Has substantial content
                                    last_shown_section = section_num
                                    logger.info(f"Found last SHOWN section from assistant display: {section_num} ({section_title})")
                                    break
                            except ValueError:
                                pass
                        
                        # Pattern 2: Section number followed by section title anywhere in content
                        # But only if it's not an update confirmation
                        if not last_shown_section:
                            section_match = re.search(r'(\d+)\.\s+([A-Z][a-zA-Z\s]+)', content, re.IGNORECASE)
                            if section_match:
                                try:
                                    section_num = int(section_match.group(1))
                                    # Make sure this isn't an update message
                                    if "updated" not in content.lower()[:50]:  # Check first 50 chars
                                        last_shown_section = section_num
                                        logger.info(f"Found last SHOWN section from assistant content: {section_num}")
                                        break
                                except ValueError:
                                    pass
                
                # Second pass (fallback): If still not found, look for any section reference
                # But prioritize sections from assistant messages (likely shown) over update messages
                if not last_shown_section:
                    for msg in reversed(history):
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = []
                            for item in content:
                                if isinstance(item, dict):
                                    text_parts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    text_parts.append(item)
                            content = " ".join(text_parts)
                        elif isinstance(content, dict):
                            content = content.get("text", "")
                        
                        if not isinstance(content, str):
                            continue
                        
                        # Skip update confirmations even in fallback
                        if "updated successfully" in content.lower() or "✅" in content:
                            continue
                        
                        # Look for any section number pattern
                        section_match = re.search(r'(\d+)\.\s+', content, re.IGNORECASE)
                        if section_match:
                            try:
                                last_shown_section = int(section_match.group(1))
                                logger.info(f"Found section from history (fallback): {last_shown_section}")
                                break
                            except ValueError:
                                pass

            # Check for explicit command parameter from agent
            command = event.get("command")
            
            # Parse command
            assistant_response = ""
            brd_updated = False

            # Validate command - if it contains BRD ID fragments (like "832"), ignore it and parse message directly
            use_command = False
            if command:
                # Check if command looks suspicious (contains numbers that might be BRD ID fragments)
                # BRD IDs are UUIDs, so if we see a 3-digit number > 100, it's likely a BRD ID fragment
                suspicious_match = re.search(r'\b(\d{3,})\b', command)
                if suspicious_match:
                    potential_id = int(suspicious_match.group(1))
                    # If the number is > 100, it's likely a BRD ID fragment, not a section number
                    if potential_id > 100:
                        logger.warning(f"Ignoring suspicious command from agent (likely contains BRD ID fragment): {command}")
                        logger.info(f"Will parse original message instead: {user_message_clean}")
                        use_command = False
                    else:
                        use_command = True
                else:
                    use_command = True

            if command and use_command:
                # Use explicit command from agent
                logger.info(f"Using explicit command from agent: {command}")
                if command == "list":
                    assistant_response = handle_list_sections(brd_data)
                elif command.startswith("show "):
                    try:
                        section_num = int(command.split()[1])
                        assistant_response = handle_show_section(brd_data, section_num)
                    except (IndexError, ValueError):
                        assistant_response = "Usage: show <section number>"
                elif command.startswith("update "):
                    # Parse: "update 3: Add more details about security"
                    match = re.match(r'update\s+(\d+):\s*(.+)', command, re.IGNORECASE)
                    if match:
                        section_num = int(match.group(1))
                        instruction = match.group(2)

                        result = handle_update_section(brd_data, section_num, instruction, history)
                        assistant_response = result["message"]

                        if result["success"]:
                            # Save updated BRD to S3
                            updated_brd = result["updated_brd"]
                            save_brd_to_s3(brd_id, updated_brd)
                            # Always save text file - critical for downloads
                            try:
                                save_brd_text_to_s3(brd_id, updated_brd)
                                logger.info(f"Successfully updated BRD text file for {brd_id}")
                            except Exception as text_err:
                                logger.error(f"CRITICAL: Failed to update BRD text file: {text_err}")
                                # Still mark as updated but log the error
                            brd_updated = True
                    else:
                        assistant_response = "Usage: update <section number>: <your instruction>"
                else:
                    # Unknown command, fall back to parsing message directly
                    logger.info(f"Unknown command format, parsing message directly: {user_message_clean}")
                    # Fall through to message parsing below
                    use_command = False
            
            # If command was invalid or not provided, parse the message directly
            if not use_command or not command:
                logger.info(f"Parsing message directly (command ignored or not provided): {user_message_clean}")
            
            if not use_command or not command or (command and not use_command):
                # Parse message directly (either no command or command was invalid)
                if user_message_clean.lower() == "list":
                    assistant_response = handle_list_sections(brd_data)

                elif user_message_clean.lower().startswith("show ") or ("show" in user_message_clean.lower() and ("section" in user_message_clean.lower() or any(word in user_message_clean.lower() for word in ["assumptions", "constraints", "stakeholders", "scope", "purpose", "updated"]))):
                    # Handle "show 4", "show section 3", "show me section 3", "show assumptions", "show me constraints", "show me updated section"
                    try:
                        # Special case: "show me updated section" or "show updated section"
                        if "updated" in user_message_clean.lower():
                            # Use the last UPDATED section (not shown section)
                            # Priority 1: Check current request tracking (if update happened in this request)
                            if last_updated_section:
                                section_num = last_updated_section
                                assistant_response = handle_show_section(brd_data, section_num)
                                logger.info(f"✅ [SHOW UPDATED] Using section from current request: {section_num}")
                            else:
                                # Priority 2: Check the current message for update confirmation
                                # (Agent often includes previous update confirmation in the message)
                                last_updated_from_message = None
                                current_message_lower = user_message.lower()
                                if "✅" in user_message or "updated successfully" in current_message_lower:
                                    section_match = re.search(r"Section\s+['\"](\d+)\.\s*([^'\"]+)['\"]", user_message, re.IGNORECASE)
                                    if section_match:
                                        try:
                                            last_updated_from_message = int(section_match.group(1))
                                            found_title = section_match.group(2).strip()
                                            logger.info(f"🔍 [SHOW UPDATED] Found update confirmation in current message: Section {last_updated_from_message} ({found_title})")
                                        except (ValueError, IndexError):
                                            pass
                                
                                # Priority 3: Try to find from conversation history
                                # CRITICAL: Find the MOST RECENT update (not just the first one)
                                last_updated_from_history = None
                                if not last_updated_from_message and history:
                                    logger.info(f"🔍 [HISTORY PARSE] Analyzing {len(history)} messages in history to find most recent update")
                                    # Iterate through history in reverse (most recent first)
                                    for idx, msg in enumerate(reversed(history)):
                                        # Only check assistant messages (they contain update confirmations)
                                        role = msg.get("role", "").lower()
                                        if role != "assistant":
                                            logger.debug(f"🔍 [HISTORY PARSE] Skipping non-assistant message at position {idx}")
                                            continue
                                        
                                        content = msg.get("content", "")
                                        if isinstance(content, list):
                                            text_parts = []
                                            for item in content:
                                                if isinstance(item, dict):
                                                    text_parts.append(item.get("text", ""))
                                                elif isinstance(item, str):
                                                    text_parts.append(item)
                                            content = " ".join(text_parts)
                                        elif isinstance(content, dict):
                                            content = content.get("text", "")
                                        
                                        if not isinstance(content, str):
                                            continue
                                        
                                        # Look for update confirmation messages
                                        # Pattern: "✅ Section '11. Constraints' updated successfully"
                                        if "updated successfully" in content.lower() or ("✅" in content and "Section" in content):
                                            # Extract section number from message like "✅ Section '11. Constraints' updated successfully"
                                            section_match = re.search(r"Section\s+['\"](\d+)\.\s*([^'\"]+)['\"]", content, re.IGNORECASE)
                                            if section_match:
                                                try:
                                                    found_section = int(section_match.group(1))
                                                    found_title = section_match.group(2).strip()
                                                    # This is the most recent update confirmation we found
                                                    if not last_updated_from_history:
                                                        last_updated_from_history = found_section
                                                        logger.info(f"🔍 [HISTORY PARSE] ✅ Found most recent updated section from history (position {idx}): {last_updated_from_history} ({found_title})")
                                                        logger.info(f"🔍 [HISTORY PARSE] Full message content: {content[:200]}...")
                                                        # Break immediately since we're iterating in reverse (most recent first)
                                                        break
                                                    else:
                                                        logger.debug(f"🔍 [HISTORY PARSE] Already found section {last_updated_from_history}, skipping section {found_section}")
                                                except (ValueError, IndexError) as e:
                                                    logger.warning(f"🔍 [HISTORY PARSE] Error parsing section from message: {e}, content: {content[:100]}")
                                                    pass
                                            else:
                                                logger.debug(f"🔍 [HISTORY PARSE] Message contains 'updated successfully' but regex didn't match: {content[:100]}")
                                        else:
                                            logger.debug(f"🔍 [HISTORY PARSE] Assistant message at position {idx} is not an update confirmation: {content[:100] if len(content) < 100 else content[:100] + '...'}")
                                    
                                    if not last_updated_from_history:
                                        logger.warning(f"🔍 [HISTORY PARSE] ⚠️ No update confirmation found in {len(history)} history messages")
                                
                                # Use the most recent source (message > history)
                                section_num = last_updated_from_message or last_updated_from_history
                                
                                if section_num:
                                    logger.info("=" * 80)
                                    logger.info(f"🔍 [SHOW UPDATED] Using section: {section_num} (source: {'current message' if last_updated_from_message else 'history'})")
                                    logger.info(f"🔍 [SHOW UPDATED] About to call handle_show_section with section_number={section_num}")
                                    logger.info("=" * 80)
                                    assistant_response = handle_show_section(brd_data, section_num)
                                    logger.info(f"✅ [SHOW UPDATED] Successfully retrieved section {section_num}")
                                    logger.info(f"📄 [SHOW UPDATED] Response length: {len(assistant_response)} chars")
                                    logger.info(f"📄 [SHOW UPDATED] Response preview: {assistant_response[:200]}...")
                                else:
                                    logger.warning("⚠️ [SHOW UPDATED] No updated section found in current message or history")
                                    assistant_response = "I don't have information about which section was last updated. Please specify a section number, e.g., 'show section 4'."
                        else:
                            # Try to extract section number
                            match = re.search(r'show\s+(?:me\s+)?(?:section\s+)?(\d+)', user_message_clean.lower())
                            if not match:
                                match = re.search(r'section\s+(\d+)', user_message_clean.lower())
                            if match:
                                section_num = int(match.group(1))
                                assistant_response = handle_show_section(brd_data, section_num)
                            else:
                                # Try to find by section title (e.g., "show assumptions", "show constraints")
                                # Extract potential section title
                                show_match = re.search(r'show\s+(?:me\s+)?(?:section\s+)?(.+)', user_message_clean.lower())
                                if show_match:
                                    section_identifier = show_match.group(1).strip()
                                    section_num = _find_section_by_title_or_number(brd_data, section_identifier)
                                    if section_num:
                                        assistant_response = handle_show_section(brd_data, section_num)
                                    else:
                                        # Fallback: try to get number from "show N" format
                                        try:
                                            section_num = int(user_message_clean.split()[1])
                                            assistant_response = handle_show_section(brd_data, section_num)
                                        except (IndexError, ValueError):
                                            assistant_response = f"Could not find section '{section_identifier}'. Please use 'list' to see all sections or specify a section number."
                                else:
                                    # Fallback: try to get number from "show N" format
                                    try:
                                        section_num = int(user_message_clean.split()[1])
                                        assistant_response = handle_show_section(brd_data, section_num)
                                    except (IndexError, ValueError):
                                        assistant_response = "Usage: show <section number> or show section <number> or show <section name>"
                    except (IndexError, ValueError):
                        assistant_response = "Usage: show <section number> or show section <number> or show <section name>"

                # CRITICAL: Check if this is a general question BEFORE parsing as update command
                # Questions like "what updated i have made till now?" should go to Claude, not be parsed as commands
                is_question = False
                if not assistant_response:  # Only check if we haven't handled it yet
                    is_question = any(phrase in user_message_clean.lower() for phrase in [
                        'what updated', 'which section', 'what changes', 'what did i', 'what have i',
                        'show me what', 'tell me what', 'what sections', 'which sections'
                    ])
                    
                    # Also check if it's a question by looking for question words at the start
                    question_start = re.match(r'^(what|which|where|when|why|how|tell|show)\s+', user_message_clean.lower())
                    is_question = is_question or (question_start and ('updated' in user_message_clean.lower() or 'section' in user_message_clean.lower()))
                
                if is_question and not any(word in user_message_clean.lower() for word in ['change', 'replace', 'update', 'modify', 'edit']):
                    # This is a question, not an update command - route to Claude
                    logger.info(f"Detected question, routing to Claude: {user_message_clean[:100]}")
                    # Fall through to general question handling below
                    pass
                elif not assistant_response and any(word in user_message_clean.lower() for word in ['update', 'modify', 'edit', 'change', 'replace']):
                    # Handle various natural language update patterns
                    section_num = None
                    section_title = None
                    instruction = None
                    match = None
                    
                    # Pattern 1: "update section 4: change sarah to aman" or "update 4: change sarah to aman"
                    # Use DOTALL flag to match newlines in instruction
                    match = re.search(r'(?:update|modify|edit)\s+(?:section\s+)?(\d+)[:\s]+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                    if match:
                        section_num = int(match.group(1))
                        instruction = match.group(2).strip()
                        logger.info(f"Pattern 1 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 1b: "update section stakeholders: change sarah to aman" (section by title)
                    if not match:
                        match = re.search(r'(?:update|modify|edit)\s+section\s+([a-zA-Z\s]+?)[:\s]+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_title = match.group(1).strip()
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 1b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                    
                    # Pattern 2: "update section 4 sarah to aman" or "update 4 sarah to aman" (no colon, direct)
                    if not match:
                        match = re.search(r'(?:update|modify|edit)\s+(?:section\s+)?(\d+)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_num = int(match.group(1))
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 2 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 2b: "update section stakeholders sarah to aman" (section by title, no colon)
                    if not match:
                        match = re.search(r'(?:update|modify|edit)\s+section\s+([a-zA-Z\s]+?)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_title = match.group(1).strip()
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 2b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                    
                    # Pattern 3: "update in section 4 sarah to aman" or "update in section 4 change sarah to aman"
                    if not match:
                        match = re.search(r'(?:update|modify|edit)\s+in\s+section\s+(\d+)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_num = int(match.group(1))
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 3 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 3b: "update in section stakeholders sarah to aman"
                    if not match:
                        match = re.search(r'(?:update|modify|edit)\s+in\s+section\s+([a-zA-Z\s]+?)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_title = match.group(1).strip()
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 3b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                    
                    # Pattern 4: "in section 4 change sarah to aman" or "section 4 change sarah to aman"
                    if not match:
                        match = re.search(r'(?:in\s+)?section\s+(\d+).*?(?:change|replace|update|modify|edit)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_num = int(match.group(1))
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 4 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 4b: "in section stakeholders change sarah to aman"
                    if not match:
                        match = re.search(r'(?:in\s+)?section\s+([a-zA-Z\s]+?).*?(?:change|replace|update|modify|edit)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_title = match.group(1).strip()
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 4b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                    
                    # Pattern 5: "change sarah to aman in section 4"
                    if not match:
                        match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+?)\s+(?:to|with)\s+(.+?)\s+in\s+(?:section\s+)?(\d+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            old_text = match.group(1).strip()
                            new_text = match.group(2).strip()
                            section_num = int(match.group(3))
                            instruction = f"change {old_text} to {new_text}"
                            logger.info(f"Pattern 5 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 5b: "change sarah to aman in section stakeholders"
                    if not match:
                        match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+?)\s+(?:to|with)\s+(.+?)\s+in\s+section\s+([a-zA-Z\s]+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            old_text = match.group(1).strip()
                            new_text = match.group(2).strip()
                            section_title = match.group(3).strip()
                            instruction = f"change {old_text} to {new_text}"
                            logger.info(f"Pattern 5b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                    
                    # Pattern 6: "change X to Y" with section number elsewhere (most flexible fallback)
                    if not match:
                        # Look for "change X to Y" pattern and section number separately
                        change_match = re.search(r'change\s+(.+?)\s+to\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        section_match = re.search(r'section\s+(\d+)', user_message_clean, re.IGNORECASE)
                        if change_match and section_match:
                            old_text = change_match.group(1).strip()
                            new_text = change_match.group(2).strip()
                            section_num = int(section_match.group(1))
                            instruction = f"change {old_text} to {new_text}"
                            logger.info(f"Pattern 6 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 6b: "change X to Y" with section title elsewhere
                    if not match:
                        change_match = re.search(r'change\s+(.+?)\s+to\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        section_match = re.search(r'section\s+([a-zA-Z\s]+)', user_message_clean, re.IGNORECASE)
                        if change_match and section_match:
                            old_text = change_match.group(1).strip()
                            new_text = change_match.group(2).strip()
                            section_title = section_match.group(1).strip()
                            instruction = f"change {old_text} to {new_text}"
                            logger.info(f"Pattern 6b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                    
                    # Pattern 7: "in section N" followed by any text (catch-all for "update in section 4 sarah to aman")
                    if not match:
                        match = re.search(r'(?:in\s+)?section\s+(\d+)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_num = int(match.group(1))
                            instruction = match.group(2).strip()
                            logger.info(f"Pattern 7 matched: section={section_num}, instruction length={len(instruction)} chars")
                    
                    # Pattern 7b: "in section TITLE" followed by any text
                    # CRITICAL: Only match if there's a clear update command word BEFORE "section"
                    # This prevents matching "which section i have updated" as an update command
                    if not match:
                        # Require an update command word before "section" to avoid false matches
                        match = re.search(r'(?:update|modify|edit|change|replace)\s+(?:in\s+)?section\s+([a-zA-Z\s]{3,}?)\s+(.+)', user_message_clean, re.IGNORECASE | re.DOTALL)
                        if match:
                            section_title = match.group(1).strip()
                            instruction = match.group(2).strip()
                            # Additional validation: section title should be at least 3 chars and not be a question word
                            if len(section_title) >= 3 and section_title.lower() not in ['i', 'have', 'what', 'which', 'where', 'when', 'why', 'how']:
                                logger.info(f"Pattern 7b matched: section_title={section_title}, instruction length={len(instruction)} chars")
                            else:
                                match = None  # Reject this match
                    
                    # Pattern 8: "change X to Y here" or "update X to Y here" (context-based, use last shown section)
                    if not match and not section_num and not section_title:
                        if "here" in user_message_clean.lower():
                            logger.info("=" * 80)
                            logger.info("🔍 [STEP 3] PATTERN 8 MATCHED - Context-based update with 'here'")
                            logger.info(f"   User message: '{user_message_clean}'")
                            logger.info(f"   last_shown_section: {last_shown_section}")
                            logger.info(f"   last_shown_section_title: '{last_shown_section_title}'")
                            logger.info("=" * 80)
                            
                            # PRIORITY: Use section from current message if found
                            if last_shown_section:
                                logger.info("=" * 80)
                                logger.info("🔍 [STEP 4] FINDING SECTION BY TITLE FOR UPDATE")
                                logger.info(f"   last_shown_section (number): {last_shown_section}")
                                logger.info(f"   last_shown_section_title: '{last_shown_section_title}'")
                                
                                # CRITICAL: ALWAYS use section title to find the correct section if available
                                # This fixes the issue where section numbers don't match array indices
                                if last_shown_section_title and brd_data:
                                    logger.info(f"   🔎 Searching for section with title: '{last_shown_section_title}'")
                                    # Find section by title to get the correct index
                                    found_section_num = _find_section_by_title_or_number(brd_data, last_shown_section_title)
                                    logger.info(f"   🔎 _find_section_by_title_or_number returned: {found_section_num}")
                                    
                                    if found_section_num:
                                        # CRITICAL: _find_section_by_title_or_number returns section number accounting for document title
                                        # With document title: section 11 = array index 11
                                        # Without document title: section 11 = array index 10
                                        sections = brd_data.get("sections", [])
                                        has_doc_title = False
                                        if sections:
                                            first_title = sections[0].get("title", "").lower()
                                            if ("ai-powered" in first_title or "brd" in first_title or 
                                                (len(first_title) < 30 and not re.match(r'^\d+\.', first_title))):
                                                has_doc_title = True
                                        
                                        # Convert section number to array index
                                        if has_doc_title:
                                            # With document title: section 11 = array index 11
                                            array_index = found_section_num
                                            logger.info(f"   📋 Document title exists. Section {found_section_num} = array index {array_index}")
                                        else:
                                            # Without document title: section 11 = array index 10
                                            array_index = found_section_num - 1
                                            logger.info(f"   📋 No document title. Section {found_section_num} = array index {array_index}")
                                        
                                        # Verify the found section actually has the expected title
                                        found_section = sections[array_index] if array_index < len(sections) else None
                                        if found_section:
                                            found_title = re.sub(r'^\d+\.\s*', '', found_section.get("title", "")).strip().lower()
                                            expected_title = last_shown_section_title.lower().strip()
                                            logger.info(f"   📋 Found section #{found_section_num} (array index {array_index}) with title: '{found_section.get('title', '')}'")
                                            logger.info(f"   📋 Cleaned found title: '{found_title}'")
                                            logger.info(f"   📋 Expected title: '{expected_title}'")
                                            
                                            if expected_title in found_title or found_title in expected_title:
                                                logger.info(f"   ✅ Title match verified! Using section #{found_section_num}")
                                                section_num = found_section_num
                                            else:
                                                logger.warning(f"   ⚠️ Title mismatch! Expected '{last_shown_section_title}', found '{found_section.get('title', '')}'. Using title-based search result anyway.")
                                                section_num = found_section_num
                                        else:
                                            logger.warning(f"   ⚠️ Could not retrieve section at array index {array_index}, using section number {last_shown_section}")
                                            section_num = last_shown_section
                                    else:
                                        logger.warning(f"   ⚠️ Could not find section by title '{last_shown_section_title}', using section number {last_shown_section}")
                                        section_num = last_shown_section
                                else:
                                    logger.warning(f"   ⚠️ No section title stored (last_shown_section_title={last_shown_section_title}), using section number {last_shown_section}")
                                    section_num = last_shown_section
                                
                                logger.info(f"   ✅ FINAL DECISION: section_num = {section_num}")
                                logger.info("=" * 80)
                                
                                # Extract the change instruction
                                change_match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+?)\s+(?:to|with)\s+(.+)', user_message_clean, re.IGNORECASE)
                                if change_match:
                                    old_text = change_match.group(1).strip()
                                    new_text = change_match.group(2).strip()
                                    instruction = f"change {old_text} to {new_text}"
                                    logger.info(f"Pattern 8 (context-based) matched: section={section_num} (from title '{last_shown_section_title}' or number {last_shown_section}), instruction={instruction}")
                                else:
                                    # Just "change X to Y here" without explicit "to"
                                    change_match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+)', user_message_clean, re.IGNORECASE)
                                    if change_match:
                                        instruction = change_match.group(1).strip()
                                        logger.info(f"Pattern 8 (context-based, simple) matched: section={section_num} (from title '{last_shown_section_title}' or number {last_shown_section}), instruction={instruction}")
                            else:
                                logger.warning(f"User said 'here' but no last_shown_section found. Message: '{user_message_clean[:100]}', History length: {len(history) if history else 0}")
                                # Try to find section from the instruction itself as a last resort
                                # Extract what they want to change and try to find it in sections
                                change_match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+?)\s+(?:to|with)\s+(.+)', user_message_clean, re.IGNORECASE)
                                if not change_match:
                                    change_match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+)', user_message_clean, re.IGNORECASE)
                                if change_match:
                                    logger.info(f"Attempting to find section by searching for change target in BRD sections...")
                                    # This is a fallback - we'll let Claude handle it if we can't find the section
                    
                    # If we found a section title, convert it to section number
                    if section_title and not section_num:
                        section_num = _find_section_by_title_or_number(brd_data, section_title)
                        if section_num:
                            logger.info(f"Found section number {section_num} for title '{section_title}'")
                        else:
                            logger.warning(f"Could not find section with title '{section_title}'")
                    
                    if section_num and instruction:
                        # Ensure section_num is an integer
                        try:
                            section_num = int(section_num)
                        except (ValueError, TypeError):
                            logger.error(f"Section number is not a valid integer: {section_num} (type: {type(section_num)})")
                            assistant_response = f"Invalid section number format: {section_num}. Please specify a number."
                        else:
                            logger.info(f"Final parsed update command: section {section_num} (type: {type(section_num)}), instruction: '{instruction}'")
                            logger.info(f"BRD has {len(brd_data.get('sections', []))} sections")
                            logger.info(f"Section number validation: {section_num} >= 1: {section_num >= 1}, {section_num} <= {len(brd_data.get('sections', []))}: {section_num <= len(brd_data.get('sections', []))}")
                            result = handle_update_section(brd_data, section_num, instruction, history)
                            assistant_response = result["message"]
                            logger.info(f"Update result: success={result.get('success')}, message={result.get('message')[:100]}")

                            if result["success"]:
                                # Save updated BRD to S3
                                updated_brd = result["updated_brd"]
                                try:
                                    save_brd_to_s3(brd_id, updated_brd)
                                    logger.info(f"✅ Successfully saved BRD structure to S3 for {brd_id}")
                                except Exception as s3_err:
                                    logger.error(f"CRITICAL: Failed to save BRD structure to S3: {s3_err}")
                                    assistant_response = f"Error: Failed to save BRD update. {str(s3_err)}"
                                    # Don't mark as updated if save failed
                                else:
                                    # Only save text file if structure save succeeded
                                    try:
                                        save_brd_text_to_s3(brd_id, updated_brd)
                                        logger.info(f"✅ Successfully updated BRD text file for {brd_id}")
                                    except Exception as text_err:
                                        logger.error(f"CRITICAL: Failed to update BRD text file: {text_err}")
                                        # Still mark as updated but log the error
                                    # Track which section was updated for "show me updated section"
                                    last_updated_section = section_num
                                    # Also store in the response message so it can be tracked in history
                                    if "✅" not in assistant_response:
                                        section_title = brd_data.get("sections", [])[section_num-1].get("title", "Untitled")
                                        assistant_response = f"✅ Section '{section_num}. {section_title}' updated successfully\n\n{assistant_response}"
                                    brd_updated = True
                    else:
                        # Failed to parse update command - log with safe variable access
                        section_num_str = str(section_num) if section_num is not None else "None"
                        instruction_str = str(instruction) if instruction is not None else "None"
                        logger.warning(f"Failed to parse update command. section_num={section_num_str}, instruction={instruction_str}, message='{user_message_clean}'")
                        logger.info(f"Attempting Pattern 5 match test for: '{user_message_clean}'")
                        # Try Pattern 5 explicitly as a fallback
                        pattern5_match = re.search(r'(?:change|replace|update|modify|edit)\s+(.+?)\s+(?:to|with)\s+(.+?)\s+in\s+(?:section\s+)?(\d+)', user_message_clean, re.IGNORECASE)
                        if pattern5_match:
                            old_text = pattern5_match.group(1).strip()
                            new_text = pattern5_match.group(2).strip()
                            section_num = int(pattern5_match.group(3))
                            instruction = f"change {old_text} to {new_text}"
                            logger.info(f"Pattern 5 fallback matched: section={section_num}, instruction={instruction}")
                            # Execute the update
                            try:
                                section_num = int(section_num)
                                result = handle_update_section(brd_data, section_num, instruction, history)
                                assistant_response = result["message"]
                                logger.info(f"Update result: success={result.get('success')}, message={result.get('message')[:100]}")
                                if result["success"]:
                                    updated_brd = result["updated_brd"]
                                    try:
                                        save_brd_to_s3(brd_id, updated_brd)
                                        logger.info(f"✅ Successfully saved BRD structure to S3 for {brd_id}")
                                    except Exception as s3_err:
                                        logger.error(f"CRITICAL: Failed to save BRD structure to S3: {s3_err}")
                                        assistant_response = f"Error: Failed to save BRD update. {str(s3_err)}"
                                    else:
                                        # Only save text file if structure save succeeded
                                        try:
                                            save_brd_text_to_s3(brd_id, updated_brd)
                                            logger.info(f"✅ Successfully updated BRD text file for {brd_id}")
                                        except Exception as text_err:
                                            logger.error(f"CRITICAL: Failed to update BRD text file: {text_err}")
                                        # Track which section was updated
                                        last_updated_section = section_num
                                        brd_updated = True
                            except Exception as e:
                                logger.error(f"Error in Pattern 5 fallback update: {e}")
                                assistant_response = f"Error processing update: {str(e)}"
                        else:
                            if section_title:
                                assistant_response = f"Section '{section_title}' not found. Use 'list' to see all sections."
                            else:
                                assistant_response = f"Could not parse update command. Please specify the section number or name and what to change. Examples:\n- update section 4: change sarah to aman\n- update section stakeholders: change sarah to aman\n- in section 4 change sarah to aman\n- change sarah to aman in section 4\n- update sarah to aman in section stakeholders"
                
                # If we detected a question but didn't handle it above, route to Claude
                if is_question and not assistant_response:
                    # Fall through to general question handling
                    pass
                elif assistant_response:
                    # We already handled it (update command or show command)
                    pass
                else:
                    # This shouldn't happen, but route to general question handling
                    pass

            # Handle general questions (including those that weren't parsed as commands)
            if not assistant_response or (is_question and not brd_updated):
                # General question or greeting - use Claude with context
                # Also track last updated section from history for questions about updates
                # CRITICAL: Only check assistant messages and find the MOST RECENT update
                if not last_updated_section and history:
                    for msg in reversed(history):
                        # Only check assistant messages (they contain update confirmations)
                        role = msg.get("role", "").lower()
                        if role != "assistant":
                            continue
                        
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text_parts = []
                            for item in content:
                                if isinstance(item, dict):
                                    text_parts.append(item.get("text", ""))
                                elif isinstance(item, str):
                                    text_parts.append(item)
                            content = " ".join(text_parts)
                        elif isinstance(content, dict):
                            content = content.get("text", "")
                        
                        if not isinstance(content, str):
                            continue
                        
                        # Look for update confirmation messages
                        # Pattern: "✅ Section '11. Constraints' updated successfully"
                        if "updated successfully" in content.lower() or ("✅" in content and "Section" in content):
                            # Extract section number from message like "✅ Section '11. Constraints' updated successfully"
                            section_match = re.search(r"Section\s+['\"](\d+)\.", content, re.IGNORECASE)
                            if section_match:
                                try:
                                    last_updated_section = int(section_match.group(1))
                                    logger.info(f"Found most recent updated section from history: {last_updated_section}")
                                    # Break immediately since we're iterating in reverse (most recent first)
                                    break
                                except ValueError:
                                    pass
                # Check if it's a simple greeting or non-command message
                user_msg_lower = user_message_clean.lower().strip()
                is_greeting = user_msg_lower in ['hi', 'hello', 'hey', 'help', '?', 'help me']
                
                if is_greeting or len(user_message_clean.strip()) < 10:
                    # Handle greetings and short messages
                    if is_greeting:
                        assistant_response = f"""Hello! I'm your BRD assistant. I can help you with:

- **List sections**: "list" or "show all sections"
- **View a section**: "show section 4" or "show me section 4" or "show stakeholders"
- **Update a section**: "change X to Y in section 4" or "update section 4: change X to Y"
- **Context-aware updates**: When viewing a section, say "change X to Y here" to update that section

Current BRD has {len(brd_data.get('sections', []))} sections.

What would you like to do?"""
                    else:
                        # Very short message - ask for clarification
                        assistant_response = "I'm here to help with your BRD. You can:\n- List sections: 'list'\n- Show a section: 'show section 4'\n- Update a section: 'change X to Y in section 4'\n\nWhat would you like to do?"
                else:
                    # General question - use Claude with context
                    # CRITICAL: For questions about updates, analyze FULL history to find ALL update confirmations
                    is_update_question = any(phrase in user_message_clean.lower() for phrase in [
                        "which sections", "what sections", "how many sections", "sections i have updated",
                        "sections updated", "sections changed", "sections modified", "sections edited"
                    ])
                    
                    # Build comprehensive update history if user is asking about updates
                    all_updated_sections = []
                    if is_update_question and history:
                        logger.info("Analyzing full conversation history to find all updated sections...")
                        for msg in reversed(history):
                            role = msg.get("role", "").lower()
                            if role != "assistant":
                                continue
                            
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                text_parts = []
                                for item in content:
                                    if isinstance(item, dict):
                                        text_parts.append(item.get("text", ""))
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                content = " ".join(text_parts)
                            elif isinstance(content, dict):
                                content = content.get("text", "")
                            
                            if not isinstance(content, str):
                                continue
                            
                            # Look for update confirmation messages
                            if "updated successfully" in content.lower() or ("✅" in content and "Section" in content):
                                section_match = re.search(r"Section\s+['\"](\d+)\.\s*([^'\"]+)", content, re.IGNORECASE)
                                if section_match:
                                    try:
                                        section_num = int(section_match.group(1))
                                        section_title = section_match.group(2).strip()
                                        # Avoid duplicates
                                        if not any(s["number"] == section_num for s in all_updated_sections):
                                            all_updated_sections.append({
                                                "number": section_num,
                                                "title": section_title
                                            })
                                            logger.info(f"Found updated section: {section_num} - {section_title}")
                                    except (ValueError, IndexError):
                                        pass
                    
                    # Include conversation history to help Claude understand context
                    history_context = ""
                    if history:
                        # For update questions, use more history; otherwise use recent messages
                        num_messages = len(history) if is_update_question else min(10, len(history))
                        recent_messages = history[-num_messages:] if not is_update_question else history
                        history_context = "\n\nConversation history:\n"
                        for msg in recent_messages:
                            role = msg.get("role", "user")
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                text = " ".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in content)
                            elif isinstance(content, dict):
                                text = content.get("text", "")
                            else:
                                text = str(content)
                            # Truncate to avoid token limits, but keep update confirmations
                            text_preview = text[:300] if not ("updated successfully" in text.lower() or "✅" in text) else text[:500]
                            history_context += f"{role.capitalize()}: {text_preview}\n"
                    
                    # Build context about updated sections
                    update_context = ""
                    if all_updated_sections:
                        # Sort by section number
                        all_updated_sections.sort(key=lambda x: x["number"])
                        sections_list = ", ".join([f"Section {s['number']} ({s['title']})" for s in all_updated_sections])
                        update_context = f"\n\nAll updated sections found in conversation history:\n{sections_list}\nTotal: {len(all_updated_sections)} section(s)"
                    elif last_updated_section:
                        try:
                            updated_section = brd_data.get("sections", [])[last_updated_section - 1]
                            update_context = f"\n\nLast updated section: Section {last_updated_section} - {updated_section.get('title', 'Untitled')}"
                        except (IndexError, ValueError):
                            pass
                    
                    prompt = f"""You are a BRD assistant helping the user work with their Business Requirements Document.

Current BRD has {len(brd_data.get('sections', []))} sections.{update_context}
{history_context}
User's current message: "{user_message_clean}"

Available commands:
- list: Show all sections
- show N: Display section N (e.g., "show section 4" or "show 4")
- update N: instruction: Modify section N (e.g., "update section 4: change X to Y")

CRITICAL INSTRUCTIONS:
- If the user asks "which sections have I updated" or "how many sections have I updated", you MUST list ALL sections that were updated based on the conversation history above.
- Look for messages containing "✅ Section 'X. Title' updated successfully" in the conversation history.
- Provide a clear, numbered list of all updated sections with their titles.
- If no update information is found, say so clearly.

If the user is asking about updates they made, reference the conversation history and the updated sections information above.
If the user is asking about a section they recently viewed or updated, reference that context from the conversation history.

Please provide a helpful, friendly response."""

                    try:
                        assistant_response = invoke_claude_for_chat(prompt, history)
                    except Exception as e:
                        logger.error(f"Error invoking Claude for general question: {e}")
                        assistant_response = f"I understand you're asking: '{user_message_clean}'. Could you be more specific? I can help you list sections, view sections, or update sections in your BRD."

            # Add assistant response to memory
            add_message_to_memory(session_id, "assistant", assistant_response)

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "response": assistant_response,
                    "brd_updated": brd_updated
                })
            }

        # GET HISTORY
        elif action == "get_history":
            session_id = event.get("session_id")

            if not session_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "session_id is required"})
                }

            history = get_session_history(session_id)

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "messages": history
                })
            }

        # DELETE SESSION
        elif action == "delete_session":
            session_id = event.get("session_id")

            if not session_id:
                return {
                    "statusCode": 400,
                    "body": json.dumps({"error": "session_id is required"})
                }

            success = delete_memory_session(session_id)

            return {
                "statusCode": 200,
                "body": json.dumps({
                    "success": success,
                    "message": "Session deleted" if success else "Failed to delete session"
                })
            }

        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Unknown action: {action}"})
            }

    except Exception as e:
        logger.error(f"Lambda execution error: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "Internal server error",
                "message": str(e)
            })
        }
