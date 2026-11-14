# Gateway image. Semantic cache deps (torch, ~1 GB with the CPU build) are left out unless asked for:
#   docker build --build-arg SEMANTIC=true -t llm-gateway .
FROM python:3.12-slim

ARG SEMANTIC=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/requests.db

WORKDIR /app

# Dependencies first: this layer is reused as long as the requirements files don't change
COPY requirements.txt requirements-semantic.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$SEMANTIC" = "true" ]; then \
      pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-semantic.txt; \
    fi

COPY app ./app

# Don't run as root; /data holds the SQLite request log (mounted as a volume)
RUN useradd --create-home gateway && mkdir /data && chown gateway /data
USER gateway

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
