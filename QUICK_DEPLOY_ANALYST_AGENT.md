# Quick Steps to Deploy Analyst Agent

## Prerequisites
- ✅ AWS credentials configured (`aws configure`)
- ✅ Docker installed and running
- ✅ Analyst agent code ready (`.bedrock_agentcore/analyst_agent/`)

---

## Step-by-Step Deployment

### Step 1: Build and Push Docker Image to ECR

**⚠️ IMPORTANT: AgentCore requires ARM64 architecture!**

You have two options:

#### Option A: Use agentcore launch (RECOMMENDED - builds ARM64 automatically)

```powershell
cd "c:\Users\Aman1Kumar\OneDrive - Sirius AI\Documents\agentcore-starter 3\agentcore-starter"
.\BUILD_ANALYST_AGENT_ARM64.ps1
```

This uses `agentcore launch` which builds ARM64 in the cloud via CodeBuild (no local Docker needed).

#### Option B: Build locally with docker buildx (requires Docker Buildx)

```powershell
cd "c:\Users\Aman1Kumar\OneDrive - Sirius AI\Documents\agentcore-starter 3\agentcore-starter"
.\BUILD_AND_PUSH_ANALYST_AGENT.ps1
```

**Note:** This requires `docker buildx` to build ARM64 on Windows. If buildx is not available, use Option A.

**What this does:**
- Uses existing ECR repository `deluxe-sdlc` (same as my_agent)
- Builds Docker image from `.bedrock_agentcore/analyst_agent/`
- Tags image as `analyst-agent` (not `latest`)
- Pushes to ECR

**Output:** You'll get an Image URI like:
```
448049797912.dkr.ecr.us-east-1.amazonaws.com/deluxe-sdlc:analyst-agent
```

**Note:** Uses the same ECR repository as `my_agent` but with a different tag (`analyst-agent` vs `agentcore`).

**Copy this Image URI** - you'll need it in Step 2.

---

### Step 2: Create Agent in AWS Console

1. **Navigate to AgentCore:**
   - Go to: https://us-east-1.console.aws.amazon.com/bedrock-agentcore/
   - Click **"Runtime"** in left menu
   - Click **"Host Agent"**
   - Click **"Create agent"**

2. **Fill in Agent Details:**
   - **Name:** `analyst_agent`
   - **Description (optional):** "Business Analyst Agent for requirements gathering and BRD generation"

3. **Agent Source:**
   - **Source type:** Select **"ECR Container"**
   - **Image URI:** Paste the Image URI from Step 1
     - Example: `448049797912.dkr.ecr.us-east-1.amazonaws.com/deluxe-sdlc:analyst-agent`
   - Or click **"Browse images"** to select from list

4. **Permissions:**
   - **Service role for Amazon Bedrock AgentCore:**
     - **Option A (Recommended):** Select the same IAM role used by `my_agent`
       - This role already has all required permissions
     - **Option B:** Create a new role with these permissions:
       - Lambda invoke (for `brd_generator_lambda`, `brd_chat_lambda`)
       - Bedrock invoke (for Claude model)
       - AgentCore Memory access
       - S3 access (for BRD storage)

5. **Create:**
   - Review all settings
   - Click **"Create agent"**
   - Wait 1-2 minutes for creation

---

### Step 3: Get the Agent ARN

After the agent is created:

1. **In AWS Console:**
   - Go to the agent details page
   - Find the **"Agent Runtime ARN"**
   - Copy it

   Format: `arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/analyst_agent-<ID>`

2. **Or get it via CLI:**
   ```powershell
   aws bedrock-agentcore list-runtimes --region us-east-1 --query "runtimeSummaries[?contains(runtimeArn, 'analyst_agent')].runtimeArn" --output text
   ```

---

### Step 4: Update app.py

Open `app.py` and update line 91:

```python
ANALYST_AGENT_ARN = os.getenv("ANALYST_AGENT_ARN", "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/analyst_agent-<YOUR-ACTUAL-ID>")
```

Replace `<YOUR-ACTUAL-ID>` with the actual ID from Step 3.

**Or set as environment variable:**
```powershell
$env:ANALYST_AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/analyst_agent-<YOUR-ACTUAL-ID>"
```

---

### Step 5: Restart Backend Server

Restart your FastAPI server to pick up the new ARN:

```powershell
# Stop the current server (Ctrl+C)
# Then restart:
python app.py
# Or if using uvicorn:
uvicorn app:app --reload
```

---

### Step 6: Test the Analyst Agent

1. **Start the frontend** (if not running):
   ```powershell
   cd "c:\Users\Aman1Kumar\Downloads\deluxe-sdlc-frontend\deluxe-sdlc-frontend"
   npm run dev
   ```

2. **Navigate to Analyst Agent page:**
   - Go to: http://localhost:5173/analyst-agent
   - Or click "Create BRD with Analyst" button from BRD Assistant page

3. **Test the conversation:**
   - Start chatting with Mary (the analyst agent)
   - Ask about your project requirements
   - Generate a BRD after gathering enough information

---

## Verification Checklist

- [ ] Docker image built and pushed to ECR
- [ ] Image URI copied
- [ ] Agent created in AWS Console
- [ ] Agent ARN copied
- [ ] `app.py` updated with correct ARN
- [ ] Backend server restarted
- [ ] Frontend accessible
- [ ] Can chat with analyst agent
- [ ] BRD generation works

---

## Troubleshooting

### Error: "Image not found"
- **Solution:** Verify the Image URI is correct
- Check ECR repository: `aws ecr describe-images --repository-name analyst-agent --region us-east-1`

### Error: "Access denied"
- **Solution:** Check IAM role has required permissions
- Use the same role as `my_agent` for consistency

### Error: "Agent not found" in backend
- **Solution:** Verify the Agent ARN in `app.py` is correct
- Check agent exists: `aws bedrock-agentcore describe-runtime --runtime-arn <ARN> --region us-east-1`

### Error: "Could not connect to endpoint"
- **Solution:** This is a network issue, retry the deployment
- Check AWS service status

---

## Quick Reference Commands

```powershell
# 1. Build and push image
.\BUILD_AND_PUSH_ANALYST_AGENT.ps1

# 2. Get Image URI (if needed)
.\GET_IMAGE_URI.ps1

# 3. List agents to find ARN
aws bedrock-agentcore list-runtimes --region us-east-1 --query "runtimeSummaries[?contains(runtimeArn, 'analyst_agent')]"

# 4. Verify agent exists
aws bedrock-agentcore describe-runtime --runtime-arn <YOUR-ARN> --region us-east-1
```

---

## Summary

1. ✅ Run `BUILD_AND_PUSH_ANALYST_AGENT.ps1` → Get Image URI
2. ✅ Create agent in AWS Console → Get Agent ARN
3. ✅ Update `app.py` with Agent ARN
4. ✅ Restart backend server
5. ✅ Test in frontend

That's it! 🎉

