# Hermes Agent to-B 后续开发计划

> 本文档用于承接当前“去 C 端消息 gateway、保留智能体核心能力”的改造结果，规划下一阶段真正面向企业场景的服务层建设。
>
> 核心原则：不要复活旧 `gateway` 消息平台网关；新的 B 端入口应建设在 `server/` 或新的 enterprise API 层中。

## 1. 目标定位

当前项目已经逐步从个人/聊天平台智能体，收敛为面向企业内部系统的智能体运行框架。后续建设重点不再是 Telegram、Discord、微信、QQ 等消息平台接入，而是：

- 为自有 Web 前端提供稳定 API。
- 支持企业用户、租户、角色与权限管理。
- 让 agent 的工具调用、文件访问、数据库访问都可控、可审计、可追踪。
- 支持长任务、异步任务、任务状态查询和结果回传。
- 为未来多客户独立部署、半自动运维和安全交付打基础。

## 2. 总体路线

| 阶段 | 主题 | 目标 |
|---|---|---|
| Phase 1 | 统一 API 入口 | 建立前端和 agent 之间的稳定后端边界 |
| Phase 2 | 身份、租户、角色 | 建立用户体系和权限上下文 |
| Phase 3 | 工具权限与文件边界 | 控制 agent 能调用什么、能读写哪里 |
| Phase 4 | 审计与任务状态 | 让每次执行可追踪、可回放、可排障 |
| Phase 5 | 前端交互协议 | 在前端设计确定后固化交互事件、状态机和数据结构 |

## 3. Phase 1：统一 API 入口

### 3.1 建设目标

提供一个稳定的 to-B 后端入口，作为自有前端、企业系统和 agent runtime 之间的边界。

### 3.2 建议范围

- 新建或强化 `server/` 下的 FastAPI 服务。
- API 不直接暴露底层 `AIAgent` 构造细节。
- 把会话创建、消息发送、工具执行、文件上传、任务查询等能力封装成明确接口。
- 支持同步短请求和异步长任务两种模式。

### 3.3 计划任务

- [ ] 设计 API 路由分层：
  - `/api/sessions`
  - `/api/chat`
  - `/api/tasks`
  - `/api/files`
  - `/api/tools`
  - `/api/audit`
  - `/api/admin`
- [ ] 增加统一请求上下文 `RequestContext`：
  - `tenant_id`
  - `user_id`
  - `role`
  - `session_id`
  - `request_id`
  - `trace_id`
- [ ] 建立统一错误格式：
  - `code`
  - `message`
  - `details`
  - `request_id`
- [ ] 建立 API 层到 agent runtime 的适配层，避免前端直接依赖内部类。

### 3.4 验收标准

- 前端只通过 API 调用 agent，不直接依赖 CLI、旧 gateway、测试脚本。
- 每个请求都有 `request_id` 和明确错误结构。
- API 层能独立做权限、审计、限流和任务状态管理。

## 4. Phase 2：用户 / 租户 / 角色鉴权

### 4.1 建设目标

支持企业场景中的多人使用、租户隔离和角色授权。

### 4.2 核心模型

| 实体 | 说明 |
|---|---|
| Tenant | 企业客户或独立部署单元 |
| User | 企业内部使用者 |
| Role | 用户角色，例如管理员、普通用户、审计员 |
| Permission | 细粒度能力权限 |
| Session | 用户与 agent 的一次会话 |

### 4.3 计划任务

- [ ] 设计基础用户表和租户表。
- [ ] 设计角色模型：
  - `tenant_admin`
  - `operator`
  - `viewer`
  - `auditor`
- [ ] 设计登录方式：
  - 第一阶段可用本地账号 / API token。
  - 后续支持企业 SSO / OAuth / OIDC。
- [ ] 所有 session、memory、file、task、audit 记录都必须绑定：
  - `tenant_id`
  - `user_id`
- [ ] 增加权限中间件，在进入 agent runtime 前完成鉴权。

### 4.4 验收标准

- 不同用户的会话、文件、记忆、任务互相不可见。
- 普通用户不能执行管理员接口。
- 任意 agent 执行都能追溯到具体租户和用户。

## 5. Phase 3：工具权限策略

### 5.1 建设目标

让企业管理员可以控制 agent 能调用哪些工具，以及每类工具的权限边界。

### 5.2 权限策略维度

