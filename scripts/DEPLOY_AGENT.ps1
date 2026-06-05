# Deploy AgentCore Agent with 'agentcore' tag

Write-Host "Deploying AgentCore Agent..." -ForegroundColor Cyan
Write-Host ""

cd "C:\Users\Aman1Kumar\OneDrive - Sirius AI\Documents\agentcore-starter"
.venv\Scripts\Activate.ps1

# Deploy
agentcore launch

# Retag image
Write-Host ""
Write-Host "Retagging image as 'agentcore'..." -ForegroundColor Yellow
python retag_image.py

Write-Host ""
Write-Host "Done! Agent deployed and tagged." -ForegroundColor Green














