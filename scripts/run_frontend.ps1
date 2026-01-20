# Startup script for AgentCore Frontend
Write-Host "Starting AgentCore Frontend..."
.venv\Scripts\activate
pip install -r requirements_frontend.txt
uvicorn app:app --reload --port 8000
