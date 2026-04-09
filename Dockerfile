FROM public.ecr.aws/docker/library/python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt
FROM public.ecr.aws/docker/library/python:3.12-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY app.py .
COPY auth.py .
COPY environment.py .
COPY env_vdi.py .
COPY env_local.py .
COPY db_config.py .
COPY db_helper.py .
COPY db_helper_vector.py .
COPY langfuse_client.py .
COPY llm_gateway.py .
COPY routers/ ./routers/
COPY services/ ./services/
COPY templates/ ./templates/
COPY prompts/ ./prompts/
COPY migrations/ ./migrations/
COPY database/ ./database/
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
