# API 文档（前端对接）

> Hermes to-B Agent headless 服务的 HTTP 接口说明。
> Base URL：`http://<host>:8000`（本地 dev：`http://127.0.0.1:8000`；容器：映射的端口）。

## 1. 通用约定

| 项 | 值 |
|---|---|
| 协议 | HTTP/1.1 |
| 请求体 | `application/json`（除 `/chat` 的响应是 SSE） |
| 鉴权 | 除 `/health`、`/auth/login`、`/auth/register` 外，所有端点都要 `Authorization: Bearer <JWT>` |
| 时间戳 | Unix 秮（秒，float） |
| 用户标识 | `user_id`（UUID 字符串）——会话/记忆/权限都按它隔离 |

### 错误响应
所有错误统一格式（FastAPI 默认）：
```json
{ "detail": "错误描述" }
```
常见状态码：
| 码 | 含义 |
|---|---|
| 200 | 成功 |
| 401 | 未认证 / token 失效 / 用户名密码错 |
| 403 | 无权限（非 admin、或越权访问他人资源） |
| 404 | 资源不存在（或不属于你——隔离场景下统一返回 404 防探测） |
| 409 | 冲突（如用户名已存在） |

---

## 2. 认证流程

```
1. POST /auth/login {username, password}  →  拿 access_token (JWT, 24h)
2. 后续所有请求 Header 加:  Authorization: Bearer <access_token>
3. token 过期 → 重新 login
```

`user` 对象结构（登录/注册/`/auth/me` 都返回）：
```json
{ "id": "uuid", "username": "alice", "role": "user" | "admin" }
```

---

## 3. 端点详解

### 3.1 鉴权 Auth

#### `POST /auth/login`
登录拿 JWT。**无需鉴权。**
```json
// 请求
{ "username": "alice", "password": "pw" }
// 响应 200
{ "access_token": "eyJhbGciOi...", "token_type": "bearer",
  "user": { "id": "...", "username": "alice", "role": "user" } }
// 响应 401
{ "detail": "invalid username or password" }
```

#### `POST /auth/register`（仅 admin）
创建新用户。**需 admin token。**（首跑 bootstrap 的 admin 才能创建其他人）
```json
// 请求
{ "username": "carol", "password": "carolpw" }   // role 默认 "user"
// 响应 200
{ "access_token": "...", "token_type": "bearer",
  "user": { "id": "...", "username": "carol", "role": "user", "created_at": 1783... } }
// 403 非 admin；409 用户名已存在
```

#### `GET /auth/me`
查当前登录用户。
```json
// 响应 200
{ "user": { "id": "...", "username": "alice", "role": "user" } }
```

---

### 3.2 对话 Chat

#### `POST /chat`（SSE 流式）
核心对话端点。响应是 **`text/event-stream`**（SSE），逐 token 推送。

**请求：**
```json
{
  "message": "统计客户表里有多少行",
  "session_id": "可选，续会话时传上次的 session_id",
  "request_id": "可选，客户端生成的幂等请求 ID",
  "interaction_type": "chat | agent，Web Chat 固定传 chat",
  "mode": "可选：\"plan\" | \"execute\"（默认 execute）"
}
```

**响应（SSE 事件流）：**
```
event: delta
data: {"content": "共"}

event: delta
data: {"content": "5"}

event: delta
data: {"content": " 行"}

event: final
data: {"content": "共 5 行"}

event: done
data: {"session_id": "abc-123", "user_id": "uuid-..."}
```

| 事件 | data 内容 | 说明 |
|---|---|---|
| `session` | `{"session_id":"...","request_id":"...","title":"...","message":{...}}` | 用户消息已落库，会话已进入运行状态 |
| `delta` | `{"content": "<文本片段>"}` | 流式 token（可能多次） |
| `final` | `{"content":"<完整回答>","message":{...},"status":"completed|cancelled"}` | 完整回答已写入 MySQL |
| `error` | `{"message":"<错误信息>","code":"..."}` | agent 执行出错（如沙盒失败） |
| `done` | `{"session_id": "...", "user_id": "..."}` | 流结束，带本次会话 id |

**前端实现要点：**
- 用 `EventSource` 或 `fetch` + `ReadableStream` 消费 SSE。
- 收到 `done` 关闭连接。
- SSE 连接断开不会取消后端任务；需要停止时调用 `POST /chat/{request_id}/cancel`。
- 运行中回答由 Redis 保存聚合快照；切换页签由前端全局运行管理器恢复，刷新页面后由会话详情恢复。
- `session_id` 存下来，下次想续上下文就带在请求里。
- `mode="plan"` 时 agent 只调研出方案不执行（前端可加「批准执行」按钮，批准后用 `mode="execute"` 再发一次）。
- 401（无 token）/403（session 不属于你）。

**curl 示例：**
```bash
curl -N -X POST http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer <JWT>" \
  -H "Content-Type: application/json" \
  -d '{"message":"Reply with exactly: PONG"}'
```

---

### 3.3 会话 Sessions

