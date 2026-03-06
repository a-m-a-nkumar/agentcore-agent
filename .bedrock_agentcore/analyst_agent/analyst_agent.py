"""
Business Analyst Agent for AgentCore Runtime using Strands framework
This agent uses Lambda functions as tools for requirements gathering and BRD generation.
"""

import json
import os
from typing import Optional

# Defensive import strategy for BedrockAgentCoreApp (same as my_agent)
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    print("[ANALYST-AGENT] Imported BedrockAgentCoreApp from bedrock_agentcore.runtime", flush=True)
except ImportError:
    try:
        from bedrock_agentcore import BedrockAgentCoreApp
        print("[ANALYST-AGENT] Imported BedrockAgentCoreApp from bedrock_agentcore", flush=True)
    except ImportError:
        try:
            from bedrock_agentcore.runtime.app import BedrockAgentCoreApp
            print("[ANALYST-AGENT] Imported BedrockAgentCoreApp from bedrock_agentcore.runtime.app", flush=True)
        except ImportError as e:
            print(f"[ANALYST-AGENT] Failed to import BedrockAgentCoreApp: {e}", flush=True)
            raise

from strands import Agent, tool
from strands.models import BedrockModel

# Initialize the AgentCore Runtime app
app = BedrockAgentCoreApp()

# Configuration
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')

# Lambda Function ARNs (can also use function names, but ARNs are more explicit)
LAMBDA_REQUIREMENTS_GATHERING_ARN = os.getenv(
    'LAMBDA_REQUIREMENTS_GATHERING_ARN',
    'arn:aws:lambda:us-east-1:448049797912:function:requirements_gathering_lambda'
)
LAMBDA_BRD_FROM_HISTORY_ARN = os.getenv(
    'LAMBDA_BRD_FROM_HISTORY_ARN',
    'arn:aws:lambda:us-east-1:448049797912:function:brd_from_history_lambda'
)

# For backward compatibility, also support function names
LAMBDA_REQUIREMENTS_GATHERING = os.getenv('LAMBDA_REQUIREMENTS_GATHERING', LAMBDA_REQUIREMENTS_GATHERING_ARN)
LAMBDA_BRD_FROM_HISTORY = os.getenv('LAMBDA_BRD_FROM_HISTORY', LAMBDA_BRD_FROM_HISTORY_ARN)

# Lazy loading of boto3 Lambda client
_lambda_client = None
# Lazy loading of Agent
_agent_instance = None


