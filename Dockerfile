# One image: the gateway API plus the built dashboard, served by the gateway at "/".
# Semantic cache deps (torch, ~1 GB with the CPU build) are left out unless asked for:
#   docker build --build-arg SEMANTIC=true -t llm-gateway .

# Stage 1: build the React dashboard (only the static files are kept)
FROM node:20-alpine AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/index.html dashboard/vite.config.js ./
COPY dashboard/src ./src
RUN npm run build

# Stage 2: the gateway
FROM python:3.12-slim

ARG SEMANTIC=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/requests.db \
    DASHBOARD_DIR=/app/dashboard/dist \
    PORT=8000

WORKDIR /app

# Dependencies first: this layer is reused as long as the requirements files don't change
COPY requirements.txt requirements-semantic.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$SEMANTIC" = "true" ]; then \
      pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-semantic.txt; \
    fi

COPY app ./app
COPY --from=dashboard /dashboard/dist ./dashboard/dist

# Don't run as root; /data holds the SQLite request log (a volume with compose; on Render's free
# plan the disk is wiped on every restart, so the log starts empty)
RUN useradd --create-home gateway && mkdir /data && chown gateway /data
USER gateway

# Hosts like Render pick the port and pass it in $PORT; 8000 otherwise
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health')"
# Shell form so $PORT is filled in; exec makes uvicorn PID 1, so it gets the stop signal and shuts down cleanly
CMD exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
