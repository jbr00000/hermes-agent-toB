# Chat 真实接入资源清单（不含知识库）

## 当前实施状态（2026-08-06）

已完成首个可运行闭环：

- Docker Compose 中的 MySQL 8 与 Redis 7，宿主机端口分别为 `13306`、`16379`。
- SQLAlchemy 领域模型、PyMySQL 连接池和 Alembic 初始迁移。
- 用户、刷新会话、对话、消息、模型运行、个人记忆和审计统一写入 MySQL。
- Redis 会话锁、请求状态、取消标志和短期 SSE 事件缓冲。
- 登录、浏览器会话恢复、退出、新建/查询/更新会话、历史消息和 Chat SSE 接口。
- Chat 服务端只读工具策略（当前仅开放 `db`），以及前端真实登录、历史会话、流式消息和停止生成。

当前批次尚未包含：会话游标分页、客户端 SSE 断线续传、跨进程模型中断、附件对象存储、限流、生产 Ingress 和知识库。前端附件与知识库按钮仍是后续接入入口，不代表后端能力已开放。

## 1. 建设顺序

当前阶段只打通 Chat，验收通过后再开始 Agent：

1. 登录、用户身份和权限。
2. 新建会话、发送消息、SSE 流式响应和停止生成。
3. 历史会话持久化、恢复、重命名、置顶、归档和分页查询。
4. Chat 只读工具调用、审计和异常恢复。
5. 可选的聊天附件上传、解析结果回传和产物下载。
6. 完成稳定性、安全性和备份恢复测试。
7. 再接入 Agent 的计划、审批、执行、任务队列和产物管理。

知识库、向量数据库、Embedding、文档入库和知识库权限不在本阶段范围内。

## 2. 核心原则

- **MySQL 是业务数据的唯一事实来源**：历史会话、消息、用户、权限和审计必须落 MySQL。
- **Redis 只保存短生命周期状态**：缓存、限流、分布式锁、运行中请求和 SSE 断线续传信息。Redis 丢失后不能导致历史会话丢失。
- **文件正文不写入 MySQL 或 Redis**：启用聊天附件时，文件进入 MinIO/S3，对象元数据进入 MySQL。
- **客户业务数据库与 Hermes 自身数据库隔离**：Chat 查询客户数据库时使用独立的只读账号、独立连接池和独立网络策略。
- **Chat 和 Agent 使用服务端强制策略隔离**：不能只依赖前端的模式切换。Chat 禁止终端、文件写入、数据库写入和有副作用的 MCP 工具。

这里的“Session”需要拆成三类，不能混用：

| Session 类型 | 保存位置 | 说明 |
| --- | --- | --- |
| 对话会话与消息历史 | MySQL | 长期保存，可审计、检索、备份和恢复 |
| 登录会话与刷新令牌 | MySQL + Redis | MySQL 保存哈希后的权威记录，Redis 做缓存、撤销和快速校验 |
| 正在运行的流式请求 | Redis | 保存锁、取消标志、事件游标和短期重放缓冲区 |

## 3. 首期必需资源

### 3.1 基础服务

| 资源 | 用途 | 首期要求 |
| --- | --- | --- |
| Hermes Web 前端 | Chat 操作界面 | 使用现有 `apps/web`，从 Mock API 切换到真实 API |
| FastAPI 服务 | 统一 API、鉴权、会话和流式响应 | 尽量无状态，可部署多个副本 |
| MySQL 8 | 持久化业务数据 | 必需；启用 `utf8mb4`、UTC 时间、自动备份 |
| Redis 7 | 临时运行状态和协调 | 必需；设置最大内存、淘汰策略和 key TTL |
| Nginx 或企业 Ingress | TLS、同源代理、SSE 转发 | 必需；关闭 SSE 响应缓冲，配置合理超时 |
| DeepSeek/OpenAI 兼容模型服务 | Chat 推理 | 必需；准备 Base URL、API Key、模型名和额度策略 |
| 密钥管理 | 保存数据库、JWT 和模型密钥 | 至少使用部署环境变量或 Secret，禁止提交到 Git |

### 3.2 后端依赖建议

