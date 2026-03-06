FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim
WORKDIR /app

# All environment variables in one layer
ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-east-1 \
    AWS_DEFAULT_REGION=us-east-1 \
    BEDROCK_AGENTCORE_MEMORY_ID=sdlc_dev_agentcore_memory-VF74Yf64ZB \
    BEDROCK_AGENTCORE_MEMORY_NAME=sdlc_dev_agentcore_memory \
    AGENTCORE_MEMORY_ID=sdlc_dev_agentcore_memory-VF74Yf64ZB \
    AGENTCORE_ACTOR_ID=brd-session \
    AGENTCORE_GATEWAY_ID=sdlc-dev-agentcore-gateway-z8tkb0pspy \
    S3_BUCKET_NAME=sdlc-orch-dev-us-east-1-app-data \
    LAMBDA_BRD_GENERATOR=arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-generator \
    LAMBDA_BRD_RETRIEVER=arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-retriever \
    LAMBDA_BRD_CHAT=arn:aws:lambda:us-east-1:590184044598:function:sdlc-dev-brd-chat \
    DLXAI_GATEWAY_URL=https://dlxai-dev.deluxe.com/proxy \
    GATEWAY_MODEL=Claude-4.5-Sonnet



# Use root requirements.txt (note: .bedrock_agentcore/ is excluded by .dockerignore)
COPY requirements.txt requirements.txt
RUN uv pip install -r requirements.txt




RUN uv pip install aws-opentelemetry-distro==0.12.2


# Signal that this is running in Docker for host binding logic
ENV DOCKER_CONTAINER=1

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

# Copy entire project (respecting .dockerignore)
COPY . .

# Use the full module path

CMD ["opentelemetry-instrument", "python", "-m", "my_agent"]
