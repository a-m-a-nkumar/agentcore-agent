# ECR push and ECS – backend & frontend

## Push both images to ECR (tags: backend, frontend)

From **agentcore-starter** (with both images built locally as `backend:latest` and `frontend:latest`):

```powershell
.\scripts\PUSH_ECR.ps1
```

Or run manually:

```powershell
$Region = "us-east-1"
$EcrUri = "448049797912.dkr.ecr.us-east-1.amazonaws.com"
$Repo  = "deluxe-sdlc"

# Verify AWS
aws sts get-caller-identity --region $Region

# Login to ECR
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $EcrUri

# Backend (container port 8000)
docker tag backend:latest ${EcrUri}/${Repo}:backend
docker push ${EcrUri}/${Repo}:backend

# Frontend (container port 8080)
docker tag frontend:latest ${EcrUri}/${Repo}:frontend
docker push ${EcrUri}/${Repo}:frontend
```

## Build images first (if needed)

- **Backend:** From `agentcore-starter`: `docker build -t backend .`
- **Frontend:** From `deluxe-sdlc-frontend` (folder with Dockerfile): `docker build -t frontend .`

## Production ports

| Service  | Container port | Use in ECS |
|----------|----------------|------------|
| Backend  | 8000           | Map in task definition (e.g. port 8000 → ALB) |
| Frontend | 8080           | Map in task definition (e.g. port 8080 → ALB) |

- Backend listens on **8000** inside the container (see `Dockerfile` CMD).
- Frontend (nginx) listens on **8080** inside the container (see frontend `Dockerfile` EXPOSE 8080).

In ECS:

- Backend task: container port **8000**, host port from dynamic mapping or 8000.
- Frontend task: set `BACKEND_URL` to the backend’s internal URL (e.g. `http://backend-service:8000` or the backend task’s discovery address). Frontend container port **8080**.

## ECR image URIs after push

- Backend:  `448049797912.dkr.ecr.us-east-1.amazonaws.com/deluxe-sdlc:backend`
- Frontend: `448049797912.dkr.ecr.us-east-1.amazonaws.com/deluxe-sdlc:frontend`

Use these in your ECS task definitions as the container image for each service.
