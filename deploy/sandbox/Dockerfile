# to-B Agent 沙箱镜像：在默认运行时镜像（nikolaik/python-nodejs）上预装
# 交付物常用 Python 库。
#
# 为什么必须烘进镜像：沙箱出网默认拒绝（to-B fail-closed，无外网 egress），
# 容器内 pip install 会失败，所有 agent 需要的依赖只能在构建期装入。
#
# 构建：docker build -t hermes-sandbox:dev deploy/sandbox
# 启用：$HERMES_HOME/.env 设 TERMINAL_DOCKER_IMAGE=hermes-sandbox:dev
#
# 依赖精确钉版本（与 pyproject.toml 同一供应链接纪律）；加库时在此追加。
FROM nikolaik/python-nodejs:python3.11-nodejs20

RUN pip install --no-cache-dir \
    openpyxl==3.1.5
