"""
BRD Agent for AgentCore Runtime using Strands framework
This agent uses Lambda functions as tools for BRD generation, retrieval, and editing.
"""

import json
import os
import re
from typing import Optional

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

# Initialize the AgentCore Runtime app
app = BedrockAgentCoreApp()

# Lambda function names (configurable via environment variables)
LAMBDA_GENERATOR = os.getenv('LAMBDA_BRD_GENERATOR', 'brd_generator_lambda')
LAMBDA_RETRIEVER = os.getenv('LAMBDA_BRD_RETRIEVER', 'brd_retriever_lambda')
LAMBDA_CHAT = os.getenv('LAMBDA_BRD_CHAT', 'brd_chat_lambda')
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# AgentCore Memory configuration
AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'Test-DGwqpP7Rvj')
AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'brd-session')

# Lazy loading of boto3 Lambda client
_lambda_client = None
# Lazy loading of AgentCore Memory client
_agentcore_memory_client = None
# Lazy loading of Agent
_agent_instance = None

def _get_lambda_client():
    """Lazy load Lambda client with extended timeout to avoid initialization timeout"""
    global _lambda_client
    if _lambda_client is None:
        import boto3
        from botocore.config import Config
        # Increase timeout to 15 minutes (900 seconds) - max Lambda execution time
        config = Config(
            read_timeout=900,
            connect_timeout=60,
            retries={'max_attempts': 0}  # Don't retry on timeout - Lambda is already processing
        )
        _lambda_client = boto3.client('lambda', region_name=AWS_REGION, config=config)
    return _lambda_client

def _get_agentcore_memory_client():
    """Lazy load AgentCore Memory client to avoid initialization timeout"""
    global _agentcore_memory_client
    if _agentcore_memory_client is None:
        import boto3
        _agentcore_memory_client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)
    return _agentcore_memory_client

