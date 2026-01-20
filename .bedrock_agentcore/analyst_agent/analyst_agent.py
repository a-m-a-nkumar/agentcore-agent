"""
Business Analyst Agent for AgentCore Runtime using Strands framework
This agent conducts structured Q&A sessions to gather requirements and generate BRDs.
Inspired by BMad Method's analyst persona.
"""

import json
import os
import uuid
from typing import Optional, Dict, List

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
LAMBDA_GENERATOR = os.getenv('LAMBDA_BRD_GENERATOR', 'brd_generator_lambda')
AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'Test-DGwqpP7Rvj')
AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'analyst-session')

# Lazy loading
_lambda_client = None
_agentcore_memory_client = None
_agent_instance = None
_bedrock_runtime = None

def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        import boto3
        _lambda_client = boto3.client('lambda', region_name=AWS_REGION)
    return _lambda_client

def _get_agentcore_memory_client():
    global _agentcore_memory_client
    if _agentcore_memory_client is None:
        import boto3
        _agentcore_memory_client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)
        # Verify it's a boto3 client (should have create_event, list_events, etc.)
        if not hasattr(_agentcore_memory_client, 'create_event'):
            print(f"[ANALYST-AGENT] ERROR: Client type is {type(_agentcore_memory_client)}, expected boto3 client", flush=True)
            print(f"[ANALYST-AGENT] Available methods: {[m for m in dir(_agentcore_memory_client) if not m.startswith('_')][:20]}", flush=True)
    return _agentcore_memory_client

def _get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        import boto3
        _bedrock_runtime = boto3.client('bedrock-runtime', region_name=AWS_REGION)
    return _bedrock_runtime

