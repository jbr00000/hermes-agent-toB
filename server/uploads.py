"""临时上传文件（chat/agent 附件）的落盘与解析。

与知识库构建管线的区别：知识库文档是租户级长期资产（分块 + ES/Milvus
索引 + 专属 worker 进程）；上传文件是单次会话/任务的临时上下文——
**只解析不分块**，全文直接注入模型消息，随 owner（session/task）删除。

解析复用知识库的 parser_client（txt/md 本地直读、xlsx openpyxl、
pdf/office 走客户自托管 MinerU）。不依赖知识库开关：knowledge.enabled
只控制知识库路由/worker，这里只读 deployment.yaml 里的 mineru/soffice
配置；未配置 MinerU 的部署上 PDF/Office 会以 failed + 明确错误收尾，
txt/md/xlsx 不受影响。

解析在 server 进程内的后台线程执行（单文件、秒级到分钟级），不像知识库
那样引入独立 worker——附件上传是交互路径上的同步等待场景。
"""
from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Literal

from hermes_constants import get_hermes_home

from server.deployment_config import load_deployment_config
from server.knowledge.chunker import count_tokens, token_encoding
from server.knowledge.parser_client import SUPPORTED_EXTS, parse_document
from server.storage import get_repository

logger = logging.getLogger(__name__)

MAX_FILES_PER_OWNER = 5
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB——全文注入模型的场景，再大的文件 token 也装不下

OwnerType = Literal["session", "task"]

_UPLOADS_DIR = "uploads"
_OWNER_DIR_NAMES: dict[OwnerType, str] = {"session": "sessions", "task": "tasks"}


class UploadLimitError(Exception):
    """批量保存时数量超上限；existing 供路由层拼错误文案。"""

    def __init__(self, owner_type: OwnerType, existing: int) -> None:
        super().__init__(
            f"每个{'会话' if owner_type == 'session' else '任务'}最多上传 "
            f"{MAX_FILES_PER_OWNER} 个文件（已有 {existing} 个）"
        )
        self.existing = existing


def owner_dir(user_id: str, owner_type: OwnerType, owner_id: str) -> Path:
    """$HERMES_HOME/uploads/<user_id>/<sessions|tasks>/<owner_id>/"""
    return (
        Path(get_hermes_home())
        / _UPLOADS_DIR
        / user_id
        / _OWNER_DIR_NAMES[owner_type]
        / owner_id
    )


# 数量上限检查与落盘必须在同一把锁里（单 API 进程部署），否则两个并发请求
# 可各通过 count 检查、合计突破 MAX_FILES_PER_OWNER
_SAVE_LOCK = threading.Lock()


def save_uploads(
    user_id: str,
    owner_type: OwnerType,
    owner_id: str,
    files: list[tuple[str, bytes]],
) -> list[dict]:
    """锁内检查数量上限并批量落盘 + 派发解析。files 为 (文件名, 内容)，须已校验。"""
    with _SAVE_LOCK:
        existing = get_repository().count_uploaded_files(owner_type, owner_id)
        if existing + len(files) > MAX_FILES_PER_OWNER:
            raise UploadLimitError(owner_type, existing)
        return [
            save_upload(user_id, owner_type, owner_id, file_name, content)
            for file_name, content in files
        ]


def save_upload(
    user_id: str,
    owner_type: OwnerType,
    owner_id: str,
    file_name: str,
    content: bytes,
) -> dict:
    """建 parsing 记录 + 原件落盘 + 派发后台解析。返回记录 dict。"""
    repository = get_repository()
    file_ext = Path(file_name).suffix.lower()
    file_id = str(uuid.uuid4())
    dest_dir = owner_dir(user_id, owner_type, owner_id) / file_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    original = dest_dir / f"original{file_ext}"
    original.write_bytes(content)
    record = repository.create_uploaded_file(
        user_id,
        file_id=file_id,
        owner_type=owner_type,
        owner_id=owner_id,
        file_name=file_name,
        file_ext=file_ext,
        size_bytes=len(content),
        file_path=original.relative_to(Path(get_hermes_home())).as_posix(),
    )
    _dispatch_parse(file_id)
    # 回读一次：生产上后台线程刚启动，多半仍是 parsing；测试里 _dispatch_parse
    # 被 patch 成同步执行，回读让响应直接反映解析结果。
    return repository.get_uploaded_file(file_id) or record


