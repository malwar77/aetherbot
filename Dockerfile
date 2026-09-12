FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aetherbot/ ./aetherbot/
COPY strategies/ ./strategies/
COPY config/ ./config/

# dry-run by default; the config gates still apply even if overridden
ENV AETHERBOT_MODE=dry_run
CMD ["python", "-m", "aetherbot.main", "start", "--config", "config/config.example.yaml"]
