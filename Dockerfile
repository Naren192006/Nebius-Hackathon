# TrustRouter gateway (Backend A) with the claim verifier (Backend B) importable.
#
#   docker build -t trustrouter-api .
#   docker run --rm -p 8000:8000 --env-file backend/.env trustrouter-api
#
# Keys are never baked in: they arrive from backend/.env at run time.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/trustrouter/verifier

WORKDIR /app

# Dependencies first, so editing code does not reinstall them.
COPY backend/requirements.txt ./backend/requirements.txt
# tavily-python is the verifier's search client; the gateway alone does not use it.
RUN pip install --no-cache-dir -r backend/requirements.txt "tavily-python>=0.5"

COPY backend/app ./backend/app
COPY backend/schema ./backend/schema
COPY trustrouter/verifier ./trustrouter/verifier
# verifier/verify.py imports `llm` from its sibling backend directory.
COPY trustrouter/backend/llm.py ./trustrouter/backend/llm.py

RUN useradd --create-home --uid 10001 app \
    && mkdir -p backend/data backend/logs \
    && chown -R app:app backend/data backend/logs

USER app
WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
