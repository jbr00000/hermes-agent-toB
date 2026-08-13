# Hermes headless server (to-B fork) — see docs/改造计划.md Step 3.6.
# Single-container image for the headless FastAPI server. Per-customer state
# (config.yaml, .env, users.db, sessions, business.db) lives on a mounted
# volume at /data (= HERMES_HOME), so the image is customer-agnostic.
#
# CN-network friendly: base image from a domestic registry mirror + pip from
# the Aliyun PyPI mirror. For a non-CN build, swap FROM to
# `python:3.12-slim` and drop the `-i ...` pip flag.
# node stage: P3 浏览器兜底需要 agent-browser CLI（Node >=24），从官方
# node 镜像复制二进制，避免在构建期下载 node 发行包（CDN 不稳定）。
FROM docker.m.daocloud.io/library/node:24-slim AS node
FROM docker.m.daocloud.io/library/python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 24 + agent-browser（browser_* 工具的驱动；版本精确锁定，
# 升级 = 显式改版本的交付动作）。npm 走 npmmirror 国内源。
COPY --from=node /usr/local /usr/local
RUN npm install -g agent-browser@0.34.0 --registry=https://registry.npmmirror.com \
    && npm cache clean --force

# Install the project via the Aliyun mirror. Headless server deps are declared
# in pyproject.toml so local installs and Docker builds stay aligned.
COPY . /app
RUN pip install --no-cache-dir \
        -i https://mirrors.aliyun.com/pypi/simple/ \
        --trusted-host mirrors.aliyun.com \
        -e .

# HERMES_HOME holds per-customer state and is provided by a volume.
# Bind 0.0.0.0 so the published port (-p) reaches uvicorn from the host.
ENV HERMES_HOME=/data
ENV HERMES_HEADLESS=1
ENV HERMES_SERVER_HOST=0.0.0.0
ENV TERMINAL_ENV=docker
ENV TERMINAL_DOCKER_NETWORK=false
VOLUME ["/data"]

EXPOSE 8000
CMD ["python", "-m", "server"]