| 维度 | 示例 |
|---|---|
| 用户角色 | 管理员可配置工具，普通用户只能使用白名单工具 |
| 工具类型 | terminal、browser、file、db、mcp、image、audio、video |
| 风险等级 | 只读、可写、外部网络、执行命令、高风险 |
| 会话模式 | plan 模式只读，execute 模式可执行 |
| 租户配置 | 不同客户可启用不同工具集 |

### 5.3 计划任务

- [ ] 建立 `ToolPolicy` 模型：
  - `tool_name`
  - `enabled`
  - `allowed_roles`
  - `risk_level`
  - `requires_approval`
  - `scope`
- [ ] 在工具调用入口统一做权限检查。
- [ ] 区分只读工具和变更型工具。
- [ ] 对高风险工具增加审批机制。
- [ ] MCP 工具接入时必须先注册、审核、入库，再允许 agent 调用。
- [ ] 支持按租户配置工具白名单。

### 5.4 验收标准

- 未授权工具不能被 agent 绕过调用。
- 工具调用失败时返回明确权限错误，而不是底层异常。
- 审计日志能记录工具名称、参数摘要、调用人、调用结果。

## 6. Phase 4：文件访问边界

### 6.1 建设目标

确保 agent 只能访问当前租户、当前用户、当前任务允许访问的文件范围。

### 6.2 文件空间建议

建议按如下逻辑划分文件空间：

```text
storage/
  tenants/
    {tenant_id}/
      users/
        {user_id}/
      sessions/
        {session_id}/
      tasks/
        {task_id}/
      shared/
```

### 6.3 计划任务

- [ ] 建立文件元数据表：
  - `file_id`
  - `tenant_id`
  - `owner_user_id`
  - `session_id`
  - `task_id`
  - `path`
  - `mime_type`
  - `size`
  - `created_at`
- [ ] 禁止 API 直接传任意绝对路径给 agent。
- [ ] 前端上传文件后返回 `file_id`，agent 通过受控 resolver 获取文件路径。
- [ ] terminal / python / file tools 的工作目录限制在任务 workspace 内。
- [ ] 输出文件必须写入任务 workspace 或指定导出目录。
- [ ] 增加路径穿越防护：
  - 禁止 `..`
  - 禁止跨租户路径
  - 禁止访问系统目录
  - 禁止访问未授权绝对路径

### 6.4 验收标准

- 用户不能读取其他用户或其他租户文件。
- agent 生成代码也不能绕过文件边界。
- 所有输入文件和输出文件都可在任务记录中追踪。

## 7. Phase 5：操作审计

### 7.1 建设目标

企业场景必须能回答三个问题：

- 谁在什么时候发起了什么任务？
- agent 调用了哪些工具、访问了哪些文件、查询了哪些数据？
- 最终输出是什么，是否成功，失败原因是什么？

### 7.2 审计事件类型

| 事件 | 说明 |
|---|---|
| `auth.login` | 用户登录 |
| `session.create` | 创建会话 |
| `chat.message` | 用户发送消息 |
| `tool.call` | 工具调用开始 |
| `tool.result` | 工具调用结束 |
| `file.read` | 文件读取 |
| `file.write` | 文件写入 |
| `db.query` | 数据库查询 |
| `task.created` | 创建任务 |
| `task.completed` | 任务完成 |
| `policy.denied` | 权限拒绝 |

### 7.3 计划任务

- [ ] 建立统一审计写入接口 `AuditLogger`。
- [ ] 所有 API 请求写入基础审计。
- [ ] 所有工具调用写入工具审计。
- [ ] 文件读写、数据库查询单独记录。
- [ ] 敏感字段脱敏：
  - API key
  - token
  - password
  - cookie
  - authorization header
- [ ] 提供审计查询 API。

### 7.4 验收标准

- 任意任务都能查看完整执行轨迹。
- 审计日志不泄露密钥。
- 管理员可以按用户、时间、任务、工具类型筛选审计记录。

## 8. Phase 6：任务状态与结果回传

### 8.1 建设目标

企业任务往往不是一次 HTTP 请求能完成的。需要支持异步任务、进度状态、流式输出、失败重试和结果下载。

### 8.2 任务状态机

```text
created -> queued -> running -> waiting_approval -> completed
                               -> failed
                               -> cancelled
```

### 8.3 计划任务