| 能力 | 建议组件 | 说明 |
| --- | --- | --- |
| ORM 与连接池 | SQLAlchemy 2.x | 避免路由直接编写数据库细节 |
| MySQL 驱动 | `PyMySQL` | 当前与同步 FastAPI/SQLAlchemy 调用链一致，全项目只保留一种驱动 |
| 数据库迁移 | Alembic | 表结构变更必须版本化，禁止生产环境手工改表 |
| Redis 客户端 | `redis-py` asyncio API | 用于锁、限流、取消和 SSE 临时事件 |
| API 数据模型 | 现有 Pydantic/FastAPI 模型 | 前后端字段和错误码形成稳定契约 |
| 集成测试 | pytest + MySQL/Redis 测试容器或独立测试实例 | 不用 SQLite 替代生产数据库做关键集成测试 |

不建议在 Chat 首期引入 Kafka、Elasticsearch、Celery 或 Kubernetes。当前单客户 10 至 50 用户的目标规模下，MySQL、Redis 和 FastAPI 已足够；这些组件应由真实容量或可靠性需求触发。

## 4. 数据资源清单

### 4.1 MySQL 数据

建议先建立以下表或等价领域模型：

| 数据 | 关键字段或要求 |
| --- | --- |
| `tenants` | 即使当前是一客户一部署，也保留稳定的 `tenant_id` 边界 |
| `users` | 账号、状态、角色、密码哈希、所属租户、创建与更新时间 |
| `auth_sessions` | 刷新令牌哈希、设备信息、过期时间、撤销时间 |
| `conversations` | 用户、类型 `chat/agent`、标题、置顶、归档、状态、更新时间 |
| `messages` | 会话、顺序号、角色、结构化内容、状态、模型运行标识、时间 |
| `model_runs` | 模型、耗时、Token 用量、结束原因、错误类型、请求标识 |
| `attachments` | 对象存储 key、原文件名、类型、大小、哈希、所有者和状态 |
| `memory_items` | 现有个人记忆数据；它不等同于知识库 |
| `memory_candidates` | 待确认的记忆候选及处理状态 |
| `audit_events` | 登录、会话访问、Chat 请求、工具调用、权限拒绝和管理操作 |
| `idempotency_requests` | 防止重试导致同一条消息或模型请求重复执行 |

数据库约束与索引至少包括：

- 所有业务表带 `tenant_id`，用户数据查询同时限定 `tenant_id` 和 `user_id`。
- 会话列表使用 `(tenant_id, user_id, archived, updated_at)` 组合索引和游标分页。
- 消息使用 `(conversation_id, sequence_no)` 唯一约束，保证顺序稳定。
- 每次发送消息携带 `request_id`/幂等键，服务端建立唯一约束。
- 大型工具输出只保存摘要和产物引用，不把无上限日志直接塞进消息表。
- 删除默认采用归档或软删除；物理清理由明确的数据保留策略执行。

### 4.2 Redis 数据

Redis 建议只承担以下职责：

| Key 类型 | 用途 | 建议 TTL |
| --- | --- | --- |
| 会话并发锁 | 防止同一会话同时生成两条回复 | 60 秒并由运行任务续期 |
| 运行请求状态 | 记录 running/cancelling 和执行节点 | 15 至 60 分钟 |
| 取消标志 | 支持前端“停止生成”跨 API 副本生效 | 15 至 60 分钟 |
| SSE 事件缓冲 | 按事件 ID 保存短期增量，支持断线续传 | 15 至 60 分钟 |
| 用户/租户限流 | 控制请求频率和并发模型调用数 | 按限流窗口设置 |
| 鉴权缓存/撤销 | 快速识别已撤销登录会话 | 不超过令牌有效期 |
| 模型临时状态 | 供应商退避、熔断和短期健康状态 | 秒级到分钟级 |

Key 必须统一带命名空间和租户边界，例如：

```text
hermes:{tenant_id}:conversation:{conversation_id}:lock
hermes:{tenant_id}:request:{request_id}:state
hermes:{tenant_id}:request:{request_id}:cancel
hermes:{tenant_id}:stream:{request_id}:events
hermes:{tenant_id}:rate:user:{user_id}
```

禁止只在 Redis 中保存消息正文、会话列表、审计记录、用户资料或附件元数据。

### 4.3 对象存储（启用聊天附件时必需）

第一版如果保留前端附件入口，需要准备 MinIO 或企业 S3 兼容存储：

- 原始上传文件、解析后的临时文件和模型生成产物写入对象存储。
- MySQL 只保存对象 key、哈希、大小、MIME、所有者和生命周期状态。
- 上传前校验扩展名、MIME、大小和文件名，下载使用短时签名 URL 或鉴权代理。
- 对象 key 必须含租户隔离，服务端不能接受用户直接提交任意本地路径。
- 配置病毒扫描接口、生命周期清理和存储配额；暂未接入扫描时，不允许文件被自动执行。

