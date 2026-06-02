# Testing JSON Extraction Fix

## The Issue
The analyst agent returns responses in this format:
```json
{
  "result": "Pikachu — noted...",
  "session_id": "fdd62309-7731-4f05-b0fb-791f92f3c499",
  "message": "Pikachu — noted..."
}
```

But the frontend was displaying this raw JSON instead of extracting the message text.

## The Fix

### Backend Changes (app.py)

1. **Created `extract_text_from_analyst_response()` helper function** (lines 1416-1492):
   - Handles 3 cases:
     - **Case 1**: Direct analyst response: `{"result": "...", "session_id": "...", "message": "..."}`
     - **Case 2**: AgentCore-wrapped: `{"result": "{\"result\": \"...\", ...}"}`
     - **Case 3**: Content array: `{"content": [{"text": "..."}]}`
   
2. **Call helper FIRST in `/analyst-chat` endpoint** (line 1559):
   - Before any other parsing
   - If extraction succeeds, return immediately
   - Otherwise, fall back to complex nested parsing

3. **Added comprehensive debug logging**:
   - `[extract_text]` prefix shows extraction steps
   - `[ANALYST-CHAT]` prefix shows endpoint flow
   - Logs show: JSON structure, keys, extraction success/failure

### Frontend Parsing (Already Exists)
The frontend in `analystApi.ts` (lines 108-152) already has fallback JSON parsing, but the backend should handle it first.

## How to Test

### 1. Check Backend Logs
After sending a message, look for these log lines:

```
[ANALYST-CHAT] Raw response: {"result": "...", "session_id": "...", "message": "..."}
[ANALYST-CHAT] Calling extract_text_from_analyst_response...
[extract_text] Called with response length: XXX
[extract_text] Response starts with '{', attempting JSON parse
[extract_text] JSON parse successful, type: <class 'dict'>
[extract_text] Parsed dict keys: ['result', 'session_id', 'message']
[extract_text] has_message: True, has_result_and_session: True
[extract_text] Extracted message_text type: <class 'str'>, length: XXX
[extract_text] ✅ Returning message_text (string)
[ANALYST-CHAT] extract_text_from_analyst_response returned: message=Pikachu — noted..., session_id=fdd62309-...
[ANALYST-CHAT] ✅ Direct extraction successful: XXX chars
[ANALYST-CHAT] Extracted message preview: Pikachu — noted...
```

### 2. Check Frontend Response
The frontend should receive:
```json
{
  "result": "Pikachu — noted. I like it.\n\nAlright, let's dig in...",
  "response": "Pikachu — noted. I like it.\n\nAlright, let's dig in...",
  "session_id": "fdd62309-7731-4f05-b0fb-791f92f3c499",
  "brd_id": null
}
```

**NOT** this:
```json
{
  "result": "{\"result\": \"...\", \"session_id\": \"...\", \"message\": \"...\"}",
  ...
}
```

### 3. Visual Test
In the chat UI, you should see:
```
Pikachu — noted. I like it.

Alright, let's dig in. What problem are you trying to solve, or what triggered this project in the first place?
```

**NOT**:
```json
{"result": "Pikachu — noted...", "session_id": "...", "message": "..."}
```

## Troubleshooting

### If extraction still fails:

1. **Check the raw response format** in backend logs:
   - Look for `[ANALYST-CHAT] Raw response:`
   - Copy the exact JSON structure

2. **Verify the extraction logic matches**:
   - The helper checks for `'message'` OR (`'result'` AND `'session_id'`)
   - For the given format, it should match: has both `message` and `result`

3. **Check if extraction returns None**:
   - Look for `[ANALYST-CHAT] ⚠️ Direct extraction returned None`
   - If yes, check the debug logs from `[extract_text]` to see why

4. **Verify the response is actually JSON**:
   - If it's already plain text, the extraction returns it as-is
   - Look for `[extract_text] Response doesn't start with '{'`

## Expected Flow

```
User sends message
  ↓
Backend calls analyst agent
  ↓
Analyst agent returns: {"result": "...", "session_id": "...", "message": "..."}
  ↓
extract_text_from_analyst_response() extracts "message" field
  ↓
Backend returns: {"result": "plain text", "response": "plain text", ...}
  ↓
Frontend displays: "plain text"
```

## Files Modified
- `app.py`: Added `extract_text_from_analyst_response()` helper and debug logging
- No frontend changes needed (it already has fallback parsing)