def invoke_lambda_tool(function_name: str, payload: dict) -> dict:
    """
    Invoke a Lambda function as a tool
    
    Args:
        function_name: Name of the Lambda function
        payload: Payload to send to Lambda
        
    Returns:
        Response from Lambda function
    """
    try:
        lambda_client = _get_lambda_client()
        print(f"[BRD-AGENT] Invoking Lambda: {function_name}", flush=True)
        
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',  # Synchronous invocation
            Payload=json.dumps(payload)
        )
        
        # Read response
        response_payload = json.loads(response['Payload'].read())
        
        # Check for Lambda errors
        if 'FunctionError' in response:
            error_msg = response_payload.get('errorMessage', 'Unknown Lambda error')
            print(f"[BRD-AGENT] Lambda error: {error_msg}", flush=True)
            raise Exception(f"Lambda function error: {error_msg}")
        
        print(f"[BRD-AGENT] Lambda response received", flush=True)
        return response_payload
        
    except Exception as e:
        print(f"[BRD-AGENT] Error invoking Lambda {function_name}: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        raise

# --- Tool Definitions (using @tool decorator) ---

@tool
def generate_brd(template: str, transcript: str, brd_id: Optional[str] = None) -> str:
    """
    Generate a Business Requirements Document (BRD) from a template and transcript.
    
    Args:
        template: The BRD template structure that defines the format and sections
        transcript: The meeting transcript or requirements text to extract information from
        brd_id: Optional BRD ID (will be generated if not provided)
    
    Returns:
        Success message with BRD ID
    """
    payload = {
        'template': template,
        'transcript': transcript
    }
    if brd_id:
        payload['brd_id'] = brd_id
    
    lambda_response = invoke_lambda_tool(LAMBDA_GENERATOR, payload)
    
    print(f"[BRD-AGENT] Lambda response type: {type(lambda_response)}", flush=True)
    print(f"[BRD-AGENT] Lambda response keys: {lambda_response.keys() if isinstance(lambda_response, dict) else 'not a dict'}", flush=True)
    
    # Parse response - handle different response formats
    if isinstance(lambda_response, dict):
        # Handle Lambda HTTP response format
        if 'statusCode' in lambda_response and 'body' in lambda_response:
            print(f"[BRD-AGENT] Detected Lambda HTTP response format", flush=True)
            try:
                body = json.loads(lambda_response['body'])
                print(f"[BRD-AGENT] Parsed body keys: {body.keys()}", flush=True)
                
                if body.get('brd'):
                    brd_text = body['brd']
                    brd_id_from_lambda = body.get('brd_id')
                    print(f"[BRD-AGENT] Found BRD! Length: {len(brd_text)} chars, ID: {brd_id_from_lambda}", flush=True)
                    
                    # Return as JSON so app.py can parse it easily
                    return json.dumps({
                        'status': 'success',
                        'brd': brd_text,
                        'brd_id': brd_id_from_lambda
                    })
            except Exception as e:
                print(f"[BRD-AGENT] Error parsing Lambda body: {e}", flush=True)
        
        # Other formats
        elif 'response' in lambda_response:
            response_body = lambda_response['response'].get('responseBody', {})
            text_body = response_body.get('TEXT', {}).get('body', '')
            brd_id = lambda_response.get('brd_id') or brd_id
            return json.dumps({'status': 'success', 'brd': text_body, 'brd_id': brd_id})
        elif 'brd' in lambda_response:
            return json.dumps({'status': 'success', 'brd': lambda_response['brd'], 'brd_id': lambda_response.get('brd_id')})
    
    # Fallback
    return json.dumps({'status': 'error', 'message': str(lambda_response)[:500]})

@tool
def fetch_brd(brd_id: str) -> str:
    """
    Fetch and retrieve the entire BRD document by its ID.
    Use this tool when the user wants to view or download the complete BRD document.
    
    Do NOT use this tool if the user wants to:
    - View a specific section (use chat_with_brd instead)
    - List sections (use chat_with_brd instead)
    - Edit or update sections (use chat_with_brd instead)
    
    Use this tool only when the user explicitly wants the full/entire/complete BRD document.
    
    Args:
        brd_id: The BRD ID to retrieve (UUID format)
    
    Returns:
        The complete BRD content as text
    """
    payload = {'brd_id': brd_id}
    lambda_response = invoke_lambda_tool(LAMBDA_RETRIEVER, payload)
    
    # Parse response - handle different response formats
    if isinstance(lambda_response, dict):
        if 'response' in lambda_response:
            # Bedrock Agent format
            response_body = lambda_response['response'].get('responseBody', {})
            text_body = response_body.get('TEXT', {}).get('body', '')
            return text_body
        elif 'body' in lambda_response:
            # Lambda response format
            body = lambda_response.get('body', {})
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except:
                    return body
            return body.get('response', body.get('message', str(body)))
    return str(lambda_response)

@tool
def chat_with_brd(
    action: str,
    brd_id: str,
    session_id: Optional[str] = None,
    message: Optional[str] = None,
    template: Optional[str] = None,
    transcript: Optional[str] = None
) -> str:
    """
    Chat with a BRD to edit, update, list, or view sections using natural language.
    
    IMPORTANT: You MUST call this tool for ANY user request related to an existing BRD, including:
    - Listing sections ("list", "list all sections", "show me all sections")
    - Viewing sections ("show section 4", "show me section 4", "show 4", "display section 4", "show stakeholders", "show me constraints")
    - Updating sections ("update section 4: change X to Y", "in section 4 change X to Y", "change X to Y in section 4", "update 4 X to Y", "change X to Y here")
    - Showing updated sections ("show me updated section", "show updated section", "show me the section I just updated", "what did I update")
    - Questions about updates ("how many sections have I updated?", "which sections did I change?", "what changes have I made?")
    - Any questions or edits about the BRD
    
    DO NOT respond directly to the user. ALWAYS call this tool and return its response.
    
    The tool understands natural language commands and handles typos/variations:
    - "list all sections" or "list sections" - Shows all section names
    - "show section 4" or "show me section 4" or "show 4" or "display section 4" - Displays section 4 content
    - "show stakeholders" or "show me stakeholders" - Shows the Stakeholders section
    - "update section 4: change sarah to aman" - Updates section 4
    - "in section 4 change sarah to aman" - Updates section 4
    - "change sarah to aman in section 4" - Updates section 4
    - "update 4 sarah to aman" - Updates section 4
    - "update sarah to aman in section 4" - Updates section 4
    - "change X to Y here" - Updates the last shown section
    - "show me updated section" or "show updated section" or "show me updatd section" (typo) - Shows the last updated section
    - "how many sections have I updated?" or "which sections did I change?" - Lists all updated sections
    - Any other questions about the BRD content
    
    The tool is intelligent and can understand user intent even with typos, variations, or unclear phrasing.
    
    Args:
        action: Always use "send_message" for chat/edit operations
        brd_id: The BRD ID to chat with (required) - use the BRD ID provided in the context/enhanced message
        session_id: Session ID (use the session ID provided in the context/enhanced message, or auto-generate if not provided)
        message: The user's EXACT natural language message/command (required)
                Pass the user's message exactly as they wrote it, even if it has typos. The Lambda will handle intent detection.
                Examples: 
                  - "list all sections"
                  - "show section 4"
                  - "show me updatd section" (typo - will be understood as "show me updated section")
                  - "update section 4: change sarah to aman"
                  - "in section 4 change sarah to aman"
                  - "change sarah to aman in section 4"
                  - "update sarah to aman in section 4"
                  - "how many sections i have updated?" (question about update history)
        template: Template text (only for create_session, not needed for chat)
        transcript: Transcript text (only for create_session, not needed for chat)
    
    Returns:
        Chat response message with the result of the operation
    """
    payload = {
        'action': action,
        'brd_id': brd_id
    }
    
    # Always provide session_id - Lambda will auto-create if missing
    # Use provided session_id, or generate one based on BRD ID for consistency
    if not session_id and brd_id:
        session_id = f"brd-session-{brd_id}"
        print(f"[BRD-AGENT] Auto-generated session_id: {session_id}", flush=True)
    
    if session_id:
        payload['session_id'] = session_id
    if message:
        payload['message'] = message
    if template:
        payload['template'] = template
    if transcript:
        payload['transcript'] = transcript
    
    lambda_response = invoke_lambda_tool(LAMBDA_CHAT, payload)
    
    # Parse response - handle different response formats
    if isinstance(lambda_response, dict):
        if 'statusCode' in lambda_response:
            # Lambda response format
            body = lambda_response.get('body', '{}')
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except:
                    return body
            return body.get('message', body.get('response', str(body)))
        else:
            return lambda_response.get('message', lambda_response.get('response', str(lambda_response)))
    return str(lambda_response)

@tool
def get_brd_conversation_history(brd_id: str, session_id: Optional[str] = None) -> str:
    """
    Get conversation history from AgentCore Memory for a BRD session.
    
    Use this tool to understand:
    - What the user has been discussing
    - Which sections have been updated
    - Previous questions and answers
    - Context for understanding user intent
    
    This helps you answer questions directly without calling Lambda, and provides
    context for making intelligent decisions about what operations to perform.
    
    Args:
        brd_id: The BRD ID (required)
        session_id: Session ID (auto-generated as "brd-session-{brd_id}" if not provided)
    
    Returns:
        Formatted conversation history as text, or error message if retrieval fails
    """
    if not brd_id:
        return "Error: brd_id is required to retrieve conversation history."
    
    if not session_id:
        session_id = f"brd-session-{brd_id}"
        print(f"[BRD-AGENT] Auto-generated session_id for history: {session_id}", flush=True)
    
    client = _get_agentcore_memory_client()
    try:
        print(f"[BRD-AGENT] Retrieving conversation history for session: {session_id}", flush=True)
        response = client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=100
        )
        
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
                # Skip system messages
                if text_content.startswith("Starting BRD editing session") or text_content == "Session closed by user.":
                    continue
                role = conv_data.get("role", "assistant").lower()
                # Format: "User: message" or "Assistant: message"
                messages.append(f"{role.capitalize()}: {text_content}")
        
        if messages:
            history_text = "\n".join(messages)
            print(f"[BRD-AGENT] Retrieved {len(messages)} messages from history", flush=True)
            return history_text
        else:
            print(f"[BRD-AGENT] No conversation history found for session: {session_id}", flush=True)
            return "No conversation history found for this BRD session."
    except Exception as e:
        error_msg = f"Error retrieving conversation history: {str(e)}"
        print(f"[BRD-AGENT] {error_msg}", flush=True)
        return error_msg

