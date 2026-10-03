# syntax=docker/dockerfile:1
FROM python:3.12.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
# Keep installation and cache cleanup in one layer, including on VFS builders.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 ca-certificates && \
    uv sync --frozen --no-dev --extra parsing --extra models && \
    uv cache clean && rm -rf /var/lib/apt/lists/*
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DATA_DIR=/data
CMD ["uvicorn", "ragagent.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
