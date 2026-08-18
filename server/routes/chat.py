"""Authenticated SSE Chat/Agent endpoint with durable conversation storage."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
import time
import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sse_starlette.sse import EventSourceResponse

from server import auth
from server.deps import get_current_user
from server.storage import get_repository, get_runtime_store
from server.tool_events import sanitize_tool_event_payload, tool_risk_level

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    interaction_type: Literal["chat", "agent"] | None = None
    mode: Optional[str] = None
    # 知识库问答模式（interaction_type=chat + mode=knowledge）可选的选库限定
    kb_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    # 知识库问答的检索模式：fast=单次融合检索（默认）；precise=轻量模型
    # 指代消解 + 后续编排能力（未配置辅助模型时自动降级 fast）
    search_mode: Optional[Literal["fast", "precise"]] = None


_active_agents: dict[str, tuple[str, object]] = {}
_active_agents_lock = threading.Lock()

def _tool_result_failed(result: Any) -> bool:
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return False
    if not isinstance(parsed, dict):
        return False
    status = str(parsed.get("status") or "").strip().lower()
    exit_code = parsed.get("exit_code")
    return (
        status in {"error", "failed", "failure"}
        or bool(parsed.get("error"))
        or (isinstance(exit_code, int) and exit_code != 0)
    )


def _agent_runtime_metadata(agent, mode: str | None) -> dict:
    return {
        "provider": getattr(agent, "provider", None),
        "model": getattr(agent, "model", None),
        "reasoning_config": getattr(agent, "reasoning_config", None),
        "enabled_toolsets": list(getattr(agent, "enabled_toolsets", None) or []),
        "mode": mode or "chat",
    }


_CITATION_SNIPPET_CHARS = 200


def _extract_citations(result: Any) -> list[dict[str, Any]]:
    """Parse a knowledge_search tool result into citation cards (前端引用卡片).

    Deterministic companion of the model's 【N】 markers: the model cites by
    ``num``; the UI renders these cards regardless of what the model wrote.
    """
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(parsed, dict):
        return []
    chunks = parsed.get("chunks")
    if not isinstance(chunks, list):
        return []
    citations: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        citations.append(
            {
                "num": chunk.get("num"),
                "chunk_id": chunk.get("chunk_id"),
                "doc_id": chunk.get("doc_id"),
                "doc_name": chunk.get("doc_name") or "",
                "chunk_title": chunk.get("chunk_title") or "",
                "snippet": str(chunk.get("content") or "")[:_CITATION_SNIPPET_CHARS],
                "score": chunk.get("score"),
            }
        )
    return citations


# 全角【数字】：knowledge 模式 RAG prompt 强制的引用格式。全角括号 + 纯数字在
# 中文正文、代码、markdown 里几乎不会自然出现（半角 [0] / [text](url) 不匹配），
# 误匹配率约等于零。支持【1】【1】【3】【1,3】【1、3】等连标写法。
_CITATION_REF_PATTERN = re.compile(r"【(\d+(?:\s*[,、]\s*\d+)*)】")


def _cited_nums(text: str) -> set[int]:
    """提取回答文本里全部【N】引用编号（模型声称的依据集合）。"""
    nums: set[int] = set()
    for match in _CITATION_REF_PATTERN.finditer(text):
        nums.update(
            int(part) for part in re.split(r"[,、\s]+", match.group(1)) if part.isdigit()
        )
    return nums


def _interrupt_local_agent(request_id: str, user_id: str) -> bool:
    with _active_agents_lock:
        active = _active_agents.get(request_id)
    if active is None or active[0] != user_id:
        return False
    interrupt = getattr(active[1], "interrupt", None)
    if callable(interrupt):
        interrupt("cancelled by user")
    return True


@router.post("/chat")
async def chat(req: ChatRequest, request: Request, user: dict = Depends(get_current_user)):
    repository = get_repository()
    runtime_store = get_runtime_store()
    user_id = user["id"]
    # Legacy callers that send plan/execute without interaction_type remain
    # Agent calls. New browser clients must declare Chat explicitly.
    interaction_type = req.interaction_type or ("agent" if req.mode else "chat")

    # Per-user feature gate — must run before ANY storage side effect below,
    # otherwise a rejected call would still leave behind an empty conversation.
    required_feature = "agent" if interaction_type == "agent" else "chat"
    if not auth.user_features(user).get(required_feature, True):
        raise HTTPException(
            status_code=403,
            detail=f"feature '{required_feature}' is disabled for this user",
        )

    # 知识库问答模式：chat 交互 + mode=knowledge（+ 可选 kb_id 选库限定）。
    # 校验全部前置——被拒的请求不能留下任何存储副作用。
    requested_knowledge_mode = (
        interaction_type == "chat" and (req.mode or "").strip().lower() == "knowledge"
    )
    if req.kb_id and not requested_knowledge_mode:
        raise HTTPException(status_code=400, detail="kb_id 仅知识库问答模式可用")
    if req.search_mode and not requested_knowledge_mode:
        raise HTTPException(status_code=400, detail="search_mode 仅知识库问答模式可用")
    knowledge_base: dict[str, Any] | None = None
    knowledge_cfg = None
    search_mode = "fast"
    if requested_knowledge_mode:
        if not auth.user_features(user).get("knowledge", True):
            raise HTTPException(
                status_code=403, detail="feature 'knowledge' is disabled for this user"
            )
        from server.deployment_config import load_deployment_config

        knowledge_cfg = load_deployment_config().knowledge
        if not knowledge_cfg.enabled:
            raise HTTPException(status_code=409, detail="知识库未启用")
        if req.kb_id:
            knowledge_base = repository.get_knowledge_base(req.kb_id)
            if knowledge_base is None:
                raise HTTPException(status_code=404, detail="知识库不存在")
        from server.knowledge.aux_llm import aux_llm_configured
        from server.knowledge.request_context import normalize_search_mode

        search_mode = normalize_search_mode(req.search_mode)
        if search_mode == "precise" and not aux_llm_configured(knowledge_cfg):
            # 未配置辅助模型：精准模式没有改写/编排能力可挂，静默降级快速
            logger.info("search_mode=precise 但 aux_llm 未配置，降级为 fast")
            search_mode = "fast"

    if req.session_id:
        from server.sessions import assert_session_owned

        assert_session_owned(user_id, req.session_id)
        session_id = req.session_id
        existing = repository.get_conversation(session_id)
        if existing is None:
            repository.ensure_conversation(
                session_id, user_id, source="headless", interaction_type=interaction_type
            )
        elif existing.get("interaction_type") != interaction_type:
            raise HTTPException(status_code=409, detail="session interaction type mismatch")
    else:
        session_id = repository.create_conversation(
            user_id, interaction_type=interaction_type, source="headless"
        )["id"]

    if interaction_type == "chat":
        if requested_knowledge_mode:
            effective_mode = "knowledge"
            mode_state = {
                "state": "knowledge",
                "tool_mode": "knowledge",
                "search_mode": search_mode,
            }
        else:
            effective_mode = "chat"
            mode_state = {"state": "chat", "tool_mode": "chat"}
    else:
        from server.sessions import resolve_chat_mode

        mode_state = resolve_chat_mode(user_id, session_id, req.mode)
        effective_mode = mode_state["tool_mode"]

    task = (
        repository.get_task_by_conversation(user_id, session_id)
        if interaction_type == "agent"
        else None
    )
    permission = (
        repository.get_task_permission(user_id, task["id"])
        if task is not None
        else {"mode": "read"}
    )
    permission_mode = "read" if effective_mode in {"chat", "plan", "knowledge"} else permission["mode"]
    sandbox_task_id = None
    if task is not None:
        from server.sandbox import task_sandbox_key

        sandbox_task_id = task_sandbox_key(user_id, task["id"])

    request_id = req.request_id or str(uuid.uuid4())
    lock_token = runtime_store.acquire_conversation(session_id)
    if lock_token is None:
        raise HTTPException(status_code=409, detail="session already has a running response")
    try:
        repository.create_model_run(request_id, user_id, session_id)
    except IntegrityError as exc:
        runtime_store.release_conversation(session_id, lock_token)
        raise HTTPException(status_code=409, detail="request_id already exists") from exc
    if task is not None:
        try:
            repository.create_task_run(request_id, user_id, task["id"], effective_mode)
        except RuntimeError as exc:
            repository.finish_model_run(request_id, status="failed", error=str(exc))
            runtime_store.release_conversation(session_id, lock_token)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception:
            repository.finish_model_run(request_id, status="failed", error="task run creation failed")
            runtime_store.release_conversation(session_id, lock_token)
            raise

    repository.update_conversation(user_id, session_id, status="running")
    runtime_store.mark_request(request_id, "running")
    snapshot_started_at = time.time()
    run_started_at = time.monotonic()
    runtime_store.save_chat_snapshot(
        request_id,
        session_id,
        "",
        0,
        started_at=snapshot_started_at,
    )

    event_queue: queue.Queue[object] = queue.Queue()
    sentinel = object()
    event_sequence = 0
    event_lock = threading.Lock()
    snapshot_lock = threading.Lock()
    snapshot_chunks: list[str] = []
    snapshot_last_saved_at = run_started_at
    stream_attached = threading.Event()
    stream_attached.set()
    agent_holder: dict[str, object] = {}
    tool_statuses: dict[str, str] = {}
    tool_started_at: dict[str, float] = {}
    tool_status_lock = threading.Lock()
    # knowledge_search 命中的分块 → 引用卡片（chunk_id 去重，保序；重复命中挪到末尾）
    citations_by_chunk: dict[str, dict[str, Any]] = {}

    def emit(event: str, data: dict) -> int:
        nonlocal event_sequence
        with event_lock:
            event_sequence += 1
            event_id = event_sequence
        if event != "delta":
            runtime_store.append_event(request_id, event_id, event, data)
        if stream_attached.is_set():
            event_queue.put(
                {
                    "id": str(event_id),
                    "event": event,
                    "data": json.dumps(data, ensure_ascii=False),
                }
            )
        return event_id

    def emit_tool_event(
        event_type: str,
        *,
        tool_name: str | None,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        if task is None:
            return
        stored = repository.record_tool_event(
            task_id=task["id"],
            run_id=request_id,
            event_type=event_type,
            tool_name=tool_name,
            risk_level=tool_risk_level(tool_name),
            status=status,
            payload=sanitize_tool_event_payload(event_type, tool_name, payload),
        )
        emit(
            event_type,
            {
                **stored,
                "task_id": task["id"],
                "request_id": request_id,
            },
        )

    def on_tool_start(tool_call_id: str, tool_name: str, display_args: Any) -> None:
        if tool_name.startswith("_"):
            return
        tool_started_at[tool_call_id] = time.monotonic()
        emit_tool_event(
            "tool.started",
            tool_name=tool_name,
            status="running",
            payload={"tool_call_id": tool_call_id, "arguments": display_args},
        )

    def on_tool_complete(
        tool_call_id: str,
        tool_name: str,
        display_args: Any,
        result: Any,
    ) -> None:
        if tool_name.startswith("_"):
            return
        failed = _tool_result_failed(result)
        with tool_status_lock:
            tool_statuses[tool_name] = "failed" if failed else "completed"
        started_at = tool_started_at.pop(tool_call_id, None)
        duration_ms = (
            max(0, int((time.monotonic() - started_at) * 1000))
            if started_at is not None
            else 0
        )
        emit_tool_event(
            "tool.completed",
            tool_name=tool_name,
            status="failed" if failed else "completed",
            payload={
                "tool_call_id": tool_call_id,
                "arguments": display_args,
                "result": result,
                "duration_ms": duration_ms,
            },
        )
        # 引用产出与 emit_tool_event 无关（chat 模式下后者提前 return）：
        # knowledge_search 一完成就推送当前累计的引用列表，前端流式渲染卡片。
        if tool_name == "knowledge_search" and not failed:
            for citation in _extract_citations(result):
                chunk_id = str(citation.get("chunk_id") or "")
                if not chunk_id:
                    continue
                citations_by_chunk.pop(chunk_id, None)
                citations_by_chunk[chunk_id] = citation
            if citations_by_chunk:
                emit(
                    "citations",
                    {
                        "chunks": list(citations_by_chunk.values()),
                        "request_id": request_id,
                    },
                )

    def on_tool_progress(*args: Any, **kwargs: Any) -> None:
        event_name = str(args[0]) if args else "tool.progress"
        if event_name != "tool.progress":
            return
        tool_name = str(args[1]) if len(args) > 1 else None
        if tool_name and tool_name.startswith("_"):
            return
        emit_tool_event(
            "tool.progress",
            tool_name=tool_name,
            status="running",
            payload={"arguments": list(args[2:]), "metadata": kwargs},
        )

    def on_delta(chunk: str) -> None:
        nonlocal snapshot_last_saved_at
        if runtime_store.is_cancelled(request_id):
            interrupt = getattr(agent_holder.get("agent"), "interrupt", None)
            if callable(interrupt):
                interrupt("cancelled by user")
            return
        if chunk:
            event_id = emit("delta", {"content": chunk, "request_id": request_id})
            snapshot_content: str | None = None
            with snapshot_lock:
                snapshot_chunks.append(chunk)
                now = time.monotonic()
                if now - snapshot_last_saved_at >= 0.2:
                    snapshot_content = "".join(snapshot_chunks)
                    snapshot_last_saved_at = now
            if snapshot_content is not None:
                runtime_store.save_chat_snapshot(
                    request_id,
                    session_id,
                    snapshot_content,
                    event_id,
                    started_at=snapshot_started_at,
                )

    def run_agent() -> None:
        runtime_metadata: dict = {"mode": effective_mode}
        try:
            from server.agent_factory import build_agent
            from server.audit import record_event
            from server.memory import save_memory_candidate

            prior_messages = repository.get_messages(session_id)
            history = [
                {"role": message["role"], "content": message["content"]}
                for message in prior_messages
            ]
            title = repository.get_owned_conversation(user_id, session_id)["title"]
            if not prior_messages:
                title = " ".join(req.message.split()).strip()[:40] or title
                repository.update_conversation(user_id, session_id, title=title)

            displayed_user_message = (
                "执行已批准计划"
                if task is not None and effective_mode == "execute"
                else req.message
            )
            user_message = repository.append_message(
                session_id,
                "user",
                displayed_user_message,
            )
            emit(
                "session",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "title": title,
                    "message": user_message,
                },
            )

            # 精准模式第 1 步：用会话历史把追问改写成独立检索问题（轻量模型，
            # 失败/无历史时返回原文）。改写结果不进 system prompt——它是每轮
            # 都变的内容，放系统提示会让缓存前缀逐轮失效（不变量 1）；改成缀在
            # 当前 user 轮次尾部（落库消息仍是用户原文），前缀缓存不受影响。
            knowledge_search_query = None
            if (
                effective_mode == "knowledge"
                and search_mode == "precise"
                and knowledge_cfg is not None
            ):
                from server.knowledge.query_rewrite import rewrite_query_with_history

                rewritten = rewrite_query_with_history(
                    req.message, history, config=knowledge_cfg
                )
                if rewritten and rewritten != req.message.strip():
                    knowledge_search_query = rewritten

            agent = build_agent(
                session_id=session_id,
                user_id=user_id,
                prefill_messages=history,
                mode=effective_mode,
                permission_mode=permission_mode,
                knowledge_kb_id=knowledge_base["id"] if knowledge_base else None,
                knowledge_kb_name=(
                    str(knowledge_base.get("name") or "") if knowledge_base else None
                ),
                tool_progress_callback=on_tool_progress,
                tool_start_callback=on_tool_start,
                tool_complete_callback=on_tool_complete,
            )
            agent_holder["agent"] = agent
            with _active_agents_lock:
                _active_agents[request_id] = (user_id, agent)

            runtime_metadata = _agent_runtime_metadata(agent, effective_mode)
            runtime_metadata["plan_state"] = mode_state.get("state")
            runtime_metadata["permission_mode"] = permission_mode
            model_config = {
                "provider": runtime_metadata.get("provider"),
                "reasoning_config": runtime_metadata.get("reasoning_config"),
                "enabled_toolsets": runtime_metadata.get("enabled_toolsets"),
                "mode": runtime_metadata.get("mode"),
                "plan_state": runtime_metadata.get("plan_state"),
            }
            repository.update_conversation_model(
                session_id,
                model=runtime_metadata.get("model"),
                model_config=model_config,
            )
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status="started",
                mode=effective_mode,
                metadata={**runtime_metadata, "request_id": request_id},
            )

            chat_kwargs: dict[str, Any] = {"stream_callback": on_delta}
            if sandbox_task_id is not None:
                chat_kwargs["task_id"] = sandbox_task_id
            # 本轮检索模式写入请求上下文（同线程的 knowledge_search 工具读取，
            # 不经模型透传）。非 knowledge 模式恒为 fast。
            from server.knowledge.request_context import set_search_mode

            set_search_mode(search_mode if effective_mode == "knowledge" else "fast")
            # 精准模式：把改写后的检索问题缀在当前 user 轮次尾部发给模型
            # （落库的 user 消息仍是用户原文）。放这里而不是 system prompt——
            # 系统提示必须全程字节稳定（不变量 3），逐轮变化的内容只能进当前轮。
            model_message = req.message
            if knowledge_search_query:
                model_message = (
                    f"{req.message}\n\n"
                    f"（知识库检索提示：该问题已结合对话历史明确为"
                    f"「{knowledge_search_query}」，调用 knowledge_search 时"
                    f"请使用该表述作为 query）"
                )
            # 快速模式：跳过"模型决定是否检索"的第一轮 LLM 调用——服务端直接
            # 检索并把分块注入当前 user 轮次尾部（与精准模式提示同一位置）。
            # 引用卡片与工具拦截路径共用 citations_by_chunk：此处产出的卡片
            # 立即推送给前端，后续【N】过滤/落库逻辑不变。模型确需补充检索时
            # 仍可自行调用 knowledge_search（工具结果继续走 on_tool_complete）。
            if (
                effective_mode == "knowledge"
                and search_mode == "fast"
                and knowledge_cfg is not None
            ):
                try:
                    from server.knowledge.retriever import search_chunks

                    fast_chunks = search_chunks(
                        req.message,
                        kb_id=knowledge_base["id"] if knowledge_base else None,
                        config=knowledge_cfg,
                    )
                except Exception as exc:
                    # 检索失败不判负：退化为模型自行调工具（工具层会把错误转成
                    # 可读的 tool_error，模型据此如实告知用户）
                    logger.warning(
                        "fast knowledge retrieval failed, falling back to tool path: %s", exc
                    )
                    fast_chunks = None
                if fast_chunks is not None:
                    if fast_chunks:
                        blocks: list[str] = []
                        for index, chunk in enumerate(fast_chunks, start=1):
                            chunk_id = str(chunk["chunk_id"])
                            citations_by_chunk.pop(chunk_id, None)
                            citations_by_chunk[chunk_id] = {
                                "num": index,
                                "chunk_id": chunk_id,
                                "doc_id": chunk["doc_id"],
                                "doc_name": chunk["doc_name"],
                                "chunk_title": chunk["chunk_title"],
                                "snippet": str(chunk.get("content") or "")[:_CITATION_SNIPPET_CHARS],
                                "score": chunk["score"],
                            }
                            source = f'《{chunk["doc_name"]}》'
                            if chunk.get("chunk_title"):
                                source += f'·{chunk["chunk_title"]}'
                            # 与工具侧一致的 4000 字符截断，防止超长块撑爆上下文
                            blocks.append(
                                f"【{index}】{source}\n{str(chunk['content'])[:4000]}"
                            )
                        model_message += (
                            "\n\n（知识库检索结果（已按相关度排序；严格基于以下内容回答，"
                            "引用时在句末标注对应编号【N】）：\n"
                            + "\n\n".join(blocks)
                            + "\n）"
                        )
                        # 检索一完成就推送引用卡片，随回答流式渲染（与工具路径一致）
                        emit(
                            "citations",
                            {
                                "chunks": list(citations_by_chunk.values()),
                                "request_id": request_id,
                            },
                        )
                    else:
                        model_message += "\n\n（知识库检索结果：未检索到相关内容）"
            final = agent.chat(model_message, **chat_kwargs) or ""
            cancelled = runtime_store.is_cancelled(request_id)
            with tool_status_lock:
                unresolved_tool_failure = any(
                    status == "failed" for status in tool_statuses.values()
                )
            assistant_status = (
                "cancelled" if cancelled else "failed" if unresolved_tool_failure else "completed"
            )
            duration_ms = max(0, int((time.monotonic() - run_started_at) * 1000))
            citations = list(citations_by_chunk.values())
            # 卡片与回答口径一致（仅 knowledge 模式，那里【N】是 prompt 硬规则）：
            # 只保留回答实际标注的来源。模型答"未找到"时不标号 → 卡片清空，
            # 不会出现"嘴上说没有、卡片却显示一堆"的自相矛盾。
            if citations and effective_mode == "knowledge":
                used_nums = _cited_nums(final)
                citations = [c for c in citations if c.get("num") in used_nums]
            assistant_message = repository.append_message(
                session_id,
                "assistant",
                final,
                status=assistant_status,
                model_run_id=request_id,
                duration_ms=duration_ms,
                metadata={"citations": citations} if citations else None,
            )
            if final and assistant_status == "completed":
                # Users with the memory feature off should not silently
                # accumulate memory candidates in the background.
                if auth.user_features(user).get("memory", True):
                    try:
                        save_memory_candidate(user_id, session_id, req.message, final)
                    except Exception:
                        logger.debug("Could not save memory candidate", exc_info=True)

            final_status = assistant_status
            if task is not None:
                if cancelled:
                    repository.finish_task_run(
                        request_id,
                        status="cancelled",
                        task_status="cancelled",
                    )
                    repository.revoke_task_permissions(user_id, task["id"])
                    emit(
                        "task.status",
                        {
                            "task_id": task["id"],
                            "request_id": request_id,
                            "status": "cancelled",
                            "permission_mode": "read",
                        },
                    )
                elif unresolved_tool_failure:
                    repository.finish_task_run(
                        request_id,
                        status="failed",
                        task_status="failed",
                        error="one or more tools failed",
                    )
                    repository.revoke_task_permissions(user_id, task["id"])
                    emit(
                        "task.status",
                        {
                            "task_id": task["id"],
                            "request_id": request_id,
                            "status": "failed",
                            "permission_mode": "read",
                        },
                    )
                elif effective_mode == "plan":
                    plan = repository.create_task_plan(user_id, task["id"], final)
                    repository.finish_task_run(
                        request_id,
                        status="completed",
                        task_status="awaiting_approval",
                    )
                    emit(
                        "plan.required",
                        {
                            "task_id": task["id"],
                            "request_id": request_id,
                            "plan": plan,
                        },
                    )
                    emit(
                        "task.status",
                        {
                            "task_id": task["id"],
                            "request_id": request_id,
                            "status": "awaiting_approval",
                            "permission_mode": "read",
                        },
                    )
                else:
                    repository.finish_task_run(
                        request_id,
                        status="completed",
                        task_status="completed",
                    )
                    repository.revoke_task_permissions(user_id, task["id"])
                    emit(
                        "task.status",
                        {
                            "task_id": task["id"],
                            "request_id": request_id,
                            "status": "completed",
                            "permission_mode": "read",
                        },
                    )
            record_event(
                event_type="chat_turn",
                session_id=session_id,
                user_id=user_id,
                status=final_status,
                mode=effective_mode,
                metadata={
                    **runtime_metadata,
                    "request_id": request_id,
                    "response_chars": len(final),
                },
            )
            repository.finish_model_run(
                request_id,
                status=final_status,
                provider=runtime_metadata.get("provider"),
                model=runtime_metadata.get("model"),
                error="one or more tools failed" if unresolved_tool_failure else None,
            )
            repository.update_conversation(user_id, session_id, status="idle")
            final_event_id = emit(
                "final",
                {
                    "content": final,
                    "message": assistant_message,
                    "session_id": session_id,
                    "request_id": request_id,
                    "title": title,
                    "status": final_status,
                },
            )
            runtime_store.save_chat_snapshot(
                request_id,
                session_id,
                final,
                final_event_id,
                status=final_status,
                started_at=snapshot_started_at,
                ttl_seconds=300,
            )
        except Exception as exc:
            cancelled = runtime_store.is_cancelled(request_id)
            if cancelled:
                logger.info("Chat request %s cancelled during agent interruption", request_id)
            else:
                logger.exception("Chat request %s failed", request_id)
            error_text = f"{type(exc).__name__}: {exc}"
            with snapshot_lock:
                partial_content = "".join(snapshot_chunks)
            assistant_message = None
            try:
                from server.audit import record_event

                record_event(
                    event_type="chat_turn",
                    session_id=session_id,
                    user_id=user_id,
                    status="cancelled" if cancelled else "failed",
                    mode=effective_mode,
                    metadata={"request_id": request_id, "plan_state": mode_state.get("state")},
                    error=None if cancelled else error_text,
                )
                if cancelled:
                    duration_ms = max(0, int((time.monotonic() - run_started_at) * 1000))
                    assistant_message = repository.append_message(
                        session_id,
                        "assistant",
                        partial_content,
                        status="cancelled",
                        model_run_id=request_id,
                        duration_ms=duration_ms,
                    )
                repository.finish_model_run(
                    request_id,
                    status="cancelled" if cancelled else "failed",
                    error=None if cancelled else error_text,
                )
                repository.update_conversation(user_id, session_id, status="idle")
                if task is not None:
                    repository.finish_task_run(
                        request_id,
                        status="cancelled" if cancelled else "failed",
                        task_status="cancelled" if cancelled else "failed",
                        error=None if cancelled else error_text,
                    )
                    repository.revoke_task_permissions(user_id, task["id"])
                    emit(
                        "task.status",
                        {
                            "task_id": task["id"],
                            "request_id": request_id,
                            "status": "cancelled" if cancelled else "failed",
                            "permission_mode": "read",
                        },
                    )
            except Exception:
                logger.exception("Failed to persist Chat terminal state")
            if cancelled:
                terminal_event_id = emit(
                    "final",
                    {
                        "content": partial_content,
                        "message": assistant_message,
                        "session_id": session_id,
                        "request_id": request_id,
                        "title": title,
                        "status": "cancelled",
                    },
                )
            else:
                terminal_event_id = emit(
                    "error",
                    {
                        "message": "回答生成失败，请稍后重试",
                        "code": type(exc).__name__,
                        "request_id": request_id,
                    },
                )
            runtime_store.save_chat_snapshot(
                request_id,
                session_id,
                partial_content,
                terminal_event_id,
                status="cancelled" if cancelled else "failed",
                started_at=snapshot_started_at,
                ttl_seconds=300,
            )
        finally:
            with _active_agents_lock:
                _active_agents.pop(request_id, None)
            runtime_store.mark_request(request_id, "done")
            runtime_store.release_conversation(session_id, lock_token)
            if stream_attached.is_set():
                event_queue.put(sentinel)
            if task is not None and effective_mode == "execute":
                from server.sandbox import release_task_sandbox

                threading.Thread(
                    target=release_task_sandbox,
                    args=(user_id, task["id"]),
                    daemon=True,
                    name=f"sandbox-release-{task['id'][:8]}",
                ).start()

    if task is not None:
        emit(
            "task.status",
            {
                "task_id": task["id"],
                "request_id": request_id,
                "status": "planning" if effective_mode == "plan" else "running",
                "permission_mode": permission_mode,
            },
        )

    threading.Thread(target=run_agent, daemon=True, name=f"chat-{request_id[:8]}").start()

    async def event_gen():
        try:
            while True:
                if await request.is_disconnected():
                    stream_attached.clear()
                    break
                try:
                    item = await asyncio.to_thread(event_queue.get, True, 0.25)
                except queue.Empty:
                    continue
                if item is sentinel:
                    emit_data = {
                        "session_id": session_id,
                        "user_id": user_id,
                        "request_id": request_id,
                    }
                    yield {
                        "id": str(event_sequence + 1),
                        "event": "done",
                        "data": json.dumps(emit_data, ensure_ascii=False),
                    }
                    break
                yield item
                if await request.is_disconnected():
                    stream_attached.clear()
                    break
        finally:
            stream_attached.clear()

    return EventSourceResponse(event_gen())


@router.post("/chat/{request_id}/cancel", status_code=202)
def cancel_chat(request_id: str, user: dict = Depends(get_current_user)):
    run = get_repository().get_owned_model_run(user["id"], request_id)
    if run is None:
        raise HTTPException(status_code=404, detail="running request not found")
    if run["status"] != "running":
        raise HTTPException(status_code=409, detail="request is not running")
    get_runtime_store().cancel_request(request_id)
    interrupted_locally = _interrupt_local_agent(request_id, user["id"])
    return {
        "request_id": request_id,
        "status": "cancelling",
        "interrupted_locally": interrupted_locally,
    }
