# EdgeAdviser — edge-deployed equipment intelligence agent.
#
# Multi-stage build: the webui (Vue/Vite) is compiled with Node, then the
# static dist is copied into a slim Python runtime image together with the
# edge_adviser package. Result: a single image, no node in the runtime layer,
# workspace data lives entirely in a mounted volume.
#
# Build (from repo root):
#   docker build -f edge_adviser/Dockerfile -t edge-adviser:latest .
#
# Multi-arch: this build is amd64+arm64 clean (all pip deps publish aarch64
# wheels; node/python base images are multi-arch). CI builds both platforms
# via buildx+QEMU — see .github/workflows/edge-image.yml. There is deliberately
# no apt package in the runtime stage, so no dpkg arch branching is needed.
#
# Run:
#   docker run -d --name edge-adviser \
#     -p 8000:8000 \
#     -v /var/lib/edge-adviser:/data \
#     -v ./config.json:/app/config.json:ro \
#     edge-adviser:latest

# ---------------------------------------------------------------- stage 1 --
FROM node:20-alpine AS webui-build
WORKDIR /build
COPY edge_adviser/webui/package*.json ./
RUN npm ci --no-audit --no-fund
COPY edge_adviser/webui/ ./
RUN npm run build

# ---------------------------------------------------------------- stage 2 --
FROM python:3.11-slim AS runtime

# Unbuffered logs (journalctl/docker logs), no pycache churn on edge disks.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package first so Docker layer caching keeps deps across code
# edits; nanobot-ai pulls the SDK, the rest are edge_adviser's direct deps.
COPY edge_adviser/pyproject.toml ./edge_adviser/pyproject.toml
COPY edge_adviser/edge_adviser ./edge_adviser/edge_adviser
RUN pip install ./edge_adviser

# Static HMI bundle from stage 1 — served by FastAPI at "/".
COPY --from=webui-build /build/dist ./edge_adviser/webui/dist

# Default workspace INSIDE the container; mount a volume here (or point
# --workspace elsewhere) so snapshots/reports/knowledge survive restarts.
RUN mkdir -p /data
ENV EDGE_ADVISER_WORKSPACE=/data

EXPOSE 8000

# Health check: the honest /api/health endpoint (degraded while the scheduler
# warms up is still a valid HTTP response — this only checks process liveness).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status < 500 else 1)"

# config.json is bind-mounted read-only (see compose / unit docs). The
# threshold-editing PATCH endpoint needs it writable — mount it without :ro
# if you use that feature; threshold edits then persist across restarts.
CMD ["edge-adviser", "serve", "--config", "/app/config.json", \
     "--host", "0.0.0.0", "--port", "8000"]
