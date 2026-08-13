from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .repository import tenant_id

logger = logging.getLogger(__name__)


class RuntimeStore:
    def __init__(self) -> None:
        self._url = os.environ.get("HERMES_REDIS_URL", "").strip()
        self._redis = Redis.from_url(self._url, decode_responses=True) if self._url else None
        self._local_guard = threading.Lock()
        self._local_locks: dict[str, str] = {}
        self._local_cancelled: set[str] = set()
        self._local_chat_snapshots: dict[str, tuple[float, dict[str, Any]]] = {}
        self._local_condition = threading.Condition(self._local_guard)
        self._local_agent_jobs: dict[str, dict[str, Any]] = {}
        self._local_agent_meta: dict[str, dict[str, Any]] = {}
        self._local_agent_pending: deque[str] = deque()
        self._local_agent_processing: dict[str, dict[str, Any]] = {}
        self._local_knowledge_jobs: dict[str, dict[str, Any]] = {}
        self._local_knowledge_meta: dict[str, dict[str, Any]] = {}
        self._local_knowledge_pending: deque[str] = deque()
        self._local_knowledge_processing: dict[str, dict[str, Any]] = {}
        self._local_events: dict[str, list[dict[str, Any]]] = {}
        self._local_workers: dict[str, float] = {}

    def _key(self, *parts: str) -> str:
        return ":".join(("hermes", tenant_id(), *parts))

    @property
    def redis_enabled(self) -> bool:
        return self._redis is not None

    def health(self) -> bool | None:
        if self._redis is None:
            return None
        try:
            return bool(self._redis.ping())
        except RedisError:
            return False

    def acquire_conversation(self, conversation_id: str, ttl_seconds: int = 300) -> str | None:
        token = str(uuid.uuid4())
        key = self._key("conversation", conversation_id, "lock")
        if self._redis is not None:
            try:
                if self._redis.set(key, token, nx=True, ex=ttl_seconds):
                    return token
                return None
            except RedisError as exc:
                logger.warning("Redis lock unavailable, using process-local lock: %s", exc)
        with self._local_guard:
            if key in self._local_locks:
                return None
            self._local_locks[key] = token
            return token

    def release_conversation(self, conversation_id: str, token: str) -> None:
        key = self._key("conversation", conversation_id, "lock")
        if self._redis is not None:
            try:
                self._redis.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    key,
                    token,
                )
                return
            except RedisError as exc:
                logger.warning("Redis lock release failed: %s", exc)
        with self._local_guard:
            if self._local_locks.get(key) == token:
                self._local_locks.pop(key, None)

    def mark_request(self, request_id: str, state: str, ttl_seconds: int = 3600) -> None:
        if self._redis is None:
            return
        try:
            self._redis.set(self._key("request", request_id, "state"), state, ex=ttl_seconds)
        except RedisError as exc:
            logger.warning("Redis request state write failed: %s", exc)

    def cancel_request(self, request_id: str, ttl_seconds: int = 3600) -> bool:
        key = self._key("request", request_id, "cancel")
        if self._redis is not None:
            try:
                self._redis.set(key, "1", ex=ttl_seconds)
                return True
            except RedisError as exc:
                logger.warning("Redis cancellation write failed: %s", exc)
        with self._local_guard:
            self._local_cancelled.add(key)
        return self._redis is None

    def cancellation_state(self, request_id: str) -> bool | None:
        key = self._key("request", request_id, "cancel")
        if self._redis is not None:
            try:
                return self._redis.get(key) == "1"
            except RedisError as exc:
                logger.warning("Redis cancellation read failed: %s", exc)
                return None
        with self._local_guard:
            return key in self._local_cancelled

    def is_cancelled(self, request_id: str) -> bool:
        return self.cancellation_state(request_id) is True

    def append_event(
        self,
        request_id: str,
        event_id: int,
        event: str,
        data: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> None:
        key = self._key("stream", request_id, "events-v2")
        payload = json.dumps(
            {"id": event_id, "event": event, "data": data, "created_at": time.time()},
            ensure_ascii=False,
        )
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline()
                pipe.zadd(key, {payload: event_id})
                pipe.zremrangebyrank(key, 0, -501)
                pipe.expire(key, ttl_seconds)
                pipe.execute()
                return
            except RedisError as exc:
                logger.warning("Redis SSE buffer write failed: %s", exc)
        with self._local_guard:
            events = self._local_events.setdefault(key, [])
            events.append(json.loads(payload))
            del events[:-500]

    def list_events(self, request_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        key = self._key("stream", request_id, "events-v2")
        payloads: list[str] = []
        if self._redis is not None:
            try:
                payloads = self._redis.zrangebyscore(key, f"({after_id}", "+inf")
            except RedisError as exc:
                logger.warning("Redis SSE buffer read failed: %s", exc)
        else:
            with self._local_guard:
                return [
                    dict(event)
                    for event in self._local_events.get(key, [])
                    if int(event.get("id") or 0) > after_id
                ]
        events: list[dict[str, Any]] = []
        for payload in payloads:
            try:
                event = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict) and int(event.get("id") or 0) > after_id:
                events.append(event)
        return events

    def last_event_id(self, request_id: str) -> int:
        key = self._key("stream", request_id, "events-v2")
        if self._redis is not None:
            try:
                latest = self._redis.zrevrange(key, 0, 0, withscores=True)
                return int(latest[0][1]) if latest else 0
            except RedisError as exc:
                logger.warning("Redis SSE event cursor read failed: %s", exc)
        with self._local_guard:
            return max(
                (
                    int(event.get("id") or 0)
                    for event in self._local_events.get(key, [])
                ),
                default=0,
            )

    def save_chat_snapshot(
        self,
        request_id: str,
        conversation_id: str,
        content: str,
        sequence: int,
        *,
        status: str = "running",
        started_at: float | None = None,
        ttl_seconds: int = 3600,
    ) -> dict[str, Any]:
        now = time.time()
        snapshot = {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "content": content,
            "sequence": sequence,
            "status": status,
            "started_at": started_at,
            "updated_at": now,
        }
        key = self._key("stream", request_id, "snapshot")
        if self._redis is not None:
            try:
                self._redis.set(
                    key,
                    json.dumps(snapshot, ensure_ascii=False),
                    ex=ttl_seconds,
                )
                return snapshot
            except RedisError as exc:
                logger.warning("Redis Chat snapshot write failed: %s", exc)
        with self._local_guard:
            self._local_chat_snapshots[key] = (now + ttl_seconds, snapshot)
        return snapshot

    def get_chat_snapshot(self, request_id: str) -> dict[str, Any] | None:
        key = self._key("stream", request_id, "snapshot")
        if self._redis is not None:
            try:
                payload = self._redis.get(key)
                if payload:
                    snapshot = json.loads(payload)
                    return snapshot if isinstance(snapshot, dict) else None
            except (RedisError, json.JSONDecodeError) as exc:
                logger.warning("Redis Chat snapshot read failed: %s", exc)
        with self._local_guard:
            cached = self._local_chat_snapshots.get(key)
            if cached is None:
                return None
            expires_at, snapshot = cached
            if expires_at <= time.time():
                self._local_chat_snapshots.pop(key, None)
                return None
            return dict(snapshot)

    def enqueue_agent_job(self, job: dict[str, Any]) -> bool:
        request_id = str(job["request_id"])
        payload_key = self._key("agent", "job", request_id, "payload")
        meta_key = self._key("agent", "job", request_id, "meta")
        pending_key = self._key("agent", "queue", "pending")
        payload = json.dumps(job, ensure_ascii=False)
        now = time.time()
        if self._redis is not None:
            script = (
                "if redis.call('exists', KEYS[1]) == 1 then return 0 end; "
                "redis.call('set', KEYS[1], ARGV[1]); "
                "redis.call('hset', KEYS[2], 'state', 'queued', 'attempt', '0', "
                "'worker_id', '', 'lease_until', '0', 'updated_at', ARGV[2]); "
                "redis.call('lpush', KEYS[3], ARGV[3]); return 1"
            )
            try:
                return bool(
                    self._redis.eval(
                        script,
                        3,
                        payload_key,
                        meta_key,
                        pending_key,
                        payload,
                        str(now),
                        request_id,
                    )
                )
            except RedisError as exc:
                raise RuntimeError("Redis Agent queue is unavailable") from exc
        with self._local_condition:
            if request_id in self._local_agent_jobs:
                return False
            self._local_agent_jobs[request_id] = dict(job)
            self._local_agent_meta[request_id] = {
                "state": "queued",
                "attempt": 0,
                "worker_id": "",
                "lease_until": 0.0,
                "updated_at": now,
            }
            self._local_agent_pending.appendleft(request_id)
            self._local_condition.notify()
            return True

    def claim_agent_job(
        self,
        worker_id: str,
        *,
        timeout_seconds: int = 2,
        lease_seconds: int = 15,
    ) -> dict[str, Any] | None:
        pending_key = self._key("agent", "queue", "pending")
        processing_key = self._key("agent", "queue", "processing")
        if self._redis is not None:
            try:
                request_id = self._redis.brpoplpush(
                    pending_key,
                    processing_key,
                    timeout=max(0, timeout_seconds),
                )
                if not request_id:
                    return None
                payload = self._redis.get(
                    self._key("agent", "job", request_id, "payload")
                )
                if not payload:
                    self._redis.lrem(processing_key, 0, request_id)
                    return None
                meta_key = self._key("agent", "job", request_id, "meta")
                attempt = int(self._redis.hincrby(meta_key, "attempt", 1))
                now = time.time()
                self._redis.hset(
                    meta_key,
                    mapping={
                        "state": "processing",
                        "worker_id": worker_id,
                        "lease_until": str(now + lease_seconds),
                        "updated_at": str(now),
                    },
                )
                job = json.loads(payload)
                job["delivery_attempt"] = attempt
                return job
            except (RedisError, json.JSONDecodeError) as exc:
                raise RuntimeError("Redis Agent queue claim failed") from exc
        deadline = time.monotonic() + max(0, timeout_seconds)
        with self._local_condition:
            while not self._local_agent_pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._local_condition.wait(remaining)
            request_id = self._local_agent_pending.pop()
            meta = self._local_agent_meta[request_id]
            meta.update(
                state="processing",
                attempt=int(meta.get("attempt") or 0) + 1,
                worker_id=worker_id,
                lease_until=time.time() + lease_seconds,
                updated_at=time.time(),
            )
            self._local_agent_processing[request_id] = dict(meta)
            return {
                **self._local_agent_jobs[request_id],
                "delivery_attempt": meta["attempt"],
            }

    def heartbeat_agent_job(
        self,
        request_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 15,
    ) -> bool | None:
        meta_key = self._key("agent", "job", request_id, "meta")
        now = time.time()
        if self._redis is not None:
            script = (
                "if redis.call('hget', KEYS[1], 'state') == 'processing' and "
                "redis.call('hget', KEYS[1], 'worker_id') == ARGV[1] then "
                "redis.call('hset', KEYS[1], 'lease_until', ARGV[2], 'updated_at', ARGV[3]); "
                "return 1 else return 0 end"
            )
            try:
                return bool(
                    self._redis.eval(
                        script,
                        1,
                        meta_key,
                        worker_id,
                        str(now + lease_seconds),
                        str(now),
                    )
                )
            except RedisError as exc:
                logger.warning("Redis Agent lease heartbeat failed: %s", exc)
                return None
        with self._local_guard:
            meta = self._local_agent_processing.get(request_id)
            if not meta or meta.get("worker_id") != worker_id:
                return False
            meta["lease_until"] = now + lease_seconds
            meta["updated_at"] = now
            self._local_agent_meta[request_id].update(meta)
            return True

    def finish_agent_job(
        self,
        request_id: str,
        worker_id: str,
        *,
        status: str,
        ttl_seconds: int = 86400,
        allow_stale: bool = False,
    ) -> bool:
        processing_key = self._key("agent", "queue", "processing")
        meta_key = self._key("agent", "job", request_id, "meta")
        payload_key = self._key("agent", "job", request_id, "payload")
        now = time.time()
        if self._redis is not None:
            script = (
                "local state = redis.call('hget', KEYS[1], 'state'); "
                "local owner = redis.call('hget', KEYS[1], 'worker_id') or ''; "
                "if (state == 'processing' and owner == ARGV[1]) or "
                "(ARGV[6] == '1' and state == 'stale') then "
                "redis.call('lrem', KEYS[2], 0, ARGV[2]); "
                "redis.call('hset', KEYS[1], 'state', ARGV[3], 'worker_id', ARGV[1], "
                "'lease_until', '0', 'updated_at', ARGV[4]); "
                "redis.call('expire', KEYS[1], ARGV[5]); "
                "redis.call('expire', KEYS[3], ARGV[5]); return 1 else return 0 end"
            )
            try:
                return bool(
                    self._redis.eval(
                        script,
                        3,
                        meta_key,
                        processing_key,
                        payload_key,
                        worker_id,
                        request_id,
                        status,
                        str(now),
                        str(ttl_seconds),
                        "1" if allow_stale else "0",
                    )
                )
            except RedisError as exc:
                logger.warning("Redis Agent queue acknowledgement failed: %s", exc)
                return False
        with self._local_condition:
            meta = self._local_agent_meta.get(request_id)
            if meta is None:
                return False
            owned = meta.get("state") == "processing" and meta.get("worker_id") == worker_id
            stale = allow_stale and meta.get("state") == "stale"
            if not (owned or stale):
                return False
            self._local_agent_processing.pop(request_id, None)
            meta.update(
                state=status,
                worker_id=worker_id,
                lease_until=0.0,
                updated_at=now,
            )
            return True

    def take_expired_agent_jobs(self, *, now: float | None = None) -> list[dict[str, Any]]:
        cutoff = now or time.time()
        processing_key = self._key("agent", "queue", "processing")
        if self._redis is not None:
            try:
                request_ids = self._redis.lrange(processing_key, 0, -1)
            except RedisError as exc:
                raise RuntimeError("Redis Agent recovery scan failed") from exc
            stale: list[dict[str, Any]] = []
            script = (
                "local lease = tonumber(redis.call('hget', KEYS[1], 'lease_until') or '0'); "
                "local state = redis.call('hget', KEYS[1], 'state'); "
                "if state == 'queued' or (state == 'processing' and lease <= tonumber(ARGV[1])) then "
                "redis.call('lrem', KEYS[2], 0, ARGV[2]); "
                "redis.call('hset', KEYS[1], 'state', 'stale', 'worker_id', '', 'updated_at', ARGV[1]); "
                "return 1 else return 0 end"
            )
            for request_id in request_ids:
                meta_key = self._key("agent", "job", request_id, "meta")
                try:
                    claimed = self._redis.eval(
                        script, 2, meta_key, processing_key, str(cutoff), request_id
                    )
                    if not claimed:
                        continue
                    payload = self._redis.get(
                        self._key("agent", "job", request_id, "payload")
                    )
                    if payload:
                        job = json.loads(payload)
                        if isinstance(job, dict):
                            stale.append(job)
                except (RedisError, json.JSONDecodeError) as exc:
                    logger.warning("Redis Agent stale job recovery failed: %s", exc)
            return stale
        stale = []
        with self._local_condition:
            for request_id, meta in list(self._local_agent_processing.items()):
                if float(meta.get("lease_until") or 0) > cutoff:
                    continue
                self._local_agent_processing.pop(request_id, None)
                self._local_agent_meta[request_id].update(
                    state="stale", worker_id="", updated_at=cutoff
                )
                stale.append(dict(self._local_agent_jobs[request_id]))
        return stale

    def requeue_agent_job(self, job: dict[str, Any]) -> bool:
        request_id = str(job["request_id"])
        pending_key = self._key("agent", "queue", "pending")
        meta_key = self._key("agent", "job", request_id, "meta")
        if self._redis is not None:
            payload_key = self._key("agent", "job", request_id, "payload")
            script = (
                "if redis.call('exists', KEYS[1]) == 0 then return -1 end; "
                "if redis.call('hget', KEYS[2], 'state') == 'queued' then return 0 end; "
                "redis.call('hset', KEYS[2], 'state', 'queued', 'worker_id', '', "
                "'lease_until', '0', 'updated_at', ARGV[1]); "
                "redis.call('lpush', KEYS[3], ARGV[2]); return 1"
            )
            try:
                result = int(
                    self._redis.eval(
                        script,
                        3,
                        payload_key,
                        meta_key,
                        pending_key,
                        str(time.time()),
                        request_id,
                    )
                )
                if result == -1:
                    return self.enqueue_agent_job(job)
                return result == 1
            except RedisError as exc:
                raise RuntimeError("Redis Agent requeue failed") from exc
        with self._local_condition:
            if request_id not in self._local_agent_jobs:
                self._local_agent_jobs[request_id] = dict(job)
            meta = self._local_agent_meta.setdefault(request_id, {"attempt": 0})
            if meta.get("state") == "queued":
                return False
            meta.update(
                state="queued", worker_id="", lease_until=0.0, updated_at=time.time()
            )
            self._local_agent_pending.appendleft(request_id)
            self._local_condition.notify()
            return True

    def agent_job_state(self, request_id: str) -> dict[str, Any] | None:
        meta_key = self._key("agent", "job", request_id, "meta")
        if self._redis is not None:
            try:
                meta = self._redis.hgetall(meta_key)
            except RedisError as exc:
                logger.warning("Redis Agent job state read failed: %s", exc)
                return {"state": "unavailable"}
            if not meta:
                return None
            return {
                **meta,
                "attempt": int(meta.get("attempt") or 0),
                "lease_until": float(meta.get("lease_until") or 0),
                "updated_at": float(meta.get("updated_at") or 0),
            }
        with self._local_guard:
            meta = self._local_agent_meta.get(request_id)
            return dict(meta) if meta is not None else None

    # ------------------------------------------------------ knowledge queue
    # 与 agent 队列同构，Redis key 以 "knowledge" 命名空间隔离（knowledge: 前缀）。
    # job 形如 {"job_id", "doc_id", "user_id", "queued_at"}。

    def enqueue_knowledge_job(self, job: dict[str, Any]) -> bool:
        job_id = str(job["job_id"])
        payload_key = self._key("knowledge", "job", job_id, "payload")
        meta_key = self._key("knowledge", "job", job_id, "meta")
        pending_key = self._key("knowledge", "queue", "pending")
        payload = json.dumps(job, ensure_ascii=False)
        now = time.time()
        if self._redis is not None:
            script = (
                "if redis.call('exists', KEYS[1]) == 1 then return 0 end; "
                "redis.call('set', KEYS[1], ARGV[1]); "
                "redis.call('hset', KEYS[2], 'state', 'queued', 'attempt', '0', "
                "'worker_id', '', 'lease_until', '0', 'updated_at', ARGV[2]); "
                "redis.call('lpush', KEYS[3], ARGV[3]); return 1"
            )
            try:
                return bool(
                    self._redis.eval(
                        script, 3, payload_key, meta_key, pending_key,
                        payload, str(now), job_id,
                    )
                )
            except RedisError as exc:
                raise RuntimeError("Redis knowledge queue is unavailable") from exc
        with self._local_condition:
            if job_id in self._local_knowledge_jobs:
                return False
            self._local_knowledge_jobs[job_id] = dict(job)
            self._local_knowledge_meta[job_id] = {
                "state": "queued",
                "attempt": 0,
                "worker_id": "",
                "lease_until": 0.0,
                "updated_at": now,
            }
            self._local_knowledge_pending.appendleft(job_id)
            self._local_condition.notify()
            return True

    def claim_knowledge_job(
        self,
        worker_id: str,
        *,
        timeout_seconds: int = 2,
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        pending_key = self._key("knowledge", "queue", "pending")
        processing_key = self._key("knowledge", "queue", "processing")
        if self._redis is not None:
            try:
                job_id = self._redis.brpoplpush(
                    pending_key, processing_key, timeout=max(0, timeout_seconds)
                )
                if not job_id:
                    return None
                payload = self._redis.get(
                    self._key("knowledge", "job", job_id, "payload")
                )
                if not payload:
                    self._redis.lrem(processing_key, 0, job_id)
                    return None
                meta_key = self._key("knowledge", "job", job_id, "meta")
                attempt = int(self._redis.hincrby(meta_key, "attempt", 1))
                now = time.time()
                self._redis.hset(
                    meta_key,
                    mapping={
                        "state": "processing",
                        "worker_id": worker_id,
                        "lease_until": str(now + lease_seconds),
                        "updated_at": str(now),
                    },
                )
                job = json.loads(payload)
                job["delivery_attempt"] = attempt
                return job
            except (RedisError, json.JSONDecodeError) as exc:
                raise RuntimeError("Redis knowledge queue claim failed") from exc
        deadline = time.monotonic() + max(0, timeout_seconds)
        with self._local_condition:
            while not self._local_knowledge_pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._local_condition.wait(remaining)
            job_id = self._local_knowledge_pending.pop()
            meta = self._local_knowledge_meta[job_id]
            meta.update(
                state="processing",
                attempt=int(meta.get("attempt") or 0) + 1,
                worker_id=worker_id,
                lease_until=time.time() + lease_seconds,
                updated_at=time.time(),
            )
            self._local_knowledge_processing[job_id] = dict(meta)
            return {
                **self._local_knowledge_jobs[job_id],
                "delivery_attempt": meta["attempt"],
            }

    def heartbeat_knowledge_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
    ) -> bool | None:
        meta_key = self._key("knowledge", "job", job_id, "meta")
        now = time.time()
        if self._redis is not None:
            script = (
                "if redis.call('hget', KEYS[1], 'state') == 'processing' and "
                "redis.call('hget', KEYS[1], 'worker_id') == ARGV[1] then "
                "redis.call('hset', KEYS[1], 'lease_until', ARGV[2], 'updated_at', ARGV[3]); "
                "return 1 else return 0 end"
            )
            try:
                return bool(
                    self._redis.eval(
                        script, 1, meta_key, worker_id,
                        str(now + lease_seconds), str(now),
                    )
                )
            except RedisError as exc:
                logger.warning("Redis knowledge lease heartbeat failed: %s", exc)
                return None
        with self._local_guard:
            meta = self._local_knowledge_processing.get(job_id)
            if not meta or meta.get("worker_id") != worker_id:
                return False
            meta["lease_until"] = now + lease_seconds
            meta["updated_at"] = now
            self._local_knowledge_meta[job_id].update(meta)
            return True

    def finish_knowledge_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        status: str,
        ttl_seconds: int = 86400,
        allow_stale: bool = False,
    ) -> bool:
        processing_key = self._key("knowledge", "queue", "processing")
        meta_key = self._key("knowledge", "job", job_id, "meta")
        payload_key = self._key("knowledge", "job", job_id, "payload")
        now = time.time()
        if self._redis is not None:
            script = (
                "local state = redis.call('hget', KEYS[1], 'state'); "
                "local owner = redis.call('hget', KEYS[1], 'worker_id') or ''; "
                "if (state == 'processing' and owner == ARGV[1]) or "
                "(ARGV[6] == '1' and state == 'stale') then "
                "redis.call('lrem', KEYS[2], 0, ARGV[2]); "
                "redis.call('hset', KEYS[1], 'state', ARGV[3], 'worker_id', ARGV[1], "
                "'lease_until', '0', 'updated_at', ARGV[4]); "
                "redis.call('expire', KEYS[1], ARGV[5]); "
                "redis.call('expire', KEYS[3], ARGV[5]); return 1 else return 0 end"
            )
            try:
                return bool(
                    self._redis.eval(
                        script, 3, meta_key, processing_key, payload_key,
                        worker_id, job_id, status, str(now), str(ttl_seconds),
                        "1" if allow_stale else "0",
                    )
                )
            except RedisError as exc:
                logger.warning("Redis knowledge queue acknowledgement failed: %s", exc)
                return False
        with self._local_condition:
            meta = self._local_knowledge_meta.get(job_id)
            if meta is None:
                return False
            owned = meta.get("state") == "processing" and meta.get("worker_id") == worker_id
            stale = allow_stale and meta.get("state") == "stale"
            if not (owned or stale):
                return False
            self._local_knowledge_processing.pop(job_id, None)
            meta.update(state=status, worker_id=worker_id, lease_until=0.0, updated_at=now)
            return True

    def take_expired_knowledge_jobs(self, *, now: float | None = None) -> list[dict[str, Any]]:
        cutoff = now or time.time()
        processing_key = self._key("knowledge", "queue", "processing")
        if self._redis is not None:
            try:
                job_ids = self._redis.lrange(processing_key, 0, -1)
            except RedisError as exc:
                raise RuntimeError("Redis knowledge recovery scan failed") from exc
            stale: list[dict[str, Any]] = []
            script = (
                "local lease = tonumber(redis.call('hget', KEYS[1], 'lease_until') or '0'); "
                "local state = redis.call('hget', KEYS[1], 'state'); "
                "if state == 'queued' or (state == 'processing' and lease <= tonumber(ARGV[1])) then "
                "redis.call('lrem', KEYS[2], 0, ARGV[2]); "
                "redis.call('hset', KEYS[1], 'state', 'stale', 'worker_id', '', 'updated_at', ARGV[1]); "
                "return 1 else return 0 end"
            )
            for job_id in job_ids:
                meta_key = self._key("knowledge", "job", job_id, "meta")
                try:
                    claimed = self._redis.eval(
                        script, 2, meta_key, processing_key, str(cutoff), job_id
                    )
                    if not claimed:
                        continue
                    payload = self._redis.get(
                        self._key("knowledge", "job", job_id, "payload")
                    )
                    if payload:
                        job = json.loads(payload)
                        if isinstance(job, dict):
                            stale.append(job)
                except (RedisError, json.JSONDecodeError) as exc:
                    logger.warning("Redis knowledge stale job recovery failed: %s", exc)
            return stale
        stale = []
        with self._local_condition:
            for job_id, meta in list(self._local_knowledge_processing.items()):
                if float(meta.get("lease_until") or 0) > cutoff:
                    continue
                self._local_knowledge_processing.pop(job_id, None)
                self._local_knowledge_meta[job_id].update(
                    state="stale", worker_id="", updated_at=cutoff
                )
                stale.append(dict(self._local_knowledge_jobs[job_id]))
        return stale

    def requeue_knowledge_job(self, job: dict[str, Any]) -> bool:
        job_id = str(job["job_id"])
        pending_key = self._key("knowledge", "queue", "pending")
        meta_key = self._key("knowledge", "job", job_id, "meta")
        if self._redis is not None:
            payload_key = self._key("knowledge", "job", job_id, "payload")
            script = (
                "if redis.call('exists', KEYS[1]) == 0 then return -1 end; "
                "if redis.call('hget', KEYS[2], 'state') == 'queued' then return 0 end; "
                "redis.call('hset', KEYS[2], 'state', 'queued', 'worker_id', '', "
                "'lease_until', '0', 'updated_at', ARGV[1]); "
                "redis.call('lpush', KEYS[3], ARGV[2]); return 1"
            )
            try:
                result = int(
                    self._redis.eval(
                        script, 3, payload_key, meta_key, pending_key,
                        str(time.time()), job_id,
                    )
                )
                if result == -1:
                    return self.enqueue_knowledge_job(job)
                return result == 1
            except RedisError as exc:
                raise RuntimeError("Redis knowledge requeue failed") from exc
        with self._local_condition:
            if job_id not in self._local_knowledge_jobs:
                self._local_knowledge_jobs[job_id] = dict(job)
            meta = self._local_knowledge_meta.setdefault(job_id, {"attempt": 0})
            if meta.get("state") == "queued":
                return False
            meta.update(
                state="queued", worker_id="", lease_until=0.0, updated_at=time.time()
            )
            self._local_knowledge_pending.appendleft(job_id)
            self._local_condition.notify()
            return True

    def knowledge_job_state(self, job_id: str) -> dict[str, Any] | None:
        meta_key = self._key("knowledge", "job", job_id, "meta")
        if self._redis is not None:
            try:
                meta = self._redis.hgetall(meta_key)
            except RedisError as exc:
                logger.warning("Redis knowledge job state read failed: %s", exc)
                return {"state": "unavailable"}
            if not meta:
                return None
            return {
                **meta,
                "attempt": int(meta.get("attempt") or 0),
                "lease_until": float(meta.get("lease_until") or 0),
                "updated_at": float(meta.get("updated_at") or 0),
            }
        with self._local_guard:
            meta = self._local_knowledge_meta.get(job_id)
            return dict(meta) if meta is not None else None

    def touch_worker(self, worker_id: str, *, ttl_seconds: int = 15) -> None:
        now = time.time()
        if self._redis is not None:
            try:
                self._redis.set(
                    self._key("agent", "worker", worker_id), str(now), ex=ttl_seconds
                )
                self._redis.set(
                    self._key("agent", "worker", "last_seen"), str(now), ex=ttl_seconds
                )
                return
            except RedisError as exc:
                logger.warning("Redis Agent worker heartbeat failed: %s", exc)
        with self._local_guard:
            self._local_workers[worker_id] = now + ttl_seconds

    def worker_health(self) -> bool | None:
        if self._redis is not None:
            try:
                return self._redis.exists(self._key("agent", "worker", "last_seen")) == 1
            except RedisError:
                return False
        with self._local_guard:
            now = time.time()
            return any(expires_at > now for expires_at in self._local_workers.values()) or None


_runtime_store: RuntimeStore | None = None
_runtime_lock = threading.Lock()


def get_runtime_store() -> RuntimeStore:
    global _runtime_store
    configured_url = os.environ.get("HERMES_REDIS_URL", "").strip()
    if _runtime_store is None or _runtime_store._url != configured_url:
        with _runtime_lock:
            if _runtime_store is None or _runtime_store._url != configured_url:
                _runtime_store = RuntimeStore()
    return _runtime_store


def reset_runtime_store_for_tests() -> None:
    global _runtime_store
    with _runtime_lock:
        _runtime_store = None
