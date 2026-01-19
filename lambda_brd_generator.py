import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

import boto3

# Configure logging for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_REGION = os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION", "us-east-1")
# Claude Sonnet 4.5 has 200K token context window TOTAL (input + output)
# Reserve ~50K tokens for prompt (instructions + template + transcript)
# This leaves ~150K tokens for generation
# IMPORTANT: Can set higher for longer BRDs, but 8192 is a safe default
MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "8192"))
TEMPERATURE = float(os.getenv("BEDROCK_TEMPERATURE", "0"))

_bedrock_runtime = None


def _get_bedrock_client():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_runtime


def _coerce_event(event: Any) -> Dict[str, Any]:
    if isinstance(event, dict):
        return event
    if isinstance(event, str):
        try:
            return json.loads(event)
        except json.JSONDecodeError:
            return {"message": event}
    return {}


def _truncate_text(text: str, max_chars: int) -> str:
    """
    Truncate text to max_chars, trying to cut at sentence boundaries.
    
    Args:
        text: Text to truncate
        max_chars: Maximum characters allowed
        
    Returns:
        Truncated text (with ellipsis if truncated)
    """
    if len(text) <= max_chars:
        return text
    
    # Try to cut at sentence boundary
    truncated = text[:max_chars]
    last_period = truncated.rfind('.')
    last_newline = truncated.rfind('\n')
    cut_point = max(last_period, last_newline)
    
    if cut_point > max_chars * 0.8:  # Only use sentence boundary if it's not too early
        return truncated[:cut_point + 1] + "\n\n[... transcript truncated for length ...]"
    else:
        return truncated + "\n\n[... transcript truncated for length ...]"


def _convert_brd_text_to_structure(brd_text: str) -> Optional[Dict]:
    """
    Convert plain-text BRD into structured JSON format.
    This is a simplified version that creates a basic structure from the text.
    """
    try:
        import re
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
            
            # Look for section headers (numbered sections like "1. Title" or "## Title" or "SECTION 4:")
            # CRITICAL: Only recognize sections 1-16. Ignore numbered items beyond 16 (they're sub-items within sections)
            section_match = re.match(r'^(?:SECTION\s+)?(\d+)\.?\s*(.+)$', line, re.IGNORECASE)
            if section_match:
                section_num = int(section_match.group(1))
                # Only treat as section if it's 1-16 (the main BRD sections)
                # Numbers 17+ are likely sub-items, flow steps, or use case details within a section
                if section_num > 16:
                    # This is a sub-item, not a section - treat as content
                    if current_section:
                        current_content.append(line)
                    continue
                
                # Also check if this looks like a document title (usually the first line without "Document Overview" etc.)
                title_text = section_match.group(2).strip()
                # Skip if it's likely a document title (contains "AI-Powered" or similar, and we haven't seen "Document Overview" yet)
                if section_num == 1 and not any(sec.get('title', '').lower().startswith('document overview') for sec in sections):
                    # Check if this looks like a document title rather than a section
                    if 'ai-powered' in title_text.lower() or 'brd' in title_text.lower() or len(title_text) < 30:
                        # Likely document title - skip it, don't create a section
                        if current_section:
                            current_content.append(line)
                        continue
            
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
                # Check if line is a table row (contains | or tabs)
                if '|' in line or '\t' in line:
                    # Try to parse as table
                    if '|' in line:
                        cells = [cell.strip() for cell in line.split('|') if cell.strip()]
                    else:
                        cells = [cell.strip() for cell in line.split('\t') if cell.strip()]
                    
                    if cells and len(cells) > 1:
                        # Check if we have a table block already
                        if current_section['content'] and current_section['content'][-1].get('type') == 'table':
                            current_section['content'][-1]['rows'].append(cells)
                        else:
                            # Start new table
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
                
                # Check if line is a bullet point
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
        
        if sections:
            return {"sections": sections}
        return None
    except Exception as e:
        logger.error(f"Failed to convert BRD text to structure: {e}")
        return None


