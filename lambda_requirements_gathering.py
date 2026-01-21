"""
Requirements Gathering Lambda Function
Conducts conversation using Mary's persona and stores in AgentCore Memory
"""

import json
import logging
import os
from typing import List, Dict, Optional

import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'Test-DGwqpP7Rvj')
AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'analyst-session')
MAX_TOKENS = 2000
TEMPERATURE = 0.7
MAX_HISTORY_MESSAGES = 99

# Lazy loading
_bedrock_runtime = None
_agentcore_memory_client = None

# Strategic Business Analyst and Requirements Discovery Prompt
MARY_REQUIREMENTS_PROMPT = """You are a Strategic Business Analyst and Requirements Discovery Expert.

YOUR ROLE
You help users explore, clarify, and shape their business requirements through
natural, thoughtful conversation.

Your responsibility is discovery, not documentation.
A complete BRD is an eventual outcome — not an immediate objective.

It is acceptable for information to be incomplete early on.
Progress and understanding matter more than completeness.

────────────────────────────
PERSONALITY & TONE
- Curious, thoughtful, and genuinely interested
- Analytical but conversational — never interrogative
- You sound like a smart analyst thinking out loud with the user
- You enjoy uncovering patterns and acknowledge insights openly

Use "I" and "you" naturally.
Avoid rigid or template-heavy language.

────────────────────────────
CONVERSATION PRINCIPLES
- Ask one clear question at a time (occasionally two if closely related)
- Build on what the user has already shared
- Reflect understanding before moving forward
- Allow ambiguity early; reduce it gradually
- Prefer examples over abstractions
- Guide the conversation — never force it

If something is unclear → ask.
If something is partial → continue and note it.
If assumptions are required → state them explicitly and confirm later.

Never block progress due to missing information.

────────────────────────────
DISCOVERY INTELLIGENCE
You are continuously building an internal understanding of:
- The problem and why it matters
- Who is affected and how
- Desired outcomes and success signals
- Functional expectations and constraints
- Risks, assumptions, and dependencies

Follow the user's depth and energy.
Do not chase completeness.

────────────────────────────
BRD COVERAGE REFERENCE (INTERNAL ONLY)

A complete Business Requirements Document may include the following areas.
These exist as a coverage reference — not as a checklist.

Do NOT collect these in order.
Do NOT ask questions just to fill sections.
Many will naturally emerge through conversation and be completed later.

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

────────────────────────────
ANALYTICAL TOOLS (USE SELECTIVELY)
You may apply frameworks when they add clarity:
- Five Whys
- Jobs-to-be-Done
- Light SWOT reasoning
- Priority framing (Must / Should / Could / Later)

Never force a framework into the conversation.

────────────────────────────
WHAT YOU SHOULD NOT DO
- Do NOT run questionnaires
- Do NOT jump between unrelated topics
- Do NOT rush toward BRD generation
- Do NOT fabricate details
- Do NOT finalize prematurely

────────────────────────────
OPENING BEHAVIOR
Begin with curiosity, not structure.

If this is the first message, start with:
"Let's start simple — what problem are you trying to solve, or what triggered this idea?"

If continuing a conversation:
- Acknowledge what they've shared
- Reflect your understanding
- Ask a natural follow-up question that deepens insight"""


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


def add_message_to_memory(session_id: str, role: str, content: str):
    """Add a message to AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    try:
        logger.info(f"Adding {role} message to session {session_id}")
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
        logger.info(f"Successfully added {role} message to session {session_id}")
    except Exception as e:
        logger.error(f"Error adding message to memory: {e}")
        # Don't raise - allow conversation to continue


def get_conversation_history(session_id: str, max_messages: int = 99) -> List[Dict]:
    """Get conversation history from AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    max_results = min(max_messages, 99)  # API constraint
    
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
        
        logger.info(f"Retrieved {len(messages)} messages from history")
        return messages
        
    except Exception as e:
        logger.error(f"Error retrieving history: {e}")
        return []


def build_conversation_context(messages: List[Dict]) -> str:
    """Build conversation context for the prompt"""
    if not messages:
        return "This is the start of a new conversation."
    
    context_lines = ["Previous conversation:"]
    for msg in messages[-10:]:  # Last 10 messages for context
        role = msg['role'].capitalize()
        content = msg['content']
        context_lines.append(f"{role}: {content}")
    
    return "\n".join(context_lines)


def lambda_handler(event, context):
    """
    Lambda handler for requirements gathering conversation.
    
    Expected event:
    {
        "session_id": "analyst-session-xxx",
        "user_message": "User's message"
    }
    """
    logger.info("=== Requirements Gathering Lambda Started ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Extract inputs
        session_id = event.get('session_id')
        user_message = event.get('user_message')
        
        if not session_id or not user_message:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required fields: session_id and user_message'
                })
            }
        
        logger.info(f"Session ID: {session_id}")
        logger.info(f"User message: {user_message[:200]}...")
        
        # Store user message in memory
        add_message_to_memory(session_id, 'user', user_message)
        
        # Get conversation history
        history = get_conversation_history(session_id, MAX_HISTORY_MESSAGES)
        conversation_context = build_conversation_context(history)
        
        # Build full prompt
        full_prompt = f"""{MARY_REQUIREMENTS_PROMPT}

{conversation_context}

User's latest message: {user_message}

Respond as Mary. If this is the first message, introduce yourself warmly. Otherwise, acknowledge their response and ask a relevant follow-up question."""
        
        logger.info(f"Calling Bedrock with prompt length: {len(full_prompt)} chars")
        
        # Call Bedrock to generate response
        bedrock = _get_bedrock_runtime()
        response = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": full_prompt}]
                }
            ],
            inferenceConfig={
                "maxTokens": MAX_TOKENS,
                "temperature": TEMPERATURE
            }
        )
        
        # Extract response
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        
        assistant_response = "".join(
            block.get("text", "")
            for block in content_blocks
            if "text" in block
        )
        
        logger.info(f"Generated response length: {len(assistant_response)} chars")
        
        # Store assistant response in memory
        add_message_to_memory(session_id, 'assistant', assistant_response)
        
        # Return success
        return {
            'statusCode': 200,
            'body': json.dumps({
                'response': assistant_response,
                'session_id': session_id,
                'status': 'success'
            })
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Error in requirements gathering'
            })
        }
