# API 文档（前端对接）

> Hermes to-B Agent headless 服务的 HTTP 接口说明。
> Base URL：`http://<host>:8000`（本地 dev：`http://127.0.0.1:8000`；容器：映射的端口）。

## 1. 通用约定

| 项 | 值 |
|---|---|
| 协议 | HTTP/1.1 |
| 请求体 | `application/json`（除 `/chat` 的响应是 SSE） |
| 鉴权 | 除 `/health`、`/auth/login`、`/auth/refresh`、`/auth/session`、`/auth/logout` 外，所有端点都要 `Authorization: Bearer <JWT>`（refresh/session/logout 走 `hermes_refresh_token` HttpOnly cookie） |
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
| 401 | 未认证 / token 失效 / 用户名密码错 / 账号被禁用 |
| 403 | 无权限（角色不足、功能开关关闭、或越权访问他人资源）；`detail == "must_change_password"` 表示账号被标记强制改密，改密前仅 `/auth/change-password`、`/auth/me`、`/auth/session`、`/auth/logout` 可达 |
| 404 | 资源不存在（或不属于你——隔离场景下统一返回 404 防探测） |
| 409 | 冲突（如用户名已存在） |
| 429 | 登录失败次数过多，账号临时锁定 |

---

## 2. 认证流程

```
1. POST /auth/login {username, password}  →  拿 access_token (JWT, 24h)
2. 后续所有请求 Header 加:  Authorization: Bearer <access_token>
3. token 过期 → 重新 login
```

`user` 对象结构（登录/刷新/`/auth/me`/用户管理都返回）：
```json
{
  "id": "uuid",
  "username": "alice",
  "role": "superadmin" | "admin" | "user",
  "status": "active" | "disabled",
  "features": { "agent": true, "chat": true, "knowledge": true, "memory": true },
  "must_change_password": false,
  "created_at": 1783...
}
```
- `features`：per-user 功能开关；某一项为 `false` 时对应端点对该用户（含 admin）返回 403。
- `must_change_password: true`：新建/被重置密码的账号，须先走 `POST /auth/change-password`；期间其他端点一律 403（见上表）。前端拿到该 403 后重拉 `/auth/me` 即可切到强制改密页。

---

## 3. 端点详解

### 3.1 鉴权 Auth

#### `POST /auth/login`
登录拿 JWT。**无需鉴权。** 同时种 `hermes_refresh_token` HttpOnly cookie。
```json
// 请求
{ "username": "alice", "password": "pw", "remember": false }
// remember=true → refresh cookie 带 Max-Age（默认 30 天）；false → 会话 cookie，关浏览器即失效
// 响应 200
{ "access_token": "eyJhbGciOi...", "token_type": "bearer",
  "user": { "id": "...", "username": "alice", "role": "user", "status": "active",
            "features": {...}, "must_change_password": false, "created_at": 1783... } }
// 响应 401
{ "detail": "invalid username or password" }
// 响应 429（连续失败触发锁定，默认 15 分钟）
{ "detail": "账号已锁定，请 N 分钟后重试" }
```

#### `POST /auth/refresh` / `GET /auth/session` / `POST /auth/logout`
浏览器会话续期/恢复/登出，均走 refresh cookie（**无需 Bearer**）：
- `POST /auth/refresh` → 200 返回新 `access_token` + `user`；401 表示 refresh 会话失效（需重新登录）。
- `GET /auth/session` → 同上但匿名访问不算错误：`{ "authenticated": false }` 或 `{ "authenticated": true, "access_token": "...", "user": {...} }`。
- `POST /auth/logout` → 204，吊销 refresh 会话并清 cookie。

#### `POST /auth/change-password`
用户自助改密。**需 Bearer**（`must_change_password` 账号在白名单内，可用）。
```json
// 请求
{ "old_password": "initial-pass", "new_password": "new-pass-123" }   // 新密码至少 8 位
// 响应 200（镜像 login：新 access_token + user，并种新 refresh cookie；旧 refresh 会话全部吊销）
{ "access_token": "...", "token_type": "bearer", "user": { ..., "must_change_password": false } }
// 400 旧密码不正确；422 新密码不合规
```

#### `GET /auth/me`
查当前登录用户。
```json
// 响应 200
{ "user": { "id": "...", "username": "alice", "role": "user", "status": "active",
            "features": {...}, "must_change_password": false, "created_at": 1783... } }
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
{ "message": "读取文档并生成执行计划", "request_id": "可选幂等标识",
  "knowledge": { "enabled": true, "kb_id": "可选，限定指定知识库" } }
```

