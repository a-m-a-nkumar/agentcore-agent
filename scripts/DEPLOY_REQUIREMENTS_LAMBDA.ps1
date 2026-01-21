# ====================================================================
# DEPLOY REQUIREMENTS GATHERING LAMBDA
# Deploys the requirements gathering Lambda function to AWS
# ====================================================================

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deploy Requirements Gathering Lambda" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$FUNCTION_NAME = "requirements_gathering_lambda"
$PACKAGE_DIR = "lambda_requirements_package"
$ZIP_FILE = "lambda_requirements_package.zip"

# Step 1: Check AWS credentials
Write-Host "[1/6] Checking AWS credentials..." -ForegroundColor Yellow
$awsCheck = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] AWS credentials not configured!" -ForegroundColor Red
    Write-Host "  Run: aws configure" -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] AWS credentials valid" -ForegroundColor Green
Write-Host ""

# Step 2: Create package directory
Write-Host "[2/6] Creating package directory..." -ForegroundColor Yellow
if (Test-Path $PACKAGE_DIR) {
    Remove-Item -Recurse -Force $PACKAGE_DIR
}
New-Item -ItemType Directory -Path $PACKAGE_DIR | Out-Null
Write-Host "  [OK] Package directory created" -ForegroundColor Green
Write-Host ""

# Step 3: Copy Lambda function
Write-Host "[3/6] Copying Lambda function..." -ForegroundColor Yellow
if (Test-Path "lambda_requirements_gathering.py") {
    Copy-Item "lambda_requirements_gathering.py" "$PACKAGE_DIR\lambda_requirements_gathering.py" -Force
    Write-Host "  [OK] Copied lambda_requirements_gathering.py" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] lambda_requirements_gathering.py not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 4: Create deployment package
Write-Host "[4/6] Creating deployment package..." -ForegroundColor Yellow
if (Test-Path $ZIP_FILE) {
    Remove-Item $ZIP_FILE -Force
}

# Use Python script to create zip (handles dependencies if needed)
Set-Location $PACKAGE_DIR
Compress-Archive -Path * -DestinationPath "..\$ZIP_FILE" -Force
Set-Location ..

if (Test-Path $ZIP_FILE) {
    $zipSize = (Get-Item $ZIP_FILE).Length / 1MB
    Write-Host "  [OK] Package created: $ZIP_FILE ($([math]::Round($zipSize, 2)) MB)" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Failed to create package!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 5: Check if Lambda function exists
Write-Host "[5/6] Checking if Lambda function exists..." -ForegroundColor Yellow
$functionExists = aws lambda get-function --function-name $FUNCTION_NAME 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [INFO] Function exists, will update code" -ForegroundColor Yellow
    $updateMode = $true
} else {
    Write-Host "  [INFO] Function doesn't exist, will create new" -ForegroundColor Yellow
    $updateMode = $false
}
Write-Host ""

# Step 6: Deploy to AWS Lambda
Write-Host "[6/6] Deploying to AWS Lambda..." -ForegroundColor Yellow

if ($updateMode) {
    # Update existing function
    aws lambda update-function-code `
        --function-name $FUNCTION_NAME `
        --zip-file "fileb://$ZIP_FILE"
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Lambda function code updated!" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to update Lambda function!" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [INFO] To create a new Lambda function, use AWS Console or CLI with:" -ForegroundColor Yellow
    Write-Host "  aws lambda create-function --function-name $FUNCTION_NAME \" -ForegroundColor Cyan
    Write-Host "    --runtime python3.13 \" -ForegroundColor Cyan
    Write-Host "    --role <YOUR_LAMBDA_ROLE_ARN> \" -ForegroundColor Cyan
    Write-Host "    --handler lambda_requirements_gathering.lambda_handler \" -ForegroundColor Cyan
    Write-Host "    --zip-file fileb://$ZIP_FILE \" -ForegroundColor Cyan
    Write-Host "    --timeout 300 \" -ForegroundColor Cyan
    Write-Host "    --memory-size 512" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Deployment Complete! ✅" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Function Name: $FUNCTION_NAME" -ForegroundColor Yellow
Write-Host "Package: $ZIP_FILE" -ForegroundColor Yellow
Write-Host ""