如果 Chat 首期不开放附件，可暂不部署 MinIO，并在前端隐藏或禁用附件入口，而不是保留无效按钮。

## 5. Chat 功能与资源对应表

| 前端功能 | 后端模块/接口 | MySQL | Redis | 其他资源 |
| --- | --- | --- | --- | --- |
| 登录、退出、当前用户 | Auth API | 用户、角色、登录会话 | 会话缓存、撤销 | JWT/Secret |
| 新建对话 | Conversation API | 新建会话 | 可选短期幂等 | 无 |
| 历史会话列表 | Session API | 标题、置顶、归档、更新时间 | 可选热点缓存 | 无 |
| 加载历史消息 | Message API | 消息与模型运行记录 | 不作为历史来源 | 无 |
| 发送消息 | Chat API | 用户消息、助手消息、运行记录 | 并发锁、幂等屏障 | 模型服务 |
| SSE 流式输出 | Chat Stream | 最终结果和状态 | 事件 ID、短期重放 | Nginx SSE 配置 |
| 停止生成 | Cancel API | 最终记录 cancelled 状态 | 取消标志、运行节点 | Agent 中断能力 |
| 自动生成标题 | 现有标题生成能力 | 会话标题 | 可选去重锁 | 模型服务 |
| 置顶、重命名、归档 | Conversation API | 持久化状态 | 可选缓存失效 | 无 |
| Chat 只读工具 | Tool Policy + Agent Core | 调用摘要、结果引用 | 运行中状态 | 客户只读数据源/MCP |
| 个人记忆 | Memory API | 记忆和候选 | 可选缓存 | 不属于知识库 |
| 审计查询 | Audit API | 权威审计事件 | 不保存权威审计 | 日志/监控平台 |
| 聊天附件 | Attachment API | 元数据 | 上传状态 | MinIO/S3、可选病毒扫描 |

## 6. 代码结构要求

现有 SQLite 能力不应在每个路由中直接替换成 MySQL 调用。先建立稳定接口，再接入具体适配器：

```text
ChatService
  -> ConversationRepository   -> MySQLConversationRepository
  -> MessageRepository        -> MySQLMessageRepository
  -> UserRepository           -> MySQLUserRepository
  -> MemoryRepository         -> MySQLMemoryRepository
  -> AuditRepository          -> MySQLAuditRepository
  -> StreamStateStore         -> RedisStreamStateStore
  -> LockManager              -> RedisLockManager
  -> ObjectStore              -> S3ObjectStore（可选）
```

这样可以保留 SQLite 作为局部开发/单元测试适配器，但生产配置只启用 MySQL。旧 `users.db`、session SQLite 和 `audit.db` 如有保留价值，应使用一次性迁移脚本导入 MySQL，不能长期双写。

Chat 服务端策略还需要单独收紧：

- 请求必须显式标识 `interaction_type=chat`，不能沿用默认 `execute`。
- Chat 工具白名单只允许只读数据库查询、会话检索和明确标注为只读的 MCP 工具。
- 终端、文件写入、数据库写入、浏览器提交操作和未标注风险的 MCP 工具在 Chat 中一律拒绝。
- 工具风险判定和拒绝原因由服务端生成并写入审计，不能信任前端传入的权限级别。

## 7. 部署资源基线

以下是单客户、10 至 50 名用户的起步值，最终以并发量、上下文长度、附件规模和压测结果调整：

| 服务 | 开发/联调 | 小规模生产基线 |
| --- | --- | --- |
| FastAPI | 2 vCPU / 4 GB，单实例 | 2 个实例，每个 2 vCPU / 4 GB |
| MySQL | 2 vCPU / 4 GB / 50 GB SSD | 4 vCPU / 8 GB / 100 GB SSD，独立备份盘 |
| Redis | 1 vCPU / 1 GB | 2 vCPU / 2 至 4 GB，设置内存上限 |
| Nginx/Ingress | 可与联调机共用 | 1 至 2 个实例，1 vCPU / 1 GB |
| MinIO/S3 | 附件关闭时不需要 | 按文件量从 100 GB 起，并设置生命周期 |
| 监控与日志 | 本地结构化日志 | 2 vCPU / 4 GB 起，容量按保留期计算 |

生产环境还需准备：

