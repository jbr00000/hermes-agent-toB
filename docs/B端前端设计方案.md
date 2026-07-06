# Hermes Agent B 端 Web 前端设计方案

> 本文档记录 Hermes Agent to-B 版本的前端设计方向。前端以 Proma 的工作台体验为参考，但不直接迁移 Electron 架构；第一版目标是构建一个浏览器访问的企业智能体工作台。

## 1. 设计结论

### 1.1 前端形态

- 选择：Web 管理后台 / 企业工作台。
- 不选择：Electron 桌面客户端。
- 原因：
  - Hermes 当前后端是 FastAPI + HTTP/SSE，天然适合 Web 前端对接。
  - to-B 场景需要多人登录、角色权限、部署、审计和统一入口。
  - Proma 的 Electron 主进程、IPC、本地窗口、自动更新、本地文件系统能力不适合直接搬入 Web 端。

### 1.2 产品优先级

- 第一版优先普通业务用户路径。
- 用户登录后默认进入 Agent 工作台。
- 管理员能力作为侧边栏次级入口，不做成首屏控制台。

### 1.3 实现策略

- 在 Hermes 仓库中新建 Web 前端工程。
- 建议路径：`apps/web`。
- 借鉴 Proma 的布局、Tab、组件风格和局部交互。
- 不 fork 整个 Proma Electron 前端，不照搬 `window.electronAPI`、IPC 和本地文件逻辑。

## 2. Proma 借鉴与减法

### 2.1 借鉴内容

| Proma 能力 | Hermes 前端借鉴方式 |
|---|---|
| 工作台外壳 | 左侧导航 + 中间多 Tab 工作区 + 右侧上下文面板 |
| TabBar | 借鉴外观、拖拽、关闭、激活态、恢复机制 |
| AgentView | 借鉴流式对话、输入区、运行状态、停止按钮 |
| Plan mode UI | 映射到 Hermes 的 plan / approve / execute 状态 |
| RichTextInput | 第一版可先简化，保留后续富输入升级空间 |
| SpeechButton | 保留语音输入入口，后续接语音后端 |
| Workspace / Agent Skills | 转换成“业务空间 / 知识库 / 能力中心” |
| Memory UI | 映射到 Hermes memory 与 memory candidates |
| Tool activity UI | 第一版做简化状态，后续等 SSE 结构化事件补齐后升级 |

### 2.2 不直接迁移内容

| Proma 内容 | Hermes 处理方式 |
|---|---|
| Electron 主进程 | 删除，不进入 Web 版 |
| `window.electronAPI` | 改成 HTTP API client + SSE client |
| 本地窗口控制 / 自动更新 | 删除 |
| 本地文件浏览器 / Git diff / worktree | 第一版不迁移 |
| Claude SDK 原始事件渲染 | 不照搬，等待 Hermes 后端事件结构升级 |
| 飞书 / 钉钉 / 微信桥接 | 不进入第一版 Web 工作台 |
| Proma 当前 `openTab` 收敛逻辑 | 不照搬，Hermes 要做真正多 Tab |

### 2.3 Proma Tab 持久化参考

Proma 当前 Tab 主状态不是 IndexedDB，也不是纯 localStorage。

- Renderer 中 `tabsAtom` / `activeTabIdAtom` 是 Jotai 内存态。
- 启动时通过 Electron IPC 读取 `settings.tabState`。
- 运行时监听 Tab 状态变化，防抖写入 `~/.proma/settings.json`。
- localStorage 主要用于轻量偏好，例如侧栏折叠、面板宽度、模型选择。

Hermes Web 版没有 Electron 主进程，因此用以下方式替代：

- Jotai 作为运行时 UI 状态。
- Dexie / IndexedDB 作为 Web 版的本地 `settings.json`。
- localStorage 只保存极轻配置和短期 token。

## 3. 技术栈

### 3.1 推荐栈

- React
- Vite
- TypeScript
- Tailwind CSS
- Jotai
- TanStack Query
- Dexie
- Radix UI / shadcn 风格组件
- lucide-react

### 3.2 状态分工

