# Quick script to get the ECR Image URI for analyst-agent

$accountId = (aws sts get-caller-identity --query Account --output text)
$region = "us-east-1"
$repoName = "deluxe-sdlc"  # Same repository as my_agent
$imageTag = "analyst-agent"  # Use specific tag instead of 'latest'

$imageUri = "$accountId.dkr.ecr.$region.amazonaws.com/$repoName" + ":" + "$imageTag"

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Analyst Agent Image URI" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host $imageUri -ForegroundColor Green
Write-Host ""
Write-Host "Copy this URI and paste it in AWS Console when creating the agent." -ForegroundColor Yellow
Write-Host ""