`knowledge` 为运行级知识库选择（`plan`/`execute`/`retry` 均支持）：整个字段缺省 = 保持默认（挂 `knowledge` 工具集、全库可检索）；`enabled=false` 本轮不挂 `knowledge_search`（此时不能再传 `kb_id`，否则 400）；`kb_id` 把检索限定到指定库。显式 `enabled=true` 要求部署已启用知识库（否则 409）且用户有 knowledge feature（否则 403）；`kb_id` 不存在返回 404。校验全部前置，被拒请求不会入队。

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
只允许执行已批准的计划。请求体可传 `request_id`（不传时由服务端生成）、`message`（追加指令）与 `knowledge`（运行级知识库选择，语义同 `/plan`）。Worker 真正领取任务时会重新校验权限租约，过期租约按 `read` 处理。

#### `GET /tasks/{task_id}/runs/{request_id}/events`（SSE）
重新订阅已经入队或运行中的 Agent 任务。`content_offset` 查询参数表示前端已经持有的回答字符数，服务端只补发其后的快照内容，并按 Redis 有序事件游标重放后续状态/工具事件。Redis/MySQL 读取在线程池执行，不占用 Uvicorn 事件循环。

#### `POST /tasks/{task_id}/cancel` / `POST /tasks/{task_id}/retry`
取消当前排队或运行中的任务，或按最近一次运行阶段重试。取消请求首先持久化到 MySQL，Redis 只承担快速通知，因此 API/Worker 重启后仍然有效。SSE 断开本身不会取消任务。Worker 异常后 Plan 最多按 `HERMES_AGENT_MAX_ATTEMPTS` 投递；超过上限进入失败终态。已经开始的 Execute 不会自动重放，必须显式重试。

#### `DELETE /tasks/{task_id}`
删除非运行中的任务聚合，包括会话、消息、运行、计划、权限租约、工具事件和产物记录；审计记录保留。

#### `GET /tasks/{task_id}/tool-approvals?status=`
controlled 档（逐条审批）的待决命令列表；前端重连时用它重建 pending 审批的 ground truth。`status` 可选 `pending | approved | denied | expired`。
```json
{ "approvals": [ { "id": "...", "task_id": "...", "run_request_id": "...",
                   "tool_name": "terminal", "args_summary": {...},
                   "status": "pending", "created_at": 1783... } ] }
```

#### `POST /tasks/{task_id}/tool-approvals/{approval_id}`
批准/拒绝一条待执行命令。不写 SSE——审批门控的下一拍轮询会发现决定，由 executor 统一发 `tool.approval_resolved` 事件。
```json
// 请求
{ "decision": "approve" }   // "approve" | "deny" | "allow_all"（本次运行后续命令不再询问）
// 响应 200
{ "approval": { "id": "...", "status": "approved", ... } }
// 404 approval 不存在或不属于你；409 已被决定（含并发竞态）
```

---

### 3.5 附件 Uploads

临时上传文件（chat 会话 / agent 任务共用）：用户随消息传文件做问答上下文，服务端**只解析全文、不分块**，注入下一轮模型消息。`owner_type=session` 挂在 chat 会话，`owner_type=task` 挂在 agent 任务；随 owner 删除。每个 owner 累计最多 **5 个**、单文件 ≤ **20MB**，扩展名白名单与知识库解析器一致。

注入规则（服务端行为，前端只需展示状态）：
- chat / plan 模式：ready 附件全文拼进**下一条 user 消息**发给模型并落库；已注入过的文件不重复注入（历史重放 + 前缀缓存命中）。
- agent execute 阶段：不注文本，原件暂存进沙箱任务工作区 `uploads/` 供模型用终端读取；交付物列表不含 `uploads/`。
- token 预算 = 模型输入上限 − 历史 − system 粗估(4k) − 输出余量(8k)；超预算**不阻断发送**，从最新上传的文件开始截断/跳过，并在消息 `metadata.attachments` 里逐文件标注 `full | truncated | skipped`。消息 `metadata.display_content` 是用户原文，气泡展示用它而不是注入全文后的 content。

#### `POST /uploads`（multipart）
```
表单字段：owner_type = "session" | "task"；owner_id；files = 1~N 个文件
```
```json
// 响应 201（落盘即返回，解析在后台线程进行，多半仍是 parsing）
{ "files": [ { "id": "...", "owner_type": "session", "owner_id": "...",
               "file_name": "案情.txt", "file_ext": ".txt", "size_bytes": 1024,
               "parse_status": "parsing", "parse_error": null,
               "token_count": 0, "created_at": 1783... } ] }
// 400 超 5 个 / 不支持的格式 / 空文件；413 单文件超 20MB；404 owner 不存在或不属于你
```

#### `GET /uploads?owner_type=&owner_id=`
列出某 owner 的全部附件 + token 预算用量。有 parsing 中的文件时前端轮询（~1.5s）。
```json
{ "files": [ { "id": "...", "parse_status": "ready", "token_count": 5320, ... } ],
  "budget": { "max_input_tokens": 128000, "budget_tokens": 116000,
              "file_tokens": 150000, "over_budget": true } }
// over_budget=true 时前端显示黄色警告条（不阻断发送）
```

