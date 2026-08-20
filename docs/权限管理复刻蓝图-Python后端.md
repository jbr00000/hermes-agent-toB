# 权限管理复刻蓝图 — Python 后端 + Vue3 前端

## 文档定位

把 LONE AI 当前框架（Java/Spring Boot + Sa-Token）的权限管理方案，复刻到一个**新的智能体框架**。

- 后端：**Python**（本文选定 `FastAPI + SQLAlchemy 2.0 + Alembic + Redis`，见下"技术选型"）
- 前端：**沿用当前 Vue3 技术栈照搬**，仅删除微信小程序相关
- 本版**不含**：微信小程序登录、MCP（可选后补）、演示模式（可选后补）

> 本文是蓝图，不是完整实现。关键机制给出可直接落地的 Python 骨架；CRUD 类样板只给签名。原 Java 事实源见 `code/backend/lone-ai-server/`。

---

## 技术选型（可替换，但建议保持）

| 角色 | 当前(Java) | 本蓝图(Python) | 选择理由 |
|---|---|---|---|
| Web 框架 | Spring Boot 3 | **FastAPI** | 控制器/DTO/依赖注入风格最接近 Spring；原生 async |
| ORM | Spring Data JPA | **SQLAlchemy 2.0 (async)** | 多对多/懒加载等价 JPA；`selectinload` 替代 JPA 抓取 |
| 迁移 | Flyway | **Alembic** | 版本化 DDL，对应 Flyway 的 V1/Vn |
| 会话/鉴权 | Sa-Token | **自建 opaque token + Redis** | 忠实复刻 Sa-Token"token 不携带 claims、服务端存会话、权限实时算"的特性 |
| 密码哈希 | BCryptPasswordEncoder | **passlib[bcrypt]** | 同算法，哈希结果互通 |
| 数据库 | MySQL | **MySQL (aiomysql)** | 与现框架一致；换 Postgres 仅改驱动 |
| 校验/DTO | Jakarta Validation + record | **Pydantic v2** | 请求/响应模型 + 校验 |
| 缓存/会话存储 | Redis | **Redis (redis-py async)** | 存 token→userId |

> 若团队偏好 Django：把 FastAPI 换成 Django REST Framework，模型用 Django ORM，鉴权用 DRF + 自定义 token auth，整体映射关系不变。但 FastAPI 与当前 Spring 结构对照更直观，本蓝图以 FastAPI 为准。

**鉴权策略决策（重要）**：用 **opaque token 存 Redis**，不用 JWT。
- 当前 Sa-Token 的 token 是不透明串，权限码每次请求从 DB 实时算（`StpInterfaceImpl`），所以"改了角色授权立刻生效""删 token 立刻踢人"都成立。
- JWT 把权限写进 payload，要么接受"授权变更后旧 token 仍带旧权限直到过期"，要么每次都查 DB 校验 token——后者等于白用 JWT。因此复刻时直接用 opaque token + Redis，行为与现框架一致。
- JWT 仅在"完全无状态、可接受分钟级授权延迟、需横向扩展无共享存储"时再考虑。

---

## 一、目标架构总览

```
┌─────────────────────────── 前端 (Vue3, 照搬) ───────────────────────────┐
│  stores/auth.ts   ← /api/auth/me → {permissions:Set, menus:树}          │
│  router/index.ts  ← 动态路由由 menus 驱动; meta.permission 守卫          │
│  RoleManagementView.vue ← 两棵授权树: 菜单树 + AI授权树(managed ACTION)  │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ Authorization: Bearer <opaque-token>
                                 ▼
┌─────────────────────────── 后端 (FastAPI) ──────────────────────────────┐
│  routers/auth.py        login / logout / captcha / me / dev-token        │
│  routers/system/*.py    users / roles / resources  (CRUD + 角色授权)      │
│  routers/home_config.py AI业务/场景 CRUD  ← managed ACTION 的派生源       │
│  ───────────────────────────────────────────────────────────────────── │
│  services/auth_service.py      登录校验 + 权限解析(me)                     │
│  services/role_service.py      角色 CRUD + 角色↔资源 + 角色↔用户          │
│  services/resource_service.py  资源 CRUD + 菜单树 + 禁止人工建ACTION      │
│  services/home_config_service  AI业务/场景 → 派生managed ACTION + sync    │
│  core/security.py              token签发/校验 + bcrypt                    │
│  core/deps.py                  current_user_id 依赖 (读Redis→DB)          │
│  db/seed.py                    启动自举: SUPER_ADMIN/admin/基础资源       │
│  ───────────────────────────────────────────────────────────────────── │
│  models/   User Role Resource + user_role role_resource + HomeBizModule/Route │
└────────────────────────────────┬────────────────────────────────────────┘
                                 ▼
                   MySQL (业务库)   Redis (token→userId 会话)
```

