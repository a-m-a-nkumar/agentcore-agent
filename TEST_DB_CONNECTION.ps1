# Database Connection Test Runner
# Sets environment variables and runs the connection test

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  DATABASE CONNECTION TEST" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Set environment variables (only for this session)
$env:DATABASE_HOST = "deluxe-db.c7ameyeqe2m2.us-east-1.rds.amazonaws.com"
$env:DATABASE_PORT = "5432"
$env:DATABASE_NAME = "postgres"
$env:DATABASE_USER = "postgres"
$env:DATABASE_PASSWORD = "]S7]_qph(k(GNiM9oGU>EXKuUQz$"

Write-Host "Environment variables set (session only)" -ForegroundColor Green
Write-Host ""

# Check if psycopg2 is installed
Write-Host "Checking for psycopg2..." -ForegroundColor Yellow
python -c "import psycopg2" 2>$null

if ($LASTEXITCODE -ne 0) {
    Write-Host "psycopg2 not found. Installing..." -ForegroundColor Yellow
    pip install psycopg2-binary
    Write-Host ""
}

# Run the test
Write-Host "Running connection test..." -ForegroundColor Yellow
Write-Host ""
python test_db_connection.py

# Clear sensitive environment variables
Remove-Item Env:DATABASE_PASSWORD -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Environment variables cleared" -ForegroundColor Green
