# Hermes to-B Agent ☤

> 基于 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) v0.18.0 改造的**面向 to-B 场景的私有智能体框架**。
> 安全/隐私优先、单租户每客户独立部署、Docker 沙盒代码执行、本地持久记忆、多用户隔离。

## 项目定位

把开源个人助手 Hermes 改造成**企业级、可交付客户独立部署的 agent 平台**。一个 headless FastAPI 服务作为前端 BFF，驱动 `AIAgent` 核心，提供：对话（流式）、查客户数据库（只读）、沙盒跑生成代码、规划模式、持久记忆、多用户管理——全部按用户隔离，容器化部署。

详见 [`改造计划.md`](docs/改造计划.md)（需求 + 9 个锁定架构决策 + 改造记录）。

## 核心特性

| 能力 | 说明 |
|---|---|
| **headless 服务** | FastAPI，前端 BFF：JWT 鉴权、SSE 流式对话、按用户隔离会话/记忆 |
| **沙盒代码执行** | agent 生成的代码只在 **Docker 容器**里跑，物理上够不到宿主机（决策 5） |
| **数据库只读查询** | `db_query` 工具连客户业务库，**只读在 DB GRANT 层强制** |
| **持久记忆 + 跨会话** | 本地 SQLite，按 user_id 隔离（决策 7，无云记忆） |
| **规划模式** | agent 先调研出方案、用户批准后再执行 |
| **多用户** | 单租户共享实例，admin 管理用户，人与人隔离 |
| **容器化** | Dockerfile + docker-compose，每客户状态走 volume |
| **保留 4 个模型 provider** | 自托管（OpenAI 兼容）、智谱 GLM、阿里百炼、DeepSeek |

## 架构概览

```
前端 ──HTTP/SSE──> server/ (FastAPI, JWT 鉴权)
                      │
                      ├── AIAgent (run_agent.py)
                      │     ├── db_query ──> 客户业务库（只读）
                      │     ├── terminal/execute_code ──> Docker 沙盒
                      │     └── plan mode / 记忆注入
                      ├── SessionDB (state.db) ── 按用户的会话历史
                      ├── memory.db ── 按用户的持久记忆
                      └── users.db ── 用户 + bcrypt + 角色
```

详细模块说明见 [`项目架构详解.md`](docs/项目架构详解.md)；前端对接的 API 见 [`API文档.md`](docs/API文档.md)。

## 快速开始

### 1. 环境
- Python 3.12（`requires-python >=3.11,<3.14`）
- Docker（沙盒代码执行需要；Docker Desktop 即可）
- 推荐：conda env `hermes`（`D:\Anaconda\envs\hermes`）

```bash
conda create -n hermes python=3.12 -y
conda activate hermes
pip install -e ".[dev]"
```

### 2. 配置 HERMES_HOME（本地 dev 状态目录）
```bash
mkdir -p .hermes-dev
cp .env.example .hermes-dev/.env       # 填 DEEPSEEK_API_KEY 等
cp cli-config.yaml.example .hermes-dev/config.yaml   # 调 model 等
```

### 3. 跑 headless 服务
```bash
HERMES_HOME=.hermes-dev python -m server     # 启动在 :8000
```
首跑会自动 bootstrap admin 用户；生产/交付环境必须先设置 `HERMES_ADMIN_PASSWORD`。本地临时开发如需使用 `admin/changeme`，显式设置 `HERMES_ALLOW_DEFAULT_ADMIN=1`。

### 4. 容器化
```bash
docker compose up       # 镜像 hermes-agent-tob:dev，挂载 .hermes-dev 为 /data
```

## 配置

| 文件（都在 `$HERMES_HOME`） | 内容 |
|---|---|
| `deployment.yaml` | **交付声明**：客户标识、模型、DB 环境变量名、沙盒策略、MCP server、功能开关 |
| `.env` | **密钥**：API key、DB URL、沙盒镜像、功能开关 |
| `config.yaml` | **行为**：model、reasoning_effort、features、terminal |
| `state.db` | 会话历史（SQLite + FTS5） |
| `memory.db` | 持久记忆（SQLite） |
| `users.db` | 用户 |
| `audit.db` | 审计事件：会话轮次 + 工具调用摘要 |
| `jwt.key` | JWT 签名密钥 |

功能开关（默认全关，前端可开）：
```yaml
features:
  computer_use: false    # 桌面控制（需 cua-driver）
  host_terminal: false   # 宿主机 shell（关=只 Docker 沙盒）
```

`deployment.yaml.example` 是客户交付配置模板；默认不存在时系统使用安全默认值（Docker 沙盒、禁止网络出口、关闭 host terminal / computer use）。

## 安全模型

- **沙盒默认**：agent 跑代码只在 Docker，不开 `host_terminal` 就够不到宿主。
- **DB 只读在 GRANT 层**：db_query + 沙盒共用客户的只读凭证，写操作在数据库被拒。
- **工具级审计**：headless 会话中的工具调用会记录工具名、参数键、SQL 指纹、耗时和状态，不复制完整 SQL 或 secret 值。
- **按用户隔离**：会话/记忆/审计按 user_id 隔离（10–50 人共享一个部署，不串）。
- **无云记忆/无遥测**：记忆本地 SQLite，无外发分析。
- 详见 [`docs/security/network-egress-isolation.md`](docs/security/network-egress-isolation.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [`改造计划.md`](docs/改造计划.md) | 需求 + 9 个架构决策 + 改造记录 |
| [`项目架构详解.md`](docs/项目架构详解.md) | 每个模块的作用 + 数据流 |
| [`API文档.md`](docs/API文档.md) | 前端对接的 API 接口（含 SSE 格式） |
| [`CLAUDE.md`](CLAUDE.md) | 给 AI 助手的项目导览 |
| [`AGENTS.md`](AGENTS.md) | 开发指南 + 硬性约束 + 编码规范 |
| [`UPSTREAM.md`](UPSTREAM.md) | 硬分叉基线记录 |

## 许可证

MIT（继承自上游 Hermes）。原 [`LICENSE`](LICENSE) + 版权声明保留。
