# AgentCore BRD Generator - Final Setup

## ✅ System Architecture

```
User Browser (localhost:8000)
    ↓
FastAPI App (app.py - port 8000)
    ↓
AgentCore Agent (my_agent-0BLwDgF9uK on AWS)
    ↓
Lambda Tools (brd_generator_lambda, brd_chat_lambda, brd_retriever_lambda)
    ↓
S3 Storage (test-development-bucket-siriusai)
```

## 🚀 How to Start

```powershell
.\START_APP.ps1
```

Then open: **http://localhost:8000/**

## 📋 Features

### 1. BRD Generation
- Upload transcript (.docx or .txt)
- Upload template (.docx)
- Agent generates BRD using Lambda
- Full BRD displays with beautiful formatting
- Download button to save as .txt

### 2. Live Chat
- Chat with agent about the BRD
- Agent knows the BRD ID automatically
- Can fetch BRD using retriever Lambda
- Can edit BRD using chat Lambda
- Session maintained across conversation

### 3. BRD Editing
Chat commands that work:
- "Can you edit this BRD?"
- "Add more details to section 5"
- "Update the timeline"
- "Show me the current BRD"
- "Make the ROI section more specific"

## 🔧 Configuration

### Agent
- Name: my_agent
- ARN: arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK
- Model: global.anthropic.claude-sonnet-4-5-20250929-v1:0 (inference profile)
- Tools: generate_brd, fetch_brd, chat_with_brd

### Lambda Functions
- brd_generator_lambda - Generates BRDs
- brd_chat_lambda - Edits BRDs via chat
- brd_retriever_lambda - Fetches BRDs from S3
- Model: anthropic.claude-sonnet-4-5-20250929-v1:0 (direct)
- API: converse() (supports inference profiles)

### IAM Permissions
- Runtime Role: AmazonBedrockAgentCoreSDKRuntime-us-east-1-e72c1a7c7a
- Permissions: Lambda invoke, Bedrock model access, S3 access

### Docker
- Repository: deluxe-sdlc
- Tags: latest, agentcore
- Platform: linux/arm64

## 📁 Essential Files

- `app.py` - FastAPI backend
- `my_agent.py` - AgentCore agent code
- `templates/index.html` - UI with formatting
- `requirements.txt` - Python dependencies
- `requirements_frontend.txt` - FastAPI dependencies
- `.bedrock_agentcore.yaml` - Agent configuration
- `retag_image.py` - Docker image tagging
- `START_APP.ps1` - Start the app
- `DEPLOY_AGENT.ps1` - Deploy agent updates

## 🔄 Workflow

1. **User uploads files** → FastAPI receives them
2. **FastAPI calls agent** → Passes template + transcript
3. **Agent detects files** → Calls generate_brd() function
4. **Function invokes Lambda** → brd_generator_lambda
5. **Lambda generates BRD** → Using Claude Sonnet 4.5
6. **Lambda saves to S3** → Returns BRD content + ID
7. **Agent returns JSON** → Clean format for parsing
8. **FastAPI extracts BRD** → Sends to UI
9. **UI displays BRD** → Formatted HTML with download
10. **User can chat** → Edit BRD via brd_chat_lambda

## 🎯 How Chat Works

1. **User sends chat message** → Includes BRD ID in context
2. **FastAPI forwards to agent** → With BRD ID and session ID
3. **Agent processes request** → Uses appropriate tool:
   - `fetch_brd` - To retrieve current BRD
   - `chat_with_brd` - To make edits
4. **Lambda executes** → Makes changes
5. **Response returns** → UI shows update

## ✨ Key Features

- ✅ Single port (8000) for everything
- ✅ Beautiful BRD formatting (headings, lists, tables)
- ✅ Download BRD as .txt file
- ✅ Live chat with context awareness
- ✅ Automatic BRD ID tracking
- ✅ Session persistence
- ✅ Full error logging
- ✅ Modern, responsive UI

## 🐛 Troubleshooting

### BRD not generating?
Check terminal logs for:
```
[APP] Starting BRD generation
[APP] Calling agent...
[BRD-AGENT] Template and transcript detected
[BRD-AGENT] Invoking Lambda: brd_generator_lambda
```

### Chat not working?
- Make sure BRD was generated first
- Check that BRD ID is showing in badge
- Session ID must be 33+ characters (now fixed)

### Lambda errors?
Check CloudWatch logs:
- `/aws/lambda/brd_generator_lambda`
- `/aws/lambda/brd_chat_lambda`

## 📞 Quick Commands

```powershell
# Start app
.\START_APP.ps1

# Deploy agent changes
.\DEPLOY_AGENT.ps1

# Check agent status
agentcore status

# View agent logs
agentcore logs
```

## 🎉 Success!

Your AgentCore BRD Generator is fully functional with:
- Beautiful UI
- Full BRD formatting
- Download capability
- Live chat for editing
- Complete integration with AWS services

Everything works on **PORT 8000** only!













