# Sarjy backend — the FastAPI voice loop, containerised for Cloud Run (Phase 3).
#
# The interpreter matches the one the project is developed on (Python 3.14, D-019), so a
# wheel that resolves locally resolves here too. No secret is ever baked in: config.py reads
# backend/.env when the file exists and falls through to the real environment when it does
# not (D-031) — which is exactly the shape Cloud Run provides.

FROM python:3.14-slim

# Unbuffered so a log line reaches Cloud Logging when it is written, not when a buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first: this layer rebuilds only when the pins change, not on every code edit.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Containers run as root unless told otherwise, and it costs one line not to.
RUN useradd --create-home --uid 1000 sarjy
USER sarjy

# Cloud Run injects PORT and ignores anything hardcoded; the default keeps a plain
# `docker run -p 8080:8080` working locally without passing it.
ENV PORT=8080
EXPOSE 8080

# Shell form so ${PORT} expands; `exec` so uvicorn is PID 1 and receives Cloud Run's SIGTERM
# directly, which is what lets an instance shut down gracefully instead of being killed.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'
