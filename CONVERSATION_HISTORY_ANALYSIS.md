# Requirements Gathering Lambda - Conversation History Analysis

## Current State

### ✅ Conversation History IS ALREADY IMPLEMENTED!

The `lambda_requirements_gathering.py` file ALREADY has full conversation history support:

1. **Stores messages** in AgentCore Memory (lines 266, 313)
2. **Retrieves history** from AgentCore Memory (line 269)
3. **Builds context** from history (line 270)
4. **Includes in prompt** to Claude (lines 273-279)

## How It Works

```python
# 1. Store user message
add_message_to_memory(session_id, 'user', user_message)

# 2. Get conversation history
history = get_conversation_history(session_id, MAX_HISTORY_MESSAGES)
# Returns: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]

# 3. Build conversation context
conversation_context = build_conversation_context(history)
# Returns: "Previous conversation:\nUser: ...\nAssistant: ..."

# 4. Include in prompt to Claude
full_prompt = f"""{MARY_REQUIREMENTS_PROMPT}

{conversation_context}

User's latest message: {user_message}

Respond as Mary..."""

# 5. Store Claude's response
add_message_to_memory(session_id, 'assistant', assistant_response)
```

## Why It Might Not Be Working

### Issue 1: Lambda Not Deployed
- The code exists locally but might not be deployed to AWS
- **Solution:** Deploy the Lambda using deployment script

### Issue 2: Session ID Changes
- Frontend might be creating a new session ID for each message
- **Solution:** Check frontend - it should reuse the same session_id

### Issue 3: Actor ID Mismatch
- Current actor ID: `analyst-session` (line 21)
- If frontend/analyst agent uses a different actor ID, messages won't be found
- **Solution:** Ensure consistent actor ID everywhere

### Issue 4: Empty History Retrieved
- AgentCore Memory might not be returning events
- Could be permissions issue or wrong memory ID
- **Solution:** Check CloudWatch logs for "Retrieved X messages from history"

## Debugging Steps

### Step 1: Check CloudWatch Logs

Look for these log messages in the Lambda execution logs:

```
Adding user message to session {session_id}
Retrieved {X} messages from history
Calling Bedrock with prompt length: {X} chars
Generated response length: {X} chars
```

### Step 2: Verify Session ID Consistency

Check if the same session ID is being used across multiple requests:

```
Session ID: analyst-session-abc123  <- Should be the same every time
```

### Step 3: Check Actor ID

Verify the actor ID matches:
- **Lambda:** `analyst-session` (line 21)
- **Frontend:** Should also be `analyst-session`
- **Analyst Agent:** Should also be `analyst-session`

### Step 4: Test Memory Storage

Add this test to verify messages are being stored:

1. Send message: "My project is called NamoAI"
2. Check logs: Should see "Adding user message to session..."
3. Send another message: "What's my project name?"
4. Check logs: Should see "Retrieved 2 messages from history"
5. Response should reference "NamoAI"

## Quick Fix: Add More Logging

To debug, add these logs to the Lambda:

**After line 269:**
```python
history = get_conversation_history(session_id, MAX_HISTORY_MESSAGES)
logger.info(f"DEBUG: Retrieved {len(history)} messages")
if history:
    logger.info(f"DEBUG: First message: {history[0]}")
    logger.info(f"DEBUG: Last message: {history[-1]}")
```

**After line 270:**
```python
conversation_context = build_conversation_context(history)
logger.info(f"DEBUG: Context:\n{conversation_context}")
```

Then redeploy and check CloudWatch logs.

## Configuration Check

Verify these environment variables in the deployed Lambda:

```
AGENTCORE_MEMORY_ID=Test-DGwqpP7Rvj
AGENTCORE_ACTOR_ID=analyst-session
```

## Expected Behavior

**First Message:**
- No history retrieved
- Context: "This is the start of a new conversation."
- Mary introduces herself

**Second Message:**
- History contains first exchange
- Context: "Previous conversation:\nUser: ...\nAssistant: ..."
- Mary references what was said before

**Third Message:**
- History contains both previous exchanges
- Mary should remember project name, etc.

## Conclusion

**The code is correct!** The conversation history functionality is fully implemented and should work. The issue is likely:

1. Lambda not deployed with this code
2. Session ID not being reused by frontend
3. Actor ID mismatch between components

Check the deployment and session management first!
