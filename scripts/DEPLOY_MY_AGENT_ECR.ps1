# Deploy my_agent to AgentCore Runtime using ECR Container method
# This script builds the ARM64 container image via CodeBuild and deploys to AgentCore
#
# Prerequisites:
#   - AWS CLI configured with valid credentials
#   - AgentCore CLI installed (pip install bedrock-agentcore-starter-toolkit)
#   - Virtual environment with required packages
#
# Usage: .\scripts\DEPLOY_MY_AGENT_ECR.ps1

# Set console encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$ErrorActionPreference = "Continue"

# Configuration
$REGION = "us-east-1"
$ECR_REPOSITORY = "deluxe-sdlc"
$IMAGE_TAG = "agentcore"  # Changed from my-agent to agentcore as per user request
$AGENT_NAME = "my_agent"

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "   Deploy my_agent to AgentCore Runtime (ECR Method)    " -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Agent Name:     $AGENT_NAME" -ForegroundColor White
Write-Host "ECR Repository: $ECR_REPOSITORY" -ForegroundColor White
Write-Host "Image Tag:      $IMAGE_TAG" -ForegroundColor White
Write-Host "Region:         $REGION" -ForegroundColor White
Write-Host ""

# Get script directory and navigate to project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir
Write-Host "[INFO] Working directory: $RootDir" -ForegroundColor Gray
Write-Host ""

# ============================================================================
# STEP 1: Verify Prerequisites
# ============================================================================
Write-Host "[1/6] Verifying prerequisites..." -ForegroundColor Yellow

# Check virtual environment
if (Test-Path ".venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
    Write-Host "  [OK] Virtual environment activated" -ForegroundColor Green
}
else {
    Write-Host "  [ERROR] Virtual environment not found!" -ForegroundColor Red
    Write-Host "  Create it with: python -m venv .venv" -ForegroundColor Gray
    exit 1
}

# Check AgentCore CLI
$agentcoreCmd = Get-Command agentcore -ErrorAction SilentlyContinue
if (-not $agentcoreCmd) {
    Write-Host "  [WARN] AgentCore CLI not found, installing..." -ForegroundColor Yellow
    pip install bedrock-agentcore-starter-toolkit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Failed to install toolkit!" -ForegroundColor Red
        exit 1
    }
}
Write-Host "  [OK] AgentCore CLI available" -ForegroundColor Green

# Check AWS credentials
Write-Host ""
Write-Host "[2/6] Verifying AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity --region $REGION 2>&1 | ConvertFrom-Json
    $ACCOUNT_ID = $identity.Account
    Write-Host "  [OK] AWS Account: $ACCOUNT_ID" -ForegroundColor Green
    Write-Host "  [OK] User ARN: $($identity.Arn)" -ForegroundColor Green
}
catch {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    Write-Host "  Run: aws configure" -ForegroundColor Gray
    exit 1
}

# ============================================================================
# STEP 2: Verify Agent Files
# ============================================================================
Write-Host ""
Write-Host "[3/6] Verifying agent files..." -ForegroundColor Yellow

if (-not (Test-Path "my_agent.py")) {
    Write-Host "  [ERROR] my_agent.py not found!" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] my_agent.py exists" -ForegroundColor Green

if (-not (Test-Path "requirements.txt")) {
    Write-Host "  [WARN] requirements.txt not found, creating..." -ForegroundColor Yellow
    @"
strands-agents>=0.1.0
boto3>=1.28.0
python-docx>=0.8.11
pydantic>=2.0.0
"@ | Set-Content "requirements.txt"
    Write-Host "  [OK] Created requirements.txt" -ForegroundColor Green
}
Write-Host "  [OK] requirements.txt exists" -ForegroundColor Green

if (-not (Test-Path ".bedrock_agentcore.yaml")) {
    Write-Host "  [ERROR] .bedrock_agentcore.yaml not found!" -ForegroundColor Red
    Write-Host "  Run: agentcore create --name $AGENT_NAME" -ForegroundColor Gray
    exit 1
}
Write-Host "  [OK] .bedrock_agentcore.yaml exists" -ForegroundColor Green