**一条铁律**：所有 HTTP 路径与原 Java 服务**完全一致**（`/api/auth/login`、`/api/auth/me`、`/api/system/roles` …）。这样前端可以几乎零改动直接对接。

---

## 二、数据模型（SQLAlchemy）

五张表 + 两张 AI 业务表，字段与原 `V1__baseline_schema.sql` 对齐。

```python
# app/models/__init__.py  (或拆分到各文件)
import enum
from datetime import datetime
from sqlalchemy import BigInteger, String, SmallInteger, Boolean, Enum, ForeignKey, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase): pass

# —— 中间表 ——
user_role = Table(
    "sys_user_role", Base.metadata,
    Column("user_id", BigInteger, ForeignKey("sys_user.id"), primary_key=True),
    Column("role_id", BigInteger, ForeignKey("sys_role.id"), primary_key=True),
)
role_resource = Table(
    "sys_role_resource", Base.metadata,
    Column("role_id", BigInteger, ForeignKey("sys_role.id"), primary_key=True),
    Column("resource_id", BigInteger, ForeignKey("sys_resource.id"), primary_key=True),
)

class ResourceType(str, enum.Enum):
    MENU = "MENU"
    PAGE = "PAGE"
    ACTION = "ACTION"   # 仅系统派生的 AI 授权使用，人工不可创建

class Resource(Base):
    __tablename__ = "sys_resource"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    name: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[ResourceType] = mapped_column(Enum(ResourceType))
    route_name: Mapped[str | None] = mapped_column(String(64), unique=False)  # 业务幂等键
    path: Mapped[str | None] = mapped_column(String(128))
    component_key: Mapped[str | None] = mapped_column(String(64))
    permission_code: Mapped[str | None] = mapped_column(String(64))
    icon: Mapped[str | None] = mapped_column(String(64))
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_resource.id"))
    sort_no: Mapped[int] = mapped_column(SmallInteger, default=0)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class Role(Base):
    __tablename__ = "sys_role"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at / updated_at  # 同上
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    resources: Mapped[list[Resource]] = relationship(secondary=role_resource)
    users: Mapped[list[User]] = relationship(secondary=user_role, back_populates="roles", viewonly=True)

class User(Base):
    __tablename__ = "sys_user"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at / updated_at
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password: Mapped[str] = mapped_column(String(255))           # bcrypt
    display_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(32))        # 本版保留字段，登录不用
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[list[Role]] = relationship(secondary=user_role, back_populates="users")
```

字段与原表一一对应，只去掉 `openid`（微信专用）。`ResourceType` 三值的语义保持不变：**MENU/PAGE 给人，ACTION 只给系统派生**。

---

## 三、认证层

### 3.1 token 签发与校验（`core/security.py`）

```python
import secrets
from passlib.context import CryptContext

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
TOKEN_TTL = 7 * 24 * 3600          # 与 Sa-Token 默认同级
SUPER_ADMIN = "SUPER_ADMIN"

def hash_password(raw: str) -> str: return pwd.hash(raw)
def verify_password(raw: str, hashed: str) -> bool: return pwd.verify(raw, hashed)

async def issue_token(redis, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    await redis.set(f"auth:token:{token}", str(user_id), ex=TOKEN_TTL)
    return token

async def revoke_token(redis, token: str) -> None:
    await redis.delete(f"auth:token:{token}")
```

