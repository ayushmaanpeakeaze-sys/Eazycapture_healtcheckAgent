FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/app

# Install runtime deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# Migration tooling — needed so the container can run `alembic upgrade head`.
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# Non-root for production. Owns /srv/app so reloads work in dev too.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /srv/app
USER appuser

EXPOSE 8001

# Bind the port the platform assigns ($PORT, e.g. on Render); fall back to 8001
# for local Docker. Shell form so ${PORT:-8001} expands at runtime.
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8001'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=2).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
