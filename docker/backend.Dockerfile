# syntax=docker/dockerfile:1
FROM python:3.12.11-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv
WORKDIR /app
ARG RAGAGENT_SOURCE_COMMIT=unknown
ARG RAGAGENT_BUILD_TIME=unknown
COPY . .
# Keep installation and cache cleanup in one layer, including on VFS builders.
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 ca-certificates && \
    uv sync --frozen --no-dev --extra parsing --extra models && \
    uv cache clean && rm -rf /var/lib/apt/lists/*
RUN RAGAGENT_SOURCE_COMMIT="$RAGAGENT_SOURCE_COMMIT" RAGAGENT_BUILD_TIME="$RAGAGENT_BUILD_TIME" \
    python scripts/build_metadata.py --output src/ragagent/build-metadata.json && \
    groupadd --gid 10001 ragagent && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /data ragagent && \
    mkdir -p /data /models /app/config && chown -R 10001:10001 /data /models /app/config
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 DATA_DIR=/data
USER 10001:10001
CMD ["uvicorn", "ragagent.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
