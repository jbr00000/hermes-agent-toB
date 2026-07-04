# Hermes headless server (to-B fork) — see 改造计划.md Step 3.6.
# Single-container image for the headless FastAPI server. Per-customer state
# (config.yaml, .env, users.db, sessions, business.db) lives on a mounted
# volume at /data (= HERMES_HOME), so the image is customer-agnostic.
#
# CN-network friendly: base image from a domestic registry mirror + pip from
# the Aliyun PyPI mirror. For a non-CN build, swap FROM to
# `python:3.12-slim` and drop the `-i ...` pip flag.
FROM docker.m.daocloud.io/library/python:3.12-slim

WORKDIR /app

# Install the project (editable) + the server's extra deps via the Aliyun mirror.
# bcrypt: password hashing (server/auth.py). passlib is NOT used (1.7.4 is
# incompatible with bcrypt 5.x).
COPY . /app
RUN pip install --no-cache-dir \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com \
        -e . bcrypt sse-starlette

# HERMES_HOME holds per-customer state and is provided by a volume.
# Bind 0.0.0.0 so the published port (-p) reaches uvicorn from the host.
ENV HERMES_HOME=/data
ENV HERMES_SERVER_HOST=0.0.0.0
VOLUME ["/data"]

EXPOSE 8000
CMD ["python", "-m", "server"]
