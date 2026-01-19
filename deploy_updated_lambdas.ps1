# ====================================================================
# Deploy Updated Lambda Functions with Converse API
# ====================================================================

$REGION = "us-east-1"
$MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deploying Updated Lambda Functions" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Updates:" -ForegroundColor White
Write-Host "  - API: invoke_model() -> converse()" -ForegroundColor Gray
Write-Host "  - Model: Inference Profile ($MODEL_ID)" -ForegroundColor Gray
Write-Host ""

# Step 1: Update environment variables
Write-Host "[1/3] Updating Lambda environment variables..." -ForegroundColor Yellow
Write-Host ""

$lambdas = @("brd_generator_lambda", "brd_chat_lambda")

foreach ($func in $lambdas) {
    Write-Host "  Updating $func..." -ForegroundColor Gray
    try {
        aws lambda update-function-configuration `
            --function-name $func `
            --environment "Variables={BEDROCK_MODEL_ID=$MODEL_ID}" `
            --region $REGION `
            --output json | Out-Null
        Write-Host "  [OK] $func" -ForegroundColor Green
    } catch {
        Write-Host "  [WARN] $func - $_" -ForegroundColor Yellow
    }
}
Write-Host ""

# Step 2: Package and deploy generator Lambda
Write-Host "[2/3] Packaging and deploying brd_generator_lambda..." -ForegroundColor Yellow
Write-Host ""

try {
    # Create deployment package
    if (Test-Path "lambda_generator_package.zip") {
        Remove-Item "lambda_generator_package.zip" -Force
    }
    
    Write-Host "  Creating zip package..." -ForegroundColor Gray
    cd lambda_generator_package
    Compress-Archive -Path * -DestinationPath ../lambda_generator_package.zip -Force
    cd ..
    
    Write-Host "  Uploading to Lambda..." -ForegroundColor Gray
    aws lambda update-function-code `
        --function-name brd_generator_lambda `
        --zip-file fileb://lambda_generator_package.zip `
        --region $REGION `
        --output json | Out-Null
    
    Write-Host "  [OK] Generator deployed" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Failed to deploy generator: $_" -ForegroundColor Red
}
Write-Host ""

# Step 3: Package and deploy chat Lambda
Write-Host "[3/3] Packaging and deploying brd_chat_lambda..." -ForegroundColor Yellow
Write-Host ""

try {
    # Create deployment package
    if (Test-Path "lambda_chat_package.zip") {
        Remove-Item "lambda_chat_package.zip" -Force
    }
    
    Write-Host "  Creating zip package..." -ForegroundColor Gray
    cd lambda_chat_package
    Compress-Archive -Path * -DestinationPath ../lambda_chat_package.zip -Force
    cd ..
    
    Write-Host "  Uploading to Lambda..." -ForegroundColor Gray
    aws lambda update-function-code `
        --function-name brd_chat_lambda `
        --zip-file fileb://lambda_chat_package.zip `
        --region $REGION `
        --output json | Out-Null
    
    Write-Host "  [OK] Chat deployed" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Failed to deploy chat: $_" -ForegroundColor Red
}
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Lambda functions now use:" -ForegroundColor Cyan
Write-Host "  - API: converse() (modern API)" -ForegroundColor White
Write-Host "  - Model: $MODEL_ID" -ForegroundColor White
Write-Host ""
Write-Host "Benefits:" -ForegroundColor Cyan
Write-Host "  - Supports inference profiles" -ForegroundColor Green
Write-Host "  - Future-proof for AWS updates" -ForegroundColor Green
Write-Host "  - Same Claude Sonnet 4.5 capabilities" -ForegroundColor Green
Write-Host ""
Write-Host "Next Step:" -ForegroundColor Cyan
Write-Host "  Test at http://localhost:8001/agent" -ForegroundColor White
Write-Host ""