### 3.2 全局登录依赖（`core/deps.py`）— 对应 `SaInterceptor`

```python
from fastapi import Request, HTTPException, Depends
from app.db import get_session, get_redis

PUBLIC_PATHS = {                       # 对应 SaTokenConfiguration 的白名单
    "/api/auth/login", "/api/auth/captcha", "/api/auth/dev-token",
    "/api/auth/demo-mode", "/api/health", "/actuator", "/docs",
    "/openapi.json", "/redoc",
}

async def current_user_id(request: Request) -> int:
    if request.url.path in PUBLIC_PATHS:
        return 0   # 公开端点不走鉴权（实际由路由自身决定是否注入本依赖）
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "未登录")
    redis = get_redis()
    raw = await redis.get(f"auth:token:{token}")
    if not raw:
        raise HTTPException(401, "会话已失效")
    return int(raw)
```

> 实践中推荐用 **FastAPI 中间件**而非每个路由注入依赖来做全局登录拦截（白名单放行，其余校验 token），与 `SaInterceptor` 行为最接近。上面是依赖形式的等价表达。

### 3.3 登录服务（`services/auth_service.py`）

```python
async def login(req, session, redis) -> str:
    # 1. 校验验证码 (captcha_service.verify)
    # 2. 查用户 by username
    # 3. user.enabled 否则抛 AUTH_USER_DISABLED + 写审计
    # 4. verify_password 失败则抛 AUTH_INVALID_CREDENTIALS + 写审计
    # 5. 写审计成功
    return await issue_token(redis, user.id)

async def current_user_response(user_id, session) -> CurrentUserResponse:
    perms = await load_permissions(session, user_id)      # 见第四节
    user = await load_user_eager(session, user_id)
    role_codes = sorted(r.code for r in user.roles if r.enabled)
    menus = build_menu_tree([r for r in flat_resources(user) if r.enabled])  # 见第四节
    return CurrentUserResponse(
        id=user.id, username=user.username, display_name=user.display_name,
        roles=role_codes, permissions=perms, menus=menus)
```

**登录接口路径保持**：`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/captcha`、`GET /api/auth/me`、`POST /api/auth/dev-token`（仅本机）。**删除** `/api/auth/wechat/mini/login`。

---

## 四、权限解析层（核心，必须忠实复刻）

这一段是整个方案的灵魂。原 Java 里它出现在三处逻辑相同（`AuthService.currentUser` / `StpInterfaceImpl.getPermissionList` / `HomeConfigService.loadCurrentUserPermissions`）。Python 里抽成**一个函数**，杜绝三份不一致：

```python
# services/permission_service.py
from sqlalchemy import select
from sqlalchemy.orm import selectinload

async def load_user_eager(session, user_id: User):
    """一次性把 user→roles→resources 抓全，避免 N+1。"""
    stmt = select(User).options(
        selectinload(User.roles).selectinload(Role.resources)
    ).where(User.id == user_id)
    return await session.scalar(stmt)

async def load_permissions(session, user_id) -> set[str]:
    user = await load_user_eager(session, user_id)
    if not user:
        return set()
    codes: set[str] = set()
    for role in user.roles:
        if not role.enabled:
            continue
        for res in role.resources:
            if res.enabled and res.permission_code:
                codes.add(res.permission_code)
    return codes
```

> **N+1 警示**：原 JPA 靠懒加载，存在潜在 N+1。Python 版**必须 `selectinload` 两层抓全**（`User.roles.resources`），权限解析是每次 `/api/auth/me` 和首页过滤都走的 hot path。

### 4.1 菜单树构建（`build_menu_tree`）— 排除 ACTION