def invoke_lambda_tool(function_name: str, payload: dict) -> dict:
    """Invoke a Lambda function as a tool"""
    try:
        lambda_client = _get_lambda_client()
        print(f"[ANALYST-AGENT] Invoking Lambda: {function_name}", flush=True)
        
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
        
        return response_payload
        
    except Exception as e:
        print(f"[ANALYST-AGENT] Error invoking Lambda {function_name}: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        raise

# --- AgentCore Memory Functions ---

def create_analyst_session(project_id: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, str]:
    """Create a new AgentCore Memory session for requirements gathering"""
    if not session_id:
        if not project_id:
            project_id = str(uuid.uuid4())
        session_id = f"analyst-session-{project_id}"
    else:
        # Use provided session_id (from runtime session)
        if not project_id:
            # Extract project_id from session_id if it follows the pattern
            if session_id.startswith("analyst-session-"):
                project_id = session_id.replace("analyst-session-", "")
            else:
                project_id = str(uuid.uuid4())
    
    client = _get_agentcore_memory_client()
    
    # Note: Sessions in AgentCore Memory are created automatically when you create events
    # We don't need to explicitly call create_session - it doesn't exist in the boto3 client
    # Instead, we'll create an initial event which will create the session automatically
    
    try:
        # Check if session already exists by trying to list events
        print(f"[ANALYST-AGENT] Checking if session {session_id} exists with actor {AGENTCORE_ACTOR_ID} in memory {AGENTCORE_MEMORY_ID}", flush=True)
        try:
            # Try to list events for this session to see if it exists
            list_response = client.list_events(
                memoryId=AGENTCORE_MEMORY_ID,
                sessionId=session_id,
                actorId=AGENTCORE_ACTOR_ID,
                maxResults=1
            )
            events = list_response.get('events', [])
            if events:
                print(f"[ANALYST-AGENT] ✅ Session {session_id} already exists", flush=True)
                # Session exists, return it
                return {
                    "session_id": session_id,
                    "project_id": project_id,
                    "message": "Continuing conversation..."
                }
        except Exception as list_err:
            # If list_events fails, the session doesn't exist yet - that's fine
            error_str = str(list_err).lower()
            if "not found" in error_str or "resourcenotfoundexception" in error_str:
                print(f"[ANALYST-AGENT] Session {session_id} doesn't exist yet, will be created with first event", flush=True)
            else:
                print(f"[ANALYST-AGENT] Could not check session existence: {list_err}", flush=True)
        
        # Session will be created automatically when we add the first message
        print(f"[ANALYST-AGENT] Session {session_id} will be created automatically with first event", flush=True)
        
        # Add welcome message
        welcome_msg = """Hello! I'm Mary, your Strategic Business Analyst. I'm here to help you create a comprehensive Business Requirements Document (BRD) through a structured conversation.

I'll ask you questions about your project to understand:
• Project purpose and objectives
• Business drivers and pain points
• Stakeholders and their roles
• Scope (what's in and out)
• Functional and non-functional requirements
• Constraints and assumptions
• Success criteria

Let's start! What is the main idea or goal of your project?"""
        
        # Only add welcome message if this is a new session (check if session already has messages)
        try:
            existing_messages = get_conversation_history(session_id, max_messages=1)
            if not existing_messages:
                # New session, add welcome message
                add_message_to_memory(session_id, "assistant", welcome_msg)
            else:
                # Session already exists, use a continuation message
                welcome_msg = "Continuing our conversation..."
        except:
            # If we can't check, assume it's new and add welcome message
            add_message_to_memory(session_id, "assistant", welcome_msg)
        
        return {
            "session_id": session_id,
            "project_id": project_id,
            "message": welcome_msg
        }
    except Exception as e:
        # If session already exists, that's fine - just return the session info
        if "already exists" in str(e).lower() or "ConflictException" in str(type(e).__name__):
            print(f"[ANALYST-AGENT] Session {session_id} already exists, reusing it", flush=True)
            return {
                "session_id": session_id,
                "project_id": project_id,
                "message": "Continuing conversation..."
            }
        print(f"[ANALYST-AGENT] Error creating session: {e}", flush=True)
        raise

def add_message_to_memory(session_id: str, role: str, content: str):
    """Add a message to AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    try:
        print(f"[ANALYST-AGENT] Attempting to add {role} message to session {session_id}, actor {AGENTCORE_ACTOR_ID}, memory {AGENTCORE_MEMORY_ID}", flush=True)
        response = client.create_event(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            payload=[
                {
                    "conversational": {
                        "role": role.lower(),
                        "content": {
                            "text": content
                        }
                    }
                }
            ]
        )
        print(f"[ANALYST-AGENT] ✅ Successfully added {role} message to session {session_id}", flush=True)
        print(f"[ANALYST-AGENT] Event response: {response}", flush=True)
    except Exception as e:
        print(f"[ANALYST-AGENT] ❌ Error adding message to memory: {e}", flush=True)
        import traceback
        print(f"[ANALYST-AGENT] Traceback: {traceback.format_exc()}", flush=True)
        # Don't raise - allow conversation to continue even if memory storage fails
        # This prevents the agent from crashing if memory is temporarily unavailable

def get_conversation_history(session_id: str, max_messages: int = 99) -> List[Dict]:
    """Get full conversation history from AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    # Ensure maxResults doesn't exceed API limit
    max_results = min(max_messages, 99)  # API constraint: must be <= 100
    
    try:
        response = client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=max_results
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
                
                role = conv_data.get("role", "assistant").lower()
                messages.append({
                    "role": role,
                    "content": text_content
                })
        
        print(f"[ANALYST-AGENT] Retrieved {len(messages)} messages from history", flush=True)
        return messages
        
    except Exception as e:
        print(f"[ANALYST-AGENT] Error retrieving history: {e}", flush=True)
        return []

def format_conversation_as_transcript(messages: List[Dict]) -> str:
    """Format conversation history as a transcript for BRD generation"""
    transcript_lines = []
    
    for msg in messages:
        role = msg.get("role", "assistant").capitalize()
        content = msg.get("content", "")
        transcript_lines.append(f"{role}: {content}")
    
    return "\n\n".join(transcript_lines)

# --- Tool Definitions ---

@tool
def get_brd_template() -> str:
    """
    Get the standard BRD template structure (for reference only).
    NOTE: The BRD generator tool already has access to the template stored in S3.
    You do NOT need to ask the user for a template - it's already available.
    This tool is only for your reference to understand the BRD structure.
    Returns the template text that defines the BRD format.
    """
    template = """Business Requirements Document (BRD) Template

1. Document Overview
   - Document Title
   - Project Name
   - Document Version
   - Date
   - Prepared By
   - Document Status

2. Purpose
   - Clear statement of the project's purpose and objectives

3. Background / Context
   - Business Drivers
   - Pain Points
   - Current State

4. Stakeholders
   - List of stakeholders with roles and responsibilities

5. Scope
   - In Scope
   - Out of Scope

6. Business Objectives & ROI
   - Financial Impact
   - Business Objectives

7. Functional Requirements
   - Detailed functional requirements with priorities

8. Non-Functional Requirements
   - Performance, scalability, security, compliance requirements

9. User Stories / Use Cases
   - User stories and use case descriptions

10. Assumptions
    - Key assumptions about the project

11. Constraints
    - Budget, timeline, technical, compliance constraints

12. Acceptance Criteria / KPIs
    - Success metrics and acceptance criteria

13. Timeline / Milestones
    - Project phases and milestones

14. Risks and Dependencies
    - Risk assessment and dependencies

15. Approval & Review
    - Approval workflow and review schedule

16. Glossary & Appendix
    - Acronyms, abbreviations, and reference documents
"""
    return template

@tool
def check_requirements_completeness(session_id: str) -> str:
    """
    Analyze the conversation history to determine if enough information has been gathered.
    Returns a status indicating if requirements are complete or what's still missing.
    NOTE: BRD generation can work with minimal information - this is just a check, not a blocker.
    """
    messages = get_conversation_history(session_id)
    
    # Removed minimum message requirement - BRD can be generated with any amount of information
    if len(messages) < 2:  # At least need user message and assistant response
        return "COMPLETE: Ready to generate BRD. You can use generate_brd_from_conversation even with minimal information."
    
    # Use Bedrock to analyze completeness
    conversation_text = format_conversation_as_transcript(messages)
    
    prompt = f"""Analyze this requirements gathering conversation and determine if we have enough information to generate a comprehensive BRD.

Conversation:
{conversation_text}

Evaluate if we have sufficient information about:
1. Project purpose and objectives
2. Business drivers and pain points
3. Stakeholders
4. Scope (in and out)
5. Functional requirements
6. Non-functional requirements
7. Constraints and assumptions
8. Success criteria

Respond with:
- "COMPLETE" if we have enough information
- "INCOMPLETE: [list missing areas]" if more information is needed

Your response:"""
    
    try:
        bedrock = _get_bedrock_runtime()
        response = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 500, "temperature": 0}
        )
        
        result = response['output']['message']['content'][0]['text']
        print(f"[ANALYST-AGENT] Completeness check: {result[:200]}...", flush=True)
        return result
        
    except Exception as e:
        print(f"[ANALYST-AGENT] Error checking completeness: {e}", flush=True)
        return "INCOMPLETE: Unable to analyze. Continue gathering requirements."

