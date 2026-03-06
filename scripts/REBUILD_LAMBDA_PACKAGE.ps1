# ====================================================================
# Rebuild Lambda Chat Package Cleanly
# ====================================================================
# This script rebuilds the lambda_chat_package directory with clean dependencies
# to fix the "ast.NodeVisitor" import error

$REGION = "us-east-1"

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Rebuilding Lambda Chat Package" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Remove old package directory and zip
Write-Host "[1/4] Cleaning old package..." -ForegroundColor Yellow
if (Test-Path "lambda_chat_package") {
    Remove-Item -Recurse -Force "lambda_chat_package"
    Write-Host "  [OK] Removed old lambda_chat_package directory" -ForegroundColor Green
}
if (Test-Path "lambda_chat_package.zip") {
    Remove-Item -Force "lambda_chat_package.zip"
    Write-Host "  [OK] Removed old zip file" -ForegroundColor Green
}
Write-Host ""

# Step 2: Create fresh package directory
Write-Host "[2/4] Creating fresh package directory..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path "lambda_chat_package" -Force | Out-Null
Write-Host "  [OK] Created lambda_chat_package directory" -ForegroundColor Green
Write-Host ""

# Step 3: Install dependencies
Write-Host "[3/4] Installing dependencies..." -ForegroundColor Yellow
Write-Host "  Installing boto3, botocore, and jmespath..." -ForegroundColor Gray

# Create a temporary requirements file for Lambda
# Note: jmespath is included in botocore, so we don't need to install it separately
$lambdaRequirements = @"
boto3==1.40.74
botocore==1.40.74
"@

$lambdaRequirements | Out-File -FilePath "lambda_requirements_temp.txt" -Encoding utf8

try {
    # Install to the package directory with all dependencies
    # Using --no-cache-dir to avoid any cached conflicts
    python -m pip install -r lambda_requirements_temp.txt -t lambda_chat_package --upgrade --no-cache-dir 2>&1 | Out-Null
    
    Write-Host "  [OK] Dependencies installed" -ForegroundColor Green
    
    # Remove duplicate package directories (keep only the latest)
    Write-Host "  Removing duplicate package versions..." -ForegroundColor Gray
    Get-ChildItem -Path "lambda_chat_package" -Directory -Filter "*-*.dist-info" | 
        Group-Object { ($_.Name -split '-')[0] } | 
        ForEach-Object { 
            $packages = $_.Group | Sort-Object Name -Descending
            if ($packages.Count -gt 1) {
                $packages[1..($packages.Count-1)] | Remove-Item -Recurse -Force
                Write-Host "    Removed duplicate: $($packages[1..($packages.Count-1)].Name -join ', ')" -ForegroundColor Gray
            }
        }
} catch {
    Write-Host "  [ERROR] Failed to install dependencies: $_" -ForegroundColor Red
    exit 1
} finally {
    Remove-Item "lambda_requirements_temp.txt" -Force -ErrorAction SilentlyContinue
}
Write-Host ""

# Step 4: Copy Lambda function
Write-Host "[4/4] Copying Lambda function..." -ForegroundColor Yellow
Copy-Item "lambda_brd_chat.py" -Destination "lambda_chat_package\lambda_brd_chat.py"
Write-Host "  [OK] Copied lambda_brd_chat.py" -ForegroundColor Green
Write-Host ""

# Step 5: Remove unnecessary files and fix conflicts
Write-Host "[5/5] Cleaning up unnecessary files..." -ForegroundColor Yellow
Get-ChildItem -Path "lambda_chat_package" -Recurse -Include "__pycache__","*.pyc",".DS_Store" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path "lambda_chat_package" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# CRITICAL: Remove any ast.py at root level - it shadows Python's built-in ast module
if (Test-Path "lambda_chat_package\ast.py") {
    Write-Host "  [WARNING] Found ast.py at root - removing to prevent shadowing Python's built-in ast module" -ForegroundColor Yellow
    Remove-Item "lambda_chat_package\ast.py" -Force
}

Write-Host "  [OK] Removed cache files and fixed conflicts" -ForegroundColor Green
Write-Host ""

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Package Rebuilt Successfully! ✅" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next step: Run .\DEPLOY_CHAT_LAMBDA.ps1 to deploy" -ForegroundColor Yellow
Write-Host ""

