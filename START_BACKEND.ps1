# ====================================================================
# START BACKEND SERVER
# AgentCore Backend (FastAPI + Python)
# ====================================================================

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  AgentCore Backend Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check if virtual environment exists
Write-Host "[1/5] Checking virtual environment..." -ForegroundColor Yellow
if (-Not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "  [WARN] Virtual environment not found!" -ForegroundColor Yellow
    Write-Host "  Creating virtual environment..." -ForegroundColor Gray
    python -m venv .venv
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Virtual environment created!" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to create virtual environment!" -ForegroundColor Red
        Write-Host "  Make sure Python is installed and in PATH" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Host "  [OK] Virtual environment found!" -ForegroundColor Green
}
Write-Host ""

# Step 2: Activate virtual environment
Write-Host "[2/5] Activating virtual environment..." -ForegroundColor Yellow
try {
    & ".\.venv\Scripts\Activate.ps1"
    Start-Sleep -Milliseconds 500
    
    if ($env:VIRTUAL_ENV) {
        Write-Host "  [OK] Virtual environment activated!" -ForegroundColor Green
        Write-Host "  Path: $env:VIRTUAL_ENV" -ForegroundColor Gray
    } else {
        Write-Host "  [ERROR] Failed to activate virtual environment!" -ForegroundColor Red
        Write-Host "  Run this command first:" -ForegroundColor Yellow
        Write-Host "  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Cyan
        exit 1
    }
} catch {
    Write-Host "  [ERROR] Failed to activate virtual environment!" -ForegroundColor Red
    Write-Host "  Error: $_" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 3: Check and install dependencies
Write-Host "[3/5] Checking dependencies..." -ForegroundColor Yellow
$pipList = pip list 2>&1
if ($pipList -notmatch "fastapi") {
    Write-Host "  [WARN] Dependencies not installed!" -ForegroundColor Yellow
    Write-Host "  Installing from requirements.txt..." -ForegroundColor Gray
    pip install -r requirements.txt
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  [OK] Dependencies installed successfully!" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Failed to install dependencies!" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [OK] Dependencies already installed!" -ForegroundColor Green
}
Write-Host ""

# Step 4: Check AWS credentials
Write-Host "[4/5] Checking AWS credentials..." -ForegroundColor Yellow
$awsCheck = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -eq 0) {
    $identity = $awsCheck | ConvertFrom-Json
    Write-Host "  [OK] AWS credentials configured!" -ForegroundColor Green
    Write-Host "  Account: $($identity.Account)" -ForegroundColor Gray
    Write-Host "  User: $($identity.Arn.Split('/')[-1])" -ForegroundColor Gray
} else {
    Write-Host "  [WARN] AWS credentials not configured!" -ForegroundColor Yellow
    Write-Host "  The backend may not work properly without AWS credentials." -ForegroundColor Yellow
    Write-Host "  Configure using: aws configure" -ForegroundColor Cyan
}
Write-Host ""

# Step 5: Check environment variables
Write-Host "[5/5] Checking environment variables..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Write-Host "  [OK] .env file found!" -ForegroundColor Green
} else {
    Write-Host "  [WARN] .env file not found!" -ForegroundColor Yellow
    Write-Host "  Create a .env file with required configuration" -ForegroundColor Yellow
}
Write-Host ""

# Start the backend server
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Starting FastAPI Backend Server..." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Server Information:" -ForegroundColor Yellow
Write-Host "  - Backend URL: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  - API Docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host "  - ReDoc: http://localhost:8000/redoc" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Run uvicorn with hot reload for development
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Cleanup on exit
Write-Host ""
Write-Host "[INFO] Server stopped." -ForegroundColor Yellow
Write-Host "Deactivating virtual environment..." -ForegroundColor Gray
deactivate
Write-Host "[OK] Cleanup complete!" -ForegroundColor Green
Write-Host ""
