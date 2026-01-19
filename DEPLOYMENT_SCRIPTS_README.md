# Deployment Scripts Guide

This document explains the different deployment scripts and which agent each one deploys.

## ⚠️ Important: Script Separation

**Each script is dedicated to ONE agent only. Do not mix them up!**

---

## 📋 Script Overview

### 1. `DEPLOY_AGENT.ps1` - **MY_AGENT ONLY**

**Purpose:** Deploys `my_agent` (BRD Assistant Agent)

**What it does:**
- Deploys from `.bedrock_agentcore/my_agent/` directory
- Uses ECR repository: `deluxe-sdlc`
- Tags image as: `agentcore` (removes `latest` tag)
- Uses `agentcore launch` CLI command
- Retags image using `retag_image.py`

**Usage:**
```powershell
.\DEPLOY_AGENT.ps1
```

**Agent Details:**
- Agent Name: `my_agent`
- Agent ARN: `arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK`
- ECR Repo: `deluxe-sdlc`
- Image Tag: `agentcore`
- Directory: `.bedrock_agentcore/my_agent/`

---

### 2. `BUILD_AND_PUSH_ANALYST_AGENT.ps1` - **ANALYST_AGENT ONLY**

**Purpose:** Builds and pushes `analyst_agent` Docker image to ECR (for manual AWS Console deployment)

**What it does:**
- Builds Docker image from `.bedrock_agentcore/analyst_agent/` directory
- Uses ECR repository: `analyst-agent` (separate from my_agent)
- Tags image as: `analyst-agent` (NOT `latest`)
- Pushes to ECR for manual agent creation in AWS Console

**Usage:**
```powershell
.\BUILD_AND_PUSH_ANALYST_AGENT.ps1
```

**Agent Details:**
- Agent Name: `analyst_agent` (to be created in AWS Console)
- ECR Repo: `analyst-agent`
- Image Tag: `analyst-agent`
- Directory: `.bedrock_agentcore/analyst_agent/`

**After running:** Use the Image URI in AWS Console to create the agent manually.

---

### 3. `DEPLOY_ANALYST_AGENT.ps1` - **ANALYST_AGENT ONLY (Alternative)**

**Purpose:** Deploys `analyst_agent` using `agentcore launch` CLI (temporarily swaps files)

**What it does:**
- Temporarily swaps `analyst_agent.py` as `my_agent.py`
- Runs `agentcore launch` (which looks for `.bedrock_agentcore/my_agent/`)
- Restores original `my_agent.py` after deployment
- ⚠️ **Not recommended** - use `BUILD_AND_PUSH_ANALYST_AGENT.ps1` instead

**Usage:**
```powershell
.\DEPLOY_ANALYST_AGENT.ps1
```

**Note:** This script is less reliable. Prefer `BUILD_AND_PUSH_ANALYST_AGENT.ps1` for analyst agent.

---

## 🔑 Key Differences

| Feature | MY_AGENT | ANALYST_AGENT |
|---------|----------|---------------|
| **Script** | `DEPLOY_AGENT.ps1` | `BUILD_AND_PUSH_ANALYST_AGENT.ps1` |
| **ECR Repository** | `deluxe-sdlc` | `deluxe-sdlc` (same repo) |
| **Image Tag** | `agentcore` | `analyst-agent` |
| **Directory** | `.bedrock_agentcore/my_agent/` | `.bedrock_agentcore/analyst_agent/` |
| **Deployment Method** | `agentcore launch` | Manual ECR push + AWS Console |
| **Agent ARN** | `my_agent-0BLwDgF9uK` | (Created in Console) |

---

## ✅ Verification Checklist

Before deploying, verify:

### For MY_AGENT:
- [ ] You're using `DEPLOY_AGENT.ps1`
- [ ] Directory exists: `.bedrock_agentcore/my_agent/`
- [ ] ECR repo: `deluxe-sdlc`
- [ ] Image will be tagged as: `agentcore`

### For ANALYST_AGENT:
- [ ] You're using `BUILD_AND_PUSH_ANALYST_AGENT.ps1`
- [ ] Directory exists: `.bedrock_agentcore/analyst_agent/`
- [ ] ECR repo: `analyst-agent`
- [ ] Image will be tagged as: `analyst-agent`

---

## 🚫 Common Mistakes to Avoid

1. ❌ **Don't run `DEPLOY_AGENT.ps1` for analyst_agent**
   - It will deploy my_agent instead!

2. ❌ **Don't run `BUILD_AND_PUSH_ANALYST_AGENT.ps1` for my_agent**
   - It will build analyst_agent instead!

3. ✅ **Same ECR repository, different tags**
   - Both agents use `deluxe-sdlc` repository
   - my_agent uses tag `agentcore`
   - analyst_agent uses tag `analyst-agent`

4. ❌ **Don't use `latest` tag**
   - my_agent uses `agentcore` tag
   - analyst_agent uses `analyst-agent` tag

---

## 📝 Quick Reference

```powershell
# Deploy MY_AGENT (BRD Assistant)
.\DEPLOY_AGENT.ps1

# Build and push ANALYST_AGENT (for AWS Console)
.\BUILD_AND_PUSH_ANALYST_AGENT.ps1

# Get Image URI for analyst_agent
.\GET_IMAGE_URI.ps1
```

---

## 🔧 Troubleshooting

### Error: "Wrong agent directory"
- **Solution:** Check which script you're running and verify the correct directory exists

### Error: "ECR repository not found"
- **Solution:** Both agents use the same repository `deluxe-sdlc`
  ```powershell
  # Create repository (if it doesn't exist)
  aws ecr create-repository --repository-name deluxe-sdlc --region us-east-1
  ```
  Note: This repository should already exist from `my_agent` deployment.

### Error: "Image tag conflict"
- **Solution:** Each agent uses different tags:
  - my_agent: `agentcore`
  - analyst_agent: `analyst-agent`
  - No conflicts possible!

---

## 📞 Need Help?

If you're unsure which script to use:
1. Check which agent you want to deploy
2. Refer to the table above
3. Use the correct script for that agent
4. Verify the ECR repository and tag match

