"""
BRD Agent for AgentCore Runtime using Strands framework
This agent uses Lambda functions as tools for BRD generation, retrieval, and editing.
"""

import json
import logging
import os
import re
import ssl
import threading
import traceback
import uuid
from typing import Optional
from urllib import request as _urlreq

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.openai import OpenAIModel
from strands.models import BedrockModel
from environment import (
    AGENT_MODEL_PROVIDER,
    DEFAULT_DLXAI_GATEWAY_URL,
    DEFAULT_DLXAI_GATEWAY_KEY,
    DEFAULT_GATEWAY_MODEL,
    DEFAULT_LAMBDA_BRD_GENERATOR,
    DEFAULT_LAMBDA_BRD_RETRIEVER,
    DEFAULT_LAMBDA_BRD_CHAT,
    DEFAULT_AGENTCORE_MEMORY_ID,
    DEFAULT_AGENTCORE_ACTOR_ID,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=None,  # defaults to stderr, captured by CloudWatch/AgentCore runtime
)
logger = logging.getLogger(__name__)

# Per-invocation user_id (set by entrypoint, read by tools when building Lambda payloads
# so each Lambda can attribute its own LLM token usage to the right user).
_current_user_id: Optional[str] = None


def _record_tokens_via_callback(user_id: Optional[str], total_tokens: int, source: str) -> None:
    """Fire-and-forget HTTP callback to backend's /api/internal/record-tokens.
    Used for tokens spent by Strands inside this agent (BedrockModel / OpenAIModel),
    which don't go through llm_gateway.
    """
    if not user_id or not total_tokens or total_tokens <= 0:
        return

    def _post():
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        api_key = os.getenv("INTERNAL_API_KEY", "")
        if not backend_url or not api_key:
            logger.info(f"[BRD-AGENT] cannot record tokens: BACKEND_URL/INTERNAL_API_KEY not set "
                  f"(would have recorded {total_tokens} tokens for {user_id})")
            return
        try:
            body = json.dumps({
                "user_id": user_id, "tokens": total_tokens, "source": source,
            }).encode("utf-8")
            req = _urlreq.Request(
                f"{backend_url}/api/internal/record-tokens",
                data=body,
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                method="POST",
            )
            ctx = None
            if os.getenv("INTERNAL_TLS_VERIFY", "1") == "0":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with _urlreq.urlopen(req, timeout=5, context=ctx) as resp:
                if resp.status >= 400:
                    logger.error(f"[BRD-AGENT] record-tokens callback {resp.status}: {resp.read()[:200]!r}")
        except Exception as e:
            logger.error(f"[BRD-AGENT] record-tokens callback failed for {user_id}: {e}")

    threading.Thread(target=_post, daemon=True).start()


def _capture_strands_metrics(agent_obj, user_id: Optional[str], source: str) -> None:
    """Read Strands accumulated token usage off an agent and ship it to backend."""
    if not user_id:
        return
    try:
        # Strands stores cumulative token usage on agent.event_loop_metrics.accumulated_usage
        metrics = getattr(agent_obj, "event_loop_metrics", None) or getattr(agent_obj, "metrics", None)
        usage = None
        if metrics is not None:
            usage = getattr(metrics, "accumulated_usage", None) or getattr(metrics, "total_token_usage", None)
        if usage is None:
            return
        total = 0
        if isinstance(usage, dict):
            total = usage.get("totalTokens") or usage.get("total_tokens") or 0
        else:
            total = getattr(usage, "totalTokens", 0) or getattr(usage, "total_tokens", 0)
        if total:
            logger.info(f"[BRD-AGENT] Strands tokens={total} user={user_id} source={source}")
            _record_tokens_via_callback(user_id, int(total), source)
    except Exception as e:
        logger.error(f"[BRD-AGENT] _capture_strands_metrics failed: {e}")

# Initialize the AgentCore Runtime app
app = BedrockAgentCoreApp()

# Lambda function names — defaults come from environment switch (VDI vs local)
LAMBDA_GENERATOR = os.getenv('LAMBDA_BRD_GENERATOR', DEFAULT_LAMBDA_BRD_GENERATOR)
LAMBDA_RETRIEVER = os.getenv('LAMBDA_BRD_RETRIEVER', DEFAULT_LAMBDA_BRD_RETRIEVER)
LAMBDA_CHAT = os.getenv('LAMBDA_BRD_CHAT', DEFAULT_LAMBDA_BRD_CHAT)
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# LLM gateway/model — defaults come from environment switch (VDI vs local)
DLXAI_GATEWAY_URL = os.getenv('DLXAI_GATEWAY_URL', DEFAULT_DLXAI_GATEWAY_URL)
DLXAI_GATEWAY_KEY = os.getenv('DLXAI_GATEWAY_KEY', DEFAULT_DLXAI_GATEWAY_KEY)
GATEWAY_MODEL = os.getenv('GATEWAY_MODEL', DEFAULT_GATEWAY_MODEL)