def _extract_text_from_docx(docx_bytes: bytes) -> str:
    """
    Extract text from DOCX file using pure Python (no external dependencies).
    
    DOCX files are ZIP archives containing XML files. This function:
    1. Extracts the ZIP
    2. Reads word/document.xml
    3. Parses XML to extract text content
    
    Args:
        docx_bytes: Raw bytes of the DOCX file
        
    Returns:
        Extracted text content
    """
    import zipfile
    import xml.etree.ElementTree as ET
    import io
    
    try:
        # DOCX is a ZIP archive
        zip_file = zipfile.ZipFile(io.BytesIO(docx_bytes))
        
        # Read the main document XML
        document_xml = zip_file.read('word/document.xml')
        
        # Parse XML
        root = ET.fromstring(document_xml)
        
        # Define namespace (DOCX uses specific namespaces)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        
        # Extract all text from paragraphs
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
        raise RuntimeError(f"Failed to extract text from DOCX: {e}")


def _invoke_bedrock(prompt: str, max_tokens: int = None) -> str:
    """
    Invoke Bedrock model to generate BRD.
    
    Args:
        prompt: The full prompt text
        max_tokens: Maximum tokens to generate. If None, uses MAX_TOKENS env var.
    """
    client = _get_bedrock_client()
    model_id = BEDROCK_MODEL_ID
    effective_max_tokens = max_tokens if max_tokens is not None else MAX_TOKENS

    # Log configuration
    logger.info(f"Using model: {model_id}")
    logger.info(f"Max tokens: {effective_max_tokens}, Temperature: {TEMPERATURE}")
    logger.info(f"Prompt length: {len(prompt)} characters (~{len(prompt)//4} tokens estimated)")

    if model_id.startswith("anthropic.") or model_id.startswith("global.anthropic."):
        # Use converse() API which supports inference profiles
        logger.info("Using converse() API for Anthropic model")
        
        response = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ],
            inferenceConfig={
                "maxTokens": effective_max_tokens,
                "temperature": TEMPERATURE,
            }
        )

        # Log the response structure
        logger.info(f"Response keys: {list(response.keys())}")
        logger.info(f"Stop reason: {response.get('stopReason', 'N/A')}")

        # Extract text from converse() response format
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        
        brd_text = "".join(
            block.get("text", "")
            for block in content_blocks
            if "text" in block
        )

        logger.info(f"Generated BRD length: {len(brd_text)} characters")

        # Check if response was truncated due to token limit
        if response.get("stopReason") == "max_tokens":
            logger.warning("Response was truncated due to max_tokens limit!")
            raise RuntimeError(
                f"Model hit max_tokens limit ({effective_max_tokens}). "
                f"Increase BEDROCK_MAX_TOKENS or simplify the prompt/template. "
                f"Generated {len(brd_text)} characters before truncation."
            )
    elif model_id.startswith("amazon.titan-text"):
        payload = {
            "inputText": prompt,
            "textGenerationConfig": {
                "temperature": TEMPERATURE,
                "maxTokenCount": effective_max_tokens,
                "topP": 0.9,
                "stopSequences": [],
            },
        }
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(payload).encode("utf-8"),
        )
        response_body = json.loads(response["body"].read())

        # Log the response structure
        logger.info(f"Response body keys: {list(response_body.keys())}")

        results = response_body.get("results", [])
        if results:
            completion_reason = results[0].get("completionReason", "N/A")
            logger.info(f"Completion reason: {completion_reason}")

        brd_text = "".join(result.get("outputText", "") for result in results)

        logger.info(f"Generated BRD length: {len(brd_text)} characters")

        # Check if response was truncated due to token limit
        if results and results[0].get("completionReason") == "LENGTH":
            logger.warning("Response was truncated due to LENGTH limit!")
            raise RuntimeError(
                f"Model hit max token limit ({effective_max_tokens}). "
                f"Increase BEDROCK_MAX_TOKENS or simplify the prompt/template. "
                f"Generated {len(brd_text)} characters before truncation."
            )
    elif "llama" in model_id.lower():
        payload = {
            "prompt": prompt,
            "max_gen_len": effective_max_tokens,
            "temperature": TEMPERATURE,
            "top_p": 0.9,
        }
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(payload).encode("utf-8"),
        )
        response_body = json.loads(response["body"].read())

        # Log the response structure
        logger.info(f"Response body keys: {list(response_body.keys())}")
        logger.info(f"Stop reason: {response_body.get('stop_reason', 'N/A')}")

        brd_text = response_body.get("generation", "")

        logger.info(f"Generated BRD length: {len(brd_text)} characters")

        # Check if response was truncated due to token limit
        if response_body.get("stop_reason") == "length":
            logger.warning(f"Response was truncated due to length limit! Generated {len(brd_text)} characters.")
            # ALWAYS return the partial BRD, never raise an error
            # Even if it's short, return what we have - better than an error message
            if brd_text and len(brd_text.strip()) > 50:
                logger.info("Returning partial BRD (truncated due to token limit)")
                # Add a note at the end
                brd_text += "\n\n[Note: This BRD was truncated due to token limits. Consider reducing the template or transcript length for a complete BRD.]"
            else:
                # If we got very little, still return it but with a warning
                logger.warning("Generated very little content, but returning it anyway")
                brd_text = brd_text or "[BRD generation started but was cut short due to token limits. Please reduce input size.]"
    else:
        raise RuntimeError(
            f"Model '{model_id}' is not supported. "
            "Use an Anthropic Claude 3, Amazon Titan, or Meta Llama model."
        )

    if not brd_text or not brd_text.strip():
        logger.error("Model response was empty or whitespace-only!")
        logger.error(f"brd_text value: '{brd_text[:200] if brd_text else 'None'}'")
        # Don't raise error - return a placeholder BRD instead
        # This prevents error messages from being saved as BRD content
        brd_text = "[BRD generation failed: Model returned empty response. Please check input and try again.]"
        logger.warning("Returning placeholder BRD instead of error")

    logger.info("BRD generation successful!")
    return brd_text


