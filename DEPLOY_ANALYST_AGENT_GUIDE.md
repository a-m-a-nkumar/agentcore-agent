# Deploy Analyst Agent as Separate AgentCore Runtime

This guide will help you deploy the analyst agent as a **separate** AgentCore runtime that uses the existing Lambda tools without disturbing your current `my_agent`.

## Prerequisites

1. ✅ Analyst agent code (`analyst_agent.py`) is ready
2. ✅ Existing Lambda functions (`brd_generator_lambda`, `brd_chat_lambda`) are deployed
3. ✅ AWS credentials configured
4. ✅ AgentCore CLI installed

## Option 1: Deploy via AWS Console (Recommended for First Time)

### Step 1: Build and Push Docker Image to ECR

**Easy way - Use the provided script:**
```powershell
.\BUILD_AND_PUSH_ANALYST_AGENT.ps1
```

This script will:
- Get your AWS account ID
- Create ECR repository if needed
- Build the Docker image
- Tag and push to ECR
- Display the Image URI for you to copy

**Manual way:**
```powershell
# Get your AWS account ID
$accountId = (aws sts get-caller-identity --query Account --output text)
$region = "us-east-1"
$repoName = "analyst-agent"

# Verify ECR repository exists (should already exist from my_agent deployment)
# If it doesn't exist, create it:
aws ecr create-repository --repository-name deluxe-sdlc --region $region

# Login to ECR
aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $accountId.dkr.ecr.$region.amazonaws.com

# Build image
cd ".bedrock_agentcore\analyst_agent"
docker build -t analyst-agent:analyst-agent .
cd ..\..

# Tag and push (using 'analyst-agent' tag, same repo as my_agent)
docker tag analyst-agent:analyst-agent $accountId.dkr.ecr.$region.amazonaws.com/deluxe-sdlc:analyst-agent
docker push $accountId.dkr.ecr.$region.amazonaws.com/deluxe-sdlc:analyst-agent
```

**Image URI format:**
```
<account-id>.dkr.ecr.<region>.amazonaws.com/deluxe-sdlc:analyst-agent
Example: 448049797912.dkr.ecr.us-east-1.amazonaws.com/deluxe-sdlc:analyst-agent
```

**Note:** 
- Uses the same ECR repository as `my_agent` (`deluxe-sdlc`)
- Tagged as `analyst-agent` (not `latest`) to avoid conflicts
- `my_agent` uses tag `agentcore`, `analyst_agent` uses tag `analyst-agent`

### Step 2: Create Agent in AWS Console

1. **Navigate to AgentCore:**
   - Go to AWS Console
   - Search for "Bedrock AgentCore" or navigate to: `https://us-east-1.console.aws.amazon.com/bedrock-agentcore/`
   - Click **"Runtime"** in the left menu
   - Click **"Host Agent"**
   - Click **"Create agent"**

2. **Agent Details:**
   - **Name:** `analyst_agent`
   - **Description (optional):** "Business Analyst Agent for requirements gathering and BRD generation"

3. **Agent Source:**
   - **Source type:** Select **"ECR Container"**
   - **Image URI:** Paste the ECR image URI from Step 1
     - Example: `448049797912.dkr.ecr.us-east-1.amazonaws.com/analyst-agent:latest`
   - Click **"Browse images"** if you want to select from a list

4. **Permissions:**
   - **Service role for Amazon Bedrock AgentCore:**
     - **Option A (Recommended):** Select the same IAM role used by your `my_agent`
       - This ensures consistent permissions
       - The role should already have:
         - Lambda invoke permissions (for `brd_generator_lambda`, `brd_chat_lambda`)
         - Bedrock invoke permissions (for Claude model)
         - AgentCore Memory access
         - S3 access (for BRD storage)
     - **Option B:** Create a new role with the same permissions
       - Click "Create new role"
       - Attach policies: `AWSLambda_FullAccess`, `AmazonBedrockFullAccess`, `AmazonS3FullAccess`
       - Add AgentCore Memory permissions

5. **Review and Create:**
   - Review all settings
   - Click **"Create agent"**
   - Wait for the agent to be created (may take 1-2 minutes)

### Step 3: Get the Agent ARN

After creation, copy the **Agent Runtime ARN** from the agent details page.

Format: `arn:aws:bedrock-agentcore:us-east-1:<account-id>:runtime/analyst_agent-<id>`

### Step 4: Update app.py

Update the `ANALYST_AGENT_ARN` in `app.py`:

```python
ANALYST_AGENT_ARN = os.getenv("ANALYST_AGENT_ARN", "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/analyst_agent-<YOUR-ID>")
```

Or set it as an environment variable:
```powershell
$env:ANALYST_AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/analyst_agent-<YOUR-ID>"
```

## Option 2: Deploy via AgentCore CLI

### Step 1: Prepare Files

Ensure these files are in `.bedrock_agentcore/analyst_agent/`:
- `analyst_agent.py`
- `requirements.txt`
- `Dockerfile`

### Step 2: Deploy

```powershell
cd ".bedrock_agentcore\analyst_agent"
agentcore launch
```

This will:
1. Build the Docker image
2. Push to ECR
3. Create the AgentCore runtime
4. Return the Agent ARN

### Step 3: Update app.py

Copy the Agent ARN from the deployment output and update `app.py` as shown in Option 1, Step 4.

## Verify Deployment

1. **Check Agent in Console:**
   - Go to AgentCore > Runtime
   - You should see both `my_agent` and `analyst_agent`

2. **Test the Endpoint:**
   ```powershell
   # Restart your backend
   # Then test via frontend or:
   curl -X POST http://localhost:8000/analyst-chat \
     -H "Authorization: Bearer <your-token>" \
     -F "message=Hello" \
     -F "session_id=none"
   ```

## Important Notes

✅ **Separate Agents:** `my_agent` and `analyst_agent` are completely separate runtimes
✅ **Shared Tools:** Both agents use the same Lambda functions (`brd_generator_lambda`, `brd_chat_lambda`)
✅ **Separate Memory:** Analyst agent uses its own session IDs (`analyst-session-*`)
✅ **No Interference:** Changes to one agent don't affect the other

## Troubleshooting

### Error: "Agent not found"
- Verify the Agent ARN is correct
- Check the agent exists in AWS Console
- Ensure the region matches (us-east-1)

### Error: "Access denied"
- Check IAM role has Lambda invoke permissions
- Verify Bedrock permissions
- Check AgentCore Memory permissions

### Error: "Image not found"
- Verify ECR image URI is correct
- Check image was pushed successfully
- Ensure image is in the same region as the agent

## Environment Variables for Analyst Agent

The analyst agent uses these environment variables (set in Dockerfile or AgentCore):
- `BEDROCK_MODEL_ID` - Claude model ID (default: `global.anthropic.claude-sonnet-4-5-20250929-v1:0`)
- `AWS_REGION` - AWS region (default: `us-east-1`)
- `LAMBDA_BRD_GENERATOR` - Lambda function name (default: `brd_generator_lambda`)
- `AGENTCORE_MEMORY_ID` - Memory ID for conversation storage
- `AGENTCORE_ACTOR_ID` - Actor ID for sessions (default: `analyst-session`)

