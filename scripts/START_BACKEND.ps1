# START_BACKEND.ps1
# Script to start the AgentCore Backend Server

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Starting AgentCore Backend Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if virtual environment exists
if (-Not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
    Write-Host "[ERROR] Virtual environment not found!" -ForegroundColor Red
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
    Write-Host "[SUCCESS] Virtual environment created!" -ForegroundColor Green
    Write-Host ""
}

# Activate virtual environment
Write-Host "[INFO] Activating virtual environment..." -ForegroundColor Yellow

# Try to activate and check if VIRTUAL_ENV is set
try {
    & ".\.venv\Scripts\Activate.ps1"
    Start-Sleep -Milliseconds 500
    
    # Check if virtual environment is activated by looking for VIRTUAL_ENV variable
    if ($env:VIRTUAL_ENV) {
        Write-Host "[SUCCESS] Virtual environment activated!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Failed to activate virtual environment!" -ForegroundColor Red
        Write-Host "You may need to run: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "[ERROR] Failed to activate virtual environment!" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Check if dependencies are installed
Write-Host "[INFO] Checking dependencies..." -ForegroundColor Yellow
$pipList = pip list 2>&1
if ($pipList -notmatch "fastapi") {
    Write-Host "[WARNING] Dependencies not found. Installing..." -ForegroundColor Yellow
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install dependencies!" -ForegroundColor Red
        exit 1
    }
    Write-Host "[SUCCESS] Dependencies installed!" -ForegroundColor Green
} else {
    Write-Host "[SUCCESS] Dependencies already installed!" -ForegroundColor Green
}
Write-Host ""

# Check AWS credentials
Write-Host "[INFO] Checking AWS credentials..." -ForegroundColor Yellow
$awsCheck = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] AWS credentials configured!" -ForegroundColor Green
} else {
    Write-Host "[WARNING] AWS credentials not configured or invalid!" -ForegroundColor Yellow
    Write-Host "The backend may not work properly without AWS credentials." -ForegroundColor Yellow
    Write-Host "Configure using: aws configure" -ForegroundColor Cyan
}
Write-Host ""

# Start the backend server
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Starting FastAPI Backend Server..." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Server will be available at:" -ForegroundColor Yellow
Write-Host "  - http://localhost:8000" -ForegroundColor Cyan
Write-Host "  - API Docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host ""

# Run uvicorn with hot reload for development
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Deactivate virtual environment on exit
Write-Host ""
Write-Host "[INFO] Server stopped. Deactivating virtual environment..." -ForegroundColor Yellow
deactivate