#### `GET /sessions`
列出**当前用户**的会话（按 user_id 隔离，看不到别人的）。
```json
// 响应 200
{ "sessions": [
  { "id": "abc-123", "model": "deepseek-v4-pro", "started_at": 1783..., "ended_at": null }
]}
```

#### `GET /sessions/{session_id}`
查某会话详情 + 消息历史（必须属于当前用户，否则 404）。
```json
// 响应 200
{ "session": { "id": "...", "source": "headless", "user_id": "...", "started_at": ..., ... },
  "messages": [ {"role":"user","content":"..."}, {"role":"assistant","content":"..."}, ... ],
  "active_run": {
    "id": "request-id",
    "status": "running",
    "started_at": 1783.0,
    "elapsed_ms": 3200,
    "partial_content": "当前已经生成的完整正文快照",
    "sequence": 42,
    "snapshot_updated_at": 1783.2
  } }
// 404 不存在或不属于你
```

任务不在运行时 `active_run` 为 `null`。`partial_content` 是短期运行态，不替代 MySQL 中的最终消息。

#### `POST /sessions/{session_id}/resume`
校验会话可恢复（确认归属）。真正续上下文是 `/chat` 带 `session_id`。
```json
// 响应 200
{ "session_id": "...", "resumable": true }
// 404 不存在或不属于你
```

---

### 3.4 Agent 任务 Tasks

Agent 模式必须通过任务接口进入。服务端持久化任务、运行、计划、权限租约、工具事件和产物；前端不能通过本地状态自行提升权限。

#### `POST /tasks`
新建当前用户的 Agent 任务，同时创建一条 `interaction_type=agent` 的会话。可通过 `source_session_id` 承接一条已结束且属于当前用户的 Chat 会话；服务端会在创建时复制其消息快照，并保存来源会话 ID。
```json
// 请求
{ "title": "检查费用测算文档", "source_session_id": "可选的 Chat 会话 ID" }
// 响应 201
{ "task": { "id": "...", "session_id": "...", "source_session_id": "...", "status": "draft", "permission": { "mode": "read" } } }
```

#### `GET /tasks` / `GET /tasks/{task_id}`
列表接口返回当前用户的任务摘要；详情接口同时返回 `session`、`messages`、`active_run`、`plan`、`permission`、`runs`、`events` 和 `artifacts`。

排队或运行中的 `active_run.partial_content` 来自 Redis 完整回答快照，并包含 `status=queued|running` 与 `phase=plan|execute`。最终消息、计划和工具事件以 MySQL 为准。

#### `POST /tasks/{task_id}/plan`（SSE）
以只读工具生成计划。无论当前权限租约为何，Plan 阶段都强制只读。接口将任务写入 Redis 队列后保持 SSE 订阅；实际执行由独立 Agent Worker 完成，浏览器断开不会终止任务。Redis 入队失败时服务端将 MySQL 运行记录收敛为失败、释放任务占用并返回 `503` 和 `Retry-After`。
```json
{ "message": "读取文档并生成执行计划", "request_id": "可选幂等标识" }
```

除 `/chat` 的 `session/delta/final/error/done` 事件外，还可能收到：

| SSE 事件 | 含义 |
|---|---|
| `task.status` | 任务进入 `queued/planning/running/completed/failed/cancelled` 等状态 |
| `tool.started` | 工具开始；包含 `risk_level` 和参数键名/SQL 指纹摘要，不包含原始参数 |
| `tool.progress` | 工具进度；只包含结构化进度摘要 |
| `tool.completed` | 工具完成；包含状态、耗时和结果类型/规模摘要，不包含原始结果 |
| `plan.required` | 计划已生成，任务进入 `awaiting_approval` |

#### `POST /tasks/{task_id}/approve`
批准最新待审批计划。批准后任务进入 `ready`。

#### `PUT /tasks/{task_id}/permission`
设置当前任务的临时权限租约。
```json
{ "mode": "full", "ttl_seconds": 900 }
```

`mode` 可取 `read`、`controlled`、`full`。当前版本只有 `full` 会在 Execute 阶段启用 Docker 终端；扩展工具尚未接入风险分类，因此 `controlled` 暂不开放未分类写工具。任务执行完成、失败或取消后自动恢复为 `read`。

#### `POST /tasks/{task_id}/execute`（SSE）
只允许执行已批准的计划。请求体可传 `request_id`；不传时由服务端生成。Worker 真正领取任务时会重新校验权限租约，过期租约按 `read` 处理。

#### `GET /tasks/{task_id}/runs/{request_id}/events`（SSE）
重新订阅已经入队或运行中的 Agent 任务。`content_offset` 查询参数表示前端已经持有的回答字符数，服务端只补发其后的快照内容，并按 Redis 有序事件游标重放后续状态/工具事件。Redis/MySQL 读取在线程池执行，不占用 Uvicorn 事件循环。