def _get_agent(fresh=False):
    """
    Get Strands agent with Lambda tools.
    
    Args:
        fresh: If True, create a new agent instance (avoids conversation history conflicts)
    """
    global _agent_instance
    
    # For chat requests, always create a fresh agent to avoid tool block conflicts
    if fresh or _agent_instance is None:
        try:
            # Initialize Bedrock model
            model = BedrockModel(model_id=BEDROCK_MODEL_ID)
            
            # Create list of tools
            tools = [generate_brd, fetch_brd, chat_with_brd, get_brd_conversation_history]
            
            # Create agent with model and tools
            agent = Agent(model=model, tools=tools)
            
            if not fresh:
                _agent_instance = agent
                print("[BRD-AGENT] Strands agent initialized with Lambda tools", flush=True)
            else:
                print("[BRD-AGENT] Created fresh Strands agent instance (no conversation history)", flush=True)
            
            return agent
            
        except ImportError as e:
            print(f"[BRD-AGENT] Error: Strands not available ({e})", flush=True)
            raise
        except Exception as e:
            print(f"[BRD-AGENT] Error initializing agent: {str(e)}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            raise
    
    return _agent_instance

@app.entrypoint
def invoke(payload):
    """
    AgentCore Runtime entry point for BRD operations
    
    This function receives user requests and routes them through the Strands agent,
    which intelligently selects the appropriate Lambda tools based on the user's intent.
    
    Expected payload format:
    {
        "prompt": "User message/request",
        "text": "Alternative text field",
        "template": "Optional template text",
        "transcript": "Optional transcript text",
        "brd_id": "Optional BRD ID"
    }
    """
    try:
        print("=" * 80, flush=True)
        print("[BRD-AGENT] Handler invoked (Strands + Lambda Tools)", flush=True)
        print("=" * 80, flush=True)
        
        # Extract user message from payload
        user_message = payload.get("prompt") or payload.get("text") or payload.get("message", "Hello! How can I help you with BRD operations?")
        
        print(f"[BRD-AGENT] User message: {user_message[:200]}...", flush=True)
        print(f"[BRD-AGENT] Payload keys: {list(payload.keys())}", flush=True)
        
        # Get the agent instance (lazy loaded)
        agent = _get_agent()
        
        # If template and transcript are provided, directly call generate_brd tool
        if payload.get('template') and payload.get('transcript'):
            print(f"[BRD-AGENT] Template and transcript detected, calling generate_brd tool directly", flush=True)
            try:
                # Directly call the generate_brd function
                template = payload.get('template')
                transcript = payload.get('transcript')
                
                # Generate proper UUID for BRD if not provided
                import uuid
                brd_id = payload.get('brd_id')
                if not brd_id or brd_id == 'none' or brd_id.startswith('generated-'):
                    brd_id = str(uuid.uuid4())
                    print(f"[BRD-AGENT] Generated new BRD ID: {brd_id}", flush=True)
                else:
                    print(f"[BRD-AGENT] Using provided BRD ID: {brd_id}", flush=True)
                
                print(f"[BRD-AGENT] Template length: {len(template)} chars", flush=True)
                print(f"[BRD-AGENT] Transcript length: {len(transcript)} chars", flush=True)
                
                # Call the tool directly
                result_text = generate_brd(template=template, transcript=transcript, brd_id=brd_id)
                print(f"[BRD-AGENT] Direct tool call completed", flush=True)
                
            except Exception as e:
                print(f"[BRD-AGENT] Error in direct tool call: {str(e)}", flush=True)
                import traceback
                print(traceback.format_exc(), flush=True)
                result_text = f"Error generating BRD: {str(e)}"
        # Handle chat/edit requests for existing BRDs
        elif payload.get('brd_id') and payload.get('brd_id') != 'none':
            brd_id = payload.get('brd_id')
            print(f"[BRD-AGENT] BRD ID provided: {brd_id}, using Memory + LLM for intelligent decision making", flush=True)
            
            # Get session_id from payload if provided
            session_id_from_payload = payload.get('session_id')
            if not session_id_from_payload or session_id_from_payload == 'none':
                # Generate a session ID based on BRD ID for consistency
                session_id_from_payload = f"brd-session-{brd_id}"
                print(f"[BRD-AGENT] No session_id provided, using: {session_id_from_payload}", flush=True)
            
            # STEP 1: Get conversation history from AgentCore Memory
            conversation_context = ""
            try:
                print(f"[BRD-AGENT] ========================================", flush=True)
                print(f"[BRD-AGENT] STEP 1: Retrieving conversation history from AgentCore Memory...", flush=True)
                print(f"[BRD-AGENT] BRD ID: {brd_id}", flush=True)
                print(f"[BRD-AGENT] Session ID: {session_id_from_payload}", flush=True)
                history_result = get_brd_conversation_history(brd_id, session_id_from_payload)
                if history_result and "Error" not in history_result and "No conversation history" not in history_result:
                    conversation_context = f"\n\n=== CONVERSATION HISTORY ===\n{history_result}\n=== END HISTORY ===\n"
                    print(f"[BRD-AGENT] ✅ Retrieved conversation history ({len(history_result)} chars)", flush=True)
                    # Log update confirmations found in history for debugging
                    import re
                    update_matches = re.findall(r"✅ Section ['\"](\d+)\.\s*([^'\"]+)['\"] updated successfully", history_result, re.IGNORECASE)
                    if update_matches:
                        print(f"[BRD-AGENT] 📋 Found {len(update_matches)} update confirmation(s) in history:", flush=True)
                        for section_num, section_title in update_matches:
                            print(f"[BRD-AGENT]   - Section {section_num}: {section_title.strip()}", flush=True)
                    else:
                        print(f"[BRD-AGENT] ⚠️ No update confirmations found in history", flush=True)
                else:
                    print(f"[BRD-AGENT] ⚠️ No conversation history available or error occurred", flush=True)
                print(f"[BRD-AGENT] ========================================", flush=True)
            except Exception as e:
                print(f"[BRD-AGENT] ❌ Could not retrieve history: {e}", flush=True)
                import traceback
                print(traceback.format_exc(), flush=True)
            
            # STEP 2: Build enhanced message with Memory context for LLM decision making
            print(f"[BRD-AGENT] ========================================", flush=True)
            print(f"[BRD-AGENT] STEP 2: Building enhanced message with Memory context for LLM", flush=True)
            print(f"[BRD-AGENT] User message: {user_message[:200]}...", flush=True)
            print(f"[BRD-AGENT] ========================================", flush=True)
            
            enhanced_message = f"""You are a BRD (Business Requirements Document) assistant. You have access to conversation history and can make intelligent decisions.

USER'S CURRENT MESSAGE: {user_message}
{conversation_context}

AVAILABLE TOOLS:
1. get_brd_conversation_history(brd_id, session_id) - Get conversation history (already retrieved above)
2. chat_with_brd(action, brd_id, session_id, message) - For viewing/updating sections, listing sections
3. fetch_brd(brd_id) - Get the complete BRD document
4. generate_brd(template, transcript, brd_id) - Generate a new BRD

CRITICAL PARAMETERS:
- brd_id: "{brd_id}"
- session_id: "{session_id_from_payload}"

DECISION LOGIC:

1. If user asks "show me updated section" or "show updated section":
   - FIRST: Analyze conversation history to find the MOST RECENT update confirmation
   - Look for the LAST (most recent) message containing "✅ Section 'X. Title' updated successfully"
   - Extract the section number (e.g., "11" from "✅ Section '11. Constraints' updated successfully")
   - THEN: Call chat_with_brd with action="send_message", brd_id="{brd_id}", session_id="{session_id_from_payload}", message="show me updated section"
   - The Lambda will handle showing the correct section based on its internal tracking
   - DO NOT try to answer directly - you need the Lambda to retrieve the actual section content

2. If user asks "which sections have I updated?" or "what sections i have updated so far?":
   - Analyze conversation history to find ALL update confirmations
   - Look for ALL messages containing "✅ Section 'X. Title' updated successfully"
   - Extract section numbers and titles from ALL such messages
   - List them in chronological order (oldest to newest)
   - You can answer this directly from history WITHOUT calling tools

3. If user wants to VIEW a section (e.g., "show section 4", "show stakeholders", "list sections"):
   - Call chat_with_brd with:
     - action="send_message"
     - brd_id="{brd_id}"
     - session_id="{session_id_from_payload}"
     - message="{user_message}" (exact user message)

3. If user wants to UPDATE a section (e.g., "change X to Y", "update section 4", "change X to Y here"):
   - Call chat_with_brd with:
     - action="send_message"
     - brd_id="{brd_id}"
     - session_id="{session_id_from_payload}"
     - message="{user_message}" (exact user message, even with typos)

4. If user wants the FULL BRD document:
   - Call fetch_brd with brd_id="{brd_id}"

5. If user wants to GENERATE a new BRD:
   - Call generate_brd with template and transcript

IMPORTANT:
- Use conversation history to understand context (e.g., "here" refers to last shown section)
- For questions, try to answer from history first before calling tools
- Pass user messages exactly as written (don't fix typos - Lambda handles that)
- Be intelligent: if history shows recent updates, you can answer questions about them directly

Now analyze the user's message and conversation history, then decide what action to take."""
            
            try:
                # STEP 3: Let the Strands agent use LLM + Memory to make intelligent decisions
                print(f"[BRD-AGENT] ========================================", flush=True)
                print(f"[BRD-AGENT] STEP 3: Invoking Strands agent with Memory context for intelligent decision making", flush=True)
                print(f"[BRD-AGENT] ========================================", flush=True)
                result = agent(enhanced_message)
                
                # Extract response from agent result
                if hasattr(result, 'data'):
                    result_text = str(result.data) if result.data else str(result)
                elif hasattr(result, 'output'):
                    result_text = str(result.output)
                elif hasattr(result, 'message'):
                    result_text = result.message
                elif isinstance(result, str):
                    result_text = result
                else:
                    result_text = str(result)
                
                print(f"[BRD-AGENT] ========================================", flush=True)
                print(f"[BRD-AGENT] STEP 4: Agent LLM response received", flush=True)
                print(f"[BRD-AGENT] Response length: {len(result_text)} chars", flush=True)
                print(f"[BRD-AGENT] Response preview: {result_text[:300]}...", flush=True)
                print(f"[BRD-AGENT] ========================================", flush=True)
            except Exception as e:
                print(f"[BRD-AGENT] Error in agent LLM execution: {e}", flush=True)
                import traceback
                print(traceback.format_exc(), flush=True)
                # Fallback: try direct call if LLM fails
                try:
                    print(f"[BRD-AGENT] Falling back to direct chat_with_brd call", flush=True)
                    result_text = chat_with_brd(
                        action="send_message",
                        brd_id=brd_id,
                        session_id=session_id_from_payload,
                        message=user_message
                    )
                except Exception as fallback_error:
                    print(f"[BRD-AGENT] Fallback also failed: {fallback_error}", flush=True)
                    result_text = f"Error processing request: {str(e)}. Please try rephrasing your request."
        else:
            # Run the agent with user message for general queries
            print(f"[BRD-AGENT] Running Strands agent for general query...", flush=True)
            
            # Use the synchronous call method (agent is callable)
            result = agent(user_message)
            
            # Extract response from agent result
            if hasattr(result, 'data'):
                result_text = str(result.data) if result.data else str(result)
            elif hasattr(result, 'output'):
                result_text = str(result.output)
            elif hasattr(result, 'message'):
                result_text = result.message
            elif isinstance(result, str):
                result_text = result
            else:
                result_text = str(result)
        
        print(f"[BRD-AGENT] Response length: {len(result_text)} characters", flush=True)
        
        # Try to extract BRD ID from result if present
        brd_id = payload.get('brd_id')
        
        # Build response
        response = {
            "result": result_text
        }
        
        if brd_id:
            response["brd_id"] = brd_id
        
        print(f"[BRD-AGENT] Returning response", flush=True)
        print("=" * 80, flush=True)
        
        return response
        
    except Exception as e:
        print("=" * 80, flush=True)
        print("[BRD-AGENT] ERROR", flush=True)
        print("=" * 80, flush=True)
        print(f"[BRD-AGENT] Error: {str(e)}", flush=True)
        import traceback
        error_trace = traceback.format_exc()
        print(error_trace, flush=True)
        
        return {
            "result": f"Error processing request: {str(e)}. Please check CloudWatch logs for details.",
            "isError": True
        }

if __name__ == "__main__":
    # Run the app locally for testing
    print("[BRD-AGENT] Starting AgentCore Runtime app locally...", flush=True)
    print(f"[BRD-AGENT] Lambda Generator: {LAMBDA_GENERATOR}", flush=True)
    print(f"[BRD-AGENT] Lambda Retriever: {LAMBDA_RETRIEVER}", flush=True)
    print(f"[BRD-AGENT] Lambda Chat: {LAMBDA_CHAT}", flush=True)
    print(f"[BRD-AGENT] Bedrock Model: {BEDROCK_MODEL_ID}", flush=True)
    app.run()
