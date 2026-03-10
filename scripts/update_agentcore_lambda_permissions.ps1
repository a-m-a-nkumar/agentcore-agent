# Update AgentCore Runtime Role Lambda Permissions
# Adds permissions for requirements_gathering_lambda and brd_from_history_lambda

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Updating AgentCore Lambda Permissions" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

$roleName = "AmazonBedrockAgentCoreSDKRuntime-us-east-1-e72c1a7c7a"
$policyName = "AgentCoreLambdaInvokePolicy"

Write-Host "Role: $roleName" -ForegroundColor Yellow
Write-Host "Policy: $policyName" -ForegroundColor Yellow
Write-Host ""

# Create updated policy document as JSON string
$policyJson = @'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "lambda:InvokeFunction",
                "lambda:InvokeAsync"
            ],
            "Resource": [
                "arn:aws:lambda:us-east-1:448049797912:function:brd_generator_lambda",
                "arn:aws:lambda:us-east-1:448049797912:function:brd_retriever_lambda",
                "arn:aws:lambda:us-east-1:448049797912:function:brd_chat_lambda",
                "arn:aws:lambda:us-east-1:448049797912:function:requirements_gathering_lambda",
                "arn:aws:lambda:us-east-1:448049797912:function:brd_from_history_lambda"
            ]
        }
    ]
}
'@

Write-Host "Updated Policy Document:" -ForegroundColor Yellow
Write-Host $policyJson -ForegroundColor Gray
Write-Host ""

# Save to temp file
$tempFile = [System.IO.Path]::GetTempFileName() + ".json"
$policyJson | Out-File -FilePath $tempFile -Encoding utf8 -NoNewline

Write-Host "Updating IAM policy..." -ForegroundColor Yellow
try {
    aws iam put-role-policy `
        --role-name $roleName `
        --policy-name $policyName `
        --policy-document "file://$tempFile" `
        --output json
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Successfully updated Lambda invoke permissions!" -ForegroundColor Green
        Write-Host ""
        Write-Host "The role can now invoke:" -ForegroundColor Yellow
        Write-Host "  - brd_generator_lambda" -ForegroundColor White
        Write-Host "  - brd_retriever_lambda" -ForegroundColor White
        Write-Host "  - brd_chat_lambda" -ForegroundColor White
        Write-Host "  - requirements_gathering_lambda" -ForegroundColor Green
        Write-Host "  - brd_from_history_lambda" -ForegroundColor Green
    } else {
        Write-Host "❌ Failed to update policy" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "❌ Error updating policy: $_" -ForegroundColor Red
    exit 1
} finally {
    # Clean up temp file
    if (Test-Path $tempFile) {
        Remove-Item $tempFile -Force
    }
}

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Done!" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