- [ ] 建立 `Task` 模型：
  - `task_id`
  - `tenant_id`
  - `user_id`
  - `session_id`
  - `status`
  - `progress`
  - `created_at`
  - `started_at`
  - `finished_at`
  - `error`
- [ ] 支持任务创建 API。
- [ ] 支持任务状态查询 API。
- [ ] 支持任务取消 API。
- [ ] 支持任务事件流：
  - SSE 或 WebSocket 二选一。
- [ ] 支持结果文件列表和下载。
- [ ] 支持后台执行和前端断线重连后的状态恢复。

### 8.4 验收标准

- 前端刷新页面后仍能恢复任务状态。
- 长任务不会阻塞 API worker。
- 任务失败时能返回明确失败阶段和错误原因。

## 9. Phase 7：与前端交互协议

### 9.1 建设目标

在前端交互设计确定后，固化前后端协议，避免 UI 与 agent runtime 相互耦合。

### 9.2 初步协议对象

| 对象 | 说明 |
|---|---|
| Session | 会话 |
| Message | 用户 / assistant / tool 消息 |
| Task | 异步任务 |
| ToolEvent | 工具调用事件 |
| FileAsset | 文件资产 |
| ApprovalRequest | 人工审批请求 |
| AuditEvent | 审计事件 |

### 9.3 计划任务

- [ ] 等前端交互稿确定后，补充页面级 API：
  - 会话页
  - 任务页
  - 文件页
  - 审计页
  - 管理后台
- [ ] 定义消息流事件：
  - `message.delta`
  - `message.completed`
  - `tool.started`
  - `tool.completed`
  - `task.progress`
  - `approval.required`
  - `error`
- [ ] 定义前端可展示的错误码。
- [ ] 定义任务结果展示结构：
  - 文本摘要
  - 文件列表
  - 表格数据
  - 图片 / 音频 / 视频资产
- [ ] 定义审批交互：
  - 允许
  - 拒绝
  - 修改后执行

### 9.4 验收标准

- 前端不需要理解 agent 内部实现。
- 任务事件可以完整驱动 UI 状态。
- 审批、取消、重试、下载都有明确协议。

## 10. 建议优先级

| 优先级 | 内容 | 原因 |
|---|---|---|
| P0 | 统一 API 入口 | 没有 API 边界，前端和后端无法稳定并行开发 |
| P0 | 用户 / 租户 / 角色鉴权 | B 端安全底座，越早做越少返工 |
| P0 | 文件访问边界 | 防止 agent 读取或写入非授权路径 |
| P0 | 工具权限策略 | 防止工具能力失控 |
| P1 | 任务状态与结果回传 | 支撑真实长任务和前端体验 |
| P1 | 操作审计 | 企业交付和排障必需 |
| P2 | 前端交互协议 | 等前端原型明确后细化 |

## 11. 第一轮建议拆分

### Sprint 1：API 与上下文骨架

- [ ] 定义 `RequestContext`。
- [ ] 建立统一 API 响应和错误格式。
- [ ] 增加 session 创建和 chat 调用基础接口。
- [ ] 让 API 请求能携带 `tenant_id/user_id/session_id`。

### Sprint 2：权限模型

- [ ] 建立用户、租户、角色、权限模型。
- [ ] 增加 API 鉴权中间件。
- [ ] 增加工具调用前的权限检查。
- [ ] 增加最小工具白名单。

### Sprint 3：文件与任务

- [ ] 建立文件上传和 `file_id` resolver。
- [ ] 建立任务模型和任务状态机。
- [ ] 支持异步任务执行。
- [ ] 支持任务结果文件回传。

### Sprint 4：审计与前端事件

- [ ] 建立 `AuditLogger`。
- [ ] 记录 API、工具、文件、任务事件。
- [ ] 输出 SSE / WebSocket 事件流。
- [ ] 根据前端原型调整事件协议。

## 12. 风险与约束

- 不要把旧 `gateway` 作为 B 端 API 网关复用；它的抽象是消息平台适配，不是企业 API 边界。
- 工具权限必须在工具执行入口强制检查，不能只依赖前端隐藏按钮。
- 文件权限必须在后端 resolver 和执行环境两层限制。
- 数据库只读必须落在数据库 GRANT 层，不能只靠应用层约束。
- 审计日志必须脱敏，否则会把密钥、路径、业务数据泄露到日志系统。
- 前端协议不宜过早锁死，建议等第一版交互稿确定后再冻结。

