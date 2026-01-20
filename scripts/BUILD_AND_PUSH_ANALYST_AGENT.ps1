# Build and Push ANALYST_AGENT Docker Image to ECR
# This script ONLY deploys analyst_agent - DO NOT use for my_agent
# This prepares the image for deployment via AWS Console

# Set console encoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Building ANALYST_AGENT Docker Image" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  This script deploys: analyst_agent (Business Analyst)" -ForegroundColor Yellow
Write-Host "⚠️  For my_agent, use: DEPLOY_AGENT.ps1" -ForegroundColor Yellow
Write-Host ""

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
cd $ScriptDir

# Verify we're deploying the correct agent
$agentDir = ".bedrock_agentcore\analyst_agent"
if (-not (Test-Path $agentDir)) {
    Write-Host "  [ERROR] analyst_agent directory not found: $agentDir" -ForegroundColor Red
    Write-Host "  This script is for analyst_agent only!" -ForegroundColor Red
    exit 1
}
Write-Host "[INFO] Building agent from: $agentDir" -ForegroundColor Cyan
Write-Host ""

# Configuration - ANALYST AGENT SPECIFIC
$region = "us-east-1"
$repoName = "deluxe-sdlc"  # Same ECR repo as my_agent, but different tag
$imageTag = "analyst-agent"  # Use specific tag instead of 'latest' to avoid conflicts

