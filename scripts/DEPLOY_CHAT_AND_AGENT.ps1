# Deploy BRD Chat Lambda and BRD Agent
# Run from agentcore-starter directory: .\scripts\DEPLOY_CHAT_AND_AGENT.ps1

$ErrorActionPreference = "Stop"
$REGION = "us-east-1"
$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RootDir

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying BRD Chat Lambda + Agent" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Rebuild Lambda package (minimal - Lambda runtime has boto3) ---
Write-Host "[1/4] Rebuilding Lambda chat package..." -ForegroundColor Yellow
if (Test-Path "lambda_chat_package") {
    Remove-Item -Recurse -Force "lambda_chat_package"
}
if (Test-Path "lambda_chat_package.zip") {
    Remove-Item -Force "lambda_chat_package.zip"
}

New-Item -ItemType Directory -Path "lambda_chat_package" -Force | Out-Null
Copy-Item "lambda_brd_chat.py" -Destination "lambda_chat_package\lambda_brd_chat.py" -Force
# Lambda Python runtime includes boto3/botocore - no need to bundle
Write-Host "  [OK] Package created (lambda_brd_chat.py only)" -ForegroundColor Green
Write-Host ""

# --- Step 2: Create zip and deploy Lambda ---
Write-Host "[2/4] Deploying brd_chat_lambda..." -ForegroundColor Yellow
python create_lambda_zip.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Failed to create zip!" -ForegroundColor Red
    exit 1
}

aws lambda update-function-code `
    --function-name brd_chat_lambda `
    --zip-file fileb://lambda_chat_package.zip `
    --region $REGION `
    --output json 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] brd_chat_lambda deployed" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Lambda deployment failed" -ForegroundColor Red
    exit 1
}
Write-Host ""

# --- Step 3: Deploy BRD Agent ---
Write-Host "[3/4] Deploying BRD Agent (my_agent)..." -ForegroundColor Yellow
if (Test-Path ".venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
}
agentcore launch
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] BRD Agent deployed" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Agent deployment may have failed" -ForegroundColor Yellow
}
Write-Host ""

# --- Step 4: Retag image ---
Write-Host "[4/4] Retagging image..." -ForegroundColor Yellow
python retag_image.py 2>$null
Write-Host "  [OK] Done" -ForegroundColor Green
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  - brd_chat_lambda: Updated with LLM-based intent parsing" -ForegroundColor White
Write-Host "  - BRD Agent: Deployed to AgentCore" -ForegroundColor White
Write-Host ""
