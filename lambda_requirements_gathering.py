"""
Requirements Gathering Lambda Function
Conducts conversation using Mary's persona and stores in AgentCore Memory
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional

import boto3

# Import prompts from centralized prompts module
from prompts import get_requirements_gathering_prompt

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
BEDROCK_MODEL_ID = os.getenv('BEDROCK_MODEL_ID', 'global.anthropic.claude-sonnet-4-5-20250929-v1:0')
BEDROCK_GUARDRAIL_ARN = os.getenv('BEDROCK_GUARDRAIL_ARN', '')
BEDROCK_GUARDRAIL_VERSION = os.getenv('BEDROCK_GUARDRAIL_VERSION', '1')
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'Test-DGwqpP7Rvj')
AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'analyst-session')
MAX_TOKENS = 2000
TEMPERATURE = 0.7
MAX_HISTORY_MESSAGES = 20

# Lazy loading
_bedrock_runtime = None
_agentcore_memory_client = None


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
    
    # Convert role to uppercase to match AgentCore enum: USER, ASSISTANT, TOOL, OTHER
    role_upper = role.upper()
    if role_upper not in ['USER', 'ASSISTANT', 'TOOL', 'OTHER']:
        # Default to USER if role doesn't match expected values
        role_upper = 'USER' if role.lower() in ['user', 'human'] else 'ASSISTANT'
    
    try:
        logger.info(f"Adding {role_upper} message to session {session_id}")
        response = client.create_event(
            memoryId=AGENTCORE_MEMORY_ID,
            sessionId=session_id,
            actorId=AGENTCORE_ACTOR_ID,
            eventTimestamp=datetime.utcnow(),
            payload=[
                {
                    "conversational": {
                        "role": role_upper,
                        "content": {
                            "text": content
                        }
                    }
                }
            ]
        )
        logger.info(f"Successfully added {role_upper} message to session {session_id}")
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
        
        # Build full prompt using the centralized prompt function (model needs full context)
        full_prompt = get_requirements_gathering_prompt(
            conversation_context=conversation_context,
            user_message=user_message
        )
        
        logger.info(f"Calling Bedrock with prompt length: {len(full_prompt)} chars")
        
        # Build user message content: full prompt for the model, but guardContent so the
        # guardrail only evaluates the user's actual input (avoids prompt-attack false positives
        # on our instruction block).
        user_content = [
            {"text": full_prompt},
            {"guardContent": {"text": {"text": user_message}}},
        ]
        
        # Call Bedrock to generate response
        bedrock = _get_bedrock_runtime()
        converse_kwargs = {
            "modelId": BEDROCK_MODEL_ID,
            "messages": [
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "inferenceConfig": {
                "maxTokens": MAX_TOKENS,
                "temperature": TEMPERATURE
            }
        }
        if BEDROCK_GUARDRAIL_ARN:
            converse_kwargs["guardrailConfig"] = {
                "guardrailIdentifier": BEDROCK_GUARDRAIL_ARN,
                "guardrailVersion": BEDROCK_GUARDRAIL_VERSION,
                "trace": "enabled",
            }
            logger.info(f"Guardrail enabled: ARN={BEDROCK_GUARDRAIL_ARN}, version={BEDROCK_GUARDRAIL_VERSION}")
        else:
            logger.warning("BEDROCK_GUARDRAIL_ARN not set - guardrail disabled")
        response = bedrock.converse(**converse_kwargs)
        
        # Log guardrail intervention if present
        stop_reason = response.get("stopReason", "")
        if stop_reason == "guardrail_intervened":
            logger.info("Guardrail intervened - blocked content replaced with guardrail message")
        
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
