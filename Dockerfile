# AgencyOS production image. Single process: the in-process task runner and rate limiter assume
# one worker, so scale up (bigger machine) rather than out (more replicas) for now.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production \
    PORT=8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts
RUN mkdir -p data storage \
    && useradd --create-home --uid 1000 agencyos \
    && chown -R agencyos:agencyos /app/data /app/storage
USER agencyos

WORKDIR /app/backend
EXPOSE 8000
VOLUME ["/app/data", "/app/storage"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/v1/health/ready').status==200 else 1)"

# Apply migrations, then serve. --proxy-headers trusts X-Forwarded-For only from FORWARDED_ALLOW_IPS
# (set it to your reverse proxy's address) so rate limits see real client IPs.
CMD ["sh", "-c", "python -m app.cli init-db && exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --proxy-headers --forwarded-allow-ips \"$FORWARDED_ALLOW_IPS\""]