#### `DELETE /uploads/{file_id}`
删除附件（原件 + 解析产物 + 沙箱暂存副本）。
```json
// 响应 200
{ "deleted": "<file_id>" }
// 404 不存在或不属于你
```

---

### 3.6 记忆 Memory

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

### 3.7 用户管理 Users（仅 superadmin）

所有 `/users` 端点都需 **superadmin** token（admin/user 返回 403）。防锁死规则：不能删除/禁用/降级自己，也不能删除/禁用/降级最后一个 active superadmin（400/409）。所有变更写审计（`user_admin`）。

#### `GET /users`
```json
{ "users": [ { "id":"...", "username":"admin", "role":"superadmin", "status":"active",
               "features": {...}, "must_change_password": false, "created_at":... } ] }
```

#### `POST /users`
```json
// 请求
{ "username": "bob", "password": "bobpw123",          // 至少 8 位
  "role": "user",                                      // 可选："superadmin"|"admin"|"user"，默认 user
  "features": { "knowledge": false } }                 // 可选：缺省的键默认启用
// 响应 200 —— 新用户 must_change_password=true，首次登录后须先改密
{ "user": { "id":"...", "username":"bob", "role":"user", "status":"active",
            "features": {...}, "must_change_password": true, "created_at":... } }
// 400 用户名/密码/角色不合规；409 用户名已存在
```

#### `DELETE /users/{user_id}`
```json
{ "deleted": "<user_id>" }   // 404 不存在
```

#### `PUT /users/{user_id}/role`
```json
// 请求
{ "role": "admin" }   // "superadmin" | "admin" | "user"
// 响应 200
{ "user_id": "...", "role": "admin" }
// 400 自我降级/角色非法；409 目标是最后一个 active superadmin
```

#### `PUT /users/{user_id}/password`
管理员重置密码。目标用户被重新标记 `must_change_password=true`，旧密码与全部 refresh 会话立即失效。
```json
// 请求
{ "password": "new-pass-123" }   // 至少 8 位；不落审计
// 响应 200
{ "user_id": "...", "password_reset": true }
```

#### `PUT /users/{user_id}/status`
```json
// 请求
{ "status": "disabled" }   // "active" | "disabled"
// 响应 200 —— 禁用后该用户已签发的 access token 立即失效（每请求重读用户行）
{ "user_id": "...", "status": "disabled" }
```

#### `PUT /users/{user_id}/features`
Patch 语义：只传要改的键，缺省键保持原值。
```json
// 请求
{ "memory": false }        // 键：agent | chat | knowledge | memory
// 响应 200 —— 下一个请求即生效（不烘焙在 JWT 里）
{ "user_id": "...", "features": { "agent": true, "chat": true, "knowledge": true, "memory": false } }
```

---

### 3.8 功能开关 Features

#### `GET /features`
返回当前功能开关状态（前端据此渲染「是否启用宿主机访问」按钮）与当前角色的数据权限。
```json
{ "features": { "host_terminal": false },
  "data_permissions": { "enabled": true, "allowed_tables": ["orders", "customers"] } }
```
> `data_permissions.enabled=false` / `allowed_tables=null` 表示该角色不限制表访问；列出（可为空数组）即白名单（见 `deployment.yaml` 的 `data_permissions` 段）。
> 开关本身在 `config.yaml` 的 `features` 段或 `HERMES_FEATURE_*` env 配置。`POST` 改开关（Inc 2，等你前端按钮设计好再加）。

---

### 3.9 健康 Health

#### `GET /health`（无需鉴权）
```json
{ "status": "ok" }
```
用于负载均衡 / 容器探针。

---

### 3.10 知识库 Knowledge

企业知识库，按「知识库（base）→ 文档 → 分块」三级组织，使用分三步：**① 新建知识库 → ② 往库里上传文档（只落盘，不解析）→ ③ 勾选文档批量触发解析**（构建链路见 [`知识库构建方案.md`](知识库构建方案.md)）。`deployment.yaml` 里 `knowledge.enabled=false`（缺省）时**整组路由 404**，前端据此显示"当前部署未启用知识库"。此外**全部端点（含 admin 的变更端点）都要求用户的 `features.knowledge=true`**，被关掉的用户访问一律 403。

所有**写操作（建库/改库/删库/上传/解析/删除/重试）仅 admin**；所有读操作普通用户可用。

文档状态机：`uploaded → pending → parsing → syncing → ready / failed`。`uploaded` 是上传后的静止态（待解析），只有显式调用 parse/retry 才会置为 `pending` 并入队；构建是异步的——202 立即返回，前端轮询 `GET /knowledge/documents`（构建中有文档时 3s 一次）直到全部终态。存量部署升级时，旧文档自动回填进按租户创建的「默认知识库」。

