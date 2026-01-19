# Alternative: Build Analyst Agent using agentcore launch (handles ARM64 automatically)
# This is the RECOMMENDED method as it builds ARM64 in the cloud via CodeBuild

# Set console encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying ANALYST_AGENT via agentcore launch" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  This script deploys: analyst_agent (Business Analyst)" -ForegroundColor Yellow
Write-Host "⚠️  For my_agent, use: DEPLOY_AGENT.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "✅ This method builds ARM64 automatically via CodeBuild (no local Docker needed)" -ForegroundColor Green
Write-Host ""

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
cd $ScriptDir

Write-Host "[1/4] Activating virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv\Scripts\Activate.ps1") {
    .venv\Scripts\Activate.ps1
    Write-Host "  [OK] Virtual environment activated" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Virtual environment not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Check if agentcore CLI is available
Write-Host "[2/4] Checking for AgentCore CLI..." -ForegroundColor Yellow
$agentcoreCmd = Get-Command agentcore -ErrorAction SilentlyContinue
if (-not $agentcoreCmd) {
    Write-Host "  [ERROR] AgentCore CLI not found!" -ForegroundColor Red
    Write-Host "  Installing bedrock-agentcore-starter-toolkit..." -ForegroundColor Gray
    pip install bedrock-agentcore-starter-toolkit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Failed to install toolkit!" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Toolkit installed" -ForegroundColor Green
} else {
    Write-Host "  [OK] AgentCore CLI found" -ForegroundColor Green
}
Write-Host ""

# Verify AWS credentials and get account info
Write-Host "[3/4] Verifying AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity --region us-east-1 2>&1 | ConvertFrom-Json
    $accountId = $identity.Account
    $region = "us-east-1"
    Write-Host "  [OK] AWS Account: $accountId" -ForegroundColor Green
    Write-Host "  [OK] User ARN: $($identity.Arn)" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Verify analyst_agent directory exists
$agentDir = ".bedrock_agentcore\analyst_agent"
if (-not (Test-Path $agentDir)) {
    Write-Host "  [ERROR] analyst_agent directory not found: $agentDir" -ForegroundColor Red
    exit 1
}
Write-Host "[INFO] Deploying agent from: $agentDir" -ForegroundColor Cyan
Write-Host ""

# Deploy using agentcore launch (builds ARM64 in cloud)
Write-Host "[4/4] Deploying analyst_agent via agentcore launch..." -ForegroundColor Yellow
Write-Host "  Agent: analyst_agent (Business Analyst)" -ForegroundColor Cyan
Write-Host "  Directory: .bedrock_agentcore\analyst_agent" -ForegroundColor Cyan
Write-Host "  ECR Repository: deluxe-sdlc" -ForegroundColor Cyan
Write-Host "  Image Tag: analyst-agent (after retagging)" -ForegroundColor Cyan
Write-Host "  This will build ARM64 image in the cloud via CodeBuild" -ForegroundColor Cyan
Write-Host "  This may take 5-10 minutes..." -ForegroundColor Gray
Write-Host ""

try {
    # Set environment variable to suppress emoji output if needed
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONLEGACYWINDOWSSTDIO = "1"
    
    # IMPORTANT: agentcore launch must be run from ROOT directory (like my_agent script)
    # It automatically looks for .bedrock_agentcore/analyst_agent
    # DO NOT cd into the agent directory - stay in root!
    Write-Host "  Running agentcore launch from root directory..." -ForegroundColor Gray
    Write-Host "  (agentcore launch will automatically detect .bedrock_agentcore/analyst_agent)" -ForegroundColor Gray
    Write-Host ""
    
    # Run agentcore launch from ROOT directory (same as my_agent script)
    # It will automatically detect and use .bedrock_agentcore/analyst_agent
    $output = agentcore launch 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    
    # Check if deployment succeeded (ignore encoding errors if deployment worked)
    if ($exitCode -eq 0 -or $output -match "deployed|success|complete|runtime") {
        Write-Host "  [OK] Analyst agent deployed successfully" -ForegroundColor Green
        
        # Try to extract the agent ARN from output
        $agentArn = $null
        if ($output -match "arn:aws:bedrock-agentcore:([^\s]+)") {
            $agentArn = $matches[0]
            Write-Host "  [INFO] Agent ARN: $agentArn" -ForegroundColor Cyan
        }
        
        # We're already in script directory (root), no need to cd
        
        # Always retag from 'latest' to 'analyst-agent' after deployment
        # This ensures the most recent image is always tagged as 'analyst-agent'
        Write-Host ""
        Write-Host "[5/5] Retagging image from 'latest' to 'analyst-agent'..." -ForegroundColor Yellow
        Write-Host "  Repository: deluxe-sdlc" -ForegroundColor Cyan
        Write-Host "  Source Tag: latest" -ForegroundColor Cyan
        Write-Host "  Target Tag: analyst-agent" -ForegroundColor Cyan
        Write-Host "  (This ensures the most recent image is tagged as 'analyst-agent')" -ForegroundColor Gray
        
        try {
            # Check if 'latest' tag exists (should exist after agentcore launch)
            $latestCheck = aws ecr describe-images --repository-name deluxe-sdlc --region us-east-1 --image-ids imageTag=latest 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  [OK] Found 'latest' tag, retagging to 'analyst-agent'..." -ForegroundColor Green
                
                # Set environment variables for retag script
                $env:ECR_REPOSITORY = "deluxe-sdlc"
                $env:SOURCE_TAG = "latest"
                $env:TARGET_TAG = "analyst-agent"
                python retag_image.py
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [OK] Image retagged to 'analyst-agent' and 'latest' tag removed" -ForegroundColor Green
                    Write-Host "  [INFO] Most recent image is now tagged as 'analyst-agent'" -ForegroundColor Cyan
                } else {
                    Write-Host "  [WARN] Image retagging failed - check if 'latest' tag exists" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  [WARN] 'latest' tag not found - image may have been tagged differently" -ForegroundColor Yellow
                Write-Host "  [INFO] Checking if 'analyst-agent' tag already exists..." -ForegroundColor Gray
                
                # Check if analyst-agent tag exists
                $analystCheck = aws ecr describe-images --repository-name deluxe-sdlc --region us-east-1 --image-ids imageTag=analyst-agent 2>&1
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  [OK] Image already tagged as 'analyst-agent'" -ForegroundColor Green
                } else {
                    Write-Host "  [WARN] Neither 'latest' nor 'analyst-agent' tag found" -ForegroundColor Yellow
                    Write-Host "  [INFO] Please check AWS Console for the actual image tags" -ForegroundColor Gray
                }
            }
        } catch {
            Write-Host "  [WARN] Could not retag image: $_" -ForegroundColor Yellow
            Write-Host "  [INFO] You may need to manually retag the image in AWS Console" -ForegroundColor Gray
        }
        
        # Construct and display the final image URI
        $imageUri = "$accountId.dkr.ecr.$region.amazonaws.com/deluxe-sdlc:analyst-agent"
        
        Write-Host ""
        Write-Host "=====================================" -ForegroundColor Cyan
        Write-Host "Deployment Complete! ✅" -ForegroundColor Green
        Write-Host "=====================================" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Image URI for AWS Console:" -ForegroundColor Yellow
        Write-Host "  $imageUri" -ForegroundColor White
        Write-Host ""
        
        if ($agentArn) {
            Write-Host "Agent ARN:" -ForegroundColor Yellow
            Write-Host "  $agentArn" -ForegroundColor White
            Write-Host ""
            Write-Host "IMPORTANT: Update ANALYST_AGENT_ARN in app.py:" -ForegroundColor Yellow
            Write-Host "  ANALYST_AGENT_ARN = `"$agentArn`"" -ForegroundColor Gray
            Write-Host ""
        }
    } elseif ($output -match "UnicodeEncodeError|charmap") {
        # Encoding error but deployment might have succeeded
        Write-Host "  [WARN] Console encoding issue detected, checking deployment status..." -ForegroundColor Yellow
        Write-Host "  [INFO] Deployment may have succeeded despite encoding error" -ForegroundColor Yellow
        Write-Host "  Please check AWS Console to verify deployment" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Analyst agent deployment failed!" -ForegroundColor Red
        Write-Host "  Error output: $($output.Substring(0, [Math]::Min(500, $output.Length)))" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "  [ERROR] Failed to deploy analyst agent: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Use the Image URI above to create the agent in AWS Console (if not already created)" -ForegroundColor Gray
Write-Host "  2. Update ANALYST_AGENT_ARN in app.py with the agent ARN" -ForegroundColor Gray
Write-Host "  3. Restart the backend server (app.py)" -ForegroundColor Gray
Write-Host ""