def _dispatch_parse(file_id: str) -> None:
    """默认后台线程解析；测试可 monkeypatch 为同步执行。"""
    threading.Thread(target=parse_upload, args=(file_id,), daemon=True).start()


def parse_upload(file_id: str) -> None:
    """解析一个上传文件：成功写 parsed.md + token 数，失败记 parse_error。"""
    repository = get_repository()
    record = repository.get_uploaded_file(file_id)
    if record is None:
        return
    home = Path(get_hermes_home())
    original = home / record["file_path"]
    dest_dir = original.parent
    parsed = dest_dir / "parsed.md"
    try:
        config = load_deployment_config().knowledge
        doc = parse_document(original, record["file_ext"], config)
        parsed.write_text(doc.content_md, encoding="utf-8")
        repository.update_uploaded_file_parse(
            file_id,
            status="ready",
            parser=doc.parser,
            token_count=count_tokens(doc.content_md),
            parsed_path=parsed.relative_to(home).as_posix(),
        )
    except Exception as exc:  # ParseError 或意外错误都归为 failed，错误展示给用户
        logger.warning("upload parse failed: %s (%s): %s", file_id, record["file_name"], exc)
        repository.update_uploaded_file_parse(file_id, status="failed", error=str(exc))


def read_parsed_text(record: dict) -> str:
    """读取解析全文；未 ready 或产物缺失返回空串（调用方据此跳过注入）。"""
    if record.get("parse_status") != "ready" or not record.get("parsed_path"):
        return ""
    parsed = Path(get_hermes_home()) / record["parsed_path"]
    try:
        return parsed.read_text(encoding="utf-8")
    except OSError:
        return ""


def remove_upload_disk_files(record: dict) -> None:
    """删除单个上传文件的磁盘目录（原件 + 解析产物 + 沙箱里的暂存副本）。"""
    file_path = record.get("file_path") or ""
    if file_path:
        dest_dir = (Path(get_hermes_home()) / file_path).parent
        if dest_dir.parent == owner_dir(
            record["user_id"], record["owner_type"], record["owner_id"]
        ):
            shutil.rmtree(dest_dir, ignore_errors=True)
    if record.get("owner_type") == "task":
        try:
            from server.sandbox import task_workspace_dir

            staged = (
                task_workspace_dir(record["user_id"], record["owner_id"], create=False)
                / "uploads"
                / f"{record['id']}{record['file_ext']}"
            )
            staged.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass


def remove_owner_disk_files(user_id: str, owner_type: OwnerType, owner_id: str) -> None:
    """owner（session/task）删除时清掉整个上传目录。"""
    shutil.rmtree(owner_dir(user_id, owner_type, owner_id), ignore_errors=True)


def stage_task_uploads(user_id: str, task_id: str) -> list[str]:
    """把任务附件的**原始文件**复制进沙箱任务工作区的 uploads/ 子目录。

    宿主机侧直拷（工作区是绑定挂载，容器内即 /workspace/tasks/<id>/uploads/），
    execute 阶段模型可用 terminal/read_file 直接读取原始二进制（xlsx 等
    解析后丢格式的格式尤其需要）。幂等；返回容器内相对路径列表。
    交付文件列表（GET /tasks/{id}/artifacts）排除 uploads/ 顶层目录。
    """
    from server.sandbox import task_workspace_dir

    records = get_repository().list_uploaded_files(user_id, "task", task_id)
    if not records:
        return []
    dest_dir = task_workspace_dir(user_id, task_id) / "uploads"
    staged: list[str] = []
    home = Path(get_hermes_home())
    for record in records:
        source = home / record["file_path"]
        if not source.is_file():
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 用「文件id+原扩展名」命名，避免中文文件名/重名在容器工具链里的麻烦；
        # 原始文件名在注入文本和 prompt 里另行告知模型
        dest = dest_dir / f"{record['id']}{record['file_ext']}"
        if not dest.exists() or dest.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, dest)
        staged.append(f"uploads/{dest.name}")
    return staged


