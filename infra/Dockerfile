# --- Stage 1: build frontend ---
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime ---
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo libtiff6 curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create an unprivileged user.
RUN useradd -u 10001 -m ndb
RUN mkdir -p /tmp/ndb && chown -R ndb:ndb /tmp/ndb /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# --- NDI-python integration (Phase A) ---------------------------------------
# NDI-python and its git-sourced kin (did, ndr, vhlab-toolbox-python,
# ndi-compress) are installed with `--no-deps` to skip matplotlib (~50-70 MB)
# and opencv-python-headless (~80 MB) which they declare but our backend never
# imports. The runtime deps we DO need are listed in backend/requirements.txt
# above (pandas, networkx, jsonschema, openMINDS, portalocker, h5py, requests).
#
# These pip installs are split into individual layers so a change to one of the
# four git pins re-runs only that single layer (each is small + cached).
RUN pip install --no-deps "vhlab-toolbox-python @ git+https://github.com/VH-Lab/vhlab-toolbox-python.git@main"
RUN pip install --no-deps "did @ git+https://github.com/VH-Lab/DID-python.git@main"
RUN pip install --no-deps "ndr @ git+https://github.com/VH-lab/NDR-python.git@main"
RUN pip install --no-deps "ndi-compress @ git+https://github.com/Waltham-Data-Science/NDI-compress-python.git@main"
RUN pip install --no-deps "ndi @ git+https://github.com/Waltham-Data-Science/NDI-python.git@main"
# Sanity: import every entry point we use in production so a missing
# transitive dep fails the build, not the first request.
RUN python -c "from vlt.file.custom_file_formats import vhsb_read, vhsb_readheader; \
import ndicompress; from ndi.ontology import lookup; print('ndi-python stack importable')"

COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist ./frontend_dist

USER ndb

ENV ONTOLOGY_CACHE_DB_PATH=/tmp/ndb/ontology.db \
    LOG_FORMAT=json \
    WEB_CONCURRENCY=4 \
    PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT}/api/health || exit 1

EXPOSE 8000

# Shell form for PORT expansion; uvicorn worker count via env.
# `--proxy-headers --forwarded-allow-ips '*'` (B8): Railway fronts this
# container with an edge proxy that rewrites the source IP to its own.
# Without these flags `request.client.host` resolves to the Railway edge
# IP for every request, collapsing the per-IP rate-limit envelope onto a
# single subject. The wildcard is intentional: Railway does not publish a
# stable edge-IP list and the container is not directly internet-reachable.
# Tests at backend/tests/unit/test_proxy_headers_config.py pin both this
# CMD and infra/railway.toml's startCommand to keep belt-and-suspenders.
CMD ["sh", "-c", "exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-4} --timeout-graceful-shutdown 30 --proxy-headers --forwarded-allow-ips '*'"]
