FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_DATA_DIR=/app/data

WORKDIR /app

# Install dependencies first so code changes don't invalidate this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY scheduler.py ./

# Run as an unprivileged user; only the data directory must be writable.
RUN useradd --create-home --uid 1000 agent \
    && mkdir -p /app/data \
    && chown -R agent:agent /app/data
USER agent

VOLUME ["/app/data"]

# The scheduler handles SIGTERM, so `docker stop` shuts it down cleanly.
CMD ["python", "scheduler.py"]
