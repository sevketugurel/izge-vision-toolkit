FROM node:22.18.0-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
COPY scripts/frontend_licenses.mjs /build/scripts/frontend_licenses.mjs
RUN node /build/scripts/frontend_licenses.mjs

FROM ghcr.io/astral-sh/uv:0.8.4 AS uv
FROM python:3.14.4-slim-bookworm AS backend
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend \
    MEDIA_ROOT=/data/media HF_HOME=/data/hf IZGE_CAMERA_CONFIG_DIRS=/app/config/cameras UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home izge \
    && mkdir -p /data/media /data/hf /app && chown -R izge:izge /data /app
WORKDIR /app
COPY --chown=izge:izge pyproject.toml uv.lock ./
USER izge
# CPU PyTorch (linux index) + transformers for the frozen DINOv3 backbone; weights are fetched at
# runtime into HF_HOME with HF_TOKEN and never baked into the image.
RUN uv sync --frozen --no-dev --extra ml --extra gcp --no-install-project --no-cache
COPY --chown=izge:izge backend ./backend
COPY --chown=izge:izge alembic.ini ./
COPY --chown=izge:izge config ./config
COPY --chown=izge:izge scripts/license_inventory.py ./scripts/license_inventory.py
COPY --from=frontend --chown=izge:izge /build/frontend/dist ./frontend/dist
COPY --from=frontend --chown=izge:izge /build/frontend/licenses ./licenses/node
COPY --chown=izge:izge frontend/package-lock.json ./frontend/package-lock.json
RUN python scripts/license_inventory.py > /app/THIRD_PARTY_BUILD.json
EXPOSE 8000
CMD ["uvicorn", "izge.api:app", "--host", "0.0.0.0", "--port", "8000"]