- 域名、企业证书、内外网访问规则和模型服务出口白名单。
- MySQL 每日备份、binlog/PITR、恢复演练和备份加密。
- Redis 高可用或托管实例；即使 Redis 故障，历史会话仍应可查询。
- 结构化日志、指标和告警，至少覆盖 API 错误率、SSE 中断、模型耗时、Token 用量、数据库连接池和 Redis 内存。
- 数据保留期、会话导出/删除、审计留存和敏感字段脱敏策略。

## 8. 配置项清单

建议由部署 Secret 注入以下配置，名称可在实现时与现有配置体系统一：

```text
HERMES_DATABASE_URL
HERMES_DATABASE_POOL_SIZE
HERMES_DATABASE_MAX_OVERFLOW
HERMES_REDIS_URL
HERMES_JWT_SECRET
HERMES_ACCESS_TOKEN_TTL
HERMES_REFRESH_TOKEN_TTL
LLM_PROVIDER
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
OBJECT_STORE_ENDPOINT        # 启用附件时
OBJECT_STORE_ACCESS_KEY      # 启用附件时
OBJECT_STORE_SECRET_KEY      # 启用附件时
OBJECT_STORE_BUCKET          # 启用附件时
MAX_UPLOAD_SIZE              # 启用附件时
AUDIT_RETENTION_DAYS
CHAT_MAX_CONCURRENCY_PER_USER
CHAT_REQUEST_TIMEOUT
```

客户业务数据源的账号和连接串必须使用另一组 Secret，并强制只读权限，不能复用 Hermes 自身 MySQL 账号。

## 9. Chat 接入实施批次

### C1：存储基础

- 建立 Repository/Store 接口、SQLAlchemy 模型和 Alembic。
- 将用户、会话、消息、记忆和审计迁移到 MySQL 适配器。
- 接入 Redis 连接、命名空间、健康检查和故障降级。
- 明确是否迁移现有 SQLite 数据。

### C2：鉴权与会话历史

- 前端接入登录、当前用户、退出和令牌刷新。
- 完成会话新建、列表、详情、重命名、置顶、归档和游标分页。
- 接入自动标题生成，并把标题持久化到 MySQL。

### C3：Chat 主链路

- 定义稳定的发送消息与 SSE 事件协议。
- 实现用户消息落库、模型流式输出、助手消息提交和失败状态落库。
- 使用 Redis 实现同会话串行、停止生成和 SSE 短期断线续传。
- 强制执行 Chat 只读工具策略和 MCP 风险元数据校验。

### C4：附件与运维能力

- 需要附件时接入 MinIO/S3、上传校验、下载鉴权和生命周期清理。
- 提供审计查询、请求链路标识、用量统计、指标和告警。
- 完成 MySQL 备份恢复、Redis 故障、API 多副本和模型超时测试。

### C5：Chat 验收后冻结接口

- 固化 OpenAPI、SSE 事件、错误码和前端类型定义。
- 移除 Chat 页面 Mock 数据和无真实能力的控件。
- Chat 验收通过后，Agent 复用同一套身份、会话、消息、审计和文件基础设施。

## 10. Chat 完成标准

- 服务重启、API 副本切换或 Redis 清空后，历史会话和消息不丢失。
- 任意用户只能读取自己和被授权空间的会话，所有查询都经过租户与用户边界校验。
- 同一会话并发提交不会产生消息乱序或重复模型调用。
- SSE 断开可恢复；无法恢复时，前端能从 MySQL 查询最终状态。
- “停止生成”在多 API 副本下生效，并留下完整状态和审计记录。
- Chat 无法调用终端、文件写入、数据库写入或未标注为只读的 MCP 工具。
- 密钥、完整提示词中的敏感信息和大段工具结果不会进入普通日志。
- MySQL 备份可恢复，附件启用时对象存储也有对应恢复和清理策略。
- 前端 Chat 不再依赖 Mock API；核心流程通过真实 MySQL、Redis 和模型服务集成测试。

## 11. Agent 阶段再准备的资源

Chat 验收前不需要引入以下资源：

- 后台任务队列与 Worker，用于长任务、重试和任务恢复。
- 隔离的终端/代码执行沙箱和按租户划分的工作目录。
- Agent 计划、审批、权限提升、步骤状态和检查点数据模型。
- 长任务事件总线或 Redis Streams 持久消费方案。
- 定时任务调度器、任务产物目录和更长周期的运行日志。
- 数据库写操作审批、浏览器有副作用操作审批和完全访问授权机制。

Agent 应复用 Chat 已验证的 MySQL、Redis、鉴权、审计、SSE 和对象存储基础，而不是另建一套会话体系。