#### `POST /tasks/{task_id}/cancel` / `POST /tasks/{task_id}/retry`
取消当前排队或运行中的任务，或按最近一次运行阶段重试。取消请求首先持久化到 MySQL，Redis 只承担快速通知，因此 API/Worker 重启后仍然有效。SSE 断开本身不会取消任务。Worker 异常后 Plan 最多按 `HERMES_AGENT_MAX_ATTEMPTS` 投递；超过上限进入失败终态。已经开始的 Execute 不会自动重放，必须显式重试。

#### `DELETE /tasks/{task_id}`
删除非运行中的任务聚合，包括会话、消息、运行、计划、权限租约、工具事件和产物记录；审计记录保留。

---

### 3.5 记忆 Memory

持久记忆（跨会话，按 user_id 隔离）。存进去后，该用户**每次 /chat 都会自动注入**到 agent 系统提示。

#### `GET /memory`
```json
{ "memories": [ { "id": "...", "content": "用户偏好简洁回答", "created_at": 1783... } ] }
```

#### `POST /memory`
```json
// 请求
{ "content": "该客户的核心业务是跨境电商" }
// 响应 200
{ "memory": { "id": "...", "user_id": "...", "content": "...", "created_at": 1783... } }
```

#### `DELETE /memory/{memory_id}`
```json
// 响应 200
{ "deleted": "<memory_id>" }
// 404 不存在或不属于你
```

---

### 3.6 用户管理 Users（仅 admin）

所有 `/users` 端点都需 **admin** token（非 admin 返回 403）。

#### `GET /users`
```json
{ "users": [ { "id":"...", "username":"admin", "role":"admin", "created_at":... },
              { "id":"...", "username":"alice", "role":"user", "created_at":... } ] }
```

#### `POST /users`
```json
// 请求
{ "username": "bob", "password": "bobpw", "role": "user" }   // role 可选，默认 user
// 响应 200
{ "user": { "id":"...", "username":"bob", "role":"user", "created_at":... } }
// 409 用户名已存在
```

#### `DELETE /users/{user_id}`
```json
{ "deleted": "<user_id>" }   // 404 不存在
```

#### `PUT /users/{user_id}/role`
```json
// 请求
{ "role": "admin" }   // "user" | "admin"
// 响应 200
{ "user_id": "...", "role": "admin" }
```

---

### 3.7 功能开关 Features

#### `GET /features`
返回当前功能开关状态（前端据此渲染「是否启用宿主机访问」按钮）。
```json
{ "features": { "host_terminal": false } }
```
> 开关本身在 `config.yaml` 的 `features` 段或 `HERMES_FEATURE_*` env 配置。`POST` 改开关（Inc 2，等你前端按钮设计好再加）。

---

### 3.8 健康 Health

#### `GET /health`（无需鉴权）
```json
{ "status": "ok" }
```
用于负载均衡 / 容器探针。

---

## 4. SSE 流式消费示例（前端 JS）

```javascript
// 用 fetch + ReadableStream 消费 /chat 的 SSE（带 JWT）
async function chat(token, message, sessionId) {
  const resp = await fetch('http://host:8000/chat', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // 按 SSE 事件边界（空行）切分解析
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
      const evt = block.match(/^event: (.+)$/m)?.[1];
      const data = JSON.parse(block.match(/^data: (.+)$/m)?.[1] || '{}');
      if (evt === 'delta')      appendToUi(data.content);   // 流式拼接
      else if (evt === 'final') finalizeUi(data.content);   // 完整回答
      else if (evt === 'error') showError(data.content);
      else if (evt === 'done')  saveSessionId(data.session_id);
    }
  }
}
```

> 浏览器原生 `EventSource` 不支持自定义 Header（无法带 JWT），所以用 `fetch` + `ReadableStream` 手动解析 SSE。

---

## 5. 典型对接流程

1. **登录**：`POST /auth/login` → 存 `access_token`。
2. **对话**：`POST /chat`（带 token + message）→ 流式渲染。存返回的 `session_id`。
3. **续聊**：`POST /chat`（带 token + message + 上次的 `session_id`）→ agent 记得上下文。
4. **历史**：`GET /sessions` 列表 → `GET /sessions/{id}` 看详情。
5. **Agent 规划**：`POST /tasks` → `POST /tasks/{id}/plan`，消费 SSE 并等待 `plan.required`。
6. **Agent 审批执行**：`POST /tasks/{id}/approve` → 按需设置临时权限 → `POST /tasks/{id}/execute`。
7. **Agent 恢复**：全局前端管理器继续持有 SSE；页面刷新后用 `GET /tasks/{id}` 的运行状态、工具事件和 Redis 快照恢复。
8. **记忆**：`POST /memory` 存长期事实 → 之后所有对话自动带上。
9. **管理**（admin）：`/users` 增删用户、改角色。
10. **开关**：`GET /features` 读 host_terminal 状态，渲染按钮。

---

## 6. 附：本地快速验证

```bash
# 登录（首跑 admin/changeme）
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 对话（SSE）
curl -N -X POST http://127.0.0.1:8000/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message":"Reply with exactly: PONG"}'

# 列会话
curl -s http://127.0.0.1:8000/sessions -H "Authorization: Bearer $TOKEN"
```