| 类型 | 技术 | 示例 |
|---|---|---|
| UI 运行态 | Jotai | 当前 Tab、侧栏开关、权限模式、输入草稿 |
| 服务端数据 | TanStack Query | 会话列表、用户列表、记忆列表、功能开关、知识库列表 |
| 本地持久化 | Dexie / IndexedDB | 打开的 Tabs、Tab UI 状态、最近访问空间 |
| 轻量偏好 | atomWithStorage / localStorage | 主题、API Base URL、侧栏折叠 |
| 认证 token | localStorage，后续升级 | Bearer JWT |

### 3.3 token 策略

第一版选择：短期 localStorage，后续升级为 httpOnly Cookie / refresh token。

规则：

- JWT 短期放 localStorage。
- 退出登录时清理 token、Query cache 和敏感 UI 状态。
- 401 自动清理 token 并跳回登录页。
- 不把完全访问授权持久化。
- 不把文档正文、数据库查询结果、大文件内容默认写入 IndexedDB。

## 4. 信息架构

### 4.1 一级导航

| 导航 | 用户 | 说明 |
|---|---|---|
| Agent 工作台 | 全部用户 | 默认首页，处理任务、查看会话、审批计划 |
| 知识库 | 有权限用户 | 空间、知识库、文档入库、解析状态、权限 |
| 记忆中心 | 全部用户 | 个人记忆、待确认记忆候选 |
| 任务中心 | 全部用户 | 后续承接任务状态与结果回传 |
| 审计中心 | 管理员 / 审计员 | 后续承接 audit API |
| 用户与权限 | 管理员 | 用户、角色、空间成员 |
| 能力与安全 | 管理员 | 功能开关、工具策略、模型与运行模式 |

### 4.2 第一版默认布局

```text
┌──────────────────────────────────────────────────────────────┐
│ Top Bar: 当前空间 / 全局搜索 / 用户菜单 / 运行环境状态         │
├───────────────┬───────────────────────────────┬──────────────┤
│ Left Sidebar  │ Main Tab Workspace            │ Right Panel  │
│ - 新建任务     │ - TabBar                      │ - 任务文件    │
│ - 会话列表     │ - Agent 会话                  │ - 引用知识库  │
│ - 知识库       │ - 知识库页                    │ - 计划审批    │
│ - 记忆中心     │ - 文档详情                    │ - 权限模式    │
│ - 管理入口     │                               │              │
└───────────────┴───────────────────────────────┴──────────────┘
```

## 5. 多 Tab 工作区

### 5.1 设计选择

- 采用 Proma 式多 Tab 外观。
- Hermes 实现真正多 Tab，不照搬 Proma 当前只保留当前会话的收敛逻辑。
- 第一版限制最多打开 12 个 Tab。
- 同类型同 `refId` 的 Tab 默认复用，不重复打开。
- 超过上限时提示用户关闭旧 Tab。

### 5.2 Tab 类型

| Tab 类型 | refId | 说明 |
|---|---|---|
| `agent` | `session_id` | Agent 会话 |
| `knowledgeBase` | `kb_id` | 知识库管理页 |
| `document` | `doc_id` | 文档详情 / 解析结果 / 权限 |
| `users` | 固定 key | 用户与权限 |
| `audit` | 固定 key 或过滤条件 hash | 审计中心 |
| `task` | `task_id` | 后续任务详情 |

### 5.3 本地持久化模型

建议使用 Dexie 建表：

```ts
tabs: {
  id: string
  type: 'agent' | 'knowledgeBase' | 'document' | 'users' | 'audit' | 'task'
  title: string
  refId: string
  order: number
  pinned: boolean
  updatedAt: number
}

tabUiState: {
  tabId: string
  rightPanelOpen?: boolean
  rightPanelTab?: 'files' | 'knowledge' | 'plan' | 'permissions'
  selectedSpaceId?: string
  selectedKnowledgeBaseIds?: string[]
  attachedFileRefs?: string[]
  draft?: string
  updatedAt: number
}

workspaceUiState: {
  key: string
  value: unknown
  updatedAt: number
}
```

安全限制：

- 不保存完全访问授权。
- 不保存文件正文。
- 不保存数据库结果。
- 不保存敏感凭据。

## 6. Agent 工作台

### 6.1 核心流程

