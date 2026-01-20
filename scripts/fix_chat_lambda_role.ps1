# Fix brd_chat_lambda role issue

Write-Host "Fixing brd_chat_lambda role..." -ForegroundColor Cyan

$workingRole = "arn:aws:iam::448049797912:role/lambda_exec_gateway_local_file_writer"

try {
    aws lambda update-function-configuration `
        --function-name brd_chat_lambda `
        --role $workingRole `
        --region us-east-1 `
        --no-cli-pager
    
    Write-Host "[SUCCESS] Role updated" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Manual fix via AWS Console:" -ForegroundColor Yellow
    Write-Host "1. Go to Lambda console" -ForegroundColor White
    Write-Host "2. Select brd_chat_lambda" -ForegroundColor White
    Write-Host "3. Configuration > Permissions" -ForegroundColor White
    Write-Host "4. Change execution role to: lambda_exec_gateway_local_file_writer" -ForegroundColor White
}

Write-Host ""
Write-Host "Done!" -ForegroundColor Green













