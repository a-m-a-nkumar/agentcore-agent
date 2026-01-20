# ====================================================================
# Deploy BRD Generator Lambda Function
# ====================================================================

$REGION = "us-east-1"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying BRD Generator Lambda" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
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

# Step 2: Copy latest generator file to package
Write-Host "[2/3] Preparing generator Lambda package..." -ForegroundColor Yellow
Write-Host ""

try {
    # Copy latest lambda_brd_generator.py to package directory
    if (Test-Path "lambda_brd_generator.py") {
        Copy-Item "lambda_brd_generator.py" "lambda_generator_package\lambda_brd_generator.py" -Force
        Write-Host "  [OK] Copied latest lambda_brd_generator.py to package" -ForegroundColor Green
    } else {
        Write-Host "  [WARN] lambda_brd_generator.py not found in root directory" -ForegroundColor Yellow
    }
    
    # Remove old zip if exists
    if (Test-Path "lambda_generator_package.zip") {
        Remove-Item "lambda_generator_package.zip" -Force
        Write-Host "  Removed old zip file" -ForegroundColor Gray
    }
    
    Write-Host "  Creating zip package from lambda_generator_package/..." -ForegroundColor Gray
    
    # Use PowerShell Compress-Archive
    cd lambda_generator_package
    Compress-Archive -Path * -DestinationPath ../lambda_generator_package.zip -Force
    cd ..
    
    if (Test-Path "lambda_generator_package.zip") {
        $zipSize = (Get-Item "lambda_generator_package.zip").Length / 1MB
        Write-Host "  [OK] Package created: lambda_generator_package.zip ($([math]::Round($zipSize, 2)) MB)" -ForegroundColor Green
    } else {
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
    Write-Host "  Uploading lambda_generator_package.zip to brd_generator_lambda..." -ForegroundColor Gray
    
    $result = aws lambda update-function-code `
        --function-name brd_generator_lambda `
        --zip-file fileb://lambda_generator_package.zip `
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
Write-Host "The BRD generator Lambda is now live." -ForegroundColor Cyan
Write-Host ""
Write-Host "Note: It may take 10-30 seconds for the new code to be active." -ForegroundColor Yellow
Write-Host ""