```python
def build_menu_tree(resources: list[Resource]) -> list[MenuNode]:
    # 仅 enabled + 非 ACTION；按 sort_no, id 排序；按 parent_id 组装树
    nodes = [r for r in resources if r.enabled and r.resource_type != ResourceType.ACTION]
    nodes.sort(key=lambda r: (r.sort_no, r.id))
    children: dict[int | None, list[Resource]] = {}
    for r in nodes:
        children.setdefault(r.parent_id, []).append(r)
    def build(parent_id):
        return [MenuNode(
            id=r.id, name=r.name, route_name=r.route_name, path=r.path,
            component_key=r.component_key, icon=r.icon, resource_type=r.resource_type,
            children=build(r.id)) for r in children.get(parent_id, [])]
    return build(None)
```

**关键**：`resource_type != ACTION` 这一行保证 managed ACTION 永远不进菜单树——这是双层设计的隔离点。

---

## 五、第一层：菜单/页面授权（人工资源）

### 5.1 资源 CRUD 守卫（`services/resource_service.py`）

复刻原 `ResourceService.validateRequest` 的核心约束——**人工不得创建/编辑 ACTION**：

```python
def validate_resource(req: SaveResourceRequest, rid: int | None):
    if req.resource_type == ResourceType.ACTION:
        raise BizError("历史 ACTION 权限节点已停用，不允许再创建或编辑")
    if req.parent_id == rid:
        raise BizError("不能将自身设为父节点")
    # route_name / permission_code 唯一性校验（排除自身）
    ...
```

删除资源前还要校验"无子节点"+ 解除所有角色绑定（对应原 `ResourceService.delete`）。

### 5.2 角色授权（`services/role_service.py`）

角色的"授权"就是设置 `role.resources`：

```python
async def save_role(rid, req: SaveRoleRequest, session):
    role = ...  # load or new
    if not req.resource_ids:
        raise BizError("角色至少需要一个资源")        # 对应 ROLE_RESOURCE_REQUIRED
    resources = await session.scalars(select(Resource).where(Resource.id.in_(req.resource_ids)))
    role.resources = list(resources)                   # 全量替换 = 重新授权
    session.add(role)
```

**这就是"授权"的全部**：无论菜单资源还是 AI managed ACTION 资源，都是改 `role.resources`，走同一个保存接口。前端 `RoleManagementView.vue` 提交的 `resourceIds` 里可以混有两类资源。

---

## 六、第二层：AI 授权（managed ACTION）— 最关键

复刻原 `HomeConfigService` 的派生 + sync 机制。AI 业务/场景（`home_biz_module` / `home_biz_route`）是权限点的**来源**，每次增删改都要把对应 managed ACTION 资源同步到 `sys_resource`。

### 6.1 派生规则（常量，与原 Java 逐字一致）

```python
MOD_CODE_PREFIX = "ai:home-config:module:"     # + {id} + ":view"
ROUTE_CODE_PREFIX = "ai:home-config:route:"    # + {id} + ":view"
MOD_ROUTE_NAME_PREFIX = "home-config-module-permission-"
ROUTE_ROUTE_NAME_PREFIX = "home-config-route-permission-"
HOME_CONFIG_ROUTE_NAME = "system-home-config"  # managed ACTION 的根挂载点

def module_permission_code(module_id: int) -> str:
    return f"{MOD_CODE_PREFIX}{module_id}:view"
def route_permission_code(route_id: int) -> str:
    return f"{ROUTE_CODE_PREFIX}{route_id}:view"
```

### 6.2 sync 机制（`services/home_config_service.py`）