# Get AWS account ID
Write-Host "[1/5] Getting AWS account ID..." -ForegroundColor Yellow
try {
    $accountId = aws sts get-caller-identity --query Account --output text --region $region
    if (-not $accountId) {
        Write-Host "  [ERROR] Failed to get AWS account ID" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Account ID: $accountId" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    Write-Host "  Please run: aws configure" -ForegroundColor Yellow
    exit 1
}
Write-Host ""

# Verify ECR repository exists (deluxe-sdlc - same as my_agent)
Write-Host "[2/5] Checking ECR repository..." -ForegroundColor Yellow
$ecrUri = "$accountId.dkr.ecr.$region.amazonaws.com/$repoName"
try {
    aws ecr describe-repositories --repository-names $repoName --region $region 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Repository '$repoName' does not exist!" -ForegroundColor Red
        Write-Host "  [INFO] This repository should already exist (used by my_agent)" -ForegroundColor Yellow
        Write-Host "  [INFO] Please create it first or check the repository name" -ForegroundColor Yellow
        exit 1
    } else {
        Write-Host "  [OK] Repository exists: $repoName" -ForegroundColor Green
        Write-Host "  [INFO] Using same repository as my_agent (different tag: analyst-agent)" -ForegroundColor Cyan
    }
} catch {
    Write-Host "  [ERROR] Failed to check repository: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Login to ECR
Write-Host "[3/5] Logging in to ECR..." -ForegroundColor Yellow
try {
    # Get ECR password first (PowerShell handles this better)
    Write-Host "  Getting ECR login password..." -ForegroundColor Gray
    $ecrPassword = aws ecr get-login-password --region $region 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Failed to get ECR login password" -ForegroundColor Red
        Write-Host "  [INFO] Error: $ecrPassword" -ForegroundColor Gray
        exit 1
    }
    
    # Login to ECR using the password
    Write-Host "  Logging in to Docker..." -ForegroundColor Gray
    $ecrPassword | docker login --username AWS --password-stdin $ecrUri 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Logged in to ECR" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Docker login failed" -ForegroundColor Red
        Write-Host "  [INFO] Trying alternative method using cmd.exe..." -ForegroundColor Yellow
        
        # Alternative: Use cmd.exe for the pipe operation
        $loginScript = "aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $ecrUri"
        $null = cmd /c $loginScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] Logged in to ECR (using cmd.exe)" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] Both login methods failed" -ForegroundColor Red
            Write-Host "  [INFO] Please try manual login:" -ForegroundColor Yellow
            Write-Host "    aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $ecrUri" -ForegroundColor Gray
            Write-Host "  [INFO] Or in cmd.exe (not PowerShell):" -ForegroundColor Yellow
            Write-Host "    cmd /c `"aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $ecrUri`"" -ForegroundColor Gray
            exit 1
        }
    }
} catch {
    Write-Host "  [ERROR] Failed to login: $_" -ForegroundColor Red
    Write-Host "  [INFO] Please try manual login:" -ForegroundColor Yellow
    Write-Host "    aws ecr get-login-password --region $region | docker login --username AWS --password-stdin $ecrUri" -ForegroundColor Gray
    exit 1
}
Write-Host ""

# Build Docker image for ARM64 architecture (required by AgentCore)
Write-Host "[4/5] Building Docker image for ARM64..." -ForegroundColor Yellow
Write-Host "  ⚠️  CRITICAL: AgentCore requires ARM64 architecture" -ForegroundColor Yellow
Write-Host "  Architecture: linux/arm64 (required by AgentCore)" -ForegroundColor Cyan
Write-Host "  This may take a few minutes..." -ForegroundColor Gray
cd ".bedrock_agentcore\analyst_agent"
$localImageName = "$repoName" + ":" + "$imageTag"

# Check if buildx is available
$buildxCheck = docker buildx version 2>&1
$buildxAvailable = $LASTEXITCODE -eq 0

if ($buildxAvailable) {
    Write-Host "  Using docker buildx for ARM64 cross-platform build..." -ForegroundColor Gray
    
    # Check if builder exists, create if not
    $builderList = docker buildx ls 2>&1
    $builderExists = $builderList -match "arm64-builder|default"
    
    if (-not $builderExists) {
        Write-Host "  Creating ARM64 builder..." -ForegroundColor Gray
        docker buildx create --name arm64-builder --driver docker-container --platform linux/arm64,linux/amd64 --use 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [WARN] Could not create builder, using default..." -ForegroundColor Yellow
            docker buildx use default 2>&1 | Out-Null
        }
    } else {
        # Use existing builder
        docker buildx use default 2>&1 | Out-Null
    }
    
    # Build for ARM64 using buildx (--load to load into local Docker)
    Write-Host "  Building ARM64 image (this may take longer for cross-platform build)..." -ForegroundColor Gray
    try {
        docker buildx build --platform linux/arm64 -t $localImageName --load . 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [OK] ARM64 image built successfully" -ForegroundColor Green
        } else {
            Write-Host "  [ERROR] Docker buildx build failed" -ForegroundColor Red
            Write-Host "  [INFO] Try: docker buildx create --use --name arm64-builder" -ForegroundColor Yellow
            cd $ScriptDir
            exit 1
        }
    } catch {
        Write-Host "  [ERROR] Buildx build failed: $_" -ForegroundColor Red
        cd $ScriptDir
        exit 1
    }
} else {
    Write-Host "  [ERROR] docker buildx is not available!" -ForegroundColor Red
    Write-Host "  [INFO] AgentCore requires ARM64 architecture" -ForegroundColor Yellow
    Write-Host "  [INFO] Options:" -ForegroundColor Yellow
    Write-Host "    1. Install Docker Buildx (recommended)" -ForegroundColor Gray
    Write-Host "    2. Use 'agentcore launch' which builds ARM64 automatically via CodeBuild" -ForegroundColor Gray
    Write-Host "    3. Build on an ARM64 machine" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  [INFO] To use agentcore launch (builds ARM64 in cloud):" -ForegroundColor Cyan
    Write-Host "    cd .bedrock_agentcore\analyst_agent" -ForegroundColor Gray
    Write-Host "    agentcore launch" -ForegroundColor Gray
    cd $ScriptDir
    exit 1
}
cd $ScriptDir
Write-Host ""

# Tag image for ECR
Write-Host "[5/5] Tagging and pushing image to ECR..." -ForegroundColor Yellow
$fullImageUri = "$ecrUri" + ":" + "$imageTag"
try {
    docker tag $localImageName $fullImageUri
    Write-Host "  [OK] Image tagged: $fullImageUri" -ForegroundColor Green
    
    Write-Host "  Pushing to ECR (this may take a few minutes)..." -ForegroundColor Gray
    docker push $fullImageUri
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Image pushed successfully" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to push image" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "  [ERROR] Failed to tag/push: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "ANALYST_AGENT Build and Push Complete! ✅" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Deployed Agent: analyst_agent (Business Analyst)" -ForegroundColor White
Write-Host "ECR Repository: deluxe-sdlc (same as my_agent)" -ForegroundColor White
Write-Host "Image Tag: analyst-agent" -ForegroundColor White
Write-Host ""
Write-Host "Image URI for AWS Console:" -ForegroundColor Yellow
Write-Host "  $fullImageUri" -ForegroundColor White
Write-Host ""
Write-Host "Note: Image is tagged as 'analyst-agent' (not 'latest') to avoid conflicts" -ForegroundColor Cyan
Write-Host ""
Write-Host "⚠️  Remember: This script is ONLY for analyst_agent" -ForegroundColor Yellow
Write-Host "⚠️  For my_agent, use: DEPLOY_AGENT.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Go to AWS Console > Bedrock AgentCore > Runtime > Host Agent" -ForegroundColor Gray
Write-Host "  2. Click 'Create agent'" -ForegroundColor Gray
Write-Host "  3. Name: analyst_agent" -ForegroundColor Gray
Write-Host "  4. Source type: ECR Container" -ForegroundColor Gray
Write-Host "  5. Image URI: $fullImageUri" -ForegroundColor Gray
Write-Host "  6. Use the same IAM role as your my_agent" -ForegroundColor Gray
Write-Host "  7. After creation, copy the Agent ARN and update app.py" -ForegroundColor Gray
Write-Host ""

