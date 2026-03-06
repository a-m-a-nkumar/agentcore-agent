"""
Lambda function to retrieve BRDs from S3
This function is called by the Bedrock Agent to retrieve BRD content by ID
"""

import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "test-development-bucket-siriusai")
REGION = os.getenv("AWS_REGION", "us-east-1")

# S3 client
s3_client = boto3.client("s3", region_name=REGION)


def lambda_handler(event, context):
    """
    Retrieve BRD from S3 by ID
    
    Expected event format (from Bedrock Agent):
    {
        "brd_id": "uuid-string"
    }
    
    Returns:
    {
        "messageVersion": "1.0",
        "response": {
            "responseState": "SUCCESS",
            "responseBody": {
                "TEXT": {
                    "body": "BRD content..."
                }
            }
        }
    }
    """
    try:
        logger.info("=" * 80)
        logger.info("[BRD_RETRIEVER] Starting BRD retrieval")
        logger.info(f"[BRD_RETRIEVER] Event type: {type(event)}")
        logger.info(f"[BRD_RETRIEVER] Event: {json.dumps(event, indent=2, default=str)[:1000]}")
        
        # Wrap everything in try-catch to ensure we always return a valid response
        # Extract BRD ID from event
        # Bedrock Agent passes parameters in different formats
        brd_id = None
        
        # Bedrock Agent may pass the event as a dict with parameters
        if isinstance(event, dict):
            # Try direct parameter
            brd_id = event.get("brd_id") or event.get("brdId") or event.get("id")
            
            # If not found, check if it's nested in parameters
            if not brd_id and "parameters" in event:
                params = event["parameters"]
                # Handle both dict and list formats
                if isinstance(params, dict):
                    brd_id = params.get("brd_id") or params.get("brdId") or params.get("id")
                elif isinstance(params, list):
                    # List format: [{"name": "brd_id", "value": "..."}]
                    for param in params:
                        if isinstance(param, dict):
                            param_name = param.get("name")
                            param_value = param.get("value")
                            if param_name in ["brd_id", "brdId", "id"] and param_value:
                                brd_id = param_value
                                break
            
            # Check if event has actionGroupInput with parameters
            if not brd_id and "actionGroupInput" in event:
                action_input = event["actionGroupInput"]
                if isinstance(action_input, dict):
                    params = action_input.get("parameters", {})
                    if isinstance(params, dict):
                        brd_id = params.get("brd_id") or params.get("brdId") or params.get("id")
                    elif isinstance(params, list):
                        for param in params:
                            if isinstance(param, dict):
                                param_name = param.get("name")
                                param_value = param.get("value")
                                if param_name in ["brd_id", "brdId", "id"] and param_value:
                                    brd_id = param_value
                                    break
            
            # If still not found, check if event itself is the ID (string)
            if not brd_id and len(event) == 1:
                # Try to get the first value
                for key, value in event.items():
                    if isinstance(value, str) and len(value) > 30:  # UUIDs are 36 chars
                        brd_id = value
                        break
        
        # If event is a string, it might be the BRD ID itself
        elif isinstance(event, str) and len(event) > 30:
            brd_id = event
        
        if not brd_id:
            logger.error("[BRD_RETRIEVER] No BRD ID found in event")
            return {
                "messageVersion": "1.0",
                "response": {
                    "responseState": "FAILURE",
                    "responseBody": {
                        "TEXT": {
                            "body": f"Error: Missing brd_id parameter. Event received: {str(event)[:500]}"
                        }
                    }
                }
            }
        
        logger.info(f"[BRD_RETRIEVER] BRD ID: {brd_id}")
        
        # Construct S3 key
        brd_key = f"brds/{brd_id}/BRD_{brd_id}.txt"
        logger.info(f"[BRD_RETRIEVER] S3 Key: {brd_key}")
        logger.info(f"[BRD_RETRIEVER] S3 Bucket: {S3_BUCKET_NAME}")
        
        # Retrieve BRD from S3
        try:
            response = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=brd_key)
            brd_content = response["Body"].read().decode("utf-8")
            logger.info(f"[BRD_RETRIEVER] Successfully retrieved BRD: {len(brd_content)} characters")
            
            # Bedrock Agent expects specific response format
            # Format: {"messageVersion": "1.0", "response": {"responseBody": {"TEXT": {"body": "..."}}}}
            # CRITICAL: Keep response body under 4000 chars to avoid size limits
            max_content_length = 3000  # Leave room for metadata
            if len(brd_content) > max_content_length:
                truncated_content = brd_content[:max_content_length] + f"\n\n[... BRD truncated, total length: {len(brd_content)} characters ...]"
                response_message = f"BRD retrieved successfully. BRD ID: {brd_id}. Content length: {len(brd_content)} characters (truncated for display).\n\nBRD Content:\n{truncated_content}"
            else:
                response_message = f"BRD retrieved successfully. BRD ID: {brd_id}. Content length: {len(brd_content)} characters.\n\nBRD Content:\n{brd_content}"
            
            # Ensure response message is not too long
            if len(response_message) > 4000:
                response_message = response_message[:4000] + "..."
            
            try:
                response = {
                    "messageVersion": "1.0",
                    "response": {
                        "responseBody": {
                            "TEXT": {
                                "body": response_message
                            }
                        }
                    }
                }
                
                # Validate JSON
                json.dumps(response)
                logger.info(f"[BRD_RETRIEVER] Returning response. Message length: {len(response_message)} chars")
                return response
            except Exception as json_error:
                logger.error(f"[BRD_RETRIEVER] Error creating response JSON: {json_error}", exc_info=True)
                # Return minimal error response
                return {
                    "messageVersion": "1.0",
                    "response": {
                        "responseState": "FAILURE",
                        "responseBody": {
                            "TEXT": {
                                "body": f"Error formatting response: {str(json_error)[:500]}"
                            }
                        }
                    }
                }
            
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "NoSuchKey":
                logger.error(f"[BRD_RETRIEVER] BRD not found in S3: {brd_key}")
                return {
                    "messageVersion": "1.0",
                    "response": {
                        "responseState": "FAILURE",
                        "responseBody": {
                            "TEXT": {
                                "body": f"BRD not found: {brd_id}. S3 key: {brd_key}"
                            }
                        }
                    }
                }
            else:
                logger.error(f"[BRD_RETRIEVER] S3 error: {e}")
                # Don't raise - return error in proper format instead
                return {
                    "messageVersion": "1.0",
                    "response": {
                        "responseState": "FAILURE",
                        "responseBody": {
                            "TEXT": {
                                "body": f"S3 error ({error_code}): {str(e)[:500]}"
                            }
                        }
                    }
                }
        
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"[BRD_RETRIEVER] Exception: {type(e).__name__}")
        logger.error(f"[BRD_RETRIEVER] Error message: {str(e)}")
        logger.error(f"[BRD_RETRIEVER] Full traceback:", exc_info=True)
        logger.error("=" * 80)
        
        return {
            "messageVersion": "1.0",
            "response": {
                "responseState": "FAILURE",
                "responseBody": {
                    "TEXT": {
                        "body": f"Failed to retrieve BRD: {str(e)}"
                    }
                }
            }
        }