@tool
def generate_brd_from_conversation(session_id: str, brd_id: Optional[str] = None) -> str:
    """
    Generate a BRD from the conversation history stored in AgentCore Memory.
    The BRD generator tool already has access to the template stored in S3, so you don't need to provide it.
    This tool will:
    1. Retrieve all conversation history from AgentCore Memory
    2. Format it as a transcript
    3. Send it to the BRD generator Lambda along with the template location in S3
    4. The generator will create the BRD using Bedrock
    
    NOTE: This tool works with ANY amount of conversation history - even a single message is enough.
    The BRD generator will create a comprehensive document based on whatever information is available.
    
    Args:
        session_id: The session ID for the requirements gathering conversation
        brd_id: Optional BRD ID (auto-generated if not provided)
    
    Returns:
        Success message with BRD ID
    """
    if not brd_id:
        brd_id = str(uuid.uuid4())
    
    print(f"[ANALYST-AGENT] Generating BRD from conversation session: {session_id}", flush=True)
    
    # 1. Get conversation history
    messages = get_conversation_history(session_id)
    if not messages:
        # Even if no messages, we can still generate a BRD with minimal information
        # Create a minimal transcript from the session_id itself
        print(f"[ANALYST-AGENT] No conversation history found, but proceeding with BRD generation anyway", flush=True)
        messages = [{"role": "user", "content": "Project requirements gathering session"}]
    
    # 2. Format as transcript
    transcript = format_conversation_as_transcript(messages)
    print(f"[ANALYST-AGENT] Formatted transcript: {len(transcript)} characters", flush=True)
    
    # 3. Call BRD generator Lambda with S3 template location (template is already in S3)
    # The generator Lambda will fetch the template from S3 automatically
    s3_bucket = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
    template_s3_key = "templates/Deluxe_BRD_Template_v2+2.docx"
    
    payload = {
        "template_s3_bucket": s3_bucket,
        "template_s3_key": template_s3_key,
        "transcript": transcript,
        "brd_id": brd_id
    }
    
    try:
        result = invoke_lambda_tool(LAMBDA_GENERATOR, payload)
        
        # Parse response
        if isinstance(result, dict):
            if 'statusCode' in result:
                body = result.get('body', '{}')
                if isinstance(body, str):
                    body = json.loads(body)
                message = body.get('message', 'BRD generated successfully')
            else:
                message = result.get('message', 'BRD generated successfully')
        else:
            message = str(result)
        
        # Add success message to memory
        success_msg = f"✅ BRD generated successfully! BRD ID: {brd_id}\n\nYou can now view and edit this BRD using the BRD chat agent."
        add_message_to_memory(session_id, "assistant", success_msg)
        
        return f"{message}\n\nBRD ID: {brd_id}"
        
    except Exception as e:
        error_msg = f"Error generating BRD: {str(e)}"
        print(f"[ANALYST-AGENT] {error_msg}", flush=True)
        add_message_to_memory(session_id, "assistant", error_msg)
        return error_msg

