# Deploy ANALYST_AGENT to AgentCore using agentcore CLI
# This script ONLY deploys analyst_agent - DO NOT use for my_agent
# NOTE: This script temporarily swaps files - consider using BUILD_AND_PUSH_ANALYST_AGENT.ps1 instead

# Set console encoding to UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying ANALYST_AGENT to AgentCore" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  This script deploys: analyst_agent (Business Analyst)" -ForegroundColor Yellow
Write-Host "⚠️  For my_agent, use: DEPLOY_AGENT.ps1" -ForegroundColor Yellow
Write-Host "⚠️  Alternative: Use BUILD_AND_PUSH_ANALYST_AGENT.ps1 for manual ECR push" -ForegroundColor Yellow
Write-Host ""

# Get the script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
cd $ScriptDir

Write-Host "[1/5] Activating virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv\Scripts\Activate.ps1") {
    .venv\Scripts\Activate.ps1
    Write-Host "  [OK] Virtual environment activated" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Virtual environment not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Check if agentcore CLI is available
Write-Host "[2/5] Checking for AgentCore CLI..." -ForegroundColor Yellow
$agentcoreCmd = Get-Command agentcore -ErrorAction SilentlyContinue
if (-not $agentcoreCmd) {
    Write-Host "  [ERROR] AgentCore CLI not found!" -ForegroundColor Red
    exit 1
} else {
    Write-Host "  [OK] AgentCore CLI found" -ForegroundColor Green
}
Write-Host ""

# Verify AWS credentials
Write-Host "[3/5] Verifying AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity --region us-east-1 2>&1 | ConvertFrom-Json
    Write-Host "  [OK] AWS Account: $($identity.Account)" -ForegroundColor Green
    Write-Host "  [OK] User ARN: $($identity.Arn)" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Backup and swap agent files
Write-Host "[4/5] Preparing agent files for deployment..." -ForegroundColor Yellow
$backupDir = ".agent_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

# Backup my_agent.py
if (Test-Path "my_agent.py") {
    Copy-Item "my_agent.py" "$backupDir\my_agent.py" -Force
    Write-Host "  [OK] Backed up my_agent.py" -ForegroundColor Green
}

# Copy analyst_agent.py as my_agent.py temporarily
if (Test-Path "analyst_agent.py") {
    Copy-Item "analyst_agent.py" "my_agent.py" -Force
    Write-Host "  [OK] Copied analyst_agent.py as my_agent.py for deployment" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] analyst_agent.py not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Deploy agent
Write-Host "[5/5] Deploying analyst agent..." -ForegroundColor Yellow
Write-Host "  This will deploy analyst_agent as a new AgentCore runtime" -ForegroundColor Gray
Write-Host "  This may take a few minutes..." -ForegroundColor Gray
Write-Host ""

try {
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONLEGACYWINDOWSSTDIO = "1"
    
    # Run agentcore launch
    $output = agentcore launch 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    
    if ($exitCode -eq 0 -or $output -match "deployed|success|complete|runtime") {
        Write-Host "  [OK] Analyst agent deployed successfully" -ForegroundColor Green
        
        # Try to extract the agent ARN from output
        if ($output -match "arn:aws:bedrock-agentcore:([^\\s]+)") {
            $agentArn = $matches[0]
            Write-Host "  [INFO] Agent ARN: $agentArn" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "  IMPORTANT: Update ANALYST_AGENT_ARN in app.py:" -ForegroundColor Yellow
            Write-Host "    ANALYST_AGENT_ARN = `"$agentArn`"" -ForegroundColor Gray
        }
    } else {
        Write-Host "  [WARN] Deployment output unclear. Please check AWS Console." -ForegroundColor Yellow
        Write-Host "  Output: $($output.Substring(0, [Math]::Min(500, $output.Length)))" -ForegroundColor Gray
    }
} catch {
    Write-Host "  [ERROR] Failed to deploy analyst agent: $_" -ForegroundColor Red
} finally {
    # Restore my_agent.py
    Write-Host ""
    Write-Host "Restoring my_agent.py..." -ForegroundColor Yellow
    if (Test-Path "$backupDir\my_agent.py") {
        Copy-Item "$backupDir\my_agent.py" "my_agent.py" -Force
        Write-Host "  [OK] Restored my_agent.py" -ForegroundColor Green
    }
    
    # Clean up backup (optional - keep for safety)
    # Remove-Item -Recurse -Force $backupDir
    Write-Host "  [INFO] Backup saved in: $backupDir" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deployment Complete! ✅" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Check AWS Console for the new analyst agent runtime ARN" -ForegroundColor Gray
Write-Host "  2. Update ANALYST_AGENT_ARN in app.py with the new ARN" -ForegroundColor Gray
Write-Host "  3. Restart the backend server (app.py)" -ForegroundColor Gray
Write-Host ""
