# PUSH_ECR.ps1
# Push backend and frontend Docker images to ECR with tags backend and frontend.
# Production ports: backend 8000, frontend 8080 (container ports; ECS maps these).
# Prereqs: Build both images first:
#   - From agentcore-starter:  docker build -t backend .
#   - From deluxe-sdlc-frontend: docker build -t frontend .

$ErrorActionPreference = "Stop"
$Region = "us-east-1"
$AccountId = "448049797912"
$EcrUri = "${AccountId}.dkr.ecr.${Region}.amazonaws.com"
$RepoName = "deluxe-sdlc"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Push backend & frontend to ECR" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. Verify AWS identity
Write-Host "[1/5] Verifying AWS identity..." -ForegroundColor Yellow
aws sts get-caller-identity --region $Region
if ($LASTEXITCODE -ne 0) { throw "AWS identity check failed. Run aws configure or set credentials." }
Write-Host ""

# 2. Login to ECR
Write-Host "[2/5] Logging in to ECR..." -ForegroundColor Yellow
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $EcrUri
if ($LASTEXITCODE -ne 0) { throw "ECR login failed." }
Write-Host ""

# 3. Ensure repository exists (create if not)
Write-Host "[3/5] Ensuring ECR repository '$RepoName' exists..." -ForegroundColor Yellow
$repoExists = aws ecr describe-repositories --repository-names $RepoName --region $Region 2>$null
if (-not $repoExists) {
    Write-Host "Creating repository $RepoName..." -ForegroundColor Gray
    aws ecr create-repository --repository-name $RepoName --region $Region
}
Write-Host ""

# 4. Tag and push backend (port 8000)
Write-Host "[4/5] Tagging and pushing backend:latest -> ${EcrUri}/${RepoName}:backend ..." -ForegroundColor Yellow
docker tag backend:latest "${EcrUri}/${RepoName}:backend"
docker push "${EcrUri}/${RepoName}:backend"
if ($LASTEXITCODE -ne 0) { throw "Backend push failed. Build first: docker build -t backend ." }
Write-Host ""

# 5. Tag and push frontend (port 8080)
Write-Host "[5/5] Tagging and pushing frontend:latest -> ${EcrUri}/${RepoName}:frontend ..." -ForegroundColor Yellow
docker tag frontend:latest "${EcrUri}/${RepoName}:frontend"
docker push "${EcrUri}/${RepoName}:frontend"
if ($LASTEXITCODE -ne 0) { throw "Frontend push failed. Build first (from deluxe-sdlc-frontend): docker build -t frontend ." }
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Done. Images in ECR:" -ForegroundColor Green
Write-Host "  - ${EcrUri}/${RepoName}:backend  (container port 8000)" -ForegroundColor Green
Write-Host "  - ${EcrUri}/${RepoName}:frontend (container port 8080)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