```python
async def sync_managed_action(session, parent_id, route_name, name, code, sort_no):
    res = await session.scalar(select(Resource).where(Resource.route_name == route_name))
    if not code:                                   # 无 code → 删除已有
        if res:
            await detach_and_delete(session, res)
        return
    if res is None:                                # 新建: 隐藏 + 启用
        res = Resource(visible=False, enabled=True)
    res.name, res.resource_type = name, ResourceType.ACTION
    res.parent_id, res.route_name = route_name, route_name
    res.permission_code, res.sort_no = code, sort_no
    res.path = res.component_key = res.icon = None
    session.add(res); await session.flush()
    await ensure_super_admin_grant(session, res)   # 超管自动持有

async def ensure_super_admin_grant(session, res):
    sa = await session.scalar(select(Role).where(Role.code == SUPER_ADMIN))
    if sa and res not in sa.resources:
        sa.resources.append(res)

# 模块增删改时:
async def on_module_save(module, session):
    module.permission_code = module_permission_code(module.id)
    await sync_managed_action(session,
        parent_id=await home_config_resource_id(session),
        route_name=f"{MOD_ROUTE_NAME_PREFIX}{module.id}",
        name=f"AI业务：{module.name}", code=module.permission_code,
        sort_no=3000 + module.default_order)
```

> **排序约定**（与原 Java 一致）：模块级 ACTION `sort_no = 3000 + default_order`；场景级 `6000 + default_order`；场景 ACTION 的 `parent_id` 挂到所属模块的 managed ACTION 资源上，形成 `首页配置根 → AI业务 → AI场景` 的隐藏树。

### 6.3 启动幂等初始化（`HomeConfigPermissionBootstrap` 等价）

原 Java 用 `CommandLineRunner @Order(1)`。Python 用 FastAPI 的 `lifespan` 启动钩子：

```python
# app/main.py
@asynccontextmanager
async def lifespan(app):
    async with AsyncSession() as session:
        await seed_bootstrap(session)          # 见第七节, 先跑
        await ensure_managed_actions(session)  # 遍历所有 module/route 补齐
    yield

async def ensure_managed_actions(session):
    for m in await all_modules(session):
        await on_module_save(m, session)
    for r in await all_routes(session):
        await on_route_save(r, session)
```

### 6.4 首页按权限过滤（消费侧）

`GET /api/homepage` 构建 AI 工作台时，用当前用户权限集合过滤：

```python
async def homepage_config(user_id, session):
    perms = await load_permissions(session, user_id)
    modules = [m for m in await enabled_modules(session)
               if has_access(m.permission_code, perms)]      # code 空→放行; 否则需在集合内
    # 路由再按所属模块 + route.permission_code 二次过滤
    ...

def has_access(code, perms) -> bool:
    return not code or code in perms
```

**用户没被授权的 AI 业务/场景，首页根本不返回**——与原 `HomeConfigService.getHomepageConfig` 行为一致。

---

## 七、初始化 / 种子（`db/seed.py`）

对应原 `DataInitializer`，启动自举 SUPER_ADMIN + admin + 基础资源：

```python
async def seed_bootstrap(session):
    resources = ensure_builtin_resources(session)   # 按 route_name upsert 全部菜单/页面
    sa = await ensure_role(session, "SUPER_ADMIN", "超级管理员")
    sa.resources = list({*sa.resources, *resources})# 超管持有全部基础资源
    # managed ACTION 在启动钩子里补，不在这里
    admin = await ensure_user(session, "admin", "admin123456", display="系统管理员")
    admin.roles = [sa]
```

基础资源清单直接照搬原 `DataInitializer.ensureResources()` 的 22 条（dashboard、各 AI/知识/NL2SQL/系统管理页面），`route_name`/`permission_code`/`path`/`component_key`/`icon`/`sort_no` 原样保留——前端 `componentRegistry` 靠 `component_key` 解析组件，**改了就对不上**。

---

## 八、前端层（照搬，仅删微信）

当前 `code/frontend/lone-ai-web/` 的 Vue3 技术栈**整体保留**：Vue3 + TS + Vite + Pinia + Vue Router + 自研 `Ui*` 组件 + `ant-design/lucide` 图标。

**可直接拷贝、几乎零改动的文件**：

