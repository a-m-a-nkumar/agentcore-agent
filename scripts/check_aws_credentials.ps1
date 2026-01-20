# Check AWS Credentials Script
# This script helps verify and refresh AWS credentials

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "AWS Credentials Check" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Check if AWS CLI is installed
try {
    $awsVersion = aws --version 2>&1
    Write-Host "✅ AWS CLI installed: $awsVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ AWS CLI not found. Please install it first." -ForegroundColor Red
    Write-Host "   Download from: https://aws.amazon.com/cli/" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# Check current credentials
Write-Host "Checking current AWS credentials..." -ForegroundColor Yellow
try {
    $identity = aws sts get-caller-identity 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ AWS credentials are valid!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Current Identity:" -ForegroundColor Cyan
        $identity | ConvertFrom-Json | Format-List
    } else {
        Write-Host "❌ AWS credentials are invalid or expired!" -ForegroundColor Red
        Write-Host ""
        Write-Host "Error:" -ForegroundColor Yellow
        Write-Host $identity -ForegroundColor Red
    }
} catch {
    Write-Host "❌ Failed to check credentials: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "How to Fix Credentials:" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Option 1: Configure AWS CLI" -ForegroundColor Yellow
Write-Host "  aws configure" -ForegroundColor White
Write-Host ""
Write-Host "Option 2: Use AWS SSO (if configured)" -ForegroundColor Yellow
Write-Host "  aws sso login" -ForegroundColor White
Write-Host ""
Write-Host "Option 3: Set Environment Variables" -ForegroundColor Yellow
Write-Host "  `$env:AWS_ACCESS_KEY_ID = 'your-access-key'" -ForegroundColor White
Write-Host "  `$env:AWS_SECRET_ACCESS_KEY = 'your-secret-key'" -ForegroundColor White
Write-Host "  `$env:AWS_SESSION_TOKEN = 'your-session-token'  # If using temporary credentials" -ForegroundColor White
Write-Host ""
Write-Host "Option 4: Use AWS Profile" -ForegroundColor Yellow
Write-Host "  `$env:AWS_PROFILE = 'your-profile-name'" -ForegroundColor White
Write-Host ""