# ------------------------------------------------------------- token 预算与截断

# 预算 = 模型上下文上限 − 历史消息 − system prompt/工具 schema 粗估 − 输出余量。
# 超预算不阻断发送（用户已确认）：从最新上传的文件开始截断/跳过并明示。
OUTPUT_RESERVE_TOKENS = 8192
SYSTEM_RESERVE_TOKENS = 4096


def attachment_budget(history_tokens: int = 0, max_input_tokens: int | None = None) -> int:
    """附件全文可用的 token 预算（可为 0，绝不小于 0）。

    max_input_tokens 缺省时从 runtime_config 读；调用方已持有配置（如
    budget_summary 一次请求多处使用）时传入，避免重复加载。"""
    if max_input_tokens is None:
        from server.runtime_config import load_runtime_config

        max_input_tokens = load_runtime_config().max_input_tokens
    return max(0, max_input_tokens - history_tokens - SYSTEM_RESERVE_TOKENS - OUTPUT_RESERVE_TOKENS)


def _messages_token_sum(messages: list[dict]) -> int:
    """一组消息 content 的 token 粗估（历史占用，供预算/注入共用）。"""
    return sum(count_tokens(str(m.get("content") or "")) for m in messages)


def owner_history_tokens(user_id: str, owner_type: OwnerType, owner_id: str) -> int:
    """owner 已有对话历史的 token 粗估（注入新消息前的占用）。"""
    repository = get_repository()
    conversation_id = owner_id
    if owner_type == "task":
        task = repository.get_owned_task(user_id, owner_id)
        if task is None:
            return 0
        conversation_id = task["session_id"]
    return _messages_token_sum(repository.get_messages(conversation_id))


def _truncate_to_tokens(text: str, limit: int) -> str:
    encoding = token_encoding()
    tokens = encoding.encode(text)
    if len(tokens) <= limit:
        return text
    return encoding.decode(tokens[: max(0, limit)])


def build_attachment_block(
    records: list[dict], budget_tokens: int
) -> tuple[str, list[dict]]:
    """把 ready 附件的全文拼成注入块，按预算从最新上传的开始截断。

    records 须按上传时间升序（list_uploaded_files 的默认序）。返回
    (注入文本, 每文件使用明细)；明细里 status ∈ full | truncated | skipped，
    前端 chip 与警告条据此展示。解析中/失败的附件不参与注入也不占预算。
    """
    ready = [r for r in records if r.get("parse_status") == "ready"]
    if not ready:
        return "", []

    parts: list[str] = []
    usage: list[dict] = []
    remaining = max(0, budget_tokens)
    total = len(ready)
    for index, record in enumerate(ready, start=1):
        text = read_parsed_text(record)
        tokens = int(record.get("token_count") or 0) or count_tokens(text)
        header = f"【附件 {index}/{total}：{record['file_name']}】"
        if not text:
            usage.append(_usage(record, tokens, 0, "skipped"))
            parts.append(f"{header}（解析产物缺失，未注入内容）")
            continue
        if tokens <= remaining:
            usage.append(_usage(record, tokens, tokens, "full"))
            parts.append(f"{header}\n{text}")
            remaining -= tokens
        elif remaining > 0:
            cut = _truncate_to_tokens(text, remaining)
            usage.append(_usage(record, tokens, remaining, "truncated"))
            parts.append(
                f"{header}（已截断，仅展示前 {remaining} tokens，原文共 {tokens} tokens）\n{cut}"
            )
            remaining = 0
        else:
            usage.append(_usage(record, tokens, 0, "skipped"))
            parts.append(f"{header}（上下文预算不足，未注入内容）")
    return "\n\n".join(parts), usage


def _usage(record: dict, tokens: int, included: int, status: str) -> dict:
    return {
        "id": record["id"],
        "file_name": record["file_name"],
        "token_count": tokens,
        "included_tokens": included,
        "status": status,
    }