def _get_lambda_client():
    """Lazy load Lambda client to avoid initialization timeout"""
    global _lambda_client
    if _lambda_client is None:
        import boto3
        _lambda_client = boto3.client('lambda', region_name=AWS_REGION)
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
        print(f"[ANALYST-AGENT] Invoking Lambda: {function_name}", flush=True)
        print(f"[ANALYST-AGENT] Payload keys: {list(payload.keys())}", flush=True)
        
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        response_payload = json.loads(response['Payload'].read())
        
        if 'FunctionError' in response:
            error_msg = response_payload.get('errorMessage', 'Unknown Lambda error')
            print(f"[ANALYST-AGENT] Lambda error: {error_msg}", flush=True)
            raise Exception(f"Lambda function error: {error_msg}")
        
        print(f"[ANALYST-AGENT] Lambda response received successfully", flush=True)
        return response_payload
        
    except Exception as e:
        print(f"[ANALYST-AGENT] Error invoking Lambda {function_name}: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
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
        print(f"[ANALYST-AGENT] {error_msg}", flush=True)
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
        print(f"[ANALYST-AGENT] {error_msg}", flush=True)
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
            # Initialize Bedrock model
            model = BedrockModel(model_id=BEDROCK_MODEL_ID)
            
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
                print("[ANALYST-AGENT] Strands agent initialized with Lambda tools", flush=True)
            else:
                print("[ANALYST-AGENT] Created fresh Strands agent instance", flush=True)
            
            return agent
            
        except Exception as e:
            print(f"[ANALYST-AGENT] Error initializing agent: {str(e)}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
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
    try:
        print("=" * 80, flush=True)
        print("[ANALYST-AGENT] Handler invoked (Strands + Lambda Tools)", flush=True)
        print("=" * 80, flush=True)
        
        # Extract user message
        user_message = payload.get("prompt") or payload.get("text") or payload.get("message", "Hello! I'd like to create a BRD.")
        
        print(f"[ANALYST-AGENT] User message: {user_message[:200]}...", flush=True)
        print(f"[ANALYST-AGENT] Payload keys: {list(payload.keys())}", flush=True)
        
        # Get or create session ID
        session_id = payload.get("session_id") or payload.get("runtime_session_id")
        if not session_id:
            import uuid
            session_id = f"analyst-session-{str(uuid.uuid4())}"
            print(f"[ANALYST-AGENT] Generated new session ID: {session_id}", flush=True)
        else:
            print(f"[ANALYST-AGENT] Using session ID: {session_id}", flush=True)
        
        # Check if this is a BRD generation request
        is_generate_request = any(keyword in user_message.lower() for keyword in [
            "generate brd", "create brd", "generate document", "create document",
            "generate the brd", "create the brd", "make brd", "build brd"
        ])
        
        # If it's a generate request, use the generate_brd_from_history tool directly
        if is_generate_request:
            print(f"[ANALYST-AGENT] Detected BRD generation request, calling generate_brd_from_history tool", flush=True)
            try:
                result_text = generate_brd_from_history(session_id=session_id)
                return json.dumps({
                    "result": result_text,
                    "session_id": session_id,
                    "message": result_text
                })
            except Exception as e:
                error_msg = f"Error generating BRD: {str(e)}"
                print(f"[ANALYST-AGENT] {error_msg}", flush=True)
                return json.dumps({
                    "result": error_msg,
                    "session_id": session_id,
                    "message": error_msg
                })
        
        # For all other messages, ALWAYS call gather_requirements to ensure messages are stored
        print(f"[ANALYST-AGENT] Calling gather_requirements tool to store message and get response", flush=True)
        try:
            result_text = gather_requirements(session_id=session_id, user_message=user_message)
            
            # Return the response from gather_requirements
            return json.dumps({
                "result": result_text,
                "session_id": session_id,
                "message": result_text
            })
        except Exception as e:
            error_msg = f"Error in requirements gathering: {str(e)}"
            print(f"[ANALYST-AGENT] {error_msg}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            
            # Fallback: try using the agent directly if tool call fails
            print(f"[ANALYST-AGENT] Falling back to direct agent call", flush=True)
            agent = _get_agent()
            
            # Build prompt for the agent with session context
            enhanced_prompt = f"""Session ID: {session_id}

User's message: {user_message}

Please help the user with their BRD requirements gathering or generation request."""
        
        try:
            # Invoke the agent (fallback only)
            result = agent(enhanced_prompt)
            
            # Extract result text
            if hasattr(result, 'data'):
                result_text = str(result.data) if result.data else str(result)
            elif hasattr(result, 'output'):
                result_text = str(result.output)
            elif isinstance(result, str):
                result_text = result
            else:
                result_text = str(result)
            
            print(f"[ANALYST-AGENT] Agent response generated successfully", flush=True)
            
            # Return JSON with session_id
            return json.dumps({
                "result": result_text,
                "session_id": session_id,
                "message": result_text
            })
            
        except Exception as e:
            error_msg = f"I apologize, but I encountered an error: {str(e)}"
            print(f"[ANALYST-AGENT] Error in agent execution: {str(e)}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            
            return json.dumps({
                "result": error_msg,
                "session_id": session_id,
                "message": error_msg
            })
    
    except Exception as e:
        print(f"[ANALYST-AGENT] Error in invoke: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        
        session_id = payload.get("session_id", "unknown")
        return json.dumps({
            "result": f"Error: {str(e)}",
            "session_id": session_id,
            "message": f"Error: {str(e)}"
        })


# Run the app when module is loaded (always run, not just when executed directly)
print("[ANALYST-AGENT] Initializing AgentCore Runtime app...", flush=True)
print(f"[ANALYST-AGENT] Bedrock Model: {BEDROCK_MODEL_ID}", flush=True)
print(f"[ANALYST-AGENT] Lambda Requirements Gathering ARN: {LAMBDA_REQUIREMENTS_GATHERING_ARN}", flush=True)
print(f"[ANALYST-AGENT] Lambda BRD from History ARN: {LAMBDA_BRD_FROM_HISTORY_ARN}", flush=True)
app.run()
