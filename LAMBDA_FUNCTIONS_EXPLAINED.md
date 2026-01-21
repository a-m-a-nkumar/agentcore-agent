# Lambda Functions Explanation

## Overview

This document explains the two new Lambda functions that support the Business Analyst Agent's requirements gathering and BRD generation workflow.

---

## 1. Requirements Gathering Lambda (`requirements_gathering_lambda`)

**ARN:** `arn:aws:lambda:us-east-1:448049797912:function:requirements_gathering_lambda`

### Purpose

This Lambda function conducts structured, conversational requirements gathering using "Mary's" persona (a Strategic Business Analyst). It acts as the conversational engine that guides users through discovering and documenting their business requirements.

### What It Does

1. **Receives User Messages**
   - Takes a `session_id` and `user_message` as input
   - Each message is part of an ongoing requirements gathering conversation

2. **Stores Conversation in AgentCore Memory**
   - Saves the user's message to AgentCore Memory with the specified `session_id`
   - This ensures all conversation history is preserved for later BRD generation

3. **Retrieves Conversation History**
   - Fetches up to 99 previous messages from AgentCore Memory
   - Uses this history to maintain context and continuity in the conversation

4. **Generates Mary's Response**
   - Uses Claude (Bedrock) with a specialized prompt that embodies "Mary's" persona
   - Mary is a Strategic Business Analyst who:
     - Asks thoughtful, one-at-a-time questions
     - Builds on what the user has already shared
     - Reflects understanding before moving forward
     - Allows ambiguity early and reduces it gradually
     - Never blocks progress due to missing information
   - The response is conversational, analytical, and genuinely interested

5. **Stores Assistant Response**
   - Saves Mary's response back to AgentCore Memory
   - This completes the conversation turn (user → assistant)

6. **Returns Response**
   - Returns Mary's response text to the agent
   - The agent then presents this to the user

### Input Format

```json
{
  "session_id": "analyst-session-xxx",
  "user_message": "I want to build an AI-powered customer service platform"
}
```

### Output Format

```json
{
  "statusCode": 200,
  "body": {
    "response": "That sounds like an exciting project! Let me help you explore this...",
    "session_id": "analyst-session-xxx",
    "status": "success"
  }
}
```

### Key Features

- **Persona-Driven**: Uses Mary's Strategic Business Analyst persona consistently
- **Context-Aware**: Maintains conversation history for continuity
- **Non-Blocking**: Never stops progress due to incomplete information
- **Memory Integration**: All conversations are stored in AgentCore Memory for later use

### When It's Called

- When the analyst agent receives a user message during requirements gathering
- The agent calls `gather_requirements(session_id, user_message)` tool
- This tool internally invokes this Lambda function

---

## 2. BRD from History Lambda (`brd_from_history_lambda`)

**ARN:** `arn:aws:lambda:us-east-1:448049797912:function:brd_from_history_lambda`

### Purpose

This Lambda function generates a Business Requirements Document (BRD) from the conversation history stored in AgentCore Memory. It acts as the bridge between the requirements gathering conversation and the final BRD document.

### What It Does

1. **Receives Session ID**
   - Takes a `session_id` (and optional `brd_id`) as input
   - The `session_id` identifies which conversation history to use

2. **Fetches Conversation History**
   - Retrieves all messages from AgentCore Memory for the specified `session_id`
   - Gets up to 99 messages (API limit)
   - Extracts the conversational content (role and text) from each event

3. **Formats as Transcript**
   - Converts the conversation history into a transcript format:
     ```
     User: [user's message]
     
     Assistant: [Mary's response]
     
     User: [next message]
     
     Assistant: [next response]
     ...
     ```
   - This transcript format is what the BRD generator Lambda expects

4. **Invokes BRD Generator Lambda**
   - Calls the `brd_generator_lambda` with:
     - The formatted transcript
     - Template location in S3 (`templates/Deluxe_BRD_Template_v2+2.docx`)
     - The BRD ID (auto-generated if not provided)
   - The BRD generator Lambda then:
     - Downloads the template from S3
     - Uses Claude (Bedrock) to generate a comprehensive BRD from the transcript
     - Saves the BRD structure (JSON) and rendered text to S3

5. **Returns BRD ID**
   - Returns the BRD ID and success message
   - The BRD can now be downloaded, viewed, or edited using the BRD chat agent

