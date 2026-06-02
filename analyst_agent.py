"""
Business Analyst Agent for AgentCore Runtime using Strands framework
This agent uses Lambda functions as tools for requirements gathering and BRD generation.
"""

import json
import logging
import os
import ssl
import threading
import traceback
import uuid
from typing import Optional
from urllib import request as _urlreq

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
    """Fire-and-forget HTTP callback to backend's /api/internal/record-tokens for
    Strands tokens spent inside this agent (BedrockModel / OpenAIModel)."""
    if not user_id or not total_tokens or total_tokens <= 0:
        return

    def _post():
        backend_url = os.getenv("BACKEND_URL", "").rstrip("/")
        api_key = os.getenv("INTERNAL_API_KEY", "")
        if not backend_url or not api_key:
            logger.info(f"[ANALYST-AGENT] cannot record tokens: BACKEND_URL/INTERNAL_API_KEY not set "
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
                    logger.error(f"[ANALYST-AGENT] record-tokens callback {resp.status}: {resp.read()[:200]!r}")
        except Exception as e:
            logger.error(f"[ANALYST-AGENT] record-tokens callback failed for {user_id}: {e}")

    threading.Thread(target=_post, daemon=True).start()


def _capture_strands_metrics(agent_obj, user_id: Optional[str], source: str) -> None:
    """Read Strands accumulated token usage off an agent and ship it to backend."""
    if not user_id:
        return
    try:
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
            logger.info(f"[ANALYST-AGENT] Strands tokens={total} user={user_id} source={source}")
            _record_tokens_via_callback(user_id, int(total), source)
    except Exception as e:
        logger.error(f"[ANALYST-AGENT] _capture_strands_metrics failed: {e}")

# Defensive import strategy for BedrockAgentCoreApp (same as my_agent)
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    logger.info("[ANALYST-AGENT] Imported BedrockAgentCoreApp from bedrock_agentcore.runtime")
except ImportError:
    try:
        from bedrock_agentcore import BedrockAgentCoreApp
        logger.info("[ANALYST-AGENT] Imported BedrockAgentCoreApp from bedrock_agentcore")
    except ImportError:
        try:
            from bedrock_agentcore.runtime.app import BedrockAgentCoreApp
            logger.info("[ANALYST-AGENT] Imported BedrockAgentCoreApp from bedrock_agentcore.runtime.app")
        except ImportError as e:
            logger.error(f"[ANALYST-AGENT] Failed to import BedrockAgentCoreApp: {e}")
            raise

from strands import Agent, tool
from strands.models.openai import OpenAIModel
from strands.models import BedrockModel
from environment import (
    AGENT_MODEL_PROVIDER,
    DEFAULT_DLXAI_GATEWAY_URL,
    DEFAULT_DLXAI_GATEWAY_KEY,
    DEFAULT_GATEWAY_MODEL,
    DEFAULT_LAMBDA_REQUIREMENTS_GATHERING,
    DEFAULT_LAMBDA_BRD_FROM_HISTORY,
)

# Initialize the AgentCore Runtime app
app = BedrockAgentCoreApp()

# Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# LLM gateway/model — defaults come from environment switch (VDI vs local)
DLXAI_GATEWAY_URL = os.getenv('DLXAI_GATEWAY_URL', DEFAULT_DLXAI_GATEWAY_URL)
DLXAI_GATEWAY_KEY = os.getenv('DLXAI_GATEWAY_KEY', DEFAULT_DLXAI_GATEWAY_KEY)
GATEWAY_MODEL = os.getenv('GATEWAY_MODEL', DEFAULT_GATEWAY_MODEL)

# Lambda ARNs — defaults come from environment switch (VDI vs local)
LAMBDA_REQUIREMENTS_GATHERING = os.getenv('LAMBDA_REQUIREMENTS_GATHERING', DEFAULT_LAMBDA_REQUIREMENTS_GATHERING)
LAMBDA_BRD_FROM_HISTORY = os.getenv('LAMBDA_BRD_FROM_HISTORY', DEFAULT_LAMBDA_BRD_FROM_HISTORY)

# Lazy loading of boto3 Lambda client
_lambda_client = None
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
        logger.info(f"[ANALYST-AGENT] Invoking Lambda: {function_name}")
        logger.info(f"[ANALYST-AGENT] Payload keys: {list(payload.keys())}")
        
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        response_payload = json.loads(response['Payload'].read())
        
        if 'FunctionError' in response:
            error_msg = response_payload.get('errorMessage', 'Unknown Lambda error')
            logger.error(f"[ANALYST-AGENT] Lambda error: {error_msg}")
            raise Exception(f"Lambda function error: {error_msg}")

        logger.info(f"[ANALYST-AGENT] Lambda response received successfully")
        return response_payload
        
    except Exception as e:
        logger.error(f"[ANALYST-AGENT] Error invoking Lambda {function_name}: {str(e)}")
        logger.exception("Exception details:")
        raise


# --- Tool Definitions (using @tool decorator) ---

@tool
def gather_requirements(session_id: str, user_message: str) -> str:
    """
    Conduct requirements gathering conversation with the user.
    
    This tool uses a Lambda function that:
    - Uses Mary's persona to guide the conversation
    - Asks structured questions about the project
    - Stores conversation in AgentCore Memory
    - Returns Mary's response to continue the dialogue
    
    Use this tool when:
    - Starting a new requirements gathering session
    - User provides information about their project
    - Continuing an ongoing requirements conversation
    
    Args:
        session_id: The session ID for this requirements gathering conversation
        user_message: The user's message or response
        
    Returns:
        Mary's response with follow-up questions or acknowledgments
    """
    payload = {
        'session_id': session_id,
        'user_message': user_message
    }
    if _current_user_id:
        payload['user_id'] = _current_user_id  # for token usage tracking

    try:
        result = invoke_lambda_tool(LAMBDA_REQUIREMENTS_GATHERING, payload)
        
        # Parse response
        if isinstance(result, dict):
            if 'statusCode' in result:
                body = result.get('body', '{}')
                if isinstance(body, str):
                    body = json.loads(body)
                response_text = body.get('response', body.get('message', 'Response received'))
            else:
                response_text = result.get('response', result.get('message', str(result)))
        else:
            response_text = str(result)
        
        return response_text
        
    except Exception as e:
        error_msg = f"Error in requirements gathering: {str(e)}"
        logger.error(f"[ANALYST-AGENT] {error_msg}")
        return error_msg


@tool
def generate_brd_from_history(session_id: str, brd_id: Optional[str] = None) -> str:
    """
    Generate a BRD from the conversation history stored in AgentCore Memory.
    
    This tool:
    - Fetches all conversation history from AgentCore Memory for the session
    - Formats it as a transcript
    - Calls the BRD generator Lambda with the template from S3
    - Saves the generated BRD to S3
    - Returns the BRD ID for future reference
    
    Use this tool when:
    - User requests to generate a BRD
    - User says they're done providing requirements
    - User asks to create the document
    
    NOTE: This works with ANY amount of conversation history - even minimal information.
    The BRD generator will create a comprehensive document based on available information.
    
    Args:
        session_id: The session ID containing the requirements conversation
        brd_id: Optional BRD ID (will be auto-generated if not provided)
        
    Returns:
        Success message with BRD ID
    """
    payload = {
        'session_id': session_id
    }

    if brd_id:
        payload['brd_id'] = brd_id
    if _current_user_id:
        payload['user_id'] = _current_user_id  # for token usage tracking

    try:
        result = invoke_lambda_tool(LAMBDA_BRD_FROM_HISTORY, payload)
        
        # Parse response
        if isinstance(result, dict):
            if 'statusCode' in result:
                body = result.get('body', '{}')
                if isinstance(body, str):
                    body = json.loads(body)
                brd_id_result = body.get('brd_id', 'unknown')
                message = body.get('message', 'BRD generated successfully')
            else:
                brd_id_result = result.get('brd_id', 'unknown')
                message = result.get('message', 'BRD generated successfully')
        else:
            brd_id_result = 'unknown'
            message = str(result)
        
        success_msg = f"✅ {message}\n\nBRD ID: {brd_id_result}\n\nYou can now view and edit this BRD using the BRD chat agent."
        return success_msg
        
    except Exception as e:
        error_msg = f"Error generating BRD: {str(e)}"
        logger.error(f"[ANALYST-AGENT] {error_msg}")
        return error_msg


def _get_agent(fresh=False):
    """
    Get Strands agent with Lambda tools.
    
    Args:
        fresh: If True, create a new agent instance
    """
    global _agent_instance
    
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
            
            # System prompt for the agent
            system_prompt = """You are Mary, a Strategic Business Analyst and Requirements Expert.

Your role is to help users create Business Requirements Documents (BRDs) through structured conversation.

You have two tools available:
1. gather_requirements - Use this to conduct the requirements gathering conversation
2. generate_brd_from_history - Use this when the user is ready to generate the BRD

WORKFLOW:
1. When a user starts, use gather_requirements to begin the conversation
2. Continue using gather_requirements for each user response
3. When the user indicates they're ready or asks to generate the BRD, use generate_brd_from_history

IMPORTANT:
- Always pass the session_id to both tools
- The gather_requirements tool handles the conversation - you just need to call it with the user's message
- The generate_brd_from_history tool works with any amount of conversation history
- Be helpful and guide the user through the process"""
            
            # Create list of tools
            tools = [gather_requirements, generate_brd_from_history]
            
            # Create agent with model, tools, and system prompt
            agent = Agent(model=model, tools=tools, system_prompt=system_prompt)
            
            if not fresh:
                _agent_instance = agent
                logger.info("[ANALYST-AGENT] Strands agent initialized with Lambda tools")
            else:
                logger.info("[ANALYST-AGENT] Created fresh Strands agent instance")

            return agent

        except Exception as e:
            logger.error(f"[ANALYST-AGENT] Error initializing agent: {str(e)}")
            logger.exception("Exception details:")
            raise
    
    return _agent_instance


@app.entrypoint
def invoke(payload):
    """
    AgentCore Runtime entry point for Business Analyst agent.
    
    This agent conducts structured Q&A to gather requirements and generate BRDs.
    
    Expected payload format:
    {
        "prompt": "User message",
        "text": "Alternative text field",
        "session_id": "Optional session ID",
        "runtime_session_id": "Runtime session ID"
    }
    """
    global _current_user_id
    try:
        logger.info("=" * 80)
        logger.info("[ANALYST-AGENT] Handler invoked (Strands + Lambda Tools)")
        logger.info("=" * 80)

        # Stash user_id so tools can attribute Lambda LLM calls.
        _current_user_id = payload.get("user_id") or None
        logger.info(f"[ANALYST-AGENT] user_id={_current_user_id or 'unknown'}")

        # Extract user message
        user_message = payload.get("prompt") or payload.get("text") or payload.get("message", "Hello! I'd like to create a BRD.")

        logger.info(f"[ANALYST-AGENT] User message: {user_message[:200]}...")
        logger.info(f"[ANALYST-AGENT] Payload keys: {list(payload.keys())}")
        
        # Get or create session ID
        session_id = payload.get("session_id") or payload.get("runtime_session_id")
        if not session_id:
            session_id = f"analyst-session-{str(uuid.uuid4())}"
            logger.info(f"[ANALYST-AGENT] Generated new session ID: {session_id}")
        else:
            logger.info(f"[ANALYST-AGENT] Using session ID: {session_id}")
        
        # Check if this is a BRD generation request
        is_generate_request = any(keyword in user_message.lower() for keyword in [
            "generate brd", "create brd", "generate document", "create document",
            "generate the brd", "create the brd", "make brd", "build brd"
        ])
        
        # If it's a generate request, use the generate_brd_from_history tool directly
        if is_generate_request:
            logger.info(f"[ANALYST-AGENT] Detected BRD generation request, calling generate_brd_from_history tool")
            try:
                result_text = generate_brd_from_history(session_id=session_id)
                return json.dumps({
                    "result": result_text,
                    "session_id": session_id,
                    "message": result_text
                })
            except Exception as e:
                error_msg = f"Error generating BRD: {str(e)}"
                logger.error(f"[ANALYST-AGENT] {error_msg}")
                return json.dumps({
                    "result": error_msg,
                    "session_id": session_id,
                    "message": error_msg
                })
        
        # For all other messages, ALWAYS call gather_requirements to ensure messages are stored
        logger.info(f"[ANALYST-AGENT] Calling gather_requirements tool to store message and get response")
        try:
            result_text = gather_requirements(session_id=session_id, user_message=user_message)
            
            # Return the response from gather_requirements
            return {
                "result": result_text,
                "session_id": session_id,
                "message": result_text
            }
        except Exception as e:
            error_msg = f"Error in requirements gathering: {str(e)}"
            logger.error(f"[ANALYST-AGENT] {error_msg}")
            logger.exception("Exception details:")

            # Fallback: try using the agent directly if tool call fails
            logger.info(f"[ANALYST-AGENT] Falling back to direct agent call")
            agent = _get_agent()
            
            # Build prompt for the agent with session context
            enhanced_prompt = f"""Session ID: {session_id}

User's message: {user_message}

Please help the user with their BRD requirements gathering or generation request."""
        
        try:
            # Invoke the agent (fallback only)
            result = agent(enhanced_prompt)
            _capture_strands_metrics(agent, _current_user_id, "analyst_agent_fallback")

            # Extract result text
            if hasattr(result, 'data'):
                result_text = str(result.data) if result.data else str(result)
            elif hasattr(result, 'output'):
                result_text = str(result.output)
            elif isinstance(result, str):
                result_text = result
            else:
                result_text = str(result)
            
            logger.info(f"[ANALYST-AGENT] Agent response generated successfully")
            
            # Return dict with session_id (not JSON string - let AgentCore handle serialization)
            return {
                "result": result_text,
                "session_id": session_id,
                "message": result_text
            }
            
        except Exception as e:
            error_msg = f"I apologize, but I encountered an error: {str(e)}"
            logger.error(f"[ANALYST-AGENT] Error in agent execution: {str(e)}")
            logger.exception("Exception details:")
            
            return {
                "result": error_msg,
                "session_id": session_id,
                "message": error_msg
            }
    
    except Exception as e:
        logger.error(f"[ANALYST-AGENT] Error in invoke: {str(e)}")
        logger.exception("Exception details:")

        session_id = payload.get("session_id", "unknown")
        return {
            "result": f"Error: {str(e)}",
            "session_id": session_id,
            "message": f"Error: {str(e)}"
        }
    finally:
        _current_user_id = None


# Run the app when module is loaded (always run, not just when executed directly)
logger.info("[ANALYST-AGENT] Initializing AgentCore Runtime app...")
logger.info(f"[ANALYST-AGENT] Gateway Model: {GATEWAY_MODEL} via {DLXAI_GATEWAY_URL}")
logger.info(f"[ANALYST-AGENT] Lambda Requirements Gathering ARN: {LAMBDA_REQUIREMENTS_GATHERING_ARN}")
logger.info(f"[ANALYST-AGENT] Lambda BRD from History ARN: {LAMBDA_BRD_FROM_HISTORY_ARN}")
app.run()
