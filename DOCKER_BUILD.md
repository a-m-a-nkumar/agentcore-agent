# Docker build – backend and frontend

Build and tag the **backend** and **frontend** images as `backend` and `frontend` respectively.

## Backend (agentcore-starter)

From the **agentcore-starter** directory:

```bash
docker build -t backend .
```

Run:

```bash
docker run -p 8000:8000 --env-file .env backend
```

(Or pass env vars with `-e`; ensure AWS and app config are set.)

## Frontend (deluxe-sdlc-frontend)

From the **deluxe-sdlc-frontend** project root (folder that contains `package.json`, `vite.config.ts`, and `Dockerfile`):

```bash
docker build -t frontend .
```

Run (point to backend URL):

```bash
docker run -p 8080:8080 -e BACKEND_URL=http://host.docker.internal:8000 frontend
```

For Docker Compose or same network, use the backend service name, e.g. `-e BACKEND_URL=http://backend:8000`.

## Run both with Docker Compose (optional)

Example `docker-compose.yml` in **agentcore-starter**:

```yaml
version: "3.8"
services:
  backend:
    image: backend
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    # Or set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET_NAME, etc.

  frontend:
    image: frontend
    build: ../deluxe-sdlc-frontend  # or path to your frontend repo
    ports:
      - "8080:8080"
    environment:
      BACKEND_URL: http://backend:8000
    depends_on:
      - backend
```

Then:

```bash
docker compose build
docker compose up
```

- Backend: http://localhost:8000  
- Frontend: http://localhost:8080  

Frontend will proxy API requests to the backend using `BACKEND_URL`.
