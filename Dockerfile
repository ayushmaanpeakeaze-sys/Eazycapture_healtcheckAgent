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

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
