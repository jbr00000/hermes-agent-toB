# Chat 前后端联调指南

## 1. 范围

本轮只接入 Chat。Agent、知识库、附件解析和对象存储仍保留前端入口，但未接入真实后端。

数据边界：

- `HERMES_DATABASE_URL`：Hermes 自身 MySQL，保存账号、登录会话、对话、消息、模型运行、记忆和审计。
- `HERMES_REDIS_URL`：Hermes 运行态 Redis，保存锁、取消通知、请求状态、回答快照和短期 SSE 事件；取消请求的权威记录在 MySQL。
- `HERMES_DB_URL`：客户业务数据库，只供 Chat 的 `db_query` 工具读取，禁止与 Hermes 自身 MySQL 共用账号。

## 2. 启动基础服务

在仓库根目录准备 `.env.compose`，字段参考 `compose.env.example`，然后执行：

```powershell
docker compose --env-file .env.compose up -d mysql redis
docker compose --env-file .env.compose ps
```

默认只监听本机：

- MySQL：`127.0.0.1:13306`
- Redis：`127.0.0.1:16379`

## 3. 配置应用

在 `.hermes-dev/.env` 中设置真实密钥，不要提交该文件：

```dotenv
HERMES_DATABASE_URL=mysql+pymysql://hermes_app:<password>@127.0.0.1:13306/hermes_tob?charset=utf8mb4
HERMES_REDIS_URL=redis://:<password>@127.0.0.1:16379/0
HERMES_TENANT_ID=local-dev
HERMES_ADMIN_USERNAME=admin
HERMES_ADMIN_PASSWORD=<admin-password>
HERMES_SERVER_HOST=127.0.0.1
HERMES_SERVER_PORT=8000
HERMES_AGENT_MAX_ATTEMPTS=3
DEEPSEEK_API_KEY=<api-key>
```

首次启动或模型表结构变化后，先执行迁移：

```powershell
$env:HERMES_HOME = (Resolve-Path .hermes-dev).Path
D:\Anaconda\envs\hermes\Scripts\alembic.exe upgrade head
```

生产和正常联调必须配置 Redis 并启动独立 `server.worker`。仅单进程开发测试可显式设置 `HERMES_ALLOW_EMBEDDED_AGENT_WORKER=1`；默认值为 `0`，避免部署时静默退化为进程内队列。

## 4. 启动后端与前端

后端必须使用项目指定的 Conda 环境：

```powershell
$env:HERMES_HOME = (Resolve-Path .hermes-dev).Path
D:\Anaconda\envs\hermes\python.exe -m server
```

前端在另一个终端启动：

```powershell
cd apps/web
npm install
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 会把 `/api/*` 代理到 `http://127.0.0.1:8000/*`。

## 5. 验证

基础健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

预期 `database` 和 `redis` 都为 `ok`。浏览器验证顺序：登录、切换 Chat、新建问答、观察流式回复、刷新页面并确认历史会话恢复。

自动化回归：

```powershell
D:\Anaconda\envs\hermes\python.exe -m pytest -q tests/server
cd apps/web
npm run build
```

## 6. 当前接口

| 能力 | 接口 |
| --- | --- |
| 登录/恢复/刷新/退出 | `POST /auth/login`、`GET /auth/session`、`POST /auth/refresh`、`POST /auth/logout` |
| 会话列表与新建 | `GET /sessions?interaction_type=chat`、`POST /sessions` |
| 会话详情与更新 | `GET /sessions/{id}`、`PATCH /sessions/{id}` |
| Chat 流式回复 | `POST /chat`，SSE 事件为 `session/delta/final/error/done` |
| 停止生成 | `POST /chat/{request_id}/cancel` |
| 运行状态 | `GET /health`、`GET /features` |

Chat 请求必须显式传递 `interaction_type=chat`。当前服务端仅开放强制只读的 `db` 工具集；旧 `session_search` 尚未迁移到租户化 MySQL，因此不会暴露给 Chat。服务端不信任前端传入的权限状态。