# ============================================================================
# STEP 3: Build and Deploy using agentcore launch
# ============================================================================
Write-Host ""
Write-Host "[4/6] Building and deploying agent via agentcore launch..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  This command will:" -ForegroundColor Cyan
Write-Host "    1. Package your agent code" -ForegroundColor Gray
Write-Host "    2. Upload to S3 source bucket" -ForegroundColor Gray
Write-Host "    3. Trigger CodeBuild to build ARM64 container" -ForegroundColor Gray
Write-Host "    4. Push container to ECR ($ECR_REPOSITORY)" -ForegroundColor Gray
Write-Host "    5. Update AgentCore Runtime with new image" -ForegroundColor Gray
Write-Host ""
Write-Host "  This process takes 3-5 minutes. Please wait..." -ForegroundColor Gray
Write-Host ""

# Set environment for proper encoding
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONLEGACYWINDOWSSTDIO = "1"

# Run agentcore launch for my_agent (default agent)
$deploymentSuccess = $false
try {
    Write-Host "  Executing: agentcore launch --agent $AGENT_NAME" -ForegroundColor Gray
    $output = agentcore launch --agent $AGENT_NAME 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    
    # Display filtered output
    $outputLines = $output -split "`n"
    foreach ($line in $outputLines) {
        if ($line -match "error|ERROR|fail|FAIL|FAILED" -and $line.Trim()) {
            Write-Host "  $line" -ForegroundColor Red
        }
        elseif ($line -match "✅|SUCCESS|deployed successfully" -and $line.Trim()) {
            Write-Host "  $line" -ForegroundColor Green
        }
        elseif ($line -match "Building|Uploading|Pushing|Creating|Updating|🚀|🔄" -and $line.Trim()) {
            Write-Host "  $line" -ForegroundColor Cyan
        }
    }
    
    # FIXED: Better failure detection - check for explicit failure indicators
    $hasFailed = $output -match "❌|FAILED|failed|Build failed"
    $hasSuccess = ($exitCode -eq 0) -and ($output -match "deployed successfully|✅ Agent|Image pushed")
    
    if ($hasFailed) {
        Write-Host ""
        Write-Host "  [ERROR] CodeBuild FAILED! The image was NOT pushed to ECR." -ForegroundColor Red
        Write-Host ""
        Write-Host "  To debug, check CodeBuild logs:" -ForegroundColor Yellow
        Write-Host "    1. Go to AWS Console > CodeBuild > Projects > bedrock-agentcore-my_agent-builder" -ForegroundColor Gray
        Write-Host "    2. Click 'Build history' > Latest build > 'View logs'" -ForegroundColor Gray
        Write-Host ""
        Write-Host "  Common fixes:" -ForegroundColor Yellow
        Write-Host "    - Check requirements.txt for missing/incompatible packages" -ForegroundColor Gray
        Write-Host "    - Ensure my_agent.py has no syntax errors" -ForegroundColor Gray
        Write-Host "    - Verify IAM permissions for CodeBuild role" -ForegroundColor Gray
        exit 1
    }
    elseif ($hasSuccess) {
        Write-Host ""
        Write-Host "  [OK] Agent deployed successfully!" -ForegroundColor Green
        $deploymentSuccess = $true
    }
    else {
        Write-Host ""
        Write-Host "  [WARN] Deployment status unclear. Exit code: $exitCode" -ForegroundColor Yellow
        Write-Host "  [INFO] Check AWS Console for status" -ForegroundColor Gray
        # Continue to check ECR anyway
    }
}
catch {
    Write-Host "  [ERROR] Deployment failed: $_" -ForegroundColor Red
    exit 1
}

# ============================================================================
# STEP 4: Verify ECR Image was pushed
# ============================================================================
Write-Host ""
Write-Host "[5/6] Verifying ECR image was pushed..." -ForegroundColor Yellow

