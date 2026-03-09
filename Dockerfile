# AgentCore Backend - Dockerfile
# Build: docker build -t backend .
# Run:   docker run -p 8000:8000 --env-file .env backend

FROM python:3.12-slim

WORKDIR /app

# Install system deps if needed (e.g. for psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency file first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY auth.py .
COPY db_config.py .
COPY db_helper.py .
COPY db_helper_vector.py .
COPY langfuse_client.py .
COPY routers/ ./routers/
COPY services/ ./services/
COPY templates/ ./templates/
COPY prompts/ ./prompts/
COPY migrations/ ./migrations/
COPY database/ ./database/

# Optional: copy Lambda modules if any server-side code references them (e.g. for imports)
# COPY lambda_*.py ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Production: run uvicorn without reload
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
