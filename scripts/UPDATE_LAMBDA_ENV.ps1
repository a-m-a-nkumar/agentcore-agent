# ====================================================================
# Update Lambda Environment Variables from .env
# ====================================================================
# Reads .env and updates all Lambda functions with the configured values.
# Run from project root: .\scripts\UPDATE_LAMBDA_ENV.ps1
# ====================================================================

# Ensure we run from project root (parent of scripts/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "  Update Lambda Environment from .env" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".env")) {
    Write-Host "  [ERROR] .env file not found!" -ForegroundColor Red
    exit 1
}

Write-Host "  Reading .env and updating Lambda functions..." -ForegroundColor Yellow
python scripts/update_lambda_env.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  [OK] Lambda environment variables updated!" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  [ERROR] Update failed!" -ForegroundColor Red
    exit 1
}
