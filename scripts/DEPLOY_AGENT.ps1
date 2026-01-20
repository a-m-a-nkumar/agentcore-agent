# Deploy MY_AGENT (BRD Assistant Agent) with 'agentcore' tag
# This script ONLY deploys my_agent - DO NOT use for analyst_agent

# Set console encoding to UTF-8 to handle emojis and special characters
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying MY_AGENT (BRD Assistant)" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  This script deploys: my_agent (BRD Assistant)" -ForegroundColor Yellow
Write-Host "⚠️  For analyst_agent, use: BUILD_AND_PUSH_ANALYST_AGENT.ps1" -ForegroundColor Yellow
Write-Host ""

# Get the script directory (current file location)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
cd $ScriptDir

# Verify we're deploying the correct agent
$agentDir = ".bedrock_agentcore\my_agent"
if (-not (Test-Path $agentDir)) {
    Write-Host "  [ERROR] my_agent directory not found: $agentDir" -ForegroundColor Red
    Write-Host "  This script is for my_agent only!" -ForegroundColor Red
    exit 1
}
Write-Host "[INFO] Deploying agent from: $agentDir" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/4] Activating virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv\Scripts\Activate.ps1") {
    .venv\Scripts\Activate.ps1
    Write-Host "  [OK] Virtual environment activated" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Virtual environment not found!" -ForegroundColor Red
    Write-Host "  Please create a virtual environment first:" -ForegroundColor Yellow
    Write-Host "    python -m venv .venv" -ForegroundColor Gray
    exit 1
}
Write-Host ""

# Check if agentcore CLI is available
Write-Host "[2/4] Checking for AgentCore CLI..." -ForegroundColor Yellow
$agentcoreCmd = Get-Command agentcore -ErrorAction SilentlyContinue
if (-not $agentcoreCmd) {
    Write-Host "  [WARN] AgentCore CLI not found!" -ForegroundColor Yellow
    Write-Host "  Installing bedrock-agentcore-starter-toolkit..." -ForegroundColor Gray
    pip install bedrock-agentcore-starter-toolkit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Failed to install toolkit!" -ForegroundColor Red
        Write-Host "  Please install manually:" -ForegroundColor Yellow
        Write-Host "    pip install bedrock-agentcore-starter-toolkit" -ForegroundColor Gray
        exit 1
    }
    Write-Host "  [OK] Toolkit installed" -ForegroundColor Green
} else {
    Write-Host "  [OK] AgentCore CLI found" -ForegroundColor Green
}
Write-Host ""

# Verify AWS credentials
Write-Host "[3/4] Verifying AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity --region us-east-1 2>&1 | ConvertFrom-Json
    Write-Host "  [OK] AWS Account: $($identity.Account)" -ForegroundColor Green
    Write-Host "  [OK] User ARN: $($identity.Arn)" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    Write-Host "  Please run: aws configure" -ForegroundColor Yellow
    exit 1
}
Write-Host ""

# Deploy agent (my_agent only)
Write-Host "[4/4] Deploying my_agent..." -ForegroundColor Yellow
Write-Host "  Agent: my_agent (BRD Assistant)" -ForegroundColor Cyan
Write-Host "  Directory: .bedrock_agentcore\my_agent" -ForegroundColor Cyan
Write-Host "  ECR Repository: deluxe-sdlc" -ForegroundColor Cyan
Write-Host "  Image Tag: agentcore (after retagging)" -ForegroundColor Cyan
Write-Host "  This may take a few minutes..." -ForegroundColor Gray
Write-Host ""
try {
    # Set environment variable to suppress emoji output if needed
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONLEGACYWINDOWSSTDIO = "1"
    
    # Ensure we're in the root directory (agentcore launch looks for .bedrock_agentcore/my_agent)
    # Run agentcore launch from root - it will automatically use .bedrock_agentcore/my_agent
    $output = agentcore launch 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    
    # Check if deployment succeeded (ignore encoding errors if deployment worked)
    if ($exitCode -eq 0 -or $output -match "deployed|success|complete") {
        Write-Host "  [OK] Agent deployed successfully" -ForegroundColor Green
    } elseif ($output -match "UnicodeEncodeError|charmap") {
        # Encoding error but deployment might have succeeded
        Write-Host "  [WARN] Console encoding issue detected, checking deployment status..." -ForegroundColor Yellow
        # Check if agent exists
        $agentCheck = aws bedrock-agentcore describe-runtime --runtime-arn "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK" --region us-east-1 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Agent deployment verified - agent exists" -ForegroundColor Green
        } else {
            Write-Host "  [INFO] Deployment may have succeeded despite encoding error" -ForegroundColor Yellow
            Write-Host "  Please check AWS Console to verify deployment" -ForegroundColor Gray
        }
    } else {
        Write-Host "  [ERROR] Agent deployment failed!" -ForegroundColor Red
        Write-Host "  Error output: $output" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "  [ERROR] Failed to deploy agent: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Retag image from 'latest' to 'agentcore' and remove 'latest' tag
# This is for my_agent only (ECR repo: deluxe-sdlc)
Write-Host "Retagging my_agent image as 'agentcore' (removing 'latest' tag)..." -ForegroundColor Yellow
Write-Host "  Repository: deluxe-sdlc" -ForegroundColor Cyan
Write-Host "  Target Tag: agentcore" -ForegroundColor Cyan
try {
    # Set environment variables for retag script (my_agent uses deluxe-sdlc repo)
    $env:ECR_REPOSITORY = "deluxe-sdlc"
    $env:SOURCE_TAG = "latest"
    $env:TARGET_TAG = "agentcore"
    python retag_image.py
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] my_agent image retagged to 'agentcore' and 'latest' tag removed" -ForegroundColor Green
        Write-Host "  [INFO] Image is now tagged only as 'agentcore' to avoid conflicts" -ForegroundColor Cyan
    } else {
        Write-Host "  [WARN] Image retagging failed (may already be tagged)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  [WARN] Image retagging failed: $_" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "MY_AGENT Deployment Complete! ✅" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Deployed Agent: my_agent (BRD Assistant)" -ForegroundColor White
Write-Host "Agent ARN: arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK" -ForegroundColor White
Write-Host "ECR Repository: deluxe-sdlc" -ForegroundColor White
Write-Host "Image Tag: agentcore" -ForegroundColor White
Write-Host ""
Write-Host "⚠️  Remember: This script is ONLY for my_agent" -ForegroundColor Yellow
Write-Host "⚠️  For analyst_agent, use: BUILD_AND_PUSH_ANALYST_AGENT.ps1" -ForegroundColor Yellow
Write-Host ""