# Check when 'latest' tag was last pushed
$now = Get-Date
$imageFound = $false
try {
    $imageInfo = aws ecr describe-images --repository-name $ECR_REPOSITORY --region $REGION --image-ids imageTag=latest --query 'imageDetails[0].imagePushedAt' --output text 2>&1
    if ($LASTEXITCODE -eq 0 -and $imageInfo -ne "None") {
        $pushedTime = [DateTime]::Parse($imageInfo)
        $timeDiff = ($now - $pushedTime).TotalMinutes
        
        if ($timeDiff -le 10) {
            Write-Host "  [OK] Latest image pushed $([math]::Round($timeDiff, 1)) minutes ago - this is from current deployment!" -ForegroundColor Green
            $imageFound = $true
        }
        else {
            Write-Host "  [WARN] Latest image is $([math]::Round($timeDiff, 1)) minutes old - NOT from this deployment!" -ForegroundColor Yellow
            Write-Host "  [INFO] The CodeBuild may have failed to push the image." -ForegroundColor Gray
        }
    }
    else {
        Write-Host "  [WARN] Could not verify 'latest' tag timestamp" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "  [WARN] Could not verify image: $_" -ForegroundColor Yellow
}

# ============================================================================
# STEP 5: Retag Image to 'agentcore'
# ============================================================================
if ($imageFound) {
    Write-Host ""
    Write-Host "[6/6] Retagging image from 'latest' to '$IMAGE_TAG'..." -ForegroundColor Yellow

    try {
        # Get the image manifest
        $manifest = aws ecr batch-get-image --repository-name $ECR_REPOSITORY --region $REGION --image-ids imageTag=latest --query 'images[0].imageManifest' --output text 2>&1
        
        if ($LASTEXITCODE -eq 0 -and $manifest) {
            # Put the image with new tag
            $putResult = aws ecr put-image --repository-name $ECR_REPOSITORY --region $REGION --image-tag $IMAGE_TAG --image-manifest $manifest 2>&1
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  [OK] Image tagged as '$IMAGE_TAG'" -ForegroundColor Green
            }
            else {
                # Check if it's because tag already exists
                if ($putResult -match "ImageAlreadyExistsException") {
                    Write-Host "  [OK] Image already has '$IMAGE_TAG' tag" -ForegroundColor Green
                }
                else {
                    Write-Host "  [WARN] Retagging result: $putResult" -ForegroundColor Yellow
                }
            }
        }
    }
    catch {
        Write-Host "  [WARN] Could not retag image: $_" -ForegroundColor Yellow
    }
}
else {
    Write-Host ""
    Write-Host "[6/6] Skipping retag - no new image was pushed" -ForegroundColor Yellow
}

# ============================================================================
# STEP 7: Update AgentCore env from .env (guardrails for Bedrock calls)
# ============================================================================
Write-Host ""
Write-Host "[7/7] Updating AgentCore runtime environment from .env..." -ForegroundColor Yellow
if (Test-Path ".env") {
    python scripts/update_agentcore_env.py 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Agent env vars updated (BEDROCK_GUARDRAIL_ARN, etc.)" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] Could not update agent env - continuing" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [WARN] No .env file - agents will use existing env vars" -ForegroundColor Yellow
}

# ============================================================================
# Display Results
# ============================================================================
Write-Host ""

# Get ECR image URI
$ECR_URI = "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPOSITORY"

# Check what tags are available
Write-Host "  Available image tags in ECR:" -ForegroundColor Gray
$tags = aws ecr describe-images --repository-name $ECR_REPOSITORY --region $REGION --query 'imageDetails[*].imageTags' --output text 2>&1
if ($LASTEXITCODE -eq 0) {
    $tagList = $tags -split "`t" | Where-Object { $_ -and $_.Trim() }
    foreach ($tag in $tagList | Select-Object -First 8) {
        Write-Host "    - $tag" -ForegroundColor White
    }
}

if ($imageFound) {
    Write-Host ""
    Write-Host "=========================================================" -ForegroundColor Cyan
    Write-Host "              Deployment Complete!                       " -ForegroundColor Green
    Write-Host "=========================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "ECR Image URIs:" -ForegroundColor Yellow
    Write-Host "  Latest: ${ECR_URI}:latest" -ForegroundColor White
    Write-Host "  Tagged: ${ECR_URI}:$IMAGE_TAG" -ForegroundColor White
    Write-Host ""
    Write-Host "Agent Runtime ARN:" -ForegroundColor Yellow
    Write-Host "  arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${AGENT_NAME}-0BLwDgF9uK" -ForegroundColor White
}
else {
    Write-Host ""
    Write-Host "=========================================================" -ForegroundColor Yellow
    Write-Host "              Deployment INCOMPLETE                       " -ForegroundColor Yellow
    Write-Host "=========================================================" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "The deployment did not push a new image to ECR." -ForegroundColor Red
    Write-Host "Please check CodeBuild logs in AWS Console for errors." -ForegroundColor Red
}

Write-Host ""
Write-Host "To update hosting in AWS Console:" -ForegroundColor Yellow
Write-Host "  1. Go to: Amazon Bedrock AgentCore > Runtime > $AGENT_NAME" -ForegroundColor Gray
Write-Host "  2. Click 'Update hosting'" -ForegroundColor Gray
Write-Host "  3. Set Image URI to: ${ECR_URI}:$IMAGE_TAG" -ForegroundColor Gray
Write-Host "  4. Click 'Save'" -ForegroundColor Gray
Write-Host ""