# AgentCore Memory configuration
AGENTCORE_MEMORY_ID = DEFAULT_AGENTCORE_MEMORY_ID
AGENTCORE_ACTOR_ID = DEFAULT_AGENTCORE_ACTOR_ID

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
        logger.info(f"[BRD-AGENT] Invoking Lambda: {function_name}")
        
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
            logger.error(f"[BRD-AGENT] Lambda error: {error_msg}")
            raise Exception(f"Lambda function error: {error_msg}")

        logger.info(f"[BRD-AGENT] Lambda response received")
        return response_payload

    except Exception as e:
        logger.error(f"[BRD-AGENT] Error invoking Lambda {function_name}: {str(e)}")
        logger.exception("Exception details:")
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
    if _current_user_id:
        payload['user_id'] = _current_user_id  # for token usage tracking

    lambda_response = invoke_lambda_tool(LAMBDA_GENERATOR, payload)
    
    logger.info(f"[BRD-AGENT] Lambda response type: {type(lambda_response)}")
    logger.info(f"[BRD-AGENT] Lambda response keys: {lambda_response.keys() if isinstance(lambda_response, dict) else 'not a dict'}")
    
    # Parse response - handle different response formats
    if isinstance(lambda_response, dict):
        # Handle Lambda HTTP response format
        if 'statusCode' in lambda_response and 'body' in lambda_response:
            logger.info(f"[BRD-AGENT] Detected Lambda HTTP response format")
            try:
                body = json.loads(lambda_response['body'])
                logger.info(f"[BRD-AGENT] Parsed body keys: {body.keys()}")

                if body.get('brd'):
                    brd_text = body['brd']
                    brd_id_from_lambda = body.get('brd_id')
                    logger.info(f"[BRD-AGENT] Found BRD! Length: {len(brd_text)} chars, ID: {brd_id_from_lambda}")
                    
                    # Return as JSON so app.py can parse it easily
                    return json.dumps({
                        'status': 'success',
                        'brd': brd_text,
                        'brd_id': brd_id_from_lambda
                    })
            except Exception as e:
                logger.error(f"[BRD-AGENT] Error parsing Lambda body: {e}")
        
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
    # LOG exactly what message is being sent to Lambda
    logger.info(f"[BRD-AGENT] chat_with_brd called with:")
    logger.info(f"[BRD-AGENT]   action={action}")
    logger.info(f"[BRD-AGENT]   brd_id={brd_id}")
    logger.info(f"[BRD-AGENT]   session_id={session_id}")
    logger.info(f"[BRD-AGENT]   message (first 300 chars)={message[:300] if message else 'None'}")
    
    payload = {
        'action': action,
        'brd_id': brd_id
    }
    
    # Always provide session_id - Lambda will auto-create if missing
    # Use provided session_id, or generate one based on BRD ID for consistency
    if not session_id and brd_id:
        session_id = f"brd-session-{brd_id}"
        logger.info(f"[BRD-AGENT] Auto-generated session_id: {session_id}")
    
    if session_id:
        payload['session_id'] = session_id
    if message:
        payload['message'] = message
    if template:
        payload['template'] = template
    if transcript:
        payload['transcript'] = transcript
    if _current_user_id:
        payload['user_id'] = _current_user_id  # for token usage tracking

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
        logger.info(f"[BRD-AGENT] Auto-generated session_id for history: {session_id}")
    
    client = _get_agentcore_memory_client()
    try:
        logger.info(f"[BRD-AGENT] Retrieving conversation history for session: {session_id}")
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
            logger.info(f"[BRD-AGENT] Retrieved {len(messages)} messages from history")
            return history_text
        else:
            logger.info(f"[BRD-AGENT] No conversation history found for session: {session_id}")
            return "No conversation history found for this BRD session."
    except Exception as e:
        error_msg = f"Error retrieving conversation history: {str(e)}"
        logger.error(f"[BRD-AGENT] {error_msg}")
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
            # Initialize model — gateway (VDI) or Bedrock directly (local)
            if AGENT_MODEL_PROVIDER == "bedrock":
                model = BedrockModel(model_id=GATEWAY_MODEL)
            else:
                model = OpenAIModel(
                    model_id=GATEWAY_MODEL,
                    client_args={
                        "base_url": DLXAI_GATEWAY_URL,
                        "api_key": DLXAI_GATEWAY_KEY,
                    },
                )
            
            # Create list of tools
            tools = [generate_brd, fetch_brd, chat_with_brd, get_brd_conversation_history]
            
            # Create agent with model and tools
            agent = Agent(model=model, tools=tools)
            
            if not fresh:
                _agent_instance = agent
                logger.info("[BRD-AGENT] Strands agent initialized with Lambda tools")
            else:
                logger.info("[BRD-AGENT] Created fresh Strands agent instance (no conversation history)")
            
            return agent
            
        except ImportError as e:
            logger.error(f"[BRD-AGENT] Error: Strands not available ({e})")
            raise
        except Exception as e:
            logger.error(f"[BRD-AGENT] Error initializing agent: {str(e)}")
            logger.exception("Exception details:")
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
    global _current_user_id
    try:
        logger.info("=" * 80)
        logger.info("[BRD-AGENT] Handler invoked (Strands + Lambda Tools)")
        logger.info("=" * 80)

        # Stash user_id so tools can attribute Lambda LLM calls.
        _current_user_id = payload.get("user_id") or None
        logger.info(f"[BRD-AGENT] user_id={_current_user_id or 'unknown'}")

        # Extract user message from payload
        user_message = payload.get("prompt") or payload.get("text") or payload.get("message", "Hello! How can I help you with BRD operations?")

        logger.info(f"[BRD-AGENT] User message: {user_message[:200]}...")
        logger.info(f"[BRD-AGENT] Payload keys: {list(payload.keys())}")
        
        # Get the agent instance (lazy loaded)
        agent = _get_agent()
        
        # If template and transcript are provided, directly call generate_brd tool
        if payload.get('template') and payload.get('transcript'):
            logger.info(f"[BRD-AGENT] Template and transcript detected, calling generate_brd tool directly")
            try:
                # Directly call the generate_brd function
                template = payload.get('template')
                transcript = payload.get('transcript')
                
                # Generate proper UUID for BRD if not provided
                brd_id = payload.get('brd_id')
                if not brd_id or brd_id == 'none' or brd_id.startswith('generated-'):
                    brd_id = str(uuid.uuid4())
                    logger.info(f"[BRD-AGENT] Generated new BRD ID: {brd_id}")
                else:
                    logger.info(f"[BRD-AGENT] Using provided BRD ID: {brd_id}")

                logger.info(f"[BRD-AGENT] Template length: {len(template)} chars")
                logger.info(f"[BRD-AGENT] Transcript length: {len(transcript)} chars")

                # Call the tool directly
                result_text = generate_brd(template=template, transcript=transcript, brd_id=brd_id)
                logger.info(f"[BRD-AGENT] Direct tool call completed")

            except Exception as e:
                logger.error(f"[BRD-AGENT] Error in direct tool call: {str(e)}")
                logger.exception("Exception details:")
                result_text = f"Error generating BRD: {str(e)}"
        # Handle chat/edit requests for existing BRDs
        elif payload.get('brd_id') and payload.get('brd_id') != 'none':
            brd_id = payload.get('brd_id')
            logger.info(f"[BRD-AGENT] BRD ID provided: {brd_id}")
            
            # Get session_id from payload if provided
            session_id_from_payload = payload.get('session_id')
            if not session_id_from_payload or session_id_from_payload == 'none':
                session_id_from_payload = f"brd-session-{brd_id}"
                logger.info(f"[BRD-AGENT] No session_id provided, using: {session_id_from_payload}")
            
            # DIRECT PATH: For straightforward commands (update, show, list), bypass the Strands Agent LLM
            # and call chat_with_brd directly. This prevents the Agent LLM from reformulating the message.
            # The Lambda has its own LLM parsing logic that handles intent detection, section resolution, etc.
            is_direct_command = any(keyword in user_message.lower() for keyword in [
                'change', 'update', 'modify', 'edit', 'replace', 'remove', 'add', 'delete',
                'show', 'list', 'summarize', 'transfer',
                'everywhere', 'all sections', 'entire document'
            ])
            
            if is_direct_command:
                # DIRECT PATH: Send user's exact message to Lambda without Agent LLM interference
                logger.info(f"[BRD-AGENT] ========================================")
                logger.info(f"[BRD-AGENT] DIRECT PATH: Bypassing Strands Agent LLM for command: {user_message[:200]}")
                logger.info(f"[BRD-AGENT] ========================================")
                
                try:
                    result_text = chat_with_brd(
                        action="send_message",
                        brd_id=brd_id,
                        session_id=session_id_from_payload,
                        message=user_message
                    )
                    logger.info(f"[BRD-AGENT] DIRECT PATH result: {str(result_text)[:300]}")
                except Exception as e:
                    logger.error(f"[BRD-AGENT] DIRECT PATH error: {e}")
                    logger.exception("Exception details:")
                    result_text = f"Error processing request: {str(e)}. Please try rephrasing your request."
            else:
                # AGENT PATH: For complex queries, questions about history, etc.
                # Use the Strands Agent LLM for intelligent decision making
                logger.info(f"[BRD-AGENT] AGENT PATH: Using Strands Agent for: {user_message[:200]}")
                
                # Retrieve conversation history from AgentCore Memory
                conversation_context = ""
                try:
                    history_result = get_brd_conversation_history(brd_id, session_id_from_payload)
                    if history_result and "Error" not in history_result and "No conversation history" not in history_result:
                        conversation_context = f"\n\n=== CONVERSATION HISTORY ===\n{history_result}\n=== END HISTORY ===\n"
                        logger.info(f"[BRD-AGENT] Retrieved conversation history ({len(history_result)} chars)")
                    else:
                        logger.info(f"[BRD-AGENT] No conversation history available")
                except Exception as e:
                    logger.error(f"[BRD-AGENT] Could not retrieve history: {e}")
                
                enhanced_message = f"""You are a BRD (Business Requirements Document) assistant.

USER'S CURRENT MESSAGE: {user_message}
{conversation_context}

CRITICAL PARAMETERS:
- brd_id: "{brd_id}"
- session_id: "{session_id_from_payload}"

DECISION LOGIC:
1. If user asks about update history ("which sections updated?", "what changes?"):
   - Analyze conversation history and answer directly
2. If user asks "show me updated section" or similar:
   - Call chat_with_brd(action="send_message", brd_id="{brd_id}", session_id="{session_id_from_payload}", message="show me updated section")
3. For ANY other request (view, update, list, questions about BRD):
   - Call chat_with_brd(action="send_message", brd_id="{brd_id}", session_id="{session_id_from_payload}", message="{user_message}")
   - CRITICAL: Pass the user's EXACT message. Do NOT rewrite, summarize, or modify it.
4. For full BRD document: Call fetch_brd(brd_id="{brd_id}")

Now analyze and take action."""
                
                try:
                    result = agent(enhanced_message)
                    _capture_strands_metrics(agent, _current_user_id, "pm_agent_chat")
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

                    logger.info(f"[BRD-AGENT] AGENT PATH result: {result_text[:300]}")
                except Exception as e:
                    logger.error(f"[BRD-AGENT] AGENT PATH error, falling back to direct: {e}")
                    try:
                        result_text = chat_with_brd(
                            action="send_message",
                            brd_id=brd_id,
                            session_id=session_id_from_payload,
                            message=user_message
                        )
                    except Exception as fallback_error:
                        logger.error(f"[BRD-AGENT] Fallback also failed: {fallback_error}")
                        result_text = f"Error processing request: {str(e)}. Please try rephrasing your request."
        else:
            # Run the agent with user message for general queries
            logger.info(f"[BRD-AGENT] Running Strands agent for general query...")

            # Use the synchronous call method (agent is callable)
            result = agent(user_message)
            _capture_strands_metrics(agent, _current_user_id, "pm_agent_general")

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
        
        logger.info(f"[BRD-AGENT] Response length: {len(result_text)} characters")
        
        # Try to extract BRD ID from result if present
        brd_id = payload.get('brd_id')
        
        # Build response
        response = {
            "result": result_text
        }
        
        if brd_id:
            response["brd_id"] = brd_id
        
        logger.info(f"[BRD-AGENT] Returning response")
        logger.info("=" * 80)
        
        return response
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error("[BRD-AGENT] ERROR")
        logger.error("=" * 80)
        logger.error(f"[BRD-AGENT] Error: {str(e)}")
        logger.exception("Exception details:")

        return {
            "result": f"Error processing request: {str(e)}. Please check CloudWatch logs for details.",
            "isError": True
        }
    finally:
        _current_user_id = None

if __name__ == "__main__":
    # Run the app locally for testing
    logger.info("[BRD-AGENT] Starting AgentCore Runtime app locally...")
    logger.info(f"[BRD-AGENT] Lambda Generator: {LAMBDA_GENERATOR}")
    logger.info(f"[BRD-AGENT] Lambda Retriever: {LAMBDA_RETRIEVER}")
    logger.info(f"[BRD-AGENT] Lambda Chat: {LAMBDA_CHAT}")
    logger.info(f"[BRD-AGENT] Gateway Model: {GATEWAY_MODEL} via {DLXAI_GATEWAY_URL}")
    app.run()
