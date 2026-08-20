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

from hermes_constants import get_hermes_home

from server.deployment_config import load_deployment_config
from server.knowledge.chunker import count_tokens
from server.knowledge.parser_client import SUPPORTED_EXTS, parse_document
from server.storage import get_repository

logger = logging.getLogger(__name__)

MAX_FILES_PER_OWNER = 5
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB——全文注入模型的场景，再大的文件 token 也装不下

_UPLOADS_DIR = "uploads"
_OWNER_DIR_NAMES = {"session": "sessions", "task": "tasks"}


def owner_dir(user_id: str, owner_type: str, owner_id: str) -> Path:
    """$HERMES_HOME/uploads/<user_id>/<sessions|tasks>/<owner_id>/"""
    return (
        Path(get_hermes_home())
        / _UPLOADS_DIR
        / user_id
        / _OWNER_DIR_NAMES[owner_type]
        / owner_id
    )


def save_upload(
    user_id: str,
    owner_type: str,
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


def remove_file_files(record: dict) -> None:
    """删除单个上传文件的磁盘目录（原件 + 解析产物）。"""
    file_path = record.get("file_path") or ""
    if not file_path:
        return
    dest_dir = (Path(get_hermes_home()) / file_path).parent
    if dest_dir.parent == owner_dir(
        record["user_id"], record["owner_type"], record["owner_id"]
    ):
        shutil.rmtree(dest_dir, ignore_errors=True)


def remove_owner_files(user_id: str, owner_type: str, owner_id: str) -> None:
    """owner（session/task）删除时清掉整个上传目录。"""
    shutil.rmtree(owner_dir(user_id, owner_type, owner_id), ignore_errors=True)


# ------------------------------------------------------------- token 预算与截断

# 预算 = 模型上下文上限 − 历史消息 − system prompt/工具 schema 粗估 − 输出余量。
# 超预算不阻断发送（用户已确认）：从最新上传的文件开始截断/跳过并明示。
OUTPUT_RESERVE_TOKENS = 8192
SYSTEM_RESERVE_TOKENS = 4096

_TOKEN_ENCODING = None


def _encoding():
    global _TOKEN_ENCODING
    if _TOKEN_ENCODING is None:
        import tiktoken

        _TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
    return _TOKEN_ENCODING


def attachment_budget(history_tokens: int = 0) -> int:
    """附件全文可用的 token 预算（可为 0，绝不小于 0）。"""
    from server.runtime_config import load_runtime_config

    cap = load_runtime_config().max_input_tokens
    return max(0, cap - history_tokens - SYSTEM_RESERVE_TOKENS - OUTPUT_RESERVE_TOKENS)


def owner_history_tokens(user_id: str, owner_type: str, owner_id: str) -> int:
    """owner 已有对话历史的 token 粗估（注入新消息前的占用）。"""
    repository = get_repository()
    conversation_id = owner_id
    if owner_type == "task":
        task = repository.get_owned_task(user_id, owner_id)
        if task is None:
            return 0
        conversation_id = task["session_id"]
    messages = repository.get_messages(conversation_id)
    return sum(count_tokens(str(m.get("content") or "")) for m in messages)


def _truncate_to_tokens(text: str, limit: int) -> str:
    tokens = _encoding().encode(text)
    if len(tokens) <= limit:
        return text
    return _encoding().decode(tokens[: max(0, limit)])


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


def budget_summary(user_id: str, owner_type: str, owner_id: str) -> dict:
    """GET /uploads 附带的预算用量：前端据此渲染超预算警告（不阻断）。"""
    from server.runtime_config import load_runtime_config

    records = get_repository().list_uploaded_files(user_id, owner_type, owner_id)
    file_tokens = sum(
        int(r.get("token_count") or 0) for r in records if r.get("parse_status") == "ready"
    )
    budget = attachment_budget(owner_history_tokens(user_id, owner_type, owner_id))
    return {
        "max_input_tokens": load_runtime_config().max_input_tokens,
        "budget_tokens": budget,
        "file_tokens": file_tokens,
        "over_budget": file_tokens > budget,
    }