1. 用户创建或打开 Agent 会话。
2. 用户选择业务空间、知识库引用范围和临时文件。
3. 用户输入任务。
4. 前端调用 `/chat`，通过 SSE 展示流式输出。
5. 若任务包含高风险操作，进入计划模式。
6. 用户审批计划。
7. 用户按需要切换权限模式。
8. 前端调用 execute，Agent 执行。
9. 任务结束后自动降权。

### 6.2 Hermes API 对接

| UI 能力 | API | 状态 |
|---|---|---|
| 登录 | `POST /auth/login` | 已有 |
| 当前用户 | `GET /auth/me` | 已有 |
| 会话列表 | `GET /sessions` | 已有 |
| 会话详情 | `GET /sessions/{session_id}` | 已有 |
| 恢复会话 | `POST /sessions/{session_id}/resume` | 已有 |
| 流式对话 | `POST /chat` | 已有 |
| 查询模式 | `GET /sessions/{session_id}/mode` | 代码已有，API 文档需同步 |
| 进入计划 | `POST /sessions/{session_id}/plan` | 代码已有，API 文档需同步 |
| 批准计划 | `POST /sessions/{session_id}/approve` | 代码已有，API 文档需同步 |
| 执行计划 | `POST /sessions/{session_id}/execute` | 代码已有，API 文档需同步 |

### 6.3 SSE 第一版事件处理

Hermes 当前 `/chat` SSE 事件：

- `delta`
- `final`
- `error`
- `done`

前端第一版只做文本流。

后续建议扩展事件：

- `tool_start`
- `tool_progress`
- `tool_result`
- `plan_required`
- `permission_required`
- `task_status`
- `artifact_created`

扩展后可逐步复用 Proma 的工具活动折叠展示思路。

## 7. 权限与风险分级

### 7.1 Agent 权限模式

第一版采用三档权限：

| 模式 | 允许操作 |
|---|---|
| 只读模式 | 普通问答、知识库检索、读取已授权文件、生成计划 |
| 受控写入 | 生成/导出结果文件、创建个人记忆、上传文档到待处理区 |
| 完全访问 | 修改共享知识库、数据库写操作、终端命令、批量变更、高危工具调用 |

规则：

- 默认只读模式。
- 完全访问仅当前任务生效。
- 任务结束、失败、取消或切换任务后自动降权。
- 完全访问不写入 localStorage / IndexedDB。

### 7.2 高风险操作双门控

以下操作必须同时满足：

1. 计划已生成并被用户审批。
2. 当前任务权限模式为完全访问。

操作类型：

- 修改共享知识库。
- 数据库写操作。
- 终端命令。
- 批量变更。
- 删除文件。
- 批量删除知识库内容。
- 其他高危工具调用。

### 7.3 风险分级

| 操作 | 执行策略 |
|---|---|
| 普通问答 | 直接执行 |
| 知识库检索总结 | 直接执行 |
| 读取已授权文件 | 直接执行，界面展示读取范围 |
| 导出结果文件 | 受控写入 |
| 上传文档到待处理区 | 受控写入 |
| 修改共享知识库 | 计划审批 + 完全访问 |
| 数据库写操作 | 计划审批 + 完全访问 |
| 终端命令 | 计划审批 + 完全访问 |
| 删除 / 批量删除 | 计划审批 + 完全访问 + 二次确认 |

## 8. 文件与知识库设计

### 8.1 设计选择

第一版同时支持：

- Agent 临时附件。
- 项目 / 业务空间知识库。
- Agent 对话时选择已有知识库引用。

后端文档解析和知识库构建能力由外部已有系统接入，Hermes 前端只抽象对接层，不在当前阶段要求 Hermes 后端补 `/files`。

### 8.2 Agent 临时附件

用于当前任务，例如：

- 上传 Excel。
- 让 Agent 读取、总结。
- 生成 txt / docx / xlsx / pdf 等结果文件。

前端表现：

- 输入区支持附件按钮和拖拽上传。
- 右侧面板展示“本次任务文件”。
- 文件状态：待上传、上传中、可用、解析中、解析完成、失败。
- 文件默认只绑定当前任务，不进入共享知识库。

### 8.3 知识库模块

知识库按“项目 / 业务空间”组织。

核心页面：

- 空间列表。
- 知识库列表。
- 文档列表。
- 文档详情。
- 解析状态。
- 权限配置。
- 检索测试。

