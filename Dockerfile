# Hermes headless server (to-B fork) — see 改造计划.md Step 3.6.
# Single-container image for the headless FastAPI server. Per-customer state
# (config.yaml, .env, users.db, sessions, business.db) lives on a mounted
# volume at /data (= HERMES_HOME), so the image is customer-agnostic.
FROM python:3.12-slim

# git + ripgrep help the agent's search/files tools when they're enabled.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the project (editable) + the server's extra deps.
# bcrypt: password hashing (server/auth.py). passlib is NOT used (1.7.4 is
# incompatible with bcrypt 5.x).
COPY . /app
RUN pip install --no-cache-dir -e . bcrypt

# HERMES_HOME holds per-customer state and is provided by a volume.
ENV HERMES_HOME=/data
VOLUME ["/data"]

EXPOSE 8000
CMD ["python", "-m", "server"]