| 文件 | 作用 | 改动 |
|---|---|---|
| `src/stores/auth.ts` | token + currentUser + permissions(Set) + 动态路由 | 删微信相关 import 即可 |
| `src/router/index.ts` | 路由守卫 + `meta.permission` 校验 | 无 |
| `src/views/system/RoleManagementView.vue` | 角色/双授权树（菜单树 + AI授权树） | 无 |
| `src/components/PermissionTreeNode.vue` | 通用授权树节点 | 无 |
| `src/views/system/ResourceManagementView.vue` | 菜单配置（人工资源 CRUD） | 无 |
| `src/views/system/UserManagementView.vue` | 用户管理 | 无 |
| `src/lib/http.ts` | axios + `Authorization: Bearer` 注入 | 无 |
| `src/types.ts` | `CurrentUser`/`MenuNode` 类型 | 删微信字段 |

**必须删除**：
- `views/LoginView.vue` 里的微信登录入口与 `getWxLoginQrcode` 之类调用
- 所有 `wechat/mini` 相关 API 模块
- 微信相关的 store/env 配置

**前端判定 AI 授权树的逻辑照搬**（`RoleManagementView.vue:136-144`）：
```ts
const isAiManaged = (r) => r.resourceType === 'ACTION'
  && (r.permissionCode?.startsWith('ai:home-config:module:')
   || r.permissionCode?.startsWith('ai:home-config:route:'))
```
后端只要把 permission_code 前缀保持一致，前端这棵树自动正确。

---

## 九、API 契约对照（路径必须保持一致）

| 方法 路径 | 用途 | 备注 |
|---|---|---|
| POST `/api/auth/login` | 账号密码登录 | captcha + bcrypt |
| POST `/api/auth/logout` | 登出 | 删 Redis token |
| GET `/api/auth/captcha` | 图形验证码 | 返回 `{captchaId, image}` |
| GET `/api/auth/me` | 当前用户 + 权限 + 菜单树 | 核心，前端驱动源 |
| POST `/api/auth/dev-token` | 本机开发 token | 仅 `127.0.0.1` |
| ~~POST `/api/auth/wechat/mini/login`~~ | ~~微信登录~~ | **删除** |
| GET/POST/PUT/DELETE `/api/system/users` | 用户 CRUD | |
| GET/POST/PUT/DELETE `/api/system/roles` | 角色 CRUD | 含 `resource_ids` 全量授权 |
| GET/POST `/api/system/roles/{id}/users` | 角色-用户绑定/解绑 | |
| GET `/api/system/roles/options` | 启用角色下拉 | |
| GET/POST/PUT/DELETE `/api/system/resources` | 资源 CRUD | **禁止 ACTION 类型** |
| GET/POST/PUT/DELETE `/api/admin/home-config/*` | AI 业务/场景 CRUD | managed ACTION 派生源 |
| GET `/api/homepage` | 首页配置（按权限过滤） | |
| GET/POST `/api/home-preference` | 用户首页偏好 | 可后补 |

---

## 十、继承的设计约束（来自 V10–V17 的血泪）

复刻时**从第一天就 bake in**，不要等迁移再收敛：

1. **不做通用按钮级 ACTION 权限**。资源类型 ACTION 只服务 AI 授权，人工创建入口直接拒绝。
2. **后端控制器只校验登录态，不校验页面权限码**。页面可见性交给"前端路由由 `/api/auth/me` 的 menus 驱动 + 守卫 `meta.permission`"。后端一旦开始按页面码拦截，就会重新走上 V10–V13 之前那套细粒度泥潭。
3. **AI 授权不复用菜单授权的页面资源**，而是独立 managed ACTION。要恢复按钮级权限，新建独立模型，别碰 AI 的 managed ACTION（原 README:48-52 明确）。
4. **权限码是前后端唯一契约**，命名空间化：页面码 `<domain>:<entity>:page`、AI 码 `ai:home-config:{module|route}:{id}:view`。
5. **权限实时计算、无缓存**。改授权即生效的前提是不缓存；若未来加缓存，必须配合角色变更时的失效策略。

---

## 十一、建议目录结构