### 8.4 知识库权限

选择混合模式：

- 默认空间级权限。
- 敏感文档支持文档级权限覆盖。

角色模型：

| 角色 | 权限 |
|---|---|
| 普通成员 | 读取、引用有权访问的知识库 |
| 知识库贡献者 | 上传、更新自己或被授权维护的文档 |
| 知识库管理员 | 文档审核、分类、删除、权限覆盖 |
| 空间管理员 | 成员管理、角色分配、空间配置 |
| 系统管理员 | 跨空间管理、全局配置 |

权限规则：

- 空间成员不等于自动拥有写入或管理权限。
- 普通成员默认只读。
- 文档默认继承空间权限。
- 文档可开启权限覆盖。
- Agent 只能引用当前用户可访问的知识库和文档。

## 9. 管理能力

### 9.1 用户与权限

可直接对接 Hermes 已有 API：

| UI | API |
|---|---|
| 用户列表 | `GET /users` |
| 创建用户 | `POST /users` |
| 删除用户 | `DELETE /users/{user_id}` |
| 修改角色 | `PUT /users/{user_id}/role` |

第一版 Hermes 已有角色：

- `admin`
- `user`

后续空间和知识库角色需要新后端能力承接。

### 9.2 记忆中心

可直接对接：

| UI | API |
|---|---|
| 我的记忆 | `GET /memory` |
| 新增记忆 | `POST /memory` |
| 删除记忆 | `DELETE /memory/{memory_id}` |
| 记忆候选 | `GET /memory/candidates` |
| 批准候选 | `POST /memory/candidates/{candidate_id}/approve` |
| 删除候选 | `DELETE /memory/candidates/{candidate_id}` |

说明：

- 记忆候选接口代码已存在，但 API 文档需要同步。
- 记忆候选是 B 端前端很重要的“可控学习”入口。

### 9.3 能力与安全

可直接对接：

- `GET /features`

第一版展示：

- `host_terminal` 是否开启。
- 当前终端后端是 docker 还是 local。
- 模型 provider / model 摘要。
- 当前用户角色。

后续需要补：

- 工具权限策略 API。
- 功能开关写入 API。
- MCP 管理 API。
- 部署配置只读 API。

### 9.4 审计中心

后端已有 `server/audit.py` 内部能力，但当前没有路由。

前端第一版：

- 保留导航入口。
- 展示“待后端开放审计查询接口”的空态。

后续需要补：

- `GET /audit/events`
- 按用户、会话、任务、工具、时间范围筛选。
- 高危操作单独标记。
- 导出审计日志。

## 10. API Client 设计

### 10.1 模块划分

```text
src/
  api/
    client.ts
    auth.ts
    sessions.ts
    chat-stream.ts
    memory.ts
    users.ts
    features.ts
    knowledge.ts
  state/
    tabs.ts
    auth.ts
    permissions.ts
    workspace.ts
  db/
    dexie.ts
    tab-store.ts
```

### 10.2 请求层规则

- 所有请求通过统一 `apiClient`。
- 自动附带 `Authorization: Bearer <token>`。
- 401 统一登出。
- 普通查询使用 TanStack Query。
- SSE 不走 TanStack Query，使用专门 stream controller。
- mutation 成功后按 query key 精确失效缓存。

### 10.3 Query Key 建议

```ts
['me']
['features']
['sessions']
['session', sessionId]
['memory']
['memoryCandidates', status]
['users']
['spaces']
['knowledgeBases', spaceId]
['documents', knowledgeBaseId]
```

## 11. 页面设计草案

### 11.1 登录页

字段：

- 用户名。
- 密码。
- API 地址配置入口。

行为：

- 登录成功保存短期 token。
- 拉取 `/auth/me`。
- 恢复本地 Tabs。
- 进入 Agent 工作台。

### 11.2 Agent 工作台

区域：

- 左侧：新建任务、会话列表、空间切换、管理入口。
- 中间：TabBar + 当前 Tab 内容。
- 右侧：任务文件、引用知识库、计划审批、权限模式。

关键控件：

- 新建任务。
- 计划 / 执行模式状态。
- 权限模式切换：只读、受控写入、完全访问。
- 附件上传。
- 知识库引用选择。
- 停止生成。
- 重新执行。

