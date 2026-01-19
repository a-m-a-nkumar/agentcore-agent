# AgentCore BRD Generator

## Quick Start

### 1. Start the App
```powershell
.\START_APP.ps1
```

Then open: http://localhost:8000/

### 2. Generate a BRD
1. Upload your transcript file (.docx or .txt)
2. Upload your BRD template (.docx)
3. Click "Generate BRD"
4. Wait 30-60 seconds
5. View the complete BRD in the UI

### 3. Chat to Edit
Use the chatbox to refine your BRD:
- "Add more details to section 5"
- "Update the ROI calculation"
- "Make the timeline more specific"

## Deploy Agent

To redeploy the agent with code changes:
```powershell
.\DEPLOY_AGENT.ps1
```

## Architecture

- **UI**: Templates served by FastAPI
- **Backend**: FastAPI app.py on port 8000
- **Agent**: AgentCore (my_agent-0BLwDgF9uK)
- **Tools**: Lambda functions (brd_generator_lambda, brd_chat_lambda)

## Configuration

- Agent ARN: `arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK`
- Model: `global.anthropic.claude-sonnet-4-5-20250929-v1:0` (inference profile)
- Lambda Model: `anthropic.claude-sonnet-4-5-20250929-v1:0` (direct)














