# Continuum backend (FastAPI API on /api). Build from the repo root:
#   docker build -f backend/Dockerfile .

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/continuum
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt
# Bake the local embedding model into the image so the first start is offline-safe.
ENV FASTEMBED_CACHE_PATH=/srv/continuum/.fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
COPY backend/ ./backend/

# Configuration is read from environment variables (see .env / compose).
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
