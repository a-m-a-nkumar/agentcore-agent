"""
BRD from History Lambda Function
Fetches conversation history from AgentCore Memory and generates BRD
"""

import json
import logging
import os
import uuid
from typing import List, Dict

import boto3

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
AGENTCORE_MEMORY_ID = os.getenv('AGENTCORE_MEMORY_ID', 'Test-DGwqpP7Rvj')
AGENTCORE_ACTOR_ID = os.getenv('AGENTCORE_ACTOR_ID', 'analyst-session')
LAMBDA_BRD_GENERATOR = os.getenv('LAMBDA_BRD_GENERATOR', 'brd_generator_lambda')
S3_BUCKET = os.getenv('S3_BUCKET_NAME', 'test-development-bucket-siriusai')
TEMPLATE_S3_KEY = 'templates/Deluxe_BRD_Template_v2+2.docx'

# Lazy loading
_lambda_client = None
_agentcore_memory_client = None


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client('lambda', region_name=AWS_REGION)
    return _lambda_client


def _get_agentcore_memory_client():
    global _agentcore_memory_client
    if _agentcore_memory_client is None:
        _agentcore_memory_client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)
    return _agentcore_memory_client


def get_conversation_history(session_id: str, max_messages: int = 99) -> List[Dict]:
    """Get conversation history from AgentCore Memory"""
    client = _get_agentcore_memory_client()
    
    max_results = min(max_messages, 99)  # API constraint
    
    try:
        logger.info(f"Fetching conversation history for session: {session_id}")
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
        logger.error(f"Error retrieving history: {e}", exc_info=True)
        return []


def format_as_transcript(messages: List[Dict]) -> str:
    """Format conversation history as a transcript"""
    transcript_lines = []
    
    for msg in messages:
        role = msg.get("role", "assistant").capitalize()
        content = msg.get("content", "")
        transcript_lines.append(f"{role}: {content}")
    
    transcript = "\n\n".join(transcript_lines)
    logger.info(f"Formatted transcript: {len(transcript)} characters")
    return transcript


def invoke_brd_generator(transcript: str, brd_id: str) -> dict:
    """Invoke the BRD generator Lambda"""
    lambda_client = _get_lambda_client()
    
    payload = {
        'template_s3_bucket': S3_BUCKET,
        'template_s3_key': TEMPLATE_S3_KEY,
        'transcript': transcript,
        'brd_id': brd_id
    }
    
    logger.info(f"Invoking BRD generator Lambda with BRD ID: {brd_id}")
    logger.info(f"Transcript length: {len(transcript)} chars")
    
    try:
        response = lambda_client.invoke(
            FunctionName=LAMBDA_BRD_GENERATOR,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        response_payload = json.loads(response['Payload'].read())
        
        if 'FunctionError' in response:
            error_msg = response_payload.get('errorMessage', 'Unknown error')
            logger.error(f"BRD generator error: {error_msg}")
            raise Exception(f"BRD generator error: {error_msg}")
        
        logger.info("BRD generator completed successfully")
        return response_payload
        
    except Exception as e:
        logger.error(f"Error invoking BRD generator: {e}", exc_info=True)
        raise


def lambda_handler(event, context):
    """
    Lambda handler for BRD generation from conversation history.
    
    Expected event:
    {
        "session_id": "analyst-session-xxx",
        "brd_id": "optional-brd-id"
    }
    """
    logger.info("=== BRD from History Lambda Started ===")
    logger.info(f"Event: {json.dumps(event, default=str)}")
    
    try:
        # Extract inputs
        session_id = event.get('session_id')
        brd_id = event.get('brd_id')
        
        if not session_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': 'Missing required field: session_id'
                })
            }
        
        # Generate BRD ID if not provided
        if not brd_id:
            brd_id = str(uuid.uuid4())
            logger.info(f"Generated new BRD ID: {brd_id}")
        else:
            logger.info(f"Using provided BRD ID: {brd_id}")
        
        # Fetch conversation history
        messages = get_conversation_history(session_id)
        
        if not messages:
            logger.warning("No conversation history found, proceeding with minimal transcript")
            messages = [{
                "role": "user",
                "content": "Requirements gathering session"
            }]
        
        # Format as transcript
        transcript = format_as_transcript(messages)
        
        # Invoke BRD generator Lambda
        generator_result = invoke_brd_generator(transcript, brd_id)
        
        # Parse generator response
        if isinstance(generator_result, dict):
            if 'statusCode' in generator_result:
                body = generator_result.get('body', '{}')
                if isinstance(body, str):
                    body = json.loads(body)
                message = body.get('message', 'BRD generated successfully')
            else:
                message = generator_result.get('message', 'BRD generated successfully')
        else:
            message = 'BRD generated successfully'
        
        logger.info(f"BRD generation completed. BRD ID: {brd_id}")
        
        # Return success
        return {
            'statusCode': 200,
            'body': json.dumps({
                'brd_id': brd_id,
                'message': message,
                'status': 'success',
                's3_location': f's3://{S3_BUCKET}/brds/{brd_id}/'
            })
        }
        
    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'message': 'Error generating BRD from history'
            })
        }