#### `GET /knowledge/bases`
知识库列表。**需登录（普通用户可读）。**
```json
// 响应 200
{ "bases": [
    { "id": "kb-uuid", "name": "运维规范", "description": "…",
      "doc_count": 12, "chunk_count": 860, "created_at": 1783…, "updated_at": 1783… }
  ] }
```

#### `POST /knowledge/bases`（仅 admin）
新建知识库。Body：`{ "name": "运维规范", "description": "可选" }`。
```json
// 响应 201
{ "base": { "id": "kb-uuid", "name": "运维规范", … } }
// 409 同名知识库已存在（同一租户内 name 唯一）
```

#### `PATCH /knowledge/bases/{kb_id}`（仅 admin）
改名/描述。Body：`{ "name": "可选", "description": "可选" }`（至少一个字段）。
```json
// 响应 200 → { "base": { … } }
// 400 没有需要更新的字段；404 不存在；409 名称冲突
```

#### `DELETE /knowledge/bases/{kb_id}`（仅 admin）
删除知识库并**级联删除**库内全部文档：清 ES/Milvus 投影 → 删本地文件 → 删 MySQL 行。
```json
// 响应 200
{ "deleted": "kb-uuid", "documents": 12 }
```

#### `GET /knowledge/documents?status=&kb_id=&limit=&offset=`
文档列表 + 汇总。**需登录（普通用户可读）。** `status` 可选过滤（`uploaded/pending/parsing/syncing/ready/failed`）；`kb_id` 可选按库过滤。聊天页的知识引用列表用 `status=ready` 取全库可检索文档。
```json
// 响应 200
{ "documents": [
    { "id": "doc-uuid", "kb_id": "kb-uuid", "title": "费用测算办法", "file_name": "费用测算办法.pdf",
      "file_ext": ".pdf", "size_bytes": 831022, "status": "ready",
      "error": null, "parser": "mineru", "chunk_count": 64, "retry_count": 0,
      "uploader_id": "...", "created_at": 1783…, "updated_at": 1783…, "finished_at": 1783… }
  ],
  "stats": { "documents": 12, "chunks": 860 } }
```

#### `GET /knowledge/documents/{doc_id}`
单个文档详情。**需登录。**
```json
// 响应 200
{ "document": { "id": "…", "kb_id": "…", "status": "parsing", … } }
// 404 文档不存在
```

#### `GET /knowledge/documents/{doc_id}/chunks`
分块预览（构建完成后有内容）。**需登录。**
```json
// 响应 200
{ "chunks": [
    { "id": "chunk-uuid", "kb_id": "kb-uuid", "doc_id": "…", "doc_name": "费用测算办法",
      "chunk_title": "第三章 / 费用构成", "content": "……", "doc_pos": 0,
      "token_num": 387, "is_use": true }
  ] }
```

#### `POST /knowledge/bases/{kb_id}/documents`（仅 admin，multipart）
上传文档到指定知识库——**只建 `uploaded` 文档，不触发解析**（步骤②）。**需 admin token。** `Content-Type: multipart/form-data`，字段：`file`（文件）、`title`（可选，缺省用文件名去扩展名）。

支持格式：`.pdf .doc .docx .ppt .pptx .xls .xlsx .txt .md`；大小上限 `knowledge.max_file_mb`（默认 100MB）。
```json
// 响应 202
{ "document": { "id": "doc-uuid", "kb_id": "kb-uuid", "status": "uploaded", … } }
// 400 格式不支持/空文件；404 知识库不存在；413 超大小
```

#### `POST /knowledge/documents/parse`（仅 admin）
批量触发解析（步骤③）。Body：`{ "document_ids": ["doc-uuid", …] }`。对 `uploaded`/`failed` 状态的文档逐个置 `pending` 并入队；其他状态（构建中/已就绪）跳过。
```json
// 响应 202
{ "queued": [{ "id": "doc-uuid", "job_id": "job-uuid" }],
  "skipped": [{ "id": "doc-uuid", "reason": "状态 ready 不可解析" }] }
// 503 队列不可用（无 Redis 且未开内嵌 worker）
```

#### `DELETE /knowledge/documents/{doc_id}`（仅 admin）
删除文档：清 ES/Milvus 投影 → 删本地文件 → 删 MySQL 行。
```json
// 响应 200
{ "deleted": "doc-uuid" }
```

#### `POST /knowledge/documents/{doc_id}/retry`（仅 admin）
重新构建。仅 `failed`/`ready` 状态可重试（`retry_count` 递增）。
```json
// 响应 202
{ "document_id": "doc-uuid", "job_id": "job-uuid" }
// 409 构建中不可重试；410 原始文件已丢失（需重新上传）
```

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