# --- Agent Setup ---

def _get_agent(fresh=False):
    """Get Strands agent instance"""
    global _agent_instance
    
    if fresh or _agent_instance is None:
        try:
            model = BedrockModel(model_id=BEDROCK_MODEL_ID)
            
            # System prompt to ensure agent understands its role
            system_prompt = """You are Mary, a Strategic Business Analyst and Requirements Expert.

CRITICAL INSTRUCTIONS:
- The BRD template is ALREADY stored in S3 and available to the generator tool - DO NOT ask users for templates
- You are creating the transcript through conversation - DO NOT ask users for transcripts or meeting notes
- Your ONLY job is to gather information through structured Q&A conversation
- Focus on asking questions about: project purpose, business drivers, stakeholders, scope, requirements, constraints, success criteria
- When you have enough information, use check_requirements_completeness, then suggest generating the BRD
- The generate_brd_from_conversation tool will automatically use your conversation history as the transcript

Your persona: Excited, thorough, analytical. Ask one question at a time. Show enthusiasm when discovering important information."""
            
            tools = [get_brd_template, check_requirements_completeness, generate_brd_from_conversation]
            agent = Agent(model=model, tools=tools, system_prompt=system_prompt)
            
            if not fresh:
                _agent_instance = agent
                print("[ANALYST-AGENT] Strands agent initialized with system prompt", flush=True)
            else:
                print("[ANALYST-AGENT] Created fresh agent instance with system prompt", flush=True)
            
            return agent
            
        except Exception as e:
            print(f"[ANALYST-AGENT] Error initializing agent: {str(e)}", flush=True)
            import traceback
            print(traceback.format_exc(), flush=True)
            raise
    
    return _agent_instance

# --- Entry Point ---

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
        "project_id": "Optional project ID"
    }
    """
    try:
        print("=" * 80, flush=True)
        print("[ANALYST-AGENT] Handler invoked", flush=True)
        print("=" * 80, flush=True)
        
        # Extract user message
        user_message = payload.get("prompt") or payload.get("text") or payload.get("message", "Hello! I'd like to create a BRD.")
        
        print(f"[ANALYST-AGENT] User message: {user_message[:200]}...", flush=True)
        
        # Get or create session
        session_id = payload.get("session_id")
        project_id = payload.get("project_id")
        runtime_session_id = payload.get("runtime_session_id")  # Get runtime session ID if provided
        
        if not session_id:
            # Use runtime_session_id if provided, otherwise create new session
            if runtime_session_id:
                # Use the runtime session ID as the AgentCore Memory session ID for consistency
                session_id = runtime_session_id
                print(f"[ANALYST-AGENT] Using provided runtime_session_id as session_id: {session_id}", flush=True)
                # Create the session in AgentCore Memory with this ID
                try:
                    session_info = create_analyst_session(project_id, session_id)
                    initial_response = session_info["message"]
                except Exception as e:
                    # Session might already exist, that's fine
                    print(f"[ANALYST-AGENT] Session might already exist: {e}", flush=True)
                    initial_response = "Continuing conversation..."
            else:
                # Create new session
                session_info = create_analyst_session(project_id)
                session_id = session_info["session_id"]
                initial_response = session_info["message"]
            
            # Add user's first message
            add_message_to_memory(session_id, "user", user_message)
            
            # Get agent
            agent = _get_agent()
            
            # Build enhanced prompt with BMad-inspired persona
            enhanced_prompt = f"""You are Mary, a Strategic Business Analyst and Requirements Expert.