def lambda_handler(event, context):
    """
    Lambda handler for BRD generation.
    
    This function is called by:
    1. Bedrock Agent (Agent Mode) - expects Bedrock Agent response format
    2. AgentCore Gateway (Direct Mode) - expects simple JSON with 'brd' field
    
    CRITICAL: Always returns a valid response, even on errors.
    """
    logger.info("=== BRD Generator Lambda Started ===")
    logger.info(f"Received event type: {type(event)}")
    logger.info(f"Received event: {json.dumps(event, default=str)[:1000]}")
    
    evt = _coerce_event(event)

    # Log the FULL event structure for debugging (no truncation)
    logger.info("=" * 80)
    logger.info("=== FULL EVENT STRUCTURE DEBUG ===")
    logger.info("=" * 80)
    try:
        if isinstance(evt, dict):
            logger.info(f"Event is a dict with {len(evt)} keys")
            logger.info(f"Event keys: {list(evt.keys())}")
            
            # Log each key-value pair separately
            for key, value in evt.items():
                if isinstance(value, (dict, list)):
                    logger.info(f"  {key}: {type(value).__name__} with {len(value)} items")
                    if isinstance(value, dict):
                        logger.info(f"    Sub-keys: {list(value.keys())}")
                        # Check for actionGroupInput
                        if key == "actionGroupInput" or "actionGroupInput" in str(value):
                            logger.info(f"    Found actionGroupInput structure!")
                            if isinstance(value, dict) and "actionGroupInput" in value:
                                logger.info(f"    actionGroupInput keys: {list(value['actionGroupInput'].keys())}")
                else:
                    value_str = str(value)
                    if len(value_str) > 200:
                        logger.info(f"  {key}: {value_str[:200]}... (truncated, length: {len(value_str)})")
                    else:
                        logger.info(f"  {key}: {value_str}")
            
            # Try to find brd_id in various locations
            logger.info("--- Searching for brd_id ---")
            if "brd_id" in evt:
                logger.info(f"  Found brd_id at top level: {evt['brd_id']}")
            if "brdId" in evt:
                logger.info(f"  Found brdId at top level: {evt['brdId']}")
            if "actionGroupInput" in evt:
                ag_input = evt["actionGroupInput"]
                if isinstance(ag_input, dict):
                    if "brd_id" in ag_input:
                        logger.info(f"  Found brd_id in actionGroupInput: {ag_input['brd_id']}")
                    if "brdId" in ag_input:
                        logger.info(f"  Found brdId in actionGroupInput: {ag_input['brdId']}")
            if "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    if "brd_id" in params:
                        logger.info(f"  Found brd_id in parameters dict: {params['brd_id']}")
                elif isinstance(params, list):
                    logger.info(f"  parameters is a list with {len(params)} items")
                    for i, param in enumerate(params):
                        if isinstance(param, dict):
                            if param.get("name") == "brd_id" or param.get("key") == "brd_id":
                                logger.info(f"  Found brd_id in parameters[{i}]: {param.get('value')}")
        else:
            logger.info(f"Event is NOT a dict, it's: {type(evt)}")
            logger.info(f"Event value: {str(evt)[:500]}")
    except Exception as e:
        logger.error(f"Failed to log event structure: {e}", exc_info=True)
    logger.info("=" * 80)

    # Handle agent invocation format
    # Bedrock Agent passes parameters in different formats:
    # 1. Direct: {"template": "...", "transcript": "...", "brd_id": "..."}
    # 2. Nested: {"parameters": {"template": "...", "transcript": "...", "brd_id": "..."}}
    # 3. List format: {"parameters": [{"name": "template", "value": "..."}, ...]}
    # 4. Action group format: {"actionGroupInput": {"template": "...", "transcript": "...", "brd_id": "..."}}
    # 5. NEW: S3 keys format: {"template_s3_bucket": "...", "template_s3_key": "...", "transcript_s3_bucket": "...", "transcript_s3_key": "..."}
    
    template_text = None
    transcript_text = None
    brd_id = None
    template_s3_bucket = None
    template_s3_key = None
    transcript_s3_bucket = None
    transcript_s3_key = None
    
    # First, try direct access
    if isinstance(evt, dict):
        template_text = evt.get("template") or evt.get("template_text")
        transcript_text = evt.get("transcript") or evt.get("transcript_text")
        brd_id = evt.get("brd_id") or evt.get("brdId")
        
        # Check for S3 keys (new approach - files in S3, not in message)
        template_s3_bucket = evt.get("template_s3_bucket")
        template_s3_key = evt.get("template_s3_key")
        transcript_s3_bucket = evt.get("transcript_s3_bucket")
        transcript_s3_key = evt.get("transcript_s3_key")
        
        # Check actionGroupInput format (Bedrock Agent format)
        if not template_text and "actionGroupInput" in evt:
            logger.info("Detected actionGroupInput format")
            action_input = evt["actionGroupInput"]
            if isinstance(action_input, dict):
                template_text = action_input.get("template") or action_input.get("template_text")
                transcript_text = action_input.get("transcript") or action_input.get("transcript_text")
                if not brd_id:
                    brd_id = action_input.get("brd_id") or action_input.get("brdId")
                
                # Check for S3 keys in actionGroupInput
                if not template_s3_bucket:
                    template_s3_bucket = action_input.get("template_s3_bucket")
                    template_s3_key = action_input.get("template_s3_key")
                    transcript_s3_bucket = action_input.get("transcript_s3_bucket")
                    transcript_s3_key = action_input.get("transcript_s3_key")
        
        # If not found, check nested "parameters" key
        if not template_text and "parameters" in evt:
            logger.info("Detected agent invocation format with 'parameters' key")
            params = evt["parameters"]
            
            # Handle dict format: {"parameters": {"template": "...", "transcript": "...", "brd_id": "..."}}
            if isinstance(params, dict):
                template_text = params.get("template") or params.get("template_text")
                transcript_text = params.get("transcript") or params.get("transcript_text")
                if not brd_id:
                    brd_id = params.get("brd_id") or params.get("brdId")
            
            # Handle list format: {"parameters": [{"name": "template", "value": "..."}, ...]}
            elif isinstance(params, list):
                logger.info(f"Parameters is a list with {len(params)} items")
                for param in params:
                    if isinstance(param, dict):
                        param_name = param.get("name") or param.get("key")
                        param_value = param.get("value") or param.get("val")
                        
                        if param_name == "template" or param_name == "template_text":
                            template_text = param_value
                        elif param_name == "transcript" or param_name == "transcript_text":
                            transcript_text = param_value
                        elif param_name == "brd_id" or param_name == "brdId":
                            brd_id = param_value
                        elif param_name == "template_s3_bucket":
                            template_s3_bucket = param_value
                        elif param_name == "template_s3_key":
                            template_s3_key = param_value
                        elif param_name == "transcript_s3_bucket":
                            transcript_s3_bucket = param_value
                        elif param_name == "transcript_s3_key":
                            transcript_s3_key = param_value
                
                # Also try direct list access (if list contains dicts with keys)
                if not template_text and not transcript_text:
                    for item in params:
                        if isinstance(item, dict):
                            if "template" in item:
                                template_text = item.get("template") or item.get("template_text")
                            if "transcript" in item:
                                transcript_text = item.get("transcript") or item.get("transcript_text")
    
    # If evt itself is a list (unlikely but handle it)
    elif isinstance(evt, list):
        logger.warning("Event is a list, trying to extract from items")
        for item in evt:
            if isinstance(item, dict):
                if not template_text:
                    template_text = item.get("template") or item.get("template_text")
                if not transcript_text:
                    transcript_text = item.get("transcript") or item.get("transcript_text")
                if template_text and transcript_text:
                    break
    
    # If we have S3 keys but not text, fetch from S3
    if (template_s3_bucket and template_s3_key) and not template_text:
        logger.info(f"Fetching template from S3: s3://{template_s3_bucket}/{template_s3_key}")
        try:
            s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            template_obj = s3_client.get_object(Bucket=template_s3_bucket, Key=template_s3_key)
            template_bytes = template_obj["Body"].read()
            
            # Extract text from DOCX or plain text
            if template_s3_key.endswith(".docx"):
                template_text = _extract_text_from_docx(template_bytes)
            else:
                template_text = template_bytes.decode("utf-8", errors="replace")
            logger.info(f"Successfully fetched template from S3: {len(template_text)} characters")
        except Exception as e:
            logger.error(f"Failed to fetch template from S3: {e}", exc_info=True)
            raise RuntimeError(f"Failed to fetch template from S3: {e}")
    
    if (transcript_s3_bucket and transcript_s3_key) and not transcript_text:
        logger.info(f"Fetching transcript from S3: s3://{transcript_s3_bucket}/{transcript_s3_key}")
        try:
            s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
            transcript_obj = s3_client.get_object(Bucket=transcript_s3_bucket, Key=transcript_s3_key)
            transcript_bytes = transcript_obj["Body"].read()
            
            # Extract text from DOCX or plain text
            if transcript_s3_key.endswith(".docx"):
                transcript_text = _extract_text_from_docx(transcript_bytes)
            else:
                transcript_text = transcript_bytes.decode("utf-8", errors="replace")
            logger.info(f"Successfully fetched transcript from S3: {len(transcript_text)} characters")
        except Exception as e:
            logger.error(f"Failed to fetch transcript from S3: {e}", exc_info=True)
            raise RuntimeError(f"Failed to fetch transcript from S3: {e}")
    
    logger.info(f"Extracted template length: {len(template_text) if template_text else 0} characters")
    logger.info(f"Extracted transcript length: {len(transcript_text) if transcript_text else 0} characters")
    logger.info(f"Extracted brd_id: {brd_id if brd_id else 'NOT FOUND - S3 SAVE WILL BE SKIPPED!'}")

    if not template_text or not transcript_text:
        logger.error("Missing required fields: template or transcript")
        # Return error in Bedrock Agent expected format
        return {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": "Both 'template' and 'transcript' fields are required."
                    }
                }
            }
        }

    # Build base prompt (instructions without template/transcript)
    prompt_base = """You are a Product Manager in a software solutions company for payments. A discussion has happened within a product team, and the meeting transcript is available. You are tasked with creating a Business Requirements Document (BRD).

Below is the template structure that must be followed exactly.

### Utmost IMPORTANT
"Keep the BRD concise to fit within available tokens"
"Use bullet points and tables where possible instead of long paragraphs"
"Prioritize covering ALL 16 sections concisely rather than detailed elaboration"
"Be brief but comprehensive - quality over quantity"


### CRITICAL: The BRD MUST contain ALL 16 sections listed below:"""
    
    prompt_instructions_section = f"""{prompt_base}
1. Document Overview
2. Purpose
3. Background / Context
4. Stakeholders
5. Scope
6. Business Objectives & ROI
7. Functional Requirements
8. Non-Functional Requirements
9. User Stories / Use Cases
10. Assumptions
11. Constraints
12. Acceptance Criteria / KPIs
13. Timeline / Milestones
14. Risks and Dependencies
15. Approval & Review
16. Glossary & Appendix

### Instructions:
1. Preserve Structure
   - Follow the exact structural integrity of the template.
   - You MUST generate ALL 16 sections listed above, even if some sections have limited information from the transcript.
   - For sections with limited transcript information, use reasonable professional assumptions and standard practices.
   - Maintain all sections, headings, and tables exactly as they appear.
   - Place the transcript-derived information into the corresponding sections without adding or removing sections.
   - DO NOT skip any of the 16 sections - all must be present in the final BRD.
   - CRITICAL: DO NOT create numbered subsections beyond section 16. If you need to show use case flows, steps, or sub-items within a section, use bullet points, tables, or unnumbered paragraphs instead of creating new numbered sections like "17.", "18.", etc.
   - For example, in section 9 (User Stories / Use Cases), if you need to show a use case flow, use bullet points or a table, NOT numbered items like "11. Step 1", "12. Step 2" that could be mistaken for new sections."""

    # Calculate prompt length and truncate if needed
    # Llama 3.1 8B has 8192 token context window TOTAL
    # Reserve ~2000 tokens for instructions + template, ~1000 tokens safety margin
    # This leaves ~5000 tokens for transcript + output
    
    instructions_length = len(prompt_instructions_section)
    template_length = len(template_text)
    transcript_length = len(transcript_text)
    
    # Estimate tokens (rough: 1 token ≈ 4 characters)
    instructions_tokens = instructions_length // 4
    template_tokens = template_length // 4
    transcript_tokens = transcript_length // 4
    
    # Target: Keep total input under 2000 tokens to leave ~6000 for output
    # This ensures we have enough room for comprehensive BRD generation
    # More aggressive truncation to prevent token limit issues
    max_input_tokens = 2000
    safety_margin = 200
    reserved_for_template = 600  # Reserve up to 600 tokens for template (reduced from 800)
    
    # Truncate template if extremely long
    if template_tokens > reserved_for_template:
        max_template_chars = reserved_for_template * 4
        logger.warning(f"Template is very long ({template_tokens} tokens). Truncating to ~{reserved_for_template} tokens ({max_template_chars} chars)")
        template_text = _truncate_text(template_text, max_template_chars)
        template_length = len(template_text)
        template_tokens = template_length // 4
    
    # Calculate available space for transcript
    max_transcript_tokens = max_input_tokens - instructions_tokens - template_tokens - safety_margin
    
    # Ensure minimum space for transcript (at least 500 tokens)
    if max_transcript_tokens < 500:
        logger.warning(f"Very little space for transcript ({max_transcript_tokens} tokens). Further truncating template if needed.")
        # Recalculate with more aggressive template truncation
        max_template_tokens = max_input_tokens - instructions_tokens - 500 - safety_margin
        if max_template_tokens > 0:
            max_template_chars = max_template_tokens * 4
            template_text = _truncate_text(template_text, max_template_chars)
            template_length = len(template_text)
            template_tokens = template_length // 4
            max_transcript_tokens = 500  # Minimum for transcript
    
    # Truncate transcript if too long
    if transcript_tokens > max_transcript_tokens:
        max_transcript_chars = max_transcript_tokens * 4
        logger.warning(f"Transcript is too long ({transcript_tokens} tokens). Truncating to ~{max_transcript_tokens} tokens ({max_transcript_chars} chars)")
        transcript_text = _truncate_text(transcript_text, max_transcript_chars)
        transcript_length = len(transcript_text)
        transcript_tokens = transcript_length // 4
    
    # Build final prompt with (possibly truncated) transcript
    prompt = f"""{prompt_instructions_section}

   
--- TEMPLATE ---
{template_text}

--- TRANSCRIPT ---
{transcript_text}

Return only the completed BRD as plain text.
""".strip()

    # Calculate dynamic max tokens based on actual prompt length
    # Recalculate after truncation to get accurate estimate
    estimated_prompt_tokens = (instructions_tokens + template_tokens + transcript_tokens)
    total_context = 8192
    safety_margin = 300  # Increased safety margin for Llama
    available_output_tokens = total_context - estimated_prompt_tokens - safety_margin
    
    # Use the smaller of: available tokens or configured MAX_TOKENS
    dynamic_max_tokens = min(available_output_tokens, MAX_TOKENS)
    
    # Ensure minimum of 2000 tokens for output (but never exceed available)
    if available_output_tokens >= 2000:
        dynamic_max_tokens = max(dynamic_max_tokens, 2000)
    else:
        # If less than 2000 available, use what we have (but warn)
        logger.warning(f"Only {available_output_tokens} tokens available for output. Prompt may be too long.")
        dynamic_max_tokens = available_output_tokens
    
    # CRITICAL: Final safety check - never exceed available
    dynamic_max_tokens = min(dynamic_max_tokens, available_output_tokens)
    
    logger.info(f"Prompt breakdown: Instructions ~{instructions_tokens}, Template ~{template_tokens}, Transcript ~{transcript_tokens} tokens")
    logger.info(f"Total input: ~{estimated_prompt_tokens} tokens, Available for output: ~{available_output_tokens} tokens")
    logger.info(f"Using dynamic max_gen_len: {dynamic_max_tokens} tokens (configured MAX_TOKENS: {MAX_TOKENS})")
    
    if estimated_prompt_tokens > 3500:
        logger.warning(f"Prompt is very long ({estimated_prompt_tokens} tokens). Output limited to {dynamic_max_tokens} tokens.")

    try:
        brd_text = _invoke_bedrock(prompt, max_tokens=dynamic_max_tokens)
    except RuntimeError as exc:
        # RuntimeError usually means token limit or model issue
        error_msg = str(exc)
        logger.error(f"RuntimeError generating BRD: {error_msg}", exc_info=True)
        
        # Save error message as BRD content to S3 if brd_id provided
        error_brd_content = f"[BRD Generation Error]\n\n{error_msg}\n\nPlease check the input parameters and try again."
        brd_id = None
        if isinstance(evt, dict):
            brd_id = evt.get("brd_id") or evt.get("brdId")
            if not brd_id and "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId")
        
        if brd_id:
            try:
                s3_bucket = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
                s3_region = os.getenv("AWS_REGION", "us-east-1")
                s3_client = boto3.client("s3", region_name=s3_region)
                brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
                
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=brd_key,
                    Body=error_brd_content.encode("utf-8"),
                    ContentType="text/plain"
                )
                logger.info(f"Saved error message as BRD to S3: s3://{s3_bucket}/{brd_key}")
            except Exception as e:
                logger.error(f"Failed to save error BRD to S3: {e}", exc_info=True)
        
        # Return error in Bedrock Agent expected format
        error_response = {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"BRD generation failed: {error_msg}"
                    }
                }
            }
        }
        logger.info(f"Returning error response. brd_id: {brd_id}")
        return error_response
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Unexpected error generating BRD: {str(exc)}", exc_info=True)
        
        # Save error message as BRD content to S3 if brd_id provided
        error_brd_content = f"[BRD Generation Error]\n\nUnexpected error: {str(exc)}\n\nPlease check the logs and try again."
        brd_id = None
        if isinstance(evt, dict):
            brd_id = evt.get("brd_id") or evt.get("brdId")
            if not brd_id and "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId")
        
        if brd_id:
            try:
                s3_bucket = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
                s3_region = os.getenv("AWS_REGION", "us-east-1")
                s3_client = boto3.client("s3", region_name=s3_region)
                brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
                
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=brd_key,
                    Body=error_brd_content.encode("utf-8"),
                    ContentType="text/plain"
                )
                logger.info(f"Saved error message as BRD to S3: s3://{s3_bucket}/{brd_key}")
            except Exception as e:
                logger.error(f"Failed to save error BRD to S3: {e}", exc_info=True)
        
        # Return error in Bedrock Agent expected format
        error_response = {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"Unexpected error: {str(exc)}"
                    }
                }
            }
        }
        logger.info(f"Returning unexpected error response. brd_id: {brd_id}")
        return error_response

    # brd_id should already be extracted above, but double-check
    # (This code was redundant - brd_id is already extracted earlier)
    if not brd_id:
        logger.warning("brd_id is still None after extraction - attempting to re-extract")
        if isinstance(evt, dict):
            brd_id = evt.get("brd_id") or evt.get("brdId")
            if not brd_id and "actionGroupInput" in evt:
                action_input = evt["actionGroupInput"]
                if isinstance(action_input, dict):
                    brd_id = action_input.get("brd_id") or action_input.get("brdId")
            if not brd_id and "parameters" in evt:
                params = evt["parameters"]
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId")
                elif isinstance(params, list):
                    for param in params:
                        if isinstance(param, dict):
                            param_name = param.get("name") or param.get("key")
                            if param_name in ["brd_id", "brdId"]:
                                brd_id = param.get("value") or param.get("val")
                                break
    
    logger.info(f"Final brd_id value before S3 save: {brd_id if brd_id else 'NONE - WILL NOT SAVE TO S3'}")
    
    # FALLBACK: If brd_id is None, generate one
    # This ensures BRD is always saved to S3, even if agent doesn't pass brd_id
    if not brd_id:
        brd_id = str(uuid.uuid4())
        logger.warning(f"⚠️  brd_id was None! Generated new brd_id: {brd_id}")
        logger.warning(f"⚠️  This means the Bedrock Agent didn't pass brd_id as a parameter")
        logger.warning(f"⚠️  The BRD will be saved with this generated ID, but it won't match the expected ID")
    
    # Always save to S3 now (since we have brd_id)
    if brd_id:
        try:
            s3_bucket = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
            s3_region = os.getenv("AWS_REGION", "us-east-1")
            s3_client = boto3.client("s3", region_name=s3_region)
            brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
            
            # Save text file
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=brd_key,
                Body=brd_text.encode("utf-8"),
                ContentType="text/plain"
            )
            logger.info(f"Saved BRD text to S3: s3://{s3_bucket}/{brd_key}")
            
            # Also save structure JSON file (for chat Lambda to use)
            try:
                brd_structure = _convert_brd_text_to_structure(brd_text)
                if brd_structure:
                    structure_key = f"brds/{brd_id}/brd_structure.json"
                    s3_client.put_object(
                        Bucket=s3_bucket,
                        Key=structure_key,
                        Body=json.dumps(brd_structure, indent=2, ensure_ascii=False).encode("utf-8"),
                        ContentType="application/json"
                    )
                    logger.info(f"Saved BRD structure to S3: s3://{s3_bucket}/{structure_key}")
                else:
                    logger.warning("Could not convert BRD text to structure, skipping structure file save")
            except Exception as structure_err:
                logger.warning(f"Failed to save BRD structure (non-critical): {structure_err}")
                # Continue - structure file is optional, chat Lambda can reconstruct it
        except Exception as e:
            logger.error(f"Failed to save BRD to S3: {e}", exc_info=True)
            # Continue anyway - return the BRD even if S3 save failed

    logger.info("=== BRD Generator Lambda Completed Successfully ===")
    
    # Detect invocation method: Agent Mode vs Direct Mode
    # CRITICAL: If S3 keys are present, it MUST be Agent Mode (we use S3 keys for Agent Mode)
    # Otherwise, default to Agent Mode (safer) unless we can definitively prove it's Direct Mode
    
    is_agent_mode = True  # Default to Agent Mode (safer)
    
    # If ANY S3 keys are present, it's definitely Agent Mode (new S3-based approach)
    if template_s3_bucket or template_s3_key or transcript_s3_bucket or transcript_s3_key:
        is_agent_mode = True
        logger.info("Detected Agent Mode: S3 keys present")
    elif isinstance(evt, dict):
        # Check for Agent Mode markers
        has_agent_markers = (
            "actionGroupInput" in evt or
            ("parameters" in evt and isinstance(evt["parameters"], list)) or
            ("parameters" in evt and isinstance(evt["parameters"], dict) and ("template_s3_key" in evt.get("parameters", {}) or "template_s3_bucket" in evt.get("parameters", {})))
        )
        
        # Check for Direct Mode markers (AgentCore Gateway specific)
        # Direct Mode typically has: {"template": "...", "transcript": "..."} at top level
        # AND no actionGroupInput, AND no parameters list format, AND no S3 keys
        has_direct_markers = (
            ("template" in evt or "transcript" in evt or "template_text" in evt or "transcript_text" in evt)
            and "actionGroupInput" not in evt
            and not has_agent_markers
        )
        
        if has_agent_markers:
            is_agent_mode = True
        elif has_direct_markers:
            is_agent_mode = False
    
    logger.info(f"Invocation mode detected: {'Agent Mode' if is_agent_mode else 'Direct Mode'}")
    logger.info(f"Event keys: {list(evt.keys()) if isinstance(evt, dict) else 'N/A'}")
    logger.info(f"Has actionGroupInput: {'actionGroupInput' in evt if isinstance(evt, dict) else False}")
    logger.info(f"Has S3 keys: template_s3_bucket={bool(template_s3_bucket)}, template_s3_key={bool(template_s3_key)}")
    
    # ALWAYS return Direct Mode format for AgentCore
    # This ensures BRD content and ID are both available
    logger.info("Returning Direct Mode format (includes BRD content + ID)")
    response = {
        "statusCode": 200,
        "body": json.dumps({
            "brd": brd_text,
            "brd_id": brd_id,
            "status": "success",
            "message": f"BRD generated successfully. Length: {len(brd_text)} chars."
        })
    }
    
    # Validate JSON serialization
    try:
        response_json = json.dumps(response)
        logger.info(f"Response JSON length: {len(response_json)} chars")
        logger.info(f"Response JSON (first 200 chars): {response_json[:200]}")
        return response
        
    except Exception as e:
        logger.error(f"CRITICAL: Failed to create response: {e}", exc_info=True)
        # Return error in correct format
        return {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"Error creating response: {str(e)[:500]}"
                    }
                }
            }
    }


