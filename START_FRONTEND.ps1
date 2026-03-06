# ====================================================================
# START FRONTEND SERVER
# Deluxe SDLC Frontend (React + Vite)
# ====================================================================

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Deluxe SDLC Frontend Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Frontend directory path
$FrontendPath = "C:\Users\ArushSingh\Desktop\FRONTEND1"

# Step 1: Check if frontend directory exists
Write-Host "[1/4] Checking frontend directory..." -ForegroundColor Yellow
if (-Not (Test-Path $FrontendPath)) {
    Write-Host "  [ERROR] Frontend directory not found!" -ForegroundColor Red
    Write-Host "  Expected path: $FrontendPath" -ForegroundColor Yellow
    Write-Host "  Please update the path in this script if needed." -ForegroundColor Yellow
    exit 1
}
Write-Host "  [OK] Frontend directory found!" -ForegroundColor Green
Write-Host "  Path: $FrontendPath" -ForegroundColor Gray
Write-Host ""

# Step 2: Navigate to frontend directory
Write-Host "[2/4] Navigating to frontend directory..." -ForegroundColor Yellow
Set-Location -Path $FrontendPath
Write-Host "  [OK] Current directory: $(Get-Location)" -ForegroundColor Green
Write-Host ""

# Step 3: Check Node.js installation
Write-Host "[3/4] Checking Node.js installation..." -ForegroundColor Yellow
$nodeVersion = node --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Node.js installed: $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] Node.js not found!" -ForegroundColor Red
    Write-Host "  Please install Node.js from: https://nodejs.org/" -ForegroundColor Yellow
    exit 1
}

$npmVersion = npm --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] npm installed: v$npmVersion" -ForegroundColor Green
} else {
    Write-Host "  [ERROR] npm not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Step 4: Check and install dependencies
Write-Host "[4/4] Checking dependencies..." -ForegroundColor Yellow
if (-Not (Test-Path "node_modules")) {
    Write-Host "  [WARN] node_modules not found!" -ForegroundColor Yellow
    Write-Host "  Installing dependencies (this may take a few minutes)..." -ForegroundColor Gray
    Write-Host ""
    npm install
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "  [OK] Dependencies installed successfully!" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "  [ERROR] Failed to install dependencies!" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "  [OK] Dependencies already installed!" -ForegroundColor Green
}
Write-Host ""

# Check environment variables
if (Test-Path ".env") {
    Write-Host "  [OK] .env file found!" -ForegroundColor Green
} else {
    Write-Host "  [WARN] .env file not found!" -ForegroundColor Yellow
    Write-Host "  The frontend may need environment variables to connect to backend." -ForegroundColor Yellow
}
Write-Host ""

# Start the development server
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Starting Vite Development Server..." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Server Information:" -ForegroundColor Yellow
Write-Host "  - Frontend URL: http://localhost:5173" -ForegroundColor Cyan
Write-Host "  - Network URL: Will be shown below" -ForegroundColor Cyan
Write-Host ""
Write-Host "Features:" -ForegroundColor Yellow
Write-Host "  - Hot Module Replacement (HMR) enabled" -ForegroundColor Gray
Write-Host "  - Auto-reload on file changes" -ForegroundColor Gray
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Start the Vite dev server
npm run dev

# Cleanup message on exit
Write-Host ""
Write-Host "[INFO] Frontend server stopped." -ForegroundColor Yellow
Write-Host ""