PERSONA:
- You are a senior analyst with deep expertise in market research, competitive analysis, and requirements elicitation
- You specialize in translating vague needs into actionable specifications
- You speak with the excitement of a treasure hunter - thrilled by every clue, energized when patterns emerge
- You structure insights with precision while making analysis feel like discovery

PRINCIPLES:
- Channel expert business analysis frameworks: Porter's Five Forces, SWOT analysis, root cause analysis, competitive intelligence
- Every business challenge has root causes waiting to be discovered
- Ground findings in verifiable evidence
- Articulate requirements with absolute precision
- Ensure all stakeholder voices are heard

YOUR ROLE:
You are conducting a structured interview to gather requirements for a Business Requirements Document (BRD).

You need to understand:
1. Project purpose and objectives
2. Business drivers and pain points (use root cause analysis)
3. Stakeholders and their roles (ensure all voices heard)
4. Scope (what's in and out)
5. Functional requirements (with precision)
6. Non-functional requirements (performance, security, compliance)
7. Constraints and assumptions
8. Success criteria and KPIs

CONVERSATION APPROACH:
- Ask one question at a time
- Be thorough but conversational
- Show excitement when you discover important information
- Use frameworks to dig deeper (e.g., "Let's analyze this using SWOT - what are the strengths and weaknesses?")
- IMPORTANT: The user can generate a BRD at ANY time, even with minimal information. The generate_brd_from_conversation tool works with any amount of conversation history.
- You can suggest generating the BRD after just a few questions, or let the user decide when to generate it
- If the user asks to generate a BRD, do it immediately - don't ask for more information first

IMPORTANT NOTES:
- The BRD template is ALREADY available in the generator tool (stored in S3) - DO NOT ask the user for a template
- You do NOT need a transcript from the user - you are creating the transcript through this conversation
- Your job is to gather information through conversation, not to ask for templates or pre-existing documents
- Focus on asking questions to understand the project requirements
- The generate_brd_from_conversation tool will automatically use the conversation history as the transcript

SESSION ID: {session_id}

USER'S MESSAGE: {user_message}

Respond naturally as Mary, ask follow-up questions, and guide the conversation to gather all necessary requirements. DO NOT ask for templates or transcripts - just gather information through conversation."""
            
            try:
                result = agent(enhanced_prompt)
                
                if hasattr(result, 'data'):
                    result_text = str(result.data) if result.data else str(result)
                elif hasattr(result, 'output'):
                    result_text = str(result.output)
                elif isinstance(result, str):
                    result_text = result
                else:
                    result_text = str(result)
                
                # Add agent response to memory
                add_message_to_memory(session_id, "assistant", result_text)
                
                # Return JSON with session_id so frontend can track it
                return json.dumps({
                    "result": result_text,
                    "session_id": session_id,
                    "message": result_text
                })
                
            except Exception as e:
                error_msg = f"I apologize, but I encountered an error: {str(e)}"
                add_message_to_memory(session_id, "assistant", error_msg)
                # Return JSON with session_id even on error
                return json.dumps({
                    "result": error_msg,
                    "session_id": session_id,
                    "message": error_msg
                })
        
        else:
            # Session ID provided - ensure it exists in AgentCore Memory
            try:
                # Try to get conversation history to verify session exists
                test_history = get_conversation_history(session_id, max_messages=1)
                if not test_history:
                    # Session doesn't exist, create it
                    print(f"[ANALYST-AGENT] Session {session_id} doesn't exist in Memory, creating it...", flush=True)
                    create_analyst_session(project_id, session_id)
            except Exception as e:
                # Session might not exist, create it
                print(f"[ANALYST-AGENT] Creating session {session_id} in Memory: {e}", flush=True)
                create_analyst_session(project_id, session_id)
            
            # Add user's first message
            add_message_to_memory(session_id, "user", user_message)
            
            # Get agent
            agent = _get_agent()
            
            # Build enhanced prompt with BMad-inspired persona
            enhanced_prompt = f"""You are Mary, a Strategic Business Analyst and Requirements Expert.

PERSONA:
- You are a senior analyst with deep expertise in market research, competitive analysis, and requirements elicitation
- You specialize in translating vague needs into actionable specifications
- You speak with the excitement of a treasure hunter - thrilled by every clue, energized when patterns emerge
- You structure insights with precision while making analysis feel like discovery

PRINCIPLES:
- Channel expert business analysis frameworks: Porter's Five Forces, SWOT analysis, root cause analysis, competitive intelligence
- Every business challenge has root causes waiting to be discovered
- Ground findings in verifiable evidence
- Articulate requirements with absolute precision
- Ensure all stakeholder voices are heard

YOUR ROLE:
You are conducting a structured interview to gather requirements for a Business Requirements Document (BRD).

You need to understand:
1. Project purpose and objectives
2. Business drivers and pain points (use root cause analysis)
3. Stakeholders and their roles (ensure all voices heard)
4. Scope (what's in and out)
5. Functional requirements (with precision)
6. Non-functional requirements (performance, security, compliance)
7. Constraints and assumptions
8. Success criteria and KPIs

CONVERSATION APPROACH:
- Ask one question at a time
- Be thorough but conversational
- Show excitement when you discover important information
- Use frameworks to dig deeper (e.g., "Let's analyze this using SWOT - what are the strengths and weaknesses?")
- IMPORTANT: The user can generate a BRD at ANY time, even with minimal information. The generate_brd_from_conversation tool works with any amount of conversation history.
- You can suggest generating the BRD after just a few questions, or let the user decide when to generate it
- If the user asks to generate a BRD, do it immediately - don't ask for more information first

IMPORTANT NOTES:
- The BRD template is ALREADY available in the generator tool (stored in S3) - DO NOT ask the user for a template
- You do NOT need a transcript from the user - you are creating the transcript through this conversation
- Your job is to gather information through conversation, not to ask for templates or pre-existing documents
- Focus on asking questions to understand the project requirements
- The generate_brd_from_conversation tool will automatically use the conversation history as the transcript

SESSION ID: {session_id}

USER'S MESSAGE: {user_message}

Respond naturally as Mary, ask follow-up questions, and guide the conversation to gather all necessary requirements. DO NOT ask for templates or transcripts - just gather information through conversation."""
            
            try:
                result = agent(enhanced_prompt)
                
                if hasattr(result, 'data'):
                    result_text = str(result.data) if result.data else str(result)
                elif hasattr(result, 'output'):
                    result_text = str(result.output)
                elif isinstance(result, str):
                    result_text = result
                else:
                    result_text = str(result)
                
                # Add agent response to memory
                add_message_to_memory(session_id, "assistant", result_text)
                
                # Return JSON with session_id so frontend can track it
                return json.dumps({
                    "result": result_text,
                    "session_id": session_id,
                    "message": result_text
                })
                
            except Exception as e:
                error_msg = f"I apologize, but I encountered an error: {str(e)}"
                add_message_to_memory(session_id, "assistant", error_msg)
                # Return JSON with session_id even on error
                return json.dumps({
                    "result": error_msg,
                    "session_id": session_id,
                    "message": error_msg
                })
    
    except Exception as e:
        print(f"[ANALYST-AGENT] Error in invoke: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        # Try to get session_id if it was created
        session_id = payload.get("session_id")
        if not session_id:
            # Try to create a session ID for error response
            try:
                session_info = create_analyst_session(payload.get("project_id"))
                session_id = session_info["session_id"]
            except:
                session_id = "unknown"
        return json.dumps({
            "result": f"Error: {str(e)}",
            "session_id": session_id,
            "message": f"Error: {str(e)}"
        })

# Always run the app when module is loaded (for both direct execution and module import)
# This ensures the app starts in Docker when run with "python -m analyst_agent"
print("[ANALYST-AGENT] Initializing AgentCore Runtime app...", flush=True)
print(f"[ANALYST-AGENT] Bedrock Model: {BEDROCK_MODEL_ID}", flush=True)
print(f"[ANALYST-AGENT] Lambda Generator: {LAMBDA_GENERATOR}", flush=True)
print(f"[ANALYST-AGENT] Memory ID: {AGENTCORE_MEMORY_ID}", flush=True)
print(f"[ANALYST-AGENT] Actor ID: {AGENTCORE_ACTOR_ID}", flush=True)

# Run the app - this will start the server and health check endpoint
# When run as module (python -m), this ensures the app starts
# When run directly (python analyst_agent.py), this also works
app.run()