def budget_summary(user_id: str, owner_type: OwnerType, owner_id: str) -> dict:
    """GET /uploads 附带的预算用量：前端据此渲染超预算警告（不阻断）。"""
    from server.runtime_config import load_runtime_config

    max_input_tokens = load_runtime_config().max_input_tokens
    records = get_repository().list_uploaded_files(user_id, owner_type, owner_id)
    file_tokens = sum(
        int(r.get("token_count") or 0) for r in records if r.get("parse_status") == "ready"
    )
    budget = attachment_budget(
        owner_history_tokens(user_id, owner_type, owner_id), max_input_tokens
    )
    return {
        "max_input_tokens": max_input_tokens,
        "budget_tokens": budget,
        "file_tokens": file_tokens,
        "over_budget": file_tokens > budget,
    }


# ------------------------------------------------------------- 消息注入（chat/plan）

def prepare_attachment_injection(
    user_id: str,
    owner_type: OwnerType,
    owner_id: str,
    prior_messages: list[dict],
    base_text: str,
) -> tuple[str, dict | None]:
    """把尚未注入过的 ready 附件全文拼进当前用户消息。返回 (文本, 消息 metadata)。

    按文件幂等：已注入的文件（历史消息 metadata.attachments 里有 id）不再重复
    注入——它们已躺在会话历史的固定位置上，后续轮次靠前缀缓存命中，既省
    token 又不违反「不改写历史消息」不变量。对话中途新传/刚解析完的文件在
    下一轮补注。预算从**本批**新文件里最新的开始截断（老文件已在历史中）。

    调用方把返回文本同时用于「发给模型」和「落库」（两者一致，恢复会话时
    附件内容随历史重放）；metadata.display_content 存用户原文供前端气泡
    展示，前端不认识该字段时退化为显示全文（可接受的降级）。
    """
    records = [
        r
        for r in get_repository().list_uploaded_files(user_id, owner_type, owner_id)
        if r.get("parse_status") == "ready"
    ]
    if not records:
        return base_text, None
    injected_ids = {
        item["id"]
        for message in prior_messages
        for item in ((message.get("metadata") or {}).get("attachments") or [])
        if isinstance(item, dict) and item.get("id")
    }
    pending = [r for r in records if r["id"] not in injected_ids]
    if not pending:
        return base_text, None

    history_tokens = _messages_token_sum(prior_messages)
    budget = attachment_budget(history_tokens + count_tokens(base_text))
    block, usage = build_attachment_block(pending, budget)
    if not block:
        return base_text, None
    text = (
        f"{base_text}\n\n"
        f"（用户随消息上传了以下文件的全文，作为本轮及后续对话的上下文；"
        f"回答与这些文件相关的问题时直接依据其内容：\n{block}\n）"
    )
    return text, {"attachments": usage, "display_content": base_text}


def task_uploads_note(user_id: str, task_id: str, container_cwd: str) -> str | None:
    """execute 阶段：把任务附件原件暂存进沙箱工作区并生成给模型的位置说明。

    返回 None 表示该任务没有附件。说明只发给模型（不落库——重试时会按
    当前附件集合重新生成，且 execute 落库的 user 消息是占位文案）。
    """
    staged = stage_task_uploads(user_id, task_id)
    if not staged:
        return None
    records = get_repository().list_uploaded_files(user_id, "task", task_id)
    by_staged_name = {f"{r['id']}{r['file_ext']}": r for r in records}
    lines = []
    for rel in staged:
        record = by_staged_name.get(rel.rsplit("/", 1)[-1])
        original = record["file_name"] if record else rel
        lines.append(f"- {container_cwd}/{rel}（原始文件名：{original}）")
    return (
        "用户随任务上传了原始文件，已放入本任务工作区（终端 cwd 的子目录）：\n"
        + "\n".join(lines)
        + "\n需要按原始格式精确处理（如 xlsx 的单元格、pdf 的版面）时，"
        "用终端/文件工具直接读取这些文件，不要凭注入的文本转述猜测格式细节。"
    )


def with_task_uploads_note(
    message: str, user_id: str, task_id: str, container_cwd: str
) -> str:
    """execute 阶段模型消息：有任务附件则在尾部追加原件位置说明，没有则原样返回。

    chat 路由与 worker（agent_execution）共用这一入口，避免两处各自拼 note。"""
    note = task_uploads_note(user_id, task_id, container_cwd)
    return f"{message}\n\n{note}" if note else message