### Input Format

```json
{
  "session_id": "analyst-session-xxx",
  "brd_id": "optional-brd-id"  // Optional, will be auto-generated if not provided
}
```

### Output Format

```json
{
  "statusCode": 200,
  "body": {
    "brd_id": "c5d9be14-f0bd-4d02-a33e-36dc55fb8c0b",
    "message": "BRD generated successfully",
    "status": "success",
    "s3_location": "s3://test-development-bucket-siriusai/brds/c5d9be14-f0bd-4d02-a33e-36dc55fb8c0b/"
  }
}
```

### Key Features

- **Works with Any Amount of History**: Even a single message can generate a BRD (though more conversation = better BRD)
- **Automatic Transcript Formatting**: Converts AgentCore Memory events into the format expected by the BRD generator
- **Template Integration**: Uses the BRD template already stored in S3
- **Idempotent**: Can be called multiple times with the same session_id to regenerate the BRD

### When It's Called

- When the user requests to generate a BRD (e.g., "generate the BRD", "create the document", "I'm done")
- The agent calls `generate_brd_from_history(session_id, brd_id)` tool
- This tool internally invokes this Lambda function

---

## Workflow Integration

### Complete Flow

1. **User starts conversation** → Analyst Agent receives message
2. **Agent calls `gather_requirements`** → Requirements Gathering Lambda
   - Stores user message in AgentCore Memory
   - Generates Mary's response using Claude
   - Stores Mary's response in AgentCore Memory
   - Returns response to agent
3. **Agent presents response** → User sees Mary's question/comment
4. **User responds** → Steps 2-3 repeat (conversation continues)
5. **User requests BRD generation** → Agent calls `generate_brd_from_history`
   - BRD from History Lambda fetches all conversation history
   - Formats it as a transcript
   - Invokes BRD Generator Lambda
   - BRD Generator creates the document and saves to S3
   - Returns BRD ID
6. **User can download/view/edit BRD** → Using the BRD ID

### Why Separate Lambdas?

- **Separation of Concerns**: Requirements gathering logic is separate from BRD generation logic
- **Reusability**: The BRD from History Lambda can be used by other agents or workflows
- **Scalability**: Each Lambda can be scaled independently based on usage
- **Maintainability**: Easier to update one function without affecting the other
- **Testing**: Each Lambda can be tested independently

---

## Environment Variables

### Requirements Gathering Lambda

- `BEDROCK_MODEL_ID`: Claude model to use (default: `global.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- `AGENTCORE_MEMORY_ID`: AgentCore Memory ID (default: `Test-DGwqpP7Rvj`)
- `AGENTCORE_ACTOR_ID`: Actor ID for memory (default: `analyst-session`)

### BRD from History Lambda

- `AGENTCORE_MEMORY_ID`: AgentCore Memory ID (default: `Test-DGwqpP7Rvj`)
- `AGENTCORE_ACTOR_ID`: Actor ID for memory (default: `analyst-session`)
- `LAMBDA_BRD_GENERATOR`: Name of BRD generator Lambda (default: `brd_generator_lambda`)
- `S3_BUCKET_NAME`: S3 bucket for templates and BRDs (default: `test-development-bucket-siriusai`)

---

## Error Handling

Both Lambdas include comprehensive error handling:

- **Missing Required Fields**: Returns 400 with clear error message
- **AgentCore Memory Errors**: Logs error but allows conversation to continue (for requirements gathering)
- **Lambda Invocation Errors**: Returns 500 with error details
- **All Errors Logged**: CloudWatch logs contain detailed error information for debugging

---

## Dependencies

Both Lambdas only require:
- **boto3**: Built into Lambda Python runtime (no external dependencies needed)
- **AWS Permissions**: 
  - Bedrock invoke permissions (for requirements gathering)
  - AgentCore Memory read/write permissions
  - Lambda invoke permissions (for BRD from History → BRD Generator)
  - S3 read permissions (for BRD Generator)

---

## Summary

- **Requirements Gathering Lambda**: Handles the conversational requirements gathering using Mary's persona
- **BRD from History Lambda**: Generates BRDs from conversation history stored in AgentCore Memory

Together, these Lambdas enable a complete workflow from requirements gathering conversation to final BRD document, all while maintaining conversation history in AgentCore Memory for future reference and regeneration.