### 11.3 知识库页面

区域：

- 左侧空间树。
- 中间知识库 / 文档列表。
- 右侧详情抽屉。

核心字段：

- 文档名。
- 来源。
- 解析状态。
- 最近更新时间。
- 权限继承 / 权限覆盖。
- 可引用状态。

### 11.4 文档详情页

内容：

- 元信息。
- 解析状态。
- 分段 / chunk 预览。
- 权限设置。
- 被哪些任务引用。
- 错误日志。

### 11.5 记忆中心

Tab：

- 已保存记忆。
- 待审核候选。

能力：

- 新增。
- 删除。
- 批准候选。
- 编辑候选内容后批准。

### 11.6 用户与权限

第一版：

- 用户列表。
- 创建用户。
- 修改 admin / user。
- 删除用户。

后续：

- 空间成员。
- 知识库角色。
- 文档覆盖权限。

## 12. 迭代计划

### 阶段 1：前端工程与基础壳

- 新建 `apps/web`。
- 接入 Vite / React / TypeScript / Tailwind。
- 建立 AppShell、LeftSidebar、TabBar、MainArea、RightPanel。
- 建立 Jotai、TanStack Query、Dexie 基础设施。
- 实现登录和 token 管理。

### 阶段 2：Agent 工作台 MVP

- 会话列表。
- 会话详情。
- `/chat` SSE 文本流。
- 多 Tab 打开 / 关闭 / 恢复。
- 权限模式 UI。
- 计划 / 审批 / 执行 UI。
- 右侧任务上下文面板。

### 阶段 3：记忆与用户管理

- 记忆中心。
- 记忆候选审核。
- 用户列表。
- 创建用户。
- 删除用户。
- 修改角色。

### 阶段 4：知识库前端

- 业务空间。
- 知识库列表。
- 文档列表。
- 文档详情。
- 临时附件入口。
- 文档权限 UI。
- 与外部文档解析 / 知识库服务的前端适配层。

### 阶段 5：安全与审计升级

- 审计中心。
- 工具权限策略中心。
- 任务状态中心。
- 高风险操作审计标记。
- 完全访问授权审计。

### 阶段 6：体验增强

- 富文本输入。
- 语音输入。
- 文件预览。
- 工具过程结构化展示。
- 多媒体能力入口。
- 快捷键与命令面板。

## 13. 后端接口缺口

当前前端可先预留，但后续需要后端补齐：

| 能力 | 建议 API |
|---|---|
| 审计查询 | `GET /audit/events` |
| 工具权限策略 | `GET/PUT /tool-policies` |
| 任务状态 | `GET /tasks`, `GET /tasks/{id}` |
| 结果产物 | `GET /artifacts`, `GET /artifacts/{id}` |
| 文件上传 | 由外部文档服务或 Hermes 后续 `/files` 承接 |
| 知识库 | 由现有文档解析 / 知识库服务承接 |
| 空间权限 | `GET/PUT /spaces/{id}/members` |
| 文档权限覆盖 | `GET/PUT /documents/{id}/permissions` |
| 结构化 SSE | 扩展 `/chat` 事件类型 |

## 14. 已确认决策清单

- 前端形态：Web 管理后台 / 企业工作台。
- 第一版用户路径：普通业务用户优先。
- 文件能力：第一版设计进前端。
- 文件产品形态：Agent 临时附件 + 知识库管理入口。
- 知识库组织：项目 / 业务空间知识库 + 个人临时文件。
- 知识库权限：空间级权限 + 敏感文档权限覆盖。
- 空间成员默认不具备写入或管理权限。
- Agent 安全：按风险分级。
- 修改知识库、数据库写操作：计划审批 + 完全访问双门控。
- 权限模式：只读、受控写入、完全访问。
- 完全访问：仅当前任务生效。
- 工作区：Proma 式多 Tab。
- 多 Tab 实现：真正多 Tab，第一版最多 12 个。
- 前端工程策略：Hermes 新建 Web 工程，借鉴 Proma，不 fork Electron。
- 本地持久化：Jotai + Dexie / IndexedDB。
- 请求状态：Jotai 管 UI，TanStack Query 管服务端数据。
- token：第一版 localStorage，后续升级 httpOnly Cookie / refresh token。