```
new-agent-framework/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                 # FastAPI app + lifespan(seed + managed ACTION 初始化)
│  │  ├─ core/{config,security,deps,errors}.py
│  │  ├─ db/{base,session,seed}.py
│  │  ├─ models/{user,role,resource,home}.py
│  │  ├─ schemas/{auth,user,role,resource,home}.py   # Pydantic DTO
│  │  ├─ services/{auth,permission,role,resource,home_config,captcha,audit}.py
│  │  └─ routers/{auth,users,roles,resources,home_config,health}.py
│  ├─ migrations/                # Alembic ( revisions 对应 V1/V10-V17 )
│  ├─ alembic.ini  requirements.txt
├─ frontend/                     # 从 lone-ai-web 拷贝, 删微信
└─ README.md                     # 记端口/runtimeId/日志路径(沿用原派生项目规范)
```

---

## 十二、分阶段实施计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0 骨架** | FastAPI + DB 连接 + Alembic 初始迁移建 5 表 + 健康检查 | `/api/health` 200；表结构与 V1 对齐 |
| **P1 认证** | bcrypt + opaque token(Redis) + login/logout/captcha/me + 全局登录中间件 + 白名单 | 能登录拿 token；`/api/auth/me` 返回结构正确；未带 token 访问受保护接口 401 |
| **P2 RBAC** | User/Role/Resource 模型 + CRUD + 权限解析 `load_permissions` + 菜单树 + ACTION 创建守卫 + 角色↔资源/用户绑定 | 给角色授权后 `/api/auth/me` 的 permissions/menus 变化正确；创建 ACTION 被拒 |
| **P3 种子** | `seed_bootstrap` + lifespan 启动钩子 | 首启自动出 `admin/admin123456` + 全部基础资源 + SUPER_ADMIN |
| **P4 AI 授权** | HomeBizModule/Route + 派生 managed ACTION + sync + 超管自动授权 + 首页按权限过滤 | 新建 AI 业务后 `sys_resource` 自动出现隐藏 ACTION；非授权用户首页看不到 |
| **P5 前端接入** | 拷贝 web 前端，删微信，对接后端 | 登录→菜单渲染→动态路由→角色双授权树→首页过滤全通 |
| **P6 收尾** | 审计日志、dev-token、错误码体系、单元自检 | 关键路径自检通过 |

每个 P 阶段留一个最小自检（`pytest` 或 `assert` 脚本）：P1 验 token 往返、P2 验权限解析、P4 验 sync 幂等。

---

## 十三、风险与取舍

- **opaque token vs JWT**：已选 opaque token 换取"实时生效"，代价是多一次 Redis 查询。可接受。
- **权限解析 hot path**：每次 `/api/auth/me` + 首页过滤都查 user→roles→resources。务必 `selectinload` 抓全；若成瓶颈，再加按 user_id 的短 TTL 缓存 + 角色变更时失效。
- **managed ACTION 与角色授权的耦合**：删 AI 业务/场景时记得 `detach_and_delete` 解除所有角色绑定，否则留孤儿关系。
- **前端 component_key 一致性**：后端种子的 `component_key` 必须与前端 `componentRegistry` 注册的 key 完全对应，否则动态路由注册不到组件。

---

## 附：与原 Java 事实源的映射

| 本蓝图 | 原 Java 事实源 |
|---|---|
| `permission_service.load_permissions` | `StpInterfaceImpl.getPermissionList` / `AuthService.currentUser` |
| `build_menu_tree` | `ResourceService.buildMenuTree` |
| 资源 ACTION 守卫 | `ResourceService.validateRequest` |
| `home_config_service.sync_managed_action` | `HomeConfigService.syncManagedActionResource` |
| 超管自动授权 | `HomeConfigService.ensureSuperAdminManagedResource` |
| 启动幂等 | `HomeConfigPermissionBootstrap` + `DataInitializer` |
| 前端 auth store / 路由守卫 | `stores/auth.ts` / `router/index.ts` |
| 前端双授权树 | `RoleManagementView.vue` (`permissionTree` / `aiPermissionTree`) |
