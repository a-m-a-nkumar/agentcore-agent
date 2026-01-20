# ====================================================================
# Deploy Updated Chat Lambda Function (BRD Editing Fixes)
# ====================================================================
# 
# This script deploys ONLY the brd_chat_lambda with the latest fixes:
# - Section title matching (e.g., "section stakeholders")
# - Better S3 save verification
# - Fresh BRD reload after updates
#
# ====================================================================

$REGION = "us-east-1"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying Updated Chat Lambda" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Fixes included:" -ForegroundColor White
Write-Host "  [OK] Section title matching (e.g., 'section stakeholders')" -ForegroundColor Green
Write-Host "  [OK] Better S3 save verification" -ForegroundColor Green
Write-Host "  [OK] Fresh BRD reload after updates" -ForegroundColor Green
Write-Host "  [OK] Enhanced error handling" -ForegroundColor Green
Write-Host ""

# Step 1: Verify AWS credentials
Write-Host "[1/3] Verifying AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity --region $REGION 2>&1 | ConvertFrom-Json
    Write-Host "  [OK] AWS Account: $($identity.Account)" -ForegroundColor Green
    Write-Host "  [OK] User ARN: $($identity.Arn)" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    Write-Host "  Please run: aws configure" -ForegroundColor Yellow
    exit 1
}
Write-Host ""

# Step 2: Package the Lambda
Write-Host "[2/3] Packaging brd_chat_lambda..." -ForegroundColor Yellow
Write-Host ""

try {
    # Remove old zip if exists
    if (Test-Path "lambda_chat_package.zip") {
        Remove-Item "lambda_chat_package.zip" -Force
        Write-Host "  Removed old zip file" -ForegroundColor Gray
    }
    
    Write-Host "  Creating zip package from lambda_chat_package/..." -ForegroundColor Gray
    
    # Use Python's zipfile module to create zip properly (preserves directory structure)
    Write-Host "  Creating zip package using Python zipfile (preserves directory structure)..." -ForegroundColor Gray
    python create_lambda_zip.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [ERROR] Failed to create zip package!" -ForegroundColor Red
        exit 1
    }
    
} catch {
    Write-Host "  [ERROR] Failed to create package: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 3: Deploy to Lambda
Write-Host "[3/3] Deploying to AWS Lambda..." -ForegroundColor Yellow
Write-Host ""

try {
    Write-Host "  Uploading lambda_chat_package.zip to brd_chat_lambda..." -ForegroundColor Gray
    
    $result = aws lambda update-function-code `
        --function-name brd_chat_lambda `
        --zip-file fileb://lambda_chat_package.zip `
        --region $REGION `
        --output json 2>&1
    
    if ($LASTEXITCODE -eq 0) {
        $lambdaInfo = $result | ConvertFrom-Json
        Write-Host "  [OK] Lambda function updated successfully!" -ForegroundColor Green
        Write-Host "  Function: $($lambdaInfo.FunctionName)" -ForegroundColor Gray
        Write-Host "  Version: $($lambdaInfo.Version)" -ForegroundColor Gray
        Write-Host "  Last Modified: $($lambdaInfo.LastModified)" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Deployment failed!" -ForegroundColor Red
        Write-Host $result -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "  [ERROR] Failed to deploy: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deployment Complete! ✅" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "The updated chat Lambda is now live with:" -ForegroundColor Cyan
Write-Host "  • Section title matching support" -ForegroundColor White
Write-Host "  • Improved update persistence" -ForegroundColor White
Write-Host "  • Better error handling" -ForegroundColor White
Write-Host ""
Write-Host "Test it now:" -ForegroundColor Cyan
Write-Host "  Try: 'update sarah chen to aman in section stakeholders'" -ForegroundColor White
Write-Host "  Then: 'show stakeholders' to verify the change" -ForegroundColor White
Write-Host ""
Write-Host "Note: It may take 10-30 seconds for the new code to be active." -ForegroundColor Yellow
Write-Host ""

