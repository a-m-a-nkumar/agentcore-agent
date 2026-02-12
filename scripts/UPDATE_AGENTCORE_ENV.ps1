# Update AgentCore Runtime Environment Variables from .env
# Sets BEDROCK_GUARDRAIL_ARN and BEDROCK_GUARDRAIL_VERSION so agents use guardrails when calling Bedrock.
# Run from project root: .\scripts\UPDATE_AGENTCORE_ENV.ps1

$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "  Update AgentCore Runtime Environment from .env" -ForegroundColor Cyan
Write-Host "  (BEDROCK_GUARDRAIL_ARN, BEDROCK_GUARDRAIL_VERSION)" -ForegroundColor Gray
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
Set-Location $RootDir

if (-not (Test-Path ".env")) {
    Write-Host "  [ERROR] .env file not found!" -ForegroundColor Red
    exit 1
}

Write-Host "  Reading .env and updating AgentCore runtimes..." -ForegroundColor Yellow
python scripts/update_agentcore_env.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  [OK] AgentCore runtime environment variables updated!" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  [WARN] Update completed with errors (see above)" -ForegroundColor Yellow
}
Write-Host ""
