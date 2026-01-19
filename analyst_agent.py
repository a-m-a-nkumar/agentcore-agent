"""
Business Analyst Agent for AgentCore Runtime using Strands framework
This agent conducts structured Q&A sessions to gather requirements and generate BRDs.
Inspired by BMad Method's analyst persona.
"""

import json
import os
import uuid
from typing import Optional, Dict, List

from bedrock_agentcore import BedrockAgentCoreApp
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

def create_analyst_session(project_id: Optional[str] = None) -> Dict[str, str]:
    """Create a new AgentCore Memory session for requirements gathering"""
    if not project_id:
        project_id = str(uuid.uuid4())
    
    session_id = f"analyst-session-{project_id}"
    client = _get_agentcore_memory_client()
    
    try:
        # Create memory session
        response = client.create_session(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID
        )
        
        print(f"[ANALYST-AGENT] Created session: {session_id}", flush=True)
        
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
        
        add_message_to_memory(session_id, "assistant", welcome_msg)
        
        return {
            "session_id": session_id,
            "project_id": project_id,
            "message": welcome_msg
        }
    except Exception as e:
        print(f"[ANALYST-AGENT] Error creating session: {e}", flush=True)
        raise

def add_message_to_memory(session_id: str, role: str, content: str):
    """Add a message to AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    try:
        client.create_event(
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
        print(f"[ANALYST-AGENT] Added {role} message to session {session_id}", flush=True)
    except Exception as e:
        print(f"[ANALYST-AGENT] Error adding message to memory: {e}", flush=True)
        raise

def get_conversation_history(session_id: str, max_messages: int = 200) -> List[Dict]:
    """Get full conversation history from AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    try:
        response = client.list_events(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            includePayloads=True,
            maxResults=max_messages
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
    Get the standard BRD template structure.
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
    """
    messages = get_conversation_history(session_id)
    
    if len(messages) < 10:  # Too few messages
        return "INCOMPLETE: Need more information. Continue asking questions."
    
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
    Reuses the existing brd_generator_lambda.
    
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
        return "Error: No conversation history found. Please have a conversation first."
    
    # 2. Format as transcript
    transcript = format_conversation_as_transcript(messages)
    print(f"[ANALYST-AGENT] Formatted transcript: {len(transcript)} characters", flush=True)
    
    # 3. Get template
    template = get_brd_template()
    
    # 4. Call BRD generator Lambda (existing)
    payload = {
        "template": template,
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
            tools = [get_brd_template, check_requirements_completeness, generate_brd_from_conversation]
            agent = Agent(model=model, tools=tools)
            
            if not fresh:
                _agent_instance = agent
                print("[ANALYST-AGENT] Strands agent initialized", flush=True)
            else:
                print("[ANALYST-AGENT] Created fresh agent instance", flush=True)
            
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
        
        if not session_id:
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
- When you have enough information, use check_requirements_completeness tool, then suggest using generate_brd_from_conversation to create the BRD

SESSION ID: {session_id}

USER'S MESSAGE: {user_message}

Respond naturally as Mary, ask follow-up questions, and guide the conversation to gather all necessary requirements."""
            
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
                
                return result_text
                
            except Exception as e:
                error_msg = f"I apologize, but I encountered an error: {str(e)}"
                add_message_to_memory(session_id, "assistant", error_msg)
                return error_msg
        
        else:
            # Existing session - continue conversation
            add_message_to_memory(session_id, "user", user_message)
            
            # Get conversation history for context
            history = get_conversation_history(session_id)
            history_text = format_conversation_as_transcript(history[-20:])  # Last 20 messages for context
            
            # Get agent
            agent = _get_agent()
            
            # Build enhanced prompt with history
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

CONVERSATION HISTORY:
{history_text}

SESSION ID: {session_id}

Continue the conversation naturally. Ask follow-up questions based on what you've learned.
When you have enough information, use check_requirements_completeness to verify, then suggest generating the BRD.

USER'S CURRENT MESSAGE: {user_message}

Respond appropriately as Mary and continue gathering requirements."""
            
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
                
                return result_text
                
            except Exception as e:
                error_msg = f"I apologize, but I encountered an error: {str(e)}"
                add_message_to_memory(session_id, "assistant", error_msg)
                return error_msg
    
    except Exception as e:
        print(f"[ANALYST-AGENT] Error in invoke: {str(e)}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        return f"Error: {str(e)}"

