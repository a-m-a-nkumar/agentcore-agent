# Check AgentCore Permissions Script
# This script helps verify if your IAM user has permissions to invoke AgentCore

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "AgentCore Permissions Check" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Get current identity
Write-Host "Current AWS Identity:" -ForegroundColor Yellow
$identity = aws sts get-caller-identity 2>&1 | ConvertFrom-Json
Write-Host "  Account: $($identity.Account)" -ForegroundColor White
Write-Host "  User ARN: $($identity.Arn)" -ForegroundColor White
Write-Host ""

# Check if user can invoke AgentCore
Write-Host "Testing AgentCore Invoke Permission..." -ForegroundColor Yellow
$agentArn = "arn:aws:bedrock-agentcore:us-east-1:448049797912:runtime/my_agent-0BLwDgF9uK"

# Try to describe the agent (requires bedrock-agentcore:DescribeAgentRuntime permission)
Write-Host "Attempting to describe agent..." -ForegroundColor Gray
$describeResult = aws bedrock-agentcore describe-agent-runtime --agent-runtime-arn $agentArn 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Can describe agent - basic permissions OK" -ForegroundColor Green
} else {
    Write-Host "❌ Cannot describe agent" -ForegroundColor Red
    Write-Host "Error: $describeResult" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Required IAM Permissions:" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Your IAM user needs these permissions:" -ForegroundColor Yellow
Write-Host "  - bedrock-agentcore:InvokeAgentRuntime" -ForegroundColor White
Write-Host "  - bedrock-agentcore:DescribeAgentRuntime (optional, for testing)" -ForegroundColor White
Write-Host ""
Write-Host "If you don't have these permissions, ask your AWS administrator to add:" -ForegroundColor Yellow
Write-Host "  - Policy: AmazonBedrockAgentCoreFullAccess" -ForegroundColor White
Write-Host "  - Or custom policy with InvokeAgentRuntime permission" -ForegroundColor White
Write-Host ""










