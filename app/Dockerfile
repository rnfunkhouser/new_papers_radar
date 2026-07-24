# Runtime image for the Daily Papers Radar. Code + mutable state are BIND-MOUNTED
# from the VM host at /app (see docker-compose.yml) — the image only provides Python,
# cron, a CA bundle, and pypdf (for full-text PDF extraction in write_briefing.py).
# All the heavy ML (embeddings, chat generation) runs on your remote LLM API, not
# here, so this stays tiny.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        cron tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pypdf

WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8001
CMD ["/entrypoint.sh"]
